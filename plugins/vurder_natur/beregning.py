# -*- coding: utf-8 -*-
"""
Ren numpy-beregning af naturændringer (ingen QGIS/GDAL-afhængighed,
så den kan testes for sig).

Afvandingsmodellen angiver afvandingsdybde (m). Negative værdier = vand
over terræn.
"""
import numpy as np

# Naturtypekoder i naturbeskyttelseslaget
SOE = 6
MOSE = 3
ENG = 1
NAVNE = {SOE: "Sø", MOSE: "Mose", ENG: "Eng"}

# Afvandingsintervaller (m). Ret her, hvis grænserne skal justeres.
SOE_MAX = 0.0    # Sø:   afvanding <  0,0
MOSE_MAX = 0.5   # Mose: 0,0 <= afvanding <= 0,5
ENG_MAX = 1.0    # Eng:  0,5 <  afvanding <= 1,0

KRITERIE_TEKST = {
    SOE: "afvanding < 0,0 m",
    MOSE: "0,0 m ≤ afvanding ≤ 0,5 m",
    ENG: "0,5 m < afvanding ≤ 1,0 m",
}


def klasse_maske(a, kode):
    """Celler i afvandingsintervallet for en given naturtype."""
    if kode == SOE:
        return a < SOE_MAX
    if kode == MOSE:
        return (a >= SOE_MAX) & (a <= MOSE_MAX)
    if kode == ENG:
        return (a > MOSE_MAX) & (a <= ENG_MAX)
    raise ValueError(kode)


def ny_klasse(fremtid, nutid, kode):
    """Celler der i fremtid ligger i intervallet, men ikke gør det i dag."""
    return klasse_maske(fremtid, kode) & ~klasse_maske(nutid, kode)


def beregn(fremtid, nutid, gyldig, projekt, natur_kode, typer, celle_ha,
           gyldig_fremtid=None, natur_andre=None):
    """
    fremtid, nutid : float-arrays (samme grid)
    gyldig         : bool-array, hvor begge modeller har værdi
    projekt        : bool-array, inden for projektgrænsen
    natur_kode     : dict {kode: bool-array} for kode 6, 3 og 1
    natur_andre    : bool-array, polygoner i naturlaget med andre koder
    typer          : set af valgte koder (1, 3, 6)
    celle_ha       : areal af én celle i ha

    Returnerer liste af rækker: (afsnit, fra, til, kriterium, ha)
    """
    f = np.where(gyldig, fremtid, np.nan)
    n = np.where(gyldig, nutid, np.nan)
    with np.errstate(invalid="ignore"):
        base = projekt & gyldig

        def ha(m):
            return float(np.count_nonzero(m & base)) * celle_ha

        raekker = []
        afsnit = "Ændret naturtype – beskyttede arealer"
        if MOSE in typer:
            raekker.append((afsnit, "Mose (3)", "Sø (6)",
                            "Fremtid: " + KRITERIE_TEKST[SOE] + ", ikke allerede i dag",
                            ha(natur_kode[MOSE] & ny_klasse(f, n, SOE))))
        if ENG in typer:
            raekker.append((afsnit, "Eng (1)", "Sø (6)",
                            "Fremtid: " + KRITERIE_TEKST[SOE] + ", ikke allerede i dag",
                            ha(natur_kode[ENG] & ny_klasse(f, n, SOE))))
            raekker.append((afsnit, "Eng (1)", "Mose (3)",
                            "Fremtid: " + KRITERIE_TEKST[MOSE] + ", ikke allerede i dag",
                            ha(natur_kode[ENG] & ny_klasse(f, n, MOSE))))

        # Ny natur: alt der ikke i dag er udpeget som sø, mose eller eng.
        # Alle tre koder regnes altid som udpeget, uanset afkrydsning.
        #  a) Uden for naturbeskyttelseslaget: KUN fremtidig afvanding tæller.
        #  b) Polygoner med andre koder: fremtidig klasse, som ikke allerede
        #     er opfyldt i nutidig afvanding (som for de beskyttede arealer).
        afsnit = "Ny natur – arealer der ikke er udpeget som sø, mose eller eng"
        ubeskyttet = ~(natur_kode[SOE] | natur_kode[MOSE] | natur_kode[ENG])
        if natur_andre is None:
            natur_andre = np.zeros_like(ubeskyttet)
        udenfor = ubeskyttet & ~natur_andre
        andre = ubeskyttet & natur_andre
        gf = gyldig if gyldig_fremtid is None else gyldig_fremtid
        ff = np.where(gf, fremtid, np.nan)
        for kode in (SOE, MOSE, ENG):
            m = udenfor & klasse_maske(ff, kode) & projekt & gf
            raekker.append((afsnit, "Uden for naturlaget", f"{NAVNE[kode]} ({kode})",
                            "Fremtid: " + KRITERIE_TEKST[kode] + " (uanset nutidig afvanding)",
                            float(np.count_nonzero(m)) * celle_ha))
        for kode in (SOE, MOSE, ENG):
            raekker.append((afsnit, "Anden naturtype i laget", f"{NAVNE[kode]} ({kode})",
                            "Fremtid: " + KRITERIE_TEKST[kode] + ", ikke allerede i dag",
                            ha(andre & ny_klasse(f, n, kode))))

        ingen_vaerdi = float(np.count_nonzero(projekt & ~gyldig)) * celle_ha
    return raekker, ingen_vaerdi
