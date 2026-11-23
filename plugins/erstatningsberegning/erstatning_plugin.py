# -*- coding: utf-8 -*-
"""Plugin-klasse: registrerer Processing-udbyderen, den fælles menu 'Effektberegning' og en knap."""

import os

from qgis.core import QgsApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QMenu

try:  # Qt6 / QGIS 4
    from qgis.PyQt.QtGui import QAction
except ImportError:  # Qt5 / QGIS 3
    from qgis.PyQt.QtWidgets import QAction

from .provider import ErstatningProvider

# Fælles menu for alle effekt-plugins (samme objectName i alle plugins)
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


class ErstatningsberegningPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.action = None
        self.plugin_dir = os.path.dirname(__file__)

    def initProcessing(self):  # noqa: N802
        if self.provider is None:
            self.provider = ErstatningProvider()
            QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):  # noqa: N802
        self.initProcessing()
        icon = QIcon(os.path.join(self.plugin_dir, "icon.png"))
        self.action = QAction(icon, "Erstatningsberegning …", self.iface.mainWindow())
        self.action.setObjectName("actionErstatningsberegning")
        self.action.setToolTip("Erstatningsberegning: arealer pr. lodsejer og sats")
        self.action.triggered.connect(self.run)

        menu = hent_effektmenu(self.iface, opret=True)
        menu.addAction(self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action is not None:
            menu = hent_effektmenu(self.iface, opret=False)
            if menu is not None:
                menu.removeAction(self.action)
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
        processing.execAlgorithmDialog("erstatning:erstatningsberegning", {})
