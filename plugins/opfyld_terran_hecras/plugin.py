import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QMenu
from .compat import QAction

MENU_TITLE = "HECRAS"


class OpfyldTerranHecras:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.menu = None
        self.dlg = None

    # ------------------------------------------------------------ menu
    def _find_or_create_menu(self):
        menubar = self.iface.mainWindow().menuBar()
        for act in menubar.actions():
            menu = act.menu()
            if menu is not None and act.text().replace("&", "").strip().upper() == MENU_TITLE:
                return menu
        menu = QMenu(MENU_TITLE, self.iface.mainWindow())
        menu.setObjectName("mHECRASMenu")
        help_menu = self.iface.helpMenu()
        if help_menu is not None:
            menubar.insertMenu(help_menu.menuAction(), menu)
        else:
            menubar.addMenu(menu)
        return menu

    # ------------------------------------------------------------ QGIS hooks
    def initGui(self):
        icon = QIcon(os.path.join(self.plugin_dir, "icon.png"))
        self.action = QAction(icon, "Opfyld terræn HECRAS", self.iface.mainWindow())
        self.action.setObjectName("OpfyldTerranHecrasAction")
        self.action.triggered.connect(self.run)

        self.iface.addToolBarIcon(self.action)
        self.menu = self._find_or_create_menu()
        self.menu.addAction(self.action)

    def unload(self):
        if self.action is not None:
            self.iface.removeToolBarIcon(self.action)
            if self.menu is not None:
                self.menu.removeAction(self.action)
                # Fjern kun HECRAS-menuen hvis ingen andre plugins bruger den
                if not self.menu.actions():
                    self.iface.mainWindow().menuBar().removeAction(self.menu.menuAction())
                    self.menu.deleteLater()
            self.action.deleteLater()
        self.action = None
        self.menu = None
        if self.dlg is not None:
            self.dlg.close()
            self.dlg.deleteLater()
            self.dlg = None

    def run(self):
        from .dialog import OpfyldDialog
        if self.dlg is None:
            self.dlg = OpfyldDialog(self.iface, self.iface.mainWindow())
        self.dlg.show()
        self.dlg.raise_()
        self.dlg.activateWindow()
