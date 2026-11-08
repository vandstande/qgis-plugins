"""Beregning: udjævn (luk) terrænet mellem to banklines.

Terrænmodellen læses gennem QGIS' rasterlag (dataProvider().sample() på Band 1),
på samme måde som i "Lav HEC-RAS Profilpkt". GDAL bruges kun til at skrive GeoTIFF.
"""
import math
import os

import numpy as np
from osgeo import gdal, ogr

from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry, QgsPointXY,
    QgsProject, QgsProviderRegistry, QgsRaster, QgsRasterLayer, QgsWkbTypes,
)

BAND = 1


class TerrainError(Exception):
    pass


# ---------------------------------------------------------------- terrænmodel
def layer_file_path(layer):
    """Filsti bag et rasterlag (tom streng hvis laget ikke er en fil)."""
    if layer is None:
        return ""
    try:
        parts = QgsProviderRegistry.instance().decodeUri(
            layer.providerType(), layer.source())
        path = parts.get("path") or ""
    except Exception:
        path = ""
    if not path:
        path = layer.source().split("|")[0]
    return path if path and os.path.isfile(path) else ""


def open_dem_layer(dem_layer=None, dem_path=""):
    """Returnér et gyldigt QgsRasterLayer for terrænmodellen."""
    if dem_path:
        if not os.path.isfile(dem_path):
            raise TerrainError(f"Terrænfilen findes ikke:\n{dem_path}")
        lyr = QgsRasterLayer(dem_path, os.path.basename(dem_path), "gdal")
        if not lyr.isValid():
            raise TerrainError(f"QGIS kan ikke åbne terrænfilen:\n{dem_path}")
        return lyr
    if dem_layer is None or not dem_layer.isValid():
        raise TerrainError("Vælg en gyldig terrænmodel.")
    return dem_layer


class _Sampler:
    """Aflæser Band 1 i et rasterlag i punkter (i lagets koordinatsystem)."""

    def __init__(self, layer):
        self.prov = layer.dataProvider()
        if self.prov is None:
            raise TerrainError("Terrænmodellen har ingen dataprovider.")
        self.has_sample = hasattr(self.prov, "sample")

    def value(self, x, y):
        pt = QgsPointXY(float(x), float(y))
        if self.has_sample:
            res = self.prov.sample(pt, BAND)
            if isinstance(res, tuple):
                v, ok = res
            else:
                v, ok = res, True
            if not ok or v is None:
                return math.nan
            v = float(v)
        else:
            r = self.prov.identify(pt, QgsRaster.IdentifyFormatValue)
            if not r.isValid():
                return math.nan
            v = r.results().get(BAND)
            if v is None:
                return math.nan
            v = float(v)
        return v if math.isfinite(v) else math.nan

    def values(self, xy, cb=None):
        out = np.empty(len(xy), dtype=np.float64)
        n = len(xy)
        for k, (x, y) in enumerate(xy):
            out[k] = self.value(x, y)
            if cb and k % 2000 == 0:
                cb(k / max(n, 1))
        return out


# ---------------------------------------------------------------- hjælpere
def _line_points(geom):
    if geom.isMultipart():
        merged = geom.mergeLines()
        if merged is not None and not merged.isEmpty():
            geom = merged
    if geom.isMultipart():
        pts = [p for part in geom.asMultiPolyline() for p in part]
    else:
        pts = geom.asPolyline()
    return [QgsPointXY(p) for p in pts]


def _get_banks(layer, only_selected, xform):
    if only_selected:
        feats = list(layer.selectedFeatures())
    else:
        feats = list(layer.getFeatures())
        if len(feats) != 2 and layer.selectedFeatureCount() == 2:
            feats = list(layer.selectedFeatures())
    feats = [f for f in feats if f.hasGeometry() and not f.geometry().isEmpty()]
    if len(feats) != 2:
        raise TerrainError(
            f"Banklines-laget skal indeholde præcis 2 linjer (fundet {len(feats)}).\n"
            "Vælg de 2 banklines og sæt flueben i 'Brug kun valgte features'.")
    lines = []
    for f in feats:
        g = QgsGeometry(f.geometry())
        if xform is not None:
            g.transform(xform)
        pts = _line_points(g)
        if len(pts) < 2:
            raise TerrainError("En af banklines har færre end 2 punkter.")
        lines.append(pts)
    return lines


def _densify(pts, step):
    g = QgsGeometry.fromPolylineXY(pts).densifyByDistance(step)
    return np.array([[p.x(), p.y()] for p in g.asPolyline()], dtype=np.float64)


def _nearest(pts, q, cb=None):
    """Afstand og indeks til nærmeste punkt i pts for hvert punkt i q."""
    try:
        from scipy.spatial import cKDTree
        d, i = cKDTree(pts).query(q)
        return d, i
    except ImportError:
        pass
    n = len(pts)
    chunk = max(1, int(4_000_000 // max(n, 1)))
    d = np.empty(len(q))
    i = np.empty(len(q), dtype=np.int64)
    for s in range(0, len(q), chunk):
        qq = q[s:s + chunk]
        dd = (qq[:, None, 0] - pts[None, :, 0]) ** 2 + (qq[:, None, 1] - pts[None, :, 1]) ** 2
        k = dd.argmin(axis=1)
        i[s:s + chunk] = k
        d[s:s + chunk] = np.sqrt(dd[np.arange(len(qq)), k])
        if cb:
            cb(s / len(q))
    return d, i


def _ogr_mem_datasource():
    for name in ("Memory", "MEM"):
        drv = ogr.GetDriverByName(name)
        if drv is not None:
            try:
                ds = drv.CreateDataSource("")
                if ds is not None:
                    return ds
            except Exception:
                pass
    raise TerrainError("Kunne ikke oprette et midlertidigt OGR-lag.")


def _crs_wkt(crs):
    if crs is None or not crs.isValid():
        return ""
    try:
        return crs.toWkt(QgsCoordinateReferenceSystem.WKT_PREFERRED_GDAL)
    except Exception:
        return crs.toWkt()


# ---------------------------------------------------------------- hovedfunktion
def luk_terraen(bank_layer, out_path, dem_layer=None, dem_path="",
                only_selected=False, only_fill=False, progress=None):
    def prog(p, txt=""):
        if progress:
            progress(p, txt)

    prog(2, "Åbner terrænmodel …")
    dem = open_dem_layer(dem_layer, dem_path)
    sampler = _Sampler(dem)

    # Gitter (cellestørrelse og placering) fra terrænmodellen
    ext = dem.extent()
    px = float(dem.rasterUnitsPerPixelX())
    py = -abs(float(dem.rasterUnitsPerPixelY()))
    if not px or not py or dem.width() <= 0 or dem.height() <= 0:
        raise TerrainError("Terrænmodellen har ingen fast cellestørrelse.\n"
                           "Gem den som GeoTIFF og vælg filen i stedet.")
    gt0x, gt0y = ext.xMinimum(), ext.yMaximum()
    xsize, ysize = dem.width(), dem.height()

    # Banklines i terrænmodellens koordinatsystem (som i Lav HEC-RAS Profilpkt)
    xform = None
    if bank_layer.crs() != dem.crs():
        xform = QgsCoordinateTransform(bank_layer.crs(), dem.crs(), QgsProject.instance())
    L, R = _get_banks(bank_layer, only_selected, xform)

    def dist(a, b):
        return math.hypot(a.x() - b.x(), a.y() - b.y())

    if dist(L[0], R[0]) + dist(L[-1], R[-1]) <= dist(L[0], R[-1]) + dist(L[-1], R[0]):
        ring = L + R[::-1]
    else:
        ring = L + R
    ring.append(ring[0])
    poly = QgsGeometry.fromPolygonXY([ring])
    if not poly.isGeosValid():
        poly = poly.makeValid()
    if poly.type() != QgsWkbTypes.PolygonGeometry:
        conv = poly.convertToType(QgsWkbTypes.PolygonGeometry, True)
        if conv is not None:
            poly = conv
    if poly.isEmpty() or poly.area() <= 0:
        raise TerrainError("Kunne ikke danne et areal mellem de 2 banklines.")

    # Vindue i terrænmodellens gitter
    bb = poly.boundingBox()
    c0 = max(0, int(math.floor((bb.xMinimum() - gt0x) / px)) - 1)
    c1 = min(xsize, int(math.ceil((bb.xMaximum() - gt0x) / px)) + 1)
    r0 = max(0, int(math.floor((bb.yMaximum() - gt0y) / py)) - 1)
    r1 = min(ysize, int(math.ceil((bb.yMinimum() - gt0y) / py)) + 1)
    if c1 <= c0 or r1 <= r0:
        raise TerrainError(
            "Banklines ligger uden for terrænmodellen.\n\n"
            f"Terrænmodel KS: {dem.crs().authid()}, udstrækning: {ext.toString(1)}\n"
            f"Banklines KS: {bank_layer.crs().authid()}, "
            f"udstrækning (omregnet): {bb.toString(1)}")
    w, h = c1 - c0, r1 - r0
    gx0 = gt0x + c0 * px
    gy0 = gt0y + r0 * py

    def rasterize(wkt, geom_type, all_touched):
        mem = gdal.GetDriverByName("MEM").Create("", w, h, 1, gdal.GDT_Byte)
        mem.SetGeoTransform((gx0, px, 0, gy0, 0, py))
        ods = _ogr_mem_datasource()
        lyr = ods.CreateLayer("g", geom_type=geom_type)
        feat = ogr.Feature(lyr.GetLayerDefn())
        feat.SetGeometry(ogr.CreateGeometryFromWkt(wkt))
        lyr.CreateFeature(feat)
        opts = ["ALL_TOUCHED=TRUE"] if all_touched else []
        gdal.RasterizeLayer(mem, [1], lyr, burn_values=[1], options=opts)
        raw = mem.GetRasterBand(1).ReadRaster(0, 0, w, h, w, h, gdal.GDT_Byte)
        return np.frombuffer(bytes(raw), dtype=np.uint8).reshape(h, w).astype(bool)

    # Celler mellem banklines (inkl. alle celler som banklines skærer)
    prog(8, "Finder celler mellem banklines …")
    mask = rasterize(poly.asWkt(), ogr.wkbMultiPolygon, True)
    edge = []
    for pts in (L, R):
        lw = QgsGeometry.fromPolylineXY(pts).asWkt()
        edge.append(rasterize(lw, ogr.wkbLineString, True))
    mask |= edge[0] | edge[1]
    rows, cols = np.nonzero(mask)
    if rows.size == 0:
        raise TerrainError("Der er ingen celler mellem de 2 banklines.")

    # Bredceller: cellerne som hver bankline går igennem – deres eksisterende
    # terrænkote aflæses i Band 1 (cellecentrum) og bevares uændret.
    banks = []
    for nr in (0, 1):
        er, ec = np.nonzero(edge[nr])
        xy = np.column_stack((gx0 + (ec + 0.5) * px, gy0 + (er + 0.5) * py))
        base = 12 + nr * 14
        prog(base, f"Aflæser terræn i bredcellerne langs bankline {nr + 1} …")
        z = sampler.values(xy, lambda f, b=base: prog(b + 14 * f))
        ok = np.isfinite(z)
        if not ok.any():
            raise TerrainError(
                f"Der blev ikke fundet terrænkoter langs bankline {nr + 1} "
                f"({len(xy)} celler aflæst i Band 1).\n\n"
                f"Terrænmodel KS: {dem.crs().authid()}, udstrækning: {ext.toString(1)}\n"
                f"Banklines KS: {bank_layer.crs().authid()}\n"
                f"Første punkt (omregnet): {L[0].x() if nr == 0 else R[0].x():.1f}, "
                f"{L[0].y() if nr == 0 else R[0].y():.1f}")
        banks.append((xy[ok], z[ok], er[ok], ec[ok]))

    # Interpolation mellem nærmeste bredcelle på hver side, vægtet efter afstand
    q = np.column_stack((gx0 + (cols + 0.5) * px, gy0 + (rows + 0.5) * py))
    prog(42, "Beregner afstand til bankline 1 …")
    dL, iL = _nearest(banks[0][0], q, lambda f: prog(42 + 14 * f))
    prog(56, "Beregner afstand til bankline 2 …")
    dR, iR = _nearest(banks[1][0], q, lambda f: prog(56 + 14 * f))
    zL = banks[0][1][iL]
    zR = banks[1][1][iR]
    s = dL + dR
    z = np.where(s > 0, (zL * dR + zR * dL) / np.where(s > 0, s, 1.0), zL)

    # Bredcellerne får præcis den eksisterende terrænkote
    keep = np.full((h, w), np.nan)
    for _xy, zb, er, ec in banks:
        keep[er, ec] = zb
    kv = keep[rows, cols]
    z = np.where(np.isfinite(kv), kv, z)

    if only_fill:
        prog(70, "Aflæser eksisterende terræn (kun opfyldning) …")
        orig = sampler.values(q, lambda f: prog(70 + 20 * f))
        z = np.where(np.isfinite(orig), np.maximum(z, orig), z)

    # Gem som GeoTIFF (Float32) i terrænmodellens gitter og koordinatsystem
    prog(92, "Gemmer GeoTIFF …")
    out_nodata = -9999.0
    rr0, rr1 = int(rows.min()), int(rows.max()) + 1
    cc0, cc1 = int(cols.min()), int(cols.max()) + 1
    ow, oh = cc1 - cc0, rr1 - rr0
    out = np.full((oh, ow), out_nodata, dtype=np.float32)
    out[rows - rr0, cols - cc0] = z.astype(np.float32)

    drv = gdal.GetDriverByName("GTiff")
    if os.path.exists(out_path):
        try:
            drv.Delete(out_path)
        except Exception:
            os.remove(out_path)
    dst = drv.Create(out_path, ow, oh, 1, gdal.GDT_Float32,
                     options=["COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER"])
    if dst is None:
        raise TerrainError(f"Kunne ikke oprette filen:\n{out_path}")
    dst.SetGeoTransform((gx0 + cc0 * px, px, 0, gy0 + rr0 * py, 0, py))
    wkt = _crs_wkt(dem.crs())
    if wkt:
        dst.SetProjection(wkt)
    ob = dst.GetRasterBand(1)
    ob.SetNoDataValue(out_nodata)
    ob.WriteRaster(0, 0, ow, oh, np.ascontiguousarray(out).tobytes(),
                   ow, oh, gdal.GDT_Float32)
    ob.FlushCache()
    dst.FlushCache()
    dst = None

    zb = np.concatenate([banks[0][1], banks[1][1]])
    prog(100, "Færdig.")
    return {"cells": int(rows.size), "px": abs(px), "py": abs(py),
            "zmin": float(np.nanmin(z)), "zmax": float(np.nanmax(z)),
            "bank_min": float(zb.min()), "bank_max": float(zb.max()),
            "bank_n": int(len(zb))}
