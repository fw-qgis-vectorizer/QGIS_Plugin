# -*- coding: utf-8 -*-
"""Orchestrates one-click segmentation session."""

from __future__ import annotations

import numpy as np

from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsFillSymbol,
    QgsGeometry,
    QgsMessageLog,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.gui import QgisInterface, QgsRubberBand
from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QMessageBox

from .one_click_crop import (
    click_needs_new_crop,
    extract_crop_at_point,
    map_point_to_crop_pixels,
    mask_to_map_geometries,
    model_pixels_from_crop,
    resize_for_encode,
    upscale_mask_to_crop,
)
from .one_click_prompts import OneClickPromptManager
from .one_click_workers import ModelEncodeWorker, ModelInitWorker, ModelPredictWorker
from .model_config import (
    LOG_CHANNEL,
    checkpoint_exists,
    encode_max_side,
    resolve_checkpoint_path,
)
from .model_predictor import ModelPredictor, build_model_predictor_config
from .one_click_log import sanitize_log_line
from .model_venv import deps_installed
from ..ui.one_click_maptool import OneClickMapTool
from ..ui.one_click_shortcut_filter import OneClickShortcutFilter


class OneClickSegmentationController:
    def __init__(self, iface: QgisInterface, settings_dialog, plugin=None):
        self.iface = iface
        self.settings = settings_dialog
        self._plugin = plugin
        self.map_tool: OneClickMapTool | None = None
        self.predictor: ModelPredictor | None = None
        self._predictor_is_shared = False
        self.prompts = OneClickPromptManager()
        self._active = False
        self._layer: QgsRasterLayer | None = None
        self._crop_info = None
        self._encoding = False
        self._predicting = False
        self._saved_geometries: list[QgsGeometry] = []
        self._last_mask_geoms: list[QgsGeometry] = []
        self._mask_band: QgsRubberBand | None = None
        self._saved_bands: list[QgsRubberBand] = []
        self._encode_worker = None
        self._predict_worker = None
        self._init_worker = None
        self._starting = False
        self._previous_map_tool = None
        self._shortcut_filter: OneClickShortcutFilter | None = None
        self._trial_session_consumed = False

    def apply_shared_predictor(self, predictor: ModelPredictor | None) -> None:
        """Use the model loaded when the main plugin opened (do not stop/cleanup on session end)."""
        self._predictor_is_shared = predictor is not None
        self.predictor = predictor

    @staticmethod
    def _predictor_ready(predictor: ModelPredictor | None) -> bool:
        if predictor is None:
            return False
        proc = predictor.process
        return proc is not None and proc.poll() is None

    def refresh_status(self):
        self.settings.refresh_ui_local()

    def on_layer_changed(self):
        """Layer combo changed — refresh status labels only."""
        from .model_config import checkpoint_exists

        self.settings.update_status(deps_installed(), checkpoint_exists())

    def _ensure_license_for_one_click(self) -> bool:
        from . import trial_helpers
        from .trial_access import ONE_CLICK_TRIAL_MIN_REMAINING, shared as trial_shared

        acc = trial_shared()
        if acc.can_start_one_click_segmentation():
            return True
        rem = acc.uses_remaining
        if not trial_helpers.is_trial_established() and not acc.has_paid_license():
            message = self.settings.tr(
                "Trial quota is loading. Please wait, or validate a paid licence key."
            )
        elif (
            rem is not None
            and rem < ONE_CLICK_TRIAL_MIN_REMAINING
            and not acc.has_paid_license()
        ):
            message = self.settings.tr("Add licence key to use.")
        else:
            message = self.settings.tr(
                "Validate a paid licence key, then try Start again."
            )
        QtWidgets.QMessageBox.warning(
            self.settings,
            self.settings.tr("License required"),
            message,
        )
        return False

    def _debit_trial_for_session(self) -> bool:
        from .trial_access import shared as trial_shared

        acc = trial_shared()
        if acc.has_paid_license():
            self._trial_session_consumed = False
            return True
        ok, msg, _data = acc.consume_for_one_click_session()
        if not ok:
            QtWidgets.QMessageBox.warning(
                self.settings,
                self.settings.tr("Trial quota"),
                msg or self.settings.tr("Cannot start segmentation."),
            )
            self.settings.refresh_ui_local()
            return False
        self._trial_session_consumed = True
        self.settings.refresh_ui_local()
        return True

    def start_segmentation(self):
        if self._starting or self.settings._setup_busy:
            return
        if not self._ensure_license_for_one_click():
            return
        layer = self.settings.get_raster_layer()
        if not layer or not layer.isValid():
            QMessageBox.warning(
                self.settings,
                self.settings.tr("One-Click Segmentation"),
                self.settings.tr(
                    "Add a raster layer to the map (Layer → Add Raster Layer), "
                    "then click Refresh and try again."
                ),
            )
            return
        if not deps_installed():
            self.settings.run_dependency_install(
                lambda ok: self._continue_start_segmentation() if ok else None
            )
            return
        if not checkpoint_exists():
            self.settings.run_weight_download(
                lambda ok: self._continue_start_segmentation() if ok else None
            )
            return
        self._start_segmentation_core(layer)

    def _continue_start_segmentation(self):
        if self._starting or self.settings._setup_busy:
            return
        layer = self.settings.get_raster_layer()
        if not layer or not layer.isValid():
            return
        if not deps_installed() or not checkpoint_exists():
            return
        if not self._debit_trial_for_session():
            return
        self._start_segmentation_core(layer)

    def _start_segmentation_core(self, layer: QgsRasterLayer):
        if self._plugin is not None:
            shared = getattr(self._plugin, "_segmentation_predictor", None)
            if self._predictor_ready(shared):
                self.apply_shared_predictor(shared)

        if self._predictor_ready(self.predictor):
            self._activate_segmentation_session(layer)
            return

        plugin = self._plugin
        if (
            plugin is not None
            and getattr(plugin, "_segmentation_preloading", False)
            and getattr(plugin, "_segmentation_preload_worker", None) is not None
        ):
            self._starting = True
            self._pending_layer = layer
            self.settings.set_busy(
                True, self.settings.tr("First startup can take about a minute…")
            )
            worker = plugin._segmentation_preload_worker
            worker.finished.connect(self._on_model_init_finished)
            worker.error.connect(self._on_model_init_error)
            return

        try:
            config = build_model_predictor_config(resolve_checkpoint_path())
        except Exception as exc:
            QMessageBox.critical(
                self.settings,
                self.settings.tr("One-Click Segmentation"),
                str(exc)[:500],
            )
            return

        self._starting = True
        self._pending_layer = layer
        load_msg = self.settings.tr("First startup can take about a minute…")
        self.settings.set_busy(True, load_msg)
        QgsMessageLog.logMessage(
            "Starting segmentation…",
            LOG_CHANNEL,
            level=Qgis.MessageLevel.Info,
        )

        self._init_worker = ModelInitWorker(config)
        self._init_worker.finished.connect(self._on_model_init_finished)
        self._init_worker.error.connect(self._on_model_init_error)
        self._init_worker.start()

    def _activate_segmentation_session(self, layer: QgsRasterLayer) -> None:
        self._layer = layer
        self.prompts.clear()
        self._crop_info = None
        self._active = True
        self._reset_mask_state()
        self._install_map_tool()
        self.settings.set_segmentation_active(True)
        self.settings.set_status(
            self.settings.tr(
                "Segmentation active — left: add roof, right: delete mask. "
                "Pan: Space (toggle) + move/trackpad, or middle mouse drag."
            )
        )
        QgsMessageLog.logMessage(
            "Segmentation ready.",
            LOG_CHANNEL,
            level=Qgis.MessageLevel.Info,
        )
        self.iface.messageBar().pushMessage(
            "FieldWatch",
            self.settings.tr("One-click segmentation started."),
            level=Qgis.MessageLevel.Info,
            duration=4,
        )

    def _on_model_init_finished(self, predictor, _msg: str):
        self._starting = False
        self.settings.set_busy(False)
        self.predictor = predictor
        if self._plugin is not None:
            self._plugin._segmentation_predictor = predictor
            self._plugin._segmentation_preloading = False
            self._predictor_is_shared = True
        layer = getattr(self, "_pending_layer", None)
        self._pending_layer = None
        if not layer:
            return
        self._activate_segmentation_session(layer)

    def _on_model_init_error(self, message: str):
        self._starting = False
        self._pending_layer = None
        self.settings.set_busy(False)
        self.settings.refresh_ui_local()
        safe_message = sanitize_log_line(message)
        QgsMessageLog.logMessage(
            f"Segmentation startup failed: {safe_message}",
            LOG_CHANNEL,
            level=Qgis.MessageLevel.Critical,
        )
        QMessageBox.critical(
            self.settings,
            self.settings.tr("One-Click Segmentation"),
            safe_message[:2000],
        )

    def _wait_background_workers(self, unloading: bool) -> None:
        timeout = 500 if unloading else 3000
        for worker in (
            self._predict_worker,
            self._encode_worker,
            self._init_worker,
        ):
            if worker is not None and worker.isRunning():
                worker.wait(timeout)
        self._predict_worker = None
        self._encode_worker = None
        self._init_worker = None

    def stop_segmentation(self, *, unloading: bool = False):
        self._starting = False
        self._pending_layer = None
        self._trial_session_consumed = False
        self._wait_background_workers(unloading)
        self._active = False
        if unloading:
            self._remove_map_tool(unloading=True)
        else:
            self._remove_map_tool()
        if self.predictor:
            if self._predictor_is_shared or unloading:
                self.predictor = None
            else:
                self.predictor.cleanup()
                self.predictor = None
        self._reset_mask_state(unloading=unloading)
        if self.map_tool and not unloading:
            try:
                self.map_tool.clear_markers()
            except RuntimeError:
                pass
        settings = self.settings
        if settings is not None:
            if unloading:
                settings.set_segmentation_active(False, unloading=True)
            else:
                settings.set_segmentation_active(False)
                settings.set_status(settings.tr("Segmentation stopped."))
                self.refresh_status()
        if unloading:
            self.map_tool = None

    def shutdown(self):
        self.stop_segmentation(unloading=True)

    def save_all_to_layer(self):
        from .trial_access import shared as trial_shared

        acc = trial_shared()
        if acc.has_paid_license():
            ok, msg, _usage = acc.consume_for_one_click_export()
            if not ok:
                QMessageBox.warning(
                    self.settings,
                    self.settings.tr("License"),
                    msg or self.settings.tr("Cannot save."),
                )
                return
            if msg:
                self.iface.messageBar().pushMessage(
                    "FieldWatch",
                    msg,
                    level=Qgis.MessageLevel.Info,
                    duration=6,
                )
        elif not self._trial_session_consumed:
            ok, msg, _usage = acc.consume_for_one_click_export()
            if not ok:
                QMessageBox.warning(
                    self.settings,
                    self.settings.tr("Trial quota"),
                    msg or self.settings.tr("Cannot save — trial quota exhausted."),
                )
                return
            if msg:
                self.iface.messageBar().pushMessage(
                    "FieldWatch",
                    msg,
                    level=Qgis.MessageLevel.Info,
                    duration=6,
                )

        geoms = list(self._saved_geometries)
        if self._last_mask_geoms:
            geoms.extend(self._last_mask_geoms)

        if not geoms:
            QMessageBox.information(
                self.settings,
                self.settings.tr("Save to layer"),
                self.settings.tr("No polygons to save. Click to segment first."),
            )
            return

        name = self.settings.get_output_layer_name()
        crs = self._layer.crs() if self._layer else QgsProject.instance().crs()
        layer = QgsVectorLayer(
            f"Polygon?crs={crs.authid()}",
            name,
            "memory",
        )
        pr = layer.dataProvider()
        pr.addAttributes(
            [
                QgsField("id", QVariant.Int),
                QgsField("score", QVariant.Double),
            ]
        )
        layer.updateFields()

        for idx, geom in enumerate(geoms):
            feat = QgsFeature()
            feat.setGeometry(geom)
            feat.setAttributes([idx + 1, 0.0])
            pr.addFeature(feat)

        symbol = QgsFillSymbol.createSimple(
            {"color": "0,180,80,80", "outline_color": "0,120,40,255", "outline_width": "0.8"}
        )
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        layer.updateExtents()
        QgsProject.instance().addMapLayer(layer)
        self.iface.mapCanvas().setExtent(layer.extent())
        self.iface.mapCanvas().refresh()

        self._reset_mask_state()
        self._clear_saved_bands()
        if self.map_tool:
            self.map_tool.clear_markers()
        self.prompts.clear()
        self._crop_info = None

        self.iface.messageBar().pushMessage(
            "FieldWatch",
            self.settings.tr("Saved {n} polygon(s) to '{name}'.").format(
                n=len(geoms), name=name
            ),
            level=Qgis.MessageLevel.Success,
            duration=5,
        )

    def _install_map_tool(self):
        canvas = self.iface.mapCanvas()
        if self.map_tool is None:
            self.map_tool = OneClickMapTool(canvas)
            self.map_tool.positive_click.connect(self._on_positive_click)
            self.map_tool.mask_delete_click.connect(self._on_mask_delete_click)
        self._install_shortcut_filter()
        self._previous_map_tool = canvas.mapTool()
        canvas.setMapTool(self.map_tool)

    def _install_shortcut_filter(self) -> None:
        if self._shortcut_filter is not None:
            return
        main_window = self.iface.mainWindow()
        if main_window is None:
            return
        self._shortcut_filter = OneClickShortcutFilter(self, main_window)
        main_window.installEventFilter(self._shortcut_filter)

    def _remove_shortcut_filter(self) -> None:
        if self._shortcut_filter is None:
            return
        if self.map_tool:
            self.map_tool.release_space_pan()
        main_window = self.iface.mainWindow()
        if main_window is not None:
            main_window.removeEventFilter(self._shortcut_filter)
        self._shortcut_filter = None

    def _remove_map_tool(self, *, unloading: bool = False):
        try:
            self._remove_shortcut_filter()
            if self.map_tool is None:
                return
            canvas = self.iface.mapCanvas()
            if canvas is None:
                return
            if canvas.mapTool() == self.map_tool:
                if self._previous_map_tool:
                    canvas.setMapTool(self._previous_map_tool)
                else:
                    canvas.unsetMapTool(self.map_tool)
        except RuntimeError:
            if not unloading:
                raise

    def _canvas_crs(self):
        return self.iface.mapCanvas().mapSettings().destinationCrs()

    def _on_positive_click(self, point: QgsPointXY):
        self._handle_click(point, positive=True)

    def _on_mask_delete_click(self, point: QgsPointXY):
        if not self._active:
            return
        if self._delete_mask_at_point(point):
            self.settings.set_status(self.settings.tr("Mask removed."))
        else:
            self.settings.set_status(
                self.settings.tr("Right-click on a mask to delete it.")
            )

    def _point_in_layer_crs(self, point: QgsPointXY) -> QgsPointXY:
        layer_crs = self._layer.crs() if self._layer else None
        canvas_crs = self._canvas_crs()
        if (
            layer_crs
            and layer_crs.isValid()
            and canvas_crs.isValid()
            and layer_crs != canvas_crs
        ):
            xform = QgsCoordinateTransform(
                canvas_crs,
                layer_crs,
                QgsProject.instance().transformContext(),
            )
            return xform.transform(QgsPointXY(point))
        return QgsPointXY(point)

    def _click_tolerance_map_units(self) -> float:
        canvas = self.iface.mapCanvas()
        extent = canvas.extent()
        w = max(canvas.width(), 1)
        h = max(canvas.height(), 1)
        tol = max(extent.width() / w, extent.height() / h) * 8.0
        return max(tol, 0.01)

    def _geometry_hit_index(self, point: QgsPointXY, geoms: list[QgsGeometry]) -> int | None:
        if not geoms:
            return None
        pt = QgsGeometry.fromPointXY(self._point_in_layer_crs(point))
        tol = self._click_tolerance_map_units()
        for idx in range(len(geoms) - 1, -1, -1):
            g = QgsGeometry(geoms[idx])
            if g.isEmpty():
                continue
            if g.contains(pt) or g.distance(pt) <= tol:
                return idx
        return None

    def _delete_mask_at_point(self, point: QgsPointXY) -> bool:
        """Remove the mask under the cursor (current or saved)."""
        idx = self._geometry_hit_index(point, self._last_mask_geoms)
        if idx is not None:
            del self._last_mask_geoms[idx]
            self.prompts.clear()
            if self.predictor:
                self.predictor._low_res_mask = None
            if self.map_tool:
                self.map_tool.clear_markers()
            self._show_all_masks()
            return True

        idx = self._geometry_hit_index(point, self._saved_geometries)
        if idx is not None:
            del self._saved_geometries[idx]
            self._show_all_masks()
            return True
        return False

    def _add_click_prompt(self, point: QgsPointXY, positive: bool) -> bool:
        """Record a click in crop pixel space. Returns False if outside the crop."""
        if not self._crop_info:
            return False
        pixels = map_point_to_crop_pixels(point, self._crop_info, self._canvas_crs())
        if pixels is None:
            self.settings.set_status(
                self.settings.tr("Click inside the highlighted map area (current crop).")
            )
            return False
        if positive:
            self.prompts.add_positive_point(*pixels)
        else:
            self.prompts.add_negative_point(*pixels)
        return True

    def _freeze_current_mask(self) -> None:
        """Keep the current polygon when starting a new building/crop."""
        if self._last_mask_geoms:
            self._saved_geometries.extend(self._last_mask_geoms)
            self._last_mask_geoms = []

    def _begin_crop_at_point(self, point: QgsPointXY, positive: bool) -> None:
        """Extract + encode a new crop centered on this click."""
        if self._encoding:
            return
        self._encoding = True
        self.settings.set_busy(True, self.settings.tr("Reading image crop…"))
        image, crop_info, err = extract_crop_at_point(
            self._layer, point, self._canvas_crs()
        )
        if image is None:
            self._encoding = False
            self.settings.set_busy(False)
            QMessageBox.warning(self.settings, "Error", err or "Could not read crop.")
            return
        image_enc, encode_scale = resize_for_encode(image, encode_max_side())
        crop_info["encode_scale"] = encode_scale
        self._crop_info = crop_info
        self._add_click_prompt(point, positive)
        if self.predictor:
            self.predictor._low_res_mask = None
        self.settings.set_busy(
            True,
            self.settings.tr("Preparing map area…"),
        )
        self._encode_worker = ModelEncodeWorker(self.predictor, image_enc)
        self._encode_worker.finished.connect(self._on_encode_done)
        self._encode_worker.error.connect(self._on_encode_error)
        self._encode_worker.start()

    def _handle_click(self, point: QgsPointXY, positive: bool):
        if not self._active or not self._layer or self._predicting:
            return

        # Another building / far away → new crop (TerraLab-style), not one giant mask.
        if (
            self._crop_info is not None
            and positive
            and click_needs_new_crop(
                point, self._crop_info, self.prompts.positive_points, self._canvas_crs()
            )
        ):
            self._freeze_current_mask()
            self.prompts.clear()
            self._crop_info = None
            if self.predictor:
                self.predictor._low_res_mask = None
            self._show_all_masks()
            if self.map_tool:
                self.map_tool.clear_markers()
            self.map_tool.add_marker(point, is_positive=True)
            self._begin_crop_at_point(point, positive)
            return

        # First click: read crop, queue encode, keep accepting more points while encoding.
        if self._crop_info is None:
            if self._encoding:
                return
            self._begin_crop_at_point(point, positive)
            return

        if self._encoding:
            self._add_click_prompt(point, positive)
            return

        if not self._add_click_prompt(point, positive):
            return
        self._run_predict()

    def _on_encode_done(self, ok, msg):
        self._encoding = False
        self.settings.set_busy(False)
        if not ok:
            QMessageBox.warning(self.settings, "Error", msg or "Encoding failed.")
            self._crop_info = None
            self.prompts.clear()
            return
        if not self.prompts.positive_points:
            self.settings.set_status(
                self.settings.tr("Add at least one point on the target, then wait for encoding.")
            )
            return
        self._run_predict()

    def _on_encode_error(self, message):
        self._encoding = False
        self.settings.set_busy(False)
        self._crop_info = None
        QMessageBox.critical(
            self.settings,
            self.settings.tr("Error"),
            sanitize_log_line(message)[:500],
        )

    def _run_predict(self):
        if not self.predictor or not self.prompts.positive_points:
            return
        model_pts = [
            model_pixels_from_crop(p, self._crop_info)
            for p in self.prompts.positive_points + self.prompts.negative_points
        ]
        coords = np.array(model_pts, dtype=np.float32).reshape(-1, 2).tolist()
        labels = [1] * len(self.prompts.positive_points) + [0] * len(
            self.prompts.negative_points
        )
        self._predicting = True
        self.settings.set_busy(True, self.settings.tr("Updating mask…"))
        mask_input = None
        multimask = None
        if self.predictor:
            mask_input = self.predictor._low_res_mask
        pos = len(self.prompts.positive_points)
        neg = len(self.prompts.negative_points)
        if pos == 1 and neg == 0 and mask_input is None:
            multimask = True
        self._predict_worker = ModelPredictWorker(
            self.predictor,
            coords,
            labels,
            mask_input=mask_input,
            multimask_output=multimask,
        )
        self._predict_worker.finished.connect(self._on_predict_done)
        self._predict_worker.error.connect(self._on_predict_error)
        self._predict_worker.start()

    def _on_predict_done(self, mask, score, _msg):
        self._predicting = False
        self.settings.set_busy(False)
        try:
            mask_full = upscale_mask_to_crop(mask, self._crop_info)
            geoms = mask_to_map_geometries(mask_full, self._crop_info)
            self._last_mask_geoms = geoms
            self._show_all_masks()
            if geoms:
                self.settings.set_status(
                    self.settings.tr("Mask updated (score {s:.2f}). Click to refine.").format(
                        s=score
                    )
                )
            else:
                self.settings.set_status(
                    self.settings.tr(
                        "No mask at these points — try a point closer to the object center."
                    )
                )
                QgsMessageLog.logMessage(
                    "Segmentation returned an empty mask.",
                    LOG_CHANNEL,
                    Qgis.MessageLevel.Warning,
                )
        except Exception as exc:
            QgsMessageLog.logMessage(
                sanitize_log_line(str(exc)), LOG_CHANNEL, Qgis.MessageLevel.Warning
            )

    def _on_predict_error(self, message):
        self._predicting = False
        self.settings.set_busy(False)
        safe = sanitize_log_line(message)
        QgsMessageLog.logMessage(safe[:2000], LOG_CHANNEL, Qgis.MessageLevel.Warning)
        self.settings.set_status(self.settings.tr("Prediction failed — see Log Messages."))
        QMessageBox.warning(
            self.settings, self.settings.tr("Error"), safe[:400]
        )

    def _geoms_for_canvas(self, geoms: list[QgsGeometry]) -> list[QgsGeometry]:
        """Transform layer-CRS mask polygons to the map canvas CRS for display."""
        if not self._layer or not geoms:
            return geoms
        layer_crs = self._layer.crs()
        canvas_crs = self._canvas_crs()
        if (
            not layer_crs.isValid()
            or not canvas_crs.isValid()
            or layer_crs == canvas_crs
        ):
            return geoms
        xform = QgsCoordinateTransform(
            layer_crs,
            canvas_crs,
            QgsProject.instance().transformContext(),
        )
        out = []
        for geom in geoms:
            g = QgsGeometry(geom)
            try:
                g.transform(xform)
            except Exception:
                continue
            if g and not g.isEmpty():
                out.append(g)
        return out

    def _all_display_geometries(self) -> list[QgsGeometry]:
        geoms: list[QgsGeometry] = []
        for g in self._saved_geometries:
            geoms.append(QgsGeometry(g))
        for g in self._last_mask_geoms:
            geoms.append(QgsGeometry(g))
        return geoms

    def _show_all_masks(self) -> None:
        self._show_mask(self._all_display_geometries())

    def _show_mask(self, geoms):
        """Draw each mask as its own geometry (addPoint chains separate roofs)."""
        self._remove_mask_rubberband()
        geoms = self._geoms_for_canvas(geoms)
        if not geoms:
            return
        canvas = self.iface.mapCanvas()
        band = QgsRubberBand(canvas, QgsWkbTypes.PolygonGeometry)
        band.setColor(QColor(0, 200, 80, 200))
        band.setFillColor(QColor(0, 200, 80, 80))
        band.setWidth(2)
        canvas_crs = self._canvas_crs()
        for geom in geoms:
            if not geom or geom.isEmpty():
                continue
            if geom.type() != QgsWkbTypes.PolygonGeometry:
                continue
            if geom.isMultipart():
                for part in geom.asGeometryCollection():
                    if part and not part.isEmpty():
                        band.addGeometry(part, canvas_crs)
            else:
                band.addGeometry(geom, canvas_crs)
        band.show()
        self._mask_band = band

    def _remove_mask_rubberband(self) -> None:
        if self._mask_band:
            try:
                self.iface.mapCanvas().scene().removeItem(self._mask_band)
            except RuntimeError:
                pass
            self._mask_band = None

    def _reset_mask_state(self, *, unloading: bool = False) -> None:
        self._saved_geometries = []
        self._last_mask_geoms = []
        if not unloading:
            self._remove_mask_rubberband()
        else:
            self._mask_band = None

    def _clear_saved_bands(self):
        for band in self._saved_bands:
            try:
                self.iface.mapCanvas().scene().removeItem(band)
            except RuntimeError:
                pass
        self._saved_bands = []

