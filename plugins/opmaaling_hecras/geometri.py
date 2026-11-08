# -*- coding: utf-8 -*-
"""
Ren Python-geometri for "Opmaaling HECRAS".

Alle funktioner arbejder på lister af (x, y)-tupler, så beregningerne
kan testes uden QGIS. Plugin-koden (opmaaling_hecras.py) oversætter
mellem QGIS-geometrier og disse lister.
"""
import math


# ---------------------------------------------------------------------------
# Polylinje-hjælpere
# ---------------------------------------------------------------------------
class Polylinje:
    """En enkelt polylinje med kumulative stationeringer."""

    def __init__(self, punkter):
        rene = []
        for p in punkter:
            p = (float(p[0]), float(p[1]))
            if not rene or (abs(p[0] - rene[-1][0]) > 1e-9 or abs(p[1] - rene[-1][1]) > 1e-9):
                rene.append(p)
        if len(rene) < 2:
            raise ValueError("Polylinjen skal have mindst to forskellige punkter")
        self.p = rene
        self.cum = [0.0]
        for i in range(1, len(rene)):
            self.cum.append(self.cum[-1] + _dist(rene[i - 1], rene[i]))
        self.laengde = self.cum[-1]

    # -- segment der indeholder stationering s
    def _seg(self, s):
        s = max(0.0, min(self.laengde, s))
        for i in range(1, len(self.cum)):
            if s <= self.cum[i] + 1e-12:
                return i - 1, s
        return len(self.cum) - 2, s

    def punkt(self, s):
        i, s = self._seg(s)
        a, b = self.p[i], self.p[i + 1]
        seglen = self.cum[i + 1] - self.cum[i]
        t = 0.0 if seglen == 0 else (s - self.cum[i]) / seglen
        return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))

    def _segretning(self, i):
        a, b = self.p[i], self.p[i + 1]
        l = _dist(a, b)
        return ((b[0] - a[0]) / l, (b[1] - a[1]) / l)

    def retning(self, s):
        """Enhedsvektor for linjens retning ved s (middel ved knudepunkter)."""
        i, s = self._seg(s)
        d = self._segretning(i)
        tol = 1e-9
        if abs(s - self.cum[i + 1]) < tol and i + 1 < len(self.p) - 1:
            d2 = self._segretning(i + 1)
            d = _norm((d[0] + d2[0], d[1] + d2[1]), d)
        elif abs(s - self.cum[i]) < tol and i > 0:
            d0 = self._segretning(i - 1)
            d = _norm((d[0] + d0[0], d[1] + d0[1]), d)
        return d

    def venstre_normal(self, s):
        dx, dy = self.retning(s)
        return (-dy, dx)

    def lokaliser(self, pt):
        """Returnerer (stationering, fortegnet afstand) for pt.
        Positiv afstand = venstre for linjen set i linjens retning."""
        bedst = None
        for i in range(len(self.p) - 1):
            a, b = self.p[i], self.p[i + 1]
            q, t = _naermeste_paa_segment(pt, a, b)
            d = _dist(pt, q)
            if bedst is None or d < bedst[0] - 1e-12:
                bedst = (d, i, t, q)
        d, i, t, q = bedst
        s = self.cum[i] + t * (self.cum[i + 1] - self.cum[i])
        dx, dy = self.retning(s)
        kryds = dx * (pt[1] - q[1]) - dy * (pt[0] - q[0])
        return s, (d if kryds >= 0 else -d)

    def skaering_med_segment(self, a, b):
        """Stationering(er) hvor segmentet a-b krydser polylinjen."""
        res = []
        for i in range(len(self.p) - 1):
            r = _segment_skaering(a, b, self.p[i], self.p[i + 1])
            if r is not None:
                t_ab, t_pl, pt = r
                s = self.cum[i] + t_pl * (self.cum[i + 1] - self.cum[i])
                res.append((s, t_ab, pt))
        return res


# ---------------------------------------------------------------------------
# Små vektorfunktioner
# ---------------------------------------------------------------------------
def _dist(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _norm(v, fallback):
    l = math.hypot(v[0], v[1])
    if l < 1e-12:
        return fallback
    return (v[0] / l, v[1] / l)


def _naermeste_paa_segment(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 == 0:
        return a, 0.0
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2
    t = max(0.0, min(1.0, t))
    return (a[0] + t * dx, a[1] + t * dy), t


def _segment_skaering(p1, p2, q1, q2):
    r = (p2[0] - p1[0], p2[1] - p1[1])
    s = (q2[0] - q1[0], q2[1] - q1[1])
    rxs = r[0] * s[1] - r[1] * s[0]
    if abs(rxs) < 1e-12:
        return None
    qp = (q1[0] - p1[0], q1[1] - p1[1])
    t = (qp[0] * s[1] - qp[1] * s[0]) / rxs
    u = (qp[0] * r[1] - qp[1] * r[0]) / rxs
    eps = 1e-9
    if -eps <= t <= 1 + eps and -eps <= u <= 1 + eps:
        return t, u, (p1[0] + t * r[0], p1[1] + t * r[1])
    return None


def naermeste_paa_polylinje(pt, punkter):
    """(nærmeste punkt, afstand, stationering langs linjen) for pt."""
    bedst = None
    cum = 0.0
    for i in range(len(punkter) - 1):
        a, b = punkter[i], punkter[i + 1]
        q, t = _naermeste_paa_segment(pt, a, b)
        d = _dist(pt, q)
        seglen = _dist(a, b)
        if bedst is None or d < bedst[1] - 1e-12:
            bedst = (q, d, cum + t * seglen)
        cum += seglen
    return bedst


# ---------------------------------------------------------------------------
# Tværprofiler
# ---------------------------------------------------------------------------
def find_profilpar(punkter, startkode, slutkode):
    """
    punkter: liste af (id, kode) i tabellens rækkefølge.
    Returnerer (par, advarsler) hvor par er liste af (start_index, slut_index).
    Et profil starter i et punkt med startkode og slutter i det næste punkt
    i tabellen med slutkode.
    """
    sk = (startkode or "").strip().upper()
    ek = (slutkode or "").strip().upper()
    par, advarsler = [], []
    i, n = 0, len(punkter)
    while i < n:
        if _kode(punkter[i][1]) == sk:
            j = i + 1
            while j < n and _kode(punkter[j][1]) not in (ek, sk):
                j += 1
            if j < n and _kode(punkter[j][1]) == ek:
                par.append((i, j))
                i = j + 1
                continue
            if j < n:
                advarsler.append("Punkt %s (%s) har ingen %s før næste %s - springes over"
                                 % (punkter[i][0], startkode, slutkode, startkode))
            else:
                advarsler.append("Punkt %s (%s) har ingen efterfølgende %s - springes over"
                                 % (punkter[i][0], startkode, slutkode))
            i = j
            continue
        i += 1
    return par, advarsler


def _kode(v):
    if v is None:
        return ""
    return str(v).strip().upper()


# ---------------------------------------------------------------------------
# Bank lines
# ---------------------------------------------------------------------------
def byg_banklinje(center, bankpunkter, max_afstand=None, bue_grader=10.0):
    """
    center: Polylinje (vandløbsmidten).
    bankpunkter: liste af (profil_station, (x, y)) - profil_station er hvor
                 tværprofilet krydser centerlinjen.
    Returnerer liste af (x, y) der går gennem bankpunkterne i
    stationsrækkefølge og mellem dem følger centerlinjens forløb.

    Metode: linjen er en parallelforskydning af centerlinjen, hvor
    sideafstanden (og en lille skævheds-korrektion) interpoleres lineært
    mellem profilerne. Ved knæk i centerlinjen:
      - ydersiden af knækket afrundes med en bue
      - indersiden får et geringshjørne (skæringen mellem de to parallelle
        linjer); eventuelle løkker klippes væk, men aldrig forbi et bankpunkt
    max_afstand bruges ikke længere (bevaret for kompatibilitet).
    """
    if len(bankpunkter) < 2:
        return [p for _, p in bankpunkter]
    bp = sorted(bankpunkter, key=lambda t: t[0])

    # side-afstand d og residual-vektor r for hvert bankpunkt, så
    # C(S) + n(S)*d + r == P præcist
    info = []
    for S, P in bp:
        C = center.punkt(S)
        n = center.venstre_normal(S)
        v = (P[0] - C[0], P[1] - C[1])
        d = v[0] * n[0] + v[1] * n[1]
        r = (v[0] - n[0] * d, v[1] - n[1] * d)
        info.append((S, P, d, r))

    trin = math.radians(bue_grader)
    ud = [bp[0][1]]
    beskyt = {0}
    for k in range(len(info) - 1):
        S0, P0, d0, r0 = info[k]
        S1, P1, d1, r1 = info[k + 1]
        if S1 - S0 < 1e-9:
            ud.append(P1)
            beskyt.add(len(ud) - 1)
            continue
        stykke = [P0]
        for vi in range(1, len(center.p) - 1):
            sv = center.cum[vi]
            if not (S0 + 1e-6 < sv < S1 - 1e-6):
                continue
            t = (sv - S0) / (S1 - S0)
            d = d0 + t * (d1 - d0)
            r = (r0[0] + t * (r1[0] - r0[0]), r0[1] + t * (r1[1] - r0[1]))
            C = center.p[vi]
            din = center._segretning(vi - 1)
            dud = center._segretning(vi)
            n_ind = (-din[1], din[0])
            n_ud = (-dud[1], dud[0])
            drej = din[0] * dud[1] - din[1] * dud[0]          # >0 = venstresving
            vinkel = math.atan2(drej, din[0] * dud[0] + din[1] * dud[1])
            if drej * d > 0:
                # inderside: gering (skæringen mellem de to parallelle linjer)
                m = (n_ind[0] + n_ud[0], n_ind[1] + n_ud[1])
                l2 = m[0] * m[0] + m[1] * m[1]
                if l2 > 0.04:           # knæk under ca. 157 grader
                    f = 2.0 * d / l2
                    stykke.append((C[0] + m[0] * f + r[0], C[1] + m[1] * f + r[1]))
                    continue
                normaler = [n_ind, n_ud]    # hårnål: løkken klippes bagefter
            else:
                normaler = [n_ind]
                if abs(vinkel) > trin:      # yderside: bue
                    antal = int(abs(vinkel) / trin)
                    a0 = math.atan2(n_ind[1], n_ind[0])
                    for m_ in range(1, antal + 1):
                        a = a0 + vinkel * m_ / (antal + 1)
                        normaler.append((math.cos(a), math.sin(a)))
                normaler.append(n_ud)
            for n in normaler:
                stykke.append((C[0] + n[0] * d + r[0], C[1] + n[1] * d + r[1]))
        stykke.append(P1)
        # løkker inden for et profilinterval fjernes (endepunkterne bevares)
        stykke = fjern_loekker(stykke)
        ud.extend(stykke[1:])
        beskyt.add(len(ud) - 1)
    # løkker på tværs af profiler fjernes, men aldrig forbi et bankpunkt
    return fjern_loekker(ud, beskyt)


def fjern_loekker(punkter, beskyt=None):
    """Fjerner løkker hvor en polylinje krydser sig selv: ved hvert kryds
    klippes stykket mellem de to krydsende segmenter væk.
    beskyt: indeks (i punkter) på punkter der aldrig må klippes væk."""
    if len(punkter) < 4:
        return list(punkter)
    xs = [p[0] for p in punkter]
    ys = [p[1] for p in punkter]
    total = sum(_dist(punkter[i], punkter[i + 1]) for i in range(len(punkter) - 1))
    celle = max(total / max(len(punkter), 1) * 4.0,
                (max(xs) - min(xs) + max(ys) - min(ys)) / 2000.0, 1e-6)
    grid = {}
    ud = [punkter[0]]
    laast = 0          # punkter i ud med indeks < laast må ikke fjernes

    def celler(a, b):
        x0, x1 = sorted((a[0], b[0]))
        y0, y1 = sorted((a[1], b[1]))
        for gx in range(int(math.floor(x0 / celle)), int(math.floor(x1 / celle)) + 1):
            for gy in range(int(math.floor(y0 / celle)), int(math.floor(y1 / celle)) + 1):
                yield (gx, gy)

    def tilfoej_seg(idx):
        for c in celler(ud[idx], ud[idx + 1]):
            grid.setdefault(c, []).append(idx)

    for qi in range(1, len(punkter)):
        q = punkter[qi]
        a = ud[-1]
        if _dist(a, q) < 1e-9:
            if beskyt and qi in beskyt:
                laast = len(ud)
            continue
        # find tidligste (ikke-nabo) segment som a-q krydser
        bedst = None
        nseg = len(ud) - 1          # gyldige segmenter: 0 .. nseg-1
        set_ = set()
        for c in celler(a, q):
            for j in grid.get(c, ()):
                if j < nseg - 1 and j + 1 >= laast and j not in set_:     # ikke det forrige segment
                    set_.add(j)
                    r = _segment_skaering(a, q, ud[j], ud[j + 1])
                    if r is not None and (bedst is None or j < bedst[0]):
                        bedst = (j, r[2])
        if bedst is not None:
            j, x = bedst
            del ud[j + 1:]
            for c in list(grid):
                grid[c] = [i for i in grid[c] if i < j]
            if _dist(ud[-1], x) > 1e-9:
                ud.append(x)
                tilfoej_seg(len(ud) - 2)
        ud.append(q)
        tilfoej_seg(len(ud) - 2)
        if beskyt and qi in beskyt:
            laast = len(ud)
    return ud
