# -*- coding: utf-8 -*-
def classFactory(iface):
    from .opmaaling_hecras import OpmaalingHecras
    return OpmaalingHecras(iface)
