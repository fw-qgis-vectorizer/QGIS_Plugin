# -*- coding: utf-8 -*-
"""Background worker: merge selected edit patches into a new GeoTIFF."""

from __future__ import annotations

from qgis.PyQt.QtCore import QThread, pyqtSignal


class ObstacleMergeWorker(QThread):
    """Copy source raster and paste selected patches into a new output file."""

    progress = pyqtSignal(int, int, str)
    finished_ok = pyqtSignal(str, str)
    error = pyqtSignal(str)

    def __init__(
        self,
        source_path: str,
        patches: list[dict],
        output_path: str,
        output_layer_name: str,
        parent=None,
    ):
        super().__init__(parent)
        self._source_path = source_path
        self._patches = list(patches)
        self._output_path = output_path
        self._output_layer_name = output_layer_name

    def run(self):
        from ..core.obstacle_merge import merge_patches_into_raster

        def _progress(current: int, total: int, message: str):
            self.progress.emit(current, total, message)

        try:
            out_path, err = merge_patches_into_raster(
                self._source_path,
                self._patches,
                self._output_path,
                progress_callback=_progress,
            )
        except Exception as exc:
            self.error.emit(f"{type(exc).__name__}: {exc}")
            return

        if not out_path:
            self.error.emit(err or "Merge failed.")
            return

        self.finished_ok.emit(out_path, self._output_layer_name)
