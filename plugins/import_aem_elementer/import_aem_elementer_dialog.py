# -*- coding: utf-8 -*-
"""
Dialog til Import AEM elementer.
Lader brugeren vælge en .bbl fil på disken samt et geopackage-lag
fra lagpanelet (Layers).
"""

from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QFileDialog,
)
from qgis.core import QgsMapLayerProxyModel
from qgis.gui import QgsMapLayerComboBox


class ImportAemElementerDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import AEM elementer")
        self.resize(480, 140)

        layout = QVBoxLayout()

        # --- Valg af .bbl fil ---
        bbl_layout = QHBoxLayout()
        bbl_layout.addWidget(QLabel(".bbl fil:"))
        self.bbl_edit = QLineEdit()
        self.bbl_edit.setPlaceholderText("Vælg en .bbl fil...")
        bbl_layout.addWidget(self.bbl_edit)
        bbl_browse_btn = QPushButton("Gennemse...")
        bbl_browse_btn.clicked.connect(self.select_bbl_file)
        bbl_layout.addWidget(bbl_browse_btn)
        layout.addLayout(bbl_layout)

        # --- Valg af geopackage-lag ---
        gpkg_layout = QHBoxLayout()
        gpkg_layout.addWidget(QLabel("Geopackage (vælg et lag fra den):"))
        self.layer_combo = QgsMapLayerComboBox()
        self.layer_combo.setFilters(QgsMapLayerProxyModel.VectorLayer)
        gpkg_layout.addWidget(self.layer_combo)
        layout.addLayout(gpkg_layout)

        info_label = QLabel(
            "Vælg et hvilket som helst lag der ligger i den geopackage,\n"
            "hvor de nye AEM-punktlag skal oprettes."
        )
        info_label.setStyleSheet("color: grey; font-size: 10px;")
        layout.addWidget(info_label)

        # --- OK / Annuller ---
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Annuller")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

    def select_bbl_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Vælg .bbl fil",
            "",
            "BBL filer (*.bbl);;Alle filer (*.*)",
        )
        if path:
            self.bbl_edit.setText(path)

    def get_bbl_path(self):
        return self.bbl_edit.text().strip()

    def get_gpkg_layer(self):
        return self.layer_combo.currentLayer()
