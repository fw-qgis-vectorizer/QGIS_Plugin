# -*- coding: utf-8 -*-
"""Local GDAL merge of edited obstacle patches into a copy of the source raster."""

from __future__ import annotations

import os
import shutil
from typing import Any

import numpy as np

from qgis.core import QgsRasterLayer

# inner_rect values are plain (xmin, ymin, xmax, ymax) tuples — never QgsRectangle
# in worker threads (QGIS C++ objects are main-thread only).


def layer_source_path(layer: QgsRasterLayer) -> str:
    src = (layer.source() or "").split("|")[0].strip()
    return src


def default_merged_output_path(source_path: str) -> str:
    base, ext = os.path.splitext(source_path)
    if not ext:
        ext = ".tif"
    return f"{base}_merged{ext}"


def _rect_tuple(rect) -> tuple[float, float, float, float] | None:
    """Normalize any rect-like value to (xmin, ymin, xmax, ymax)."""
    if rect is None:
        return None
    if isinstance(rect, (tuple, list)) and len(rect) >= 4:
        xmin, ymin, xmax, ymax = float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])
    else:
        try:
            xmin, ymin = float(rect.xMinimum()), float(rect.yMinimum())
            xmax, ymax = float(rect.xMaximum()), float(rect.yMaximum())
        except (AttributeError, RuntimeError, TypeError):
            return None
    if xmin > xmax:
        xmin, xmax = xmax, xmin
    if ymin > ymax:
        ymin, ymax = ymax, ymin
    if xmax <= xmin or ymax <= ymin:
        return None
    return xmin, ymin, xmax, ymax


def _rect_pixel_window(
    gt: tuple[float, ...],
    rect: tuple[float, float, float, float],
    raster_xsize: int,
    raster_ysize: int,
) -> tuple[int, int, int, int] | None:
    """Map rectangle (layer CRS) to pixel window col0, row0, width, height."""
    xmin, ymin, xmax, ymax = rect
    if gt[1] == 0 or gt[5] == 0:
        return None

    col0 = int(round((xmin - gt[0]) / gt[1]))
    col1 = int(round((xmax - gt[0]) / gt[1]))
    row0 = int(round((ymax - gt[3]) / gt[5]))
    row1 = int(round((ymin - gt[3]) / gt[5]))

    if col0 > col1:
        col0, col1 = col1, col0
    if row0 > row1:
        row0, row1 = row1, row0

    col0 = max(0, col0)
    row0 = max(0, row0)
    col1 = min(raster_xsize, col1)
    row1 = min(raster_ysize, row1)
    width = col1 - col0
    height = row1 - row0
    if width <= 0 or height <= 0:
        return None
    return col0, row0, width, height


def _gdal_type_to_numpy(gdal_type: int):
    from osgeo import gdal

    mapping = {
        gdal.GDT_Byte: np.uint8,
        gdal.GDT_UInt16: np.uint16,
        gdal.GDT_Int16: np.int16,
        gdal.GDT_UInt32: np.uint32,
        gdal.GDT_Int32: np.int32,
        gdal.GDT_Float32: np.float32,
        gdal.GDT_Float64: np.float64,
    }
    return mapping.get(gdal_type, np.float32)


def _paste_patch_window(
    dest_ds,
    patch_ds,
    *,
    dest_col: int,
    dest_row: int,
    patch_col: int,
    patch_row: int,
    width: int,
    height: int,
) -> str:
    dest_bands = dest_ds.RasterCount
    patch_bands = patch_ds.RasterCount
    if dest_bands == 0 or patch_bands == 0:
        return "Raster has no bands."

    bands = min(dest_bands, patch_bands)
    for band_idx in range(1, bands + 1):
        patch_band = patch_ds.GetRasterBand(band_idx)
        data = patch_band.ReadAsArray(patch_col, patch_row, width, height)
        if data is None:
            return f"Could not read patch band {band_idx}."
        if data.ndim == 0:
            data = np.array([[data]])
        elif data.ndim == 1:
            data = data.reshape(1, -1) if width == 1 else data.reshape(-1, 1)

        dest_band = dest_ds.GetRasterBand(band_idx)
        target_dtype = _gdal_type_to_numpy(dest_band.DataType)
        if data.dtype != target_dtype:
            data = data.astype(target_dtype)

        try:
            dest_band.WriteArray(data, dest_col, dest_row)
        except RuntimeError as exc:
            return f"Could not write band {band_idx}: {exc}"
    return ""


def _georef_compatible(dest_ds, patch_ds, tol: float = 1e-6) -> str:
    dest_gt = dest_ds.GetGeoTransform()
    patch_gt = patch_ds.GetGeoTransform()
    if abs(dest_gt[1] - patch_gt[1]) > tol or abs(dest_gt[5] - patch_gt[5]) > tol:
        return "Patch pixel size does not match the source raster."

    dest_proj = (dest_ds.GetProjection() or "").strip()
    patch_proj = (patch_ds.GetProjection() or "").strip()
    if dest_proj and patch_proj and dest_proj != patch_proj:
        return "Patch CRS does not match the source raster."
    return ""


def _warp_patch_onto_file(output_path: str, patch_path: str) -> str:
    """Fallback: gdal.Warp overlapping region into an existing GeoTIFF."""
    from osgeo import gdal

    try:
        result = gdal.Warp(
            output_path,
            patch_path,
            format="GTiff",
            options=gdal.WarpOptions(
                resampleAlg="near",
                multithread=True,
            ),
        )
    except RuntimeError as exc:
        return str(exc)
    if result is None:
        return f"GDAL warp failed for {os.path.basename(patch_path)}."
    result = None
    return ""


def _paste_patch_into_dest(
    dest_ds,
    patch_ds,
    output_path: str,
    inner_rect,
) -> str:
    """Write patch pixels into dest using georeferenced pixel windows."""
    err = _georef_compatible(dest_ds, patch_ds)
    if err:
        return err

    dest_gt = dest_ds.GetGeoTransform()
    patch_gt = patch_ds.GetGeoTransform()
    rect = _rect_tuple(inner_rect)

    if rect is not None:
        dest_win = _rect_pixel_window(
            dest_gt, rect, dest_ds.RasterXSize, dest_ds.RasterYSize
        )
        patch_win = _rect_pixel_window(
            patch_gt, rect, patch_ds.RasterXSize, patch_ds.RasterYSize
        )
        if not dest_win or not patch_win:
            err = _warp_patch_onto_file(output_path, patch_ds.GetDescription())
            return err or ""
        dx0, dy0, dw, dh = dest_win
        px0, py0, pw, ph = patch_win
        width = min(dw, pw)
        height = min(dh, ph)
        if width <= 0 or height <= 0:
            return "Obstacle box is outside the raster extent."
        err = _paste_patch_window(
            dest_ds,
            patch_ds,
            dest_col=dx0,
            dest_row=dy0,
            patch_col=px0,
            patch_row=py0,
            width=width,
            height=height,
        )
        if err:
            return _warp_patch_onto_file(output_path, patch_ds.GetDescription())
        return ""

    xoff = int(round((patch_gt[0] - dest_gt[0]) / dest_gt[1]))
    yoff = int(round((patch_gt[3] - dest_gt[3]) / dest_gt[5]))
    width = patch_ds.RasterXSize
    height = patch_ds.RasterYSize

    src_x0 = src_y0 = 0
    if xoff < 0:
        src_x0 = -xoff
        width += xoff
        xoff = 0
    if yoff < 0:
        src_y0 = -yoff
        height += yoff
        yoff = 0
    if xoff + width > dest_ds.RasterXSize:
        width = dest_ds.RasterXSize - xoff
    if yoff + height > dest_ds.RasterYSize:
        height = dest_ds.RasterYSize - yoff
    if width <= 0 or height <= 0:
        return "Patch falls outside the destination extent."

    err = _paste_patch_window(
        dest_ds,
        patch_ds,
        dest_col=xoff,
        dest_row=yoff,
        patch_col=src_x0,
        patch_row=src_y0,
        width=width,
        height=height,
    )
    if err:
        return _warp_patch_onto_file(output_path, patch_ds.GetDescription())
    return ""


def merge_patches_into_raster(
    source_path: str,
    patches: list[dict[str, Any]],
    output_path: str,
    *,
    progress_callback=None,
) -> tuple[str | None, str]:
    """
    Copy source to output, then paste each edited patch at its georeferenced location.

    Each patch entry: {"path": str, "inner_rect": (xmin, ymin, xmax, ymax) | None}
    """
    if not source_path or not os.path.isfile(source_path):
        return None, "Source raster file not found."
    if not patches:
        return None, "No patches selected for merge."

    for entry in patches:
        path = entry.get("path") if isinstance(entry, dict) else entry
        if not path or not os.path.isfile(path):
            name = os.path.basename(path or "")
            return None, f"Edited patch not found: {name}"

    try:
        from osgeo import gdal
    except ImportError:
        return None, "GDAL is not available."

    out_abs = os.path.abspath(output_path)
    src_abs = os.path.abspath(source_path)
    out_dir = os.path.dirname(out_abs) or "."
    if not os.path.isdir(out_dir):
        return None, "Output folder does not exist."

    total = len(patches)
    if progress_callback:
        progress_callback(0, total + 1, "Copying source raster…")

    try:
        if out_abs != src_abs:
            shutil.copy2(source_path, output_path)
    except OSError as exc:
        return None, f"Could not copy source raster: {exc}"

    dest_ds = gdal.Open(output_path, gdal.GA_Update)
    if dest_ds is None:
        return None, "Could not open output raster for writing."

    try:
        for i, entry in enumerate(patches):
            if isinstance(entry, dict):
                patch_path = entry["path"]
                inner = entry.get("inner_rect")
            else:
                patch_path = entry
                inner = None

            if progress_callback:
                progress_callback(
                    i + 1,
                    total + 1,
                    f"Merging patch {i + 1} of {total}…",
                )

            patch_ds = gdal.Open(patch_path, gdal.GA_ReadOnly)
            if patch_ds is None:
                return None, f"Could not open patch: {os.path.basename(patch_path)}"

            try:
                err = _paste_patch_into_dest(
                    dest_ds, patch_ds, output_path, inner
                )
            finally:
                patch_ds = None

            if err:
                return None, err

        dest_ds.FlushCache()
    finally:
        dest_ds = None

    if progress_callback:
        progress_callback(total + 1, total + 1, "Merge complete.")

    if not os.path.isfile(output_path):
        return None, "Merged file was not created."

    return output_path, ""
