# -*- coding: utf-8 -*-
"""Hovedklasse for QGIS-pluginet "Grav grøfter"."""

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QMenu, QMessageBox, QProgressDialog, QApplication

from .dialog import GravGroefterDialog, _enum
from . import calculator
from .compat import QAction, NIVEAU_ADVARSEL, NIVEAU_SUCCES

MENU_NAVN = "Anlægsberegning"
MENU_OBJEKT = "mAnlaegsberegningMenu"


class GravGroefter:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.menu = None

    # ------------------------------------------------------------------
    # Fælles menu "Anlægsberegning" i QGIS' menulinje
    # ------------------------------------------------------------------
    def _find_menu(self):
        menulinje = self.iface.mainWindow().menuBar()
        for a in menulinje.actions():
            m = a.menu()
            if m is None:
                continue
            if m.objectName() == MENU_OBJEKT or m.title().replace("&", "") == MENU_NAVN:
                return m
        return None

    def _hent_eller_opret_menu(self):
        m = self._find_menu()
        if m is not None:
            return m
        menulinje = self.iface.mainWindow().menuBar()
        m = QMenu(MENU_NAVN, self.iface.mainWindow())
        m.setObjectName(MENU_OBJEKT)
        try:
            hjaelp = self.iface.firstRightStandardMenu().menuAction()
            menulinje.insertMenu(hjaelp, m)
        except Exception:  # noqa: BLE001
            menulinje.addMenu(m)
        return m

    # ------------------------------------------------------------------
    def initGui(self):
        ikon = QIcon(os.path.join(self.plugin_dir, "icon.png"))
        self.action = QAction(ikon, "Grav grøfter (jordmængde)", self.iface.mainWindow())
        self.action.setObjectName("gravGroefterAction")
        self.action.setStatusTip("Beregn udgravet jordmængde [m³] for grøfter")
        self.action.triggered.connect(self.run)

        self.menu = self._hent_eller_opret_menu()
        self.menu.addAction(self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action is None:
            return
        self.iface.removeToolBarIcon(self.action)
        menu = self._find_menu()
        if menu is not None:
            menu.removeAction(self.action)
            if menu.isEmpty():
                self.iface.mainWindow().menuBar().removeAction(menu.menuAction())
                menu.deleteLater()
        self.action = None
        self.menu = None

    # ------------------------------------------------------------------
    def run(self):
        dlg = GravGroefterDialog(self.iface.mainWindow())
        if not dlg.exec():
            return
        v = dlg.valg()

        mangler = []
        if v["linje_lag"] is None:
            mangler.append("linjelag med grøfter")
        if not v["bredde_felt"]:
            mangler.append("felt med bundbredde")
        if v["punkt_lag"] is None:
            mangler.append("punktlag med bundkoter")
        if not v["kote_felt"]:
            mangler.append("felt med bundkote")
        if v["raster_lag"] is None:
            mangler.append("terrænmodel")
        if mangler:
            QMessageBox.warning(self.iface.mainWindow(), "Grav grøfter",
                                "Vælg venligst: " + ", ".join(mangler) + ".")
            return

        if v["linje_lag"].crs().isGeographic():
            QMessageBox.warning(
                self.iface.mainWindow(), "Grav grøfter",
                "Linjelaget har et geografisk koordinatsystem (grader). "
                "Brug et projiceret koordinatsystem i meter, fx EPSG:25832.")
            return

        if v["kun_valgte"] and v["linje_lag"].selectedFeatureCount() == 0:
            QMessageBox.warning(self.iface.mainWindow(), "Grav grøfter",
                                "Der er ingen valgte grøfter i linjelaget.")
            return

        prog = QProgressDialog("Beregner jordmængder …", "Annullér", 0, 1000,
                               self.iface.mainWindow())
        prog.setWindowTitle("Grav grøfter")
        prog.setWindowModality(_enum(Qt, "WindowModality", "WindowModal"))
        prog.setMinimumDuration(0)

        def fremskridt(brok, tekst):
            prog.setValue(int(brok * 1000))
            prog.setLabelText("Beregner jordmængder … " + tekst)
            QApplication.processEvents()
            return prog.wasCanceled()

        try:
            res = calculator.beregn(fremskridt=fremskridt, **v)
        except Exception as e:  # noqa: BLE001
            prog.close()
            QMessageBox.critical(self.iface.mainWindow(), "Grav grøfter",
                                 "Beregningen fejlede:\n%s" % e)
            return
        prog.setValue(1000)
        prog.close()

        if res.get("annulleret"):
            self.iface.messageBar().pushMessage("Grav grøfter", "Beregningen blev annulleret.",
                                                level=NIVEAU_ADVARSEL, duration=5)
            return

        tekst = ("%d af %d grøfter beregnet – samlet jordmængde %.1f m³ (felterne jordmgd, MaxD og MinD)."
                 % (res["antal_beregnet"], res["antal_groefter"], res["total_m3"]))
        niveau = NIVEAU_SUCCES
        if res["advarsler"] or res["mangler_terraen"]:
            tekst += " Se advarsler i loggen (fanen 'Grav grøfter')."
            niveau = NIVEAU_ADVARSEL
        self.iface.messageBar().pushMessage("Grav grøfter", tekst, level=niveau, duration=10)
