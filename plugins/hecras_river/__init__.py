def classFactory(iface):
    from .hecras_river import HecrasRiverPlugin
    return HecrasRiverPlugin(iface)
