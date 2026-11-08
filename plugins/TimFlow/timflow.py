# -*- coding: utf-8 -*-
"""
TimFlow - hoved plugin-klasse.
Opretter et værktøjslinje-ikon samt en selvstændig "TimFlow"-menu i menubjælken,
begge peger på det samme plugin-vindue (TimFlowDialog).
"""
import os

from qgis.PyQt.QtWidgets import QAction, QMenu
from qgis.PyQt.QtGui import QIcon

from .timflow_dialog import TimFlowDialog


class TimFlowPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.actions = []
        self.toolbar = self.iface.addToolBar("TimFlow")
        self.toolbar.setObjectName("TimFlowToolbar")
        self.menu = None
        self.menu_created_by_plugin = True
        self.action = None
        self.dlg = None

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        icon = QIcon(icon_path)

        self.action = QAction(icon, "TimFlow", self.iface.mainWindow())
        self.action.setObjectName("TimFlowAction")
        self.action.triggered.connect(self.run)

        # Ikon i værktøjslinjen
        self.toolbar.addAction(self.action)
        self.actions.append(self.action)

        # Menupunkt "TimFlow" i menubjælken (ikke under Plugins).
        # Findes menuen allerede (fx tilføjet af en anden komponent/plugin),
        # tilføjes handlingen blot til den - ellers oprettes en ny menu.
        menubar = self.iface.mainWindow().menuBar()

        existing_menu = None
        for a in menubar.actions():
            m = a.menu()
            if m is not None and m.title().replace("&", "") == "TimFlow":
                existing_menu = m
                break

        if existing_menu is not None:
            self.menu = existing_menu
            self.menu.addAction(self.action)
            self.menu_created_by_plugin = False
        else:
            self.menu = QMenu("TimFlow", menubar)
            self.menu.addAction(self.action)
            self.menu_created_by_plugin = True

            help_menu_action = None
            for a in menubar.actions():
                m = a.menu()
                if m is not None and m.title().replace("&", "") in ("Help", "Hjælp"):
                    help_menu_action = a
                    break

            if help_menu_action is not None:
                menubar.insertMenu(help_menu_action, self.menu)
            else:
                menubar.addMenu(self.menu)

    def unload(self):
        for action in self.actions:
            self.toolbar.removeAction(action)
        self.toolbar.deleteLater()

        if self.menu is not None:
            if getattr(self, "menu_created_by_plugin", True):
                # Pluginet oprettede selv menuen - fjern den helt igen
                self.menu.deleteLater()
            else:
                # Menuen fandtes i forvejen - fjern kun vores egen handling
                self.menu.removeAction(self.action)
            self.menu = None

        if self.dlg is not None:
            self.dlg.close()
            self.dlg = None

    def run(self):
        # Dialogen skal forblive åben (non-modal), derfor .show() og ikke .exec_()
        if self.dlg is None:
            self.dlg = TimFlowDialog(self.iface)
            self.dlg.destroyed.connect(self._on_dialog_closed)
        self.dlg.show()
        self.dlg.raise_()
        self.dlg.activateWindow()

    def _on_dialog_closed(self):
        self.dlg = None
