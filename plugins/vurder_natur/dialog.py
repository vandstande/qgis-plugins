# -*- coding: utf-8 -*-
from qgis.PyQt.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QVBoxLayout,
)
from qgis.core import QgsMapLayerProxyModel, QgsSettings
from qgis.gui import QgsFieldComboBox, QgsFileWidget, QgsMapLayerComboBox

try:  # QGIS >= 3.34
    from qgis.core import Qgis
    RASTER = Qgis.LayerFilter.RasterLayer
    POLYGON = Qgis.LayerFilter.PolygonLayer
except AttributeError:
    RASTER = QgsMapLayerProxyModel.RasterLayer
    POLYGON = QgsMapLayerProxyModel.PolygonLayer

INDSTILLING = "VurderNatur/"


class VurderNaturDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Vurder natur")
        self.setMinimumWidth(480)
        s = QgsSettings()

        lag = QGroupBox("Inddata")
        form = QFormLayout(lag)
        self.nutid = QgsMapLayerComboBox()
        self.nutid.setFilters(RASTER)
        self.fremtid = QgsMapLayerComboBox()
        self.fremtid.setFilters(RASTER)
        self.natur = QgsMapLayerComboBox()
        self.natur.setFilters(POLYGON)
        self.felt = QgsFieldComboBox()
        self.felt.setLayer(self.natur.currentLayer())
        self.natur.layerChanged.connect(self.felt.setLayer)
        self.projekt = QgsMapLayerComboBox()
        self.projekt.setFilters(POLYGON)

        form.addRow("Nutidig afvanding:", self.nutid)
        form.addRow("Fremtidig afvanding:", self.fremtid)
        form.addRow("Naturbeskyttelse:", self.natur)
        form.addRow("Felt med naturtypekode:", self.felt)
        form.addRow("Projektgrænse:", self.projekt)

        # Genvalg af sidst brugte felt
        sidste_felt = s.value(INDSTILLING + "felt", "")
        if sidste_felt:
            self.felt.setField(sidste_felt)

        typer = QGroupBox("Naturtyper der vurderes")
        th = QHBoxLayout(typer)
        self.cb_soe = QCheckBox("Sø (6)")
        self.cb_mose = QCheckBox("Mose (3)")
        self.cb_eng = QCheckBox("Eng (1)")
        for cb, noegle in ((self.cb_soe, "soe"), (self.cb_mose, "mose"), (self.cb_eng, "eng")):
            cb.setChecked(s.value(INDSTILLING + noegle, True, type=bool))
            th.addWidget(cb)
        th.addStretch()

        ud = QGroupBox("Resultat")
        uv = QVBoxLayout(ud)
        self.fil = QgsFileWidget()
        self.fil.setStorageMode(QgsFileWidget.SaveFile)
        self.fil.setFilter("Excel (*.xlsx)")
        self.fil.setFilePath(s.value(INDSTILLING + "fil", ""))
        self.aabn = QCheckBox("Åbn filen når beregningen er færdig")
        self.aabn.setChecked(s.value(INDSTILLING + "aabn", True, type=bool))
        uv.addWidget(QLabel("Gem tabel som:"))
        uv.addWidget(self.fil)
        uv.addWidget(self.aabn)

        knapper = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        knapper.button(QDialogButtonBox.Ok).setText("Kør vurdering")
        knapper.accepted.connect(self.accept)
        knapper.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        for w in (lag, typer, ud):
            layout.addWidget(w)
        layout.addWidget(knapper)

    def valgte_typer(self):
        t = set()
        if self.cb_soe.isChecked():
            t.add(6)
        if self.cb_mose.isChecked():
            t.add(3)
        if self.cb_eng.isChecked():
            t.add(1)
        return t

    def gem_indstillinger(self):
        s = QgsSettings()
        s.setValue(INDSTILLING + "felt", self.felt.currentField())
        s.setValue(INDSTILLING + "soe", self.cb_soe.isChecked())
        s.setValue(INDSTILLING + "mose", self.cb_mose.isChecked())
        s.setValue(INDSTILLING + "eng", self.cb_eng.isChecked())
        s.setValue(INDSTILLING + "fil", self.fil.filePath())
        s.setValue(INDSTILLING + "aabn", self.aabn.isChecked())
