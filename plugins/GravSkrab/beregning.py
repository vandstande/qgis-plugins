# -*- coding: utf-8 -*-
"""
Beregningskerne for Grav Skrab (ren numpy, ingen QGIS-afhængigheder).

Princip
-------
Skrabets bund er polygonen i kote zb. Uden for bunden stiger gravefladen med
anlægget 1:n, dvs. i vandret afstand d fra bunden ligger gravefladen i kote

    S(d) = zb + d / n

Gravedybden i en celle er max(0, terræn - S). Jordmængden er summen af
gravedybder gange celleareal. Kun celler der hænger sammen med bunden
regnes med (isolerede bakker langt væk graves ikke af).
"""
import numpy as np


def _segmenter(ringe):
    """Liste af ringe (sekvenser af (x, y)) -> array med linjestykker [x1, y1, x2, y2]."""
    seg = []
    for ring in ringe:
        p = np.asarray(ring, dtype=float)
        if len(p) < 3:
            continue
        if not np.allclose(p[0], p[-1]):
            p = np.vstack([p, p[:1]])
        seg.append(np.hstack([p[:-1], p[1:]]))
    if not seg:
        return np.zeros((0, 4))
    return np.vstack(seg)


def afstand_og_inde(px, py, ringe):
    """
    Vandret afstand fra punkterne (px, py) til polygonen (0 inde i den) samt
    en maske for om punktet ligger inde i polygonen (lige/ulige-reglen, så
    huller og multipolygoner håndteres).
    """
    seg = _segmenter(ringe)
    d2 = np.full(px.shape, np.inf)
    inde = np.zeros(px.shape, dtype=bool)
    with np.errstate(divide="ignore", invalid="ignore"):
        for x1, y1, x2, y2 in seg:
            dx = x2 - x1
            dy = y2 - y1
            l2 = dx * dx + dy * dy
            if l2 > 0:
                t = ((px - x1) * dx + (py - y1) * dy) / l2
                np.clip(t, 0.0, 1.0, out=t)
            else:
                t = 0.0
            qx = x1 + t * dx - px
            qy = y1 + t * dy - py
            np.minimum(d2, qx * qx + qy * qy, out=d2)
            if dy != 0:
                krydser = (y1 > py) != (y2 > py)
                xs = x1 + (py - y1) * dx / dy
                inde ^= krydser & (px < xs)
    d = np.sqrt(d2)
    d[inde] = 0.0
    return d, inde


def forbundet(maske, frø):
    """Celler i maske der (8-naboskab) hænger sammen med frø-cellerne."""
    start = maske & frø
    if not start.any():
        return np.zeros_like(maske)
    try:
        from scipy import ndimage
        lab, _ = ndimage.label(maske, structure=np.ones((3, 3), dtype=int))
        ids = np.unique(lab[start])
        ids = ids[ids > 0]
        return np.isin(lab, ids)
    except Exception:
        cur = start.copy()
        while True:
            g = cur.copy()
            g[1:, :] |= cur[:-1, :]
            g[:-1, :] |= cur[1:, :]
            g[:, 1:] |= cur[:, :-1]
            g[:, :-1] |= cur[:, 1:]
            g[1:, 1:] |= cur[:-1, :-1]
            g[:-1, :-1] |= cur[1:, 1:]
            g[1:, :-1] |= cur[:-1, 1:]
            g[:-1, 1:] |= cur[1:, :-1]
            g &= maske
            if np.array_equal(g, cur):
                return cur
            cur = g


def _udvid(m):
    g = m.copy()
    g[1:, :] |= m[:-1, :]
    g[:-1, :] |= m[1:, :]
    g[:, 1:] |= m[:, :-1]
    g[:, :-1] |= m[:, 1:]
    g[1:, 1:] |= m[:-1, :-1]
    g[:-1, :-1] |= m[1:, 1:]
    g[1:, :-1] |= m[:-1, 1:]
    g[:-1, 1:] |= m[1:, :-1]
    return g


def laveste_rand(T, gyldig, udbredelse):
    """Laveste terrænkote i cellerne lige uden for skrabets øverste udbredelse."""
    rand = _udvid(udbredelse) & ~udbredelse & gyldig
    if not rand.any():
        return None
    return float(T[rand].min())


def vandvolumen(T, gyldig, S, udbredelse, zw, celleareal):
    """
    Volumen (m3) mellem den nye flade og vandspejlskoten zw inden for skrabets
    øverste udbredelse. Den nye flade er min(terræn, graveflade).
    Returnerer (volumen, vandareal m2).
    """
    znew = np.minimum(T, S)
    vand = udbredelse & gyldig & (znew < zw)
    vol = float((zw - znew[vand]).sum() * celleareal)
    areal = float(vand.sum() * celleareal)
    return vol, areal


def beregn_skrab(T, gyldig, X, Y, ringe, zb, anlaeg, celleareal, zw=None):
    """
    T, gyldig, X, Y : 2D-arrays (terrænkote, gyldighedsmaske, cellecentre)
    ringe           : polygonens ringe i samme koordinatsystem
    zb              : bundkote
    anlaeg          : n i anlæg 1:n
    celleareal      : m2 pr. celle

    Returnerer dict med volumen (m3), middeldybde (m) over bunden,
    maske for øverste udbredelse, om masken rammer kanten af udsnittet,
    antal manglende terrænværdier inden for udbredelsen m.m.
    """
    d, inde = afstand_og_inde(X, Y, ringe)
    S = zb + d / anlaeg
    dyb = np.where(gyldig, T - S, 0.0)
    grave = gyldig & (dyb > 0)

    frø = inde.copy()
    if not frø.any():
        # meget lille polygon i forhold til cellestørrelsen
        frø = d <= d.min() + 1e-9
    udbredelse = forbundet(grave | frø, frø)

    dyb = np.where(udbredelse & grave, dyb, 0.0)
    volumen = float(dyb.sum() * celleareal)

    bund = inde & gyldig
    if not bund.any():
        bund = frø & gyldig
    middel = float(np.maximum(T[bund] - zb, 0.0).mean()) if bund.any() else None

    lavter = laveste_rand(T, gyldig, udbredelse)
    vvol = vareal = zw_brugt = None
    if zw is not None:
        # Vandet kan ikke stå højere end det laveste punkt langs skrabets kant
        zw_brugt = zw if lavter is None else min(zw, lavter)
        vvol, vareal = vandvolumen(T, gyldig, S, udbredelse, zw_brugt, celleareal)

    rammer_kant = bool(udbredelse[0, :].any() or udbredelse[-1, :].any()
                       or udbredelse[:, 0].any() or udbredelse[:, -1].any())
    mangler = int((udbredelse & ~gyldig).sum())
    tmax = float(T[gyldig].max()) if gyldig.any() else None

    return {
        "volumen": volumen,
        "middeldybde": middel,
        "udbredelse": udbredelse,
        "rammer_kant": rammer_kant,
        "mangler": mangler,
        "tmax": tmax,
        "celler_i_bund": int(inde.sum()),
        "vandvol": vvol,
        "vandareal": vareal,
        "lavter": lavter,
        "zw_brugt": zw_brugt,
    }
