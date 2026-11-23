# -*- coding: utf-8 -*-
"""CO2-beregning – QGIS plugin."""


def classFactory(iface):  # noqa: N802 (QGIS API name)
    from .co2_plugin import CO2BeregningPlugin
    return CO2BeregningPlugin(iface)
