# -*- coding: utf-8 -*-
"""
Grav Skrab - QGIS plugin

Beregner jordmængde (m3) og middeldybde for skrab, hvor polygonlaget angiver
bundens udbredelse og bundkoten står i et felt. Siderne anlægges med 1:5.
Resultatet skrives i felterne "jordmgd" og "Dybde", og der laves et nyt
polygonlag med skrabets øverste udbredelse og areal.
"""
import math
import os

import numpy as np

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
try:  # Qt6 / QGIS 4: QAction ligger i QtGui
    from qgis.PyQt.QtGui import QAction
except ImportError:  # Qt5 / QGIS 3
    from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QLabel, QMenu, QMessageBox, QProgressDialog, QVBoxLayout,
)
from qgis.core import (
    Qgis, QgsApplication, QgsCoordinateTransform, QgsFeature, QgsField,
    QgsFieldProxyModel, QgsGeometry, QgsMapLayerProxyModel, QgsMessageLog,
    QgsProject, QgsRectangle, QgsVectorFileWriter, QgsVectorLayer,
)
from qgis.gui import QgsFieldComboBox, QgsFileWidget, QgsMapLayerComboBox

from .beregning import beregn_skrab

MENU_NAVN = "Anlægsberegning"
MENU_OBJ = "mAnlaegsberegningMenu"
LOG_TAG = "Grav Skrab"
MAKS_UDVIDELSE = 2000.0  # m - største afstand fra bunden der søges ud til


# --------------------------------------------------------------------------
# Kompatibilitet QGIS 3.x / 4.x
# --------------------------------------------------------------------------
def _enum(*kandidater):
    for obj, navne in kandidater:
        o = obj
        try:
            for n in navne.split("."):
                o = getattr(o, n)
            return o
        except AttributeError:
            continue
    return None


def _lagfilter(navn):
    return _enum((Qgis, "LayerFilter." + navn), (QgsMapLayerProxyModel, "Filter." + navn),
                 (QgsMapLayerProxyModel, navn))


def _feltfilter_numerisk():
    return _enum((QgsFieldProxyModel, "Filter.Numeric"), (QgsFieldProxyModel, "Numeric"))


if Qgis.versionInt() >= 33800:
    from qgis.PyQt.QtCore import QMetaType
    T_DOUBLE = QMetaType.Type.Double
    T_INT = QMetaType.Type.LongLong
else:
    from qgis.PyQt.QtCore import QVariant
    T_DOUBLE = QVariant.Double
    T_INT = QVariant.LongLong


def _np_dtype(dt):
    base = getattr(Qgis, "DataType", Qgis)
    for navn, npdt in (("Byte", np.uint8), ("Int8", np.int8), ("UInt16", np.uint16),
                       ("Int16", np.int16), ("UInt32", np.uint32), ("Int32", np.int32),
                       ("Float32", np.float32), ("Float64", np.float64)):
        e = getattr(base, navn, None)
        if e is not None and dt == e:
            return npdt
    return None


NIV_INFO = _enum((Qgis, "MessageLevel.Info"), (Qgis, "Info"))
NIV_ADVARSEL = _enum((Qgis, "MessageLevel.Warning"), (Qgis, "Warning"))
NIV_KRITISK = _enum((Qgis, "MessageLevel.Critical"), (Qgis, "Critical"))
NIV_OK = _enum((Qgis, "MessageLevel.Success"), (Qgis, "Success"))


def _log(tekst, niveau=None):
    QgsMessageLog.logMessage(tekst, LOG_TAG, niveau if niveau is not None else NIV_INFO)


# --------------------------------------------------------------------------
# Raster
# --------------------------------------------------------------------------
class Terraen:
    """Læser udsnit af terrænmodellen som numpy-arrays."""

    def __init__(self, lag, celle=0.0):
        self.lag = lag
        self.prov = lag.dataProvider()
        self.ext = lag.extent()
        self.cw = celle if celle > 0 else lag.rasterUnitsPerPixelX()
        self.ch = celle if celle > 0 else lag.rasterUnitsPerPixelY()
        self.nodata = None
        if self.prov.sourceHasNoDataValue(1) and self.prov.useSourceNoDataValue(1):
            self.nodata = self.prov.sourceNoDataValue(1)

    def udsnit(self, rect):
        """Returnerer (T, gyldig, X, Y, beskaaret) for et udsnit flugtende med cellenettet."""
        x0r, y1r = self.ext.xMinimum(), self.ext.yMaximum()
        ncol_tot = int(round(self.ext.width() / self.cw))
        nrow_tot = int(round(self.ext.height() / self.ch))
        c0 = int(math.floor((rect.xMinimum() - x0r) / self.cw))
        c1 = int(math.ceil((rect.xMaximum() - x0r) / self.cw))
        r0 = int(math.floor((y1r - rect.yMaximum()) / self.ch))
        r1 = int(math.ceil((y1r - rect.yMinimum()) / self.ch))
        beskaaret = c0 < 0 or r0 < 0 or c1 > ncol_tot or r1 > nrow_tot
        c0, r0 = max(c0, 0), max(r0, 0)
        c1, r1 = min(c1, ncol_tot), min(r1, nrow_tot)
        if c1 <= c0 or r1 <= r0:
            return None
        ncol, nrow = c1 - c0, r1 - r0
        ext = QgsRectangle(x0r + c0 * self.cw, y1r - r1 * self.ch,
                           x0r + c1 * self.cw, y1r - r0 * self.ch)
        blok = self.prov.block(1, ext, ncol, nrow)
        npdt = _np_dtype(blok.dataType())
        T = None
        if npdt is not None:
            try:
                raw = blok.data()
                raw = raw.data() if hasattr(raw, "data") else bytes(raw)
                T = np.frombuffer(raw, dtype=npdt).reshape(nrow, ncol).astype(float)
            except Exception:
                T = None
        if T is None:  # langsom reserveløsning
            T = np.empty((nrow, ncol))
            for r in range(nrow):
                for c in range(ncol):
                    T[r, c] = np.nan if blok.isNoData(r, c) else blok.value(r, c)
        gyldig = np.isfinite(T)
        if self.nodata is not None:
            gyldig &= T != self.nodata
        if blok.hasNoDataValue():
            gyldig &= T != blok.noDataValue()

        xs = ext.xMinimum() + (np.arange(ncol) + 0.5) * self.cw
        ys = ext.yMaximum() - (np.arange(nrow) + 0.5) * self.ch
        X, Y = np.meshgrid(xs, ys)
        return T, gyldig, X, Y, beskaaret, ext


def _ringe(geom):
    if geom.isMultipart():
        polys = geom.asMultiPolygon()
    else:
        polys = [geom.asPolygon()]
    return [[(p.x(), p.y()) for p in ring] for poly in polys for ring in poly]


def _maske_til_geometri(maske, ext, cw, ch):
    from osgeo import gdal, ogr
    nrow, ncol = maske.shape
    ds = gdal.GetDriverByName("MEM").Create("", ncol, nrow, 1, gdal.GDT_Byte)
    ds.SetGeoTransform((ext.xMinimum(), cw, 0.0, ext.yMaximum(), 0.0, -ch))
    band = ds.GetRasterBand(1)
    band.WriteArray(maske.astype(np.uint8))
    drv = ogr.GetDriverByName("MEM") or ogr.GetDriverByName("Memory")
    ods = drv.CreateDataSource("skrab")
    lyr = ods.CreateLayer("p", geom_type=ogr.wkbPolygon)
    lyr.CreateField(ogr.FieldDefn("v", ogr.OFTInteger))
    gdal.Polygonize(band, band, lyr, 0, [], callback=None)
    geoms = []
    for f in lyr:
        if f.GetField(0) == 1:
            geoms.append(QgsGeometry.fromWkt(f.GetGeometryRef().ExportToWkt()))
    if not geoms:
        return QgsGeometry()
    return QgsGeometry.unaryUnion(geoms)


# --------------------------------------------------------------------------
# Dialog
# --------------------------------------------------------------------------
class GravSkrabDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Grav Skrab")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        form = QFormLayout()

        self.cmbPoly = QgsMapLayerComboBox()
        self.cmbPoly.setFilters(_lagfilter("PolygonLayer"))
        form.addRow("Skrab (bundpolygoner):", self.cmbPoly)

        self.cmbFelt = QgsFieldComboBox()
        f = _feltfilter_numerisk()
        if f is not None:
            self.cmbFelt.setFilters(f)
        form.addRow("Felt med bundkote:", self.cmbFelt)

        self.cmbNavn = QgsFieldComboBox()
        self.cmbNavn.setAllowEmptyFieldName(True)
        form.addRow("Navnefelt (valgfrit):", self.cmbNavn)

        self.cmbVand = QgsFieldComboBox()
        if f is not None:
            self.cmbVand.setFilters(f)
        self.cmbVand.setAllowEmptyFieldName(True)
        form.addRow("Felt med vandspejlskote:", self.cmbVand)

        self.chkValgte = QCheckBox("Kun valgte objekter")
        form.addRow("", self.chkValgte)

        self.cmbDem = QgsMapLayerComboBox()
        self.cmbDem.setFilters(_lagfilter("RasterLayer"))
        form.addRow("Terrænmodel:", self.cmbDem)

        self.spAnlaeg = QDoubleSpinBox()
        self.spAnlaeg.setRange(0.1, 100.0)
        self.spAnlaeg.setDecimals(2)
        self.spAnlaeg.setValue(5.0)
        self.spAnlaeg.setPrefix("1 : ")
        form.addRow("Anlæg:", self.spAnlaeg)

        self.spCelle = QDoubleSpinBox()
        self.spCelle.setRange(0.0, 50.0)
        self.spCelle.setDecimals(2)
        self.spCelle.setSingleStep(0.1)
        self.spCelle.setValue(0.0)
        self.spCelle.setSpecialValueText("Terrænmodellens opløsning")
        self.spCelle.setSuffix(" m")
        form.addRow("Beregningscelle:", self.spCelle)

        self.filUd = QgsFileWidget()
        sm = _enum((QgsFileWidget, "StorageMode.SaveFile"), (QgsFileWidget, "SaveFile"))
        self.filUd.setStorageMode(sm)
        self.filUd.setFilter("GeoPackage (*.gpkg);;ESRI Shapefile (*.shp)")
        self.filUd.lineEdit().setPlaceholderText("[Midlertidigt lag]")
        form.addRow("Gem øverste udbredelse:", self.filUd)

        lay.addLayout(form)
        info = QLabel("Resultat skrives i felterne <b>jordmgd</b> (m³), <b>Dybde</b> (m) og "
                      "<b>VandVol</b> (m³) samt <b>LavTer</b> (m) i skrablaget. Dybde = middeldybden over "
                      "skrabets bund. LavTer = laveste terrænkote langs skrabets øverste kant. "
                      "VandVol = volumen under vandspejlskoten - dog højst op til LavTer.")
        info.setWordWrap(True)
        lay.addWidget(info)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        self.cmbPoly.layerChanged.connect(self._lag_skiftet)
        self._lag_skiftet(self.cmbPoly.currentLayer())

    def _lag_skiftet(self, lag):
        self.cmbFelt.setLayer(lag)
        self.cmbNavn.setLayer(lag)
        self.cmbNavn.setField("")
        self.cmbVand.setLayer(lag)
        self.cmbVand.setField("")
        if lag is not None:
            for navn in ("bundkote", "bund", "kote", "bk"):
                idx = lag.fields().lookupField(navn)
                if idx >= 0:
                    self.cmbFelt.setField(lag.fields()[idx].name())
                    break
            for navn in ("vandspejl", "vandspejlskote", "vsp", "vandkote", "wl", "vs"):
                idx = lag.fields().lookupField(navn)
                if idx >= 0:
                    self.cmbVand.setField(lag.fields()[idx].name())
                    break
            for navn in ("navn", "name", "skrab", "id"):
                idx = lag.fields().lookupField(navn)
                if idx >= 0:
                    self.cmbNavn.setField(lag.fields()[idx].name())
                    break
            self.chkValgte.setEnabled(lag.selectedFeatureCount() > 0)
            if lag.selectedFeatureCount() == 0:
                self.chkValgte.setChecked(False)


# --------------------------------------------------------------------------
# Plugin
# --------------------------------------------------------------------------
class GravSkrab:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.menu = None

    # ---- menu ----
    def _find_eller_opret_menu(self):
        mb = self.iface.mainWindow().menuBar()
        for a in mb.actions():
            m = a.menu()
            if m is not None and (m.objectName() == MENU_OBJ
                                  or m.title().replace("&", "") == MENU_NAVN):
                return m
        m = QMenu(MENU_NAVN, mb)
        m.setObjectName(MENU_OBJ)
        try:
            foer = self.iface.firstRightStandardMenu().menuAction()
            mb.insertMenu(foer, m)
        except Exception:
            mb.addMenu(m)
        return m

    def initGui(self):
        ikon = QIcon(os.path.join(self.plugin_dir, "icon.png"))
        self.action = QAction(ikon, "Grav Skrab", self.iface.mainWindow())
        self.action.setObjectName("GravSkrabAction")
        self.action.triggered.connect(self.run)
        self.menu = self._find_eller_opret_menu()
        self.menu.addAction(self.action)

    def unload(self):
        if self.menu is not None and self.action is not None:
            self.menu.removeAction(self.action)
            if not self.menu.actions():
                self.iface.mainWindow().menuBar().removeAction(self.menu.menuAction())
                self.menu.deleteLater()
        self.action = None
        self.menu = None

    # ---- kørsel ----
    def run(self):
        dlg = GravSkrabDialog(self.iface.mainWindow())
        if not dlg.exec():
            return
        lag = dlg.cmbPoly.currentLayer()
        dem = dlg.cmbDem.currentLayer()
        felt = dlg.cmbFelt.currentField()
        if lag is None or dem is None or not felt:
            QMessageBox.warning(self.iface.mainWindow(), "Grav Skrab",
                                "Vælg skrablag, bundkotefelt og terrænmodel.")
            return
        if dem.crs().isGeographic():
            QMessageBox.warning(self.iface.mainWindow(), "Grav Skrab",
                                "Terrænmodellen skal være i et projiceret koordinatsystem (meter).")
            return
        try:
            self._beregn(lag, felt, dem, dlg.spAnlaeg.value(), dlg.spCelle.value(),
                         dlg.chkValgte.isChecked(), dlg.filUd.filePath().strip(),
                         dlg.cmbNavn.currentField(), dlg.cmbVand.currentField())
        except Exception as e:  # vis fejlen i stedet for et stacktrace-vindue
            import traceback
            _log(traceback.format_exc(), NIV_KRITISK)
            QMessageBox.critical(self.iface.mainWindow(), "Grav Skrab", "Beregningen fejlede:\n%s" % e)

    def _sikr_felter(self, lag, navne):
        prov = lag.dataProvider()
        mangler = [QgsField(n, T_DOUBLE, "double", 12, 2) for n in navne
                   if lag.fields().lookupField(n) < 0]
        if mangler:
            if lag.isEditable():
                for f in mangler:
                    lag.addAttribute(f)
            else:
                if not prov.addAttributes(mangler):
                    raise RuntimeError("Kunne ikke tilføje felter til %s" % lag.name())
                lag.updateFields()
        return [lag.fields().lookupField(n) for n in navne]

    def _beregn(self, lag, felt, dem, anlaeg, celle, kun_valgte, udfil, navnefelt="", vandfelt=""):
        projekt = QgsProject.instance()
        til_dem = QgsCoordinateTransform(lag.crs(), dem.crs(), projekt)
        fra_dem = QgsCoordinateTransform(dem.crs(), lag.crs(), projekt)
        terraen = Terraen(dem, celle)
        cw, ch = terraen.cw, terraen.ch
        celleareal = cw * ch
        margin = 3 * max(cw, ch)

        feats = list(lag.getSelectedFeatures() if kun_valgte else lag.getFeatures())
        resfelter = ["jordmgd", "Dybde", "LavTer"] + (["VandVol"] if vandfelt else [])
        idx_res = self._sikr_felter(lag, resfelter)

        ud = QgsVectorLayer("MultiPolygon", "Skrab øverste udbredelse", "memory")
        ud.setCrs(lag.crs())
        felter = []
        if navnefelt:
            nf_kilde = QgsField(lag.fields()[lag.fields().lookupField(navnefelt)])
            if nf_kilde.name().lower() in ("kilde_fid", "bundkote", "jordmgd", "dybde",
                                           "areal_bund", "areal_top", "lavter", "vandkote", "vand_brugt",
                                           "vandvol", "vandareal"):
                nf_kilde.setName("navn")
            felter.append(nf_kilde)
        ud.dataProvider().addAttributes(felter + [
            QgsField("kilde_fid", T_INT),
            QgsField("bundkote", T_DOUBLE, "double", 10, 2),
            QgsField("jordmgd", T_DOUBLE, "double", 12, 2),
            QgsField("Dybde", T_DOUBLE, "double", 10, 2),
            QgsField("areal_bund", T_DOUBLE, "double", 12, 1),
            QgsField("areal_top", T_DOUBLE, "double", 12, 1),
            QgsField("LavTer", T_DOUBLE, "double", 10, 2),
        ] + ([
            QgsField("vandkote", T_DOUBLE, "double", 10, 2),
            QgsField("vand_brugt", T_DOUBLE, "double", 10, 2),
            QgsField("VandVol", T_DOUBLE, "double", 12, 2),
            QgsField("vandareal", T_DOUBLE, "double", 12, 1),
        ] if vandfelt else []) + [
        ])
        ud.updateFields()

        prog = QProgressDialog("Beregner skrab...", "Annullér", 0, len(feats),
                               self.iface.mainWindow())
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setMinimumDuration(0)

        resultater = {}
        nye = []
        advarsler = []
        total = 0.0
        total_vand = 0.0

        for i, f in enumerate(feats):
            prog.setValue(i)
            QgsApplication.processEvents()
            if prog.wasCanceled():
                break
            zb = f[felt]
            geom = f.geometry()
            if zb is None or geom is None or geom.isEmpty() or (hasattr(zb, "isNull") and zb.isNull()):
                advarsler.append("Objekt %s: mangler geometri eller bundkote - sprunget over." % f.id())
                continue
            zb = float(zb)
            zw = None
            if vandfelt:
                zw = f[vandfelt]
                if zw is None or (hasattr(zw, "isNull") and zw.isNull()):
                    advarsler.append("Objekt %s: mangler vandspejlskote - VandVol ikke beregnet." % f.id())
                    zw = None
                else:
                    zw = float(zw)
                    if zw <= zb:
                        advarsler.append("Objekt %s: vandspejlet (%.2f) ligger ikke over bundkoten (%.2f)."
                                         % (f.id(), zw, zb))
            g = QgsGeometry(geom)
            if g.constGet().hasCurvedSegments():
                g = QgsGeometry(g.constGet().segmentize())
            g.transform(til_dem)
            ringe = _ringe(g)
            bbox = g.boundingBox()

            # Første gæt på hvor langt ud skrabet når
            r = terraen.udsnit(bbox.buffered(margin))
            if r is None:
                advarsler.append("Objekt %s: ligger uden for terrænmodellen." % f.id())
                continue
            T, gyl, *_ = r
            tmax = float(T[gyl].max()) if gyl.any() else zb
            D = anlaeg * max(tmax - zb, 0.0) + margin

            res = None
            while True:
                r = terraen.udsnit(bbox.buffered(D))
                T, gyl, X, Y, beskaaret, ext = r
                res = beregn_skrab(T, gyl, X, Y, ringe, zb, anlaeg, celleareal, zw)
                if not res["rammer_kant"]:
                    break
                if beskaaret:
                    advarsler.append("Objekt %s: skrabet når ud til terrænmodellens kant - "
                                     "jordmængden kan være for lille." % f.id())
                    break
                if D >= MAKS_UDVIDELSE:
                    advarsler.append("Objekt %s: skrabet når mere end %.0f m ud - beregning stoppet."
                                     % (f.id(), MAKS_UDVIDELSE))
                    break
                ny_tmax = res["tmax"] if res["tmax"] is not None else tmax
                D = min(max(D * 1.5, anlaeg * max(ny_tmax - zb, 0.0) + margin), MAKS_UDVIDELSE)

            if res["mangler"] > 0:
                advarsler.append("Objekt %s: %d celler uden terrænværdi inden for skrabet - regnet som 0."
                                 % (f.id(), res["mangler"]))
            if res["celler_i_bund"] == 0:
                advarsler.append("Objekt %s: bunden er mindre end en beregningscelle - usikkert resultat."
                                 % f.id())

            vol = round(res["volumen"], 2)
            dyb = None if res["middeldybde"] is None else round(res["middeldybde"], 2)
            vvol = None if res["vandvol"] is None else round(res["vandvol"], 2)
            vareal = None if res["vandareal"] is None else round(res["vandareal"], 1)
            lavter = None if res["lavter"] is None else round(res["lavter"], 2)
            zw_brugt = None if res["zw_brugt"] is None else round(res["zw_brugt"], 2)
            if zw is not None and zw_brugt is not None and zw_brugt < zw:
                advarsler.append("Objekt %s: vandspejlet (%.2f) ligger over laveste terræn langs skrabet "
                                 "(%.2f) - VandVol er beregnet med %.2f." % (f.id(), zw, lavter, zw_brugt))
            if lavter is None:
                advarsler.append("Objekt %s: kunne ikke finde terræn langs skrabets kant - LavTer mangler." % f.id())
            resultater[f.id()] = (vol, dyb, lavter) + ((vvol,) if vandfelt else ())
            total += vol
            total_vand += vvol or 0.0

            # Øverste udbredelse
            top = _maske_til_geometri(res["udbredelse"], ext, cw, ch)
            if not top.isEmpty():
                top = top.simplify(0.75 * max(cw, ch))
                top = top.smooth(2, 0.25)
                top = top.combine(g)
                top = top.makeValid()
                top = top.removeInteriorRings(10 * celleareal)
            else:
                top = QgsGeometry(g)
            areal_top = top.area()
            areal_bund = g.area()
            top.transform(fra_dem)
            top.convertToMultiType()

            nf = QgsFeature(ud.fields())
            nf.setGeometry(top)
            navn_vaerdi = [f[navnefelt]] if navnefelt else []
            nf.setAttributes(navn_vaerdi + [f.id(), zb, vol, dyb, round(areal_bund, 1), round(areal_top, 1), lavter]
                             + ([zw, zw_brugt, vvol, vareal] if vandfelt else []))
            nye.append(nf)
            _log("Objekt %s%s: bundkote %.2f, jordmængde %.1f m3, dybde %s m, areal top %.1f m2"
                 % (f.id(), " (%s)" % f[navnefelt] if navnefelt else "", zb, vol, "-" if dyb is None else "%.2f" % dyb, areal_top)
                 + ("" if lavter is None else ", LavTer %.2f" % lavter)
                 + ("" if vvol is None else ", VandVol %.1f m3 (vandspejl %.2f)" % (vvol, zw_brugt)))

        prog.setValue(len(feats))
        prog.close()

        # Skriv i skrablaget
        if lag.isEditable():
            for fid, vaerdier in resultater.items():
                for idx, v in zip(idx_res, vaerdier):
                    lag.changeAttributeValue(fid, idx, v)
        else:
            aendr = {fid: dict(zip(idx_res, vaerdier)) for fid, vaerdier in resultater.items()}
            if not lag.dataProvider().changeAttributeValues(aendr):
                advarsler.append("Kunne ikke skrive resultaterne i %s." % lag.name())
        lag.triggerRepaint()

        # Nyt lag med øverste udbredelse
        ud.dataProvider().addFeatures(nye)
        ud.updateExtents()
        if udfil:
            ud = self._gem(ud, udfil) or ud
        projekt.addMapLayer(ud)

        for a in advarsler:
            _log(a, NIV_ADVARSEL)
        tekst = "%d skrab beregnet, samlet jordmængde %.1f m³." % (len(resultater), total)
        if vandfelt:
            tekst = tekst[:-1] + ", samlet VandVol %.1f m³." % total_vand
        if advarsler:
            tekst += " %d advarsel(er) - se loggen 'Grav Skrab'." % len(advarsler)
        self.iface.messageBar().pushMessage(
            "Grav Skrab", tekst, NIV_ADVARSEL if advarsler else NIV_OK, 10)

    def _gem(self, ud, sti):
        endelse = os.path.splitext(sti)[1].lower()
        if endelse not in (".gpkg", ".shp"):
            sti += ".gpkg"
            endelse = ".gpkg"
        opt = QgsVectorFileWriter.SaveVectorOptions()
        opt.driverName = "GPKG" if endelse == ".gpkg" else "ESRI Shapefile"
        navn = os.path.splitext(os.path.basename(sti))[0]
        opt.layerName = navn
        res = QgsVectorFileWriter.writeAsVectorFormatV3(
            ud, sti, QgsProject.instance().transformContext(), opt)
        noerr = _enum((QgsVectorFileWriter, "WriterError.NoError"), (QgsVectorFileWriter, "NoError"))
        if res[0] != noerr:
            QMessageBox.warning(self.iface.mainWindow(), "Grav Skrab",
                                "Kunne ikke gemme laget:\n%s\nDet tilføjes som midlertidigt lag." % res[1])
            return None
        uri = sti + ("|layername=%s" % navn if endelse == ".gpkg" else "")
        nyt = QgsVectorLayer(uri, navn, "ogr")
        return nyt if nyt.isValid() else None
