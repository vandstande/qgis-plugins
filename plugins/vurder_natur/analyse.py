# -*- coding: utf-8 -*-
"""
Indlæser rastre og polygoner, lægger dem på samme grid og kalder beregningen.
"""
import math

import numpy as np
from osgeo import gdal, ogr, osr

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsGeometry,
    QgsProject,
    QgsWkbTypes,
)

from . import beregning as b

NODATA = -99999.0


class AnalyseFejl(Exception):
    pass


def _kode(v):
    """Naturtypekoden kan være tal eller tekst ('6', 6, 6.0)."""
    try:
        return int(float(str(v).strip().replace(",", ".")))
    except (TypeError, ValueError):
        return None


def _gyldig_geom(g):
    """
    Returnerer en gyldig, lineær 2D-(multi)polygon, som både GEOS og GDAL
    kan arbejde med – eller None.
    Håndterer buede geometrier (CurvePolygon/MultiSurface), Z/M-værdier og
    GeometryCollections, som makeValid() kan returnere.
    """
    if g is None or g.isNull() or g.isEmpty():
        return None
    if QgsWkbTypes.isCurvedType(g.wkbType()):
        g = QgsGeometry(g.constGet().segmentize())
    if QgsWkbTypes.hasZ(g.wkbType()) or QgsWkbTypes.hasM(g.wkbType()):
        a = g.constGet().clone()
        a.dropZValue()
        a.dropMValue()
        g = QgsGeometry(a)
    if not g.isGeosValid():
        g = g.makeValid()
    if g.type() != QgsWkbTypes.PolygonGeometry:
        # fx GeometryCollection: behold kun fladerne
        dele = [d for d in g.asGeometryCollection()
                if d.type() == QgsWkbTypes.PolygonGeometry and not d.isEmpty()]
        if not dele:
            return None
        g = QgsGeometry.collectGeometry(dele)
    if g.isEmpty():
        return None
    g.convertToMultiType()
    return g


def _raster_sti(lyr):
    return lyr.dataProvider().dataSourceUri().split("|")[0]


def _projekt_geometri(lyr, crs):
    tr = QgsCoordinateTransform(lyr.crs(), crs, QgsProject.instance())
    geoms = []
    for f in lyr.getFeatures():
        g = QgsGeometry(f.geometry())
        if g.isEmpty():
            continue
        g.transform(tr)
        g = _gyldig_geom(g)
        if g:
            geoms.append(g)
    if not geoms:
        raise AnalyseFejl("Projektgrænselaget indeholder ingen polygoner.")
    return QgsGeometry.unaryUnion(geoms)


def _grid(ds, bbox):
    """Udsnit af fremtids-rastrets grid, der dækker projektgrænsen."""
    gt = ds.GetGeoTransform()
    if gt[2] != 0 or gt[4] != 0:
        raise AnalyseFejl("Roterede rastre understøttes ikke.")
    rx, ry = gt[1], abs(gt[5])
    x0, y0 = gt[0], gt[3]
    minx = x0 + math.floor((bbox.xMinimum() - x0) / rx) * rx
    maxx = x0 + math.ceil((bbox.xMaximum() - x0) / rx) * rx
    maxy = y0 - math.floor((y0 - bbox.yMaximum()) / ry) * ry
    miny = y0 - math.ceil((y0 - bbox.yMinimum()) / ry) * ry
    ncol = int(round((maxx - minx) / rx))
    nrow = int(round((maxy - miny) / ry))
    return (minx, miny, maxx, maxy), rx, ry, ncol, nrow


def _laes_raster(lyr, bounds, rx, ry, wkt):
    src_wkt = lyr.crs().toWkt(QgsCoordinateReferenceSystem.WKT_PREFERRED_GDAL)
    ds = gdal.Warp(
        "", _raster_sti(lyr), format="MEM",
        outputBounds=bounds, xRes=rx, yRes=ry,
        srcSRS=src_wkt or None, dstSRS=wkt,
        resampleAlg="near", dstNodata=NODATA,
        outputType=gdal.GDT_Float32,
    )
    if ds is None:
        raise AnalyseFejl(f"Kunne ikke læse rastret '{lyr.name()}'.")
    arr = ds.GetRasterBand(1).ReadAsArray().astype(np.float64)
    gyldig = np.isfinite(arr) & (arr != NODATA)
    return arr, gyldig


def _braend(mem, geoms, srs):
    """Brænder geometrierne ind i rastret. Returnerer GDAL-fejlkode (0 = ok)."""
    vds = ogr.GetDriverByName("Memory").CreateDataSource("")
    vlyr = vds.CreateLayer("tmp", srs, ogr.wkbMultiPolygon)
    for g in geoms:
        og = ogr.CreateGeometryFromWkb(bytes(g.asWkb()))
        if og is None:
            continue
        feat = ogr.Feature(vlyr.GetLayerDefn())
        feat.SetGeometry(og)
        vlyr.CreateFeature(feat)
    return gdal.RasterizeLayer(mem, [1], vlyr, burn_values=[1])


def _rasteriser(geoms, ncol, nrow, gt, wkt, navn=""):
    mem = gdal.GetDriverByName("MEM").Create("", ncol, nrow, 1, gdal.GDT_Byte)
    mem.SetGeoTransform(gt)
    mem.SetProjection(wkt)
    mem.GetRasterBand(1).Fill(0)
    if geoms:
        srs = osr.SpatialReference()
        srs.ImportFromWkt(wkt)
        if _braend(mem, geoms, srs) != 0:
            # Én dårlig geometri må ikke vælte resten: prøv én ad gangen
            fejl = sum(1 for g in geoms if _braend(mem, [g], srs) != 0)
            if fejl == len(geoms):
                raise AnalyseFejl(f"Kunne ikke rasterisere {navn}: {gdal.GetLastErrorMsg()}")
    return mem.GetRasterBand(1).ReadAsArray().astype(bool)


def koer(nutid_lyr, fremtid_lyr, natur_lyr, kode_felt, projekt_lyr, typer, status=None):
    """Kører hele vurderingen og returnerer et resultat-dict."""
    def meld(t):
        if status:
            status(t)

    crs = fremtid_lyr.crs()
    if crs.isGeographic():
        raise AnalyseFejl("Afvandingsmodellen skal være i et projiceret koordinatsystem (meter), "
                          "fx EPSG:25832, for at arealer kan beregnes.")
    wkt = crs.toWkt(QgsCoordinateReferenceSystem.WKT_PREFERRED_GDAL)

    meld("Læser projektgrænse …")
    projekt = _projekt_geometri(projekt_lyr, crs)
    bbox = projekt.boundingBox()

    meld("Læser afvandingsmodeller …")
    fds = gdal.Open(_raster_sti(fremtid_lyr))
    if fds is None:
        raise AnalyseFejl(f"Kunne ikke åbne '{fremtid_lyr.name()}'.")
    bounds, rx, ry, ncol, nrow = _grid(fds, bbox)
    fds = None
    gt = (bounds[0], rx, 0.0, bounds[3], 0.0, -ry)
    fremtid, gf = _laes_raster(fremtid_lyr, bounds, rx, ry, wkt)
    nutid, gn = _laes_raster(nutid_lyr, bounds, rx, ry, wkt)

    meld("Læser naturbeskyttelse …")
    tr = QgsCoordinateTransform(natur_lyr.crs(), crs, QgsProject.instance())
    rect = tr.transformBoundingBox(bbox, QgsCoordinateTransform.ReverseTransform)
    req = QgsFeatureRequest().setFilterRect(rect)
    andre, pr_kode = [], {b.SOE: [], b.MOSE: [], b.ENG: []}
    for f in natur_lyr.getFeatures(req):
        g = QgsGeometry(f.geometry())
        if g.isEmpty():
            continue
        g.transform(tr)
        if not g.boundingBox().intersects(bbox):
            continue
        g = _gyldig_geom(g)
        if not g:
            continue
        k = _kode(f[kode_felt])
        if k in pr_kode:
            pr_kode[k].append(g)
        else:
            andre.append(g)  # andre koder: ny natur mulig

    meld("Rasteriserer polygoner …")
    p_maske = _rasteriser([projekt], ncol, nrow, gt, wkt, "projektgrænsen")
    natur_kode = {k: _rasteriser(v, ncol, nrow, gt, wkt, b.NAVNE[k])
                  for k, v in pr_kode.items()}
    natur_andre = _rasteriser(andre, ncol, nrow, gt, wkt, "andre naturtyper")

    meld("Beregner ændringer …")
    celle_ha = rx * ry / 10000.0
    aendringer, uden_vaerdi = b.beregn(fremtid, nutid, gf & gn, p_maske,
                                       natur_kode, typer, celle_ha, gyldig_fremtid=gf,
                                       natur_andre=natur_andre)

    meld("Beregner nuværende arealer …")
    nu, kontrol = [], []
    if not np.any(p_maske):
        raise AnalyseFejl("Projektgrænsen dækker ingen celler i afvandingsmodellen.")
    for kode in (b.SOE, b.MOSE, b.ENG):
        if kode not in typer:
            continue
        ha = 0.0
        if pr_kode[kode]:
            g = _gyldig_geom(QgsGeometry.unaryUnion(pr_kode[kode]))
            if g:
                ha = g.intersection(projekt).area() / 10000.0
        r_ha = float(np.count_nonzero(natur_kode[kode] & p_maske)) * celle_ha
        kontrol.append((b.NAVNE[kode], len(pr_kode[kode]), ha, r_ha))
        if ha > 0.05 and r_ha == 0:
            raise AnalyseFejl(f"{b.NAVNE[kode]} har {ha:.2f} ha i projektområdet, men "
                              "blev ikke fundet i rastergridet. Tjek naturlagets geometrier.")
        nu.append(("Nuværende naturbeskyttelse i projektområdet",
                   f"{b.NAVNE[kode]} ({kode})", "", "Areal i naturbeskyttelseslaget", ha))

    return {
        "raekker": nu + aendringer,
        "projekt_ha": projekt.area() / 10000.0,
        "uden_vaerdi_ha": uden_vaerdi,
        "celle": (rx, ry),
        "crs": crs.authid(),
        "kontrol": kontrol,
        "andre_koder": len(andre),
    }
