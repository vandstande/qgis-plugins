# -*- coding: utf-8 -*-
import os
import time

from qgis.core import (
    Qgis,
    QgsProject,
    QgsRasterLayer,
    QgsRasterFileWriter,
    QgsRasterPipe,
    QgsRasterProjector,
    QgsMessageLog,
)
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QMessageBox

from .modelgrid_dialog import ModelgridDialog
from .compat import NIVEAU_ADVARSEL, NIVEAU_FEJL, NIVEAU_SUCCES, QAction, RFW_NO_ERROR

PLUGIN_NAME = "Indlæs modelgrid"


class IndlaesModelgridPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.icon_path = os.path.join(self.plugin_dir, "icon2.png")

        self.toolbar = None
        self.action = None
        self.timflow_menu = None
        self.created_timflow_menu = False

    # ------------------------------------------------------------------
    # QGIS plugin lifecycle
    # ------------------------------------------------------------------
    def initGui(self):
        # Ét ikon, der åbner dialogen med fanerne "Indlæs modelgrid" og
        # "Symbologi og terræn"
        self.action = QAction(QIcon(self.icon_path), PLUGIN_NAME, self.iface.mainWindow())
        self.action.setToolTip("Indlæs modelgrid og beregn terræn minus grid")
        self.action.triggered.connect(self.open_dialog)

        self.toolbar = self.iface.addToolBar(PLUGIN_NAME)
        self.toolbar.setObjectName("IndlaesModelgridToolbar")
        self.toolbar.addAction(self.action)

        self.iface.addPluginToMenu(PLUGIN_NAME, self.action)

        # Tilføj til "TimFlow"-menuen: brug den eksisterende, hvis den findes,
        # ellers opret menupunktet først
        self._add_to_timflow_menu()

    def unload(self):
        if self.toolbar is not None:
            self.toolbar.deleteLater()
            self.toolbar = None

        if self.action is not None:
            self.iface.removePluginMenu(PLUGIN_NAME, self.action)
            if self.timflow_menu is not None:
                self.timflow_menu.removeAction(self.action)

        # Hvis menupunktet "TimFlow" blev oprettet af dette plugin og nu er
        # tomt, fjernes det igen ved unload
        if (
            self.created_timflow_menu
            and self.timflow_menu is not None
            and not self.timflow_menu.actions()
        ):
            self.iface.mainWindow().menuBar().removeAction(
                self.timflow_menu.menuAction()
            )
            self.timflow_menu = None

    # ------------------------------------------------------------------
    def _add_to_timflow_menu(self):
        """Tilføjer pluginet til "TimFlow"-menuen.

        Hvis menupunktet allerede findes (søges både i hovedmenulinjen og i
        Plugins-menuens undermenuer), bruges det. Findes det ikke, oprettes
        menupunktet "TimFlow" i hovedmenulinjen først.
        """
        menu = self._find_menu_by_title("TimFlow")

        if menu is None:
            menu = self.iface.mainWindow().menuBar().addMenu("TimFlow")
            self.created_timflow_menu = True

        self.timflow_menu = menu
        menu.addAction(self.action)

    def _find_menu_by_title(self, title):
        menu_bar = self.iface.mainWindow().menuBar()

        # Søg i hovedmenulinjens topniveau
        for action in menu_bar.actions():
            sub_menu = action.menu()
            if sub_menu is not None and sub_menu.title().replace("&", "") == title:
                return sub_menu

        # Søg i Plugins-menuens undermenuer
        plugins_menu = getattr(self.iface, "pluginMenu", None)
        if plugins_menu is not None:
            plugins_menu = self.iface.pluginMenu()
            for action in plugins_menu.actions():
                sub_menu = action.menu()
                if sub_menu is not None and sub_menu.title().replace("&", "") == title:
                    return sub_menu

        return None

    # ------------------------------------------------------------------
    def open_dialog(self):
        dialog = ModelgridDialog(self.plugin_dir, self.load_modelgrid, self.iface.mainWindow())
        dialog.exec()

    # ------------------------------------------------------------------
    def load_modelgrid(self, grid_path, output_path, qml_path, terrain_layer):
        """Beregner terræn minus grid og indlæser resultatet. Returnerer True ved succes."""
        project_crs = QgsProject.instance().crs()

        try:
            result_path = self._compute_terrain_minus_grid(
                terrain_layer, grid_path, project_crs, output_path
            )
        except Exception as exc:
            QgsMessageLog.logMessage(str(exc), PLUGIN_NAME, level=NIVEAU_FEJL)
            QMessageBox.critical(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                "Der opstod en fejl under beregningen:\n\n{}".format(exc),
            )
            return False

        result_layer_name = os.path.splitext(os.path.basename(output_path))[0]
        result_layer = QgsRasterLayer(result_path, result_layer_name)
        if not result_layer.isValid():
            QMessageBox.critical(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                "Det beregnede resultatlag kunne ikke indlæses i QGIS.",
            )
            return False

        if qml_path:
            ok, error_msg = result_layer.loadNamedStyle(qml_path)
            if not ok:
                QgsMessageLog.logMessage(
                    "Kunne ikke indlæse symbologi: {}".format(error_msg),
                    PLUGIN_NAME,
                    level=NIVEAU_ADVARSEL,
                )
            result_layer.triggerRepaint()

        QgsProject.instance().addMapLayer(result_layer)
        self.iface.messageBar().pushMessage(
            PLUGIN_NAME,
            "Modelgrid indlæst og lag '{}' oprettet.".format(result_layer_name),
            level=NIVEAU_SUCCES,
            duration=5,
        )
        return True

    # ------------------------------------------------------------------
    def _find_layer_by_name(self, name):
        for lyr in QgsProject.instance().mapLayers().values():
            if lyr.name() == name:
                return lyr
        return None

    # ------------------------------------------------------------------
    def _compute_terrain_minus_grid(self, terrain_layer, grid_path, project_crs, output_path):
        """Tilpasser gridfilens georeference til QGIS-projektet og beregner
        terræn minus grid.

        Terrænlaget kan være et hvilket som helst rasterlag-typer QGIS
        understøtter (lokal fil, WCS, WMS osv.) - det materialiseres derfor
        via QGIS' egen raster-pipeline (QgsRasterFileWriter), i stedet for
        at åbne lagets kilde-URI direkte med GDAL, som kun virker for
        lokale filer.

        Resultatet skrives til output_path (brugerens valgte fil).
        Returnerer stien til den beregnede GeoTIFF-fil.
        """
        from osgeo import gdal, osr
        import numpy as np
        import tempfile

        gdal.UseExceptions()
        tmp_dir = tempfile.gettempdir()
        stamp = int(time.time())

        # --- 1. Åbn gridfilen og bestem målgrid (udstrækning/opløsning) ---
        # Modelgridfilens egen opløsning og udstrækning bruges som facit for
        # beregningen. Hvis den ikke har en defineret CRS, antages det at
        # dens koordinater allerede passer med projektet, og projekt-CRS'en
        # tildeles ("samme georeference som QGIS-projektet").
        grid_layer = QgsRasterLayer(grid_path, "indlaes_modelgrid_tmp")
        if not grid_layer.isValid():
            raise RuntimeError("Grid-filen kunne ikke åbnes ({}).".format(grid_path))

        if not grid_layer.crs().isValid():
            grid_layer.setCrs(project_crs)

        target_crs = grid_layer.crs()
        target_extent = grid_layer.extent()
        width = grid_layer.width()
        height = grid_layer.height()

        # --- 2. Materialisér terrænlaget på nøjagtig samme grid ---
        terrain_out_path = os.path.join(
            tmp_dir, "indlaes_modelgrid_terraen_{}.tif".format(stamp)
        )
        self._materialize_raster_layer(
            terrain_layer, target_extent, width, height, target_crs, terrain_out_path
        )

        terrain_ds = gdal.Open(terrain_out_path)
        if terrain_ds is None:
            raise RuntimeError("Det materialiserede terrænlag kunne ikke åbnes.")
        geotransform = terrain_ds.GetGeoTransform()
        projection_wkt = terrain_ds.GetProjection() or target_crs.toWkt()

        terrain_band = terrain_ds.GetRasterBand(1)
        terrain_array = terrain_band.ReadAsArray().astype(np.float64)
        terrain_nodata = terrain_band.GetNoDataValue()

        # --- 3. Læs gridfilen direkte (allerede på målgriddet) ---
        grid_ds = gdal.Open(grid_path)
        if grid_ds is None:
            raise RuntimeError("Grid-filen kunne ikke åbnes ({}).".format(grid_path))

        grid_band = grid_ds.GetRasterBand(1)
        grid_array = grid_band.ReadAsArray().astype(np.float64)
        grid_nodata = grid_band.GetNoDataValue()

        if grid_array.shape != terrain_array.shape:
            raise RuntimeError(
                "Terræn- og gridlaget har ikke samme dimensioner efter tilpasning "
                "({} vs {}).".format(terrain_array.shape, grid_array.shape)
            )

        # --- 4. Beregn terræn minus grid ---
        result_array = terrain_array - grid_array

        out_nodata = -9999.0
        mask = np.zeros(result_array.shape, dtype=bool)
        if terrain_nodata is not None:
            mask |= np.isclose(terrain_array, terrain_nodata)
        if grid_nodata is not None:
            mask |= np.isclose(grid_array, grid_nodata)
        result_array[mask] = out_nodata

        out_path = output_path
        driver = gdal.GetDriverByName("GTiff")
        out_ds = driver.Create(out_path, width, height, 1, gdal.GDT_Float32)
        out_ds.SetGeoTransform(geotransform)
        out_ds.SetProjection(projection_wkt)
        out_band = out_ds.GetRasterBand(1)
        out_band.WriteArray(result_array.astype(np.float32))
        out_band.SetNoDataValue(out_nodata)
        out_band.FlushCache()

        # Ryd op
        out_ds = None
        terrain_ds = None
        grid_ds = None

        return out_path

    # ------------------------------------------------------------------
    def _materialize_raster_layer(self, layer, extent, width, height, crs, out_path):
        """Skriver et rasterlags data (uanset provider-type: fil, WCS, WMS
        osv.) til en lokal GeoTIFF-fil, tilpasset den angivne udstrækning,
        opløsning og CRS.
        """
        provider = layer.dataProvider()

        pipe = QgsRasterPipe()
        if not pipe.set(provider.clone()):
            raise RuntimeError(
                "Kunne ikke forberede laget '{}' til beregning.".format(layer.name())
            )

        if layer.crs() != crs:
            projector = QgsRasterProjector()
            projector.setCrs(layer.crs(), crs, QgsProject.instance().transformContext())
            if not pipe.insert(1, projector):
                raise RuntimeError(
                    "Kunne ikke reprojicere laget '{}'.".format(layer.name())
                )

        file_writer = QgsRasterFileWriter(out_path)
        file_writer.setOutputFormat("GTiff")
        error = file_writer.writeRaster(
            pipe, width, height, extent, crs, QgsProject.instance().transformContext()
        )
        if error != RFW_NO_ERROR:
            raise RuntimeError(
                "Fejl ved indlæsning af data for laget '{}' (fejlkode {}). "
                "Dette kan skyldes en netværksfejl mod datakilden.".format(
                    layer.name(), error
                )
            )
