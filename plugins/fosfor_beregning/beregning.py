"""Beregningskernen i Fosfor beregning.

Følger modellen "Fosformodel_okt_2025" trin for trin:

 1. Fix geometrier (jordprøvefelter)        -> klip med projektgrænsen
 2. Fix geometrier (jordbundskort)          -> klip med de klippede jordprøvefelter
 3. Opløs jordbundskortet på jordtype
 4. Intersection jordtype x jordprøvefelt
 5. Join til jordprøvefelterne efter største overlap (dominerende jordtype)
 6. Zonal statistik på fremtidsmodellen (count, sum, mean)
 7. Refactor til Fosfor-regnearkets kolonner og sortér efter Id
"""

import processing
from qgis.core import (
    QgsFeatureRequest,
    QgsGeometry,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingUtils,
    QgsProject,
    QgsVectorFileWriter,
    QgsWkbTypes,
)

# Interne feltnavne (undgår navnekonflikter med brugerens felter)
F_JORDTYPE = "_fb_jordtype"
STAT_PREFIX = "_fb_"
F_MEAN = STAT_PREFIX + "mean"

# Permeabilitet pr. jordtype (som i modellen)
PERMEABILITET = {
    "svær lerjord": 0,
    "lerjord": 0,
    "humusjord": 0.5,
    "finsandet jord": 0.5,
    "fin sandblandet lerjord": 0.5,
    "fin lerblandet sandjord": 0.5,
    "grovsandet jord": 1,
    "grov lerblandet sandjord": 1,
    "grov sandblandet lerjord": 1,
}

FIELD_DOUBLE = 6
FIELD_STRING = 10
FIELD_INT = 4  # LongLong (int8), som i modellen


class BeregningsFejl(Exception):
    pass


def _q(name):
    return '"' + name.replace('"', '""') + '"'


def _permeabilitet_expression():
    lines = ["CASE"]
    for jordtype, value in PERMEABILITET.items():
        lines.append(
            "  WHEN lower(trim({f})) = '{jt}' THEN {v}".format(
                f=_q(F_JORDTYPE), jt=jordtype, v=value))
    lines.append("  ELSE NULL")
    lines.append("END")
    return "\n".join(lines)


def _field(name, expression, ftype, length=0, precision=0):
    type_names = {FIELD_DOUBLE: "double precision", FIELD_STRING: "text", FIELD_INT: "int8"}
    return {
        "name": name,
        "expression": expression,
        "type": ftype,
        "type_name": type_names[ftype],
        "length": length,
        "precision": precision,
        "sub_type": 0,
        "alias": "",
        "comment": "",
    }


class FosforBeregning:
    def __init__(self, projektgraense, proevefelter, id_felt, afvanding,
                 jordbundskort, jordtype_felt, feedback=None, log=None):
        self.projektgraense = projektgraense
        self.proevefelter = proevefelter
        self.id_felt = id_felt
        self.afvanding = afvanding
        self.jordbundskort = jordbundskort
        self.jordtype_felt = jordtype_felt
        self.feedback = feedback or QgsProcessingFeedback()
        self.log = log or (lambda msg: None)
        self.warnings = []

        self.context = QgsProcessingContext()
        self.context.setProject(QgsProject.instance())
        self.context.setFeedback(self.feedback)
        self.context.setInvalidGeometryCheck(QgsFeatureRequest.GeometryNoCheck)

    # ------------------------------------------------------------------
    def _run(self, alg, params, step, total):
        if self.feedback.isCanceled():
            raise BeregningsFejl("Beregningen blev afbrudt.")
        self.feedback.setProgress(100.0 * step / total)
        params = dict(params)
        params.setdefault("OUTPUT", "TEMPORARY_OUTPUT")
        result = processing.run(alg, params, context=self.context,
                                feedback=self.feedback, is_child_algorithm=True)
        layer = QgsProcessingUtils.mapLayerFromString(result["OUTPUT"], self.context)
        if layer is None:
            raise BeregningsFejl("Trinnet {} gav intet resultat.".format(alg))
        return layer

    def _warn(self, msg):
        self.warnings.append(msg)
        self.log("<span style='color:#b35900'>Advarsel: {}</span>".format(msg))

    # ------------------------------------------------------------------
    def validate(self):
        for lyr, navn in ((self.projektgraense, "Projektgrænse"),
                          (self.proevefelter, "Jordprøvefelter"),
                          (self.jordbundskort, "Jordbundskort")):
            if lyr is None or not lyr.isValid():
                raise BeregningsFejl("Vælg et gyldigt lag for {}.".format(navn))
            if lyr.geometryType() != QgsWkbTypes.PolygonGeometry:
                raise BeregningsFejl("{} skal være et polygonlag.".format(navn))
        if self.afvanding is None or not self.afvanding.isValid():
            raise BeregningsFejl("Vælg et gyldigt rasterlag for fremtidsmodellen (afvanding).")
        if not self.id_felt or self.proevefelter.fields().indexOf(self.id_felt) < 0:
            raise BeregningsFejl("Vælg ID-feltet i jordprøvefelterne.")
        if not self.jordtype_felt or self.jordbundskort.fields().indexOf(self.jordtype_felt) < 0:
            raise BeregningsFejl("Vælg jordtype-feltet i jordbundskortet.")

        # ID-felt: tomme og dublerede værdier
        ids, tomme, dubletter = set(), 0, set()
        for f in self.proevefelter.getFeatures():
            v = f[self.id_felt]
            if v is None or str(v).strip() == "" or str(v) == "NULL":
                tomme += 1
                continue
            if v in ids:
                dubletter.add(v)
            ids.add(v)
        if tomme:
            self._warn("{} jordprøvefelt(er) mangler en værdi i ID-feltet '{}'.".format(tomme, self.id_felt))
        if dubletter:
            self._warn("Dublerede ID'er i jordprøvefelterne: {}.".format(
                ", ".join(str(d) for d in sorted(dubletter, key=str))))

    # ------------------------------------------------------------------
    def run(self):
        self.validate()
        total = 10

        self.log("Retter geometrier i jordprøvefelterne …")
        felter_fix = self._run("native:fixgeometries",
                               {"INPUT": self.proevefelter, "METHOD": 1}, 0, total)

        self.log("Klipper jordprøvefelterne med projektgrænsen …")
        felter = self._run("native:clip",
                           {"INPUT": felter_fix, "OVERLAY": self.projektgraense}, 1, total)
        if felter.featureCount() == 0:
            raise BeregningsFejl("Ingen jordprøvefelter ligger inden for projektgrænsen.")
        self._tjek_daekning(felter)

        self.log("Retter geometrier i jordbundskortet …")
        jb_fix = self._run("native:fixgeometries",
                           {"INPUT": self.jordbundskort, "METHOD": 1}, 2, total)

        self.log("Klipper jordbundskortet til jordprøvefelterne …")
        jb_clip = self._run("native:clip", {"INPUT": jb_fix, "OVERLAY": felter}, 3, total)
        if jb_clip.featureCount() == 0:
            raise BeregningsFejl("Jordbundskortet dækker ikke jordprøvefelterne.")

        self.log("Opløser jordbundskortet på jordtype …")
        jb_diss = self._run("native:dissolve",
                            {"INPUT": jb_clip, "FIELD": [self.jordtype_felt],
                             "SEPARATE_DISJOINT": False}, 4, total)

        self.log("Intersection mellem jordtyper og jordprøvefelter …")
        inters = self._run("native:intersection",
                           {"INPUT": jb_diss, "OVERLAY": felter,
                            "INPUT_FIELDS": [self.jordtype_felt],
                            "OVERLAY_FIELDS": [self.id_felt],
                            "OVERLAY_FIELDS_PREFIX": "_fbo_"}, 5, total)

        jordtyper = self._run("native:refactorfields", {
            "INPUT": inters,
            "FIELDS_MAPPING": [
                _field(F_JORDTYPE, _q(self.jordtype_felt), FIELD_STRING, 200),
            ]}, 6, total)

        self.log("Finder dominerende jordtype i hvert jordprøvefelt …")
        joined = self._run("native:joinattributesbylocation", {
            "INPUT": felter,
            "JOIN": jordtyper,
            "PREDICATE": [0],          # skærer
            "JOIN_FIELDS": [F_JORDTYPE],
            "METHOD": 2,               # største overlap
            "DISCARD_NONMATCHING": False,
            "PREFIX": "",
        }, 7, total)

        self.log("Zonal statistik på fremtidsmodellen …")
        zonal = self._run("native:zonalstatisticsfb", {
            "INPUT": joined,
            "INPUT_RASTER": self.afvanding,
            "RASTER_BAND": 1,
            "COLUMN_PREFIX": STAT_PREFIX,
            "STATISTICS": [0, 1, 2],   # count, sum, mean
        }, 8, total)

        self.log("Opbygger kolonner til Fosfor-regnearket …")
        mean = _q(F_MEAN)
        mapping = [
            self._id_mapping(),
            _field("Areal", "round($area/10000, 2)", FIELD_DOUBLE, 10, 2),
            _field("Type af område",
                   "CASE\n"
                   " WHEN {m} IS NULL THEN NULL\n"
                   " WHEN {m} < 0.2 THEN 'permanent vådt'\n"
                   " WHEN {m} < 0.75 THEN 'Delvist vådt'\n"
                   " ELSE 'tørt'\n"
                   "END".format(m=mean), FIELD_STRING, 20),
            _field("Over vandloebets middel vandstand",
                   "CASE\n"
                   " WHEN {m} IS NULL THEN NULL\n"
                   " WHEN {m} >= 0.5 THEN '>50'\n"
                   " ELSE '<50'\n"
                   "END".format(m=mean), FIELD_STRING, 20),
            _field("Tomt felt", "' '", FIELD_STRING, 1),
            _field("Grundvandsdybde", "round({m}, 2)".format(m=mean), FIELD_DOUBLE, 20, 2),
            _field("Tekstur", _q(F_JORDTYPE), FIELD_STRING, 50),
            _field("Permeabilitet", _permeabilitet_expression(), FIELD_DOUBLE, 20, 1),
        ]
        refac = self._run("native:refactorfields",
                          {"INPUT": zonal, "FIELDS_MAPPING": mapping}, 9, total)

        # Sorter efter Id – numerisk hvis alle ID'er er tal
        numerisk = all(self._er_tal(f["Id"]) for f in refac.getFeatures())
        expr = 'to_real("Id")' if numerisk else '"Id"'
        result = self._run("native:orderbyexpression", {
            "INPUT": refac, "EXPRESSION": expr,
            "ASCENDING": True, "NULLS_FIRST": False}, 9.5, total)
        self.feedback.setProgress(100)

        self._tjek_resultat(result)
        result.setName("Fosfor beregning – resultat")
        return result

    # ------------------------------------------------------------------
    def _id_mapping(self):
        """Id-kolonnen beholder kildefeltets type, så tal forbliver tal i Excel."""
        fld = self.proevefelter.fields().field(self.id_felt)
        if fld.isNumeric():
            if fld.type() in (6,):  # QVariant.Double
                return _field("Id", _q(self.id_felt), FIELD_DOUBLE, 20, fld.precision())
            return _field("Id", _q(self.id_felt), FIELD_INT, 10)
        return _field("Id", _q(self.id_felt), FIELD_STRING, 50)

    @staticmethod
    def _er_tal(v):
        try:
            float(str(v).replace(",", "."))
            return True
        except (TypeError, ValueError):
            return False

    def _tjek_daekning(self, felter):
        """Modellen kræver at jordprøvefelterne dækker hele projektområdet."""
        try:
            pg = [f.geometry() for f in self.projektgraense.getFeatures() if f.hasGeometry()]
            fg = [f.geometry() for f in felter.getFeatures() if f.hasGeometry()]
            if not pg or not fg:
                return
            pg_union = QgsGeometry.unaryUnion(pg)
            fg_union = QgsGeometry.unaryUnion(fg)
            if self.projektgraense.crs() != felter.crs():
                return  # lagene er i forskellige CRS – spring tjekket over
            a_pg = pg_union.area()
            a_fg = fg_union.area()
            if a_pg > 0 and a_fg / a_pg < 0.99:
                self._warn(
                    "Jordprøvefelterne dækker kun {:.1f} % af projektområdet "
                    "({:.2f} ha af {:.2f} ha). De skal dække hele projektområdet.".format(
                        100 * a_fg / a_pg, a_fg / 10000, a_pg / 10000))
        except Exception:  # tjekket må aldrig vælte beregningen
            pass

    def _tjek_resultat(self, layer):
        uden_jordtype, ukendte, uden_dybde = [], set(), []
        for f in layer.getFeatures():
            if f["Tekstur"] in (None, "") or str(f["Tekstur"]) == "NULL":
                uden_jordtype.append(str(f["Id"]))
            elif f["Permeabilitet"] is None or str(f["Permeabilitet"]) == "NULL":
                ukendte.add(str(f["Tekstur"]))
            if f["Grundvandsdybde"] is None or str(f["Grundvandsdybde"]) == "NULL":
                uden_dybde.append(str(f["Id"]))
        if uden_jordtype:
            self._warn("Ingen jordtype fra jordbundskortet for felt: {}.".format(", ".join(uden_jordtype)))
        if ukendte:
            self._warn("Ukendt jordtype – permeabilitet ikke sat: {}.".format(", ".join(sorted(ukendte))))
        if uden_dybde:
            self._warn("Ingen værdi fra fremtidsmodellen (uden for rasteret?) for felt: {}.".format(
                ", ".join(uden_dybde)))


def gem_som_xlsx(layer, path):
    if not path.lower().endswith(".xlsx"):
        path += ".xlsx"
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "XLSX"
    options.fileEncoding = "UTF-8"
    options.layerName = "Fosfor"
    options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
    ctx = QgsProject.instance().transformContext()
    if hasattr(QgsVectorFileWriter, "writeAsVectorFormatV3"):
        res = QgsVectorFileWriter.writeAsVectorFormatV3(layer, path, ctx, options)
    else:
        res = QgsVectorFileWriter.writeAsVectorFormatV2(layer, path, ctx, options)
    err = res[0]
    if err != QgsVectorFileWriter.NoError:
        msg = res[1] if len(res) > 1 else ""
        raise BeregningsFejl("Kunne ikke gemme {}: {}".format(path, msg))
    return path
