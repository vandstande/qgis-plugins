# -*- coding: utf-8 -*-
"""
TimFlow dialog - selve plugin-vinduet.
"""
import os
import re
import subprocess
import textwrap

from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QPushButton,
    QLabel,
    QMessageBox,
    QInputDialog,
    QFileDialog,
)

from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsVectorFileWriter,
    QgsField,
    QgsFields,
    QgsWkbTypes,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsLayerTreeGroup,
    QgsProviderRegistry,
    QgsSimpleFillSymbolLayer,
    QgsFillSymbol,
    QgsUnitTypes,
)
from .compat import GEOM_POINT, GEOM_POLYGON, RENDER_MM, VFW_NO_ERROR, VFW_OVERWRITE_FILE, VFW_OVERWRITE_LAYER, WKB_POINT, WKB_POLYGON, felttype


FALLBACK_CRS = "EPSG:25832"


# Skabelon for det pythonscript som knappen "Beregn" genererer.
# Bemærk: dette er en raadata (raw) skabelon - "\n" nedenfor skal forblive
# to tegn (backslash + n) i den GENEREREDE fil, da det er kildekode til
# den efterfølgende beregning (headergenerering i .grd-filen).
# Al kode ligger i main() under 'if __name__ == "__main__"', så parallel=True
# (som starter nye processer på Windows) ikke får scriptet til at genstarte.
# Tegn der skal stå som {{ }} i det genererede script er fordoblet pga. .format().
TEMPLATE = r'''import numpy as np
import timflow.steady as tfs
import matplotlib.pyplot as plt
import tkinter as tk
from tkinter import filedialog


def main():
    #*********************************************************
    #                  Opsætning af model                    *
    #*********************************************************
    # Modelområde
    x_nv = {x_nv}  # Nederste venstre hjørne
    y_nv = {y_nv}
    x_oh = {x_oh}  # Øverste højre hjørne
    y_oh = {y_oh}
    grid_x = {grid_x}  # Antal grids i x retningen, jo flere grids jo længere beregningstid
    grid_y = {grid_y}  # Antal grids i y retningen
    jordtype = {jordtype}  # permeabiliteten i m/d, hvis du har den i m/s skal du gange med 86.400
    # Referencepunkt
    xref = {xref}
    yref = {yref}
    vstref = {vstref}
    #***********************************************************
    ml = tfs.ModelMaq(kaq=jordtype, z=[40, 0], npor=0.3)
    # ****************************************************************************
    # *                           Model elementer                                *
    #*****************************************************************************
{elements}
    #***************************************************************************************************
    #*                                            RET IKKE HERUNDER                                    *
    #***************************************************************************************************
    rf = tfs.Constant(ml, xr=xref, yr=yref, hr=vstref, layer=0)  # Specificerer vandstanden i et punkt
    figsize = (6, 6)
    ml.solve()
    ml.plots.contour(
        win=[x_nv, x_oh, y_nv, y_oh],
        ngr=50,
        layers=[0],
        levels=30,
        color="C0",
        legend=True,
        figsize=figsize,
    )
    plt.show()

    print("Genererer grid .........")
    headvst = ml.headgrid2(x_nv, x_oh, grid_x, y_nv, y_oh, grid_y,
                           show_progress=True, parallel=True, layers=0)
    data = headvst.squeeze()

    print("Gemmer resultatet.......")
    root = tk.Tk()
    root.withdraw()
    filnavn = filedialog.asksaveasfilename(
        title="Gem model som",
        initialdir="C:/",
        initialfile="{modelnavn}.grd",
        defaultextension=".grd",
        filetypes=[("Gridfil", "*.grd"), ("Alle filer", "*.*")],
    )
    root.destroy()

    if filnavn:
        # Surfer ASCII (DSAA): nx ny / xmin xmax / ymin ymax / zmin zmax / data (række 1 = ymin)
        header = (
            f"DSAA\n"
            f"{{grid_x}} {{grid_y}}\n"
            f"{{x_nv}} {{x_oh}}\n"
            f"{{y_nv}} {{y_oh}}\n"
            f"{{np.nanmin(data)}} {{np.nanmax(data)}}"
        )
        np.savetxt(filnavn, data, header=header, comments="")
    print("Model er færdig")


if __name__ == "__main__":
    main()
'''


class TimFlowDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent or iface.mainWindow())
        self.iface = iface

        self.gpkg_path = None
        self.model_name = None
        self.group = None

        self.setWindowTitle("TimFlow")
        self.setMinimumWidth(260)
        # Ikke-modal: dialogen skal forblive åben og ikke blokere QGIS
        self.setModal(False)

        layout = QVBoxLayout()

        self.status_label = QLabel("Ingen model valgt.\nBrug 'Nyt' for at oprette en model,\neller 'Rediger' for at åbne en eksisterende.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        btn_new = QPushButton("Ny model")
        btn_new.clicked.connect(self.new_model)
        layout.addWidget(btn_new)

        btn_edit = QPushButton("Rediger model")
        btn_edit.clicked.connect(self.edit_model)
        layout.addWidget(btn_edit)

        btn_vandstand = QPushButton("Tilføj vandstande")
        btn_vandstand.clicked.connect(self.add_vandstand)
        layout.addWidget(btn_vandstand)

        btn_slet_vandstand = QPushButton("Slet vandstand")
        btn_slet_vandstand.clicked.connect(self.delete_vandstand)
        layout.addWidget(btn_slet_vandstand)

        btn_ref = QPushButton("Reference punkt")
        btn_ref.clicked.connect(self.reference_punkt)
        layout.addWidget(btn_ref)

        btn_beregn = QPushButton("Beregn")
        btn_beregn.clicked.connect(self.beregn)
        layout.addWidget(btn_beregn)

        self.setLayout(layout)

    # ------------------------------------------------------------------
    # Hjælpefunktioner
    # ------------------------------------------------------------------
    def get_project_crs(self):
        crs = QgsProject.instance().crs()
        if not crs.isValid():
            crs = QgsCoordinateReferenceSystem(FALLBACK_CRS)
        return crs

    def _sanitize_varname(self, name):
        v = re.sub(r"\W+", "_", name.strip())
        if not v:
            v = "lag"
        if v[0].isdigit():
            v = "_" + v
        return v

    def _apply_model_symbology(self, layer):
        """Simple Fill, Fill style: No Brush, Stroke color: black, Stroke width: 1.00 mm"""
        symbol = QgsFillSymbol.createSimple({})
        while symbol.symbolLayerCount() > 0:
            symbol.deleteSymbolLayer(0)
        fill_layer = QgsSimpleFillSymbolLayer()
        fill_layer.setBrushStyle(Qt.BrushStyle.NoBrush)
        fill_layer.setStrokeColor(QColor(0, 0, 0))
        fill_layer.setStrokeWidth(1.00)
        fill_layer.setStrokeWidthUnit(RENDER_MM)
        symbol.appendSymbolLayer(fill_layer)
        layer.renderer().setSymbol(symbol)
        layer.triggerRepaint()

    def _get_or_create_group(self):
        root = QgsProject.instance().layerTreeRoot()
        group = root.findGroup(self.model_name)
        if group is None:
            group = root.insertGroup(0, self.model_name)
        self.group = group
        return group

    def _get_clean_windows_env(self):
        """
        Bygger et miljø (bl.a. PATH) svarende til det man ville få ved at åbne
        en almindelig CMD-prompt selv - altså IKKE det miljø QGIS/OSGeo4W selv
        kører med (som ofte har sin egen, indskrænkede PATH, hvor systemets
        python ikke nødvendigvis er tilgængelig eller er en anden version).
        """
        env = dict(os.environ)
        try:
            import winreg

            def read_path(hive, subkey):
                try:
                    with winreg.OpenKey(hive, subkey) as k:
                        val, _ = winreg.QueryValueEx(k, "Path")
                        return val
                except OSError:
                    return ""

            sys_path = read_path(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            )
            user_path = read_path(winreg.HKEY_CURRENT_USER, "Environment")
            combined = ";".join([p for p in (sys_path, user_path) if p])
            if combined:
                env["PATH"] = combined
        except Exception:
            # Ikke Windows, eller registry kunne ikke læses - brug det
            # eksisterende miljø som fallback.
            pass

        # Fjern QGIS/OSGeo4W-specifikke variabler, der ellers ville få den
        # nyfundne python.exe til fejlagtigt at indlæse biblioteker/DLL'er
        # fra QGIS' egen Python-installation (giver bl.a. "SRE module
        # mismatch" og lignende fejl).
        for var in (
            "PYTHONHOME",
            "PYTHONPATH",
            "PYTHONSTARTUP",
            "QGIS_PREFIX_PATH",
            "GDAL_DATA",
            "GDAL_DRIVER_PATH",
            "PROJ_LIB",
            "PROJ_DATA",
            "OSGEO4W_ROOT",
        ):
            env.pop(var, None)

        return env

    def _list_gpkg_tables(self):
        conn = QgsProviderRegistry.instance().providerMetadata("ogr").createConnection(self.gpkg_path, {})
        return [t.tableName() for t in conn.tables()]

    def _load_layer(self, layer_name):
        vl = QgsVectorLayer(f"{self.gpkg_path}|layername={layer_name}", layer_name, "ogr")
        return vl

    def _get_model_table(self):
        """
        Finder navnet på modellens polygonlag i geopackagen. Der er kun ét
        polygonlag i en model, så laget findes ud fra sin geometritype - navnet
        er frit (ved en ny model er det modelnavnet). Returnerer None hvis
        geopackagen ikke har noget polygonlag.
        """
        if not self.gpkg_path:
            return None
        for tname in self._list_gpkg_tables():
            if tname == "Reference":
                continue
            vl = self._load_layer(tname)
            if vl.isValid() and vl.geometryType() == GEOM_POLYGON:
                return tname
        return None

    def _get_model_feature(self):
        """
        Returnerer (feature, lagnavn) for første polygon i modellens polygonlag.
        Viser en fejlmeddelelse og returnerer None, hvis der ikke er noget
        polygonlag, eller det ikke indeholder en polygon.
        """
        tname = self._get_model_table()
        if tname is None:
            QMessageBox.critical(self, "Fejl", "Der findes intet polygonlag (modellag) i geopackagen.")
            return None
        layer = self._load_layer(tname)
        if layer.featureCount() == 0:
            QMessageBox.critical(self, "Fejl", f"Der er ikke oprettet en polygon i laget '{tname}'.")
            return None
        feat = next(layer.getFeatures())
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            QMessageBox.critical(self, "Fejl", f"Der er ikke oprettet en polygon i laget '{tname}'.")
            return None
        return feat, tname

    def _get_checked_layer_names(self):
        """
        Returnerer navnene (layername) på de lag fra self.gpkg_path, der har
        deres flueben (checkbox) markeret i lagpanelet. Lag der ikke er
        indlæst i projektet, regnes ikke med.
        """
        checked = set()
        root = QgsProject.instance().layerTreeRoot()
        for node in root.findLayers():
            layer = node.layer()
            if layer is None:
                continue
            source = layer.source()
            path = source.split("|")[0]
            if path != self.gpkg_path:
                continue
            if not node.isVisible():
                continue
            match = re.search(r"layername=([^|]+)", source)
            tname = match.group(1) if match else layer.name()
            checked.add(tname)
        return checked

    def _load_existing_layers_to_group(self):
        existing_sources = set()
        for lyr in QgsProject.instance().mapLayers().values():
            existing_sources.add(lyr.source())

        model_table = self._get_model_table()
        for tname in self._list_gpkg_tables():
            src = f"{self.gpkg_path}|layername={tname}"
            if src in existing_sources:
                continue
            vl = self._load_layer(tname)
            if not vl.isValid():
                continue
            if tname == model_table:
                self._apply_model_symbology(vl)
            QgsProject.instance().addMapLayer(vl, False)
            self.group.addLayer(vl)

    def _refresh_status(self, extra=""):
        text = f"Model: {self.model_name}\nGeopackage: {self.gpkg_path}"
        if extra:
            text += f"\n{extra}"
        self.status_label.setText(text)

    # ------------------------------------------------------------------
    # Nyt
    # ------------------------------------------------------------------
    def new_model(self):
        name, ok = QInputDialog.getText(self, "Nyt TimFlow-modelnavn", "Modelnavn:")
        if not ok or not name.strip():
            return
        model_name = name.strip()

        path, _ = QFileDialog.getSaveFileName(
            self, "Vælg placering for geopackage", model_name + ".gpkg", "GeoPackage (*.gpkg)"
        )
        if not path:
            return
        if not path.lower().endswith(".gpkg"):
            path += ".gpkg"

        crs = self.get_project_crs()

        # --- Polygonlag med modelnavnet (Polygon) ---
        if model_name == "Reference":
            QMessageBox.critical(self, "Fejl", "Modelnavnet kan ikke være 'Reference', da det er navnet på et andet lag.")
            return

        fields_model = QgsFields()
        fields_model.append(QgsField("grid_x", felttype("Int")))
        fields_model.append(QgsField("grid_y", felttype("Int")))
        fields_model.append(QgsField("jordtype", felttype("Double")))

        opt_model = QgsVectorFileWriter.SaveVectorOptions()
        opt_model.driverName = "GPKG"
        opt_model.fileEncoding = "UTF-8"
        opt_model.layerName = model_name
        opt_model.actionOnExistingFile = VFW_OVERWRITE_FILE

        writer = QgsVectorFileWriter.create(
            path, fields_model, WKB_POLYGON, crs, QgsProject.instance().transformContext(), opt_model
        )
        if writer is None or writer.hasError() != VFW_NO_ERROR:
            QMessageBox.critical(self, "Fejl", "Kunne ikke oprette polygonlaget '{model_name}' i geopackage.")
            return
        del writer

        # --- Lag: Reference (Point) - tilføjes til den netop oprettede gpkg ---
        fields_ref = QgsFields()
        fields_ref.append(QgsField("refkote", felttype("Double")))

        opt_ref = QgsVectorFileWriter.SaveVectorOptions()
        opt_ref.driverName = "GPKG"
        opt_ref.fileEncoding = "UTF-8"
        opt_ref.layerName = "Reference"
        opt_ref.actionOnExistingFile = VFW_OVERWRITE_LAYER

        writer2 = QgsVectorFileWriter.create(
            path, fields_ref, WKB_POINT, crs, QgsProject.instance().transformContext(), opt_ref
        )
        if writer2 is None or writer2.hasError() != VFW_NO_ERROR:
            QMessageBox.critical(self, "Fejl", "Kunne ikke oprette laget 'Reference' i geopackage.")
            return
        del writer2

        self.gpkg_path = path
        self.model_name = model_name
        self._get_or_create_group()

        model_layer = self._load_layer(model_name)
        self._apply_model_symbology(model_layer)
        QgsProject.instance().addMapLayer(model_layer, False)
        self.group.addLayer(model_layer)

        ref_layer = self._load_layer("Reference")
        QgsProject.instance().addMapLayer(ref_layer, False)
        self.group.addLayer(ref_layer)

        self._refresh_status()

    # ------------------------------------------------------------------
    # Rediger
    # ------------------------------------------------------------------
    def edit_model(self):
        selected = self.iface.layerTreeView().selectedLayers()
        if not selected:
            QMessageBox.critical(
                self, "Fejl", "Vælg et lag fra en TimFlow-geopackage i lagpanelet (lagvælgeren), og klik 'Rediger' igen."
            )
            return

        layer = selected[0]
        source = layer.source()
        path = source.split("|")[0]
        if not path.lower().endswith(".gpkg"):
            QMessageBox.critical(self, "Fejl", "Det valgte lag stammer ikke fra en geopackage.")
            return

        self.gpkg_path = path

        # Find gruppenavn ud fra lagets placering i laghierarkiet, ellers filnavnet
        node = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
        group_name = None
        if node is not None:
            parent = node.parent()
            root = QgsProject.instance().layerTreeRoot()
            if isinstance(parent, QgsLayerTreeGroup) and parent is not root:
                group_name = parent.name()
        if not group_name:
            group_name = os.path.splitext(os.path.basename(path))[0]

        self.model_name = group_name
        self._get_or_create_group()
        self._load_existing_layers_to_group()

        self._refresh_status("(Kobling til eksisterende geopackage - ingen nye lag oprettet)")

    # ------------------------------------------------------------------
    # Tilføj vandstande
    # ------------------------------------------------------------------
    def add_vandstand(self):
        if not self.gpkg_path:
            QMessageBox.critical(self, "Fejl", "Der er ingen geopackage valgt. Opret en ny model, eller rediger en eksisterende, først.")
            return

        name, ok = QInputDialog.getText(self, "Nyt vandstandslag", "Navn på laget:")
        if not ok or not name.strip():
            return
        layer_name = name.strip()

        if layer_name in self._list_gpkg_tables():
            QMessageBox.critical(self, "Fejl", f"Der findes allerede et lag med navnet '{layer_name}' i geopackagen.")
            return

        fields = QgsFields()
        fields.append(QgsField("kote", felttype("Double")))

        crs = self.get_project_crs()
        opt = QgsVectorFileWriter.SaveVectorOptions()
        opt.driverName = "GPKG"
        opt.fileEncoding = "UTF-8"
        opt.layerName = layer_name
        opt.actionOnExistingFile = VFW_OVERWRITE_LAYER

        writer = QgsVectorFileWriter.create(
            self.gpkg_path, fields, WKB_POINT, crs, QgsProject.instance().transformContext(), opt
        )
        if writer is None or writer.hasError() != VFW_NO_ERROR:
            QMessageBox.critical(self, "Fejl", f"Kunne ikke oprette laget '{layer_name}'.")
            return
        del writer

        if self.group is None:
            self._get_or_create_group()

        vl = self._load_layer(layer_name)
        QgsProject.instance().addMapLayer(vl, False)
        self.group.addLayer(vl)

        self._refresh_status(f"Vandstandslag '{layer_name}' tilføjet.")

    # ------------------------------------------------------------------
    # Slet vandstand
    # ------------------------------------------------------------------
    def delete_vandstand(self):
        selected = self.iface.layerTreeView().selectedLayers()
        if not selected:
            QMessageBox.critical(
                self, "Fejl", "Vælg det lag i lagpanelet, der skal slettes, og klik 'Slet vandstand' igen."
            )
            return

        layer = selected[0]
        source = layer.source()
        path = source.split("|")[0]
        if not path.lower().endswith(".gpkg"):
            QMessageBox.critical(self, "Fejl", "Det valgte lag stammer ikke fra en geopackage.")
            return

        match = re.search(r"layername=([^|]+)", source)
        table_name = match.group(1) if match else layer.name()

        if table_name == "Reference" or table_name == self._get_model_table():
            QMessageBox.critical(
                self, "Fejl", f"Laget '{table_name}' er et af TimFlow's grundlag og kan ikke slettes med denne knap."
            )
            return

        answer = QMessageBox.question(
            self,
            "Slet vandstand",
            f"Skal laget '{table_name}' slettes?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        # Fjern laget fra projektet først, så der ikke ligger en fil-lås på det
        QgsProject.instance().removeMapLayer(layer.id())

        try:
            conn = QgsProviderRegistry.instance().providerMetadata("ogr").createConnection(path, {})
            conn.dropVectorTable("", table_name)
        except Exception as e:
            QMessageBox.critical(self, "Fejl", f"Kunne ikke slette laget '{table_name}' fra geopackagen: {e}")
            return

        self._refresh_status(f"Laget '{table_name}' er slettet.")

    # ------------------------------------------------------------------
    # Reference punkt
    # ------------------------------------------------------------------
    def reference_punkt(self):
        if not self.gpkg_path:
            QMessageBox.critical(self, "Fejl", "Der er ingen geopackage valgt.")
            return

        model = self._get_model_feature()
        if model is None:
            return
        feat, model_table = model
        geom = feat.geometry()

        bbox = geom.boundingBox()
        x_new = (bbox.xMaximum() - bbox.xMinimum()) * 6 + (bbox.xMinimum() + bbox.xMaximum()) / 2.0
        y_new = (bbox.yMinimum() + bbox.yMaximum()) / 2.0

        # Gennemsnit af "kote" for alle punktlag i geopackagen, undtagen "Reference"
        kote_values = []
        for tname in self._list_gpkg_tables():
            if tname == "Reference":
                continue
            vl = self._load_layer(tname)
            if not vl.isValid():
                continue
            if vl.fields().indexFromName("kote") < 0:
                continue
            for f in vl.getFeatures():
                v = f["kote"]
                if v is not None:
                    try:
                        kote_values.append(float(v))
                    except (TypeError, ValueError):
                        pass

        refkote = (sum(kote_values) / len(kote_values)) if kote_values else 0.0

        ref_layer = self._load_layer("Reference")
        if not ref_layer.isValid():
            QMessageBox.critical(self, "Fejl", "Kunne ikke åbne laget 'Reference'.")
            return

        ref_layer.startEditing()
        existing_ids = [f.id() for f in ref_layer.getFeatures()]
        if existing_ids:
            ref_layer.deleteFeatures(existing_ids)

        new_feat = QgsFeature(ref_layer.fields())
        new_feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x_new, y_new)))
        new_feat.setAttribute("refkote", refkote)
        ref_layer.addFeature(new_feat)
        ref_layer.commitChanges()

        for lyr in QgsProject.instance().mapLayersByName("Reference"):
            lyr.reload()
            lyr.triggerRepaint()
        self.iface.mapCanvas().refresh()

        QMessageBox.information(self, "TimFlow", "Referencepunktet er oprettet/opdateret.")

    # ------------------------------------------------------------------
    # Beregn
    # ------------------------------------------------------------------
    def beregn(self):
        if not self.gpkg_path:
            QMessageBox.critical(self, "Fejl", "Der er ingen geopackage valgt.")
            return

        model = self._get_model_feature()
        if model is None:
            return
        feat, model_table = model
        geom = feat.geometry()

        bbox = geom.boundingBox()
        x_nv = bbox.xMinimum()
        y_nv = bbox.yMinimum()
        x_oh = bbox.xMaximum()
        y_oh = bbox.yMaximum()
        grid_x = feat["grid_x"]
        grid_y = feat["grid_y"]
        jordtype = feat["jordtype"]

        ref_layer = self._load_layer("Reference")
        if not ref_layer.isValid() or ref_layer.featureCount() == 0:
            QMessageBox.critical(self, "Fejl", "Der er ikke oprettet et referencepunkt. Brug knappen 'Reference punkt' først.")
            return
        reffeat = next(ref_layer.getFeatures())
        ref_geom = reffeat.geometry()
        if ref_geom is None or ref_geom.isEmpty():
            QMessageBox.critical(
                self, "Fejl", "Laget 'Reference' indeholder et punkt uden geometri. Brug knappen 'Reference punkt' igen."
            )
            return
        refpt = ref_geom.asPoint()
        xref = refpt.x()
        yref = refpt.y()
        vstref = reffeat["refkote"]

        # Model elementer - et RiverString pr. punktlag (undtagen Reference),
        # men kun for de punktlag der har fluebenet markeret i lagpanelet.
        checked_layers = self._get_checked_layer_names()
        element_blocks = []
        for tname in self._list_gpkg_tables():
            if tname == "Reference" or tname == model_table:
                continue
            if tname not in checked_layers:
                continue
            vl = self._load_layer(tname)
            if not vl.isValid():
                continue
            if vl.geometryType() != GEOM_POINT:
                continue
            if vl.fields().indexFromName("kote") < 0:
                continue

            if vl.featureCount() <= 1:
                QMessageBox.critical(
                    self,
                    "Fejl",
                    f"Laget '{tname}' skal indeholde mere end et punkt. Ved søer skal du angive udbredelsen af søen.",
                )
                return

            coords = []
            kotes = []

            def _sort_key(feat):
                # Først kote i faldende orden, dernæst fid i stigende orden.
                # Punkter uden kote-værdi placeres sidst.
                try:
                    k = float(feat["kote"])
                except (TypeError, ValueError):
                    return (1, 0.0, feat.id())
                return (0, -k, feat.id())

            for f in sorted(vl.getFeatures(), key=_sort_key):
                geom = f.geometry()
                if geom is None or geom.isEmpty():
                    QMessageBox.critical(
                        self,
                        "Fejl",
                        f"Laget '{tname}' indeholder et punkt uden geometri (objekt-id {f.id()}). "
                        f"Ret eller slet punktet, og prøv igen.",
                    )
                    return
                pt = geom.asPoint()
                coords.append((pt.x(), pt.y()))
                kotes.append(f["kote"])

            if not coords:
                continue

            varname = self._sanitize_varname(tname)
            coord_lines = "\n".join([f"    ({x}, {y})," for x, y in coords])
            kote_list = ", ".join([str(k) for k in kotes])
            block = (
                f'{varname} = [\n{coord_lines}\n]\n'
                f'{varname}_kote = [{kote_list}]\n'
                f'{varname}_ml = tfs.RiverString(ml, xy={varname}, hls={varname}_kote, layers=0, name="{tname}")\n'
            )
            element_blocks.append(block)

        elements_code = textwrap.indent("\n".join(element_blocks), "    ")

        script = TEMPLATE.format(
            x_nv=x_nv,
            y_nv=y_nv,
            x_oh=x_oh,
            y_oh=y_oh,
            grid_x=grid_x,
            grid_y=grid_y,
            jordtype=jordtype,
            modelnavn=self.model_name,
            elements=elements_code,
            xref=xref,
            yref=yref,
            vstref=vstref,
        )

        path, _ = QFileDialog.getSaveFileName(
            self, "Gem pythonscript", (self.model_name or "model") + ".py", "Python (*.py)"
        )
        if not path:
            return
        if not path.lower().endswith(".py"):
            path += ".py"

        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(script)
        except OSError as e:
            QMessageBox.critical(self, "Fejl", f"Kunne ikke gemme scriptet: {e}")
            return

        answer = QMessageBox.question(
            self,
            "TimFlow",
            "Skal modellen beregnes?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            try:
                # Åbn en ny CMD-prompt og kør scriptet i den, med det miljø
                # (bl.a. PATH) man ville få ved selv at åbne en CMD-prompt -
                # så "python" er den version, der bruges når man kalder den
                # fra CMD, og ikke QGIS' egen indlejrede/indskrænkede miljø.
                env = self._get_clean_windows_env()
                cmd = f'start "TimFlow" cmd /k python -u "{path}"'
                subprocess.Popen(cmd, shell=True, env=env)
            except Exception as e:
                QMessageBox.critical(self, "Fejl", f"Kunne ikke starte beregningen: {e}")
