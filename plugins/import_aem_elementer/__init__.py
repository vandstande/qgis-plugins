# -*- coding: utf-8 -*-
"""
Import AEM elementer
---------------------
QGIS plugin til at importere AEM elementer fra en .bbl fil til punktlag
i en valgt geopackage.
"""


def classFactory(iface):
    from .import_aem_elementer import ImportAemElementer
    return ImportAemElementer(iface)
