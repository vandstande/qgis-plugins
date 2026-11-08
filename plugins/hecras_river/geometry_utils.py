# -*- coding: utf-8 -*-
"""
Fælles hjælpefunktioner til HECRAS River pluginet.

Del 1 (Banklines / Cross section lines) og Del 2 (Profilpunkter / Midtpunkter)
deler den samme metode til at finde retning og normalvektor langs en
centerlinje, så Cross section lines altid passer sammen med Banklines,
og Profilpunkter altid passer sammen med Cross section lines.
"""

import math

from qgis.core import (
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsVectorFileWriter,
    QgsWkbTypes,
    QgsCoordinateTransformContext,
    QgsProject,
)
from qgis.PyQt.QtCore import QVariant
from .compat import VFW_NO_ERROR, WKB_LINESTRING, WKB_POINT


# ---------------------------------------------------------------------------
# Grundlæggende geometri-hjælpere
# ---------------------------------------------------------------------------

def get_tangent(geom, distance, length):
    """Returnerer en normaliseret tangentvektor (dx, dy) for 'geom' i punktet
    'distance' meter fra geometriens start."""
    if length <= 0:
        return (1.0, 0.0)

    eps = max(min(length * 0.001, 0.5), 1e-4)
    d0 = max(distance - eps, 0.0)
    d1 = min(distance + eps, length)
    if d1 <= d0:
        d1 = min(d0 + eps, length)
        if d1 <= d0:
            d0 = max(d1 - eps, 0.0)

    p0 = geom.interpolate(d0).asPoint()
    p1 = geom.interpolate(d1).asPoint()
    dx = p1.x() - p0.x()
    dy = p1.y() - p0.y()
    norm = math.hypot(dx, dy)
    if norm == 0:
        return (1.0, 0.0)
    return (dx / norm, dy / norm)


def left_normal(tangent):
    """90 grader mod uret (venstre side set i tegneretningen)."""
    dx, dy = tangent
    return (-dy, dx)


def right_normal(tangent):
    """90 grader med uret (højre side set i tegneretningen)."""
    dx, dy = tangent
    return (dy, -dx)


def sample_stations(length, step):
    """Liste af stationer (afstande fra start) med jævnt mellemrum 'step',
    altid inkl. 0 og linjens totale længde."""
    if length <= 0:
        return [0.0]
    if step <= 0:
        step = max(length, 1.0)
    n = int(length // step)
    stations = [i * step for i in range(n + 1)]
    if not stations or stations[-1] < length - 1e-6:
        stations.append(length)
    return stations


def offset_point(geom, distance, length, side_normal_func, offset_dist):
    """Punkt forskudt 'offset_dist' m til siden ('side_normal_func') fra
    centerlinjen 'geom' i stationen 'distance'."""
    base = geom.interpolate(distance).asPoint()
    tangent = get_tangent(geom, distance, length)
    nx, ny = side_normal_func(tangent)
    return QgsPointXY(base.x() + nx * offset_dist, base.y() + ny * offset_dist)


def get_attr(feature, name, default=""):
    """Henter attributværdi ud fra feltnavn, hvis det findes - ellers default."""
    idx = feature.fields().indexFromName(name)
    if idx == -1:
        return default
    val = feature.attribute(idx)
    if val is None:
        return default
    return val


def first_matching_field(fields, candidates):
    """Finder det første feltnavn i 'candidates', der findes i 'fields'."""
    names = [f.name() for f in fields]
    for c in candidates:
        if c in names:
            return c
    return None


# ---------------------------------------------------------------------------
# Skrivning af shapefiler
# ---------------------------------------------------------------------------

def write_line_layer(path, crs, fields, features, geom_type=WKB_LINESTRING):
    """Skriver en liste af QgsFeature (med QgsGeometry af linjetype) til en
    shapefil."""
    save_options = QgsVectorFileWriter.SaveVectorOptions()
    save_options.driverName = "ESRI Shapefile"
    save_options.fileEncoding = "UTF-8"

    writer = QgsVectorFileWriter.create(
        path, fields, geom_type, crs,
        QgsCoordinateTransformContext(), save_options
    )
    if writer.hasError() != VFW_NO_ERROR:
        raise RuntimeError(f"Kunne ikke oprette lag: {path} ({writer.errorMessage()})")

    for feat in features:
        writer.addFeature(feat)

    del writer


def write_point_layer(path, crs, fields, features):
    return write_line_layer(path, crs, fields, features, geom_type=WKB_POINT)
