# -*- coding: utf-8 -*-
"""GDAL crop + mask GeoJSON for AI Obstacle Removal (nano banana / Gemini edit)."""

from __future__ import annotations

import json
import math
import os
import tempfile
import uuid

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsDistanceArea,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
)

MAX_UPLOAD_MB = 200
LOG_CHANNEL = "FieldWatch"


def _layer_source_path(layer: QgsRasterLayer) -> str:
    src = (layer.source() or "").split("|")[0].strip()
    return src


def pad_rectangle(
    rect: QgsRectangle,
    padding_m: float,
    crs: QgsCoordinateReferenceSystem,
) -> QgsRectangle:
    """Expand a map rectangle by padding_m (approximate for geographic CRS)."""
    if padding_m <= 0:
        return QgsRectangle(rect)

    if crs.isValid() and not crs.isGeographic():
        return QgsRectangle(
            rect.xMinimum() - padding_m,
            rect.yMinimum() - padding_m,
            rect.xMaximum() + padding_m,
            rect.yMaximum() + padding_m,
        )

    center = rect.center()
    lat = center.y()
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = max(m_per_deg_lat * math.cos(math.radians(lat)), 1e-6)
    d_lon = padding_m / m_per_deg_lon
    d_lat = padding_m / m_per_deg_lat
    return QgsRectangle(
        rect.xMinimum() - d_lon,
        rect.yMinimum() - d_lat,
        rect.xMaximum() + d_lon,
        rect.yMaximum() + d_lat,
    )


def snap_extent_to_pixels(
    extent: QgsRectangle, layer: QgsRasterLayer
) -> QgsRectangle:
    """Snap crop extent to source raster pixel grid."""
    provider = layer.dataProvider()
    if provider is None:
        return extent

    geo = provider.extent()
    cols = max(provider.xSize(), 1)
    rows = max(provider.ySize(), 1)
    pw = geo.width() / cols
    ph = geo.height() / rows
    if pw == 0 or ph == 0:
        return extent

    origin_x = geo.xMinimum()
    origin_y = geo.yMaximum()

    col0 = int(math.floor((extent.xMinimum() - origin_x) / pw))
    col1 = int(math.ceil((extent.xMaximum() - origin_x) / pw))
    row0 = int(math.floor((origin_y - extent.yMaximum()) / abs(ph)))
    row1 = int(math.ceil((origin_y - extent.yMinimum()) / abs(ph)))

    col0 = max(0, col0)
    row0 = max(0, row0)
    col1 = min(cols, col1)
    row1 = min(rows, row1)
    if col1 <= col0 or row1 <= row0:
        return extent

    xmin = origin_x + col0 * pw
    xmax = origin_x + col1 * pw
    ymax = origin_y - row0 * abs(ph)
    ymin = origin_y - row1 * abs(ph)
    return QgsRectangle(xmin, ymin, xmax, ymax)


def rectangle_size_meters(
    rect: QgsRectangle, crs: QgsCoordinateReferenceSystem
) -> tuple[float, float]:
    """Approximate width × height in meters for UI display."""
    if not crs.isValid():
        return abs(rect.width()), abs(rect.height())
    da = QgsDistanceArea()
    da.setSourceCrs(crs, QgsProject.instance().transformContext())
    da.setEllipsoid(QgsProject.instance().ellipsoid())
    center = rect.center()
    height_m = da.measureLine(
        QgsPointXY(center.x(), rect.yMinimum()),
        QgsPointXY(center.x(), rect.yMaximum()),
    )
    width_m = da.measureLine(
        QgsPointXY(rect.xMinimum(), center.y()),
        QgsPointXY(rect.xMaximum(), center.y()),
    )
    return abs(width_m), abs(height_m)


def inner_box_to_mask_geojson(inner: QgsRectangle) -> dict:
    """GeoJSON geometry object for the inner user box (crop CRS)."""
    xmin, ymin = inner.xMinimum(), inner.yMinimum()
    xmax, ymax = inner.xMaximum(), inner.yMaximum()
    return _rect_coords_to_polygon_geojson(xmin, ymin, xmax, ymax)


def _rect_coords_to_polygon_geojson(
    xmin: float, ymin: float, xmax: float, ymax: float
) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [xmin, ymin],
                [xmax, ymin],
                [xmax, ymax],
                [xmin, ymax],
                [xmin, ymin],
            ]
        ],
    }


def rect_tuple_to_mask_geojson(rect: tuple[float, float, float, float]) -> dict:
    xmin, ymin, xmax, ymax = rect
    if xmin > xmax:
        xmin, xmax = xmax, xmin
    if ymin > ymax:
        ymin, ymax = ymax, ymin
    return _rect_coords_to_polygon_geojson(xmin, ymin, xmax, ymax)


def geometry_to_mask_geojson(geometry: QgsGeometry) -> dict | None:
    """GeoJSON geometry object (Polygon/MultiPolygon) for mask field."""
    if not geometry or geometry.isEmpty():
        return None
    try:
        payload = json.loads(geometry.asJson())
    except (TypeError, ValueError):
        return None
    if isinstance(payload, dict) and payload.get("type") in (
        "Polygon",
        "MultiPolygon",
    ):
        return payload
    return None


def rect_tuple_from_qgs_rect(rect: QgsRectangle) -> tuple[float, float, float, float]:
    r = QgsRectangle(rect)
    r.normalize()
    return (
        float(r.xMinimum()),
        float(r.yMinimum()),
        float(r.xMaximum()),
        float(r.yMaximum()),
    )


def estimate_file_mb(path: str) -> float:
    try:
        return os.path.getsize(path) / (1024 * 1024)
    except OSError:
        return 0.0


def crop_to_geotiff(
    layer: QgsRasterLayer,
    inner_rect: QgsRectangle | tuple[float, float, float, float],
    *,
    padding_m: float,
    output_path: str | None = None,
    mask_geometry: dict | None = None,
) -> tuple[str | None, dict | None, str]:
    """
    Crop padded extent from layer; return path, crop metadata, error.

    Mask GeoJSON should use inner_rect (not padded), or pass mask_geometry for polygons.
    """
    if not layer or not layer.isValid():
        return None, None, "Invalid raster layer."
    crs = layer.crs()
    if not crs.isValid():
        return None, None, "Layer must be georeferenced."

    src_path = _layer_source_path(layer)
    if not src_path or not os.path.isfile(src_path):
        return None, None, "Could not read raster source path."

    if isinstance(inner_rect, tuple):
        inner = QgsRectangle(inner_rect[0], inner_rect[1], inner_rect[2], inner_rect[3])
    else:
        inner = QgsRectangle(inner_rect)
    inner.normalize()
    if inner.isEmpty() or inner.width() <= 0 or inner.height() <= 0:
        return None, None, "Draw a valid removal box."

    padded = pad_rectangle(inner, padding_m, crs)
    layer_extent = layer.extent()
    padded = padded.intersect(layer_extent)
    if padded.isEmpty():
        return None, None, "Removal box is outside the raster extent."

    padded = snap_extent_to_pixels(padded, layer)
    if padded.isEmpty():
        return None, None, "Could not align crop to raster pixels."

    if output_path is None:
        output_path = os.path.join(
            tempfile.gettempdir(),
            f"fieldwatch_edit_{uuid.uuid4().hex}_crop.tif",
        )

    try:
        from osgeo import gdal
    except ImportError:
        return None, None, "GDAL is not available."

    src_ds = gdal.Open(src_path, gdal.GA_ReadOnly)
    if src_ds is None:
        return None, None, "Could not open source raster with GDAL."

    ulx = padded.xMinimum()
    lry = padded.yMinimum()
    lrx = padded.xMaximum()
    uly = padded.yMaximum()

    try:
        out_ds = gdal.Translate(
            output_path,
            src_ds,
            projWin=[ulx, uly, lrx, lry],
            format="GTiff",
            creationOptions=["COMPRESS=LZW"],
        )
    except Exception as exc:
        return None, None, str(exc)
    finally:
        src_ds = None

    if out_ds is None:
        return None, None, "GDAL crop failed."
    width = out_ds.RasterXSize
    height = out_ds.RasterYSize
    gt = out_ds.GetGeoTransform()
    out_ds = None

    size_mb = estimate_file_mb(output_path)
    if size_mb > MAX_UPLOAD_MB:
        try:
            os.remove(output_path)
        except OSError:
            pass
        return (
            None,
            None,
            f"Crop too large ({size_mb:.0f} MB). Draw a smaller box or reduce padding.",
        )

    meta = {
        "path": output_path,
        "width": width,
        "height": height,
        "xmin": gt[0],
        "ymax": gt[3],
        "pixel_width": gt[1],
        "pixel_height": gt[5],
        "crs": crs,
        "inner_rect": inner,
        "mask_geojson": mask_geometry or inner_box_to_mask_geojson(inner),
        "size_mb": size_mb,
    }
    return output_path, meta, ""


def mask_geojson_string(inner_rect) -> str:
    if isinstance(inner_rect, tuple):
        return json.dumps(rect_tuple_to_mask_geojson(inner_rect))
    return json.dumps(inner_box_to_mask_geojson(inner_rect))
