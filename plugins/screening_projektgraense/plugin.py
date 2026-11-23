# -*- coding: utf-8 -*-
import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMenu

MENU_NAME = "Effektberegning"
MENU_OBJECT = "EffektberegningMenu"


class ProjektgraensePlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.menu = None
        self.dlg = None

    def _get_menu(self):
        """Find menuen 'Effektberegning' i menulinjen - opret den hvis den
        ikke findes (den deles med de andre effekt-plugins)."""
        bar = self.iface.mainWindow().menuBar()
        for act in bar.actions():
            m = act.menu()
            if m is not None and (m.objectName() == MENU_OBJECT
                                  or m.title().replace("&", "") == MENU_NAME):
                return m
        m = QMenu(MENU_NAME, self.iface.mainWindow())
        m.setObjectName(MENU_OBJECT)
        acts = bar.actions()
        # placer før "Hjælp"-menuen (sidste menu)
        if acts:
            bar.insertMenu(acts[-1], m)
        else:
            bar.addMenu(m)
        return m

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        self.action = QAction(icon, "Screening af projektgrænse...",
                              self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.menu = self._get_menu()
        self.menu.addAction(self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.menu is not None and self.action is not None:
            self.menu.removeAction(self.action)
            if not self.menu.actions():
                bar = self.iface.mainWindow().menuBar()
                bar.removeAction(self.menu.menuAction())
                self.menu.deleteLater()
        if self.action is not None:
            self.iface.removeToolBarIcon(self.action)
        self.menu = None
        self.action = None

    def run(self):
        from .dialog import ProjektgraenseDialog
        if self.dlg is None:
            self.dlg = ProjektgraenseDialog(self.iface)
        self.dlg.refresh()
        self.dlg.show()
        self.dlg.raise_()
        self.dlg.activateWindow()
