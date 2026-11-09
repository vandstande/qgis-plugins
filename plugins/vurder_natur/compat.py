# -*- coding: utf-8 -*-
"""Kompatibilitet mellem QGIS 3 (Qt5/PyQt5) og QGIS 4 (Qt6/PyQt6).

Navnene slås op, når pluginet indlæses: først de nye, scopede navne fra
QGIS 4, og derefter de gamle navne fra QGIS 3 som reserve. Så virker
pluginet i begge versioner.
"""

from qgis.PyQt.QtCore import QVariant
from qgis.core import (Qgis, QgsCoordinateReferenceSystem, QgsMapLayerProxyModel,
                       QgsRaster, QgsRasterFileWriter, QgsUnitTypes,
                       QgsVectorFileWriter, QgsWkbTypes)

# QAction ligger i QtGui i Qt6 og i QtWidgets i Qt5
try:
    from qgis.PyQt.QtGui import QAction  # noqa: F401
except ImportError:
    from qgis.PyQt.QtWidgets import QAction  # noqa: F401

try:
    from qgis.PyQt.QtCore import QMetaType
except ImportError:  # meget gamle Qt-versioner
    QMetaType = None


def _find(*kandidater):
    """Returnér den første af (objekt, 'A.B') der findes."""
    for obj, sti in kandidater:
        cur = obj
        try:
            for del_ in sti.split("."):
                cur = getattr(cur, del_)
            return cur
        except AttributeError:
            continue
    raise AttributeError("Ingen af navnene findes: " + ", ".join(s for _, s in kandidater))


def _scoped(cls, scope, navn):
    return _find((cls, scope + "." + navn), (cls, navn))


# ---------------------------------------------------------------- felttyper
# QGIS >= 3.38 og QGIS 4 bruger QMetaType; ældre QGIS 3 bruger QVariant.
_BRUG_QMETATYPE = QMetaType is not None and Qgis.QGIS_VERSION_INT >= 33800


def felttype(navn):
    """Felttype til QgsField: 'Double', 'Int', 'LongLong' eller 'String'."""
    if _BRUG_QMETATYPE:
        return getattr(QMetaType.Type, "QString" if navn == "String" else navn)
    return getattr(QVariant, navn)


# ---------------------------------------------------------------- beskeder
NIVEAU_INFO = _scoped(Qgis, "MessageLevel", "Info")
NIVEAU_ADVARSEL = _scoped(Qgis, "MessageLevel", "Warning")
NIVEAU_SUCCES = _scoped(Qgis, "MessageLevel", "Success")
NIVEAU_FEJL = _scoped(Qgis, "MessageLevel", "Critical")

# ---------------------------------------------------------------- filskrivning
VFW_NO_ERROR = _scoped(QgsVectorFileWriter, "WriterError", "NoError")
VFW_OVERWRITE_LAYER = _scoped(QgsVectorFileWriter, "ActionOnExistingFile", "CreateOrOverwriteLayer")
VFW_OVERWRITE_FILE = _scoped(QgsVectorFileWriter, "ActionOnExistingFile", "CreateOrOverwriteFile")
RFW_NO_ERROR = _scoped(QgsRasterFileWriter, "WriterError", "NoError")


# ---------------------------------------------------------------- geometri
def _wkb(navn):
    return _find((Qgis, "WkbType." + navn), (QgsWkbTypes, "Type." + navn), (QgsWkbTypes, navn))


def _geom(navn):
    return _find((Qgis, "GeometryType." + navn), (QgsWkbTypes, "GeometryType." + navn + "Geometry"),
                 (QgsWkbTypes, navn + "Geometry"))


WKB_POINT = _wkb("Point")
WKB_MULTIPOINT = _wkb("MultiPoint")
WKB_LINESTRING = _wkb("LineString")
WKB_POLYGON = _wkb("Polygon")
GEOM_POINT = _geom("Point")
GEOM_LINE = _geom("Line")
GEOM_POLYGON = _geom("Polygon")


# ---------------------------------------------------------------- lagvalg
def _lag(navn):
    return _find((Qgis, "LayerFilter." + navn), (QgsMapLayerProxyModel, "Filter." + navn),
                 (QgsMapLayerProxyModel, navn))


LAG_VEKTOR = _lag("VectorLayer")
LAG_RASTER = _lag("RasterLayer")
LAG_LINJE = _lag("LineLayer")
LAG_PUNKT = _lag("PointLayer")
LAG_POLYGON = _lag("PolygonLayer")

# ---------------------------------------------------------------- diverse
RENDER_MM = _find((Qgis, "RenderUnit.Millimeters"), (QgsUnitTypes, "RenderUnit.RenderMillimeters"),
                  (QgsUnitTypes, "RenderMillimeters"))
IDENTIFY_VALUE = _find((Qgis, "RasterIdentifyFormat.Value"), (QgsRaster, "IdentifyFormat.IdentifyFormatValue"),
                       (QgsRaster, "IdentifyFormatValue"))
CRS_WKT_GDAL = _find((Qgis, "CrsWktVariant.PreferredGdal"),
                     (QgsCoordinateReferenceSystem, "WktVariant.WKT_PREFERRED_GDAL"),
                     (QgsCoordinateReferenceSystem, "WKT_PREFERRED_GDAL"))


def filwidget_tilstand(navn):
    """QgsFileWidget-tilstand: 'GetFile', 'SaveFile', 'GetDirectory' ..."""
    from qgis.gui import QgsFileWidget
    return _scoped(QgsFileWidget, "StorageMode", navn)

# ---------------------------------------------------------------- transformation
from qgis.core import QgsCoordinateTransform  # noqa: E402
TRANSFORM_OMVENDT = _find((Qgis, "TransformDirection.Reverse"),
                          (QgsCoordinateTransform, "TransformDirection.ReverseTransform"),
                          (QgsCoordinateTransform, "ReverseTransform"))
