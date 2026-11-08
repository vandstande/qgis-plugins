# -*- coding: utf-8 -*-
"""
Import AEM elementer
---------------------
Læser en .bbl fil med AEM elementer og opretter et punktlag pr. element
i en valgt geopackage. Format i .bbl filen:

    Head <NAVN>
     <X> <Y> <Z>
     <X> <Y> <Z>
     <X> <Y> <Z>
    &

Hvert "Head <NAVN> ... &" bliver til et punktlag med navnet <NAVN>.
X og Y bruges kun som geometri. Z gemmes i feltet "kote" (2 decimaler).
De oprettede lag får projektets aktuelle CRS (georeference).
"""

import os

from qgis.PyQt.QtCore import QVariant, Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QMessageBox
from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
)

from .import_aem_elementer_dialog import ImportAemElementerDialog
from .compat import NIVEAU_SUCCES, QAction, VFW_NO_ERROR, VFW_OVERWRITE_FILE, VFW_OVERWRITE_LAYER, felttype


class ImportAemElementer:

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.menu = "&Import AEM elementer"

    # ------------------------------------------------------------------
    # QGIS plugin boilerplate
    # ------------------------------------------------------------------
    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        self.action = QAction(QIcon(icon_path), "Import AEM elementer", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.action.setEnabled(True)

        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu(self.menu, self.action)

    def unload(self):
        self.iface.removePluginMenu(self.menu, self.action)
        self.iface.removeToolBarIcon(self.action)

    # ------------------------------------------------------------------
    # Hovedlogik
    # ------------------------------------------------------------------
    def run(self):
        dlg = ImportAemElementerDialog(self.iface.mainWindow())
        if not dlg.exec():
            return

        bbl_path = dlg.get_bbl_path()
        layer = dlg.get_gpkg_layer()

        if not bbl_path or not os.path.isfile(bbl_path):
            QMessageBox.warning(
                self.iface.mainWindow(),
                "Import AEM elementer",
                "Vælg venligst en gyldig .bbl fil.",
            )
            return

        if layer is None:
            QMessageBox.warning(
                self.iface.mainWindow(),
                "Import AEM elementer",
                "Vælg venligst et lag der ligger i den ønskede geopackage.",
            )
            return

        gpkg_path = self.get_gpkg_path_from_layer(layer)
        if not gpkg_path:
            QMessageBox.warning(
                self.iface.mainWindow(),
                "Import AEM elementer",
                "Det valgte lag ser ikke ud til at komme fra en geopackage (.gpkg).",
            )
            return

        try:
            elements = self.parse_bbl(bbl_path)
        except Exception as e:
            QMessageBox.critical(
                self.iface.mainWindow(),
                "Import AEM elementer",
                "Fejl ved læsning af .bbl fil:\n{}".format(e),
            )
            return

        if not elements:
            QMessageBox.warning(
                self.iface.mainWindow(),
                "Import AEM elementer",
                "Der blev ikke fundet nogen AEM elementer (Head ... &) i filen.",
            )
            return

        project_crs = QgsProject.instance().crs()

        created = []
        errors = []
        for name, points in elements:
            try:
                self.create_point_layer(gpkg_path, name, points, project_crs)
                created.append(name)
            except Exception as e:
                errors.append("{}: {}".format(name, e))

        # Indlæs de oprettede lag i projektet
        for name in created:
            uri = "{}|layername={}".format(gpkg_path, name)
            vlayer = QgsVectorLayer(uri, name, "ogr")
            if vlayer.isValid():
                QgsProject.instance().addMapLayer(vlayer)

        summary = "{} af {} elementer blev importeret som punktlag.".format(
            len(created), len(elements)
        )
        if errors:
            summary += "\n\nFølgende elementer fejlede:\n" + "\n".join(errors)
            QMessageBox.warning(self.iface.mainWindow(), "Import AEM elementer", summary)
        else:
            self.iface.messageBar().pushMessage(
                "Import AEM elementer", summary, level=NIVEAU_SUCCES, duration=6
            )

    # ------------------------------------------------------------------
    # Hjælpefunktioner
    # ------------------------------------------------------------------
    def get_gpkg_path_from_layer(self, layer):
        """Udled stien til .gpkg filen fra et lag valgt i lagpanelet."""
        try:
            uri = layer.dataProvider().dataSourceUri()
        except Exception:
            return None

        # Eksempel: "/sti/til/fil.gpkg|layername=lagnavn"
        path = uri.split("|")[0]
        if path.lower().endswith(".gpkg"):
            return path

        source = layer.source()
        path = source.split("|")[0]
        if path.lower().endswith(".gpkg"):
            return path

        return None

    def parse_bbl(self, file_path):
        """
        Parser en .bbl fil og returnerer en liste af (navn, punkter),
        hvor punkter er en liste af (x, y, z) tupler.
        """
        elements = []
        current_name = None
        current_points = []

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue

                if line.lower().startswith("head"):
                    parts = line.split(None, 1)
                    current_name = parts[1].strip() if len(parts) > 1 else "Unavngivet"
                    current_points = []
                    continue

                if line == "&":
                    if current_name is not None:
                        elements.append((current_name, current_points))
                    current_name = None
                    current_points = []
                    continue

                if current_name is not None:
                    coords = line.replace(",", ".").split()
                    if len(coords) >= 3:
                        try:
                            x = float(coords[0])
                            y = float(coords[1])
                            z = float(coords[2])
                            current_points.append((x, y, z))
                        except ValueError:
                            # Spring linjer over der ikke kan tolkes som koordinater
                            continue

        # Hvis filen slutter uden afsluttende "&", medtag alligevel det sidste element
        if current_name is not None and current_points:
            elements.append((current_name, current_points))

        return elements

    def sanitize_layer_name(self, name):
        """Fjern tegn der er problematiske som lagnavn i en geopackage."""
        invalid = '<>:"/\\|?*'
        cleaned = "".join(c for c in name if c not in invalid).strip()
        return cleaned if cleaned else "Unavngivet"

    def create_point_layer(self, gpkg_path, name, points, project_crs):
        """Opretter et punktlag med navnet 'name' i geopackagen gpkg_path."""
        layer_name = self.sanitize_layer_name(name)

        crs = QgsCoordinateReferenceSystem(project_crs)

        mem_layer = QgsVectorLayer(
            "Point?crs={}".format(crs.authid() if crs.isValid() else "EPSG:4326"),
            layer_name,
            "memory",
        )

        fields = QgsFields()
        fields.append(QgsField("kote", felttype("Double"), "double", 10, 2))
        mem_layer.dataProvider().addAttributes(fields)
        mem_layer.updateFields()

        features = []
        for x, y, z in points:
            feat = QgsFeature(mem_layer.fields())
            feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
            feat.setAttribute("kote", round(z, 2))
            features.append(feat)

        mem_layer.dataProvider().addFeatures(features)
        mem_layer.updateExtents()

        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = layer_name
        if os.path.exists(gpkg_path):
            options.actionOnExistingFile = VFW_OVERWRITE_LAYER
        else:
            options.actionOnExistingFile = VFW_OVERWRITE_FILE

        write_result = QgsVectorFileWriter.writeAsVectorFormatV3(
            mem_layer,
            gpkg_path,
            QgsProject.instance().transformContext(),
            options,
        )

        # writeAsVectorFormatV3 returnerer en tuple hvor første element er
        # fejlkoden (VFW_NO_ERROR ved success)
        error_code = write_result[0]
        error_message = write_result[1] if len(write_result) > 1 else ""

        if error_code != VFW_NO_ERROR:
            raise RuntimeError(error_message or "Ukendt fejl ved skrivning til geopackage")
