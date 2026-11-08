# -*- coding: utf-8 -*-
"""
Ren geometrisk beregning for "Grav grøfter" (ingen QGIS-afhængigheder,
så funktionerne kan testes selvstændigt).

Grøftens tværsnit:
    - vandret bund med bredden b omkring linjen
    - sideskråninger med anlæg 1:1,5 (1 m lodret pr. 1,5 m vandret, ca. 34 grader)

Udgravet areal i et tværsnit = arealet mellem terrænet og grøftens
designflade, hvor terrænet ligger over designfladen. Arealet integreres
ud til det sted på hver side, hvor skråningen skærer terrænet.
Volumen findes ved at integrere tværsnitsarealerne langs linjen
(trapezmetoden / "average end area").
"""

import bisect
import math

ANLAEG = 1.5  # vandret pr. lodret


def rens_bundpunkter(stationer, koter, tol=1e-6):
    """Sorterer (station, kote)-par og midler koter der ligger i samme station."""
    par = sorted(zip(stationer, koter))
    ud_s, ud_z, gruppe = [], [], []
    for s, z in par:
        if gruppe and abs(s - gruppe[0][0]) > tol:
            ud_s.append(gruppe[0][0])
            ud_z.append(sum(g[1] for g in gruppe) / len(gruppe))
            gruppe = []
        gruppe.append((s, z))
    if gruppe:
        ud_s.append(gruppe[0][0])
        ud_z.append(sum(g[1] for g in gruppe) / len(gruppe))
    return ud_s, ud_z


def bundkote(stationer, koter, s):
    """Lineær interpolation af bundkoten i station s.
    Før første / efter sidste punkt holdes koten vandret."""
    if not stationer:
        return None
    if s <= stationer[0]:
        return koter[0]
    if s >= stationer[-1]:
        return koter[-1]
    i = bisect.bisect_right(stationer, s)
    s0, s1 = stationer[i - 1], stationer[i]
    z0, z1 = koter[i - 1], koter[i]
    return z0 + (z1 - z0) * (s - s0) / (s1 - s0)


def _side_areal(terraen, zb, halv_bund, dtrin, max_offset, side):
    """Udgravet areal på én side af linjen (offset 0 -> skråningens top).
    terraen(o) returnerer terrænkote i offset o (fortegn = side) eller None.
    Returnerer (areal, antal_manglende_terraenvaerdier)."""

    def design(o):
        return zb + max(o - halv_bund, 0.0) / ANLAEG

    # Offsets: tæt inddeling over bunden (med bundkanten eksakt med), herefter fast trin
    n_bund = max(1, int(math.ceil(halv_bund / dtrin))) if halv_bund > 0 else 0
    offsets = [halv_bund * k / n_bund for k in range(n_bund + 1)] if n_bund else [0.0]

    areal = 0.0
    mangler = 0
    o_prev = None
    f_prev = None
    idx = 0
    while True:
        if idx < len(offsets):
            o = offsets[idx]
        else:
            o = offsets[-1] + (idx - len(offsets) + 1) * dtrin
        idx += 1
        if o > max_offset:
            break

        t = terraen(side * o)
        if t is None:
            mangler += 1
            if o > halv_bund:
                break  # kan ikke finde skråningens top uden terræn
            o_prev, f_prev = None, None
            continue

        f = t - design(o)

        if o_prev is not None:
            fp = max(f_prev, 0.0)
            fc = max(f, 0.0)
            if f_prev > 0 and f <= 0 and o > halv_bund:
                # Skråningen skærer terrænet mellem o_prev og o
                o_skaer = o_prev + (o - o_prev) * f_prev / (f_prev - f)
                areal += 0.5 * f_prev * (o_skaer - o_prev)
                break
            if f_prev > 0 and f <= 0:
                o_skaer = o_prev + (o - o_prev) * f_prev / (f_prev - f)
                areal += 0.5 * f_prev * (o_skaer - o_prev)
            elif f_prev <= 0 and f > 0:
                o_skaer = o_prev + (o - o_prev) * (-f_prev) / (f - f_prev)
                areal += 0.5 * f * (o - o_skaer)
            else:
                areal += 0.5 * (fp + fc) * (o - o_prev)

        if o > halv_bund and f <= 0:
            break  # terrænet ligger allerede under skråningen

        o_prev, f_prev = o, f

    return areal, mangler


def tvaersnitsareal(terraen, zb, bundbredde, dtrin, max_dybde=50.0):
    """Samlet udgravet tværsnitsareal [m2] i én station.
    terraen(offset) -> terrænkote eller None (offset vinkelret på linjen)."""
    halv = max(bundbredde, 0.0) / 2.0
    max_offset = halv + ANLAEG * max_dybde
    a1, m1 = _side_areal(terraen, zb, halv, dtrin, max_offset, +1)
    a2, m2 = _side_areal(terraen, zb, halv, dtrin, max_offset, -1)
    return a1 + a2, m1 + m2


def integrer_volumen(stationer, arealer):
    """Trapezintegration af arealer langs linjen -> volumen [m3]."""
    v = 0.0
    for i in range(1, len(stationer)):
        v += 0.5 * (arealer[i - 1] + arealer[i]) * (stationer[i] - stationer[i - 1])
    return v
