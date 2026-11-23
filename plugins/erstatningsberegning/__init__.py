# -*- coding: utf-8 -*-
"""Erstatningsberegning – QGIS plugin."""


def classFactory(iface):  # noqa: N802 (QGIS API name)
    from .erstatning_plugin import ErstatningsberegningPlugin
    return ErstatningsberegningPlugin(iface)
