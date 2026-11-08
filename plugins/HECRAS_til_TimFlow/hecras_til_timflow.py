# -*- coding: utf-8 -*-
import os
import re
import math
import random
import string

from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMenu, QMessageBox

from qgis.core import (
    QgsProject,
    QgsVectorFileWriter,
    QgsFields,
    QgsField,
    QgsFeature,
    QgsGeometry,
    QgsWkbTypes,
    QgsVectorLayer,
    QgsCoordinateTransform,
)

from .hecras_til_timflow_dialog import HecrasTilTimFlowDialog


class HecrasTilTimFlow:
    """QGIS-plugin: HECRAS til TimFlow."""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.menu_name = "HECRAS til TimFlow"
        self.timflow_menu = None
        self._created_timflow_menu = False

    # ------------------------------------------------------------------
    # GUI-registrering
    # ------------------------------------------------------------------

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        self.action = QAction(
            QIcon(icon_path), "HECRAS til TimFlow", self.iface.mainWindow()
        )
        self.action.setWhatsThis("Opret punktlag med koter fra HECRAS til TimFlow")
        self.action.triggered.connect(self.run)

        # Ikon i toolbaren
        self.iface.addToolBarIcon(self.action)

        # Standardplacering under Plugins-menuen
        self.iface.addPluginToMenu(self.menu_name, self.action)

        # Findes top-level menuen "TimFlow" allerede, tilfoejes pluginet der.
        # Findes den ikke, oprettes menuen foerst, og pluginet tilfoejes
        # derefter til den.
        self.timflow_menu = self._find_timflow_menu()
        if self.timflow_menu is None:
            self.timflow_menu = self._create_timflow_menu()
            self._created_timflow_menu = True

        self.timflow_menu.addAction(self.action)

    def unload(self):
        self.iface.removePluginMenu(self.menu_name, self.action)
        self.iface.removeToolBarIcon(self.action)

        if self.timflow_menu is not None:
            self.timflow_menu.removeAction(self.action)
            # Fjern kun selve "TimFlow"-menuen igen, hvis det var os der
            # oprettede den, og den nu er tom (dvs. intet andet plugin har
            # tilfoejet sig til den i mellemtiden)
            if self._created_timflow_menu and self.timflow_menu.isEmpty():
                menubar = self.iface.mainWindow().menuBar()
                menubar.removeAction(self.timflow_menu.menuAction())
                self.timflow_menu.deleteLater()

    def _find_timflow_menu(self):
        """Finder en evt. eksisterende top-level menu 'TimFlow' i menubaren."""
        menubar = self.iface.mainWindow().menuBar()
        for action in menubar.actions():
            menu = action.menu()
            if menu is not None:
                title = menu.title().replace("&", "").strip()
                if title.lower() == "timflow":
                    return menu
        return None

    def _create_timflow_menu(self):
        """Opretter en ny top-level menu 'TimFlow' i menubaren, placeret
        foer Hjaelp-menuen (hvis den findes), ligesom QGIS' egne menuer."""
        menubar = self.iface.mainWindow().menuBar()
        menu = QMenu("TimFlow", menubar)

        help_menu = None
        for action in menubar.actions():
            m = action.menu()
            if m is not None and m.title().replace("&", "").strip().lower() == "help":
                help_menu = m
                break

        if help_menu is not None:
            menubar.insertMenu(help_menu.menuAction(), menu)
        else:
            menubar.addMenu(menu)

        return menu

    # ------------------------------------------------------------------
    # Kørsel
    # ------------------------------------------------------------------

    def run(self):
        dlg = HecrasTilTimFlowDialog(self.iface.mainWindow())
        if not dlg.exec_():
            return

        raster_layer = dlg.raster_layer()
        line_layer = dlg.line_layer()
        gpkg_layer = dlg.gpkg_layer()
        spacing = dlg.spacing()

        if raster_layer is None or line_layer is None or gpkg_layer is None:
            QMessageBox.warning(
                self.iface.mainWindow(),
                "HECRAS til TimFlow",
                "Vaelg venligst HECRAS resultatfil, HECRAS vandlob og "
                "TimFlow model, foer du fortsaetter.",
            )
            return

        if spacing is None or spacing <= 0:
            QMessageBox.warning(
                self.iface.mainWindow(),
                "HECRAS til TimFlow",
                "Punktafstand skal vaere stoerre end 0.",
            )
            return

        try:
            created = self._process(raster_layer, line_layer, gpkg_layer, spacing)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(
                self.iface.mainWindow(),
                "HECRAS til TimFlow",
                "Der opstod en fejl under behandlingen:\n{}".format(e),
            )
            return

        QMessageBox.information(
            self.iface.mainWindow(),
            "HECRAS til TimFlow",
            "Faerdig. {} punktlag blev oprettet i geopackagen.".format(created),
        )

    # ------------------------------------------------------------------
    # Hjaelpefunktioner
    # ------------------------------------------------------------------

    @staticmethod
    def _gpkg_path(gpkg_layer):
        """Udleder den underliggende .gpkg-fils sti fra et QGIS-lag."""
        source = gpkg_layer.source()
        return source.split("|")[0]

    @staticmethod
    def _sanitize_name(name):
        """Goer et navn brugbart som lagnavn/tabelnavn i en geopackage."""
        name = re.sub(r"[^A-Za-z0-9_]", "_", name)
        name = re.sub(r"_+", "_", name).strip("_")
        if not name or not re.match(r"^[A-Za-z_]", name):
            name = "lag_{}".format(name) if name else "lag"
        return name[:60]

    @staticmethod
    def _random_name():
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        return "punkter_{}".format(suffix)

    @staticmethod
    def _unique_name(name, used):
        if name not in used:
            return name
        i = 2
        candidate = "{}_{}".format(name, i)
        while candidate in used:
            i += 1
            candidate = "{}_{}".format(name, i)
        return candidate

    @staticmethod
    def _points_along_geometry(geom, spacing):
        """Genererer punkter med fast afstand langs en (evt. multipart) linje.

        Startpunkt (0) og slutpunkt (fuld laengde) er altid med.
        """
        points = []
        parts = geom.asGeometryCollection() if geom.isMultipart() else [geom]

        for part in parts:
            length = part.length()
            if length <= 0:
                continue

            distances = []
            d = 0.0
            while d < length:
                distances.append(d)
                d += spacing
            distances.append(length)

            seen = set()
            for dist in distances:
                key = round(dist, 6)
                if key in seen:
                    continue
                seen.add(key)

                pt_geom = part.interpolate(dist)
                if pt_geom is None or pt_geom.isEmpty():
                    continue
                points.append(pt_geom.asPoint())

        return points

    @staticmethod
    def _sample_value(provider, transform, point):
        """Sampler rasterens vaerdi i et punkt. Returnerer -999 hvis der
        ikke findes en gyldig vaerdi."""
        p = point
        if transform is not None:
            try:
                p = transform.transform(p)
            except Exception:  # noqa: BLE001
                return -999.0

        try:
            val, ok = provider.sample(p, 1)
        except Exception:  # noqa: BLE001
            return -999.0

        if not ok or val is None:
            return -999.0

        try:
            if math.isnan(val):
                return -999.0
        except TypeError:
            return -999.0

        return float(val)

    # ------------------------------------------------------------------
    # Selve databehandlingen
    # ------------------------------------------------------------------

    def _process(self, raster_layer, line_layer, gpkg_layer, spacing):
        project = QgsProject.instance()
        project_crs = project.crs()
        transform_context = project.transformContext()

        gpkg_path = self._gpkg_path(gpkg_layer)

        # Transform fra vandlobslagets CRS til projektets CRS
        line_crs = line_layer.crs()
        line_to_project = None
        if line_crs != project_crs:
            line_to_project = QgsCoordinateTransform(line_crs, project_crs, project)

        # Transform fra projektets CRS til rasterens CRS (til sampling)
        raster_crs = raster_layer.crs()
        project_to_raster = None
        if raster_crs != project_crs:
            project_to_raster = QgsCoordinateTransform(
                project_crs, raster_crs, project
            )

        raster_provider = raster_layer.dataProvider()

        # De nye punktlag skal placeres i samme gruppe i lag-panelet som
        # det lag, der er valgt som "TimFlow model". Findes laget ikke i
        # nogen gruppe (dvs. det ligger direkte i roden), placeres de nye
        # lag i roden.
        root = project.layerTreeRoot()
        gpkg_layer_node = root.findLayer(gpkg_layer.id())
        target_group = gpkg_layer_node.parent() if gpkg_layer_node is not None else root

        field_names = [f.name() for f in line_layer.fields()]
        has_river_reach = "River" in field_names and "Reach" in field_names

        used_names = set()
        created_count = 0

        for feature in line_layer.getFeatures():
            geom = feature.geometry()
            if geom is None or geom.isEmpty():
                continue

            geom = QgsGeometry(geom)
            if line_to_project is not None:
                geom.transform(line_to_project)

            # -------- Navngivning af punktlaget --------
            if has_river_reach:
                river_val = feature["River"]
                reach_val = feature["Reach"]
                river_val = "" if river_val is None else str(river_val)
                reach_val = "" if reach_val is None else str(reach_val)
                base_name = "{}_{}".format(river_val, reach_val).strip("_")
                if not base_name:
                    base_name = self._random_name()
            else:
                base_name = self._random_name()

            layer_name = self._sanitize_name(base_name)
            layer_name = self._unique_name(layer_name, used_names)
            used_names.add(layer_name)

            # -------- Punkter langs linjen --------
            points = self._points_along_geometry(geom, spacing)
            if not points:
                continue

            fields = QgsFields()
            fields.append(QgsField("kote", QVariant.Double))

            save_options = QgsVectorFileWriter.SaveVectorOptions()
            save_options.driverName = "GPKG"
            save_options.layerName = layer_name
            save_options.actionOnExistingFile = (
                QgsVectorFileWriter.CreateOrOverwriteLayer
            )
            save_options.fileEncoding = "UTF-8"

            writer = QgsVectorFileWriter.create(
                gpkg_path,
                fields,
                QgsWkbTypes.Point,
                project_crs,
                transform_context,
                save_options,
            )

            if writer is None or writer.hasError() != QgsVectorFileWriter.NoError:
                err = writer.errorMessage() if writer is not None else "ukendt fejl"
                raise RuntimeError(
                    "Kunne ikke oprette laget '{}': {}".format(layer_name, err)
                )

            for pt in points:
                kote = self._sample_value(raster_provider, project_to_raster, pt)
                feat = QgsFeature(fields)
                feat.setGeometry(QgsGeometry.fromPointXY(pt))
                feat.setAttribute("kote", kote)
                writer.addFeature(feat)

            del writer  # sikrer at data skrives til disk

            new_layer = QgsVectorLayer(
                "{}|layername={}".format(gpkg_path, layer_name),
                layer_name,
                "ogr",
            )
            if new_layer.isValid():
                # Tilfoej til projektet uden at QGIS selv placerer laget i
                # lag-panelet, saa vi selv kan indsaette det i den rigtige
                # gruppe.
                project.addMapLayer(new_layer, False)
                target_group.addLayer(new_layer)

            created_count += 1

        return created_count
