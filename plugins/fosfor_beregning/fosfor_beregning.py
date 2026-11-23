import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QMenu
from .compat import QAction

MENU_TITLE = "Effektberegning"
MENU_OBJECT_NAME = "mEffektberegningMenu"


class FosforBeregningPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.menu = None
        self.dialog = None

    # ------------------------------------------------------------------
    def _find_or_create_menu(self):
        menubar = self.iface.mainWindow().menuBar()
        for action in menubar.actions():
            menu = action.menu()
            if menu is None:
                continue
            if menu.objectName() == MENU_OBJECT_NAME or \
                    action.text().replace("&", "") == MENU_TITLE:
                return menu
        menu = QMenu(MENU_TITLE, self.iface.mainWindow())
        menu.setObjectName(MENU_OBJECT_NAME)
        # Indsæt før "Hjælp"-menuen hvis muligt
        help_menu = self.iface.helpMenu() if hasattr(self.iface, "helpMenu") else None
        if help_menu is not None:
            menubar.insertMenu(help_menu.menuAction(), menu)
        else:
            menubar.addMenu(menu)
        return menu

    def initGui(self):
        icon = QIcon(os.path.join(self.plugin_dir, "icon.png"))
        self.action = QAction(icon, "Fosfor beregning", self.iface.mainWindow())
        self.action.setObjectName("FosforBeregningAction")
        self.action.triggered.connect(self.run)
        self.menu = self._find_or_create_menu()
        self.menu.addAction(self.action)

    def unload(self):
        if self.menu is not None and self.action is not None:
            self.menu.removeAction(self.action)
            if self.menu.isEmpty():
                self.iface.mainWindow().menuBar().removeAction(self.menu.menuAction())
                self.menu.deleteLater()
        self.menu = None
        if self.dialog is not None:
            self.dialog.close()
            self.dialog = None
        self.action = None

    def run(self):
        from .dialog import FosforDialog
        if self.dialog is None:
            self.dialog = FosforDialog(self.iface, self.iface.mainWindow())
        self.dialog.refresh_defaults()
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
