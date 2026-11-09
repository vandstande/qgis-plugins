# -*- coding: utf-8 -*-
"""
Skriver resultattabellen til Excel. Bruger openpyxl hvis det findes,
ellers GDAL's XLSX-driver, og som sidste udvej CSV.
"""
import csv
import os
from datetime import datetime

from . import beregning as b

KOLONNER = ["Opgørelse", "Fra", "Til", "Kriterium", "Areal (ha)"]


def _info(resultat, inddata):
    rx, ry = resultat["celle"]
    kontrol = [(f"Kontrol {navn}",
                f"{antal} polygoner – vektorareal {v:.2f} ha, rasterareal {r:.2f} ha")
               for navn, antal, v, r in resultat.get("kontrol", [])]
    return [
        ("Kørt", datetime.now().strftime("%d-%m-%Y %H:%M")),
        ("Nutidig afvandingsmodel", inddata["nutid"]),
        ("Fremtidig afvandingsmodel", inddata["fremtid"]),
        ("Naturbeskyttelse", inddata["natur"]),
        ("Felt med naturtypekode", inddata["felt"]),
        ("Projektgrænse", inddata["projekt"]),
        ("Vurderede naturtyper", inddata["typer"]),
        ("Koordinatsystem", resultat["crs"]),
        ("Cellestørrelse (m)", f"{rx:g} x {ry:g}"),
        ("Projektområde i alt (ha)", round(resultat["projekt_ha"], 2)),
        ("Projektområde uden modelværdi (ha)", round(resultat["uden_vaerdi_ha"], 2)),
        ("Sø", b.KRITERIE_TEKST[b.SOE]),
        ("Mose", b.KRITERIE_TEKST[b.MOSE]),
        ("Eng", b.KRITERIE_TEKST[b.ENG]),
        ("Metode", "Nuværende arealer er beregnet vektor-eksakt. Ændringer er beregnet "
                   "pr. rastercelle (cellecentrum) på den fremtidige models grid."),
        ("Ny natur – uden for naturlaget", "Opgøres alene ud fra den fremtidige afvanding – "
                                           "den nutidige afvanding indgår ikke"),
        ("Ny natur – anden naturtype i laget", "Polygoner med andre koder end 6, 3 og 1. "
                                               "Tælles kun hvis klassen ikke allerede er opfyldt "
                                               "i nutidig afvanding"),
        ("Polygoner med andre koder (omkring projektet)", resultat.get("andre_koder", 0)),
    ] + kontrol


def gem(sti, resultat, inddata):
    """Gemmer og returnerer stien til den faktisk skrevne fil."""
    try:
        return _openpyxl(sti, resultat, inddata)
    except ImportError:
        pass
    try:
        return _gdal(sti, resultat, inddata)
    except Exception:
        return _csv(os.path.splitext(sti)[0] + ".csv", resultat)


def _openpyxl(sti, resultat, inddata):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Naturvurdering"
    ws.append(KOLONNER)
    fed = Font(bold=True, color="FFFFFF")
    fyld = PatternFill("solid", fgColor="2E6B4F")
    for c in ws[1]:
        c.font = fed
        c.fill = fyld
        c.alignment = Alignment(vertical="center")
    for r in resultat["raekker"]:
        ws.append([r[0], r[1], r[2], r[3], round(r[4], 2)])
        ws.cell(row=ws.max_row, column=5).number_format = "0.00"
    for kol, bredde in zip("ABCDE", (44, 18, 12, 46, 12)):
        ws.column_dimensions[kol].width = bredde
    ws.freeze_panes = "A2"

    wi = wb.create_sheet("Info")
    wi.append(["Parameter", "Værdi"])
    for c in wi[1]:
        c.font = fed
        c.fill = fyld
    for r in _info(resultat, inddata):
        wi.append(list(r))
    wi.column_dimensions["A"].width = 36
    wi.column_dimensions["B"].width = 80

    wb.save(sti)
    return sti


def _gdal(sti, resultat, inddata):
    from osgeo import gdal, ogr
    gdal.UseExceptions()
    drv = ogr.GetDriverByName("XLSX")
    if os.path.exists(sti):
        drv.DeleteDataSource(sti)
    ds = drv.CreateDataSource(sti)
    lyr = ds.CreateLayer("Naturvurdering", geom_type=ogr.wkbNone)
    for i, navn in enumerate(KOLONNER):
        lyr.CreateField(ogr.FieldDefn(navn, ogr.OFTReal if i == 4 else ogr.OFTString))
    for r in resultat["raekker"]:
        f = ogr.Feature(lyr.GetLayerDefn())
        for i, v in enumerate(r):
            f.SetField(i, round(v, 2) if i == 4 else v)
        lyr.CreateFeature(f)
    li = ds.CreateLayer("Info", geom_type=ogr.wkbNone)
    li.CreateField(ogr.FieldDefn("Parameter", ogr.OFTString))
    li.CreateField(ogr.FieldDefn("Værdi", ogr.OFTString))
    for k, v in _info(resultat, inddata):
        f = ogr.Feature(li.GetLayerDefn())
        f.SetField(0, k)
        f.SetField(1, str(v))
        li.CreateFeature(f)
    ds = None
    return sti


def _csv(sti, resultat):
    with open(sti, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(KOLONNER)
        for r in resultat["raekker"]:
            w.writerow([r[0], r[1], r[2], r[3], f"{r[4]:.2f}".replace(".", ",")])
    return sti
