import os

from qgis.core import (
    QgsMapLayerProxyModel,
    QgsProcessingFeedback,
    QgsProject,
    QgsSettings,
)
from qgis.gui import QgsFieldComboBox, QgsFileWidget, QgsMapLayerComboBox
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from .beregning import BeregningsFejl, FosforBeregning, gem_som_xlsx
from .compat import LAG_POLYGON, LAG_RASTER, filwidget_tilstand

SETTINGS_KEY = "FosforBeregning/"


class _Feedback(QgsProcessingFeedback):
    def __init__(self, progress_bar):
        super().__init__()
        self._bar = progress_bar
        self.progressChanged.connect(self._update)

    def _update(self, value):
        self._bar.setValue(int(value))
        QApplication.processEvents()


class FosforDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.feedback = None
        self.setWindowTitle("Fosfor beregning")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Beregner parametre for hvert jordprøvefelt, så de kan kopieres direkte "
            "ind i Fosfor-regnearket.<br><b>Husk:</b> jordprøvefelterne skal dække hele "
            "projektområdet og have et ID-felt med samme værdi som jordprøven. "
            "Dræningsintensiteten skal stadig vurderes manuelt.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # --- Input ---------------------------------------------------
        box_in = QGroupBox("Input")
        form = QFormLayout(box_in)

        self.cb_projekt = QgsMapLayerComboBox()
        self.cb_projekt.setFilters(LAG_POLYGON)
        form.addRow("Projektgrænse", self.cb_projekt)

        self.cb_felter = QgsMapLayerComboBox()
        self.cb_felter.setFilters(LAG_POLYGON)
        form.addRow("Jordprøvefelter", self.cb_felter)

        self.cb_id = QgsFieldComboBox()
        form.addRow("    ID-felt", self.cb_id)

        self.cb_afvanding = QgsMapLayerComboBox()
        self.cb_afvanding.setFilters(LAG_RASTER)
        form.addRow("Fremtidsmodel (afvanding)", self.cb_afvanding)

        self.cb_jb = QgsMapLayerComboBox()
        self.cb_jb.setFilters(LAG_POLYGON)
        form.addRow("Jordbundskort", self.cb_jb)

        self.cb_jordtype = QgsFieldComboBox()
        form.addRow("    Jordtype-felt", self.cb_jordtype)

        layout.addWidget(box_in)

        self.cb_felter.layerChanged.connect(self._felter_changed)
        self.cb_jb.layerChanged.connect(self._jb_changed)

        # --- Output --------------------------------------------------
        box_out = QGroupBox("Output")
        form_out = QFormLayout(box_out)
        self.file_out = QgsFileWidget()
        self.file_out.setStorageMode(filwidget_tilstand("SaveFile"))
        self.file_out.setFilter("Excel (*.xlsx)")
        self.file_out.setDialogTitle("Gem resultat som Excel")
        form_out.addRow("Resultat (xlsx)", self.file_out)
        self.chk_layer = QCheckBox("Tilføj resultatlaget til projektet")
        self.chk_open = QCheckBox("Åbn regnearket når beregningen er færdig")
        form_out.addRow(self.chk_layer)
        form_out.addRow(self.chk_open)
        layout.addWidget(box_out)

        # --- Fremdrift ----------------------------------------------
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(140)
        layout.addWidget(self.log_view)

        buttons = QDialogButtonBox()
        self.btn_run = QPushButton("Kør")
        self.btn_run.setDefault(True)
        self.btn_cancel = QPushButton("Afbryd")
        self.btn_cancel.setEnabled(False)
        self.btn_close = QPushButton("Luk")
        buttons.addButton(self.btn_run, QDialogButtonBox.AcceptRole)
        buttons.addButton(self.btn_cancel, QDialogButtonBox.ActionRole)
        buttons.addButton(self.btn_close, QDialogButtonBox.RejectRole)
        self.btn_run.clicked.connect(self.run)
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_close.clicked.connect(self.close)
        layout.addWidget(buttons)

        self._load_settings()

    # ------------------------------------------------------------------
    def _felter_changed(self, layer):
        self.cb_id.setLayer(layer)
        self._select_field(self.cb_id, layer, ("Id", "ID", "id"))

    def _jb_changed(self, layer):
        self.cb_jordtype.setLayer(layer)
        self._select_field(self.cb_jordtype, layer, ("Jordtype", "jordtype", "JORDTYPE"))

    @staticmethod
    def _select_field(combo, layer, candidates):
        if layer is None:
            return
        names = [f.name() for f in layer.fields()]
        for c in candidates:
            if c in names:
                combo.setField(c)
                return
        lower = {n.lower(): n for n in names}
        for c in candidates:
            if c.lower() in lower:
                combo.setField(lower[c.lower()])
                return

    @staticmethod
    def _set_layer_by_name(combo, name):
        if name:
            for lyr in QgsProject.instance().mapLayersByName(name):
                combo.setLayer(lyr)
                if combo.currentLayer() == lyr:
                    return True
        return False

    @staticmethod
    def _guess(combo, keywords):
        for lyr in QgsProject.instance().mapLayers().values():
            if any(k in lyr.name().lower() for k in keywords):
                combo.setLayer(lyr)
                if combo.currentLayer() == lyr:
                    return True
        return False

    def refresh_defaults(self):
        """Kaldes hver gang dialogen åbnes: sidst brugte lag, ellers gæt ud fra lagnavn."""
        s = QgsSettings()
        for combo, key, keywords in (
                (self.cb_projekt, "projekt", ("projektgr",)),
                (self.cb_felter, "felter", ("jordprøve", "jordprove", "jordprv", "prøvefelt")),
                (self.cb_afvanding, "afvanding", ("fremtid", "afvanding")),
                (self.cb_jb, "jordbund", ("jordbund", "aggregated"))):
            if not self._set_layer_by_name(combo, s.value(SETTINGS_KEY + key, "")):
                self._guess(combo, keywords)
        self._felter_changed(self.cb_felter.currentLayer())
        self._jb_changed(self.cb_jb.currentLayer())
        id_felt = s.value(SETTINGS_KEY + "id_felt", "")
        if id_felt and self.cb_felter.currentLayer() and \
                self.cb_felter.currentLayer().fields().indexOf(id_felt) >= 0:
            self.cb_id.setField(id_felt)
        jt = s.value(SETTINGS_KEY + "jordtype_felt", "")
        if jt and self.cb_jb.currentLayer() and \
                self.cb_jb.currentLayer().fields().indexOf(jt) >= 0:
            self.cb_jordtype.setField(jt)

    def _load_settings(self):
        s = QgsSettings()
        self.chk_layer.setChecked(s.value(SETTINGS_KEY + "add_layer", True, type=bool))
        self.chk_open.setChecked(s.value(SETTINGS_KEY + "open_xlsx", True, type=bool))
        last = s.value(SETTINGS_KEY + "output_dir", "")
        if last:
            self.file_out.setDefaultRoot(last)

    def _save_settings(self):
        s = QgsSettings()
        for combo, key in ((self.cb_projekt, "projekt"), (self.cb_felter, "felter"),
                           (self.cb_afvanding, "afvanding"), (self.cb_jb, "jordbund")):
            if combo.currentLayer():
                s.setValue(SETTINGS_KEY + key, combo.currentLayer().name())
        s.setValue(SETTINGS_KEY + "id_felt", self.cb_id.currentField())
        s.setValue(SETTINGS_KEY + "jordtype_felt", self.cb_jordtype.currentField())
        s.setValue(SETTINGS_KEY + "add_layer", self.chk_layer.isChecked())
        s.setValue(SETTINGS_KEY + "open_xlsx", self.chk_open.isChecked())
        path = self.file_out.filePath()
        if path:
            s.setValue(SETTINGS_KEY + "output_dir", os.path.dirname(path))

    # ------------------------------------------------------------------
    def log(self, msg):
        self.log_view.append(msg)
        QApplication.processEvents()

    def _cancel(self):
        if self.feedback:
            self.feedback.cancel()

    def _set_running(self, running):
        for w in (self.btn_run, self.btn_close, self.cb_projekt, self.cb_felter, self.cb_id,
                  self.cb_afvanding, self.cb_jb, self.cb_jordtype, self.file_out):
            w.setEnabled(not running)
        self.btn_cancel.setEnabled(running)

    def run(self):
        path = self.file_out.filePath().strip()
        if not path:
            QMessageBox.warning(self, "Fosfor beregning",
                                "Vælg hvor resultatet skal gemmes (xlsx).")
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"

        self.log_view.clear()
        self.progress.setValue(0)
        self.feedback = _Feedback(self.progress)
        self._set_running(True)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            beregning = FosforBeregning(
                projektgraense=self.cb_projekt.currentLayer(),
                proevefelter=self.cb_felter.currentLayer(),
                id_felt=self.cb_id.currentField(),
                afvanding=self.cb_afvanding.currentLayer(),
                jordbundskort=self.cb_jb.currentLayer(),
                jordtype_felt=self.cb_jordtype.currentField(),
                feedback=self.feedback,
                log=self.log,
            )
            result = beregning.run()
            path = gem_som_xlsx(result, path)
            self._save_settings()
            self.log("<b>Færdig:</b> {} jordprøvefelter gemt i {}".format(
                result.featureCount(), path))
            if beregning.warnings:
                self.log("<b>{} advarsel(er) – se ovenfor.</b>".format(len(beregning.warnings)))
            self.log("Husk at vurdere dræningsintensiteten manuelt.")

            if self.chk_layer.isChecked():
                QgsProject.instance().addMapLayer(result)
            if self.chk_open.isChecked():
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            self.iface.messageBar().pushSuccess(
                "Fosfor beregning", "Resultatet er gemt i {}".format(path))
        except BeregningsFejl as e:
            self.log("<span style='color:#c00'><b>Fejl:</b> {}</span>".format(e))
            QMessageBox.warning(self, "Fosfor beregning", str(e))
        except Exception as e:  # noqa
            import traceback
            self.log("<span style='color:#c00'><b>Uventet fejl:</b> {}</span>".format(e))
            self.log("<pre>{}</pre>".format(traceback.format_exc()))
            QMessageBox.critical(self, "Fosfor beregning", "Uventet fejl:\n{}".format(e))
        finally:
            QApplication.restoreOverrideCursor()
            self._set_running(False)
            self.feedback = None
