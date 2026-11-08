# -*- coding: utf-8 -*-
"""
TimFlow - QGIS plugin
"""


def classFactory(iface):
    from .timflow import TimFlowPlugin
    return TimFlowPlugin(iface)
