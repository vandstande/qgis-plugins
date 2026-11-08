# -*- coding: utf-8 -*-
import os

from qgis.core import QgsProject, QgsRasterLayer, QgsSettings
from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QPushButton,
    QDialogButtonBox,
    QFileDialog,
    QMessageBox,
)

SETTINGS_QML_KEY = "IndlaesModelgrid/qml_path"
SETTINGS_TERRAIN_LAYER_KEY = "IndlaesModelgrid/terraen_lag_navn"


class SymbologyDialog(QDialog):
    """Dialog til at vælge/ændre QML-symbologifil og terrænlag.

    Valgene gemmes i QgsSettings, så de huskes efter QGIS lukkes ned.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tilknyt symbologi")
        self.setMinimumWidth(420)

        self.settings = QgsSettings()

        self._build_ui()
        self._load_saved_values()

    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()

        # QML-fil valg
        qml_row = QHBoxLayout()
        self.qml_line_edit = QLineEdit()
        self.qml_line_edit.setPlaceholderText("Vælg en .qml symbologifil...")
        self.browse_button = QPushButton("Gennemse...")
        self.browse_button.clicked.connect(self._browse_qml)
        qml_row.addWidget(self.qml_line_edit)
        qml_row.addWidget(self.browse_button)
        form.addRow(QLabel("Symbologifil (.qml):"), qml_row)

        # Terrænlag valg
        self.layer_combo = QComboBox()
        self._populate_raster_layers()
        form.addRow(QLabel("Terrænlag:"), self.layer_combo)

        layout.addLayout(form)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _populate_raster_layers(self):
        self.layer_combo.clear()
        layers = QgsProject.instance().mapLayers().values()
        raster_layers = [lyr for lyr in layers if isinstance(lyr, QgsRasterLayer)]
        raster_layers.sort(key=lambda lyr: lyr.name().lower())
        for lyr in raster_layers:
            self.layer_combo.addItem(lyr.name(), lyr.id())

    # ------------------------------------------------------------------
    def _load_saved_values(self):
        qml_path = self.settings.value(SETTINGS_QML_KEY, "", type=str)
        if qml_path:
            self.qml_line_edit.setText(qml_path)

        terrain_name = self.settings.value(SETTINGS_TERRAIN_LAYER_KEY, "", type=str)
        if terrain_name:
            idx = self.layer_combo.findText(terrain_name)
            if idx >= 0:
                self.layer_combo.setCurrentIndex(idx)

    # ------------------------------------------------------------------
    def _browse_qml(self):
        start_dir = ""
        current = self.qml_line_edit.text().strip()
        if current and os.path.isdir(os.path.dirname(current)):
            start_dir = os.path.dirname(current)

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Vælg symbologifil",
            start_dir,
            "QGIS symbologifiler (*.qml);;Alle filer (*.*)",
        )
        if path:
            self.qml_line_edit.setText(path)

    # ------------------------------------------------------------------
    def _on_accept(self):
        qml_path = self.qml_line_edit.text().strip()
        if qml_path and not os.path.isfile(qml_path):
            QMessageBox.warning(
                self, "Ugyldig fil", "Den valgte symbologifil kunne ikke findes."
            )
            return

        if self.layer_combo.count() == 0:
            QMessageBox.warning(
                self,
                "Intet terrænlag",
                "Der er ingen rasterlag i projektet at vælge som terrænlag.",
            )
            return

        # Gem valg i QgsSettings, så det huskes mellem QGIS-sessioner
        self.settings.setValue(SETTINGS_QML_KEY, qml_path)
        self.settings.setValue(
            SETTINGS_TERRAIN_LAYER_KEY, self.layer_combo.currentText()
        )

        self.accept()
