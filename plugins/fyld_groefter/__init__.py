# -*- coding: utf-8 -*-


def classFactory(iface):
    from .fyld_groefter import FyldGroefter
    return FyldGroefter(iface)
