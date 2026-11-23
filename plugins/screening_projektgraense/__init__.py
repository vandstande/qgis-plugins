# -*- coding: utf-8 -*-


def classFactory(iface):
    from .plugin import ProjektgraensePlugin
    return ProjektgraensePlugin(iface)
