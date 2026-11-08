# -*- coding: utf-8 -*-
"""Dialog til valg af lag og parametre for "Grav grøfter"."""

from qgis.PyQt.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QLabel, QSpinBox, QVBoxLayout,
)
from qgis.gui import QgsFieldComboBox, QgsMapLayerComboBox
from qgis.core import QgsMapLayerProxyModel, QgsFieldProxyModel

try:
    from qgis.core import Qgis
except ImportError:  # pragma: no cover
    Qgis = None


def _enum(cls, scope, navn):
    """Henter en enum-værdi så det virker i både Qt5/QGIS 3 og Qt6/QGIS 4."""
    s = getattr(cls, scope, None)
    if s is not None and hasattr(s, navn):
        return getattr(s, navn)
    return getattr(cls, navn)


def _lagfilter(navn):
    if Qgis is not None and hasattr(Qgis, "LayerFilter") and hasattr(Qgis.LayerFilter, navn):
        return getattr(Qgis.LayerFilter, navn)
    return _enum(QgsMapLayerProxyModel, "Filter", navn)


class GravGroefterDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Grav grøfter – beregn jordmængde")
        self.setMinimumWidth(460)

        hoved = QVBoxLayout(self)

        # ---- Lag ---------------------------------------------------------
        boks_lag = QGroupBox("Lag")
        form = QFormLayout(boks_lag)

        self.cb_linje = QgsMapLayerComboBox()
        self.cb_linje.setFilters(_lagfilter("LineLayer"))
        self.cb_bredde = QgsFieldComboBox()
        self.cb_bredde.setFilters(_enum(QgsFieldProxyModel, "Filter", "Numeric"))
        self.cb_linje.layerChanged.connect(self.cb_bredde.setLayer)
        self.cb_bredde.setLayer(self.cb_linje.currentLayer())

        self.cb_punkt = QgsMapLayerComboBox()
        self.cb_punkt.setFilters(_lagfilter("PointLayer"))
        self.cb_kote = QgsFieldComboBox()
        self.cb_kote.setFilters(_enum(QgsFieldProxyModel, "Filter", "Numeric"))
        self.cb_punkt.layerChanged.connect(self.cb_kote.setLayer)
        self.cb_kote.setLayer(self.cb_punkt.currentLayer())

        self.cb_raster = QgsMapLayerComboBox()
        self.cb_raster.setFilters(_lagfilter("RasterLayer"))

        self.sb_band = QSpinBox()
        self.sb_band.setRange(1, 99)
        self.sb_band.setValue(1)

        self.chk_valgte = QCheckBox("Kun valgte grøfter")

        form.addRow("Grøfter (linjelag):", self.cb_linje)
        form.addRow("Felt med bundbredde [m]:", self.cb_bredde)
        form.addRow("Bundkoter (punktlag):", self.cb_punkt)
        form.addRow("Felt med bundkote [m]:", self.cb_kote)
        form.addRow("Terrænmodel:", self.cb_raster)
        form.addRow("Rasterbånd:", self.sb_band)
        form.addRow("", self.chk_valgte)
        hoved.addWidget(boks_lag)

        # ---- Parametre ----------------------------------------------------
        boks_par = QGroupBox("Beregning")
        form2 = QFormLayout(boks_par)

        self.sb_soeg = QDoubleSpinBox()
        self.sb_soeg.setRange(0.01, 1000.0)
        self.sb_soeg.setDecimals(2)
        self.sb_soeg.setValue(5.0)
        self.sb_soeg.setSuffix(" m")
        self.sb_soeg.setToolTip("Bundpunkter længere væk fra nærmeste grøft end dette ignoreres.")

        self.sb_langs = QDoubleSpinBox()
        self.sb_langs.setRange(0.05, 100.0)
        self.sb_langs.setDecimals(2)
        self.sb_langs.setValue(1.0)
        self.sb_langs.setSuffix(" m")
        self.sb_langs.setToolTip("Afstand mellem tværsnit langs grøften.")

        self.sb_tvaers = QDoubleSpinBox()
        self.sb_tvaers.setRange(0.01, 10.0)
        self.sb_tvaers.setDecimals(2)
        self.sb_tvaers.setValue(0.2)
        self.sb_tvaers.setSuffix(" m")
        self.sb_tvaers.setToolTip("Opløsning af terrænopslag på tværs af grøften.")

        form2.addRow("Maks. afstand punkt → grøft:", self.sb_soeg)
        form2.addRow("Tværsnitsafstand langs grøft:", self.sb_langs)
        form2.addRow("Opløsning på tværs:", self.sb_tvaers)
        hoved.addWidget(boks_par)

        info = QLabel(
            "Skråningsanlæg 1:1,5 (ca. 34°). Bundkoten interpoleres lineært mellem "
            "bundpunkterne langs linjen. Resultatet skrives i felterne <b>jordmgd</b> [m³] samt <b>MaxD</b> og <b>MinD</b> (største/mindste dybde under terræn på linjen) [m]."
        )
        info.setWordWrap(True)
        hoved.addWidget(info)

        knapper = QDialogButtonBox(
            _enum(QDialogButtonBox, "StandardButton", "Ok")
            | _enum(QDialogButtonBox, "StandardButton", "Cancel")
        )
        knapper.accepted.connect(self.accept)
        knapper.rejected.connect(self.reject)
        hoved.addWidget(knapper)

    # ------------------------------------------------------------------
    def valg(self):
        return {
            "linje_lag": self.cb_linje.currentLayer(),
            "bredde_felt": self.cb_bredde.currentField(),
            "punkt_lag": self.cb_punkt.currentLayer(),
            "kote_felt": self.cb_kote.currentField(),
            "raster_lag": self.cb_raster.currentLayer(),
            "band": self.sb_band.value(),
            "kun_valgte": self.chk_valgte.isChecked(),
            "soegeafstand": self.sb_soeg.value(),
            "trin_langs": self.sb_langs.value(),
            "trin_tvaers": self.sb_tvaers.value(),
        }
