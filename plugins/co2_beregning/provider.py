# -*- coding: utf-8 -*-
"""Processing-udbyder 'Effekter'."""

import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .co2_algorithm import CO2BeregningAlgorithm


class EffekterProvider(QgsProcessingProvider):
    def loadAlgorithms(self):  # noqa: N802
        self.addAlgorithm(CO2BeregningAlgorithm())

    def id(self):
        return "effekter"

    def name(self):
        return "Effekter"

    def longName(self):  # noqa: N802
        return "Effekter (vandstande.dk)"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
