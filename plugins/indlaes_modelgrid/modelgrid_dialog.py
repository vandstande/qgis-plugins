# -*- coding: utf-8 -*-
"""Samlet dialog til Indlæs modelgrid med 2 faner.

Fane 1: Indlæs modelgrid (vælg gridfil og resultatfil, og kør)
Fane 2: Symbologi og terræn (vælg QML-symbologifil og terrænlag)

Valgene på fane 2 gemmes i QgsSettings, så de huskes efter QGIS lukkes.
"""
import os

from qgis.core import QgsProject, QgsRasterLayer, QgsSettings
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

SETTINGS_QML_KEY = "IndlaesModelgrid/qml_path"
SETTINGS_TERRAIN_LAYER_KEY = "IndlaesModelgrid/terraen_lag_navn"
SETTINGS_GRID_DIR_KEY = "IndlaesModelgrid/sidste_grid_mappe"

FANE_INDLAES = 0
FANE_SYMBOLOGI = 1


class ModelgridDialog(QDialog):
    def __init__(self, plugin_dir, run_callback, parent=None):
        """run_callback(grid_path, output_path, qml_path, terrain_layer) -> bool"""
        super().__init__(parent)
        self.setWindowTitle("Indlæs modelgrid")
        self.setMinimumWidth(560)
        self.settings = QgsSettings()
        self.run_callback = run_callback
        self._output_auto = True  # resultatnavn foreslås, indtil brugeren selv ændrer det

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_indlaes_tab(), QIcon(os.path.join(plugin_dir, "icon2.png")),
                         "Indlæs modelgrid")
        self.tabs.addTab(self._build_symbologi_tab(), QIcon(os.path.join(plugin_dir, "icon1.png")),
                         "Symbologi og terræn")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs)

        bund = QHBoxLayout()
        bund.addStretch(1)
        luk = QPushButton("Luk")
        luk.clicked.connect(self.reject)
        bund.addWidget(luk)
        layout.addLayout(bund)

        self._load_saved_values()
        self._update_summary()
        # Første gang (intet terrænlag valgt) åbnes fane 2 direkte
        if not self.settings.value(SETTINGS_TERRAIN_LAYER_KEY, "", type=str):
            self.tabs.setCurrentIndex(FANE_SYMBOLOGI)

    # ------------------------------------------------------------ fane 1
    def _build_indlaes_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        intro = QLabel("Vælg en modelgrid-fil (*.grd). Pluginet beregner terræn minus grid, "
                       "tematiserer resultatet med den valgte symbologi og lægger det i Layers.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        form = QFormLayout()
        self.lbl_summary = QLabel()
        self.lbl_summary.setWordWrap(True)
        form.addRow("Symbologi og terræn:", self.lbl_summary)

        self.grid_edit = QLineEdit()
        self.grid_edit.setPlaceholderText("Vælg en modelgrid-fil...")
        self.grid_edit.textChanged.connect(self._on_grid_changed)
        grid_btn = QPushButton("Gennemse...")
        grid_btn.clicked.connect(self._browse_grid)
        row = QHBoxLayout(); row.addWidget(self.grid_edit); row.addWidget(grid_btn)
        form.addRow("Modelgrid-fil:", row)

        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("Navn og placering for resultatfilen (*.tif)")
        self.out_edit.textEdited.connect(self._on_output_edited)
        out_btn = QPushButton("Gennemse...")
        out_btn.clicked.connect(self._browse_output)
        row2 = QHBoxLayout(); row2.addWidget(self.out_edit); row2.addWidget(out_btn)
        form.addRow("Resultatfil:", row2)
        lay.addLayout(form)

        lay.addStretch(1)
        knap = QHBoxLayout()
        knap.addStretch(1)
        self.run_btn = QPushButton("Indlæs modelgrid")
        self.run_btn.setDefault(True)
        self.run_btn.clicked.connect(self._run)
        knap.addWidget(self.run_btn)
        lay.addLayout(knap)
        return w

    # ------------------------------------------------------------ fane 2
    def _build_symbologi_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        intro = QLabel("Vælg symbologifilen, som resultatet tematiseres med, og terrænlaget, "
                       "der trækkes fra. Valgene huskes, også når QGIS lukkes.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        form = QFormLayout()
        self.qml_edit = QLineEdit()
        self.qml_edit.setPlaceholderText("Vælg en .qml symbologifil...")
        qml_btn = QPushButton("Gennemse...")
        qml_btn.clicked.connect(self._browse_qml)
        row = QHBoxLayout(); row.addWidget(self.qml_edit); row.addWidget(qml_btn)
        form.addRow("Symbologifil (.qml):", row)

        self.layer_combo = QComboBox()
        refresh = QPushButton("Opdater liste")
        refresh.clicked.connect(self._populate_raster_layers)
        row2 = QHBoxLayout(); row2.addWidget(self.layer_combo, 1); row2.addWidget(refresh)
        form.addRow("Terrænlag:", row2)
        lay.addLayout(form)
        self._populate_raster_layers()

        lay.addStretch(1)
        knap = QHBoxLayout()
        knap.addStretch(1)
        gem = QPushButton("Gem valg")
        gem.clicked.connect(self._save_and_go_back)
        knap.addWidget(gem)
        lay.addLayout(knap)
        return w

    def _populate_raster_layers(self):
        current = self.layer_combo.currentText()
        self.layer_combo.clear()
        layers = [l for l in QgsProject.instance().mapLayers().values() if isinstance(l, QgsRasterLayer)]
        layers.sort(key=lambda l: l.name().lower())
        for lyr in layers:
            self.layer_combo.addItem(lyr.name(), lyr.id())
        wanted = current or self.settings.value(SETTINGS_TERRAIN_LAYER_KEY, "", type=str)
        idx = self.layer_combo.findText(wanted) if wanted else -1
        if idx >= 0:
            self.layer_combo.setCurrentIndex(idx)

    # ------------------------------------------------------------ gemte værdier
    def _load_saved_values(self):
        qml = self.settings.value(SETTINGS_QML_KEY, "", type=str)
        if qml:
            self.qml_edit.setText(qml)

    def _validate_symbology(self, vis_fejl=True):
        """Returnér (qml_path, terrain_layer) eller None, hvis valget ikke er gyldigt."""
        qml = self.qml_edit.text().strip()
        if qml and not os.path.isfile(qml):
            if vis_fejl:
                self.tabs.setCurrentIndex(FANE_SYMBOLOGI)
                QMessageBox.warning(self, "Ugyldig fil", "Den valgte symbologifil kunne ikke findes:\n" + qml)
            return None
        layer = QgsProject.instance().mapLayer(self.layer_combo.currentData()) if self.layer_combo.count() else None
        if layer is None:
            if vis_fejl:
                self.tabs.setCurrentIndex(FANE_SYMBOLOGI)
                QMessageBox.warning(self, "Intet terrænlag",
                                    "Vælg et terrænlag på fanen 'Symbologi og terræn'.\n\n"
                                    "Der skal være mindst ét rasterlag i projektet.")
            return None
        return qml, layer

    def _save(self):
        valg = self._validate_symbology()
        if valg is None:
            return None
        qml, layer = valg
        self.settings.setValue(SETTINGS_QML_KEY, qml)
        self.settings.setValue(SETTINGS_TERRAIN_LAYER_KEY, layer.name())
        self._update_summary()
        return valg

    def _save_and_go_back(self):
        if self._save() is not None:
            self.tabs.setCurrentIndex(FANE_INDLAES)

    def _update_summary(self):
        qml = self.qml_edit.text().strip()
        terr = self.layer_combo.currentText() or "(intet valgt)"
        qml_txt = os.path.basename(qml) if qml else "(ingen symbologi)"
        self.lbl_summary.setText("{}  ·  terræn: {}".format(qml_txt, terr))

    def _on_tab_changed(self, idx):
        if idx == FANE_INDLAES:
            self._update_summary()

    # ------------------------------------------------------------ filvalg
    def _browse_qml(self):
        cur = self.qml_edit.text().strip()
        start = os.path.dirname(cur) if cur and os.path.isdir(os.path.dirname(cur)) else ""
        path, _ = QFileDialog.getOpenFileName(self, "Vælg symbologifil", start,
                                              "QGIS symbologifiler (*.qml);;Alle filer (*.*)")
        if path:
            self.qml_edit.setText(path)

    def _browse_grid(self):
        start = self.settings.value(SETTINGS_GRID_DIR_KEY, "", type=str)
        path, _ = QFileDialog.getOpenFileName(self, "Vælg modelgrid-fil", start,
                                              "Grid-filer (*.grd);;Alle filer (*.*)")
        if path:
            self.grid_edit.setText(path)

    def _on_grid_changed(self, path):
        if self._output_auto and path:
            navn = "terraen_minus_modelgrid_{}.tif".format(os.path.splitext(os.path.basename(path))[0])
            self.out_edit.setText(os.path.join(os.path.dirname(path), navn))

    def _on_output_edited(self, _):
        self._output_auto = False

    def _browse_output(self):
        path, _ = QFileDialog.getSaveFileName(self, "Vælg navn og placering for resultatfilen",
                                              self.out_edit.text().strip(), "GeoTIFF (*.tif)")
        if path:
            self.out_edit.setText(path)
            self._output_auto = False

    # ------------------------------------------------------------ kør
    def _run(self):
        valg = self._save()
        if valg is None:
            return
        qml, layer = valg
        grid = self.grid_edit.text().strip()
        if not grid or not os.path.isfile(grid):
            QMessageBox.warning(self, "Modelgrid-fil", "Vælg en modelgrid-fil, der findes.")
            return
        out = self.out_edit.text().strip()
        if not out:
            QMessageBox.warning(self, "Resultatfil", "Vælg navn og placering for resultatfilen.")
            return
        if not out.lower().endswith(".tif"):
            out += ".tif"
        self.settings.setValue(SETTINGS_GRID_DIR_KEY, os.path.dirname(grid))
        self.run_btn.setEnabled(False)
        try:
            ok = self.run_callback(grid, out, qml, layer)
        finally:
            self.run_btn.setEnabled(True)
        if ok:
            self.accept()
