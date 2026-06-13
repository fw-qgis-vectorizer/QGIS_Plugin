# -*- coding: utf-8 -*-
"""Background worker: crop locally, POST each edit to /qgis/edit."""

from __future__ import annotations

import json
import os
import tempfile
import uuid

from qgis.PyQt.QtCore import QThread, pyqtSignal


class ObstacleRemovalWorker(QThread):
    """Process AI edit jobs sequentially against the nano banana edit API."""

    progress = pyqtSignal(int, int, str)
    box_done = pyqtSignal(int, str, str)
    box_failed = pyqtSignal(int, str)
    finished_all = pyqtSignal(int, int)
    error = pyqtSignal(str)

    def __init__(
        self,
        layer,
        edit_jobs,
        padding_m,
        auth_client,
        source_name,
        parent=None,
    ):
        super().__init__(parent)
        self._layer = layer
        self._edit_jobs = list(edit_jobs)
        self._padding_m = padding_m
        self._auth_client = auth_client
        self._source_name = source_name or "imagery"
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        from ..core.obstacle_crop import crop_to_geotiff
        from ..core.obstacle_removal_api import post_edit

        total = len(self._edit_jobs)
        ok_count = 0
        if total == 0:
            self.error.emit("No edit regions to process.")
            return

        for i, job in enumerate(self._edit_jobs):
            if self._cancelled:
                break
            idx = i + 1
            self.progress.emit(idx, total, f"Cropping {idx} of {total}…")

            crop_path = None
            tif_bytes = None
            err = ""
            rect = job["crop_rect"]
            mask = job.get("mask_geojson")
            if isinstance(mask, dict):
                mask_str = json.dumps(mask)
                mask_dict = mask
            else:
                mask_str = str(mask or "")
                try:
                    mask_dict = json.loads(mask_str) if mask_str else None
                except ValueError:
                    mask_dict = None

            prompt = job.get("prompt") or ""
            layer_suffix = job.get("layer_suffix") or f"edit_{idx}"

            try:
                crop_path, _meta, err = crop_to_geotiff(
                    self._layer,
                    rect,
                    padding_m=self._padding_m,
                    mask_geometry=mask_dict,
                )
                if not crop_path:
                    self.box_failed.emit(idx, err or "Crop failed.")
                    continue

                self.progress.emit(idx, total, f"Editing {idx} of {total}…")
                tif_bytes, _hdrs, err = post_edit(
                    crop_path,
                    prompt,
                    mask_str or None,
                    auth_client=self._auth_client,
                )
            except Exception as exc:
                self.box_failed.emit(idx, f"{type(exc).__name__}: {exc}")
                continue
            finally:
                if crop_path:
                    try:
                        os.remove(crop_path)
                    except OSError:
                        pass

            if self._cancelled:
                break
            if not tif_bytes:
                self.box_failed.emit(idx, err or "Edit failed.")
                continue

            out_path = os.path.join(
                tempfile.gettempdir(),
                f"fieldwatch_nano_edit_{uuid.uuid4().hex}.tif",
            )
            try:
                with open(out_path, "wb") as fh:
                    fh.write(tif_bytes)
            except OSError as exc:
                self.box_failed.emit(idx, str(exc))
                continue

            layer_name = f"{self._source_name}_{layer_suffix}"
            self.box_done.emit(idx, out_path, layer_name)
            ok_count += 1

        self.finished_all.emit(ok_count, total)
