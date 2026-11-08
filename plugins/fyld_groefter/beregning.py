# -*- coding: utf-8 -*-
"""
Ren numpy-beregning af grøftens kanter og opfyldningsareal i ét tværsnit.
Modulet afhænger ikke af QGIS og kan derfor testes selvstændigt.

Tværsnittet er givet ved:
    s : afstande på tværs af stregen (negativ = højre side, positiv = venstre side),
        jævnt fordelt med prøveafstand ds og med s = 0 på stregen
    z : terrænkoter i de samme punkter (NaN = ingen data)
"""

import numpy as np

FYLD_RET_LINJE = 0     # fyld op til en ret linje mellem de to kanter
FYLD_LAVESTE_KANT = 1  # vandret fyld til laveste kant
FYLD_GNS_KANT = 2      # vandret fyld til gennemsnittet af kanterne


class Parametre(object):
    def __init__(self,
                 bund_soeg=3.0,        # [m] bunden søges inden for +/- denne afstand fra stregen
                 max_halvbredde=10.0,  # [m] længst ud fra bunden, der ledes efter kant
                 min_haeldning=0.10,   # [m/m] terræn fladere end dette regnes som "uden for grøften"
                 flad_laengde=1.0,     # [m] så langt skal terrænet være fladt, før kanten accepteres
                 top_tol=0.05,         # [m] fald efter toppunkt, der markerer at man er over kanten
                 min_dybde=0.10,       # [m] mindre dybde end dette = ingen grøft i tværsnittet
                 udglat=1.0,           # [m] bredde af glidende middel, der fjerner DEM-støj
                 fyldmetode=FYLD_RET_LINJE):
        self.bund_soeg = bund_soeg
        self.max_halvbredde = max_halvbredde
        self.min_haeldning = min_haeldning
        self.flad_laengde = flad_laengde
        self.top_tol = top_tol
        self.min_dybde = min_dybde
        self.udglat = udglat
        self.fyldmetode = fyldmetode


def _udglat(z, n):
    """Glidende middel over n punkter, som tåler NaN."""
    if n <= 1:
        return z.copy()
    gyldig = np.isfinite(z)
    v = np.where(gyldig, z, 0.0)
    k = np.ones(n)
    sum_v = np.convolve(v, k, mode="same")
    sum_n = np.convolve(gyldig.astype(float), k, mode="same")
    with np.errstate(invalid="ignore", divide="ignore"):
        ud = sum_v / sum_n
    ud[sum_n == 0] = np.nan
    return ud


def _find_kant(zs, start, retning, ds, p):
    """
    Gå fra bundindekset 'start' udad i 'retning' (+1/-1) og find kanten.

    Kanten er det første af disse:
      a) terrænet flader ud (hældning < min_haeldning over flad_laengde)
         -> kant = første punkt på den flade strækning
      b) terrænet falder igen mere end top_tol under højeste punkt
         -> kant = højeste punkt (dige/vold/opkast ved grøften)
      c) max_halvbredde nås eller data slutter
         -> kant = højeste punkt fundet (markeres som usikker)
    Returnerer (indeks, sikker)
    """
    n = len(zs)
    max_trin = int(round(p.max_halvbredde / ds))
    flad_trin = max(1, int(round(p.flad_laengde / ds)))

    i_top = start
    z_top = zs[start]
    kandidat = None
    flad_taeller = 0

    i = start
    for _ in range(max_trin):
        j = i + retning
        if j < 0 or j >= n or not np.isfinite(zs[j]):
            return i_top, False
        stigning = (zs[j] - zs[i]) / ds

        if zs[j] > z_top:
            z_top = zs[j]
            i_top = j

        # b) over toppen og ned igen
        if zs[j] < z_top - p.top_tol:
            return i_top, True

        # a) terrænet er fladt
        if stigning < p.min_haeldning:
            if kandidat is None:
                kandidat = i
            flad_taeller += 1
            if flad_taeller >= flad_trin:
                return kandidat, True
        else:
            kandidat = None
            flad_taeller = 0
        i = j

    return i_top, False


def beregn_tvaersnit(s, z, p):
    """
    Finder bund og kanter og beregner opfyldningsarealet (m2) i ét tværsnit.

    Returnerer dict med:
        areal, bund_i, venstre_i, hoejre_i, dybde, bredde, fyld_z_v, fyld_z_h,
        sikker (begge kanter fundet entydigt), ok (beregning mulig)
    """
    s = np.asarray(s, dtype=float)
    z = np.asarray(z, dtype=float)
    res = dict(areal=0.0, bund_i=None, venstre_i=None, hoejre_i=None,
               dybde=0.0, bredde=0.0, fyld_z_v=None, fyld_z_h=None,
               sikker=False, ok=False)
    if len(s) < 3:
        return res
    ds = float(s[1] - s[0])

    n_glat = max(1, int(round(p.udglat / ds)))
    if n_glat % 2 == 0:
        n_glat += 1
    zs = _udglat(z, n_glat)

    # 1) Bund: laveste (udglattede) punkt inden for søgeafstanden fra stregen
    inden = np.where((np.abs(s) <= p.bund_soeg + 1e-9) & np.isfinite(zs))[0]
    if len(inden) == 0:
        return res
    bund_i = int(inden[np.argmin(zs[inden])])
    res["bund_i"] = bund_i

    # 2) Kanter på hver side
    v_i, v_ok = _find_kant(zs, bund_i, +1, ds, p)   # venstre (s > 0)
    h_i, h_ok = _find_kant(zs, bund_i, -1, ds, p)   # højre (s < 0)
    res["venstre_i"] = v_i
    res["hoejre_i"] = h_i
    res["sikker"] = bool(v_ok and h_ok)
    res["ok"] = True

    if v_i == bund_i or h_i == bund_i:
        return res

    z_v = zs[v_i]
    z_h = zs[h_i]
    dybde = min(z_v, z_h) - zs[bund_i]
    res["dybde"] = float(max(dybde, 0.0))
    res["bredde"] = float(s[v_i] - s[h_i])
    if dybde < p.min_dybde:
        return res

    # 3) Fyldflade
    idx = np.arange(h_i, v_i + 1)
    ss = s[idx]
    if p.fyldmetode == FYLD_LAVESTE_KANT:
        fz_h = fz_v = min(z_v, z_h)
    elif p.fyldmetode == FYLD_GNS_KANT:
        fz_h = fz_v = 0.5 * (z_v + z_h)
    else:
        fz_h, fz_v = z_h, z_v
    fyld = fz_h + (ss - ss[0]) * (fz_v - fz_h) / (ss[-1] - ss[0])

    # Arealet regnes på de rå (ikke udglattede) koter; huller udfyldes lineært
    zr = z[idx].copy()
    mangler = ~np.isfinite(zr)
    if mangler.all():
        return res
    if mangler.any():
        zr[mangler] = np.interp(ss[mangler], ss[~mangler], zr[~mangler])
    d = np.clip(fyld - zr, 0.0, None)
    res["areal"] = float(np.sum(0.5 * (d[1:] + d[:-1]) * np.diff(ss)))
    res["fyld_z_v"] = float(fz_v)
    res["fyld_z_h"] = float(fz_h)
    return res


def samlet_volumen(arealer, laengde):
    """
    Volumen ud fra arealer i jævnt fordelte tværsnit (midtpunktsregel).
    Tværsnit uden resultat (NaN) udfyldes lineært fra naboerne.
    """
    a = np.asarray(arealer, dtype=float)
    if len(a) == 0:
        return None
    gyldig = np.isfinite(a)
    if not gyldig.any():
        return None
    if not gyldig.all():
        x = np.arange(len(a))
        a[~gyldig] = np.interp(x[~gyldig], x[gyldig], a[gyldig])
    return float(np.sum(a) * laengde / len(a))
