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
    QgsSettings,
    QgsMessageLog,
)
from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QFileDialog, QMessageBox

from .symbology_dialog import (
    SymbologyDialog,
    SETTINGS_QML_KEY,
    SETTINGS_TERRAIN_LAYER_KEY,
)

PLUGIN_NAME = "Indlæs modelgrid"


class IndlaesModelgridPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.icon1_path = os.path.join(self.plugin_dir, "icon1.png")
        self.icon2_path = os.path.join(self.plugin_dir, "icon2.png")

        self.toolbar = None
        self.action_symbology = None
        self.action_load_grid = None
        self.timflow_menu = None
        self.created_timflow_menu = False

    # ------------------------------------------------------------------
    # QGIS plugin lifecycle
    # ------------------------------------------------------------------
    def initGui(self):
        icon1 = QIcon(self.icon1_path)
        icon2 = QIcon(self.icon2_path)

        # Egen værktøjslinje med hver sit ikon pr. knap
        self.toolbar = self.iface.addToolBar(PLUGIN_NAME)
        self.toolbar.setObjectName("IndlaesModelgridToolbar")

        self.action_symbology = QAction(icon1, "Tilknyt symbologi", self.iface.mainWindow())
        self.action_symbology.triggered.connect(self.open_symbology_dialog)
        self.action_symbology.setToolTip("Vælg symbologifil og terrænlag")

        self.action_load_grid = QAction(icon2, "Indlæs modelgrid", self.iface.mainWindow())
        self.action_load_grid.triggered.connect(self.load_modelgrid)
        self.action_load_grid.setToolTip("Indlæs en modelgrid-fil og beregn terræn minus grid")

        self.toolbar.addAction(self.action_symbology)
        self.toolbar.addAction(self.action_load_grid)

        # Tilføj til Plugins-menuen som normalt
        self.iface.addPluginToMenu(PLUGIN_NAME, self.action_symbology)
        self.iface.addPluginToMenu(PLUGIN_NAME, self.action_load_grid)

        # Tilføj til "TimFlow"-menuen: brug den eksisterende, hvis den findes,
        # ellers opret menupunktet først
        self._add_to_timflow_menu()

    def unload(self):
        if self.toolbar is not None:
            self.toolbar.deleteLater()
            self.toolbar = None

        if self.action_symbology is not None:
            self.iface.removePluginMenu(PLUGIN_NAME, self.action_symbology)
            if self.timflow_menu is not None:
                self.timflow_menu.removeAction(self.action_symbology)

        if self.action_load_grid is not None:
            self.iface.removePluginMenu(PLUGIN_NAME, self.action_load_grid)
            if self.timflow_menu is not None:
                self.timflow_menu.removeAction(self.action_load_grid)

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
        """Tilføjer pluginets handlinger til "TimFlow"-menuen.

        Hvis menupunktet allerede findes (søges både i hovedmenulinjen og i
        Plugins-menuens undermenuer), bruges det. Findes det ikke, oprettes
        menupunktet "TimFlow" i hovedmenulinjen først, hvorefter pluginet
        tilføjes til det.
        """
        menu = self._find_menu_by_title("TimFlow")

        if menu is None:
            menu = self.iface.mainWindow().menuBar().addMenu("TimFlow")
            self.created_timflow_menu = True

        self.timflow_menu = menu
        menu.addAction(self.action_symbology)
        menu.addAction(self.action_load_grid)

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
    # Knap 1: Tilknyt symbologi
    # ------------------------------------------------------------------
    def open_symbology_dialog(self):
        dialog = SymbologyDialog(self.iface.mainWindow())
        dialog.exec_()

    # ------------------------------------------------------------------
    # Knap 2: Indlæs modelgrid
    # ------------------------------------------------------------------
    def load_modelgrid(self):
        settings = QgsSettings()
        qml_path = settings.value(SETTINGS_QML_KEY, "", type=str)
        terrain_name = settings.value(SETTINGS_TERRAIN_LAYER_KEY, "", type=str)

        if not terrain_name:
            QMessageBox.warning(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                "Der er ikke valgt et terrænlag endnu.\n\n"
                "Klik først på 'Tilknyt symbologi' og vælg en symbologifil og et terrænlag.",
            )
            return

        terrain_layer = self._find_layer_by_name(terrain_name)
        if terrain_layer is None:
            QMessageBox.warning(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                "Det gemte terrænlag ('{}') findes ikke i det aktuelle projekt.\n\n"
                "Vælg venligst terrænlaget igen under 'Tilknyt symbologi'.".format(
                    terrain_name
                ),
            )
            return

        if qml_path and not os.path.isfile(qml_path):
            QMessageBox.warning(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                "Den gemte symbologifil kunne ikke findes:\n{}\n\n"
                "Vælg venligst en symbologifil igen under 'Tilknyt symbologi'.".format(
                    qml_path
                ),
            )
            return

        grid_path, _ = QFileDialog.getOpenFileName(
            self.iface.mainWindow(),
            "Vælg modelgrid-fil",
            "",
            "Grid-filer (*.grd);;Alle filer (*.*)",
        )
        if not grid_path:
            return

        default_name = "terraen_minus_modelgrid_{}.tif".format(
            os.path.splitext(os.path.basename(grid_path))[0]
        )
        default_dir = os.path.dirname(grid_path)
        default_path = os.path.join(default_dir, default_name)

        output_path, _ = QFileDialog.getSaveFileName(
            self.iface.mainWindow(),
            "Vælg navn og placering for resultatfilen",
            default_path,
            "GeoTIFF (*.tif)",
        )
        if not output_path:
            return
        if not output_path.lower().endswith(".tif"):
            output_path += ".tif"

        project_crs = QgsProject.instance().crs()

        try:
            result_path = self._compute_terrain_minus_grid(
                terrain_layer, grid_path, project_crs, output_path
            )
        except Exception as exc:
            QgsMessageLog.logMessage(str(exc), PLUGIN_NAME, level=Qgis.Critical)
            QMessageBox.critical(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                "Der opstod en fejl under beregningen:\n\n{}".format(exc),
            )
            return


        result_layer_name = os.path.splitext(os.path.basename(output_path))[0]
        result_layer = QgsRasterLayer(result_path, result_layer_name)
        if not result_layer.isValid():
            QMessageBox.critical(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                "Det beregnede resultatlag kunne ikke indlæses i QGIS.",
            )
            return

        if qml_path:
            ok, error_msg = result_layer.loadNamedStyle(qml_path)
            if not ok:
                QgsMessageLog.logMessage(
                    "Kunne ikke indlæse symbologi: {}".format(error_msg),
                    PLUGIN_NAME,
                    level=Qgis.Warning,
                )
            result_layer.triggerRepaint()

        QgsProject.instance().addMapLayer(result_layer)
        self.iface.messageBar().pushMessage(
            PLUGIN_NAME,
            "Modelgrid indlæst og lag '{}' oprettet.".format(result_layer_name),
            level=Qgis.Success,
            duration=5,
        )

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
        if error != QgsRasterFileWriter.NoError:
            raise RuntimeError(
                "Fejl ved indlæsning af data for laget '{}' (fejlkode {}). "
                "Dette kan skyldes en netværksfejl mod datakilden.".format(
                    layer.name(), error
                )
            )
