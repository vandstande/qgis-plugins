# -*- coding: utf-8 -*-
from qgis.PyQt.QtWidgets import (
    QDialog,
    QFormLayout,
    QVBoxLayout,
    QLabel,
    QDoubleSpinBox,
    QDialogButtonBox,
)
from qgis.gui import QgsMapLayerComboBox
from qgis.core import QgsMapLayerProxyModel


class HecrasTilTimFlowDialog(QDialog):
    """Dialog hvor brugeren vaelger de tre lag samt punktafstand."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("HECRAS til TimFlow")
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        # HECRAS resultatfil - raster (tiff) lag
        self.raster_combo = QgsMapLayerComboBox(self)
        self.raster_combo.setFilters(QgsMapLayerProxyModel.RasterLayer)
        form.addRow(QLabel("HECRAS resultatfil (raster):"), self.raster_combo)

        # HECRAS vandlob - linjelag
        self.line_combo = QgsMapLayerComboBox(self)
        self.line_combo.setFilters(QgsMapLayerProxyModel.LineLayer)
        form.addRow(QLabel("HECRAS vandlob (linjelag):"), self.line_combo)

        # TimFlow model - lag fra en geopackage
        self.gpkg_combo = QgsMapLayerComboBox(self)
        self.gpkg_combo.setFilters(QgsMapLayerProxyModel.VectorLayer)
        form.addRow(
            QLabel("TimFlow model (vaelg et lag fra geopackagen):"),
            self.gpkg_combo,
        )

        # Punktafstand
        self.spacing_spin = QDoubleSpinBox(self)
        self.spacing_spin.setDecimals(3)
        self.spacing_spin.setMinimum(0.001)
        self.spacing_spin.setMaximum(1000000.0)
        self.spacing_spin.setSingleStep(1.0)
        self.spacing_spin.setValue(10.0)
        self.spacing_spin.setSuffix(" m")
        form.addRow(QLabel("Punktafstand:"), self.spacing_spin)

        layout.addLayout(form)

        info = QLabel(
            "Der oprettes et punktlag i TimFlow-modellens geopackage for "
            "hver linje i HECRAS vandlobslaget. Punktlagets navn dannes ud "
            "fra felterne 'River' og 'Reach', hvis de findes - ellers "
            "genereres et tilfaeldigt navn. Feltet 'kote' udfyldes med "
            "vaerdien fra HECRAS resultatfilen (-999 hvis der ikke er data)."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def raster_layer(self):
        return self.raster_combo.currentLayer()

    def line_layer(self):
        return self.line_combo.currentLayer()

    def gpkg_layer(self):
        return self.gpkg_combo.currentLayer()

    def spacing(self):
        return self.spacing_spin.value()
