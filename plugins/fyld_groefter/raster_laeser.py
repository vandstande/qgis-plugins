# -*- coding: utf-8 -*-
"""Hurtig bilineær aflæsning af terrænmodellen via raster-blokke og numpy."""

import math
import numpy as np

from qgis.core import Qgis, QgsRectangle

_DTYPE_NAVNE = {
    "Byte": np.uint8, "Int8": np.int8, "UInt16": np.uint16, "Int16": np.int16,
    "UInt32": np.uint32, "Int32": np.int32, "Float32": np.float32, "Float64": np.float64,
}


def _dtype_for(qgis_type):
    for navn, dt in _DTYPE_NAVNE.items():
        for kilde in (getattr(Qgis, "DataType", None), Qgis):
            if kilde is None:
                continue
            v = getattr(kilde, navn, None)
            if v is not None and v == qgis_type:
                return dt
    return None


class RasterLaeser(object):
    def __init__(self, raster_lag, band=1):
        self.lag = raster_lag
        self.prov = raster_lag.dataProvider()
        self.band = band
        self.px = raster_lag.rasterUnitsPerPixelX()
        self.py = raster_lag.rasterUnitsPerPixelY()
        self.ext = self.prov.extent()
        self.nodata = None
        if self.prov.sourceHasNoDataValue(band):
            self.nodata = self.prov.sourceNoDataValue(band)

    def _blok(self, xmin, ymin, xmax, ymax):
        """Læser blok, der dækker området, justeret til rastergitteret."""
        e = self.ext
        x0 = e.xMinimum() + math.floor((xmin - e.xMinimum()) / self.px - 1) * self.px
        x1 = e.xMinimum() + math.ceil((xmax - e.xMinimum()) / self.px + 1) * self.px
        y1 = e.yMaximum() - math.floor((e.yMaximum() - ymax) / self.py - 1) * self.py
        y0 = e.yMaximum() - math.ceil((e.yMaximum() - ymin) / self.py + 1) * self.py
        x0 = max(x0, e.xMinimum()); x1 = min(x1, e.xMaximum())
        y0 = max(y0, e.yMinimum()); y1 = min(y1, e.yMaximum())
        if x1 <= x0 or y1 <= y0:
            return None
        b = int(round((x1 - x0) / self.px))
        h = int(round((y1 - y0) / self.py))
        if b < 1 or h < 1:
            return None
        blok = self.prov.block(self.band, QgsRectangle(x0, y0, x1, y1), b, h)
        arr = None
        if hasattr(blok, "as_numpy"):
            try:
                arr = np.array(blok.as_numpy(use_masking=False), dtype=float)
            except Exception:
                arr = None
        if arr is None:
            dt = _dtype_for(blok.dataType())
            if dt is not None:
                arr = np.frombuffer(bytes(blok.data()), dtype=dt).astype(float)
                arr = arr.reshape(h, b)
            else:  # langsom reserve
                arr = np.array([[blok.value(r, c) for c in range(b)] for r in range(h)],
                               dtype=float)
        arr = arr.reshape(h, b)
        if blok.hasNoDataValue():
            arr[arr == blok.noDataValue()] = np.nan
        if self.nodata is not None:
            arr[arr == self.nodata] = np.nan
        return arr, x0, y1

    def aflaes(self, x, y):
        """Bilineær interpolation i punkterne (x, y) (numpy-arrays i rasterets CRS)."""
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        ud = np.full(x.shape, np.nan)
        if x.size == 0:
            return ud
        r = self._blok(np.nanmin(x), np.nanmin(y), np.nanmax(x), np.nanmax(y))
        if r is None:
            return ud
        arr, x0, ytop = r
        h, b = arr.shape
        c = (x - x0) / self.px - 0.5
        rr = (ytop - y) / self.py - 0.5
        c0 = np.floor(c).astype(int)
        r0 = np.floor(rr).astype(int)
        fc = c - c0
        fr = rr - r0
        c0 = np.clip(c0, 0, b - 1); c1 = np.clip(c0 + 1, 0, b - 1)
        r0 = np.clip(r0, 0, h - 1); r1 = np.clip(r0 + 1, 0, h - 1)
        v = (arr[r0, c0] * (1 - fc) * (1 - fr) + arr[r0, c1] * fc * (1 - fr) +
             arr[r1, c0] * (1 - fc) * fr + arr[r1, c1] * fc * fr)
        # Hvis et af nabopixlerne mangler: brug nærmeste pixel
        mangler = ~np.isfinite(v)
        if mangler.any():
            cn = np.clip(np.round(c).astype(int), 0, b - 1)
            rn = np.clip(np.round(rr).astype(int), 0, h - 1)
            v[mangler] = arr[rn[mangler], cn[mangler]]
        inde = ((x >= self.ext.xMinimum()) & (x <= self.ext.xMaximum()) &
                (y >= self.ext.yMinimum()) & (y <= self.ext.yMaximum()))
        v[~inde] = np.nan
        return v
