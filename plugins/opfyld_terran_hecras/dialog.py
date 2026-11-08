import os
import traceback

from qgis.PyQt.QtCore import QSettings, Qt
from qgis.PyQt.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QRadioButton,
    QVBoxLayout,
)
from qgis.core import QgsMapLayerProxyModel, QgsProject, QgsRasterLayer
from qgis.gui import QgsFileWidget, QgsMapLayerComboBox

from .terrain import TerrainError, layer_file_path, luk_terraen
from .compat import LAG_LINJE, LAG_RASTER, filwidget_tilstand

TITLE = "Opfyld terræn HECRAS"
SETTINGS_KEY = "OpfyldTerranHecras/"

INTRO = (
    "Terrænet mellem de 2 banklines udjævnes (lukkes). Koten i hver celle "
    "interpoleres lineært mellem terrænkoten på nærmeste punkt på den ene og "
    "den anden bankline. Resultatet får samme cellestørrelse, gitterplacering "
    "og koordinatsystem som højdemodellen og gemmes som GeoTIFF for arealet "
    "mellem de 2 banklines."
)


def _same_file(a, b):
    if not a or not b:
        return False
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


class OpfyldDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle(TITLE)
        self.setMinimumWidth(560)

        lay = QVBoxLayout(self)
        intro = QLabel(INTRO)
        intro.setWordWrap(True)
        lay.addWidget(intro)

        # ---- Banklines
        form = QFormLayout()
        self.cbBank = QgsMapLayerComboBox()
        self.cbBank.setFilters(LAG_LINJE)
        self.chkSel = QCheckBox("Brug kun valgte features (præcis 2 linjer)")
        form.addRow("Banklines:", self.cbBank)
        form.addRow("", self.chkSel)
        lay.addLayout(form)

        # ---- Terrænmodel: fra Layers eller GeoTIFF-fil
        grp = QGroupBox("Terrænmodel")
        g = QFormLayout(grp)
        self.rbLayer = QRadioButton("Fra Layers")
        self.rbFile = QRadioButton("GeoTIFF-fil")
        self.bg = QButtonGroup(self)
        self.bg.addButton(self.rbLayer)
        self.bg.addButton(self.rbFile)
        self.cbDem = QgsMapLayerComboBox()
        self.cbDem.setFilters(LAG_RASTER)
        self.fwDem = QgsFileWidget()
        self.fwDem.setStorageMode(filwidget_tilstand("GetFile"))
        self.fwDem.setFilter("GeoTIFF (*.tif *.tiff);;Alle filer (*.*)")
        self.fwDem.setDialogTitle("Vælg terrænmodel (GeoTIFF)")
        g.addRow(self.rbLayer, self.cbDem)
        g.addRow(self.rbFile, self.fwDem)
        lay.addWidget(grp)

        # ---- Output
        form2 = QFormLayout()
        self.fwOut = QgsFileWidget()
        self.fwOut.setStorageMode(filwidget_tilstand("SaveFile"))
        self.fwOut.setFilter("GeoTIFF (*.tif *.tiff)")
        self.fwOut.setDialogTitle("Gem udjævnet terræn som GeoTIFF")
        form2.addRow("Gem GeoTIFF som:", self.fwOut)
        lay.addLayout(form2)

        self.chkFill = QCheckBox(
            "Kun opfyldning (terræn der ligger højere end den udjævnede flade bevares)")
        self.chkLoad = QCheckBox("Indlæs resultatet i Layers")
        self.chkLoad.setChecked(True)
        lay.addWidget(self.chkFill)
        lay.addWidget(self.chkLoad)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.lblStatus = QLabel("")
        lay.addWidget(self.progress)
        lay.addWidget(self.lblStatus)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btnRun = QPushButton("Luk Terræn")
        self.btnRun.setDefault(True)
        self.btnClose = QPushButton("Afslut")
        btns.addWidget(self.btnRun)
        btns.addWidget(self.btnClose)
        lay.addLayout(btns)

        # ---- Husk sidste valg
        st = QSettings()
        self.fwDem.setFilePath(st.value(SETTINGS_KEY + "dem_file", "", type=str))
        use_file = st.value(SETTINGS_KEY + "use_file", False, type=bool)
        (self.rbFile if use_file else self.rbLayer).setChecked(True)

        self.rbLayer.toggled.connect(self._update_enabled)
        self.fwDem.fileChanged.connect(lambda _p: self.rbFile.setChecked(True))
        self.btnRun.clicked.connect(self.run)
        self.btnClose.clicked.connect(self.close)
        self._update_enabled()

    # ------------------------------------------------------------------
    def _update_enabled(self):
        self.cbDem.setEnabled(self.rbLayer.isChecked())

    def _set_progress(self, value, text=""):
        self.progress.setValue(int(max(0, min(100, value))))
        if text:
            self.lblStatus.setText(text)
        QApplication.processEvents()

    def _layers_using_file(self, path):
        hits = []
        for lyr in QgsProject.instance().mapLayers().values():
            src = lyr.source().split("|")[0]
            if _same_file(src, path):
                hits.append(lyr)
        return hits

    def run(self):
        bank = self.cbBank.currentLayer()
        out = self.fwOut.filePath().strip()
        use_file = self.rbFile.isChecked()
        dem_layer, dem_path = None, ""

        if bank is None:
            QMessageBox.warning(self, TITLE, "Vælg et banklines-lag.")
            return
        if use_file:
            dem_path = self.fwDem.filePath().strip()
            if not dem_path:
                QMessageBox.warning(self, TITLE, "Vælg en GeoTIFF-fil med terrænmodellen.")
                return
            if not os.path.isfile(dem_path):
                QMessageBox.warning(self, TITLE, f"Terrænfilen findes ikke:\n{dem_path}")
                return
            dem_src = dem_path
        else:
            dem_layer = self.cbDem.currentLayer()
            if dem_layer is None:
                QMessageBox.warning(self, TITLE, "Vælg en terrænmodel i Layers.")
                return
            dem_src = layer_file_path(dem_layer)
        if not out:
            QMessageBox.warning(self, TITLE, "Angiv hvor GeoTIFF-filen skal gemmes.")
            return
        if not out.lower().endswith((".tif", ".tiff")):
            out += ".tif"
            self.fwOut.setFilePath(out)
        if _same_file(out, dem_src):
            QMessageBox.warning(self, TITLE, "Resultatet må ikke overskrive terrænmodellen.")
            return

        loaded = self._layers_using_file(out)
        if loaded:
            svar = QMessageBox.question(
                self, TITLE,
                "Filen er indlæst i Layers. Skal laget fjernes, så filen kan overskrives?")
            if svar != QMessageBox.StandardButton.Yes:
                return
            QgsProject.instance().removeMapLayers([l.id() for l in loaded])
            QApplication.processEvents()

        st = QSettings()
        st.setValue(SETTINGS_KEY + "use_file", use_file)
        if dem_path:
            st.setValue(SETTINGS_KEY + "dem_file", dem_path)

        self.btnRun.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        info = None
        try:
            info = luk_terraen(bank, out, dem_layer=dem_layer, dem_path=dem_path,
                               only_selected=self.chkSel.isChecked(),
                               only_fill=self.chkFill.isChecked(),
                               progress=self._set_progress)
        except TerrainError as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, TITLE, str(e))
        except Exception:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, TITLE, "Uventet fejl:\n\n" + traceback.format_exc())
        else:
            QApplication.restoreOverrideCursor()
        finally:
            self.btnRun.setEnabled(True)

        if info is None:
            self._set_progress(0, "")
            return

        if self.chkLoad.isChecked():
            name = os.path.splitext(os.path.basename(out))[0]
            rl = QgsRasterLayer(out, name)
            if rl.isValid():
                QgsProject.instance().addMapLayer(rl)

        self._set_progress(100, "Færdig.")
        QMessageBox.information(
            self, TITLE,
            "Terrænet er lukket mellem de 2 banklines.\n\n"
            f"Celler beregnet: {info['cells']}\n"
            f"Cellestørrelse: {info['px']:g} x {info['py']:g}\n"
            f"Aflæste punkter langs banklines (Band 1): {info['bank_n']}\n"
            f"Koter langs banklines min/max: {info['bank_min']:.3f} / {info['bank_max']:.3f}\n"
            f"Nyt terræn min/max: {info['zmin']:.3f} / {info['zmax']:.3f}\n\n"
            f"Gemt som:\n{out}")
