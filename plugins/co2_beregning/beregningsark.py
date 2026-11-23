# -*- coding: utf-8 -*-
"""
Udfylder SGAV/Miljøstyrelsens "Beregningsark CO2-effekt" (fanen 'Beregningsark').

Filen ændres direkte i sin XML (kun standardbiblioteket), så rullelister, billeder,
formatering m.m. bevares præcis som i skabelonen. Kun disse celler skrives:

  D4          Projektnavn
  D5          Samlet projektareal (ha), målt på laget Projektgrænse
  E51         Formlen =D5-SUM(E27:E50) (resterende areal; arealtjekket i E53 bliver OK)
  E27:E38     Uden buffer  (>12 %, 6-12 %, <6 %) x (Omdrift, Perm. græs, Natur, Øvrige IMK)
  E39:E50     Med buffer   (samme rækkefølge)

Formlerne (H-kolonnen, sum og arealtjek) får opdaterede cache-værdier, og Excel
bliver bedt om at genberegne ved åbning.
"""

import re
import shutil
import tempfile
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

ARK_NAVN = "Beregningsark"

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"

# Tørveklasse fra CO2-kortet -> klasse i arket
KLASSE_MAP = {">12": ">12", "6-12": "6-12", "3-6": "<6", "<3": "<6"}

# Første række for hver blok; de fire drifttyper følger i rækkefølgen
# Omdrift, Permanent græs, Natur, Øvrige IMK
START_RAEKKE = {
    ("uden_buffer", ">12"): 27,
    ("uden_buffer", "6-12"): 31,
    ("uden_buffer", "<6"): 35,
    ("med_buffer", ">12"): 39,
    ("med_buffer", "6-12"): 43,
    ("med_buffer", "<6"): 47,
}


class BeregningsarkFejl(Exception):
    pass


def saml_arealer(raekker):
    """
    raekker: iterable af (lag, toervklasse, [omdrift, permgraes, natur, oevrige]) i ha.
    Returnerer (blokke, ukendte) hvor blokke[(lag, arkklasse)] = [4 arealer].
    """
    blokke = {k: [0.0, 0.0, 0.0, 0.0] for k in START_RAEKKE}
    ukendte = {}
    for lag, toerv, arealer in raekker:
        noegle = None if toerv is None else str(toerv).replace(" ", "").replace("%", "")
        klasse = KLASSE_MAP.get(noegle)
        if klasse is None or (lag, klasse) not in blokke:
            ukendte[toerv] = ukendte.get(toerv, 0.0) + sum(arealer)
            continue
        blokke[(lag, klasse)] = [a + b for a, b in zip(blokke[(lag, klasse)], arealer)]
    for k in blokke:
        blokke[k] = [round(a, 2) for a in blokke[k]]
    return blokke, ukendte


# ---------------------------------------------------------------- XML-hjælpere
def _find_ark_sti(zf):
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    rid = None
    for sh in wb.iter("{%s}sheet" % NS_MAIN):
        if sh.get("name") == ARK_NAVN:
            rid = sh.get("{%s}id" % NS_REL)
            break
    if rid is None:
        raise BeregningsarkFejl("Regnearket har ingen fane med navnet '{}'.".format(ARK_NAVN))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    for rel in rels.iter("{%s}Relationship" % NS_PKG):
        if rel.get("Id") == rid:
            target = rel.get("Target").lstrip("/")
            return target if target.startswith("xl/") else "xl/" + target
    raise BeregningsarkFejl("Kunne ikke finde fanen '{}' i filen.".format(ARK_NAVN))


def _celle_regex(ref):
    return re.compile(r'<c r="%s"(?P<attr>(?:\s+[^>]*?)?)\s*(?:/>|>(?P<indhold>.*?)</c>)' % ref, re.S)


def _style(attr):
    m = re.search(r'\ss="(\d+)"', attr or "")
    return ' s="%s"' % m.group(1) if m else ""


def _saet_vaerdi(xml, ref, vaerdi):
    """Skriv tal eller tekst i en (eksisterende) celle uden formel."""
    rx = _celle_regex(ref)
    m = rx.search(xml)
    if m is None:
        raise BeregningsarkFejl("Cellen {} findes ikke i fanen '{}' – er det den rigtige skabelon?"
                                .format(ref, ARK_NAVN))
    s = _style(m.group("attr"))
    if isinstance(vaerdi, str):
        ny = '<c r="%s"%s t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (
            ref, s, escape(vaerdi))
    else:
        ny = '<c r="%s"%s><v>%s</v></c>' % (ref, s, repr(float(vaerdi)))
    return xml[:m.start()] + ny + xml[m.end():]


def _laes_tal(xml, ref):
    m = _celle_regex(ref).search(xml)
    if m is None or not m.group("indhold"):
        return 0.0
    v = re.search(r"<v>([^<]*)</v>", m.group("indhold"))
    try:
        return float(v.group(1)) if v else 0.0
    except ValueError:
        return 0.0


def _saet_cache(xml, ref, vaerdi):
    """Opdatér den gemte værdi i en formelcelle (formlen bevares)."""
    rx = _celle_regex(ref)
    m = rx.search(xml)
    if m is None or not m.group("indhold") or "<f" not in m.group("indhold"):
        return xml
    indhold = re.sub(r"<v>[^<]*</v>", "", m.group("indhold"))
    attr = m.group("attr") or ""
    if isinstance(vaerdi, str):
        attr = re.sub(r'\st="[^"]*"', "", attr) + ' t="str"'
        v = "<v>%s</v>" % escape(vaerdi)
    else:
        attr = re.sub(r'\st="[^"]*"', "", attr)
        v = "<v>%s</v>" % repr(float(vaerdi))
    ny = '<c r="%s"%s>%s%s</c>' % (ref, attr, indhold, v)
    return xml[:m.start()] + ny + xml[m.end():]


def _saet_formel(xml, ref, formel, vaerdi):
    """Erstat cellens indhold med en ny formel (talresultat) og cache-værdi."""
    m = _celle_regex(ref).search(xml)
    if m is None:
        raise BeregningsarkFejl("Cellen {} findes ikke i fanen '{}'.".format(ref, ARK_NAVN))
    ny = '<c r="%s"%s><f>%s</f><v>%s</v></c>' % (
        ref, _style(m.group("attr")), escape(formel), repr(float(vaerdi)))
    return xml[:m.start()] + ny + xml[m.end():]


# ------------------------------------------------------------------- udfyld
def udfyld(skabelon, destination, projektnavn, projektareal, blokke):
    """Skriv en udfyldt kopi af skabelonen til destination. Returnerer (fordelt areal E27:E50, rest i E51)."""
    with zipfile.ZipFile(skabelon) as zf:
        ark_sti = _find_ark_sti(zf)
        xml = zf.read(ark_sti).decode("utf-8")
        wb_xml = zf.read("xl/workbook.xml").decode("utf-8")

        if projektnavn:
            xml = _saet_vaerdi(xml, "D4", projektnavn)
        xml = _saet_vaerdi(xml, "D5", round(projektareal, 2))

        for (lag, klasse), start in START_RAEKKE.items():
            for i, areal in enumerate(blokke[(lag, klasse)]):
                xml = _saet_vaerdi(xml, "E%d" % (start + i), areal)

        # E51: resterende areal = projektareal minus de fordelte arealer (E27:E50).
        # (Ikke D5-E52, da E52 selv summerer E51 -> cirkulær reference.)
        d5 = round(projektareal, 2)
        fordelt = sum(_laes_tal(xml, "E%d" % r) for r in range(27, 51))
        rest = round(d5 - fordelt, 2)
        xml = _saet_formel(xml, "E51", "D5-SUM(E27:E50)", rest)

        # Cache-værdier for formlerne, så tallene også ses uden genberegning
        sum_e = 0.0
        sum_h = 0.0
        for r in range(27, 52):
            e = _laes_tal(xml, "E%d" % r)
            f = _laes_tal(xml, "F%d" % r)
            sum_e += e
            sum_h += e * f
            xml = _saet_cache(xml, "H%d" % r, e * f)
        sum_e = round(sum_e, 10)
        xml = _saet_cache(xml, "E52", sum_e)
        xml = _saet_cache(xml, "H52", sum_h)
        tjek = "OK" if abs(sum_e - d5) < 1e-9 else "Fejl"
        xml = _saet_cache(xml, "E53", tjek)   # arkets egen formel i E53 bevares

        # Bed Excel om at genberegne alt ved åbning
        if re.search(r"<calcPr\b[^>]*fullCalcOnLoad=", wb_xml) is None:
            wb_xml = re.sub(r"<calcPr\b", '<calcPr fullCalcOnLoad="1"', wb_xml, count=1)

        tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        tmp.close()
        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as ud:
            for item in zf.infolist():
                if item.filename == ark_sti:
                    data = xml.encode("utf-8")
                elif item.filename == "xl/workbook.xml":
                    data = wb_xml.encode("utf-8")
                else:
                    data = zf.read(item.filename)
                ud.writestr(item, data)

    try:
        shutil.move(tmp.name, destination)
    except PermissionError:
        raise BeregningsarkFejl(
            "Kan ikke gemme '{}'. Er filen åben i Excel? Luk den og prøv igen.".format(destination))
    return round(fordelt, 2), rest
