def classFactory(iface):
    from .fosfor_beregning import FosforBeregningPlugin
    return FosforBeregningPlugin(iface)
