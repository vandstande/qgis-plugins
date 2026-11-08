# -*- coding: utf-8 -*-
"""
Indlæs modelgrid
----------------
QGIS-plugin til indlæsning af modelgrid-filer og beregning af
terræn minus grid, tematiseret med en gemt symbologifil.
"""


def classFactory(iface):
    from .indlaes_modelgrid import IndlaesModelgridPlugin
    return IndlaesModelgridPlugin(iface)
