# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QGroupBox, QCheckBox, QDoubleSpinBox,
    QDialogButtonBox, QProgressBar, QPlainTextEdit, QLabel, QApplication,
    QPushButton)
from qgis.core import Qgis, QgsMapLayerProxyModel
from qgis.gui import QgsMapLayerComboBox, QgsFileWidget

from .runner import run_projektgraense

SETTINGS = "screening_projektgraense/"


def _filter(name):
    lf = getattr(Qgis, "LayerFilter", None)
    if lf is not None and hasattr(lf, name):
        return getattr(lf, name)
    return getattr(QgsMapLayerProxyModel, name)


def _spin(value, lo, hi, dec, suffix, step=None):
    sp = QDoubleSpinBox()
    sp.setRange(lo, hi)
    sp.setDecimals(dec)
    sp.setSuffix(suffix)
    if step:
        sp.setSingleStep(step)
    sp.setValue(value)
    return sp


class ProjektgraenseDialog(QDialog):
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.setWindowTitle("Screening af projektgrænse")
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)

        # --- input --------------------------------------------------------
        g_in = QGroupBox("Input")
        f = QFormLayout(g_in)
        self.cb_line = QgsMapLayerComboBox()
        self.cb_line.setFilters(_filter("LineLayer"))
        self.chk_sel = QCheckBox("Kun valgte linjer")
        self.cb_dem = QgsMapLayerComboBox()
        self.cb_dem.setFilters(_filter("RasterLayer"))
        self.sp_depth = _spin(1.5, 0.1, 10.0, 2, " m", 0.1)
        f.addRow("Linje i bunden af vådområdet\n(tegnet i strømretningen):",
                 self.cb_line)
        f.addRow("", self.chk_sel)
        f.addRow("Terrænmodel:", self.cb_dem)
        f.addRow("Dybde (grænse over bund):", self.sp_depth)
        lay.addWidget(g_in)

        # --- grøftetjek ---------------------------------------------------
        g_d = QGroupBox("Tjek for grøft/vandløb under linjen")
        f = QFormLayout(g_d)
        self.sp_ddist = _spin(3.0, 0.5, 50.0, 1, " m", 0.5)
        self.sp_dtol = _spin(0.3, 0.05, 5.0, 2, " m", 0.05)
        self.chk_dcorr = QCheckBox("Brug terrænet ved siden af grøften som bund")
        self.chk_dcorr.setChecked(True)
        f.addRow("Afstand til siderne:", self.sp_ddist)
        f.addRow("Grøft hvis begge sider er mere end:", self.sp_dtol)
        f.addRow("", self.chk_dcorr)
        lay.addWidget(g_d)

        # --- avanceret ----------------------------------------------------
        g_a = QGroupBox("Beregning")
        f = QFormLayout(g_a)
        self.sp_spacing = _spin(5.0, 0.5, 200.0, 1, " m", 1.0)
        self.sp_maxdist = _spin(300.0, 10.0, 5000.0, 0, " m", 50.0)
        self.sp_upmax = _spin(500.0, 0.0, 5000.0, 0, " m", 10.0)
        self.sp_res = _spin(1.0, 0.1, 50.0, 2, " m", 0.5)
        self.sp_smooth = _spin(25.0, 0.0, 1000.0, 0, " m", 5.0)
        self.chk_mono = QCheckBox("Grænsekoten må ikke stige nedstrøms")
        f.addRow("Afstand mellem tværsnit:", self.sp_spacing)
        f.addRow("Maks. bredde til hver side:", self.sp_maxdist)
        f.addRow("Maks. afstand opstrøms:", self.sp_upmax)
        f.addRow("Beregningsopløsning:", self.sp_res)
        f.addRow("Udglatning af bundkote (median):", self.sp_smooth)
        f.addRow("", self.chk_mono)
        lay.addWidget(g_a)

        # --- output -------------------------------------------------------
        g_o = QGroupBox("Output")
        f = QFormLayout(g_o)
        self.fw_out = QgsFileWidget()
        self.fw_out.setStorageMode(QgsFileWidget.StorageMode.SaveFile
                                   if hasattr(QgsFileWidget, "StorageMode")
                                   else QgsFileWidget.SaveFile)
        self.fw_out.setFilter("GeoPackage (*.gpkg)")
        self.chk_ctrl = QCheckBox("Tilføj kontrolpunkter og tværsnit")
        self.chk_ctrl.setChecked(True)
        f.addRow("GeoPackage (tom = midlertidige lag):", self.fw_out)
        f.addRow("", self.chk_ctrl)
        lay.addWidget(g_o)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        lay.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(130)
        lay.addWidget(self.log)

        bb = QDialogButtonBox()
        self.btn_run = QPushButton("Beregn")
        bb.addButton(self.btn_run, QDialogButtonBox.ButtonRole.AcceptRole
                     if hasattr(QDialogButtonBox, "ButtonRole")
                     else QDialogButtonBox.AcceptRole)
        btn_close = bb.addButton(
            QDialogButtonBox.StandardButton.Close
            if hasattr(QDialogButtonBox, "StandardButton")
            else QDialogButtonBox.Close)
        self.btn_run.clicked.connect(self.on_run)
        btn_close.clicked.connect(self.close)
        lay.addWidget(bb)

        self._load_settings()

    # ------------------------------------------------------------------
    def refresh(self):
        self.progress.setValue(0)

    def _widgets(self):
        return dict(depth=self.sp_depth, ditch_dist=self.sp_ddist,
                    ditch_tol=self.sp_dtol, spacing=self.sp_spacing,
                    maxdist=self.sp_maxdist, upstream_max=self.sp_upmax,
                    res=self.sp_res,
                    smooth_window=self.sp_smooth)

    def _load_settings(self):
        s = QSettings()
        # Standarden opstrøms er hævet fra 100 til 500 m. En gammel, gemt
        # værdi bruges derfor ikke, før dialogen er gemt med den nye standard.
        ny_opstroems = str(s.value(SETTINGS + "upstream_max_v500", "")).lower() in ("true", "1")
        for k, w in self._widgets().items():
            if k == "upstream_max" and not ny_opstroems:
                continue
            v = s.value(SETTINGS + k)
            if v is not None:
                try:
                    w.setValue(float(v))
                except (TypeError, ValueError):
                    pass
        for k, w in (("ditch_correct", self.chk_dcorr),
                     ("monotone", self.chk_mono),
                     ("ctrl", self.chk_ctrl)):
            v = s.value(SETTINGS + k)
            if v is not None:
                w.setChecked(str(v).lower() in ("true", "1"))

    def _save_settings(self):
        s = QSettings()
        for k, w in self._widgets().items():
            s.setValue(SETTINGS + k, w.value())
        s.setValue(SETTINGS + "ditch_correct", self.chk_dcorr.isChecked())
        s.setValue(SETTINGS + "monotone", self.chk_mono.isChecked())
        s.setValue(SETTINGS + "ctrl", self.chk_ctrl.isChecked())
        s.setValue(SETTINGS + "upstream_max_v500", True)

    def _log(self, txt):
        self.log.appendPlainText(txt)
        QApplication.processEvents()

    def _progress(self, pct):
        self.progress.setValue(int(pct))
        QApplication.processEvents()

    def on_run(self):
        line = self.cb_line.currentLayer()
        dem = self.cb_dem.currentLayer()
        self.log.clear()
        if line is None or dem is None:
            self._log("Vælg både linjelag og terrænmodel.")
            return
        self._save_settings()
        params = {k: w.value() for k, w in self._widgets().items()}
        params.update(ditch_correct=self.chk_dcorr.isChecked(),
                      monotone=self.chk_mono.isChecked())
        self.btn_run.setEnabled(False)
        try:
            run_projektgraense(
                line, dem, params,
                selected_only=self.chk_sel.isChecked(),
                gpkg=self.fw_out.filePath().strip(),
                add_control=self.chk_ctrl.isChecked(),
                log=self._log, progress=self._progress)
        except Exception as e:  # vis fejlen i dialogen
            import traceback
            self._log("FEJL: %s" % e)
            self._log(traceback.format_exc())
        finally:
            self.btn_run.setEnabled(True)
