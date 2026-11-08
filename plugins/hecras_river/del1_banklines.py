# -*- coding: utf-8 -*-
"""
Del 1: Lav Banklines og Cross section lines.

Ud fra en Rivercenter line dannes:
  1) Banklines: 2 linjer forskudt "Bredde på trace" meter til hver side
     af centerlinjen (venstre og højre bred).
  2) Cross section lines: linjer vinkelret på centerlinjen med "Afstand
     mellem profiler" meters mellemrum, hver linje går fra 1 m uden for
     venstre bankline til 1 m uden for højre bankline.

Begge output-lag arver "River" og "Reach" attributter fra centerlinjen,
hvor de findes.
"""

from qgis.core import (
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QVariant

from .geometry_utils import (
    get_tangent,
    left_normal,
    right_normal,
    sample_stations,
    offset_point,
    get_attr,
    first_matching_field,
    write_line_layer,
)

RIVER_FIELD_CANDIDATES = ["River", "RIVER", "river"]
REACH_FIELD_CANDIDATES = ["Reach", "REACH", "reach"]

# Punktafstand brugt til at bygge selve bankline-kurven (uafhængig af
# brugerens "Afstand mellem profiler", så banklines bliver jævne selv med
# store profil-afstande).
BANKLINE_SAMPLE_MAX_STEP = 2.0
BANKLINE_MIN_POINTS = 20


def _bankline_step(length):
    if length <= 0:
        return 1.0
    step = length / max(BANKLINE_MIN_POINTS, 1)
    return min(step, BANKLINE_SAMPLE_MAX_STEP)


def build_banklines_and_crosssections(centerline_layer, bredde, afstand):
    """Kører beregningen og returnerer
    (bankline_fields, bankline_features, xs_fields, xs_features, crs).
    Selve gemning til shapefil foretages af den kaldende kode."""

    crs = centerline_layer.crs()
    src_fields = centerline_layer.fields()
    river_field = first_matching_field(src_fields, RIVER_FIELD_CANDIDATES)
    reach_field = first_matching_field(src_fields, REACH_FIELD_CANDIDATES)

    bankline_fields = QgsFields()
    bankline_fields.append(QgsField("River", QVariant.String))
    bankline_fields.append(QgsField("Reach", QVariant.String))
    bankline_fields.append(QgsField("Side", QVariant.String))

    xs_fields = QgsFields()
    xs_fields.append(QgsField("River", QVariant.String))
    xs_fields.append(QgsField("Reach", QVariant.String))
    xs_fields.append(QgsField("Station", QVariant.Double))

    bankline_features = []
    xs_features = []

    xs_id = 0

    for src_feat in centerline_layer.getFeatures():
        geom = src_feat.geometry()
        if geom is None or geom.isEmpty():
            continue

        length = geom.length()
        if length <= 0:
            continue

        river_val = get_attr(src_feat, river_field) if river_field else ""
        reach_val = get_attr(src_feat, reach_field) if reach_field else ""

        # --- Banklines -----------------------------------------------
        step = _bankline_step(length)
        stations = sample_stations(length, step)

        left_pts = [offset_point(geom, s, length, left_normal, bredde) for s in stations]
        right_pts = [offset_point(geom, s, length, right_normal, bredde) for s in stations]

        left_geom = QgsGeometry.fromPolylineXY(left_pts)
        right_geom = QgsGeometry.fromPolylineXY(right_pts)

        for side_name, side_geom in (("Venstre", left_geom), ("Højre", right_geom)):
            f = QgsFeature(bankline_fields)
            f.setGeometry(side_geom)
            f.setAttributes([river_val, reach_val, side_name])
            bankline_features.append(f)

        # --- Cross section lines ---------------------------------------
        xs_stations = sample_stations(length, afstand)
        for s in xs_stations:
            p_left = offset_point(geom, s, length, left_normal, bredde + 1.0)
            p_right = offset_point(geom, s, length, right_normal, bredde + 1.0)
            xs_geom = QgsGeometry.fromPolylineXY([p_left, p_right])

            f = QgsFeature(xs_fields)
            f.setGeometry(xs_geom)
            f.setAttributes([river_val, reach_val, float(s)])
            xs_features.append(f)
            xs_id += 1

    return bankline_fields, bankline_features, xs_fields, xs_features, crs


def run(centerline_layer, bredde, afstand, banklines_path, xs_path):
    (bankline_fields, bankline_features,
     xs_fields, xs_features, crs) = build_banklines_and_crosssections(
        centerline_layer, bredde, afstand
    )

    write_line_layer(banklines_path, crs, bankline_fields, bankline_features)
    write_line_layer(xs_path, crs, xs_fields, xs_features)

    return len(bankline_features), len(xs_features)
