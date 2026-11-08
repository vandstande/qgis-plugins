# -*- coding: utf-8 -*-
"""QGIS-delen af beregningen: læsning af lag, terrænopslag og skrivning af jordmgd."""

import math

from qgis.core import (
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsSpatialIndex,
    QgsMessageLog,
)

from . import beregning
from .compat import (NIVEAU_INFO, NIVEAU_ADVARSEL, KAN_TILFOEJE_FELTER,
                     KAN_AENDRE_VAERDIER, har_kapabilitet)

FELTNAVN = "jordmgd"
FELT_MAXD = "MaxD"
FELT_MIND = "MinD"
LOG_TAG = "Grav grøfter"


def log(tekst, niveau=None):
    QgsMessageLog.logMessage(tekst, LOG_TAG, niveau if niveau is not None else NIVEAU_INFO)


def _dobbelt_felt(navn):
    """QgsField af typen Double – virker i både QGIS 3 og QGIS 4 (Qt6)."""
    try:
        from qgis.PyQt.QtCore import QMetaType
        return QgsField(navn, QMetaType.Type.Double, "double", 12, 2)
    except (ImportError, AttributeError, TypeError):
        from qgis.PyQt.QtCore import QVariant
        return QgsField(navn, QVariant.Double, "double", 12, 2)


class TerraenSampler:
    """Bilineært opslag i terrænmodellen. Læser et vindue pr. grøft via GDAL
    (hurtigt); falder tilbage på QGIS' provider.sample hvis GDAL ikke kan bruges."""

    def __init__(self, raster_lag, linje_crs, band=1):
        self.lag = raster_lag
        self.provider = raster_lag.dataProvider()
        self.band = band
        self.xform = None
        if raster_lag.crs() != linje_crs:
            self.xform = QgsCoordinateTransform(linje_crs, raster_lag.crs(), QgsProject.instance())

        self.ds = None
        self.arr = None
        try:
            from osgeo import gdal
            import numpy  # noqa: F401
            ds = gdal.Open(raster_lag.source().split("|")[0])
            if ds is not None:
                gt = ds.GetGeoTransform()
                if gt[2] == 0 and gt[4] == 0:
                    self.ds = ds
                    self.gt = gt
                    self.rb = ds.GetRasterBand(band)
                    self.nodata = self.rb.GetNoDataValue()
                    self.ncol = ds.RasterXSize
                    self.nrow = ds.RasterYSize
        except Exception:  # noqa: BLE001
            self.ds = None

    # ------------------------------------------------------------------
    def forbered(self, rekt_linje_crs):
        """Læs det rasterudsnit der dækker rektanglet (i linjelagets CRS)."""
        if self.ds is None:
            return
        import numpy as np
        rekt = rekt_linje_crs
        if self.xform is not None:
            rekt = self.xform.transformBoundingBox(rekt)
        gt = self.gt
        c0 = int(math.floor((rekt.xMinimum() - gt[0]) / gt[1])) - 2
        c1 = int(math.ceil((rekt.xMaximum() - gt[0]) / gt[1])) + 2
        if gt[5] < 0:
            r0 = int(math.floor((rekt.yMaximum() - gt[3]) / gt[5])) - 2
            r1 = int(math.ceil((rekt.yMinimum() - gt[3]) / gt[5])) + 2
        else:
            r0 = int(math.floor((rekt.yMinimum() - gt[3]) / gt[5])) - 2
            r1 = int(math.ceil((rekt.yMaximum() - gt[3]) / gt[5])) + 2
        c0, r0 = max(c0, 0), max(r0, 0)
        c1, r1 = min(c1, self.ncol), min(r1, self.nrow)
        if c1 <= c0 or r1 <= r0:
            self.arr = None
            return
        arr = self.rb.ReadAsArray(c0, r0, c1 - c0, r1 - r0).astype(np.float64)
        if self.nodata is not None:
            arr[arr == self.nodata] = np.nan
        self.arr = arr
        self.c_off = c0
        self.r_off = r0

    # ------------------------------------------------------------------
    def kote(self, x, y):
        if self.xform is not None:
            p = self.xform.transform(QgsPointXY(x, y))
            x, y = p.x(), p.y()

        if self.ds is None or self.arr is None:
            val, ok = self.provider.sample(QgsPointXY(x, y), self.band)
            if not ok or val is None or (isinstance(val, float) and math.isnan(val)):
                return None
            return float(val)

        gt = self.gt
        fc = (x - gt[0]) / gt[1] - 0.5
        fr = (y - gt[3]) / gt[5] - 0.5
        c0 = int(math.floor(fc))
        r0 = int(math.floor(fr))
        dx = fc - c0
        dy = fr - r0
        j = c0 - self.c_off
        i = r0 - self.r_off
        nr, nc = self.arr.shape
        if 0 <= i < nr - 1 and 0 <= j < nc - 1:
            v00 = self.arr[i, j]
            v01 = self.arr[i, j + 1]
            v10 = self.arr[i + 1, j]
            v11 = self.arr[i + 1, j + 1]
            if not (math.isnan(v00) or math.isnan(v01) or math.isnan(v10) or math.isnan(v11)):
                return float(v00 * (1 - dx) * (1 - dy) + v01 * dx * (1 - dy)
                             + v10 * (1 - dx) * dy + v11 * dx * dy)
        # Nærmeste pixel som nødløsning (kant af raster / nodata-nabo)
        jj = int(math.floor((x - gt[0]) / gt[1])) - self.c_off
        ii = int(math.floor((y - gt[3]) / gt[5])) - self.r_off
        if 0 <= ii < nr and 0 <= jj < nc:
            v = self.arr[ii, jj]
            return None if math.isnan(v) else float(v)
        # Uden for det indlæste udsnit: slå op direkte i terrænmodellen
        val, ok = self.provider.sample(QgsPointXY(x, y), self.band)
        if not ok or val is None or (isinstance(val, float) and math.isnan(val)):
            return None
        return float(val)


def _linjedele(geom):
    """Returnerer en liste af enkelt-linjer (QgsGeometry) for en (multi)linje."""
    if geom is None or geom.isEmpty():
        return []
    g = geom
    if g.isMultipart():
        merged = g.mergeLines()
        if merged and not merged.isEmpty():
            g = merged
    if g.isMultipart():
        return [QgsGeometry.fromPolylineXY(p) for p in g.asMultiPolyline() if len(p) > 1]
    return [QgsGeometry.fromPolylineXY(g.asPolyline())]


def beregn(linje_lag, bredde_felt, punkt_lag, kote_felt, raster_lag,
           soegeafstand=5.0, trin_langs=1.0, trin_tvaers=0.2, band=1,
           kun_valgte=False, fremskridt=None):
    """Beregner jordmængden for hver grøft og skriver den i feltet jordmgd.

    fremskridt(brøkdel, tekst) -> True hvis brugeren har annulleret.
    Returnerer en dict med statistik.
    """
    linje_crs = linje_lag.crs()
    projekt = QgsProject.instance()

    # ---------- linjedele + rumligt indeks -----------------------------
    if kun_valgte:
        feats = list(linje_lag.getSelectedFeatures())
    else:
        feats = list(linje_lag.getFeatures())

    dele = {}          # del-id -> (fid, geometri)
    indeks = QgsSpatialIndex()
    bredder = {}
    nid = 0
    for f in feats:
        bredder[f.id()] = f[bredde_felt]
        for d in _linjedele(f.geometry()):
            nid += 1
            dele[nid] = (f.id(), d)
            tmp = QgsFeature(nid)
            tmp.setGeometry(d)
            indeks.addFeature(tmp)

    # ---------- tilknyt bundpunkter til nærmeste linje -------------------
    punkt_xform = None
    if punkt_lag.crs() != linje_crs:
        punkt_xform = QgsCoordinateTransform(punkt_lag.crs(), linje_crs, projekt)

    bund = {k: ([], []) for k in dele}   # del-id -> (stationer, koter)
    ubrugte_punkter = 0
    for p in punkt_lag.getFeatures():
        z = p[kote_felt]
        if z is None or (hasattr(z, "isNull") and z.isNull()):
            continue
        try:
            z = float(z)
        except (TypeError, ValueError):
            continue
        g = p.geometry()
        if g is None or g.isEmpty():
            continue
        g = QgsGeometry(g)
        if punkt_xform is not None:
            g.transform(punkt_xform)
        pt = g.asPoint() if not g.isMultipart() else g.asMultiPoint()[0]
        pg = QgsGeometry.fromPointXY(QgsPointXY(pt))
        rekt = QgsRectangle(pt.x() - soegeafstand, pt.y() - soegeafstand,
                            pt.x() + soegeafstand, pt.y() + soegeafstand)
        bedst, bedst_afst = None, None
        for kid in indeks.intersects(rekt):
            afst = dele[kid][1].distance(pg)
            if afst <= soegeafstand and (bedst_afst is None or afst < bedst_afst):
                bedst, bedst_afst = kid, afst
        if bedst is None:
            ubrugte_punkter += 1
            continue
        st = dele[bedst][1].lineLocatePoint(pg)
        bund[bedst][0].append(st)
        bund[bedst][1].append(z)

    # ---------- sørg for felterne jordmgd, MaxD og MinD -----------------
    prov = linje_lag.dataProvider()
    nye = [n for n in (FELTNAVN, FELT_MAXD, FELT_MIND)
           if linje_lag.fields().indexFromName(n) < 0]
    if nye:
        if linje_lag.isEditable():
            for n in nye:
                linje_lag.addAttribute(_dobbelt_felt(n))
        else:
            if not har_kapabilitet(prov, KAN_TILFOEJE_FELTER):
                raise RuntimeError("Linjelaget tillader ikke at der tilføjes felter.")
            prov.addAttributes([_dobbelt_felt(n) for n in nye])
        linje_lag.updateFields()
    idx = linje_lag.fields().indexFromName(FELTNAVN)
    idx_max = linje_lag.fields().indexFromName(FELT_MAXD)
    idx_min = linje_lag.fields().indexFromName(FELT_MIND)
    for n, i in ((FELTNAVN, idx), (FELT_MAXD, idx_max), (FELT_MIND, idx_min)):
        if i < 0:
            raise RuntimeError("Kunne ikke oprette feltet '%s'." % n)

    # ---------- beregn volumen pr. grøft ---------------------------------
    sampler = TerraenSampler(raster_lag, linje_crs, band)
    volumen = {f.id(): 0.0 for f in feats}
    gyldig = {f.id(): False for f in feats}
    maxd = {f.id(): None for f in feats}   # største dybde under terræn [m]
    mind = {f.id(): None for f in feats}   # mindste dybde under terræn [m]
    advarsler = []
    mangler_terraen = 0

    total = max(len(dele), 1)
    for n, (kid, (fid, geom)) in enumerate(dele.items()):
        if fremskridt and fremskridt(n / total, "Grøft %d" % fid):
            return {"annulleret": True}

        b = bredder.get(fid)
        try:
            b = float(b)
        except (TypeError, ValueError):
            advarsler.append("Grøft %s: ingen gyldig bundbredde – springes over." % fid)
            continue

        stationer, koter = beregning.rens_bundpunkter(*bund[kid])
        if not stationer:
            advarsler.append("Grøft %s: ingen bundpunkter inden for %.1f m – springes over."
                             % (fid, soegeafstand))
            continue

        laengde = geom.length()
        if laengde <= 0:
            continue

        # Terrænudsnit der dækker grøften inkl. skråninger
        bb = geom.boundingBox()
        buf = b / 2.0 + beregning.ANLAEG * 30.0
        sampler.forbered(QgsRectangle(bb.xMinimum() - buf, bb.yMinimum() - buf,
                                      bb.xMaximum() + buf, bb.yMaximum() + buf))

        n_st = max(1, int(math.ceil(laengde / trin_langs)))
        st_liste, areal_liste = [], []
        for i in range(n_st + 1):
            s = laengde * i / n_st
            p = geom.interpolate(s).asPoint()
            vinkel = geom.interpolateAngle(s)       # radianer, med uret fra nord
            nx, ny = math.cos(vinkel), -math.sin(vinkel)  # normal til linjen
            zb = beregning.bundkote(stationer, koter, s)

            def terraen(o, px=p.x(), py=p.y(), nx=nx, ny=ny):
                return sampler.kote(px + o * nx, py + o * ny)

            a, m = beregning.tvaersnitsareal(terraen, zb, b, trin_tvaers)
            mangler_terraen += m
            st_liste.append(s)
            areal_liste.append(a)

            # Dybde under terræn målt på linjen (terræn - bundkote)
            t0 = terraen(0.0)
            if t0 is not None:
                d = t0 - zb
                if maxd[fid] is None or d > maxd[fid]:
                    maxd[fid] = d
                if mind[fid] is None or d < mind[fid]:
                    mind[fid] = d

        volumen[fid] += beregning.integrer_volumen(st_liste, areal_liste)
        gyldig[fid] = True

    # ---------- skriv resultater ----------------------------------------
    def _r(v, ok=True):
        return round(v, 2) if (ok and v is not None) else None

    aendringer = {}
    for fid, v in volumen.items():
        ok = gyldig[fid]
        aendringer[fid] = {
            idx: _r(v, ok),
            idx_max: _r(maxd[fid], ok),
            idx_min: _r(mind[fid], ok),
        }

    if linje_lag.isEditable():
        for fid, attr in aendringer.items():
            for i, val in attr.items():
                linje_lag.changeAttributeValue(fid, i, val)
    else:
        if not har_kapabilitet(prov, KAN_AENDRE_VAERDIER):
            raise RuntimeError("Linjelaget tillader ikke at attributter ændres.")
        prov.changeAttributeValues(aendringer)
    linje_lag.triggerRepaint()

    for a in advarsler:
        log(a, NIVEAU_ADVARSEL)
    if ubrugte_punkter:
        log("%d bundpunkter lå længere end %.1f m fra nærmeste grøft og blev ikke brugt."
            % (ubrugte_punkter, soegeafstand), NIVEAU_ADVARSEL)
    if mangler_terraen:
        log("%d terrænopslag manglede værdi (uden for terrænmodel / nodata)."
            % mangler_terraen, NIVEAU_ADVARSEL)

    beregnet = [fid for fid, ok in gyldig.items() if ok]
    return {
        "annulleret": False,
        "antal_groefter": len(feats),
        "antal_beregnet": len(beregnet),
        "total_m3": sum(volumen[f] for f in beregnet),
        "advarsler": advarsler,
        "ubrugte_punkter": ubrugte_punkter,
        "mangler_terraen": mangler_terraen,
    }
