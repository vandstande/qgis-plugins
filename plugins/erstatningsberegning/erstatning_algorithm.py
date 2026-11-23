# -*- coding: utf-8 -*-
"""
Erstatningsberegning – Processing-algoritme.

Svarer til modellen Erstatningsberegning_07_05_26.model3:

  1. Erstatningskortet klippes til projektgrænsen og deles efter sats
     (Hoj, Mellem, Konflikt, Ingen).
  2. Ejerlaget samles (opløses) pr. lodsejer – efter BFE-nummer og derefter navn
     (NavnJuster), så hver lodsejer bliver én række.
  3. For hver lodsejer beregnes arealet (m² og ha) af hver sats.
  4. Høj + Konflikt lægges sammen i feltet 'Hoej_+_konflikt' (ha).
"""

from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterField,
    QgsProcessingParameterVectorLayer,
    QgsProcessingUtils,
    QgsWkbTypes,
)

# --------------------------------------------------------------------------
# Kompatibilitet QGIS 3.x / QGIS 4 (Qt5 / Qt6)
# --------------------------------------------------------------------------
if Qgis.QGIS_VERSION_INT >= 33800:
    from qgis.PyQt.QtCore import QMetaType
    T_DOUBLE = QMetaType.Type.Double
    T_STRING = QMetaType.Type.QString
else:
    from qgis.PyQt.QtCore import QVariant
    T_DOUBLE = QVariant.Double
    T_STRING = QVariant.String

if hasattr(Qgis, "ProcessingSourceType"):
    SRC_POLYGON = Qgis.ProcessingSourceType.VectorPolygon
else:
    SRC_POLYGON = QgsProcessing.TypeVectorPolygon

if hasattr(Qgis, "WkbType"):
    WKB_MULTIPOLYGON = Qgis.WkbType.MultiPolygon
else:
    WKB_MULTIPOLYGON = QgsWkbTypes.MultiPolygon

TEMP = "TEMPORARY_OUTPUT"

# Sats-værdi i erstatningskortet -> præfiks i resultatfelterne
SATSER = [
    ("Hoj", "Hoej"),
    ("Konflikt", "Konflikt"),
    ("Mellem", "Mellem"),
    ("Ingen", "Ingen"),
]

# Attributter der kopieres fra ejerlaget (første værdi pr. lodsejer)
EKSTRA_FELTER = ["EjersAdres", "LifaId", "EjerType"]


class ErstatningsberegningAlgorithm(QgsProcessingAlgorithm):
    PROJEKT = "PROJEKT"
    ERSTATNINGSKORT = "ERSTATNINGSKORT"
    SATS_FELT = "SATS_FELT"
    EJERLAG = "EJERLAG"
    BFE_FELT = "BFE_FELT"
    NAVN_FELT = "NAVN_FELT"
    OUTPUT = "OUTPUT"
    AABN = "AABN"

    def __init__(self):
        super().__init__()
        self._fil = None
        self._aabn = False

    # ------------------------------------------------------------------ meta
    def createInstance(self):  # noqa: N802
        return ErstatningsberegningAlgorithm()

    def name(self):
        return "erstatningsberegning"

    def displayName(self):  # noqa: N802
        return "Erstatningsberegning"

    def group(self):
        return ""

    def groupId(self):  # noqa: N802
        return ""

    def shortHelpString(self):  # noqa: N802
        return (
            "<p>Beregner arealer pr. <b>lodsejer</b> inden for projektgrænsen fordelt på "
            "erstatningssatserne <b>Høj</b>, <b>Mellem</b>, <b>Konflikt</b> og <b>Ingen</b>.</p>"
            "<h3>Input</h3>"
            "<ul>"
            "<li><b>Projektgrænse</b>: polygonlag.</li>"
            "<li><b>Erstatningskort</b> og feltet med <b>sats</b> (værdierne 'Hoj', 'Mellem', "
            "'Konflikt' og 'Ingen').</li>"
            "<li><b>Ejerlag</b> (lodsejere) med felter for <b>BFE-nummer</b> og <b>navn</b> "
            "(NavnJuster). Felterne EjersAdres, LifaId og EjerType kopieres med, hvis de findes.</li>"
            "</ul>"
            "<h3>Output</h3>"
            "<p>Én række pr. lodsejer med areal i m² og ha for hver sats samt "
            "<b>Hoej_+_konflikt</b> (ha). Har en lodsejer flere ejendomme, står alle "
            "BFE-numre i feltet <b>BFEnumre</b>. Vælg 'Gem til fil…' og .xlsx for at få et "
            "Excel-ark, som kan åbnes automatisk.</p>"
        )

    # ------------------------------------------------------------ parametre
    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.PROJEKT, "Projektgrænse", [SRC_POLYGON]))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.ERSTATNINGSKORT, "Erstatningskort", [SRC_POLYGON]))
        self.addParameter(QgsProcessingParameterField(
            self.SATS_FELT, "Sats-felt i erstatningskortet",
            defaultValue="Sats", parentLayerParameterName=self.ERSTATNINGSKORT))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.EJERLAG, "Ejerlag (lodsejere)", [SRC_POLYGON]))
        self.addParameter(QgsProcessingParameterField(
            self.BFE_FELT, "BFE-nummer-felt",
            defaultValue="BFEnummer", parentLayerParameterName=self.EJERLAG))
        self.addParameter(QgsProcessingParameterField(
            self.NAVN_FELT, "Navne-felt (lodsejer)",
            defaultValue="NavnJuster", parentLayerParameterName=self.EJERLAG))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, "Erstatningsberegning (gem som .xlsx)"))
        self.addParameter(QgsProcessingParameterBoolean(
            self.AABN, "Åbn resultatet når det er gemt som fil", defaultValue=True))

    # -------------------------------------------------------------- hjælpere
    @staticmethod
    def _valid(geom):
        if geom is None or geom.isEmpty():
            return QgsGeometry()
        if not geom.isGeosValid():
            geom = geom.makeValid()
        return geom

    @staticmethod
    def _er_null(v):
        return v is None or str(v) == "NULL"

    # ---------------------------------------------------------------- kørsel
    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        from qgis import processing

        projekt = self.parameterAsVectorLayer(parameters, self.PROJEKT, context)
        kort = self.parameterAsVectorLayer(parameters, self.ERSTATNINGSKORT, context)
        ejerlag = self.parameterAsVectorLayer(parameters, self.EJERLAG, context)
        sats_felt = self.parameterAsString(parameters, self.SATS_FELT, context) or "Sats"
        bfe_felt = self.parameterAsString(parameters, self.BFE_FELT, context) or "BFEnummer"
        navn_felt = self.parameterAsString(parameters, self.NAVN_FELT, context) or "NavnJuster"

        for lyr, navn in ((projekt, "Projektgrænse"), (kort, "Erstatningskort"),
                          (ejerlag, "Ejerlag")):
            if lyr is None:
                raise QgsProcessingException("Lag mangler: {}".format(navn))
        for lyr, felt, lagnavn in ((kort, sats_felt, "erstatningskortet"),
                                   (ejerlag, bfe_felt, "ejerlaget"),
                                   (ejerlag, navn_felt, "ejerlaget")):
            if lyr.fields().lookupField(felt) < 0:
                raise QgsProcessingException(
                    "Feltet '{}' findes ikke i {}.".format(felt, lagnavn))

        crs = ejerlag.crs()
        da = QgsDistanceArea()
        da.setSourceCrs(crs, context.transformContext())
        da.setEllipsoid(context.ellipsoid())

        # 1. Klip erstatningskortet til projektgrænsen
        feedback.setProgress(5)
        feedback.pushInfo("Klipper erstatningskortet til projektgrænsen …")
        klip_id = processing.run("native:clip", {
            "INPUT": kort, "OVERLAY": projekt, "OUTPUT": TEMP},
            context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]
        klip = QgsProcessingUtils.mapLayerFromString(klip_id, context)
        if klip is None:
            raise QgsProcessingException("Kunne ikke klippe erstatningskortet.")
        if feedback.isCanceled():
            return {}

        # 2. Saml erstatningskortet pr. sats (overlap tælles kun én gang)
        feedback.setProgress(25)
        xform_kort = None
        if klip.crs() != crs:
            xform_kort = QgsCoordinateTransform(klip.crs(), crs, context.transformContext())
        s_idx = klip.fields().lookupField(sats_felt)
        pr_sats = {}
        for f in klip.getFeatures():
            if not f.hasGeometry():
                continue
            g = QgsGeometry(f.geometry())
            if xform_kort is not None:
                g.transform(xform_kort)
            pr_sats.setdefault(f[s_idx], []).append(self._valid(g))

        kendte = {s for s, _ in SATSER}
        ukendte = sorted(str(v) for v in pr_sats if v not in kendte)
        if ukendte:
            feedback.pushWarning("Sats-værdier der ikke indgår i beregningen: {}".format(
                ", ".join(ukendte)))
        sats_geom = {}
        sats_total = {}
        for sats, _ in SATSER:
            geoms = pr_sats.get(sats)
            sats_geom[sats] = self._valid(QgsGeometry.unaryUnion(geoms)) if geoms else QgsGeometry()
            sats_total[sats] = da.measureArea(sats_geom[sats]) if not sats_geom[sats].isEmpty() else 0.0
        if feedback.isCanceled():
            return {}

        # 3. Saml ejerlaget pr. lodsejer (navn); første værdi af øvrige felter
        feedback.setProgress(40)
        feedback.pushInfo("Samler ejerlaget pr. lodsejer …")
        flds = ejerlag.fields()
        b_idx = flds.lookupField(bfe_felt)
        n_idx = flds.lookupField(navn_felt)
        ekstra = [(navn, flds.lookupField(navn)) for navn in EKSTRA_FELTER]
        mangler = [navn for navn, i in ekstra if i < 0]
        if mangler:
            feedback.pushWarning("Felter der ikke findes i ejerlaget og efterlades tomme: {}"
                                 .format(", ".join(mangler)))

        ejere = {}       # navn -> dict
        raekkefolge = []
        uden_navn = 0
        for f in ejerlag.getFeatures():
            if not f.hasGeometry():
                continue
            navn = f[n_idx]
            if self._er_null(navn):
                navn = None
                uden_navn += 1
            if navn not in ejere:
                ejere[navn] = {
                    "bfe": f[b_idx],
                    "bfe_liste": [],
                    "ekstra": {n: (f[i] if i >= 0 else None) for n, i in ekstra},
                    "geoms": [],
                }
                raekkefolge.append(navn)
            e = ejere[navn]
            bfe = f[b_idx]
            if not self._er_null(bfe) and str(bfe) not in e["bfe_liste"]:
                e["bfe_liste"].append(str(bfe))
            e["geoms"].append(self._valid(QgsGeometry(f.geometry())))
        if uden_navn:
            feedback.pushWarning("{} ejerlag-objekter har intet navn og er samlet i én række."
                                 .format(uden_navn))

        # 4. Arealer pr. lodsejer og sats
        feedback.setProgress(55)
        feedback.pushInfo("Beregner arealer pr. lodsejer …")
        resultat = []
        n = max(len(raekkefolge), 1)
        for i, navn in enumerate(raekkefolge):
            if feedback.isCanceled():
                return {}
            e = ejere[navn]
            ejer_geom = self._valid(QgsGeometry.unaryUnion(e["geoms"]))
            arealer = {}
            for sats, _ in SATSER:
                sg = sats_geom[sats]
                if sg.isEmpty() or ejer_geom.isEmpty() or \
                        not ejer_geom.boundingBox().intersects(sg.boundingBox()):
                    arealer[sats] = 0.0
                    continue
                skaer = ejer_geom.intersection(sg)
                arealer[sats] = da.measureArea(skaer) if skaer and not skaer.isEmpty() else 0.0
            resultat.append((navn, e, ejer_geom, arealer))
            feedback.setProgress(55 + int(35 * (i + 1) / n))

        # 5. Output
        def kopi(idx, navn, laengde):
            """Feltdefinition kopieret fra ejerlaget (bevarer type), ellers tekstfelt."""
            if idx >= 0:
                fd = QgsField(flds.at(idx))
                fd.setName(navn)
                return fd
            return QgsField(navn, T_STRING, len=laengde)

        ekstra_idx = dict(ekstra)
        felter = QgsFields()
        felter.append(kopi(b_idx, "BFEnummer", 20))
        felter.append(QgsField("NavnJuster", T_STRING, len=254))
        felter.append(kopi(ekstra_idx["EjersAdres"], "EjersAdres", 200))
        for sats, praefiks in SATSER:
            felter.append(QgsField("{}_area_m2".format(praefiks), T_DOUBLE, len=20, prec=2))
            felter.append(QgsField("{}_area_ha".format(praefiks), T_DOUBLE, len=20, prec=4))
            if sats == "Konflikt":
                felter.append(QgsField("Hoej_+_konflikt", T_DOUBLE, len=20, prec=4))
        felter.append(kopi(ekstra_idx["LifaId"], "LifaId", 20))
        felter.append(kopi(ekstra_idx["EjerType"], "EjerType", 50))
        felter.append(QgsField("BFEnumre", T_STRING, len=254))

        ud_crs = crs
        if context.project() is not None and context.project().crs().isValid():
            ud_crs = context.project().crs()
        xform_ud = None
        if ud_crs != crs:
            xform_ud = QgsCoordinateTransform(crs, ud_crs, context.transformContext())

        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context, felter, WKB_MULTIPOLYGON, ud_crs)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        resultat.sort(key=lambda r: ("" if r[0] is None else str(r[0])).lower())
        summer = {s: 0.0 for s, _ in SATSER}
        for navn, e, geom, arealer in resultat:
            vaerdier = {
                "BFEnummer": e["bfe"],
                "NavnJuster": navn,
                "BFEnumre": ", ".join(e["bfe_liste"]),
            }
            vaerdier.update(e["ekstra"])
            for sats, praefiks in SATSER:
                m2 = arealer[sats]
                summer[sats] += m2
                vaerdier["{}_area_m2".format(praefiks)] = round(m2, 2)
                vaerdier["{}_area_ha".format(praefiks)] = round(m2 / 10000.0, 4)
            vaerdier["Hoej_+_konflikt"] = round((arealer["Hoj"] + arealer["Konflikt"]) / 10000.0, 4)

            feat = QgsFeature(felter)
            g = QgsGeometry(geom)
            if xform_ud is not None:
                g.transform(xform_ud)
            g.convertToMultiType()
            feat.setGeometry(g)
            feat.setAttributes([vaerdier.get(felter.at(i).name()) for i in range(felter.count())])
            sink.addFeature(feat)

        # Opsummering og kontrol: er hele erstatningskortet fordelt på lodsejere?
        feedback.setProgress(95)
        feedback.pushInfo("{} lodsejere.".format(len(resultat)))
        for sats, praefiks in SATSER:
            fordelt = summer[sats] / 10000.0
            total = sats_total[sats] / 10000.0
            feedback.pushInfo("{}: {:.2f} ha fordelt på lodsejere (erstatningskort i projektet: "
                              "{:.2f} ha)".format(praefiks, fordelt, total))
            if total - fordelt > 0.01:
                feedback.pushWarning(
                    "{}: {:.2f} ha af erstatningskortet ligger ikke på nogen lodsejer i ejerlaget "
                    "(fx vej, vandløb eller manglende ejendom).".format(praefiks, total - fordelt))
            elif fordelt - total > 0.01:
                feedback.pushWarning(
                    "{}: {:.2f} ha er talt mere end én gang – ejerlaget har overlappende "
                    "lodsejere.".format(praefiks, fordelt - total))

        # Åbn bagefter, hvis resultatet er gemt som fil
        self._aabn = self.parameterAsBoolean(parameters, self.AABN, context)
        self._fil = None
        sti = str(dest_id).split("|")[0]
        if sti.lower().endswith((".xlsx", ".xls", ".ods", ".csv")):
            self._fil = sti

        return {self.OUTPUT: dest_id}

    def postProcessAlgorithm(self, context, feedback):  # noqa: N802
        # Kører i hovedtråden – her må vi åbne filer i andre programmer
        if self._aabn and self._fil:
            from qgis.PyQt.QtCore import QUrl
            from qgis.PyQt.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._fil))
        return {}
