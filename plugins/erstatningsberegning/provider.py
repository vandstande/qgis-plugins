# -*- coding: utf-8 -*-
"""Processing-udbyder for Erstatningsberegning."""

import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .erstatning_algorithm import ErstatningsberegningAlgorithm


class ErstatningProvider(QgsProcessingProvider):
    def loadAlgorithms(self):  # noqa: N802
        self.addAlgorithm(ErstatningsberegningAlgorithm())

    def id(self):
        return "erstatning"

    def name(self):
        return "Effekter – Erstatning"

    def longName(self):  # noqa: N802
        return "Effekter – Erstatningsberegning (vandstande.dk)"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
