# -*- coding: utf-8 -*-
"""QGIS-delen: læs data, kald beregningskernen og lav lagene."""
import math
import os

import numpy as np
from qgis.core import (
    Qgis, QgsProject, QgsCoordinateTransform, QgsRectangle, QgsGeometry,
    QgsPointXY, QgsFeature, QgsVectorLayer, QgsVectorFileWriter,
    QgsFillSymbol, QgsMarkerSymbol, QgsLineSymbol,
    QgsCategorizedSymbolRenderer, QgsRendererCategory)

from . import core

GROUP_NAME = "Screening af projektgrænse"
MAX_CELLS = 40_000_000


# ----------------------------------------------------------------------
# hjælpefunktioner
# ----------------------------------------------------------------------
def _enum(owner, scoped, name):
    """Hent enum-værdi både på QGIS 3 (uscopet) og QGIS 4 (scopet)."""
    sc = getattr(owner, scoped, None)
    if sc is not None and hasattr(sc, name):
        return getattr(sc, name)
    return getattr(owner, name)


def _block_to_numpy(block, w, h):
    try:
        arr = block.as_numpy(use_masking=True)
        return np.ma.filled(np.ma.asarray(arr, dtype=float), np.nan)
    except Exception:
        pass
    dt = block.dataType()
    D = Qgis.DataType if hasattr(Qgis, "DataType") else Qgis
    m = {}
    for n, t in (("Byte", np.uint8), ("UInt16", np.uint16),
                 ("Int16", np.int16), ("UInt32", np.uint32),
                 ("Int32", np.int32), ("Float32", np.float32),
                 ("Float64", np.float64), ("Int8", np.int8)):
        if hasattr(D, n):
            m[getattr(D, n)] = t
    if dt not in m:
        raise ValueError("Terrænmodellens datatype understøttes ikke.")
    arr = np.frombuffer(bytes(block.data()), dtype=m[dt]).reshape(h, w)
    arr = arr.astype(float)
    if block.hasNoDataValue():
        arr[arr == block.noDataValue()] = np.nan
    return arr


def _read_dem(dem, extent, res, log):
    prov = dem.dataProvider()
    ext = extent.intersect(dem.extent())
    if ext.isEmpty():
        raise ValueError("Linjen ligger uden for terrænmodellen.")
    w = int(math.ceil(ext.width() / res))
    h = int(math.ceil(ext.height() / res))
    if w * h > MAX_CELLS:
        new_res = res * math.sqrt(w * h / MAX_CELLS)
        log("Området er stort - beregningsopløsningen er øget fra %.2f til "
            "%.2f m." % (res, new_res))
        res = new_res
        w = int(math.ceil(ext.width() / res))
        h = int(math.ceil(ext.height() / res))
    xmin, ymax = ext.xMinimum(), ext.yMaximum()
    rect = QgsRectangle(xmin, ymax - h * res, xmin + w * res, ymax)
    log("Læser terrænmodel (%d x %d celler à %.2f m)..." % (w, h, res))
    block = prov.block(1, rect, w, h)
    if block is None or not block.isValid():
        raise ValueError("Kunne ikke læse terrænmodellen.")
    arr = _block_to_numpy(block, w, h)
    return core.DemGrid(arr, xmin, ymax, res)


def _line_coords(geom, log, fid):
    if geom is None or geom.isEmpty():
        return None
    if geom.isMultipart():
        merged = geom.mergeLines()
        if not merged.isMultipart():
            return merged.asPolyline()
        parts = merged.asMultiPolyline()
        log("Linje %s består af %d adskilte dele - den længste bruges."
            % (fid, len(parts)))
        best = max(parts, key=lambda p: QgsGeometry.fromPolylineXY(p).length())
        return best
    return geom.asPolyline()


def _memory_layer(geomtype, crs, fields, name):
    uri = "%s?crs=%s" % (geomtype, crs.authid() or "")
    if not crs.authid():
        uri = geomtype
    for n, t in fields:
        uri += "&field=%s:%s" % (n, t)
    lyr = QgsVectorLayer(uri, name, "memory")
    if not crs.authid():
        lyr.setCrs(crs)
    return lyr


def _save_or_keep(lyr, gpkg, name, log):
    if not gpkg:
        return lyr
    if not gpkg.lower().endswith(".gpkg"):
        gpkg += ".gpkg"
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "GPKG"
    opts.layerName = name
    act = "CreateOrOverwriteLayer" if os.path.exists(gpkg) \
        else "CreateOrOverwriteFile"
    opts.actionOnExistingFile = _enum(QgsVectorFileWriter,
                                      "ActionOnExistingFile", act)
    res = QgsVectorFileWriter.writeAsVectorFormatV3(
        lyr, gpkg, QgsProject.instance().transformContext(), opts)
    if res[0] != _enum(QgsVectorFileWriter, "WriterError", "NoError"):
        log("Kunne ikke gemme %s: %s" % (name, res[1]))
        return lyr
    out = QgsVectorLayer("%s|layername=%s" % (gpkg, name), name, "ogr")
    return out if out.isValid() else lyr


def _style_polygon(lyr):
    sym = QgsFillSymbol.createSimple({
        "color": "80,160,230,40", "outline_color": "220,30,30",
        "outline_width": "0.8"})
    lyr.renderer().setSymbol(sym)


def _style_points(lyr):
    cats = []
    for val, col, lab in (("nej", "40,170,60", "Terræn"),
                          ("ja", "220,30,30", "Grøft/vandløb")):
        s = QgsMarkerSymbol.createSimple({"name": "circle", "color": col,
                                          "size": "1.6",
                                          "outline_style": "no"})
        cats.append(QgsRendererCategory(val, s, lab))
    lyr.setRenderer(QgsCategorizedSymbolRenderer("groeft", cats))


def _style_lines(lyr):
    cats = []
    for val, col, style in ((core.STATUS_TERRAEN, "120,120,120", "solid"),
                            (core.STATUS_MAX, "255,140,0", "dash"),
                            (core.STATUS_NODATA, "160,0,160", "dash"),
                            (core.STATUS_OPSTROEMS, "220,30,30", "dash")):
        s = QgsLineSymbol.createSimple({"color": col, "width": "0.2",
                                        "line_style": style})
        cats.append(QgsRendererCategory(val, s, val))
    lyr.setRenderer(QgsCategorizedSymbolRenderer("status", cats))


# ----------------------------------------------------------------------
# hovedfunktion
# ----------------------------------------------------------------------
def run_projektgraense(line_lyr, dem, p, selected_only=False, gpkg="",
                       add_control=True, log=print, progress=lambda v: None):
    proj = QgsProject.instance()
    dem_crs = dem.crs()
    if dem_crs.isGeographic():
        raise ValueError("Terrænmodellen skal være i et projiceret "
                         "koordinatsystem (fx EPSG:25832).")
    to_dem = QgsCoordinateTransform(line_lyr.crs(), dem_crs,
                                    proj.transformContext())
    to_line = QgsCoordinateTransform(dem_crs, line_lyr.crs(),
                                     proj.transformContext())

    feats = list(line_lyr.selectedFeatures() if selected_only
                 else line_lyr.getFeatures())
    if not feats:
        raise ValueError("Ingen linjer at beregne (er der valgt nogen?).")

    lines = []
    for f in feats:
        g = QgsGeometry(f.geometry())
        if g.isNull() or g.isEmpty():
            continue
        g.transform(to_dem)
        pts = _line_coords(g, log, f.id())
        if not pts or len(pts) < 2:
            continue
        lines.append((f.id(), [(q.x(), q.y()) for q in pts]))
    if not lines:
        raise ValueError("Ingen gyldige linjer fundet.")
    # udstrækning der skal læses af terrænmodellen
    xs = [c[0] for _, cc in lines for c in cc]
    ys = [c[1] for _, cc in lines for c in cc]
    buf = p["maxdist"] + p["ditch_dist"] + 3 * p["res"]
    ext = QgsRectangle(min(xs) - buf, min(ys) - buf,
                       max(xs) + buf, max(ys) + buf)

    res = max(p["res"], dem.rasterUnitsPerPixelX() * 0.999)
    if res > p["res"] + 1e-9:
        log("Terrænmodellens opløsning er %.2f m - den bruges." % res)
    grid = _read_dem(dem, ext, res, log)
    progress(10)

    crs = line_lyr.crs()
    poly_lyr = _memory_layer("Polygon", crs, [
        ("id", "integer"), ("linje_fid", "integer"), ("dybde", "double"),
        ("kote_start", "double"), ("kote_slut", "double"),
        ("laengde", "double"), ("areal_m2", "double"),
        ("areal_ha", "double")], "Projektgrænse")
    pt_lyr = _memory_layer("Point", crs, [
        ("linje_fid", "integer"), ("station", "double"),
        ("z_linje", "double"), ("z_venstre", "double"),
        ("z_hoejre", "double"), ("groeft", "string(3)"),
        ("z_bund", "double"), ("graensekote", "double"),
        ("bredde_v", "double"), ("bredde_h", "double"),
        ("status_v", "string(20)"), ("status_h", "string(20)")],
        "Kontrolpunkter")
    tv_lyr = _memory_layer("LineString", crs, [
        ("linje_fid", "integer"), ("station", "double"),
        ("side", "string(10)"), ("bredde", "double"),
        ("status", "string(20)"), ("graensekote", "double")],
        "Tværsnit")

    polys, pts, tvs = [], [], []
    for n, (fid, coords) in enumerate(lines):
        log("Linje %s: beregner..." % fid)
        r = core.compute(
            coords, grid, depth=p["depth"], spacing=p["spacing"],
            maxdist=p["maxdist"], smooth_window=p["smooth_window"],
            monotone=p["monotone"], ditch_dist=p["ditch_dist"],
            ditch_tol=p["ditch_tol"], ditch_correct=p["ditch_correct"],
            upstream_max=p["upstream_max"])
        for w in r["warnings"]:
            log("  ! " + w)

        # polygon = forening af firkanter mellem nabotværsnit + vifte
        geoms = []
        for ring in core.polygon_parts(r):
            g = QgsGeometry.fromPolygonXY([[QgsPointXY(x, y)
                                            for x, y in ring]])
            if not g.isGeosValid():
                g = g.makeValid()
            if not g.isEmpty() and g.area() > 0:
                geoms.append(g)
        poly = QgsGeometry.unaryUnion(geoms)
        poly = poly.buffer(0, 4)
        poly = poly.removeInteriorRings()
        area = poly.area()
        poly.transform(to_line)

        f = QgsFeature(poly_lyr.fields())
        f.setGeometry(poly)
        f.setAttributes([n + 1, fid, p["depth"], round(r["kote_start"], 2),
                         round(r["kote_slut"], 2), round(r["length"], 1),
                         round(area, 0), round(area / 1e4, 2)])
        polys.append(f)
        log("  Areal: %.2f ha, grænsekote %.2f (start) - %.2f (slut)"
            % (area / 1e4, r["kote_start"], r["kote_slut"]))

        if add_control:
            for i, s in enumerate(r["stations"]):
                g = QgsGeometry.fromPointXY(QgsPointXY(s["x"], s["y"]))
                g.transform(to_line)
                f = QgsFeature(pt_lyr.fields())
                f.setGeometry(g)
                f.setAttributes([
                    fid, round(s["station"], 1), _r(s["z_linje"]),
                    _r(s["z_venstre"]), _r(s["z_hoejre"]),
                    "ja" if s["groeft"] else "nej", _r(s["z_bund"]),
                    _r(s["graensekote"]), round(s["bredde_v"], 1),
                    round(s["bredde_h"], 1), s["status_v"], s["status_h"]])
                pts.append(f)
                for side, hit in (("venstre", r["left"][i]),
                                  ("højre", r["right"][i])):
                    tvs.append(_tv(tv_lyr, to_line, fid, s, side, hit))
            s0 = r["stations"][0]
            for hit in r["fan"]:
                tvs.append(_tv(tv_lyr, to_line, fid, s0, "opstrøms", hit))
        progress(10 + 85 * (n + 1) / len(lines))

    poly_lyr.dataProvider().addFeatures(polys)
    poly_lyr.updateExtents()
    layers = [_save_or_keep(poly_lyr, gpkg, "Projektgrænse", log)]
    _style_polygon(layers[0])
    if add_control:
        pt_lyr.dataProvider().addFeatures(pts)
        tv_lyr.dataProvider().addFeatures(tvs)
        pl = _save_or_keep(pt_lyr, gpkg, "Kontrolpunkter", log)
        tl = _save_or_keep(tv_lyr, gpkg, "Tværsnit", log)
        _style_points(pl)
        _style_lines(tl)
        layers += [pl, tl]

    root = proj.layerTreeRoot()
    grp = root.insertGroup(0, GROUP_NAME)
    for lyr in layers:
        proj.addMapLayer(lyr, False)
        grp.addLayer(lyr)
    progress(100)
    log("Færdig.")
    return layers


def _r(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) \
        else round(v, 2)


def _tv(lyr, ct, fid, s, side, hit):
    g = QgsGeometry.fromPolylineXY([QgsPointXY(s["x"], s["y"]),
                                    QgsPointXY(hit[0], hit[1])])
    g.transform(ct)
    f = QgsFeature(lyr.fields())
    f.setGeometry(g)
    f.setAttributes([fid, round(s["station"], 1), side, round(hit[2], 1),
                     hit[3], _r(s["graensekote"])])
    return f
