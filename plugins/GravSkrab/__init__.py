# -*- coding: utf-8 -*-


def classFactory(iface):
    from .grav_skrab import GravSkrab
    return GravSkrab(iface)
