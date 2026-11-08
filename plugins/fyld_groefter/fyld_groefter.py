# -*- coding: utf-8 -*-
"""
Fyld grøfter - QGIS plugin
Beregner hvor meget jord der skal til for at fylde grøfter op, ud fra en
streg pr. grøft og en terrænmodel. Volumen skrives i et felt i streglaget.
"""

import math
import os

import numpy as np

from qgis.PyQt.QtGui import QIcon
try:  # Qt6 / QGIS 4: QAction ligger i QtGui
    from qgis.PyQt.QtGui import QAction
except ImportError:  # Qt5 / QGIS 3
    from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtWidgets import (QApplication, QCheckBox, QComboBox,
                                 QDialog, QDoubleSpinBox, QFormLayout,
                                 QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                                 QMenu, QMessageBox, QProgressBar, QPushButton,
                                 QVBoxLayout)
from qgis.core import (Qgis, QgsCoordinateTransform, QgsFeature, QgsField,
                       QgsGeometry, QgsMapLayerProxyModel, QgsPointXY,
                       QgsProject, QgsVectorDataProvider, QgsVectorLayer)
from qgis.gui import QgsMapLayerComboBox

from .beregning import (FYLD_GNS_KANT, FYLD_LAVESTE_KANT, FYLD_RET_LINJE,
                        Parametre, beregn_tvaersnit, samlet_volumen)
from .raster_laeser import RasterLaeser

MENU_TITEL = "Anlægsberegning"
MENU_OBJEKTNAVN = "mAnlaegsberegning"
TVAERSNIT_PR_BLOK = 100


# ---------------------------------------------------------------- hjælpere
def _lagfilter(navn):
    for kilde in (getattr(Qgis, "LayerFilter", None),
                  getattr(QgsMapLayerProxyModel, "Filter", None),
                  QgsMapLayerProxyModel):
        if kilde is not None and hasattr(kilde, navn):
            return getattr(kilde, navn)
    return None


def _double_felt(navn):
    # QGIS >= 3.38 og QGIS 4 bruger QMetaType; ældre QGIS 3 bruger QVariant
    try:
        from qgis.PyQt.QtCore import QMetaType
        return QgsField(navn, QMetaType.Type.Double, "double", 20, 3)
    except (AttributeError, TypeError, ImportError):
        from qgis.PyQt.QtCore import QVariant
        return QgsField(navn, QVariant.Double, "double", 20, 3)


def _cap(navn):
    for kilde in (getattr(Qgis, "VectorProviderCapability", None),
                  getattr(QgsVectorDataProvider, "Capability", None),
                  QgsVectorDataProvider):
        if kilde is not None and hasattr(kilde, navn):
            return getattr(kilde, navn)
    return None


def _kan(cap, navn):
    """True hvis provideren har evnen. Ukendt navn = antag ja (QGIS afviser selv)."""
    flag = _cap(navn)
    if flag is None:
        return True
    try:
        return bool(cap & flag)
    except TypeError:
        return True


def _niveau(navn):
    # QGIS 4: Qgis.MessageLevel.Warning ... ; gamle QGIS 3: Qgis.Warning
    kilde = getattr(Qgis, "MessageLevel", None)
    if kilde is not None and hasattr(kilde, navn):
        return getattr(kilde, navn)
    return getattr(Qgis, navn)


def _info(iface, tekst, niveau="info"):
    lvl = _niveau({"info": "Info", "ok": "Success", "advarsel": "Warning"}[niveau])
    try:
        iface.messageBar().pushMessage("Fyld grøfter", tekst, lvl, 10)
    except Exception:
        pass


# ---------------------------------------------------------------- dialog
class FyldDialog(QDialog):
    def __init__(self, iface, parent=None):
        super(FyldDialog, self).__init__(parent)
        self.iface = iface
        self.afbryd = False
        self.setWindowTitle("Fyld grøfter")
        self.setMinimumWidth(460)

        lay = QVBoxLayout(self)

        # --- input
        gb_ind = QGroupBox("Data")
        f = QFormLayout(gb_ind)
        self.cb_linje = QgsMapLayerComboBox()
        flt = _lagfilter("LineLayer")
        if flt is not None:
            self.cb_linje.setFilters(flt)
        self.chk_valgte = QCheckBox("Kun valgte objekter")
        self.cb_dem = QgsMapLayerComboBox()
        flt = _lagfilter("RasterLayer")
        if flt is not None:
            self.cb_dem.setFilters(flt)
        self.ed_felt = QLineEdit("fyld_m3")
        f.addRow("Grøfter (streglag):", self.cb_linje)
        f.addRow("", self.chk_valgte)
        f.addRow("Terrænmodel:", self.cb_dem)
        f.addRow("Felt til volumen (m³):", self.ed_felt)
        lay.addWidget(gb_ind)

        # --- parametre
        gb_par = QGroupBox("Bestemmelse af grøftekant")
        f2 = QFormLayout(gb_par)

        def spin(v, mi, ma, dec=2, step=0.1, suffix=" m"):
            s = QDoubleSpinBox()
            s.setDecimals(dec); s.setRange(mi, ma); s.setSingleStep(step)
            s.setValue(v); s.setSuffix(suffix)
            return s

        self.sp_afst = spin(1.0, 0.1, 100.0)
        self.sp_proeve = spin(0.4, 0.05, 10.0)
        self.sp_bund = spin(3.0, 0.0, 50.0)
        self.sp_bredde = spin(10.0, 0.5, 100.0)
        self.sp_haeld = spin(10.0, 0.5, 100.0, 1, 1.0, " %")
        self.sp_flad = spin(1.0, 0.1, 20.0)
        self.sp_top = spin(0.05, 0.0, 2.0, 2, 0.01)
        self.sp_dybde = spin(0.10, 0.0, 5.0, 2, 0.05)
        self.sp_glat = spin(1.0, 0.0, 10.0)
        self.cb_fyld = QComboBox()
        self.cb_fyld.addItem("Ret linje mellem kanterne (genskab terræn)", FYLD_RET_LINJE)
        self.cb_fyld.addItem("Vandret til laveste kant", FYLD_LAVESTE_KANT)
        self.cb_fyld.addItem("Vandret til gennemsnit af kanterne", FYLD_GNS_KANT)

        f2.addRow("Afstand mellem tværsnit:", self.sp_afst)
        f2.addRow("Prøveafstand på tværs:", self.sp_proeve)
        f2.addRow("Søg bund inden for ± fra streg:", self.sp_bund)
        f2.addRow("Maks. afstand bund → kant:", self.sp_bredde)
        f2.addRow("Terræn fladere end (kant nået):", self.sp_haeld)
        f2.addRow("…over en længde på:", self.sp_flad)
        f2.addRow("Fald efter top (vold/opkast):", self.sp_top)
        f2.addRow("Min. grøftedybde:", self.sp_dybde)
        f2.addRow("Udglatning af terræn:", self.sp_glat)
        f2.addRow("Fyld op til:", self.cb_fyld)
        lay.addWidget(gb_par)

        # --- output
        gb_ud = QGroupBox("Resultat")
        v = QVBoxLayout(gb_ud)
        self.chk_ekstra = QCheckBox("Skriv også gns. bredde (fyld_brd), maks. dybde (fyld_dyb) "
                                    "og andel usikre tværsnit (fyld_usik)")
        self.chk_ekstra.setChecked(True)
        self.chk_kontrol = QCheckBox("Opret kontrollag med tværsnit og grøfteomrids")
        self.chk_kontrol.setChecked(True)
        v.addWidget(self.chk_ekstra)
        v.addWidget(self.chk_kontrol)
        lay.addWidget(gb_ud)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.lbl_status = QLabel("")
        lay.addWidget(self.progress)
        lay.addWidget(self.lbl_status)

        knapper = QHBoxLayout()
        self.btn_kør = QPushButton("Beregn fyld")
        self.btn_luk = QPushButton("Luk")
        knapper.addStretch()
        knapper.addWidget(self.btn_kør)
        knapper.addWidget(self.btn_luk)
        lay.addLayout(knapper)

        self.btn_kør.clicked.connect(self.koer)
        self.btn_luk.clicked.connect(self.luk)
        self.cb_dem.layerChanged.connect(self._dem_skiftet)
        self._dem_skiftet(self.cb_dem.currentLayer())

    def _dem_skiftet(self, lag):
        if lag is not None:
            try:
                px = abs(lag.rasterUnitsPerPixelX())
                if px > 0:
                    self.sp_proeve.setValue(max(0.05, round(px, 2)))
            except Exception:
                pass

    def luk(self):
        if not self.btn_kør.isEnabled():
            self.afbryd = True
        else:
            self.close()

    def parametre(self):
        return Parametre(bund_soeg=self.sp_bund.value(),
                         max_halvbredde=self.sp_bredde.value(),
                         min_haeldning=self.sp_haeld.value() / 100.0,
                         flad_laengde=self.sp_flad.value(),
                         top_tol=self.sp_top.value(),
                         min_dybde=self.sp_dybde.value(),
                         udglat=self.sp_glat.value(),
                         fyldmetode=self.cb_fyld.currentData())

    # ------------------------------------------------------------ kørsel
    def koer(self):
        linje = self.cb_linje.currentLayer()
        dem = self.cb_dem.currentLayer()
        felt = self.ed_felt.text().strip()
        if linje is None or dem is None:
            QMessageBox.warning(self, "Fyld grøfter", "Vælg både streglag og terrænmodel.")
            return
        if not felt:
            QMessageBox.warning(self, "Fyld grøfter", "Angiv et feltnavn til volumen.")
            return
        if dem.crs().isGeographic():
            QMessageBox.warning(self, "Fyld grøfter",
                                "Terrænmodellen er i et geografisk koordinatsystem (grader). "
                                "Brug en terrænmodel i et metrisk system, fx ETRS89/UTM32.")
            return

        self.afbryd = False
        self.btn_kør.setEnabled(False)
        self.btn_luk.setText("Afbryd")
        try:
            Beregner(self, linje, dem, felt).koer()
        except Exception as e:
            import traceback
            QMessageBox.critical(self, "Fyld grøfter",
                                 "Der opstod en fejl:\n%s\n\n%s" % (e, traceback.format_exc()))
        finally:
            self.btn_kør.setEnabled(True)
            self.btn_luk.setText("Luk")


# ---------------------------------------------------------------- beregning
class Beregner(object):
    def __init__(self, dlg, linje, dem, felt):
        self.dlg = dlg
        self.linje = linje
        self.dem = dem
        self.felt = felt
        self.p = dlg.parametre()
        self.afst = dlg.sp_afst.value()
        self.ds = dlg.sp_proeve.value()
        self.ekstra = dlg.chk_ekstra.isChecked()
        self.kontrol = dlg.chk_kontrol.isChecked()
        self.laeser = RasterLaeser(dem)
        # tværprofilets afstande, s = 0 på stregen
        halv = self.p.bund_soeg + self.p.max_halvbredde + self.p.udglat
        n = int(math.ceil(halv / self.ds))
        self.s = np.arange(-n, n + 1) * self.ds
        self.trans = None
        if linje.crs() != dem.crs():
            self.trans = QgsCoordinateTransform(linje.crs(), dem.crs(), QgsProject.instance())
        self.tv_feat = []
        self.omr_feat = []

    # -------------------------------------------- felter
    def _sikr_felter(self):
        prov = self.linje.dataProvider()
        cap = prov.capabilities()
        if not self.linje.isEditable():
            if not _kan(cap, "ChangeAttributeValues"):
                raise RuntimeError("Streglaget kan ikke redigeres (formatet tillader ikke ændring af attributter).")
        navne = [self.felt]
        if self.ekstra:
            navne += ["fyld_brd", "fyld_dyb", "fyld_usik"]
        mangler = [n for n in navne if self.linje.fields().indexFromName(n) < 0]
        if mangler:
            nye = [_double_felt(n) for n in mangler]
            if self.linje.isEditable():
                for nf in nye:
                    self.linje.addAttribute(nf)
            else:
                if not _kan(cap, "AddAttributes"):
                    raise RuntimeError("Feltet '%s' findes ikke, og laget tillader ikke nye felter."
                                       % ", ".join(mangler))
                prov.addAttributes(nye)
                self.linje.updateFields()
        return {n: self.linje.fields().indexFromName(n) for n in navne}

    # -------------------------------------------- én del af en streg
    def _beregn_del(self, g, fid):
        """g: QgsGeometry (enkelt linjestykke) i rasterets CRS."""
        L = g.length()
        if L <= 0:
            return None
        n = max(1, int(math.ceil(L / self.afst)))
        dst = L / n
        stationer = (np.arange(n) + 0.5) * dst
        arealer = np.full(n, np.nan)
        dybder, bredder, usikre = [], [], 0
        venstre_pkt, hoejre_pkt = [], []

        for b0 in range(0, n, TVAERSNIT_PR_BLOK):
            st = stationer[b0:b0 + TVAERSNIT_PR_BLOK]
            px, py, nx, ny = [], [], [], []
            for d in st:
                pt = g.interpolate(float(d)).asPoint()
                a = g.interpolateAngle(float(d))   # radianer, med uret fra nord
                px.append(pt.x()); py.append(pt.y())
                nx.append(-math.cos(a)); ny.append(math.sin(a))  # normal mod venstre
            px = np.array(px)[:, None]; py = np.array(py)[:, None]
            nx = np.array(nx)[:, None]; ny = np.array(ny)[:, None]
            X = px + self.s[None, :] * nx
            Y = py + self.s[None, :] * ny
            Z = self.laeser.aflaes(X.ravel(), Y.ravel()).reshape(X.shape)

            for k in range(len(st)):
                r = beregn_tvaersnit(self.s, Z[k], self.p)
                if not r["ok"]:
                    continue
                arealer[b0 + k] = r["areal"]
                if not r["sikker"]:
                    usikre += 1
                if r["areal"] > 0:
                    dybder.append(r["dybde"]); bredder.append(r["bredde"])
                vi, hi = r["venstre_i"], r["hoejre_i"]
                pv = QgsPointXY(px[k, 0] + self.s[vi] * nx[k, 0], py[k, 0] + self.s[vi] * ny[k, 0])
                ph = QgsPointXY(px[k, 0] + self.s[hi] * nx[k, 0], py[k, 0] + self.s[hi] * ny[k, 0])
                if r["areal"] > 0:
                    venstre_pkt.append(pv); hoejre_pkt.append(ph)
                if self.kontrol:
                    tf = QgsFeature()
                    tf.setGeometry(QgsGeometry.fromPolylineXY([ph, pv]))
                    tf.setAttributes([int(fid), round(float(st[k]), 2), round(r["areal"], 3),
                                      round(r["dybde"], 3), round(r["bredde"], 2),
                                      1 if r["sikker"] else 0])
                    self.tv_feat.append(tf)
            QApplication.processEvents()
            if self.dlg.afbryd:
                raise KeyboardInterrupt()

        vol = samlet_volumen(arealer, L)
        if self.kontrol and len(venstre_pkt) >= 2:
            ring = venstre_pkt + hoejre_pkt[::-1] + [venstre_pkt[0]]
            pg = QgsGeometry.fromPolygonXY([ring])
            if not pg.isGeosValid():
                pg = pg.makeValid()
            of = QgsFeature()
            of.setGeometry(pg)
            of.setAttributes([int(fid), round(vol or 0.0, 2)])
            self.omr_feat.append(of)
        return dict(vol=vol, n=n, usikre=usikre, ok=int(np.isfinite(arealer).sum()),
                    dybder=dybder, bredder=bredder)

    # -------------------------------------------- hele laget
    def koer(self):
        dlg = self.dlg
        idx = self._sikr_felter()
        if dlg.chk_valgte.isChecked():
            features = list(self.linje.getSelectedFeatures())
        else:
            features = list(self.linje.getFeatures())
        if not features:
            QMessageBox.information(dlg, "Fyld grøfter", "Ingen objekter at beregne.")
            return

        dlg.progress.setMaximum(len(features))
        aendringer = {}
        total = 0.0
        uden_data = 0
        try:
            for i, feat in enumerate(features):
                dlg.lbl_status.setText("Grøft %d af %d (fid %d)" % (i + 1, len(features), feat.id()))
                dlg.progress.setValue(i)
                QApplication.processEvents()
                geom = QgsGeometry(feat.geometry())
                vaerdier = {}
                if geom is None or geom.isEmpty():
                    vaerdier[idx[self.felt]] = None
                else:
                    if self.trans is not None:
                        geom.transform(self.trans)
                    vol_sum, n_sum, usikre, ok_sum = 0.0, 0, 0, 0
                    dyb, brd = [], []
                    nogen = False
                    for del_ in geom.constParts():
                        dg = QgsGeometry(del_.clone())
                        r =self._beregn_del(dg, feat.id())
                        if r is None:
                            continue
                        n_sum += r["n"]; usikre += r["usikre"]; ok_sum += r["ok"]
                        dyb += r["dybder"]; brd += r["bredder"]
                        if r["vol"] is not None:
                            vol_sum += r["vol"]; nogen = True
                    if nogen:
                        vaerdier[idx[self.felt]] = round(vol_sum, 2)
                        total += vol_sum
                    else:
                        vaerdier[idx[self.felt]] = None
                        uden_data += 1
                    if self.ekstra:
                        vaerdier[idx["fyld_brd"]] = round(float(np.mean(brd)), 2) if brd else 0.0
                        vaerdier[idx["fyld_dyb"]] = round(float(np.max(dyb)), 2) if dyb else 0.0
                        vaerdier[idx["fyld_usik"]] = (round(100.0 * usikre / ok_sum, 1)
                                                      if ok_sum else None)
                aendringer[feat.id()] = vaerdier
        except KeyboardInterrupt:
            dlg.lbl_status.setText("Afbrudt - intet er skrevet til laget.")
            dlg.progress.setValue(0)
            return

        # skriv resultater
        if self.linje.isEditable():
            for fid, v in aendringer.items():
                for fi, val in v.items():
                    self.linje.changeAttributeValue(fid, fi, val)
        else:
            self.linje.dataProvider().changeAttributeValues(aendringer)
            self.linje.reload()
        self.linje.triggerRepaint()

        if self.kontrol:
            self._opret_kontrollag()

        dlg.progress.setValue(len(features))
        tekst = "Beregnet %d grøfter. Samlet fyld: %.1f m³" % (len(features), total)
        if uden_data:
            tekst += " (%d uden terrændata)" % uden_data
        if self.linje.isEditable():
            tekst += ". Laget er i redigeringstilstand - husk at gemme."
        dlg.lbl_status.setText(tekst)
        _info(dlg.iface, tekst, "ok")

    def _opret_kontrollag(self):
        crs = self.dem.crs().authid() or self.dem.crs().toWkt()
        navn = self.linje.name()
        tv = QgsVectorLayer("LineString?crs=%s&field=groeft_fid:integer&field=station:double"
                            "&field=areal_m2:double&field=dybde:double&field=bredde:double"
                            "&field=sikker:integer" % crs,
                            "Fyld tværsnit - %s" % navn, "memory")
        tv.dataProvider().addFeatures(self.tv_feat)
        tv.updateExtents()
        omr = QgsVectorLayer("Polygon?crs=%s&field=groeft_fid:integer&field=fyld_m3:double" % crs,
                             "Fyld omrids - %s" % navn, "memory")
        omr.dataProvider().addFeatures(self.omr_feat)
        omr.updateExtents()
        try:
            from qgis.core import (QgsCategorizedSymbolRenderer, QgsRendererCategory,
                                   QgsLineSymbol)
            gr = QgsLineSymbol.createSimple({"color": "30,120,220", "width": "0.3"})
            rd = QgsLineSymbol.createSimple({"color": "230,40,40", "width": "0.4"})
            tv.setRenderer(QgsCategorizedSymbolRenderer("sikker", [
                QgsRendererCategory(1, gr, "Kant fundet"),
                QgsRendererCategory(0, rd, "Usikker kant")]))
            from qgis.core import QgsFillSymbol
            omr.renderer().setSymbol(QgsFillSymbol.createSimple(
                {"color": "255,170,0,70", "outline_color": "200,110,0", "outline_width": "0.4"}))
        except Exception:
            pass
        QgsProject.instance().addMapLayer(omr)
        QgsProject.instance().addMapLayer(tv)


# ---------------------------------------------------------------- plugin
def _menu_titel(tekst):
    return (tekst or "").replace("&", "").strip().lower()


def find_anlaegsmenu(iface, opret=True):
    """
    Finder menuen "Anlægsberegning" i QGIS' menulinje (oprettet af dette eller et
    andet anlægsplugin, fx Grav grøfter / Grav Skrab). Opretter den, hvis den mangler.
    """
    menulinje = iface.mainWindow().menuBar()
    for handling in menulinje.actions():
        m = handling.menu()
        if m is None:
            continue
        if (m.objectName() == MENU_OBJEKTNAVN or
                _menu_titel(m.title()) == _menu_titel(MENU_TITEL)):
            return m
    if not opret:
        return None
    m = QMenu(MENU_TITEL, iface.mainWindow())
    m.setObjectName(MENU_OBJEKTNAVN)
    # placér menuen før "Hjælp", ellers sidst
    foer = None
    try:
        hjaelp = iface.firstRightStandardMenu()
        if hjaelp is not None:
            foer = hjaelp.menuAction()
    except Exception:
        foer = None
    if foer is not None:
        menulinje.insertMenu(foer, m)
    else:
        menulinje.addMenu(m)
    return m


class FyldGroefter(object):
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dlg = None
        self.menu = None

    def initGui(self):
        ikon = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        self.action = QAction(ikon, "Fyld grøfter", self.iface.mainWindow())
        self.action.setObjectName("FyldGroefterAction")
        self.action.setToolTip("Beregn fyldvolumen for grøfter ud fra terrænmodel")
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.menu = find_anlaegsmenu(self.iface)
        self.menu.addAction(self.action)

    def unload(self):
        if self.action:
            self.iface.removeToolBarIcon(self.action)
            menu = find_anlaegsmenu(self.iface, opret=False)
            if menu is not None:
                menu.removeAction(self.action)
                # fjern menuen helt, hvis intet andet plugin bruger den længere
                if not menu.actions():
                    self.iface.mainWindow().menuBar().removeAction(menu.menuAction())
                    menu.deleteLater()
            self.menu = None
            self.action.deleteLater()
            self.action = None
        if self.dlg is not None:
            self.dlg.close()
            self.dlg.deleteLater()
            self.dlg = None

    def run(self):
        if self.dlg is None:
            self.dlg = FyldDialog(self.iface, self.iface.mainWindow())
        self.dlg.show()
        self.dlg.raise_()
        self.dlg.activateWindow()
