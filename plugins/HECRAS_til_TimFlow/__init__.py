# -*- coding: utf-8 -*-
"""
HECRAS til TimFlow
-------------------
QGIS-plugin der opretter punktlag med koter (sample fra en HEC-RAS
resultatfil) langs linjerne i et HEC-RAS vandlobslag, og gemmer dem
i en TimFlow-geopackage.
"""


def classFactory(iface):
    from .hecras_til_timflow import HecrasTilTimFlow
    return HecrasTilTimFlow(iface)
