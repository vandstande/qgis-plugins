# -*- coding: utf-8 -*-
"""Plugin-klasse: registrerer Processing-udbyderen, menuen 'Effektberegning' og en knap."""

import os

from qgis.core import QgsApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QMenu

try:  # Qt6 / QGIS 4
    from qgis.PyQt.QtGui import QAction
except ImportError:  # Qt5 / QGIS 3
    from qgis.PyQt.QtWidgets import QAction

from .provider import EffekterProvider

# Fælles menu for alle effekt-plugins. Andre plugins kan finde den via objectName.
MENU_TITEL = "Effektberegning"
MENU_OBJEKTNAVN = "mEffektberegningMenu"


def hent_effektmenu(iface, opret=True):
    """Find menuen 'Effektberegning' i menulinjen – opret den hvis den ikke findes."""
    menubar = iface.mainWindow().menuBar()
    for action in menubar.actions():
        menu = action.menu()
        if menu is None:
            continue
        if menu.objectName() == MENU_OBJEKTNAVN or \
                menu.title().replace("&", "") == MENU_TITEL:
            return menu
    if not opret:
        return None

    menu = QMenu("&" + MENU_TITEL, menubar)
    menu.setObjectName(MENU_OBJEKTNAVN)
    # Placér menuen før 'Hjælp', ellers sidst
    foer = None
    try:
        foer = iface.helpMenu().menuAction()
    except Exception:
        foer = None
    if foer is not None:
        menubar.insertMenu(foer, menu)
    else:
        menubar.addMenu(menu)
    return menu


class CO2BeregningPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.action = None
        self.plugin_dir = os.path.dirname(__file__)

    # Kaldes af QGIS når Processing initialiseres (hasProcessingProvider=yes)
    def initProcessing(self):  # noqa: N802
        if self.provider is None:
            self.provider = EffekterProvider()
            QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):  # noqa: N802
        self.initProcessing()
        icon = QIcon(os.path.join(self.plugin_dir, "icon.png"))
        self.action = QAction(icon, "CO2-beregning …", self.iface.mainWindow())
        self.action.setObjectName("actionCO2Beregning")
        self.action.setToolTip("CO2-beregning: arealer pr. tørveklasse og drifttype")
        self.action.triggered.connect(self.run)

        menu = hent_effektmenu(self.iface, opret=True)
        menu.addAction(self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action is not None:
            menu = hent_effektmenu(self.iface, opret=False)
            if menu is not None:
                menu.removeAction(self.action)
                # Fjern menuen helt, hvis intet andet plugin bruger den
                if not menu.actions():
                    self.iface.mainWindow().menuBar().removeAction(menu.menuAction())
                    menu.deleteLater()
            self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater()
            self.action = None
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    def run(self):
        from qgis import processing
        processing.execAlgorithmDialog("effekter:co2_beregning", {})
