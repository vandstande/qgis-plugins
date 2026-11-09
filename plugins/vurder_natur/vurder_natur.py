# -*- coding: utf-8 -*-
import os

from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QCursor, QDesktopServices, QIcon
from qgis.PyQt.QtWidgets import QApplication, QMenu, QMessageBox
from qgis.core import Qgis
from .compat import NIVEAU_SUCCES, QAction

MENU_TITEL = "Naturvurdering"
MENU_NAVN = "mNaturvurdering"


class VurderNaturPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.mappe = os.path.dirname(__file__)
        self.action = None
        self.menu = None

    # ---------- menu ----------
    def _find_eller_opret_menu(self):
        mb = self.iface.mainWindow().menuBar()
        for a in mb.actions():
            m = a.menu()
            if m and (m.objectName() == MENU_NAVN
                      or a.text().replace("&", "") == MENU_TITEL):
                return m
        m = QMenu(MENU_TITEL, self.iface.mainWindow())
        m.setObjectName(MENU_NAVN)
        hjaelp = self.iface.helpMenu().menuAction() if hasattr(self.iface, "helpMenu") else None
        if hjaelp:
            mb.insertMenu(hjaelp, m)
        else:
            mb.addMenu(m)
        return m

    def initGui(self):
        self.action = QAction(QIcon(os.path.join(self.mappe, "icon.png")),
                              "Vurder natur", self.iface.mainWindow())
        self.action.setObjectName("vurderNaturAction")
        self.action.triggered.connect(self.run)
        self.menu = self._find_eller_opret_menu()
        self.menu.addAction(self.action)

    def unload(self):
        if self.menu and self.action:
            self.menu.removeAction(self.action)
            if self.menu.isEmpty():
                self.iface.mainWindow().menuBar().removeAction(self.menu.menuAction())
                self.menu.deleteLater()
        self.action = None
        self.menu = None

    # ---------- kørsel ----------
    def run(self):
        from .dialog import VurderNaturDialog
        dlg = VurderNaturDialog(self.iface.mainWindow())
        while True:
            if not dlg.exec():
                return
            fejl = self._tjek(dlg)
            if not fejl:
                break
            QMessageBox.warning(dlg, "Vurder natur", fejl)
        dlg.gem_indstillinger()
        self._koer(dlg)

    def _tjek(self, dlg):
        if not dlg.nutid.currentLayer() or not dlg.fremtid.currentLayer():
            return "Vælg både en nutidig og en fremtidig afvandingsmodel."
        if dlg.nutid.currentLayer() == dlg.fremtid.currentLayer():
            return "Nutidig og fremtidig afvandingsmodel er det samme lag."
        if not dlg.natur.currentLayer() or not dlg.felt.currentField():
            return "Vælg naturbeskyttelseslag og felt med naturtypekode."
        if not dlg.projekt.currentLayer():
            return "Vælg et lag med projektgrænsen."
        if not dlg.valgte_typer():
            return "Vælg mindst én naturtype."
        if not dlg.fil.filePath():
            return "Angiv hvor Excel-filen skal gemmes."
        return None

    def _koer(self, dlg):
        from . import analyse, eksport

        sti = dlg.fil.filePath()
        if not sti.lower().endswith(".xlsx"):
            sti += ".xlsx"
        typer = dlg.valgte_typer()
        navne = {6: "Sø", 3: "Mose", 1: "Eng"}
        inddata = {
            "nutid": dlg.nutid.currentLayer().name(),
            "fremtid": dlg.fremtid.currentLayer().name(),
            "natur": dlg.natur.currentLayer().name(),
            "felt": dlg.felt.currentField(),
            "projekt": dlg.projekt.currentLayer().name(),
            "typer": ", ".join(navne[k] for k in (6, 3, 1) if k in typer),
        }
        bar = self.iface.messageBar()

        def status(t):
            self.iface.statusBarIface().showMessage("Vurder natur: " + t)
            QApplication.processEvents()

        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            res = analyse.koer(dlg.nutid.currentLayer(), dlg.fremtid.currentLayer(),
                               dlg.natur.currentLayer(), dlg.felt.currentField(),
                               dlg.projekt.currentLayer(), typer, status)
            skrevet = eksport.gem(sti, res, inddata)
        except analyse.AnalyseFejl as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self.iface.mainWindow(), "Vurder natur", str(e))
            return
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self.iface.mainWindow(), "Vurder natur",
                                 f"Der opstod en fejl:\n{e}")
            raise
        finally:
            self.iface.statusBarIface().clearMessage()
        QApplication.restoreOverrideCursor()

        bar.pushMessage("Vurder natur", f"Resultat gemt i {skrevet}", level=NIVEAU_SUCCES, duration=8)
        if dlg.aabn.isChecked():
            QDesktopServices.openUrl(QUrl.fromLocalFile(skrevet))
