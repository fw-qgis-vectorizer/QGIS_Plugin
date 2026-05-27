# -*- coding: utf-8 -*-
"""Project raster layer discovery (QGIS 3 / 4)."""

from __future__ import annotations

from qgis.core import QgsMapLayer, QgsProject, QgsRasterLayer


def _is_raster_layer(layer) -> bool:
    if isinstance(layer, QgsRasterLayer):
        return True
    try:
        layer_type = layer.type()
    except Exception:
        return False
    try:
        if layer_type == QgsMapLayer.LayerType.RasterLayer:
            return True
    except AttributeError:
        pass
    return layer_type == 1


def layer_is_usable(layer) -> bool:
    """False if the C++ layer object was deleted (e.g. during project/plugin unload)."""
    if layer is None:
        return False
    try:
        return bool(layer.isValid())
    except RuntimeError:
        return False


def list_project_raster_layers(*, valid_only: bool = True) -> list[QgsRasterLayer]:
    """Return raster layers in the current project, sorted by name."""
    found: list[QgsRasterLayer] = []
    for layer in QgsProject.instance().mapLayers().values():
        if not _is_raster_layer(layer):
            continue
        if valid_only and not layer_is_usable(layer):
            continue
        found.append(layer)
    found.sort(key=lambda lyr: lyr.name().lower())
    return found
