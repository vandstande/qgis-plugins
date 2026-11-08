# -*- coding: utf-8 -*-
"""
Opmaaling HECRAS - QGIS plugin

Danner ud fra en vandløbsmidte ("River centerlinje") og et punktlag med
opmålingspunkter ("Opmålingspunkter"):
  1. Tværprofiler (linjer fra 12PROFILSTARTV til næste 14PROFILSLUT)
  2. Bank lines (venstre og højre brink, der følger centerlinjen)
  3. Hecrasopmaaling (opmålingspunkter alignet til nærmeste tværprofil,
     med X og Y koordinat)
"""
import os
import csv

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon, QColor
from qgis.PyQt.QtWidgets import (
    QAction, QMenu, QDialog, QVBoxLayout, QFormLayout, QGroupBox, QLineEdit,
    QHBoxLayout, QPushButton, QTabWidget, QWidget, QMessageBox, QFileDialog, QApplication, QLabel,
)
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY, QgsPoint,
    QgsField, QgsFields, QgsWkbTypes, QgsCoordinateTransform,
    QgsVectorFileWriter, QgsFeatureRequest, QgsVectorDataProvider,
    QgsMessageLog,
)
from qgis.gui import QgsMapLayerComboBox, QgsFieldComboBox, QgsFileWidget

from . import geometri as geo

PLUGIN_NAVN = "Opmaaling HECRAS"
STARTKODE = "12PROFILSTARTV"
SLUTKODE = "14PROFILSLUT"
DEL1_FAERDIG = "Fortsæt først i Del 2, når tværprofilerne har været eksporteret fra HEC-RAS"
RS_FELTNAVNE = ["RS", "RiverStation", "RiverStati", "River_Stat", "RiverSta", "River_Sta",
                "Station", "XS_ID", "ProfileM"]


# ---------------------------------------------------------------------------
# Kompatibilitet QGIS 3 / QGIS 4
# ---------------------------------------------------------------------------
def _lagfilter(navn):
    try:
        from qgis.core import Qgis
        return getattr(Qgis.LayerFilter, navn)
    except Exception:
        from qgis.core import QgsMapLayerProxyModel
        return getattr(QgsMapLayerProxyModel, navn)


def _felt(navn, typ, laengde=0):
    try:
        from qgis.PyQt.QtCore import QMetaType
        t = {"int": QMetaType.Type.Int, "double": QMetaType.Type.Double,
             "string": QMetaType.Type.QString}[typ]
        f = QgsField(navn, t)
    except TypeError:
        from qgis.PyQt.QtCore import QVariant
        t = {"int": QVariant.Int, "double": QVariant.Double,
             "string": QVariant.String}[typ]
        f = QgsField(navn, t)
    if laengde:
        f.setLength(laengde)
    return f


def _gemmetilstand():
    try:
        return QgsFileWidget.StorageMode.SaveFile
    except AttributeError:
        return QgsFileWidget.SaveFile


def _hentetilstand():
    try:
        return QgsFileWidget.StorageMode.GetFile
    except AttributeError:
        return QgsFileWidget.GetFile


def _filsti(sti):
    """Normaliseret filsti til sammenligning."""
    return os.path.normcase(os.path.abspath(sti)) if sti else ""


def aabn_vektorfil(sti, lagnavn=None):
    """Åbn en .shp/.gpkg fra drevet som vektorlag. For en geopackage
    foretrækkes laget 'lagnavn', hvis det findes."""
    if not sti or not os.path.exists(sti):
        return None
    navn = os.path.splitext(os.path.basename(sti))[0]
    if lagnavn and sti.lower().endswith(".gpkg"):
        lag = QgsVectorLayer("%s|layername=%s" % (sti, lagnavn), lagnavn, "ogr")
        if lag.isValid():
            return lag
    lag = QgsVectorLayer(sti, navn, "ogr")
    return lag if lag.isValid() else None


def projektlag_for_fil(sti):
    """Lag i projektet der peger på samme fil."""
    ud = []
    for lag in QgsProject.instance().mapLayers().values():
        try:
            if lag.providerType() == "ogr" and _filsti(lag.source().split("|")[0]) == _filsti(sti):
                ud.append(lag)
        except Exception:
            pass
    return ud


def _ventecursor():
    try:
        return Qt.CursorShape.WaitCursor
    except AttributeError:
        return Qt.WaitCursor


def _vaelg_felt(combo, navn):
    """Vælg feltet 'navn' (uden hensyn til store/små bogstaver) hvis det findes."""
    lag = combo.layer()
    if lag is None:
        return
    for f in lag.fields():
        if f.name().lower() == navn.lower():
            combo.setField(f.name())
            return


def _vaelg_lag(combo, noegleord, undtag=()):
    """Forvælg det første lag hvis navn indeholder et af nøgleordene."""
    for lag in QgsProject.instance().mapLayers().values():
        navn = lag.name().lower()
        if any(u in navn for u in undtag):
            continue
        if any(k in navn for k in noegleord):
            combo.setLayer(lag)
            if combo.currentLayer() == lag:
                return


def _vaelg_felt_blandt(combo, navne):
    lag = combo.layer()
    if lag is None:
        return
    felter = {f.name().lower(): f.name() for f in lag.fields()}
    for n in navne:
        if n.lower() in felter:
            combo.setField(felter[n.lower()])
            return


def _enum(klasse, gruppe, navn):
    """Hent enum-værdi både med og uden scoped navn (PyQt5/PyQt6)."""
    try:
        return getattr(getattr(klasse, gruppe), navn)
    except AttributeError:
        return getattr(klasse, navn)


# ---------------------------------------------------------------------------
# Fælles menupunkt "HECRAS" i menulinjen
# ---------------------------------------------------------------------------
HECRAS_MENU = "HECRAS"


def _menutitel(menu):
    return menu.title().replace("&", "").strip().lower()


def find_hecras_menu(iface):
    """Find et eksisterende menupunkt 'HECRAS' i QGIS' menulinje."""
    mb = iface.mainWindow().menuBar()
    for handling in mb.actions():
        m = handling.menu()
        if m is not None and (m.objectName() == "mHecrasMenu"
                              or _menutitel(m) == HECRAS_MENU.lower()):
            return m
    return None


def hent_hecras_menu(iface):
    """Returnér menupunktet 'HECRAS' - opret det, hvis det ikke findes.
    Nyt menupunkt placeres lige før menuen Hjælp/Help."""
    menu = find_hecras_menu(iface)
    if menu is not None:
        return menu
    mw = iface.mainWindow()
    mb = mw.menuBar()
    menu = QMenu(HECRAS_MENU, mw)
    menu.setObjectName("mHecrasMenu")
    foer = None
    try:
        hjaelp = iface.helpMenu()
        if hjaelp is not None:
            foer = hjaelp.menuAction()
    except Exception:
        foer = None
    if foer is not None and foer in mb.actions():
        mb.insertMenu(foer, menu)
    else:
        mb.addMenu(menu)
    return menu


def fjern_fra_hecras_menu(iface, handling):
    """Fjern plugin'ets punkt; menupunktet 'HECRAS' fjernes kun, hvis det
    er tomt (andre HECRAS-plugins kan bruge det samme menupunkt)."""
    menu = find_hecras_menu(iface)
    if menu is None:
        return
    menu.removeAction(handling)
    if not menu.actions():
        iface.mainWindow().menuBar().removeAction(menu.menuAction())
        menu.deleteLater()


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------
class OpmaalingDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(PLUGIN_NAVN)
        self.setMinimumWidth(540)
        hoved = QVBoxLayout(self)
        self.faner = QTabWidget()
        hoved.addWidget(self.faner)

        # ================= Del 1 =================
        del1 = QWidget()
        v1 = QVBoxLayout(del1)
        grp_in = QGroupBox("Inputlag")
        form = QFormLayout(grp_in)

        self.cb_river = QgsMapLayerComboBox()
        self.cb_river.setFilters(_lagfilter("LineLayer"))
        self.cb_river_felt = QgsFieldComboBox()
        self.cb_reach_felt = QgsFieldComboBox()
        form.addRow("River centerlinje:", self.cb_river)
        form.addRow("    Felt 'River':", self.cb_river_felt)
        form.addRow("    Felt 'Reach':", self.cb_reach_felt)

        self.cb_pkt = QgsMapLayerComboBox()
        self.cb_pkt.setFilters(_lagfilter("PointLayer"))
        self.cb_kode_felt = QgsFieldComboBox()
        self.le_start = QLineEdit(STARTKODE)
        self.le_slut = QLineEdit(SLUTKODE)
        form.addRow("Opmålingspunkter:", self.cb_pkt)
        form.addRow("    Felt 'KODE':", self.cb_kode_felt)
        form.addRow("    Startkode (venstre):", self.le_start)
        form.addRow("    Slutkode (højre):", self.le_slut)
        v1.addWidget(grp_in)

        grp_ud = QGroupBox("Gem lag (tomt felt = midlertidigt lag)")
        form2 = QFormLayout(grp_ud)
        filt = "Shapefile (*.shp);;GeoPackage (*.gpkg)"
        self.fw_profil = self._filwidget(filt)
        self.fw_bank = self._filwidget(filt)
        self.fw_pkt = self._filwidget(filt)
        form2.addRow("Tværprofiler:", self.fw_profil)
        form2.addRow("Bank lines:", self.fw_bank)
        form2.addRow("Hecrasopmaaling:", self.fw_pkt)
        v1.addWidget(grp_ud)

        info = QLabel("Tværprofiler går fra startkode til næste slutkode i tabellen.\n"
                      "Lagene skal ligge i et projiceret koordinatsystem (meter).")
        info.setWordWrap(True)
        v1.addWidget(info)
        v1.addStretch(1)
        k1 = QHBoxLayout()
        k1.addStretch(1)
        self.btn_koer = QPushButton("Dan lag")
        k1.addWidget(self.btn_koer)
        v1.addLayout(k1)
        self.faner.addTab(del1, "Del 1")

        # ================= Del 2 =================
        del2 = QWidget()
        v2 = QVBoxLayout(del2)
        grp2 = QGroupBox("Inputlag")
        form3 = QFormLayout(grp2)
        filt_hent = "Vektorfiler (*.shp *.gpkg);;Shapefile (*.shp);;GeoPackage (*.gpkg)"
        self.fw_xs = self._filwidget(filt_hent, hent=True)
        self.cb_xs_rs = QgsFieldComboBox()
        self.fw_hec = self._filwidget(filt_hent, hent=True)
        form3.addRow("Tværprofiler fra HEC-RAS:", self.fw_xs)
        form3.addRow("    Felt med River Station:", self.cb_xs_rs)
        form3.addRow("Hecrasopmaaling:", self.fw_hec)
        self._xs_lag = None
        v2.addWidget(grp2)
        info2 = QLabel("Feltet RS i Hecrasopmaaling bliver tømt og udfyldt med River Station "
                       "fra det nærmeste HEC-RAS-tværprofil. Alle punkter med samme ProfilNr "
                       "får samme RS. Ændringen skrives direkte i Hecrasopmaaling-filen.")
        info2.setWordWrap(True)
        v2.addWidget(info2)
        v2.addStretch(1)
        k2 = QHBoxLayout()
        k2.addStretch(1)
        self.btn_ret = QPushButton("Ret opmålingslag")
        k2.addWidget(self.btn_ret)
        v2.addLayout(k2)
        self.faner.addTab(del2, "Del 2")

        # --- fælles
        kn = QHBoxLayout()
        kn.addStretch(1)
        btn_luk = QPushButton("Luk")
        btn_luk.clicked.connect(self.reject)
        kn.addWidget(btn_luk)
        hoved.addLayout(kn)

        # --- forbindelser og standardvalg
        self.cb_river.layerChanged.connect(self._river_skiftet)
        self.cb_pkt.layerChanged.connect(self._pkt_skiftet)
        self.fw_xs.fileChanged.connect(self._xs_skiftet)
        self.fw_pkt.fileChanged.connect(self._hec_fra_del1)
        _vaelg_lag(self.cb_river, ["centerlinj", "centerline", "river", "vandløbsmidte", "midte"])
        _vaelg_lag(self.cb_pkt, ["opmål", "opmaal", "opmal", "survey"], undtag=["hecras"])
        self._river_skiftet(self.cb_river.currentLayer())
        self._pkt_skiftet(self.cb_pkt.currentLayer())

    def _filwidget(self, filt, hent=False):
        fw = QgsFileWidget()
        fw.setStorageMode(_hentetilstand() if hent else _gemmetilstand())
        fw.setFilter(filt)
        return fw

    def _river_skiftet(self, lag):
        self.cb_river_felt.setLayer(lag)
        self.cb_reach_felt.setLayer(lag)
        _vaelg_felt(self.cb_river_felt, "River")
        _vaelg_felt(self.cb_reach_felt, "Reach")

    def _pkt_skiftet(self, lag):
        self.cb_kode_felt.setLayer(lag)
        _vaelg_felt(self.cb_kode_felt, "KODE")

    def _xs_skiftet(self, sti):
        """Læs felterne fra den valgte HEC-RAS-fil ind i feltlisten."""
        self._xs_lag = aabn_vektorfil(sti.strip()) if sti else None
        self.cb_xs_rs.setLayer(self._xs_lag)
        _vaelg_felt_blandt(self.cb_xs_rs, RS_FELTNAVNE)

    def _hec_fra_del1(self, sti):
        """Hecrasopmaaling i Del 2 følger filen valgt i Del 1."""
        sti = sti.strip()
        if sti and not os.path.splitext(sti)[1]:
            sti += ".shp"
        self.fw_hec.setFilePath(sti)


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------
class OpmaalingHecras:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.menu = None
        self.dlg = None

    def initGui(self):
        ikon = QIcon(os.path.join(self.plugin_dir, "icon.png"))
        self.action = QAction(ikon, PLUGIN_NAVN, self.iface.mainWindow())
        self.action.setObjectName("OpmaalingHecrasAction")
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.menu = hent_hecras_menu(self.iface)
        self.menu.addAction(self.action)

    def unload(self):
        if self.action:
            self.iface.removeToolBarIcon(self.action)
            fjern_fra_hecras_menu(self.iface, self.action)
            self.action = None
        self.menu = None

    def run(self):
        self.dlg = OpmaalingDialog(self.iface.mainWindow())
        self.dlg.btn_koer.clicked.connect(self.koer)
        self.dlg.btn_ret.clicked.connect(self.ret_opmaaling)
        self.dlg.show()

    # -----------------------------------------------------------------
    def _fejl(self, tekst):
        QMessageBox.warning(self.dlg, PLUGIN_NAVN, tekst)

    def koer(self):
        d = self.dlg
        river_lag = d.cb_river.currentLayer()
        pkt_lag = d.cb_pkt.currentLayer()
        if river_lag is None or pkt_lag is None:
            self._fejl("Vælg både 'River centerlinje' og 'Opmålingspunkter'.")
            return
        river_felt = d.cb_river_felt.currentField()
        reach_felt = d.cb_reach_felt.currentField()
        kode_felt = d.cb_kode_felt.currentField()
        if not river_felt or not reach_felt:
            self._fejl("Vælg felterne 'River' og 'Reach' i River centerlinje.")
            return
        if not kode_felt:
            self._fejl("Vælg feltet 'KODE' i Opmålingspunkter.")
            return
        startkode = d.le_start.text().strip()
        slutkode = d.le_slut.text().strip()
        if not startkode or not slutkode:
            self._fejl("Angiv både start- og slutkode.")
            return
        crs = pkt_lag.crs()
        if crs.isGeographic():
            svar = QMessageBox.question(
                d, PLUGIN_NAVN,
                "Opmålingspunkterne ligger i et geografisk koordinatsystem (grader).\n"
                "Afstande og X/Y bliver i grader. Fortsæt alligevel?")
            if svar != _enum(QMessageBox, "StandardButton", "Yes"):
                return

        QApplication.setOverrideCursor(_ventecursor())
        try:
            resultat = self._beregn(river_lag, pkt_lag, river_felt, reach_felt,
                                    kode_felt, startkode, slutkode)
        except Exception as e:  # vis fejlen i stedet for at crashe
            QApplication.restoreOverrideCursor()
            import traceback
            self._fejl("Der opstod en fejl:\n%s\n\n%s" % (e, traceback.format_exc()))
            return
        QApplication.restoreOverrideCursor()
        if resultat is None:
            return
        profil_lag, bank_lag, hec_lag, advarsler = resultat

        tilfoejet = []
        for lag, fw, farve in ((profil_lag, d.fw_profil, "#d7191c"),
                               (bank_lag, d.fw_bank, "#1a9641"),
                               (hec_lag, d.fw_pkt, "#2b83ba")):
            nyt = self._gem_og_tilfoej(lag, fw.filePath().strip(), farve)
            if nyt is not None:
                tilfoejet.append("%s: %d" % (nyt.name(), nyt.featureCount()))
                if lag is hec_lag and nyt.providerType() == "ogr":
                    d.fw_hec.setFilePath(nyt.source().split("|")[0])   # forudfyld Del 2

        try:
            self.iface.messageBar().pushSuccess(PLUGIN_NAVN, "Dannet: " + ", ".join(tilfoejet))
        except Exception:
            pass
        if advarsler:
            for a in advarsler:
                QgsMessageLog.logMessage(a, PLUGIN_NAVN)
            tekst = "Bemærkninger:\n- " + "\n- ".join(advarsler[:25])
            if len(advarsler) > 25:
                tekst += "\n... og %d mere (se loggen)" % (len(advarsler) - 25)
            QMessageBox.warning(d, PLUGIN_NAVN, tekst)
        QMessageBox.information(d, PLUGIN_NAVN, DEL1_FAERDIG)

    # -----------------------------------------------------------------
    # Del 2
    # -----------------------------------------------------------------
    def ret_opmaaling(self):
        d = self.dlg
        xs_sti = d.fw_xs.filePath().strip()
        hec_sti = d.fw_hec.filePath().strip()
        rs_felt = d.cb_xs_rs.currentField()
        if not xs_sti or not hec_sti:
            self._fejl("Vælg både filen med 'Tværprofiler fra HEC-RAS' og 'Hecrasopmaaling'.")
            return
        if _filsti(xs_sti) == _filsti(hec_sti):
            self._fejl("Vælg to forskellige filer.")
            return
        xs_lag = aabn_vektorfil(xs_sti)
        if xs_lag is None:
            self._fejl("Filen kunne ikke åbnes:\n%s" % xs_sti)
            return
        hec_lag = aabn_vektorfil(hec_sti, "Hecrasopmaaling")
        if hec_lag is None:
            self._fejl("Filen kunne ikke åbnes:\n%s" % hec_sti)
            return
        if not rs_felt:
            self._fejl("Vælg feltet med River Station i HEC-RAS-tværprofilerne.")
            return
        aabne = projektlag_for_fil(hec_sti)
        if any(l.isEditable() for l in aabne):
            self._fejl("Hecrasopmaaling er i redigeringstilstand i projektet.\n"
                       "Gem og afslut redigeringen først.")
            return
        QApplication.setOverrideCursor(_ventecursor())
        try:
            res = self._saet_rs(xs_lag, rs_felt, hec_lag)
        except RuntimeError as e:
            QApplication.restoreOverrideCursor()
            self._fejl(str(e))
            return
        except Exception as e:
            QApplication.restoreOverrideCursor()
            import traceback
            self._fejl("Der opstod en fejl:\n%s\n\n%s" % (e, traceback.format_exc()))
            return
        QApplication.restoreOverrideCursor()
        if res is None:
            return
        antal, uden, max_afst, advarsler = res
        for l in aabne:               # opdatér lag i projektet der viser filen
            try:
                l.dataProvider().reloadData()
                l.updateFields()
            except Exception:
                pass
            l.reload()
            l.triggerRepaint()
        tekst = "RS er sat på %d punkter i '%s'." % (antal, os.path.basename(hec_sti))
        if uden:
            tekst += "\n%d punkter fik ingen RS (tom værdi)." % uden
        if max_afst is not None:
            tekst += "\nStørste afstand fra punkt til HEC-RAS-tværprofil: %.2f m." % max_afst
        tekst += "\n\nFilen er opdateret:\n%s" % hec_sti
        if advarsler:
            for a in advarsler:
                QgsMessageLog.logMessage(a, PLUGIN_NAVN)
            tekst += "\n\nBemærkninger:\n- " + "\n- ".join(advarsler[:15])
            if len(advarsler) > 15:
                tekst += "\n... og %d mere (se loggen)" % (len(advarsler) - 15)
        tekst += "\n\nVil du gemme det rettede opmålingslag som en CSV-fil?"
        svar = QMessageBox.question(d, PLUGIN_NAVN, tekst)
        if svar == _enum(QMessageBox, "StandardButton", "Yes"):
            self._gem_csv(hec_sti)

    def _gem_csv(self, hec_sti):
        """Gem det rettede opmålingslag som kommasepareret CSV."""
        forslag = os.path.splitext(hec_sti)[0] + ".csv"
        res = QFileDialog.getSaveFileName(self.dlg, "Gem opmålingslag som CSV", forslag,
                                          "CSV-fil (*.csv)")
        sti = res[0] if isinstance(res, tuple) else res
        if not sti:
            return
        if not sti.lower().endswith(".csv"):
            sti += ".csv"
        lag = aabn_vektorfil(hec_sti, "Hecrasopmaaling")   # læs filen frisk fra disken
        if lag is None:
            self._fejl("Filen kunne ikke åbnes:\n%s" % hec_sti)
            return
        try:
            antal = skriv_csv(lag, sti)
        except (IOError, OSError) as e:
            self._fejl("CSV-filen kunne ikke gemmes - er den åben i Excel?\n%s\n\n%s" % (sti, e))
            return
        QMessageBox.information(self.dlg, PLUGIN_NAVN,
                                "CSV-filen er gemt med %d punkter:\n%s" % (antal, sti))

    def _saet_rs(self, xs_lag, rs_felt, hec_lag):
        advarsler = []
        prov = hec_lag.dataProvider()
        kap = prov.capabilities()
        if not (kap & _enum(QgsVectorDataProvider, "Capability", "ChangeAttributeValues")):
            self._fejl("Hecrasopmaaling-laget kan ikke redigeres (skrivebeskyttet format).")
            return None

        trans = None
        if xs_lag.crs() != hec_lag.crs():
            trans = QgsCoordinateTransform(xs_lag.crs(), hec_lag.crs(), QgsProject.instance())

        def feltnavn(lag, navn):
            for f in lag.fields():
                if f.name().lower() == navn.lower():
                    return f.name()
            return None

        xs_river, xs_reach = feltnavn(xs_lag, "River"), feltnavn(xs_lag, "Reach")
        pk_river, pk_reach = feltnavn(hec_lag, "River"), feltnavn(hec_lag, "Reach")
        brug_rr = bool(xs_river and xs_reach and pk_river and pk_reach)

        # --- HEC-RAS tværprofiler
        xs = []
        for f in xs_lag.getFeatures():
            g = QgsGeometry(f.geometry())
            if g is None or g.isEmpty():
                continue
            if trans:
                g.transform(trans)
            dele = g.asMultiPolyline() if g.isMultipart() else [g.asPolyline()]
            dele = [[(p.x(), p.y()) for p in del_] for del_ in dele if len(del_) >= 2]
            if not dele:
                continue
            rs = _tal(f[rs_felt])
            if rs is None:
                advarsler.append("Tværprofil (fid %s) har ingen gyldig værdi i '%s': %s"
                                 % (f.id(), rs_felt, f[rs_felt]))
                continue
            xs.append({"dele": dele, "rs": rs,
                       "rr": (_nrm(f[xs_river]), _nrm(f[xs_reach])) if brug_rr else None})
        if not xs:
            self._fejl("Der blev ikke fundet tværprofiler med gyldig River Station i '%s'."
                       % xs_lag.name())
            return None

        def afstand(P, x):
            return min(geo.naermeste_paa_polylinje(P, del_)[1] for del_ in x["dele"])

        # --- punkter, grupperet efter ProfilNr (hvis feltet findes)
        nr_felt = feltnavn(hec_lag, "ProfilNr")
        grupper = {}
        for f in hec_lag.getFeatures():
            g = f.geometry()
            if g is None or g.isEmpty():
                continue
            p = g.asMultiPoint()[0] if g.isMultipart() else g.asPoint()
            nr = _txt(f[nr_felt]) if nr_felt is not None else None
            noegle = ("nr", nr) if nr not in (None, "") else ("fid", f.id())
            rr = (_nrm(f[pk_river]), _nrm(f[pk_reach])) if brug_rr else None
            grupper.setdefault((noegle, rr), {"pkt": [], "rr": rr})["pkt"].append(
                (f.id(), (p.x(), p.y())))

        # --- vælg tværprofil pr. gruppe (mindste middelafstand)
        nye = {}
        max_afst = None
        mangler_rr = set()
        for grp in grupper.values():
            kandidater = xs
            if brug_rr:
                samme = [x for x in xs if x["rr"] == grp["rr"]]
                if samme:
                    kandidater = samme
                elif grp["rr"] not in mangler_rr:
                    mangler_rr.add(grp["rr"])
                    advarsler.append("Ingen HEC-RAS-tværprofiler med River/Reach %s/%s - "
                                     "nærmeste profil bruges" % grp["rr"])
            bedst = None
            for x in kandidater:
                afst = [afstand(P, x) for _, P in grp["pkt"]]
                m = sum(afst) / len(afst)
                if bedst is None or m < bedst[0]:
                    bedst = (m, max(afst), x)
            _, mx, x = bedst
            max_afst = mx if max_afst is None else max(max_afst, mx)
            for fid, _ in grp["pkt"]:
                nye[fid] = x["rs"]

        # --- skriv RS direkte i filen (tøm feltet / opret det som decimaltal)
        sti = hec_lag.source().split("|")[0]
        lagnavn = None
        for del_ in hec_lag.source().split("|")[1:]:
            if del_.lower().startswith("layername="):
                lagnavn = del_.split("=", 1)[1]
        alle_fid = [f.id() for f in hec_lag.getFeatures()]
        try:
            antal, uden, adv = skriv_rs_ogr(sti, lagnavn, nye)
        except ImportError:
            antal, uden, adv = self._skriv_rs_qgis(hec_lag, nye, alle_fid)
        advarsler += adv
        return antal, uden, max_afst, advarsler

    def _skriv_rs_qgis(self, hec_lag, nye, alle_fid):
        """Reserve hvis GDAL/OGR ikke kan importeres: QGIS-redigering med commit."""
        idx = hec_lag.fields().indexOf("RS")
        if idx < 0:
            for i, f in enumerate(hec_lag.fields()):
                if f.name().lower() == "rs":
                    idx = i
        hec_lag.startEditing()
        if idx < 0:
            hec_lag.addAttribute(_felt("RS", "double"))
            idx = hec_lag.fields().indexOf("RS")
        numerisk = hec_lag.fields().at(idx).isNumeric()
        antal = 0
        for fid in alle_fid:
            v = nye.get(fid)
            if v is not None:
                antal += 1
                v = round(v, 4) if numerisk else _rs_tekst(v)
            hec_lag.changeAttributeValue(fid, idx, v)
        if not hec_lag.commitChanges():
            fejl = "; ".join(hec_lag.commitErrors())
            hec_lag.rollBack()
            raise RuntimeError("RS kunne ikke gemmes i filen:\n%s" % fejl)
        return antal, len(alle_fid) - antal, []

    # -----------------------------------------------------------------
    def _beregn(self, river_lag, pkt_lag, river_felt, reach_felt,
                kode_felt, startkode, slutkode):
        advarsler = []
        crs = pkt_lag.crs()
        projekt = QgsProject.instance()
        trans = None
        if river_lag.crs() != crs:
            trans = QgsCoordinateTransform(river_lag.crs(), crs, projekt)

        # --- centerlinjer
        centre = []   # dicts: poly, river, reach
        for f in river_lag.getFeatures():
            g = QgsGeometry(f.geometry())
            if g is None or g.isEmpty():
                continue
            if trans:
                g.transform(trans)
            if g.isMultipart():
                m = g.mergeLines()
                if not m.isEmpty():
                    g = m
            dele = g.asMultiPolyline() if g.isMultipart() else [g.asPolyline()]
            for del_ in dele:
                try:
                    poly = geo.Polylinje([(p.x(), p.y()) for p in del_])
                except ValueError:
                    continue
                centre.append({"poly": poly, "river": f[river_felt],
                               "reach": f[reach_felt], "fid": f.id()})
        if not centre:
            self._fejl("Der blev ikke fundet gyldige linjer i 'River centerlinje'.")
            return None

        # --- opmålingspunkter i tabelrækkefølge
        req = QgsFeatureRequest()
        pkt_feats = []
        for f in pkt_lag.getFeatures(req):
            g = f.geometry()
            if g is None or g.isEmpty():
                continue
            pkt_feats.append(f)
        pkt_feats.sort(key=lambda f: f.id())
        if not pkt_feats:
            self._fejl("Laget 'Opmålingspunkter' indeholder ingen punkter.")
            return None

        def xy(f):
            g = f.geometry()
            if g.isMultipart():
                p = g.asMultiPoint()[0]
            else:
                p = g.asPoint()
            return (p.x(), p.y())

        par, adv = geo.find_profilpar([(f.id(), f[kode_felt]) for f in pkt_feats],
                                      startkode, slutkode)
        advarsler += adv
        if not par:
            self._fejl("Der blev ikke fundet nogen profiler.\nTjek at feltet '%s' "
                       "indeholder '%s' og '%s'." % (kode_felt, startkode, slutkode))
            return None

        # --- tværprofiler
        profiler = []  # dicts
        for nr, (i, j) in enumerate(par, start=1):
            A, B = xy(pkt_feats[i]), xy(pkt_feats[j])
            if geo._dist(A, B) < 1e-6:
                advarsler.append("Profil fra punkt %s har samme start og slut - springes over"
                                 % pkt_feats[i].id())
                continue
            midt = ((A[0] + B[0]) / 2, (A[1] + B[1]) / 2)
            bedst = None
            for ci, c in enumerate(centre):
                for s, t_ab, pt in c["poly"].skaering_med_segment(A, B):
                    afst = geo._dist(pt, midt)
                    if bedst is None or afst < bedst[0]:
                        bedst = (afst, ci, s)
            krydser = 1
            if bedst is None:
                krydser = 0
                for ci, c in enumerate(centre):
                    s, dd = c["poly"].lokaliser(midt)
                    if bedst is None or abs(dd) < bedst[0]:
                        bedst = (abs(dd), ci, s)
                advarsler.append("Profil %d krydser ikke River centerlinje - "
                                 "River/Reach taget fra nærmeste linje" % nr)
            _, ci, s = bedst
            c = centre[ci]
            profiler.append({
                "nr": nr, "A": A, "B": B, "ci": ci, "s": s, "krydser": krydser,
                "river": c["river"], "reach": c["reach"],
                "start_id": pkt_feats[i].id(), "slut_id": pkt_feats[j].id(),
            })

        # HEC-RAS river station: afstand fra nedstrøms ende (linjens slut)
        for p in profiler:
            p["rs"] = centre[p["ci"]]["poly"].laengde - p["s"]

        profil_lag = QgsVectorLayer("LineString", "Tvaerprofiler", "memory")
        profil_lag.setCrs(crs)
        pf = QgsFields()
        for fld in (_felt("ProfilNr", "int"), _felt("River", "string", 80),
                    _felt("Reach", "string", 80)):
            pf.append(fld)
        profil_lag.dataProvider().addAttributes(pf.toList())
        profil_lag.updateFields()
        feats = []
        for p in profiler:
            f = QgsFeature(profil_lag.fields())
            f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(*p["A"]), QgsPointXY(*p["B"])]))
            f.setAttributes([p["nr"], _txt(p["river"]), _txt(p["reach"])])
            feats.append(f)
        profil_lag.dataProvider().addFeatures(feats)
        profil_lag.updateExtents()

        # --- bank lines
        bank_lag = QgsVectorLayer("LineString", "Bank lines", "memory")
        bank_lag.setCrs(crs)
        bf = QgsFields()
        for fld in (_felt("River", "string", 80), _felt("Reach", "string", 80)):
            bf.append(fld)
        bank_lag.dataProvider().addAttributes(bf.toList())
        bank_lag.updateFields()
        bfeats = []
        for ci, c in enumerate(centre):
            egne = [p for p in profiler if p["ci"] == ci]
            if len(egne) < 2:
                if egne:
                    advarsler.append("River %s / Reach %s har kun ét profil - ingen bank lines"
                                     % (_txt(c["river"]), _txt(c["reach"])))
                continue
            poly = c["poly"]
            taet = max(poly.laengde / 2000.0, 0.5)
            for noegle in ("A", "B"):     # A = venstre, B = højre
                pkt = geo.byg_banklinje(poly, [(p["s"], p[noegle]) for p in egne], taet)
                f = QgsFeature(bank_lag.fields())
                f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in pkt]))
                f.setAttributes([_txt(c["river"]), _txt(c["reach"])])
                bfeats.append(f)
        bank_lag.dataProvider().addFeatures(bfeats)
        bank_lag.updateExtents()

        # --- Hecrasopmaaling
        har_z = QgsWkbTypes.hasZ(pkt_lag.wkbType())
        hec_lag = QgsVectorLayer("PointZ" if har_z else "Point", "Hecrasopmaaling", "memory")
        hec_lag.setCrs(crs)
        orig_navne = [f.name() for f in pkt_lag.fields()]
        brugte = set(n.lower() for n in orig_navne)
        nye = []
        # Station = afstand langs tværprofilet fra startpunktet (12PROFILSTARTV)
        for navn, typ, l in (("ProfilNr", "int", 0), ("River", "string", 80),
                             ("Reach", "string", 80), ("RS", "double", 0),
                             ("Station", "double", 0)) + ((("Z", "double", 0),) if har_z else ()):
            ny = navn
            k = 1
            while ny.lower() in brugte:
                ny = ("%s_%d" % (navn, k))[:10]
                k += 1
            brugte.add(ny.lower())
            nye.append(_felt(ny, typ, l))
        hf = QgsFields()
        for fld in pkt_lag.fields():
            hf.append(QgsField(fld))
        for fld in nye:
            hf.append(fld)
        hec_lag.dataProvider().addAttributes(hf.toList())
        hec_lag.updateFields()

        hfeats = []
        for f in pkt_feats:
            P = xy(f)
            bedst = None
            for p in profiler:
                q, afst, st = geo.naermeste_paa_polylinje(P, [p["A"], p["B"]])
                if bedst is None or afst < bedst[1]:
                    bedst = (q, afst, st, p)
            q, afst, st, p = bedst
            nf = QgsFeature(hec_lag.fields())
            attrs = list(f.attributes())
            ekstra = [p["nr"], _txt(p["river"]), _txt(p["reach"]), round(p["rs"], 3),
                      round(st, 3)]
            if har_z:
                z = _z(f.geometry())
                ekstra.append(z)
                nf.setGeometry(QgsGeometry(QgsPoint(q[0], q[1], z if z is not None else 0.0)))
            else:
                nf.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(q[0], q[1])))
            nf.setAttributes(attrs + ekstra)
            hfeats.append(nf)
        hec_lag.dataProvider().addFeatures(hfeats)
        hec_lag.updateExtents()

        return profil_lag, bank_lag, hec_lag, advarsler

    # -----------------------------------------------------------------
    def _gem_og_tilfoej(self, lag, sti, farve):
        projekt = QgsProject.instance()
        nyt = lag
        if sti:
            ext = os.path.splitext(sti)[1].lower()
            if ext not in (".shp", ".gpkg"):
                sti += ".shp"
                ext = ".shp"
            opt = QgsVectorFileWriter.SaveVectorOptions()
            opt.driverName = "GPKG" if ext == ".gpkg" else "ESRI Shapefile"
            opt.fileEncoding = "UTF-8"
            lagnavn = lag.name().replace(" ", "_")
            if ext == ".gpkg":
                opt.layerName = lagnavn
                if os.path.exists(sti):
                    opt.actionOnExistingFile = _enum(QgsVectorFileWriter, "ActionOnExistingFile", "CreateOrOverwriteLayer")
            res = QgsVectorFileWriter.writeAsVectorFormatV3(lag, sti, projekt.transformContext(), opt)
            if res[0] != _enum(QgsVectorFileWriter, "WriterError", "NoError"):
                self._fejl("Kunne ikke gemme '%s':\n%s" % (lag.name(), res[1]))
            else:
                uri = sti if ext == ".shp" else "%s|layername=%s" % (sti, lagnavn)
                gemt = QgsVectorLayer(uri, lag.name(), "ogr")
                if gemt.isValid():
                    nyt = gemt
        try:
            sym = nyt.renderer().symbol()
            sym.setColor(QColor(farve))
            if nyt.geometryType() == _linjetype():
                sym.setWidth(0.6)
        except Exception:
            pass
        projekt.addMapLayer(nyt)
        return nyt


def _linjetype():
    try:
        from qgis.core import Qgis
        return Qgis.GeometryType.Line
    except Exception:
        return QgsWkbTypes.LineGeometry


def _txt(v):
    if v is None:
        return None
    try:
        from qgis.core import NULL
        if v == NULL:
            return None
    except Exception:
        pass
    return str(v)


def _z(geom):
    try:
        abs_ = geom.constGet()
        if hasattr(abs_, "geometryN"):
            abs_ = abs_.geometryN(0)
        return abs_.z()
    except Exception:
        return None


def skriv_rs_ogr(sti, lagnavn, nye):
    """
    Skriv RS-værdier direkte i filen med GDAL/OGR og luk filen bagefter,
    så værdierne med sikkerhed ligger i .dbf (eller .gpkg) på disken.
    nye: {fid: rs-værdi}. Punkter uden værdi får tom (NULL) RS.
    Returnerer (antal med RS, antal uden RS, advarsler).
    """
    from osgeo import ogr
    advarsler = []
    ds = ogr.Open(sti, 1)
    if ds is None:
        raise RuntimeError("Filen kunne ikke åbnes med skriveadgang - er den åben i et "
                           "andet program (fx Excel)?\n%s" % sti)
    lyr = None
    try:
        if lagnavn:
            lyr = ds.GetLayerByName(lagnavn)
        if lyr is None:
            lyr = ds.GetLayer(0)
        defn = lyr.GetLayerDefn()
        idx = -1
        for i in range(defn.GetFieldCount()):
            if defn.GetFieldDefn(i).GetName().lower() == "rs":
                idx = i
                break
        if idx < 0:
            fd = ogr.FieldDefn("RS", ogr.OFTReal)
            fd.SetWidth(18)
            fd.SetPrecision(4)
            if lyr.CreateField(fd) != 0:
                raise RuntimeError("Feltet RS kunne ikke oprettes i %s" % sti)
            defn = lyr.GetLayerDefn()
            idx = defn.GetFieldIndex("RS")
        ftype = defn.GetFieldDefn(idx).GetType()
        heltal = ftype in (ogr.OFTInteger, ogr.OFTInteger64)
        numerisk = heltal or ftype == ogr.OFTReal
        if heltal:
            advarsler.append("Feltet RS er et heltalsfelt - decimaler i River Station afrundes")

        fids = []
        lyr.ResetReading()
        f = lyr.GetNextFeature()
        while f is not None:
            fids.append(f.GetFID())
            f = lyr.GetNextFeature()

        transaktion = bool(lyr.TestCapability(ogr.OLCTransactions))
        if transaktion:
            lyr.StartTransaction()
        antal = 0
        fejl = 0
        for fid in fids:
            f = lyr.GetFeature(fid)
            if f is None:
                continue
            v = nye.get(fid)
            if v is None:
                if hasattr(f, "SetFieldNull"):
                    f.SetFieldNull(idx)
                else:
                    f.UnsetField(idx)
            else:
                antal += 1
                if heltal:
                    f.SetField(idx, int(round(v)))
                elif numerisk:
                    f.SetField(idx, float(round(v, 4)))
                else:
                    f.SetField(idx, _rs_tekst(v))
            if lyr.SetFeature(f) != 0:
                fejl += 1
        if transaktion:
            lyr.CommitTransaction()
        lyr.SyncToDisk()
        ds.FlushCache()
        if fejl:
            raise RuntimeError("%d punkter kunne ikke skrives til %s" % (fejl, sti))
        return antal, len(fids) - antal, advarsler
    finally:
        lyr = None
        ds = None      # lukker filen, så alt er skrevet til disken


def skriv_csv(lag, sti):
    """Skriv alle felter i laget til en kommasepareret CSV (UTF-8 med BOM, så
    Excel viser æøå korrekt). Tomme værdier bliver tomme celler."""
    navne = [f.name() for f in lag.fields()]
    antal = 0
    with open(sti, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=",", quoting=csv.QUOTE_MINIMAL)
        w.writerow(navne)
        for f in lag.getFeatures():
            w.writerow([_csv_vaerdi(v) for v in f.attributes()])
            antal += 1
    return antal


def _csv_vaerdi(v):
    t = _txt(v)
    if t is None:
        return ""
    if isinstance(v, float):
        return ("%.6f" % v).rstrip("0").rstrip(".")
    return t


def _tal(v):
    """River Station som tal. HEC-RAS kan skrive fx '1234.5*' for interpolerede profiler."""
    t = _txt(v)
    if t is None:
        return None
    t = t.strip().replace("*", "").replace(",", ".").strip()
    try:
        return float(t)
    except ValueError:
        return None


def _rs_tekst(v):
    return ("%.4f" % v).rstrip("0").rstrip(".")


def _nrm(v):
    t = _txt(v)
    return t.strip().lower() if t is not None else ""
