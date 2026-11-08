# -*- coding: utf-8 -*-
def classFactory(iface):  # noqa: N802
    from .grav_groefter import GravGroefter
    return GravGroefter(iface)
