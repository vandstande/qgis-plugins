# -*- coding: utf-8 -*-
"""
Beregningskerne for "Screening af projektgrænse".

Ren numpy - ingen QGIS-afhængigheder, så den kan testes for sig.

Princip
-------
* Linjen (vådområdets bund) er tegnet i vandløbets retning.
* Langs linjen lægges stationer med fast afstand.
* For hver station findes bundkoten z0 i terrænmodellen. Der tjekkes på
  begge sider af linjen, om linjen ligger i bunden af en grøft/vandløb
  (terrænet lige ved siden af er markant højere på BEGGE sider). Er det
  tilfældet, kan bundkoten rettes til terrænet ved siden af grøften.
* Bundkoten udglattes langs linjen (glidende median), og grænsekoten
  sættes til bund + dybde (standard 1,5 m).
* Fra hver station sendes en stråle vinkelret ud til venstre og højre,
  indtil terrænet når grænsekoten. Grænsen bliver dermed tegnet parallelt
  med linjen.
* Opstrøms lukkes grænsen med en vifte af stråler bagud fra linjens
  startpunkt, med grænsekoten ved start. Viften må højst gå en given
  afstand (standard 100 m) bagud. Nås grænsekoten ikke inden for den
  afstand, beregnes grænsen ikke opstrøms - den lukkes i stedet vinkelret
  gennem startpunktet, og der gives en advarsel.
* Nedstrøms lukkes grænsen vinkelret på linjen gennem slutpunktet.
"""

import math
import numpy as np

STATUS_TERRAEN = "terræn"     # grænsekoten blev nået
STATUS_MAX = "max bredde"     # ikke nået inden for maks. afstand
STATUS_NODATA = "nodata"      # strålen løb ud af terrænmodellen
STATUS_OPSTROEMS = "maks. opstrøms"  # ikke nået inden for maks. opstrøms


class DemGrid:
    """Terrænmodel som numpy-array med nærmeste-nabo opslag."""

    def __init__(self, arr, xmin, ymax, res):
        self.arr = np.asarray(arr, dtype=float)  # nodata skal være NaN
        self.xmin = float(xmin)
        self.ymax = float(ymax)
        self.res = float(res)
        self.nrows, self.ncols = self.arr.shape

    def sample(self, x, y):
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        col = np.floor((x - self.xmin) / self.res).astype(int)
        row = np.floor((self.ymax - y) / self.res).astype(int)
        ok = (col >= 0) & (col < self.ncols) & (row >= 0) & (row < self.nrows)
        out = np.full(x.shape, np.nan)
        out[ok] = self.arr[row[ok], col[ok]]
        return out


def _polyline_measure(coords):
    c = np.asarray(coords, dtype=float)[:, :2]
    seg = np.hypot(np.diff(c[:, 0]), np.diff(c[:, 1]))
    keep = np.concatenate([[True], seg > 1e-9])  # fjern dublet-punkter
    c = c[keep]
    seg = np.hypot(np.diff(c[:, 0]), np.diff(c[:, 1]))
    m = np.concatenate([[0.0], np.cumsum(seg)])
    return c, m


def _interp(c, m, s):
    s = np.clip(s, 0.0, m[-1])
    return np.interp(s, m, c[:, 0]), np.interp(s, m, c[:, 1])


def _running_median(v, k):
    if k <= 1:
        return v.copy()
    h = k // 2
    out = np.empty_like(v)
    for i in range(len(v)):
        w = v[max(0, i - h): i + h + 1]
        w = w[~np.isnan(w)]
        out[i] = np.median(w) if len(w) else np.nan
    return out


def _fill_nan(v):
    v = v.copy()
    bad = np.isnan(v)
    if bad.all():
        return v
    idx = np.arange(len(v))
    v[bad] = np.interp(idx[bad], idx[~bad], v[~bad])
    return v


def cast_ray(grid, x0, y0, dx, dy, level, maxdist, step, ditch_offset=0.0):
    """Gå ud fra (x0,y0) i retning (dx,dy) til terrænet >= level.

    ditch_offset > 0: hvis strålen følger en grøft/vandløb, bruges terrænet
    ved siden af (laveste af de to sider i afstanden ditch_offset), så
    strålen ikke løber op ad vandløbet.
    Returnerer (afstand, status)."""
    d = np.arange(step, maxdist + 0.5 * step, step)
    px, py = x0 + d * dx, y0 + d * dy
    z = grid.sample(px, py)
    if ditch_offset > 0:
        ox, oy = -dy * ditch_offset, dx * ditch_offset
        side = np.fmin(grid.sample(px + ox, py + oy),
                       grid.sample(px - ox, py - oy))
        z = np.where(np.isnan(z), z, np.fmax(z, side))
    hit_terr = z >= level
    hit_nan = np.isnan(z)
    stop = hit_terr | hit_nan
    if not stop.any():
        return float(d[-1]), STATUS_MAX
    i = int(np.argmax(stop))
    if hit_terr[i]:
        # lineær interpolation mellem sidste "våde" og første "tørre" punkt
        if i > 0 and not np.isnan(z[i - 1]) and z[i] > z[i - 1]:
            f = (level - z[i - 1]) / (z[i] - z[i - 1])
            return float(d[i - 1] + f * step), STATUS_TERRAEN
        return float(d[i]), STATUS_TERRAEN
    return float(d[i - 1]) if i > 0 else 0.0, STATUS_NODATA


def compute(coords, grid, depth=1.5, spacing=5.0, maxdist=300.0, step=None,
            smooth_window=25.0, tangent_window=20.0, monotone=False,
            ditch_dist=3.0, ditch_tol=0.3, ditch_correct=True,
            fan_step_deg=10.0, upstream_max=100.0):
    """Beregn projektgrænse for én linje.

    coords: liste af (x, y) i strømretningen, i terrænmodellens CRS.
    Returnerer dict med stationer, venstre/højre grænsepunkter, vifte
    opstrøms og advarsler.
    """
    if step is None:
        step = grid.res
    c, m = _polyline_measure(coords)
    if len(c) < 2 or m[-1] <= 0:
        raise ValueError("Linjen har ingen længde.")
    length = m[-1]

    s = np.arange(0.0, length, spacing)
    if length - s[-1] > 0.25 * spacing or len(s) == 1:
        s = np.append(s, length)
    else:
        s[-1] = length
    x, y = _interp(c, m, s)

    # tangent som retning mellem punkter et stykke før og efter stationen
    h = max(tangent_window / 2.0, spacing / 2.0, 1e-3)
    xa, ya = _interp(c, m, s - h)
    xb, yb = _interp(c, m, s + h)
    tx, ty = xb - xa, yb - ya
    tl = np.hypot(tx, ty)
    tl[tl == 0] = 1.0
    tx, ty = tx / tl, ty / tl
    nx, ny = -ty, tx  # venstre normal (set i strømretningen)

    warnings = []

    # --- bundkote og grøftetjek -------------------------------------------
    z_line = grid.sample(x, y)
    z_left = grid.sample(x + ditch_dist * nx, y + ditch_dist * ny)
    z_right = grid.sample(x - ditch_dist * nx, y - ditch_dist * ny)
    side_min = np.fmin(z_left, z_right)
    ditch = (side_min - z_line) > ditch_tol
    ditch &= ~np.isnan(z_left) & ~np.isnan(z_right) & ~np.isnan(z_line)

    if np.isnan(z_line).all():
        raise ValueError("Linjen ligger uden for terrænmodellen (kun nodata).")
    n_nan = int(np.isnan(z_line).sum())
    if n_nan:
        warnings.append("%d stationer ligger på nodata i terrænmodellen - "
                        "bundkoten er interpoleret." % n_nan)

    z_bund = z_line.copy()
    if ditch_correct:
        z_bund[ditch] = side_min[ditch]
    z_bund = _fill_nan(z_bund)

    n_ditch = int(ditch.sum())
    if n_ditch:
        pct = 100.0 * n_ditch / len(s)
        txt = ("Linjen ser ud til at ligge i bunden af en grøft/vandløb ved "
               "%d af %d stationer (%.0f %%)" % (n_ditch, len(s), pct))
        if ditch_correct:
            txt += " - bundkoten er her sat til terrænet ved siden af."
        else:
            txt += " - kontroller linjen (se kontrolpunkterne)."
        warnings.append(txt)

    k = int(round(smooth_window / spacing))
    if k % 2 == 0:
        k += 1
    z_glat = _running_median(z_bund, max(k, 1))
    z_glat = _fill_nan(z_glat)

    level = z_glat + depth
    if monotone:
        # vandspejlet må ikke stige nedstrøms
        level = np.maximum.accumulate(level[::-1])[::-1]

    # retningskontrol
    nn = max(1, len(s) // 10)
    z_start, z_slut = np.nanmean(z_glat[:nn]), np.nanmean(z_glat[-nn:])
    if z_start + 0.2 < z_slut:
        warnings.append("Bunden er %.2f m lavere ved linjens start end ved "
                        "slut. Er linjen tegnet i strømretningen?"
                        % (z_slut - z_start))

    # --- stråler til siderne ----------------------------------------------
    left = []
    right = []
    n_max = 0
    for i in range(len(s)):
        dl, stl = cast_ray(grid, x[i], y[i], nx[i], ny[i], level[i],
                           maxdist, step)
        dr, str_ = cast_ray(grid, x[i], y[i], -nx[i], -ny[i], level[i],
                            maxdist, step)
        n_max += (stl == STATUS_MAX) + (str_ == STATUS_MAX)
        left.append((x[i] + dl * nx[i], y[i] + dl * ny[i], dl, stl))
        right.append((x[i] - dr * nx[i], y[i] - dr * ny[i], dr, str_))
    if n_max:
        warnings.append("Grænsekoten blev ikke nået inden for %.0f m i %d "
                        "stråler - grænsen er her sat ved maks. bredde."
                        % (maxdist, n_max))

    # --- vifte opstrøms ---------------------------------------------------
    # Strålen i vinklen th har den bagudrettede komposant d*sin(th); den
    # begrænses, så ingen stråle går mere end upstream_max bag startpunktet.
    fan = []
    fan_ok = True
    nsteps = max(2, int(round(180.0 / fan_step_deg)))
    for j in range(nsteps + 1):
        th = math.pi * j / nsteps
        sin_th = math.sin(th)
        dx = math.cos(th) * nx[0] - sin_th * tx[0]
        dy = math.cos(th) * ny[0] - sin_th * ty[0]
        cap = maxdist
        if sin_th > 1e-6:
            cap = min(maxdist, upstream_max / sin_th)
        d, st = cast_ray(grid, x[0], y[0], dx, dy, level[0], cap, step,
                         ditch_dist if ditch_correct else 0.0)
        if st == STATUS_MAX and cap < maxdist:
            st = STATUS_OPSTROEMS
            fan_ok = False
        fan.append((x[0] + d * dx, y[0] + d * dy, d, st))
    if not fan_ok:
        warnings.append("Grænsekoten ved linjens start (%.2f) blev ikke nået "
                        "inden for %.0f m opstrøms - projektgrænsen er ikke "
                        "beregnet opstrøms, men lukket vinkelret gennem "
                        "linjens startpunkt." % (level[0], upstream_max))

    stations = []
    for i in range(len(s)):
        stations.append(dict(
            station=float(s[i]), x=float(x[i]), y=float(y[i]),
            z_linje=float(z_line[i]), z_venstre=float(z_left[i]),
            z_hoejre=float(z_right[i]), groeft=bool(ditch[i]),
            z_bund=float(z_glat[i]), graensekote=float(level[i]),
            bredde_v=left[i][2], bredde_h=right[i][2],
            status_v=left[i][3], status_h=right[i][3]))

    return dict(stations=stations, left=left, right=right, fan=fan,
                fan_ok=fan_ok, length=float(length), warnings=warnings,
                kote_start=float(level[0]), kote_slut=float(level[-1]))


def polygon_parts(res):
    """Returnerer en liste af simple ringe (lister af (x,y)), hvis forening
    er projektgrænsen: firkanter mellem nabotværsnit + vifte opstrøms."""
    st = res["stations"]
    L, R = res["left"], res["right"]
    parts = []
    for i in range(len(st) - 1):
        p0 = (st[i]["x"], st[i]["y"])
        p1 = (st[i + 1]["x"], st[i + 1]["y"])
        parts.append([p0, L[i][:2], L[i + 1][:2], p1, p0])
        parts.append([p0, p1, R[i + 1][:2], R[i][:2], p0])
    if res.get("fan_ok", True):
        p0 = (st[0]["x"], st[0]["y"])
        fan = [f[:2] for f in res["fan"]]
        parts.append([p0] + fan + [p0])
    return parts
