# -*- coding: utf-8 -*-
"""Extract raster crops for model encoding."""

from __future__ import annotations

import math
import numpy as np
from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
)


def _block_to_band_array(block, h: int, w: int) -> np.ndarray | None:
    """Fast path: QgsRasterBlock -> 2D float array."""
    if block is None or not block.isValid() or block.width() == 0:
        return None
    raw = block.data()
    if raw is None:
        return None
    data = bytes(raw)
    if not data:
        return None

    dt = block.dataType()
    if dt in (Qgis.DataType.ARGB32, Qgis.DataType.ARGB32_Premultiplied):
        arr = np.frombuffer(data, dtype=np.uint8).reshape(h, w, 4)
        return np.stack([arr[:, :, 2], arr[:, :, 1], arr[:, :, 0]], axis=-1).astype(
            np.float32
        )

    dtype_map = {
        Qgis.DataType.Byte: np.uint8,
        Qgis.DataType.UInt16: np.uint16,
        Qgis.DataType.Int16: np.int16,
        Qgis.DataType.UInt32: np.uint32,
        Qgis.DataType.Int32: np.int32,
        Qgis.DataType.Float32: np.float32,
        Qgis.DataType.Float64: np.float64,
    }
    np_dtype = dtype_map.get(dt)
    if np_dtype is None:
        return None
    return np.frombuffer(data, dtype=np_dtype).reshape(h, w).astype(np.float32)


def _to_uint8_rgb(channels: list[np.ndarray]) -> np.ndarray:
    while len(channels) < 3:
        channels.append(channels[-1])
    stacked = np.stack(channels[:3], axis=-1)
    if stacked.dtype != np.float32:
        stacked = stacked.astype(np.float32)
    if stacked.max() <= 1.0:
        stacked = stacked * 255.0
    return np.clip(stacked, 0, 255).astype(np.uint8)


def resize_for_encode(image: np.ndarray, max_side: int) -> tuple[np.ndarray, float]:
    """Downscale crop before IPC/encode; returns (image, scale factor to map coords)."""
    h, w = image.shape[:2]
    if max(h, w) <= max_side:
        return np.ascontiguousarray(image), 1.0
    scale = max_side / float(max(h, w))
    new_h = max(1, int(round(h * scale)))
    new_w = max(1, int(round(w * scale)))
    ys = np.linspace(0, h - 1, new_h).astype(np.intp)
    xs = np.linspace(0, w - 1, new_w).astype(np.intp)
    small = image[np.ix_(ys, xs)]
    actual_scale = new_w / float(w)
    return np.ascontiguousarray(small), actual_scale


def _transform_context():
    return QgsProject.instance().transformContext()


def _crs_transform(source_crs, dest_crs) -> QgsCoordinateTransform:
    """Build QgsCoordinateTransform (works across QGIS 3.x API variants)."""
    try:
        return QgsCoordinateTransform(source_crs, dest_crs, _transform_context())
    except TypeError:
        return QgsCoordinateTransform(source_crs, dest_crs, QgsProject.instance())


def _geotransform_coeffs(provider, extent) -> tuple[float, float, float, float] | None:
    """Return origin_x, pixel_w, origin_y, pixel_h from provider geotransform or extent."""
    gt = None
    try:
        gt = provider.geoTransform()
    except Exception:
        gt = None

    coeffs = None
    if gt is not None:
        if hasattr(gt, "toGdal"):
            try:
                coeffs = tuple(gt.toGdal())
            except Exception:
                coeffs = None
        elif hasattr(gt, "__iter__") and not isinstance(gt, (str, bytes)):
            try:
                coeffs = tuple(gt)
            except TypeError:
                coeffs = None

    if coeffs and len(coeffs) >= 6:
        origin_x, pixel_w, _, origin_y, _, pixel_h = coeffs[:6]
        return float(origin_x), float(pixel_w), float(origin_y), float(pixel_h)

    cols = provider.xSize()
    rows = provider.ySize()
    if cols <= 0 or rows <= 0:
        return None
    pixel_w = extent.width() / cols
    pixel_h = -extent.height() / rows
    return float(extent.xMinimum()), float(pixel_w), float(extent.yMaximum()), float(pixel_h)


def upscale_mask_to_crop(mask: np.ndarray, crop_info: dict) -> np.ndarray:
    """Resize model-output mask back to full crop pixel grid for georeferencing."""
    target_h = int(crop_info.get("height", mask.shape[0]))
    target_w = int(crop_info.get("width", mask.shape[1]))
    h, w = mask.shape[:2]
    if h == target_h and w == target_w:
        return mask
    ys = np.linspace(0, h - 1, target_h).astype(np.intp)
    xs = np.linspace(0, w - 1, target_w).astype(np.intp)
    return mask[np.ix_(ys, xs)]


def extract_crop_at_point(
    layer: QgsRasterLayer,
    map_point: QgsPointXY,
    canvas_crs,
    crop_pixels: int = 1024,
) -> tuple[np.ndarray | None, dict | None, str]:
    """
    Read an RGB crop centered on map_point.

    Returns (image_hwc_uint8, crop_info, error_message).
    crop_info keys: width, height, xmin, ymax, xmax, ymin, pixel_width, pixel_height
    in layer CRS (y increases north for georeferenced rasters).
    """
    if not layer or not layer.isValid():
        return None, None, "Invalid raster layer."

    raster_crs = layer.crs()
    if not raster_crs.isValid():
        return None, None, "Raster has no valid CRS."

    if hasattr(map_point, "x") and hasattr(map_point, "y"):
        pt = QgsPointXY(map_point.x(), map_point.y())
    else:
        return None, None, "Invalid map point."

    if (
        canvas_crs
        and hasattr(canvas_crs, "isValid")
        and canvas_crs.isValid()
        and canvas_crs != raster_crs
    ):
        pt = _crs_transform(canvas_crs, raster_crs).transform(pt)

    extent = layer.extent()
    if not extent.contains(pt):
        return None, None, "Click is outside the raster extent."

    provider = layer.dataProvider()
    if provider is None:
        return None, None, "Raster provider unavailable."

    geo = _geotransform_coeffs(provider, extent)
    if geo is None:
        return None, None, "Raster has no pixels."
    origin_x, pixel_w, origin_y, pixel_h = geo
    if pixel_w == 0 or pixel_h == 0:
        return None, None, "Raster geotransform is invalid."

    col_f = (pt.x() - origin_x) / pixel_w
    row_f = (pt.y() - origin_y) / pixel_h
    half = crop_pixels // 2
    col0 = int(math.floor(col_f)) - half
    row0 = int(math.floor(row_f)) - half
    col1 = col0 + crop_pixels
    row1 = row0 + crop_pixels

    cols = provider.xSize()
    rows = provider.ySize()
    col0_clamp = max(0, col0)
    row0_clamp = max(0, row0)
    col1_clamp = min(cols, col1)
    row1_clamp = min(rows, row1)
    if col1_clamp <= col0_clamp or row1_clamp <= row0_clamp:
        return None, None, "Could not read raster crop at this location."

    xmin = origin_x + col0_clamp * pixel_w
    xmax = origin_x + col1_clamp * pixel_w
    ymax = origin_y + row0_clamp * pixel_h
    ymin = origin_y + row1_clamp * pixel_h
    rect = QgsRectangle(xmin, ymin, xmax, ymax)
    w = col1_clamp - col0_clamp
    h = row1_clamp - row0_clamp

    band_count = min(3, provider.bandCount())
    channels = []
    for b in range(1, band_count + 1):
        bblock = provider.block(b, rect, w, h)
        if bblock is None or not bblock.isValid():
            return None, None, f"Failed to read band {b}."
        arr = _block_to_band_array(bblock, bblock.height(), bblock.width())
        if arr is None:
            return None, None, f"Failed to read band {b}."
        if arr.ndim == 3:
            channels.extend([arr[:, :, i] for i in range(3)])
            break
        channels.append(arr)

    rgb = _to_uint8_rgb(channels)

    pad_left = col0_clamp - col0
    pad_top = row0_clamp - row0
    if pad_left or pad_top or w != crop_pixels or h != crop_pixels:
        canvas_arr = np.zeros((crop_pixels, crop_pixels, 3), dtype=np.uint8)
        y0 = pad_top
        x0 = pad_left
        canvas_arr[y0 : y0 + h, x0 : x0 + w] = rgb
        rgb = canvas_arr

    # Georef for the full crop window (1024×1024), not just the clamped read rect.
    crop_xmin = origin_x + col0 * pixel_w
    crop_xmax = origin_x + col1 * pixel_w
    crop_ymax = origin_y + row0 * pixel_h
    crop_ymin = origin_y + row1 * pixel_h

    crop_info = {
        "width": crop_pixels,
        "height": crop_pixels,
        "xmin": crop_xmin,
        "xmax": crop_xmax,
        "ymax": crop_ymax,
        "ymin": crop_ymin,
        "pixel_width": pixel_w,
        "pixel_height": pixel_h,
        "col0": col0,
        "row0": row0,
        "geo_origin_x": origin_x,
        "geo_origin_y": origin_y,
        "layer_crs": raster_crs,
        "encode_scale": 1.0,
    }
    return rgb, crop_info, ""


def map_point_to_crop_pixels(
    map_point: QgsPointXY,
    crop_info: dict,
    canvas_crs,
) -> tuple[float, float] | None:
    """Convert map coordinates to crop pixel (x, y) with origin top-left."""
    pt = QgsPointXY(map_point)
    layer_crs = crop_info.get("layer_crs")
    if layer_crs and canvas_crs and canvas_crs.isValid() and canvas_crs != layer_crs:
        pt = _crs_transform(canvas_crs, layer_crs).transform(pt)

    pw = crop_info["pixel_width"]
    ph = crop_info["pixel_height"]
    col0 = crop_info["col0"]
    row0 = crop_info["row0"]
    geo_x = crop_info.get("geo_origin_x", crop_info["xmin"])
    geo_y = crop_info.get("geo_origin_y", crop_info["ymax"])

    # Index in the fixed-size crop window (accounts for edge padding when col0/row0 < 0).
    col = (pt.x() - geo_x) / pw - col0
    row = (pt.y() - geo_y) / ph - row0
    w = crop_info["width"]
    h = crop_info["height"]
    if col < 0 or row < 0 or col >= w or row >= h:
        return None
    return float(col), float(row)


def model_pixels_from_crop(
    crop_pixels: tuple[float, float], crop_info: dict
) -> tuple[float, float]:
    """Map full-crop pixel coords to the encoded image size sent to the worker."""
    scale = float(crop_info.get("encode_scale", 1.0))
    return crop_pixels[0] * scale, crop_pixels[1] * scale


def _select_mask_geometries(geoms: list, keep_all: bool, pixel_width: float) -> list:
    """
    Component filtering on polygons instead of pixels.

    The previous pixel-by-pixel pure-Python labeling took minutes on the UI
    thread for large/noisy masks (QGIS "Not Responding"). Polygon areas give
    the same result at C speed.
    """
    if not geoms:
        return geoms
    if not keep_all:
        return [max(geoms, key=lambda g: g.area())]
    min_area = (pixel_width * pixel_width) * 24.0
    kept = [g for g in geoms if g.area() >= min_area]
    return kept or [max(geoms, key=lambda g: g.area())]


def click_needs_new_crop(
    point,
    crop_info: dict,
    positive_points: list[tuple[float, float]],
    canvas_crs,
    *,
    max_pixel_distance: float = 140.0,
) -> bool:
    """
    True when the click should start a fresh crop (e.g. another building far away).

    TerraLab re-encodes per area; we match that so two distant clicks are not one mask.
    """
    pixels = map_point_to_crop_pixels(point, crop_info, canvas_crs)
    if pixels is None:
        return True
    if not positive_points:
        return False
    px, py = pixels
    for ox, oy in positive_points:
        if math.hypot(px - ox, py - oy) <= max_pixel_distance:
            return False
    return True


def mask_to_map_geometries(
    mask: np.ndarray,
    crop_info: dict,
    simplify: float | None = None,
    *,
    keep_all: bool = False,
):
    """
    Convert binary mask to QgsGeometry list in layer CRS.

    keep_all=False keeps only the largest component (single-object masks);
    keep_all=True keeps every component above a small area threshold
    (obstacle overlays with one blob per click).
    """
    from qgis.core import QgsGeometry

    if mask is None or not np.any(mask):
        return []

    pw = abs(float(crop_info.get("pixel_width", 1.0)))
    if simplify is None:
        simplify = max(pw * 0.75, 0.01)

    geoms = _mask_polygon_geometries_gdal(mask, crop_info, simplify)
    if geoms:
        return _select_mask_geometries(geoms, keep_all, pw)

    try:
        from rasterio.features import shapes as get_shapes
        from rasterio.transform import from_bounds
    except ImportError:
        return _mask_bbox_geometries(mask, crop_info)

    xmin = crop_info["xmin"]
    xmax = crop_info["xmax"]
    ymin = crop_info["ymin"]
    ymax = crop_info["ymax"]
    h, w = mask.shape[:2]
    transform = from_bounds(xmin, ymin, xmax, ymax, w, h)

    geoms = []
    mask_u8 = (mask > 0).astype(np.uint8)
    for geom_dict, val in get_shapes(mask_u8, mask=mask_u8 > 0, transform=transform):
        if val == 0:
            continue
        wkt = _geojson_to_wkt(geom_dict)
        if not wkt:
            continue
        g = QgsGeometry.fromWkt(wkt)
        if g and not g.isEmpty():
            if simplify > 0:
                g = g.simplify(simplify)
            geoms.append(g)
    if geoms:
        return _select_mask_geometries(geoms, keep_all, pw)
    return _mask_bbox_geometries(mask, crop_info)


def _gdal_polygonize_wkts(
    mask: np.ndarray, crop_info: dict, simplify: float
) -> list[tuple[str, float]]:
    """
    Polygonize a mask with GDAL only (no QGIS objects).

    Safe to call from background QThreads — returns (wkt, area) pairs.
    """
    try:
        from osgeo import gdal, ogr, osr
    except ImportError:
        return []

    mask_u8 = (mask > 0).astype(np.uint8)
    h, w = mask_u8.shape[:2]
    if h == 0 or w == 0:
        return []

    xmin = float(crop_info["xmin"])
    ymax = float(crop_info["ymax"])
    pw = float(crop_info["pixel_width"])
    ph = float(crop_info["pixel_height"])
    gt = (xmin, pw, 0.0, ymax, 0.0, ph)

    mem_drv = gdal.GetDriverByName("MEM")
    if mem_drv is None:
        return []
    src_ds = mem_drv.Create("", w, h, 1, gdal.GDT_Byte)
    if src_ds is None:
        return []
    src_ds.SetGeoTransform(gt)
    band = src_ds.GetRasterBand(1)
    band.WriteArray(mask_u8)

    ogr_drv = ogr.GetDriverByName("Memory")
    if ogr_drv is None:
        return []
    dst_ds = ogr_drv.CreateDataSource("mask_poly")
    srs = osr.SpatialReference()
    layer_crs = crop_info.get("layer_crs")
    if layer_crs and hasattr(layer_crs, "toWkt"):
        srs.ImportFromWkt(layer_crs.toWkt())
    dst_lyr = dst_ds.CreateLayer("poly", srs=srs, geom_type=ogr.wkbPolygon)
    fld = ogr.FieldDefn("DN", ogr.OFTInteger)
    dst_lyr.CreateField(fld)

    err = gdal.Polygonize(band, band, dst_lyr, 0, [], callback=None)
    if err != 0:
        return []

    out: list[tuple[str, float]] = []
    for feat in dst_lyr:
        if feat.GetField(0) == 0:
            continue
        ogr_geom = feat.GetGeometryRef()
        if ogr_geom is None or ogr_geom.IsEmpty():
            continue
        if simplify > 0:
            ogr_geom = ogr_geom.Simplify(simplify)
            if ogr_geom is None or ogr_geom.IsEmpty():
                continue
        wkt = ogr_geom.ExportToWkt()
        if wkt:
            out.append((wkt, float(ogr_geom.GetArea())))
    return out


def mask_to_wkt_polygons(
    mask: np.ndarray,
    crop_info: dict,
    simplify: float | None = None,
    *,
    keep_all: bool = False,
) -> list[str]:
    """Convert binary mask to WKT polygons (thread-safe, no QGIS geometry objects)."""
    if mask is None or not np.any(mask):
        return []

    pw = abs(float(crop_info.get("pixel_width", 1.0)))
    if simplify is None:
        simplify = max(pw * 0.75, 0.01)

    pairs = _gdal_polygonize_wkts(mask, crop_info, simplify)
    if not pairs:
        return _mask_bbox_wkts(mask, crop_info)

    min_area = (pw * pw) * 24.0
    if keep_all:
        kept = [wkt for wkt, area in pairs if area >= min_area]
        if kept:
            return kept
        best = max(pairs, key=lambda item: item[1])
        return [best[0]]

    best = max(pairs, key=lambda item: item[1])
    return [best[0]]


def _mask_bbox_wkts(mask: np.ndarray, crop_info: dict) -> list[str]:
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return []
    pw = crop_info["pixel_width"]
    ph = crop_info["pixel_height"]
    xmin = crop_info["xmin"]
    ymax = crop_info["ymax"]
    x0 = xmin + xs.min() * pw
    x1 = xmin + (xs.max() + 1) * pw
    y0 = ymax + ys.max() * ph
    y1 = ymax + (ys.min()) * ph
    y_lo, y_hi = min(y0, y1), max(y0, y1)
    return [
        f"POLYGON (({x0} {y_lo}, {x1} {y_lo}, {x1} {y_hi}, {x0} {y_hi}, {x0} {y_lo}))"
    ]


def _mask_polygon_geometries_gdal(
    mask: np.ndarray, crop_info: dict, simplify: float
) -> list:
    """Polygonize mask with GDAL (bundled with QGIS) — true roof outlines, not bboxes."""
    from qgis.core import QgsGeometry

    geoms = []
    for wkt, _area in _gdal_polygonize_wkts(mask, crop_info, simplify):
        g = QgsGeometry.fromWkt(wkt)
        if g and not g.isEmpty():
            geoms.append(g)
    return geoms


def _mask_bbox_geometries(mask, crop_info):
    from qgis.core import QgsGeometry, QgsRectangle

    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return []
    pw = crop_info["pixel_width"]
    ph = crop_info["pixel_height"]
    xmin = crop_info["xmin"]
    ymax = crop_info["ymax"]
    x0 = xmin + xs.min() * pw
    x1 = xmin + (xs.max() + 1) * pw
    y0 = ymax + ys.max() * ph
    y1 = ymax + (ys.min()) * ph
    return [QgsGeometry.fromRect(QgsRectangle(x0, min(y0, y1), x1, max(y0, y1)))]


def _geojson_to_wkt(geojson: dict) -> str:
    gtype = geojson.get("type", "")
    coords = geojson.get("coordinates", [])
    if gtype == "Polygon" and coords:
        rings = []
        for ring in coords:
            pts = ", ".join(f"{c[0]} {c[1]}" for c in ring)
            rings.append(f"({pts})")
        return f"POLYGON ({', '.join(rings)})"
    return ""
