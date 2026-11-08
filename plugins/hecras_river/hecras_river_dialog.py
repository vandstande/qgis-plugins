# -*- coding: utf-8 -*-
"""
Dialog for HECRAS River pluginet. Brugerfladen er delt i 2 faner:

Del 1: Lav Banklines og Cross section lines
Del 2: Lav Cross section punkter og kote punkter
"""

import os

from qgis.core import QgsMapLayerProxyModel, QgsProject, QgsVectorLayer
from qgis.gui import QgsMapLayerComboBox
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QFormLayout,
    QLabel,
    QDoubleSpinBox,
    QPushButton,
    QWidget,
    QTabWidget,
    QFileDialog,
    QMessageBox,
    QCheckBox,
)

from . import del1_banklines
from . import del2_profilpunkter


DEL1_INTRO = (
    "På baggrund af en Rivercenter line dannes 2 shapefiler, "
    "en med Banklines og en med cross section lines."
)

DEL2_INTRO = (
    "På baggrund af en Rivercenter line og Cross section lines, dannes "
    "2 shapefiler, en med profilpunkter og midt punkter."
)

DEL1_DONE_MESSAGE = (
    "Nu skal du importere Rivercenterline, Banklines og Cross section "
    "lines ind i HECRAS, og efterfølgende eksportere Cross section "
    "lines, nu har de fået Riverstation."
)


class HecrasRiverDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("HECRAS River")
        self.resize(480, 420)

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs)

        self.tab1 = self._build_tab1()
        self.tab2 = self._build_tab2()

        self.tabs.addTab(self.tab1, "Del 1: Banklines og Cross section lines")
        self.tabs.addTab(self.tab2, "Del 2: Cross section punkter og kote punkter")

    # ------------------------------------------------------------------
    # Fælles: indlæs gemte shapefiler i Layers-listen
    # ------------------------------------------------------------------
    def _load_layer(self, path):
        name = os.path.splitext(os.path.basename(path))[0]
        layer = QgsVectorLayer(path, name, "ogr")
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
        else:
            QMessageBox.warning(
                self, "HECRAS River",
                f"Kunne ikke indlæse laget i Layers-listen:\n{path}"
            )

    # ------------------------------------------------------------------
    # Del 1: Lav Banklines og Cross section lines
    # ------------------------------------------------------------------
    def _build_tab1(self):
        widget = QWidget()
        outer = QVBoxLayout(widget)

        intro = QLabel(DEL1_INTRO)
        intro.setWordWrap(True)
        outer.addWidget(intro)

        form = QFormLayout()

        self.d1_centerline = QgsMapLayerComboBox()
        self.d1_centerline.setFilters(QgsMapLayerProxyModel.LineLayer)
        form.addRow("Rivercenter line:", self.d1_centerline)

        self.d1_bredde = QDoubleSpinBox()
        self.d1_bredde.setDecimals(2)
        self.d1_bredde.setRange(0.01, 100000.0)
        self.d1_bredde.setValue(10.0)
        self.d1_bredde.setSuffix(" m")
        form.addRow("Bredde på trace:", self.d1_bredde)

        self.d1_afstand = QDoubleSpinBox()
        self.d1_afstand.setDecimals(2)
        self.d1_afstand.setRange(0.01, 100000.0)
        self.d1_afstand.setValue(50.0)
        self.d1_afstand.setSuffix(" m")
        form.addRow("Afstand mellem profiler:", self.d1_afstand)

        outer.addLayout(form)

        self.d1_load_layers = QCheckBox("Indlæs lag i Layers-listen")
        self.d1_load_layers.setChecked(True)
        outer.addWidget(self.d1_load_layers)

        self.d1_status = QLabel("")
        self.d1_status.setWordWrap(True)
        outer.addWidget(self.d1_status)

        outer.addStretch(1)

        run_btn = QPushButton("Dan profiler")
        run_btn.clicked.connect(self._run_del1)
        outer.addWidget(run_btn)

        return widget

    def _run_del1(self):
        centerline_layer = self.d1_centerline.currentLayer()
        if centerline_layer is None:
            QMessageBox.warning(self, "HECRAS River", "Vælg en Rivercenter line.")
            return

        bredde = self.d1_bredde.value()
        afstand = self.d1_afstand.value()

        banklines_path, _ = QFileDialog.getSaveFileName(
            self, "Gem Banklines som", "", "Shapefiles (*.shp)"
        )
        if not banklines_path:
            return
        if not banklines_path.lower().endswith(".shp"):
            banklines_path += ".shp"

        xs_path, _ = QFileDialog.getSaveFileName(
            self, "Gem Cross section lines som", "", "Shapefiles (*.shp)"
        )
        if not xs_path:
            return
        if not xs_path.lower().endswith(".shp"):
            xs_path += ".shp"

        try:
            n_bank, n_xs = del1_banklines.run(
                centerline_layer, bredde, afstand, banklines_path, xs_path
            )
        except Exception as exc:
            QMessageBox.critical(self, "HECRAS River", f"Der opstod en fejl:\n{exc}")
            return

        self.d1_status.setText(
            f"Færdig. {n_bank} bankline-features og {n_xs} cross section lines dannet."
        )

        if self.d1_load_layers.isChecked():
            self._load_layer(banklines_path)
            self._load_layer(xs_path)

        QMessageBox.information(self, "HECRAS River", DEL1_DONE_MESSAGE)

    # ------------------------------------------------------------------
    # Del 2: Lav Cross section punkter og kote punkter
    # ------------------------------------------------------------------
    def _build_tab2(self):
        widget = QWidget()
        outer = QVBoxLayout(widget)

        intro = QLabel(DEL2_INTRO)
        intro.setWordWrap(True)
        outer.addWidget(intro)

        form = QFormLayout()

        self.d2_centerline = QgsMapLayerComboBox()
        self.d2_centerline.setFilters(QgsMapLayerProxyModel.LineLayer)
        form.addRow("Rivercenter line:", self.d2_centerline)

        self.d2_xs = QgsMapLayerComboBox()
        self.d2_xs.setFilters(QgsMapLayerProxyModel.LineLayer)
        form.addRow("Cross section lines (m. Riverstation):", self.d2_xs)

        self.d2_terrain = QgsMapLayerComboBox()
        self.d2_terrain.setFilters(QgsMapLayerProxyModel.RasterLayer)
        form.addRow("Terræn:", self.d2_terrain)

        self.d2_spacing = QDoubleSpinBox()
        self.d2_spacing.setDecimals(2)
        self.d2_spacing.setRange(0.01, 10000.0)
        self.d2_spacing.setValue(0.4)
        self.d2_spacing.setSuffix(" m")
        form.addRow("Afstand mellem profilpunkter:", self.d2_spacing)

        outer.addLayout(form)

        self.d2_load_layers = QCheckBox("Indlæs lag i Layers-listen")
        self.d2_load_layers.setChecked(True)
        outer.addWidget(self.d2_load_layers)

        self.d2_status = QLabel("")
        self.d2_status.setWordWrap(True)
        outer.addWidget(self.d2_status)

        outer.addStretch(1)

        run_btn = QPushButton("Dan profilpunkter")
        run_btn.clicked.connect(self._run_del2)
        outer.addWidget(run_btn)

        return widget

    def _run_del2(self):
        centerline_layer = self.d2_centerline.currentLayer()
        xs_layer = self.d2_xs.currentLayer()
        terrain_layer = self.d2_terrain.currentLayer()

        if centerline_layer is None or xs_layer is None or terrain_layer is None:
            QMessageBox.warning(
                self, "HECRAS River",
                "Vælg Rivercenter line, Cross section lines og Terræn."
            )
            return

        spacing = self.d2_spacing.value()

        profil_path, _ = QFileDialog.getSaveFileName(
            self, "Gem profilpunkter som", "", "Shapefiles (*.shp)"
        )
        if not profil_path:
            return
        if not profil_path.lower().endswith(".shp"):
            profil_path += ".shp"

        midt_path, _ = QFileDialog.getSaveFileName(
            self, "Gem midtpunkter som", "", "Shapefiles (*.shp)"
        )
        if not midt_path:
            return
        if not midt_path.lower().endswith(".shp"):
            midt_path += ".shp"

        try:
            n_profil, n_midt = del2_profilpunkter.run(
                centerline_layer, xs_layer, terrain_layer, spacing,
                profil_path, midt_path
            )
        except Exception as exc:
            QMessageBox.critical(self, "HECRAS River", f"Der opstod en fejl:\n{exc}")
            return

        self.d2_status.setText(
            f"Færdig. {n_profil} profilpunkter og {n_midt} midtpunkter dannet."
        )

        if self.d2_load_layers.isChecked():
            self._load_layer(profil_path)
            self._load_layer(midt_path)
