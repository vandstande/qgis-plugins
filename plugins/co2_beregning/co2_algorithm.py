# -*- coding: utf-8 -*-
"""
CO2-beregning – Processing-algoritme.

Svarer til modellen CO2_beregning_07_05_26.model3:

  1. Markkort, CO2-kort og buffer om afværgeforanstaltninger klippes til projektgrænsen.
  2. Markkortet opløses efter drifttype.
  3. Markerne deles i 'med_buffer' (skæring med bufferen) og 'uden_buffer' (difference).
  4. CO2-kortet opløses efter tørveklasse (Toerv_pct).
  5. For hver tørveklasse beregnes arealet (ha, 2 dec.) af hver drifttype
     (Omdrift, PermGraes, NATUR, OevrigeIMK) – uden og med buffer.
  6. Resultatet sorteres (uden_buffer før med_buffer, tørv >12, 6-12, 3-6, <3)
     og nummereres i feltet 'Sortering'.
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
    QgsProcessingMultiStepFeedback,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDistance,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterField,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterString,
    QgsProcessingParameterVectorLayer,
    QgsProcessingUtils,
    QgsWkbTypes,
)

from . import beregningsark

# --------------------------------------------------------------------------
# Kompatibilitet QGIS 3.x / QGIS 4 (Qt5 / Qt6)
# --------------------------------------------------------------------------
if Qgis.QGIS_VERSION_INT >= 33800:
    from qgis.PyQt.QtCore import QMetaType
    T_INT = QMetaType.Type.Int
    T_DOUBLE = QMetaType.Type.Double
    T_STRING = QMetaType.Type.QString
else:
    from qgis.PyQt.QtCore import QVariant
    T_INT = QVariant.Int
    T_DOUBLE = QVariant.Double
    T_STRING = QVariant.String

if hasattr(Qgis, "ProcessingSourceType"):
    SRC_POLYGON = Qgis.ProcessingSourceType.VectorPolygon
    SRC_LINE = Qgis.ProcessingSourceType.VectorLine
else:
    SRC_POLYGON = QgsProcessing.TypeVectorPolygon
    SRC_LINE = QgsProcessing.TypeVectorLine

if hasattr(Qgis, "WkbType"):
    WKB_MULTIPOLYGON = Qgis.WkbType.MultiPolygon
else:
    WKB_MULTIPOLYGON = QgsWkbTypes.MultiPolygon

TEMP = "TEMPORARY_OUTPUT"

# Drifttype-værdi i markkortet -> feltnavn i resultatet (rækkefølge = kolonnerækkefølge)
DRIFTTYPER = [
    ("Omdrift", "Omdrift_area"),
    ("PermGraes", "PermGræs_area"),
    ("NATUR", "Natur_area"),
    ("OevrigeIMK", "ØvrigeIMK_area"),
]

LAG_RANG = {"uden_buffer": 10, "med_buffer": 20}
TOERV_RANG = {">12": 1, "6-12": 2, "3-6": 3, "<3": 4}


class CO2BeregningAlgorithm(QgsProcessingAlgorithm):
    CO2 = "CO2"
    TOERV_FELT = "TOERV_FELT"
    PROJEKT = "PROJEKT"
    MARKKORT = "MARKKORT"
    DRIFTTYPE = "DRIFTTYPE"
    AFVAERGE = "AFVAERGE"
    BUFFER = "BUFFER"
    OUTPUT = "OUTPUT"
    OUTPUT_BUFFER = "OUTPUT_BUFFER"
    PROJEKTNAVN = "PROJEKTNAVN"
    SKABELON = "SKABELON"
    ARK = "ARK"
    AABN = "AABN"

    def __init__(self):
        super().__init__()
        self._ark_sti = None
        self._aabn = False

    # ------------------------------------------------------------------ meta
    def createInstance(self):  # noqa: N802
        return CO2BeregningAlgorithm()

    def name(self):
        return "co2_beregning"

    def displayName(self):  # noqa: N802
        return "CO2-beregning"

    def group(self):
        return ""

    def groupId(self):  # noqa: N802
        return ""

    def shortHelpString(self):  # noqa: N802
        return (
            "<p>Beregner arealer (ha) pr. <b>tørveklasse</b> og <b>drifttype</b> inden for "
            "projektgrænsen – opdelt i områder <b>uden for</b> og <b>inden for</b> en buffer "
            "om afvandende afværgeforanstaltninger.</p>"
            "<h3>Input</h3>"
            "<ul>"
            "<li><b>CO2-kort (Tekstur14/Kulstof22)</b>: polygonlag med tørveklasser.</li>"
            "<li><b>Tørveklasse-felt</b>: feltet med tørveklasse (fx '&gt;12', '6-12', '3-6', '&lt;3').</li>"
            "<li><b>Projektgrænse</b>: polygonlag.</li>"
            "<li><b>Markkort uden søer</b> og feltet med <b>drifttype</b>. Værdierne "
            "'Omdrift', 'PermGraes', 'NATUR' og 'OevrigeIMK' tælles med; andre værdier "
            "nævnes i loggen.</li>"
            "<li><b>Afværgeforanstaltninger (som afvander)</b>: linjelag, der buffres med "
            "<b>bufferbredden</b>.</li>"
            "</ul>"
            "<h3>Output</h3>"
            "<ul>"
            "<li><b>CO2 resultat</b>: én række pr. tørveklasse for hhv. 'uden_buffer' og "
            "'med_buffer' med arealer i hektar. Vælg 'Gem til fil…' og filtypen .xlsx for "
            "at få et Excel-ark.</li>"
            "<li><b>Buffer til CO2-beregningskort</b>: markerne inden for bufferen.</li>"
            "<li><b>Udfyldt beregningsark</b>: vælg Miljøstyrelsens <i>Beregningsark CO2-effekt</i> "
            "(.xlsx) som skabelon, så skrives projektnavn, projektareal (D4/D5) og arealerne "
            "(E27:E50) på fanen 'Beregningsark' i en kopi, som åbnes bagefter. "
            "Tørveklasserne 3-6 og &lt;3 lægges sammen til '&lt; 6 %'. Skabelonen ændres ikke.</li>"
            "</ul>"
            "<p>D5 er det samlede areal af laget Projektgrænse. E51 (kulstofrig jord uden aktiv "
            "udtagning) får formlen =D5-SUM(E27:E50), dvs. det resterende areal, så arealtjekket "
            "i E53 viser 'OK'. Spørgsmålene om tiltag udfyldes ikke automatisk.</p>"
        )

    # ------------------------------------------------------------ parametre
    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.CO2, "CO2-kort (Tekstur14/Kulstof22)", [SRC_POLYGON]))
        self.addParameter(QgsProcessingParameterField(
            self.TOERV_FELT, "Tørveklasse-felt i CO2-kortet",
            defaultValue="Toerv_pct", parentLayerParameterName=self.CO2))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.PROJEKT, "Projektgrænse", [SRC_POLYGON]))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.MARKKORT, "Markkort uden søer", [SRC_POLYGON]))
        self.addParameter(QgsProcessingParameterField(
            self.DRIFTTYPE, "Drifttype-felt i markkortet",
            defaultValue="Drifttype", parentLayerParameterName=self.MARKKORT))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.AFVAERGE, "Afværgeforanstaltninger (som afvander)", [SRC_LINE]))
        self.addParameter(QgsProcessingParameterDistance(
            self.BUFFER, "Bufferbredde", defaultValue=7.5,
            parentParameterName=self.AFVAERGE, minValue=0.0, maxValue=100.0))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, "CO2 resultat (gem som .xlsx)"))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_BUFFER, "Buffer til CO2-beregningskort",
            createByDefault=True))

        # Beregningsark CO2-effekt (Miljøstyrelsen)
        self.addParameter(QgsProcessingParameterString(
            self.PROJEKTNAVN, "Projektnavn (til beregningsarket)", optional=True))
        self.addParameter(QgsProcessingParameterFile(
            self.SKABELON, "Beregningsark CO2-effekt – skabelon (.xlsx)",
            extension="xlsx", optional=True))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.ARK, "Udfyldt beregningsark", fileFilter="Excel (*.xlsx)",
            optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.AABN, "Åbn beregningsarket når det er udfyldt", defaultValue=True))

    # -------------------------------------------------------------- hjælpere
    @staticmethod
    def _run(alg, params, context, feedback):
        from qgis import processing
        return processing.run(alg, params, context=context, feedback=feedback,
                              is_child_algorithm=True)["OUTPUT"]

    @staticmethod
    def _layer(layer_id, context):
        lyr = QgsProcessingUtils.mapLayerFromString(layer_id, context)
        if lyr is None:
            raise QgsProcessingException("Kunne ikke indlæse mellemresultat: {}".format(layer_id))
        return lyr

    @staticmethod
    def _valid(geom):
        if geom is None or geom.isEmpty():
            return QgsGeometry()
        if not geom.isGeosValid():
            geom = geom.makeValid()
        return geom

    def _geom_pr_drifttype(self, layer, felt, dst_crs, context, feedback, etiket):
        """Saml (union) geometrier pr. drifttype. Overlappende buffer-stykker tælles kun én gang."""
        idx = layer.fields().lookupField(felt)
        if idx < 0:
            raise QgsProcessingException(
                "Feltet '{}' findes ikke i mellemresultatet ({}).".format(felt, etiket))

        xform = None
        if layer.crs() != dst_crs:
            xform = QgsCoordinateTransform(layer.crs(), dst_crs, context.transformContext())

        samlet = {}
        for f in layer.getFeatures():
            g = f.geometry()
            if g is None or g.isEmpty():
                continue
            g = QgsGeometry(g)
            if xform is not None:
                g.transform(xform)
            samlet.setdefault(f[idx], []).append(self._valid(g))

        kendte = {k for k, _ in DRIFTTYPER}
        ukendte = [str(v) for v in samlet if v not in kendte]
        if ukendte:
            feedback.pushWarning(
                "{}: drifttype-værdier der ikke indgår i beregningen: {}".format(
                    etiket, ", ".join(sorted(ukendte))))

        resultat = {}
        for vaerdi, _ in DRIFTTYPER:
            geoms = samlet.get(vaerdi)
            resultat[vaerdi] = self._valid(QgsGeometry.unaryUnion(geoms)) if geoms else QgsGeometry()
        return resultat

    # ---------------------------------------------------------------- kørsel
    def processAlgorithm(self, parameters, context, model_feedback):  # noqa: N802
        feedback = QgsProcessingMultiStepFeedback(10, model_feedback)

        co2 = self.parameterAsVectorLayer(parameters, self.CO2, context)
        projekt = self.parameterAsVectorLayer(parameters, self.PROJEKT, context)
        markkort = self.parameterAsVectorLayer(parameters, self.MARKKORT, context)
        afvaerge = self.parameterAsVectorLayer(parameters, self.AFVAERGE, context)
        toerv_felt = self.parameterAsString(parameters, self.TOERV_FELT, context) or "Toerv_pct"
        drift_felt = self.parameterAsString(parameters, self.DRIFTTYPE, context) or "Drifttype"
        bredde = self.parameterAsDouble(parameters, self.BUFFER, context)

        for lyr, navn in ((co2, "CO2-kort"), (projekt, "Projektgrænse"),
                          (markkort, "Markkort"), (afvaerge, "Afværgeforanstaltninger")):
            if lyr is None:
                raise QgsProcessingException("Lag mangler: {}".format(navn))
        if co2.fields().lookupField(toerv_felt) < 0:
            raise QgsProcessingException("Feltet '{}' findes ikke i CO2-kortet.".format(toerv_felt))
        if markkort.fields().lookupField(drift_felt) < 0:
            raise QgsProcessingException("Feltet '{}' findes ikke i markkortet.".format(drift_felt))

        # 1. Klip markkort til projektgrænse og opløs efter drifttype
        feedback.setCurrentStep(0)
        feedback.pushInfo("Klipper markkort til projektgrænsen …")
        mark_klip = self._run("native:clip", {
            "INPUT": markkort, "OVERLAY": projekt, "OUTPUT": TEMP},
            context, feedback)
        if feedback.isCanceled():
            return {}

        feedback.setCurrentStep(1)
        mark_oploest = self._run("native:dissolve", {
            "INPUT": mark_klip, "FIELD": [drift_felt], "SEPARATE_DISJOINT": False,
            "OUTPUT": TEMP}, context, feedback)
        if feedback.isCanceled():
            return {}

        # 2. Buffer om afværgeforanstaltninger, klippet til projektgrænse
        feedback.setCurrentStep(2)
        feedback.pushInfo("Buffer om afværgeforanstaltninger ({} m) …".format(bredde))
        buf = self._run("native:buffer", {
            "INPUT": afvaerge, "DISTANCE": bredde, "SEGMENTS": 5, "END_CAP_STYLE": 0,
            "JOIN_STYLE": 0, "MITER_LIMIT": 2, "DISSOLVE": False, "OUTPUT": TEMP},
            context, feedback)
        if feedback.isCanceled():
            return {}

        feedback.setCurrentStep(3)
        buf_klip = self._run("native:clip", {
            "INPUT": buf, "OVERLAY": projekt, "OUTPUT": TEMP}, context, feedback)
        if feedback.isCanceled():
            return {}

        # 3. Marker inden for (skæring) og uden for (difference) bufferen
        feedback.setCurrentStep(4)
        feedback.pushInfo("Skæring og difference med bufferen …")
        med = self._run("native:intersection", {
            "INPUT": mark_oploest, "OVERLAY": buf_klip, "INPUT_FIELDS": [],
            "OVERLAY_FIELDS": [], "OUTPUT": TEMP}, context, feedback)
        if feedback.isCanceled():
            return {}

        feedback.setCurrentStep(5)
        uden = self._run("native:difference", {
            "INPUT": mark_oploest, "OVERLAY": buf_klip, "OUTPUT": TEMP}, context, feedback)
        if feedback.isCanceled():
            return {}

        # 4. CO2-kort klippet og opløst efter tørveklasse
        feedback.setCurrentStep(6)
        feedback.pushInfo("Klipper og opløser CO2-kort efter '{}' …".format(toerv_felt))
        co2_klip = self._run("native:clip", {
            "INPUT": co2, "OVERLAY": projekt, "OUTPUT": TEMP}, context, feedback)
        if feedback.isCanceled():
            return {}

        feedback.setCurrentStep(7)
        co2_oploest = self._run("native:dissolve", {
            "INPUT": co2_klip, "FIELD": [toerv_felt], "SEPARATE_DISJOINT": False,
            "OUTPUT": TEMP}, context, feedback)
        if feedback.isCanceled():
            return {}

        # 5. Arealberegning
        feedback.setCurrentStep(8)
        feedback.pushInfo("Beregner arealer …")
        co2_lag = self._layer(co2_oploest, context)
        med_lag = self._layer(med, context)
        uden_lag = self._layer(uden, context)
        crs = co2_lag.crs()

        da = QgsDistanceArea()
        da.setSourceCrs(crs, context.transformContext())
        da.setEllipsoid(context.ellipsoid())

        geom_uden = self._geom_pr_drifttype(uden_lag, drift_felt, crs, context, feedback, "uden_buffer")
        geom_med = self._geom_pr_drifttype(med_lag, drift_felt, crs, context, feedback, "med_buffer")

        t_idx = co2_lag.fields().lookupField(toerv_felt)
        raekker = []
        for f in co2_lag.getFeatures():
            tg = self._valid(QgsGeometry(f.geometry()))
            if tg.isEmpty():
                continue
            v = f[t_idx]
            toerv = None if v is None or str(v) == "NULL" else str(v)
            for lag_navn, geoms in (("uden_buffer", geom_uden), ("med_buffer", geom_med)):
                arealer = []
                for vaerdi, _ in DRIFTTYPER:
                    dg = geoms[vaerdi]
                    if dg.isEmpty():
                        arealer.append(0.0)
                        continue
                    skaer = tg.intersection(dg)
                    a = da.measureArea(skaer) if skaer and not skaer.isEmpty() else 0.0
                    arealer.append(round(a / 10000.0, 2))
                raekker.append((lag_navn, toerv, tg, arealer))
            if feedback.isCanceled():
                return {}

        # 6. Sortering og output
        feedback.setCurrentStep(9)
        raekker.sort(key=lambda r: (LAG_RANG.get(r[0], 90), TOERV_RANG.get(r[1], 9)))

        felter = QgsFields()
        felter.append(QgsField("Sortering", T_INT))
        felter.append(QgsField("layer", T_STRING, len=15))
        felter.append(QgsField("Toerv_pct", T_STRING))
        for _, feltnavn in DRIFTTYPER:
            felter.append(QgsField(feltnavn, T_DOUBLE, len=20, prec=2))

        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context, felter, WKB_MULTIPOLYGON, crs)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        for nr, (lag_navn, toerv, geom, arealer) in enumerate(raekker, start=1):
            feat = QgsFeature(felter)
            g = QgsGeometry(geom)
            g.convertToMultiType()
            feat.setGeometry(g)
            feat.setAttributes([nr, lag_navn, toerv] + arealer)
            sink.addFeature(feat)

        resultater = {self.OUTPUT: dest_id}

        # Buffer-output (marker inden for bufferen)
        buf_sink, buf_id = self.parameterAsSink(
            parameters, self.OUTPUT_BUFFER, context, med_lag.fields(),
            med_lag.wkbType(), med_lag.crs())
        if buf_sink is not None:
            for f in med_lag.getFeatures():
                buf_sink.addFeature(f)
            resultater[self.OUTPUT_BUFFER] = buf_id

        # Kort opsummering i loggen
        for lag_navn in ("uden_buffer", "med_buffer"):
            summer = [0.0] * len(DRIFTTYPER)
            for r in raekker:
                if r[0] == lag_navn:
                    summer = [s + a for s, a in zip(summer, r[3])]
            feedback.pushInfo("{}: {}".format(lag_navn, ", ".join(
                "{} {:.2f} ha".format(n.replace("_area", ""), s)
                for (_, n), s in zip(DRIFTTYPER, summer))))

        # Beregningsark CO2-effekt
        skabelon = self.parameterAsFile(parameters, self.SKABELON, context)
        if skabelon:
            ark_sti = self._udfyld_ark(parameters, context, feedback, skabelon,
                                       projekt, raekker)
            resultater[self.ARK] = ark_sti

        return resultater

    def _udfyld_ark(self, parameters, context, feedback, skabelon, projekt, raekker):
        feedback.pushInfo("Udfylder beregningsark …")
        ark_sti = self.parameterAsFileOutput(parameters, self.ARK, context)
        if not ark_sti:
            ark_sti = QgsProcessingUtils.generateTempFilename("beregningsark_co2_effekt.xlsx")
        if not ark_sti.lower().endswith(".xlsx"):
            ark_sti += ".xlsx"

        navn = self.parameterAsString(parameters, self.PROJEKTNAVN, context)
        if not navn and context.project() is not None:
            navn = context.project().title() or context.project().baseName()

        # Samlet projektareal (ha), overlap mellem projektpolygoner tælles én gang
        da = QgsDistanceArea()
        da.setSourceCrs(projekt.crs(), context.transformContext())
        da.setEllipsoid(context.ellipsoid())
        geoms = [self._valid(QgsGeometry(f.geometry())) for f in projekt.getFeatures()
                 if f.hasGeometry()]
        projekt_ha = da.measureArea(QgsGeometry.unaryUnion(geoms)) / 10000.0 if geoms else 0.0

        blokke, ukendte = beregningsark.saml_arealer(
            [(r[0], r[1], r[3]) for r in raekker])
        for klasse, areal in ukendte.items():
            if areal > 0:
                feedback.pushWarning(
                    "Tørveklasse '{}' passer ikke til arket – {:.2f} ha er ikke medtaget."
                    .format(klasse, areal))

        try:
            fordelt_ha, rest_ha = beregningsark.udfyld(skabelon, ark_sti, navn, projekt_ha, blokke)
        except beregningsark.BeregningsarkFejl as e:
            raise QgsProcessingException(str(e))
        except Exception as e:  # beskadiget eller forkert fil
            raise QgsProcessingException("Kunne ikke udfylde beregningsarket: {}".format(e))

        feedback.pushInfo("Beregningsark gemt: {}".format(ark_sti))
        feedback.pushInfo("D5 projektareal (Projektgrænse): {:.2f} ha".format(projekt_ha))
        feedback.pushInfo("Fordelt areal E27:E50: {:.2f} ha".format(fordelt_ha))
        feedback.pushInfo("E51 = D5 - SUM(E27:E50): {:.2f} ha".format(rest_ha))
        if rest_ha < 0:
            feedback.pushWarning(
                "E51 er negativ ({:.2f} ha): de fordelte arealer er større end projektarealet. "
                "Tjek om markkort/CO2-kort går uden for projektgrænsen.".format(rest_ha))

        self._ark_sti = ark_sti
        self._aabn = self.parameterAsBoolean(parameters, self.AABN, context)
        return ark_sti

    def postProcessAlgorithm(self, context, feedback):  # noqa: N802
        # Kører i hovedtråden – her må vi åbne filer i andre programmer
        if self._aabn and self._ark_sti:
            from qgis.PyQt.QtCore import QUrl
            from qgis.PyQt.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._ark_sti))
        return {}
