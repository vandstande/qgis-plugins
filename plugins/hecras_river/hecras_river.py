# -*- coding: utf-8 -*-
"""
HECRAS River - samlet QGIS-plugin.

Del 1: Lav Banklines og Cross section lines
Del 2: Lav Cross section punkter og kote punkter

Pluginet placeres i menupunktet "HECRAS" i QGIS' menulinje. Findes menuen
ikke i forvejen, oprettes den.
"""

import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QMenu

from .hecras_river_dialog import HecrasRiverDialog
from .compat import QAction

MENU_TITLE = "HECRAS"
MENU_OBJECT_NAME = "HECRASMenu"


class HecrasRiverPlugin:

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.dialog = None
        self.menu = None
        self.menu_created_by_us = False

    # ------------------------------------------------------------------
    # Menuhåndtering
    # ------------------------------------------------------------------
    def _find_hecras_menu(self):
        """Leder efter et eksisterende topmenupunkt med titlen HECRAS."""
        menu_bar = self.iface.mainWindow().menuBar()
        for act in menu_bar.actions():
            menu = act.menu()
            if menu is None:
                continue
            title = menu.title().replace("&", "").strip()
            if menu.objectName() == MENU_OBJECT_NAME or title.upper() == MENU_TITLE:
                return menu
        return None

    def _create_hecras_menu(self):
        """Opretter HECRAS-menuen i menulinjen (før Hjælp-menuen, hvis den findes)."""
        main_window = self.iface.mainWindow()
        menu_bar = main_window.menuBar()
        menu = QMenu(MENU_TITLE, main_window)
        menu.setObjectName(MENU_OBJECT_NAME)

        help_menu = None
        try:
            help_menu = self.iface.helpMenu()
        except Exception:
            help_menu = None

        if help_menu is not None:
            menu_bar.insertMenu(help_menu.menuAction(), menu)
        else:
            menu_bar.addMenu(menu)
        return menu

    def _get_or_create_menu(self):
        menu = self._find_hecras_menu()
        if menu is not None:
            self.menu_created_by_us = False
            return menu
        self.menu_created_by_us = True
        return self._create_hecras_menu()

    # ------------------------------------------------------------------
    # QGIS plugin-interface
    # ------------------------------------------------------------------
    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        self.action = QAction(QIcon(icon_path), "HECRAS River", self.iface.mainWindow())
        self.action.triggered.connect(self.run)

        self.iface.addToolBarIcon(self.action)

        self.menu = self._get_or_create_menu()
        self.menu.addAction(self.action)

    def unload(self):
        if self.action is not None:
            if self.menu is not None:
                self.menu.removeAction(self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None

        # Fjern kun menuen hvis vi selv oprettede den og den nu er tom
        # (andre HECRAS-plugins kan dele den).
        if self.menu is not None and self.menu_created_by_us:
            if len(self.menu.actions()) == 0:
                menu_bar = self.iface.mainWindow().menuBar()
                menu_bar.removeAction(self.menu.menuAction())
                self.menu.deleteLater()
        self.menu = None

    def run(self):
        if self.dialog is None:
            self.dialog = HecrasRiverDialog(self.iface.mainWindow())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
