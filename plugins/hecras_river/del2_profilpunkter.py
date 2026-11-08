# -*- coding: utf-8 -*-
"""
Del 2: Lav Cross section punkter og kote punkter.

Ud fra en Rivercenter line og Cross section lines (som har fået
Riverstation fra HEC-RAS) dannes:
  1) Profilpunkter: punkter langs hver cross section line med "Afstand
     mellem profilpunkter" meters mellemrum, med kote hentet fra
     terrænmodellen (Terræn).
  2) Midtpunkter (kotepunkter): for hver cross section line udvælges det
     profilpunkt, der ligger tættest på Rivercenter linjen.

Findes af midtpunktet:
  - Der findes først det punkt, hvor selve cross section-linjen (den
    fulde, kontinuerte geometri - ikke kun de udtrukne profilpunkter)
    krydser Rivercenter linjen. Krydser den flere gange, bruges det
    kryds der ligger tættest på linjens midte.
  - Krydser linjerne ikke, bruges i stedet det punkt på cross section-
    linjen, der geometrisk ligger tættest på Rivercenter linjen
    (QgsGeometry.nearestPoint), så vi ikke er afhængige af, hvor tæt
    de enkelte udtrukne profilpunkter tilfældigvis ligger.
  - Det profilpunkt, der ligger nærmest denne station langs linjen,
    bruges som midtpunkt/kotepunkt.

Rivercenter linjen transformeres til samme koordinatsystem som Cross
section lines, før afstande/skæringer beregnes, så et evt. forskelligt
CRS mellem lagene (fx efter eksport fra HEC-RAS) ikke giver forkerte
resultater.
"""

from qgis.core import (
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QVariant

from .geometry_utils import (
    sample_stations,
    write_point_layer,
)


def _sample_elevation(terrain_layer, point):
    provider = terrain_layer.dataProvider()
    value, ok = provider.sample(QgsPointXY(point.x(), point.y()), 1)
    if not ok or value is None:
        return None
    return float(value)


def _prepare_centerline_geom(centerline_layer, target_crs):
    """Samler alle centerline-features til én geometri, transformeret til
    'target_crs' (CRS'en for Cross section lines), så senere sammenligninger
    sker i samme koordinatsystem uanset lagenes egne CRS'er."""
    geoms = []
    for feat in centerline_layer.getFeatures():
        g = feat.geometry()
        if g is None or g.isEmpty():
            continue
        geoms.append(QgsGeometry(g))

    if not geoms:
        return None

    combined = QgsGeometry.unaryUnion(geoms) if len(geoms) > 1 else geoms[0]

    src_crs = centerline_layer.crs()
    if src_crs != target_crs:
        transform = QgsCoordinateTransform(src_crs, target_crs, QgsProject.instance())
        combined.transform(transform)

    return combined


def _extract_points(geom):
    """Udtrækker en liste af QgsPointXY fra en (evt. sammensat) geometri."""
    if geom is None or geom.isEmpty():
        return []

    flat_type = QgsWkbTypes.flatType(geom.wkbType())
    pts = []

    if flat_type == QgsWkbTypes.Point:
        pts.append(geom.asPoint())
    elif flat_type == QgsWkbTypes.MultiPoint:
        pts.extend(geom.asMultiPoint())
    elif geom.isMultipart():
        for part in geom.asGeometryCollection():
            pts.extend(_extract_points(part))
    else:
        # Fx en kort fælles linjestump - brug midtpunktet af den som skæring.
        c = geom.centroid()
        if c and not c.isEmpty():
            pts.append(c.asPoint())

    return pts


def _closest_station_on_line(xs_geom, centerline_geom, length):
    """Finder afstanden langs 'xs_geom' (fra dens startpunkt), hvor den er
    tættest på 'centerline_geom'. Returnerer None hvis ingen centerlinje."""
    if centerline_geom is None or centerline_geom.isEmpty():
        return None

    # 1) Faktisk skæring mellem tværsnitslinjen og rivercenter linjen.
    if xs_geom.intersects(centerline_geom):
        inter = xs_geom.intersection(centerline_geom)
        pts = _extract_points(inter)
        if pts:
            mid = length / 2.0
            best_pt = min(
                pts,
                key=lambda p: abs(xs_geom.lineLocatePoint(QgsGeometry.fromPointXY(p)) - mid),
            )
            return xs_geom.lineLocatePoint(QgsGeometry.fromPointXY(best_pt))

    # 2) Ingen skæring - brug det punkt på xs_geom der ligger tættest på
    #    centerlinjen (kontinuert, ikke kun de udtrukne profilpunkter).
    nearest = xs_geom.nearestPoint(centerline_geom)
    if nearest is None or nearest.isEmpty():
        return None
    return xs_geom.lineLocatePoint(nearest)


def build_profile_and_midpoints(centerline_layer, xs_layer, terrain_layer, spacing):
    """Returnerer (point_fields, profilpunkter_features, midtpunkter_features, crs)."""

    crs = xs_layer.crs()
    src_fields = xs_layer.fields()

    point_fields = QgsFields()
    for f in src_fields:
        point_fields.append(QgsField(f.name(), f.type()))
    point_fields.append(QgsField("XSId", QVariant.Int))
    point_fields.append(QgsField("PtDist", QVariant.Double))
    point_fields.append(QgsField("Elev", QVariant.Double))

    centerline_geom = _prepare_centerline_geom(centerline_layer, crs)

    profilpunkter_features = []
    midtpunkter_features = []

    xs_id = 0
    for xs_feat in xs_layer.getFeatures():
        geom = xs_feat.geometry()
        if geom is None or geom.isEmpty():
            xs_id += 1
            continue

        length = geom.length()
        stations = sample_stations(length, spacing)
        src_attrs = list(xs_feat.attributes())

        station_features = []  # (station, feature)
        for s in stations:
            pt = geom.interpolate(s).asPoint()
            elev = _sample_elevation(terrain_layer, pt)

            f = QgsFeature(point_fields)
            f.setGeometry(QgsGeometry.fromPointXY(pt))
            attrs = list(src_attrs) + [xs_id, float(s), elev if elev is not None else None]
            f.setAttributes(attrs)
            profilpunkter_features.append(f)
            station_features.append((s, f))

        if station_features:
            target_station = _closest_station_on_line(geom, centerline_geom, length)
            if target_station is None:
                # Ingen rivercenter-geometri at sammenligne med - brug midten
                # af tværsnittet som sikkert fallback frem for en tilfældig ende.
                target_station = length / 2.0

            _, best_feat = min(
                station_features, key=lambda item: abs(item[0] - target_station)
            )

            mid_f = QgsFeature(point_fields)
            mid_f.setGeometry(QgsGeometry(best_feat.geometry()))
            mid_f.setAttributes(best_feat.attributes())
            midtpunkter_features.append(mid_f)

        xs_id += 1

    return point_fields, profilpunkter_features, midtpunkter_features, crs


def run(centerline_layer, xs_layer, terrain_layer, spacing, profilpunkter_path, midtpunkter_path):
    (point_fields, profilpunkter_features,
     midtpunkter_features, crs) = build_profile_and_midpoints(
        centerline_layer, xs_layer, terrain_layer, spacing
    )

    write_point_layer(profilpunkter_path, crs, point_fields, profilpunkter_features)
    write_point_layer(midtpunkter_path, crs, point_fields, midtpunkter_features)

    return len(profilpunkter_features), len(midtpunkter_features)
