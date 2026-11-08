# -*- coding: utf-8 -*-
"""Kompatibilitet mellem QGIS 3 (Qt5) og QGIS 4 (Qt6).

Her slås de nye, scopede navne op først, og de gamle navne bruges kun som
reserve. Så virker pluginet både i QGIS 3 og QGIS 4.
"""

from qgis.core import Qgis, QgsVectorDataProvider

# QAction ligger i QtGui i Qt6 og i QtWidgets i Qt5
try:
    from qgis.PyQt.QtGui import QAction  # noqa: F401
except ImportError:
    from qgis.PyQt.QtWidgets import QAction  # noqa: F401


def _scoped(cls, scope, navn):
    s = getattr(cls, scope, None)
    if s is not None and hasattr(s, navn):
        return getattr(s, navn)
    return getattr(cls, navn)


# Beskedniveauer: Qgis.MessageLevel.Warning (nyt) / Qgis.Warning (gammelt)
NIVEAU_INFO = _scoped(Qgis, "MessageLevel", "Info")
NIVEAU_ADVARSEL = _scoped(Qgis, "MessageLevel", "Warning")
NIVEAU_SUCCES = _scoped(Qgis, "MessageLevel", "Success")
NIVEAU_FEJL = _scoped(Qgis, "MessageLevel", "Critical")


def _kapabilitet(navn):
    # Nyeste QGIS: Qgis.VectorProviderCapability.AddAttributes
    vpc = getattr(Qgis, "VectorProviderCapability", None)
    if vpc is not None and hasattr(vpc, navn):
        return getattr(vpc, navn)
    # Mellemversioner: QgsVectorDataProvider.Capability.AddAttributes
    return _scoped(QgsVectorDataProvider, "Capability", navn)


KAN_TILFOEJE_FELTER = _kapabilitet("AddAttributes")
KAN_AENDRE_VAERDIER = _kapabilitet("ChangeAttributeValues")


def har_kapabilitet(provider, kap):
    """True hvis dataprovideren har den givne kapabilitet."""
    try:
        return bool(provider.capabilities() & kap)
    except TypeError:
        # Nogle enum/flag-kombinationer kan ikke bruge & direkte
        try:
            return bool(int(provider.capabilities()) & int(kap))
        except Exception:  # noqa: BLE001
            return True  # lad selve skrivningen afgøre det
