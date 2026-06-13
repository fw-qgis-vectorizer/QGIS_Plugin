# -*- coding: utf-8 -*-
"""AI imagery edit — obstacle removal + solar panels via /qgis/edit."""

from __future__ import annotations

import json
import uuid

from qgis.core import (
    QgsGeometry,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsWkbTypes,
)
from qgis.gui import QgsRubberBand
from qgis.PyQt import QtWidgets, QtCore
from qgis.PyQt.QtGui import QColor

from .license_trial_panel import LicenseTrialPanel
from ..core.obstacle_edit_config import (
    OBSTACLE_REMOVAL_SYSTEM_PROMPT,
    SOLAR_PANELS_PROMPT,
    build_edit_prompt,
)
from ..core.qgis_layer_utils import layer_is_usable, list_project_raster_layers
from ..core.qt_compat import AlignCenter, CheckStateChecked, ItemDataUserRole, dialog_exec
from ..core.trial_access import shared as trial_shared
from ..core.obstacle_crop import (
    geometry_to_mask_geojson,
    rect_tuple_from_qgs_rect,
    rectangle_size_meters,
    rect_tuple_to_mask_geojson,
)
from ..ui.obstacle_box_maptool import ObstacleBoxMapTool
from ..ui.obstacle_polygon_maptool import ObstaclePolygonMapTool
from ..ui.one_click_shortcut_filter import SpacePanShortcutFilter
from ..workers.obstacle_merge_worker import ObstacleMergeWorker
from ..workers.obstacle_removal_worker import ObstacleRemovalWorker


class ObstacleRemovalDialog(QtWidgets.QDialog):
    """Draw removal boxes, enter one prompt, POST crops to nano banana edit API."""

    _PRESETS = (
        ("vehicle", "Remove the vehicle and reconstruct the ground surface beneath it"),
        (
            "equipment",
            "Remove the construction equipment and blend with the surrounding surface",
        ),
        ("tarp", "Remove the tarp and restore the surface underneath"),
        ("shadow", "Remove the shadow artifact and reconstruct the surface"),
    )

    _BOX_FILL = QColor(255, 120, 40, 70)
    _BOX_LINE = QColor(255, 120, 40, 220)

    _SOLAR_FILL = QColor(60, 180, 255, 70)
    _SOLAR_LINE = QColor(60, 180, 255, 220)

    def __init__(self, iface, parent=None, pack_dialog=None):
        super().__init__(parent or iface.mainWindow())
        self.iface = iface
        self._pack_dialog = pack_dialog
        self._boxes: list[dict] = []
        self._solar_areas: list[dict] = []
        self._overlay_bands: list[QgsRubberBand] = []
        self._map_tool: ObstacleBoxMapTool | None = None
        self._poly_map_tool: ObstaclePolygonMapTool | None = None
        self._previous_map_tool = None
        self._worker: ObstacleRemovalWorker | None = None
        self._merge_worker: ObstacleMergeWorker | None = None
        self._source_layer_id: str | None = None
        self._edit_group_name = self.tr("AI edits")
        self._solar_group_name = self.tr("Solar panels")
        self._shutting_down = False
        self._active_edit_kind = "obstacle"
        self._active_worker_kind = "obstacle"
        self._shortcut_filter: SpacePanShortcutFilter | None = None

        self.setWindowTitle(self.tr("AI Imagery Edit"))
        self.setModal(False)
        self.resize(520, 680)
        self._build_ui()
        self.populate_layers()
        self._sync_apply_state()
        self._install_shortcut_filter()

    def _active_map_tool(self):
        canvas = self.iface.mapCanvas() if self.iface else None
        if not canvas:
            return None
        tool = canvas.mapTool()
        if tool is self._map_tool or tool is self._poly_map_tool:
            return tool
        return None

    def _install_shortcut_filter(self) -> None:
        if self._shortcut_filter is not None:
            return
        main_window = self.iface.mainWindow() if self.iface else None
        if main_window is None:
            return
        self._shortcut_filter = SpacePanShortcutFilter(self._active_map_tool, main_window)
        main_window.installEventFilter(self._shortcut_filter)

    def _remove_shortcut_filter(self) -> None:
        if self._shortcut_filter is None:
            return
        for tool in (self._map_tool, self._poly_map_tool):
            if tool:
                tool.release_space_pan()
        main_window = self.iface.mainWindow() if self.iface else None
        if main_window is not None:
            main_window.removeEventFilter(self._shortcut_filter)
        self._shortcut_filter = None

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QtWidgets.QLabel(
            self.tr(
                "Remove obstacles with boxes, or add solar panels by drawing a roof polygon"
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        pan_hint = QtWidgets.QLabel(
            self.tr(
                "Pan: Space (toggle) + move/trackpad, or middle mouse drag."
            )
        )
        pan_hint.setWordWrap(True)
        pan_hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(pan_hint)

        self.licensePanel = LicenseTrialPanel(
            self,
            allow_get_licence_button=True,
            mask_license_input=False,
            prefill_license_key=False,
            cache_trial_display_only=True,
        )
        layout.addWidget(self.licensePanel)

        layer_row = QtWidgets.QHBoxLayout()
        layout.addWidget(QtWidgets.QLabel(self.tr("Input raster layer:")))
        self.layerCombo = QtWidgets.QComboBox()
        self.refreshLayerButton = QtWidgets.QPushButton(self.tr("Refresh"))
        layer_row.addWidget(self.layerCombo, 1)
        layer_row.addWidget(self.refreshLayerButton)
        layout.addLayout(layer_row)

        self.tabs = QtWidgets.QTabWidget()

        # --- Obstacle removal tab ---
        obstacle_tab = QtWidgets.QWidget()
        obstacle_layout = QtWidgets.QVBoxLayout(obstacle_tab)
        obstacle_layout.addWidget(
            QtWidgets.QLabel(self.tr("What should be removed or changed?"))
        )
        self.promptEdit = QtWidgets.QPlainTextEdit()
        self.promptEdit.setPlaceholderText(
            self.tr(
                "e.g. Remove the vehicle and reconstruct the ground surface beneath it"
            )
        )
        self.promptEdit.setMaximumHeight(80)
        obstacle_layout.addWidget(self.promptEdit)

        preset_row = QtWidgets.QHBoxLayout()
        preset_row.addWidget(QtWidgets.QLabel(self.tr("Presets:")))
        for key, text in self._PRESETS:
            btn = QtWidgets.QPushButton(self.tr(key.title()))
            btn.setProperty("_preset_text", text)
            btn.clicked.connect(self._on_preset_clicked)
            preset_row.addWidget(btn)
        preset_row.addStretch()
        obstacle_layout.addLayout(preset_row)

        obstacle_pad_row = QtWidgets.QHBoxLayout()
        obstacle_pad_row.addWidget(QtWidgets.QLabel(self.tr("Padding around box:")))
        self.paddingSpin = QtWidgets.QSpinBox()
        self.paddingSpin.setRange(0, 100)
        self.paddingSpin.setValue(8)
        self.paddingSpin.setSuffix(self.tr(" m"))
        obstacle_pad_row.addWidget(self.paddingSpin)
        obstacle_pad_row.addStretch()
        obstacle_layout.addLayout(obstacle_pad_row)

        obstacle_action_row = QtWidgets.QHBoxLayout()
        self.drawBoxButton = QtWidgets.QPushButton(self.tr("Draw removal box"))
        self.clearBoxesButton = QtWidgets.QPushButton(self.tr("Clear all"))
        self.clearBoxesButton.setEnabled(False)
        obstacle_action_row.addWidget(self.drawBoxButton)
        obstacle_action_row.addWidget(self.clearBoxesButton)
        obstacle_action_row.addStretch()
        obstacle_layout.addLayout(obstacle_action_row)

        self.boxTable = QtWidgets.QTableWidget(0, 6)
        self.boxTable.setHorizontalHeaderLabels(
            [
                self.tr("#"),
                self.tr("Merge"),
                self.tr("Area"),
                self.tr("Size"),
                self.tr("Status"),
                self.tr(""),
            ]
        )
        self.boxTable.horizontalHeader().setStretchLastSection(True)
        self.boxTable.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.boxTable.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        obstacle_layout.addWidget(self.boxTable)

        self.boxCountLabel = QtWidgets.QLabel(self.tr("0 obstacles selected"))
        self.boxCountLabel.setStyleSheet("color: palette(mid);")
        obstacle_layout.addWidget(self.boxCountLabel)

        self.applyButton = QtWidgets.QPushButton(self.tr("Remove obstacles"))
        obstacle_layout.addWidget(self.applyButton)
        obstacle_layout.addStretch()
        self.tabs.addTab(obstacle_tab, self.tr("Obstacle removal"))

        # --- Solar panels tab ---
        solar_tab = QtWidgets.QWidget()
        solar_layout = QtWidgets.QVBoxLayout(solar_tab)
        solar_intro = QtWidgets.QLabel(
            self.tr(
                "Draw a polygon over a roof (left-click vertices, right-click to finish). "
                "Solar panels are added automatically. No prompt needed."
            )
        )
        solar_intro.setWordWrap(True)
        solar_layout.addWidget(solar_intro)

        solar_pad_row = QtWidgets.QHBoxLayout()
        solar_pad_row.addWidget(QtWidgets.QLabel(self.tr("Padding around area:")))
        self.solarPaddingSpin = QtWidgets.QSpinBox()
        self.solarPaddingSpin.setRange(0, 100)
        self.solarPaddingSpin.setValue(8)
        self.solarPaddingSpin.setSuffix(self.tr(" m"))
        solar_pad_row.addWidget(self.solarPaddingSpin)
        solar_pad_row.addStretch()
        solar_layout.addLayout(solar_pad_row)

        solar_action_row = QtWidgets.QHBoxLayout()
        self.drawSolarButton = QtWidgets.QPushButton(self.tr("Draw roof polygon"))
        self.clearSolarButton = QtWidgets.QPushButton(self.tr("Clear all"))
        self.clearSolarButton.setEnabled(False)
        solar_action_row.addWidget(self.drawSolarButton)
        solar_action_row.addWidget(self.clearSolarButton)
        solar_action_row.addStretch()
        solar_layout.addLayout(solar_action_row)

        self.solarTable = QtWidgets.QTableWidget(0, 6)
        self.solarTable.setHorizontalHeaderLabels(
            [
                self.tr("#"),
                self.tr("Merge"),
                self.tr("Area"),
                self.tr("Size"),
                self.tr("Status"),
                self.tr(""),
            ]
        )
        self.solarTable.horizontalHeader().setStretchLastSection(True)
        self.solarTable.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.solarTable.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        solar_layout.addWidget(self.solarTable)

        self.solarCountLabel = QtWidgets.QLabel(self.tr("0 roof area(s) selected"))
        self.solarCountLabel.setStyleSheet("color: palette(mid);")
        solar_layout.addWidget(self.solarCountLabel)

        self.applySolarButton = QtWidgets.QPushButton(self.tr("Add solar panels"))
        solar_layout.addWidget(self.applySolarButton)
        solar_layout.addStretch()
        self.tabs.addTab(solar_tab, self.tr("Solar panels"))

        layout.addWidget(self.tabs)

        self.progressBar = QtWidgets.QProgressBar()
        self.progressBar.setVisible(False)
        layout.addWidget(self.progressBar)

        self.statusLabel = QtWidgets.QLabel()
        self.statusLabel.setWordWrap(True)
        self.statusLabel.setStyleSheet("color: palette(mid);")
        layout.addWidget(self.statusLabel)

        merge_row = QtWidgets.QHBoxLayout()
        self.exportMergedButton = QtWidgets.QPushButton(
            self.tr("Export merged imagery…")
        )
        self.exportMergedButton.setEnabled(False)
        self.exportMergedButton.setToolTip(
            self.tr(
                "Save a new GeoTIFF with checked edits merged into a copy of the source."
            )
        )
        merge_row.addWidget(self.exportMergedButton)
        merge_row.addStretch()
        layout.addLayout(merge_row)

        footer = QtWidgets.QHBoxLayout()
        self.backButton = QtWidgets.QPushButton(self.tr("Back to Pack"))
        self.cancelButton = QtWidgets.QPushButton(self.tr("Cancel"))
        self.cancelButton.setVisible(False)
        footer.addWidget(self.backButton)
        footer.addStretch()
        footer.addWidget(self.cancelButton)
        layout.addLayout(footer)

        self.refreshLayerButton.clicked.connect(self.populate_layers)
        self.layerCombo.currentIndexChanged.connect(self._sync_apply_state)
        self.promptEdit.textChanged.connect(self._sync_apply_state)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.drawBoxButton.clicked.connect(self.start_box_drawing)
        self.clearBoxesButton.clicked.connect(self.clear_all_boxes)
        self.applyButton.clicked.connect(self.apply_removals)
        self.drawSolarButton.clicked.connect(self.start_solar_drawing)
        self.clearSolarButton.clicked.connect(self.clear_all_solar)
        self.applySolarButton.clicked.connect(self.apply_solar_panels)
        self.exportMergedButton.clicked.connect(self.export_merged_imagery)
        self.cancelButton.clicked.connect(self.cancel_worker)
        self.backButton.clicked.connect(self._on_back)
        self.licensePanel.validateLicenseButton.clicked.connect(
            lambda: QtCore.QTimer.singleShot(100, self._sync_apply_state)
        )

        project = QgsProject.instance()
        self._project = project
        project.layersAdded.connect(self.populate_layers)
        project.layersRemoved.connect(self.populate_layers)

    def _on_tab_changed(self, index: int):
        self._active_edit_kind = "solar" if index == 1 else "obstacle"
        self._sync_apply_state()

    def _on_preset_clicked(self):
        btn = self.sender()
        if btn is None:
            return
        text = btn.property("_preset_text")
        if text:
            self.promptEdit.setPlainText(str(text))

    def populate_layers(self):
        self.layerCombo.blockSignals(True)
        prev_id = self.get_layer_id()
        self.layerCombo.clear()
        for layer in list_project_raster_layers():
            if not layer_is_usable(layer):
                continue
            self.layerCombo.addItem(layer.name(), layer.id())
        if prev_id:
            idx = self.layerCombo.findData(prev_id)
            if idx >= 0:
                self.layerCombo.setCurrentIndex(idx)
        self.layerCombo.blockSignals(False)
        self._sync_apply_state()

    def get_layer_id(self) -> str | None:
        idx = self.layerCombo.currentIndex()
        if idx < 0:
            return None
        return self.layerCombo.itemData(idx, ItemDataUserRole)

    def get_raster_layer(self) -> QgsRasterLayer | None:
        lid = self.get_layer_id()
        if not lid:
            return None
        layer = QgsProject.instance().mapLayer(lid)
        if isinstance(layer, QgsRasterLayer) and layer_is_usable(layer):
            return layer
        return None

    def _is_busy(self) -> bool:
        if self._worker is not None and self._worker.isRunning():
            return True
        if self._merge_worker is not None and self._merge_worker.isRunning():
            return True
        return False

    def _has_merge_selection(self) -> bool:
        for box in self._boxes:
            if box.get("merge_checked") and box.get("patch_path"):
                return True
        for area in self._solar_areas:
            if area.get("merge_checked") and area.get("patch_path"):
                return True
        return False

    def _sync_apply_state(self):
        layer = self.get_raster_layer()
        georef_ok = bool(layer and layer.crs().isValid())
        has_prompt = bool(self.promptEdit.toPlainText().strip())
        has_boxes = len(self._boxes) > 0
        has_solar = len(self._solar_areas) > 0
        acc = trial_shared()
        lic_ok = acc.can_run_large_area()
        busy = self._is_busy()
        self.applyButton.setEnabled(
            georef_ok and has_prompt and has_boxes and lic_ok and not busy
        )
        self.applySolarButton.setEnabled(
            georef_ok and has_solar and lic_ok and not busy
        )
        self.drawBoxButton.setEnabled(georef_ok and not busy)
        self.clearBoxesButton.setEnabled(has_boxes and not busy)
        self.drawSolarButton.setEnabled(georef_ok and not busy)
        self.clearSolarButton.setEnabled(has_solar and not busy)
        self.exportMergedButton.setEnabled(
            georef_ok and self._has_merge_selection() and not busy
        )
        if layer and not georef_ok:
            self.statusLabel.setText(
                self.tr("Selected layer must be georeferenced.")
            )
        elif not lic_ok:
            self.statusLabel.setText(
                self.tr("Validate a licence key to use Obstacle Removal.")
            )
        elif not busy:
            self.statusLabel.clear()

    def start_box_drawing(self):
        layer = self.get_raster_layer()
        if not layer:
            return
        canvas = self.iface.mapCanvas()
        if not canvas:
            return
        self._active_edit_kind = "obstacle"
        self._previous_map_tool = canvas.mapTool()
        if self._map_tool is None:
            self._map_tool = ObstacleBoxMapTool(canvas)
            self._map_tool.box_finished.connect(self._on_box_drawn)
        canvas.setMapTool(self._map_tool)
        self.hide()

    def start_solar_drawing(self):
        layer = self.get_raster_layer()
        if not layer:
            return
        canvas = self.iface.mapCanvas()
        if not canvas:
            return
        self._active_edit_kind = "solar"
        self._previous_map_tool = canvas.mapTool()
        if self._poly_map_tool is None:
            self._poly_map_tool = ObstaclePolygonMapTool(canvas)
            self._poly_map_tool.polygon_finished.connect(self._on_solar_polygon_drawn)
        canvas.setMapTool(self._poly_map_tool)
        self.hide()

    def _on_box_drawn(self, rect: QgsRectangle):
        layer = self.get_raster_layer()
        if not layer:
            self.show()
            return
        crs = layer.crs()
        rect_tuple = rect_tuple_from_qgs_rect(rect)
        h_m, w_m = rectangle_size_meters(rect, crs)
        box_id = str(uuid.uuid4())
        self._boxes.append(
            {
                "id": box_id,
                "rect_tuple": rect_tuple,
                "label": self.tr("Obstacle {n}").format(n=len(self._boxes) + 1),
                "size": (w_m, h_m),
                "patch_path": None,
                "layer_id": None,
                "edit_status": None,
                "merge_checked": False,
            }
        )
        self._add_overlay_rect(rect)
        self._boxes[-1]["overlay_band"] = self._overlay_bands[-1]
        self._refresh_box_table()
        if self.iface and self.iface.mapCanvas():
            self.iface.mapCanvas().setMapTool(self._map_tool)
        self.show()
        self.raise_()
        self.activateWindow()
        self._sync_apply_state()

    def _on_solar_polygon_drawn(self, geometry: QgsGeometry):
        layer = self.get_raster_layer()
        if not layer or not geometry or geometry.isEmpty():
            self.show()
            return
        mask = geometry_to_mask_geojson(geometry)
        if not mask:
            self.show()
            return
        bbox = geometry.boundingBox()
        bbox_tuple = rect_tuple_from_qgs_rect(bbox)
        crs = layer.crs()
        h_m, w_m = rectangle_size_meters(bbox, crs)
        area_id = str(uuid.uuid4())
        self._solar_areas.append(
            {
                "id": area_id,
                "bbox_tuple": bbox_tuple,
                "mask_geojson": mask,
                "label": self.tr("Solar {n}").format(n=len(self._solar_areas) + 1),
                "size": (w_m, h_m),
                "patch_path": None,
                "layer_id": None,
                "edit_status": None,
                "merge_checked": False,
            }
        )
        band = self._add_overlay_geometry(geometry, solar=True)
        self._solar_areas[-1]["overlay_band"] = band
        self._refresh_solar_table()
        if self.iface and self.iface.mapCanvas() and self._poly_map_tool:
            self.iface.mapCanvas().setMapTool(self._poly_map_tool)
        self.show()
        self.raise_()
        self.activateWindow()
        self._sync_apply_state()

    def _add_overlay_rect(self, rect: QgsRectangle):
        self._add_overlay_geometry(QgsGeometry.fromRect(rect), solar=False)

    def _add_overlay_geometry(self, geometry: QgsGeometry, *, solar: bool):
        canvas = self.iface.mapCanvas()
        if not canvas:
            return
        band = QgsRubberBand(canvas, QgsWkbTypes.PolygonGeometry)
        if solar:
            band.setColor(self._SOLAR_LINE)
            band.setFillColor(self._SOLAR_FILL)
        else:
            band.setColor(self._BOX_LINE)
            band.setFillColor(self._BOX_FILL)
        band.setWidth(2)
        band.setToGeometry(geometry, None)
        self._overlay_bands.append(band)
        return band

    def _box_status_text(self, box: dict) -> str:
        status = box.get("edit_status")
        if status == "ok":
            return self.tr("Edited")
        if status == "failed":
            return self.tr("Failed")
        return self.tr("Pending")

    def _on_merge_check_changed(self, index: int, state: int):
        if index < 0 or index >= len(self._boxes):
            return
        self._boxes[index]["merge_checked"] = state == CheckStateChecked
        self._sync_apply_state()

    def _refresh_box_table(self):
        self.boxTable.setRowCount(0)
        for i, box in enumerate(self._boxes):
            row = self.boxTable.rowCount()
            self.boxTable.insertRow(row)
            self.boxTable.setItem(row, 0, QtWidgets.QTableWidgetItem(str(i + 1)))

            merge_wrap = QtWidgets.QWidget()
            merge_layout = QtWidgets.QHBoxLayout(merge_wrap)
            merge_layout.setContentsMargins(6, 0, 0, 0)
            merge_layout.setAlignment(AlignCenter)
            merge_cb = QtWidgets.QCheckBox()
            has_patch = bool(box.get("patch_path"))
            merge_cb.setEnabled(has_patch)
            merge_cb.setChecked(bool(box.get("merge_checked")) and has_patch)
            merge_cb.stateChanged.connect(
                lambda state, idx=i: self._on_merge_check_changed(idx, state)
            )
            merge_layout.addWidget(merge_cb)
            self.boxTable.setCellWidget(row, 1, merge_wrap)

            self.boxTable.setItem(row, 2, QtWidgets.QTableWidgetItem(box["label"]))
            w_m, h_m = box["size"]
            size_txt = f"{w_m:.0f}m × {h_m:.0f}m"
            self.boxTable.setItem(row, 3, QtWidgets.QTableWidgetItem(size_txt))
            self.boxTable.setItem(
                row, 4, QtWidgets.QTableWidgetItem(self._box_status_text(box))
            )

            actions = QtWidgets.QWidget()
            actions_layout = QtWidgets.QHBoxLayout(actions)
            actions_layout.setContentsMargins(0, 0, 0, 0)
            zoom_btn = QtWidgets.QPushButton("⌖")
            zoom_btn.setToolTip(self.tr("Zoom to"))
            zoom_btn.setFixedWidth(28)
            zoom_btn.clicked.connect(lambda _=False, idx=i: self._zoom_to_box(idx))
            rm_btn = QtWidgets.QPushButton("✕")
            rm_btn.setToolTip(self.tr("Remove"))
            rm_btn.setFixedWidth(28)
            rm_btn.clicked.connect(lambda _=False, idx=i: self._remove_box(idx))
            actions_layout.addWidget(zoom_btn)
            actions_layout.addWidget(rm_btn)
            self.boxTable.setCellWidget(row, 5, actions)
        n = len(self._boxes)
        edited = sum(1 for b in self._boxes if b.get("edit_status") == "ok")
        if edited:
            self.boxCountLabel.setText(
                self.tr("{n} obstacle(s), {e} edited").format(n=n, e=edited)
            )
        else:
            self.boxCountLabel.setText(
                self.tr("{n} obstacle(s) selected").format(n=n)
            )

    def _on_solar_merge_check_changed(self, index: int, state: int):
        if index < 0 or index >= len(self._solar_areas):
            return
        self._solar_areas[index]["merge_checked"] = state == CheckStateChecked
        self._sync_apply_state()

    def _refresh_solar_table(self):
        self.solarTable.setRowCount(0)
        for i, area in enumerate(self._solar_areas):
            row = self.solarTable.rowCount()
            self.solarTable.insertRow(row)
            self.solarTable.setItem(row, 0, QtWidgets.QTableWidgetItem(str(i + 1)))

            merge_wrap = QtWidgets.QWidget()
            merge_layout = QtWidgets.QHBoxLayout(merge_wrap)
            merge_layout.setContentsMargins(6, 0, 0, 0)
            merge_layout.setAlignment(AlignCenter)
            merge_cb = QtWidgets.QCheckBox()
            has_patch = bool(area.get("patch_path"))
            merge_cb.setEnabled(has_patch)
            merge_cb.setChecked(bool(area.get("merge_checked")) and has_patch)
            merge_cb.stateChanged.connect(
                lambda state, idx=i: self._on_solar_merge_check_changed(idx, state)
            )
            merge_layout.addWidget(merge_cb)
            self.solarTable.setCellWidget(row, 1, merge_wrap)

            self.solarTable.setItem(row, 2, QtWidgets.QTableWidgetItem(area["label"]))
            w_m, h_m = area["size"]
            self.solarTable.setItem(
                row, 3, QtWidgets.QTableWidgetItem(f"{w_m:.0f}m × {h_m:.0f}m")
            )
            self.solarTable.setItem(
                row, 4, QtWidgets.QTableWidgetItem(self._box_status_text(area))
            )

            actions = QtWidgets.QWidget()
            actions_layout = QtWidgets.QHBoxLayout(actions)
            actions_layout.setContentsMargins(0, 0, 0, 0)
            zoom_btn = QtWidgets.QPushButton("⌖")
            zoom_btn.setFixedWidth(28)
            zoom_btn.clicked.connect(lambda _=False, idx=i: self._zoom_to_solar(idx))
            rm_btn = QtWidgets.QPushButton("✕")
            rm_btn.setFixedWidth(28)
            rm_btn.clicked.connect(lambda _=False, idx=i: self._remove_solar(idx))
            actions_layout.addWidget(zoom_btn)
            actions_layout.addWidget(rm_btn)
            self.solarTable.setCellWidget(row, 5, actions)

        n = len(self._solar_areas)
        edited = sum(1 for a in self._solar_areas if a.get("edit_status") == "ok")
        if edited:
            self.solarCountLabel.setText(
                self.tr("{n} roof area(s), {e} edited").format(n=n, e=edited)
            )
        else:
            self.solarCountLabel.setText(
                self.tr("{n} roof area(s) selected").format(n=n)
            )

    def _remove_solar(self, index: int):
        if index < 0 or index >= len(self._solar_areas):
            return
        area = self._solar_areas[index]
        layer_id = area.get("layer_id")
        if layer_id and not self._shutting_down:
            try:
                QgsProject.instance().removeMapLayer(layer_id)
            except RuntimeError:
                pass
        self._solar_areas.pop(index)
        band = area.get("overlay_band")
        if band:
            self._remove_overlay_band(band)
        for i, area in enumerate(self._solar_areas):
            area["label"] = self.tr("Solar {n}").format(n=i + 1)
        self._refresh_solar_table()
        self._sync_apply_state()

    def _zoom_to_box(self, index: int):
        if index < 0 or index >= len(self._boxes):
            return
        t = self._boxes[index]["rect_tuple"]
        rect = QgsRectangle(t[0], t[1], t[2], t[3])
        canvas = self.iface.mapCanvas()
        if canvas:
            canvas.setExtent(rect)
            canvas.refresh()

    def _zoom_to_solar(self, index: int):
        if index < 0 or index >= len(self._solar_areas):
            return
        t = self._solar_areas[index]["bbox_tuple"]
        rect = QgsRectangle(t[0], t[1], t[2], t[3])
        canvas = self.iface.mapCanvas()
        if canvas:
            canvas.setExtent(rect)
            canvas.refresh()

    def _remove_box(self, index: int):
        if index < 0 or index >= len(self._boxes):
            return
        box = self._boxes[index]
        layer_id = box.get("layer_id")
        if layer_id and not self._shutting_down:
            try:
                QgsProject.instance().removeMapLayer(layer_id)
            except RuntimeError:
                pass
        self._boxes.pop(index)
        band = box.get("overlay_band")
        if band:
            self._remove_overlay_band(band)
        for i, box in enumerate(self._boxes):
            box["label"] = self.tr("Obstacle {n}").format(n=i + 1)
        self._refresh_box_table()
        self._sync_apply_state()

    def _remove_overlay_band(self, band):
        if band in self._overlay_bands:
            self._overlay_bands.remove(band)
        try:
            canvas = self.iface.mapCanvas() if self.iface else None
            if canvas:
                canvas.scene().removeItem(band)
        except RuntimeError:
            pass

    def _clear_overlay_bands(self):
        bands = list(self._overlay_bands)
        self._overlay_bands.clear()
        for band in bands:
            try:
                canvas = self.iface.mapCanvas() if self.iface else None
                if canvas:
                    canvas.scene().removeItem(band)
            except RuntimeError:
                pass
        try:
            canvas = self.iface.mapCanvas() if self.iface else None
            if canvas:
                canvas.refresh()
        except RuntimeError:
            pass

    def clear_all_boxes(self):
        self._clear_items(self._boxes, remove_layers=True)
        self._refresh_box_table()
        self._sync_apply_state()

    def clear_all_solar(self):
        self._clear_items(self._solar_areas, remove_layers=True)
        self._refresh_solar_table()
        self._sync_apply_state()

    def _clear_items(self, items: list[dict], *, remove_layers: bool):
        if remove_layers and not self._shutting_down:
            for item in items:
                layer_id = item.get("layer_id")
                if layer_id:
                    try:
                        QgsProject.instance().removeMapLayer(layer_id)
                    except RuntimeError:
                        pass
        items.clear()
        self._clear_overlay_bands()

    def _build_auth_client(self):
        from ..core.vec_inference_client import VecInferenceClient
        from ..core.api_config import INFERENCE_BASE_URL

        acc = trial_shared()
        acc.load_from_settings()
        acc.sync_pending_usages()

        license_key = (
            (acc.license_key or "").strip()
            or self.licensePanel.licenseKeyLineEdit.text().strip()
        )
        jwt = (acc.jwt_token or "").strip()

        if license_key or jwt:
            client = VecInferenceClient(
                INFERENCE_BASE_URL,
                jwt_token=jwt or None,
                license_key=license_key or None,
            )
            if license_key:
                token, expiry = client.validate_license_key(license_key)
                if token:
                    acc.set_paid_license(token, license_key, expiry)
                    client.jwt_token = token
                    return client, ""
            elif jwt:
                return client, ""
            acc.clear_paid_license()

        if not acc.can_run_large_area():
            return None, self.tr(
                "Validate a licence key or activate trial to use Obstacle Removal."
            )

        if not acc.get_billable_idempotency_key():
            acc.get_billable_idempotency_key()

        ok, msg, usage, receipt = acc.consume_for_large_area_infer()
        if not ok:
            return None, msg or self.tr("Trial usage denied.")

        acc.clear_billable_idempotency_key()
        if usage:
            acc.apply_server_state(usage)
        self.licensePanel.refresh_trial_state(force=True)

        client = VecInferenceClient(
            INFERENCE_BASE_URL,
            jwt_token=None,
            license_key=None,
            trial_receipt=receipt,
            trial_install_key=acc.install_key,
            trial_server_id=acc.trial_id,
        )
        return client, ""

    def apply_removals(self):
        layer = self.get_raster_layer()
        prompt = build_edit_prompt(
            self.promptEdit.toPlainText().strip(),
            OBSTACLE_REMOVAL_SYSTEM_PROMPT,
        )
        if not layer or not prompt or not self._boxes:
            return
        jobs = []
        for i, box in enumerate(self._boxes):
            jobs.append(
                {
                    "crop_rect": box["rect_tuple"],
                    "mask_geojson": rect_tuple_to_mask_geojson(box["rect_tuple"]),
                    "prompt": prompt,
                    "layer_suffix": f"edit_{i + 1}",
                }
            )
        self._start_edit_worker(
            layer, jobs, float(self.paddingSpin.value()), "obstacle", self._boxes
        )

    def apply_solar_panels(self):
        layer = self.get_raster_layer()
        if not layer or not self._solar_areas:
            return
        jobs = []
        for i, area in enumerate(self._solar_areas):
            jobs.append(
                {
                    "crop_rect": area["bbox_tuple"],
                    "mask_geojson": area["mask_geojson"],
                    "prompt": SOLAR_PANELS_PROMPT,
                    "layer_suffix": f"solar_{i + 1}",
                }
            )
        self._start_edit_worker(
            layer,
            jobs,
            float(self.solarPaddingSpin.value()),
            "solar",
            self._solar_areas,
        )

    def _start_edit_worker(
        self, layer, jobs, padding_m, kind: str, items: list[dict]
    ):
        auth_client, err = self._build_auth_client()
        if not auth_client:
            QtWidgets.QMessageBox.warning(self, self.tr("Licence"), err)
            return

        self._source_layer_id = layer.id()
        self._active_worker_kind = kind
        for item in items:
            item["patch_path"] = None
            item["layer_id"] = None
            item["edit_status"] = None
            item["merge_checked"] = False
        if kind == "solar":
            self._refresh_solar_table()
        else:
            self._refresh_box_table()

        self._worker = ObstacleRemovalWorker(
            layer,
            jobs,
            padding_m,
            auth_client,
            layer.name(),
        )
        self._worker.progress.connect(self._on_worker_progress)
        self._worker.box_done.connect(self._on_box_done)
        self._worker.box_failed.connect(self._on_box_failed)
        self._worker.finished_all.connect(self._on_worker_finished)
        self._worker.error.connect(self._on_worker_error)

        self.progressBar.setVisible(True)
        self.progressBar.setRange(0, len(jobs))
        self.progressBar.setValue(0)
        self.applyButton.setEnabled(False)
        self.applySolarButton.setEnabled(False)
        self.drawBoxButton.setEnabled(False)
        self.drawSolarButton.setEnabled(False)
        self.cancelButton.setVisible(True)
        self._worker.start()

    def cancel_worker(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self.statusLabel.setText(self.tr("Cancelling…"))

    def _on_worker_progress(self, current: int, total: int, message: str):
        self.progressBar.setMaximum(total)
        self.progressBar.setValue(current - 1)
        self.statusLabel.setText(message)

    def _active_items(self) -> list[dict]:
        if self._active_worker_kind == "solar":
            return self._solar_areas
        return self._boxes

    def _on_box_done(self, index: int, path: str, layer_name: str):
        layer = QgsRasterLayer(path, layer_name)
        if not layer.isValid():
            self._on_box_failed(index, self.tr("Edited GeoTIFF could not be loaded."))
            return
        box_idx = index - 1
        items = self._active_items()
        if 0 <= box_idx < len(items):
            items[box_idx]["patch_path"] = path
            items[box_idx]["layer_id"] = layer.id()
            items[box_idx]["edit_status"] = "ok"
            items[box_idx]["merge_checked"] = True
        group_name = (
            self._solar_group_name
            if self._active_worker_kind == "solar"
            else self._edit_group_name
        )
        root = QgsProject.instance().layerTreeRoot()
        group = root.findGroup(group_name)
        if group is None:
            group = root.insertGroup(0, group_name)
        QgsProject.instance().addMapLayer(layer, False)
        group.addLayer(layer)
        if self._active_worker_kind == "solar":
            self._refresh_solar_table()
        else:
            self._refresh_box_table()
        self._sync_apply_state()
        try:
            self.iface.mapCanvas().refresh()
        except RuntimeError:
            pass

    def _on_box_failed(self, index: int, message: str):
        box_idx = index - 1
        items = self._active_items()
        if 0 <= box_idx < len(items):
            items[box_idx]["edit_status"] = "failed"
            items[box_idx]["patch_path"] = None
            items[box_idx]["layer_id"] = None
            items[box_idx]["merge_checked"] = False
            if self._active_worker_kind == "solar":
                self._refresh_solar_table()
            else:
                self._refresh_box_table()
            self._sync_apply_state()
        if "401" in message or "Session expired" in message or "licence" in message.lower():
            trial_shared().load_from_settings()
            self.licensePanel.refresh_trial_state(force=True)
            self._sync_apply_state()
            QtWidgets.QMessageBox.warning(
                self,
                self.tr("Licence"),
                message,
            )
        self.iface.messageBar().pushMessage(
            "FieldWatch",
            self.tr("Obstacle {i} failed: {m}").format(i=index, m=message[:200]),
            level=1,
            duration=8,
        )

    def _on_worker_finished(self, ok_count: int, total: int):
        self.progressBar.setValue(total)
        self.progressBar.setVisible(False)
        self.cancelButton.setVisible(False)
        self._worker = None
        self._clear_overlay_bands()
        self._cleanup_map_tool()
        self._sync_apply_state()
        if ok_count == total:
            self.statusLabel.setText(
                self.tr("{n} patch(es) applied. Toggle layers to compare.").format(
                    n=ok_count
                )
            )
            msg = (
                self.tr("{n} solar panel edit(s) complete.").format(n=ok_count)
                if self._active_worker_kind == "solar"
                else self.tr("{n} obstacle removal(s) complete.").format(n=ok_count)
            )
            self.iface.messageBar().pushMessage(
                "FieldWatch",
                msg,
                level=0,
                duration=6,
            )
        elif ok_count > 0:
            self.statusLabel.setText(
                self.tr("{ok} of {total} succeeded — see Log Messages.").format(
                    ok=ok_count, total=total
                )
            )
        else:
            self.statusLabel.setText(self.tr("No edits succeeded."))

    def _on_worker_error(self, message: str):
        self.progressBar.setVisible(False)
        self.cancelButton.setVisible(False)
        self._worker = None
        self._sync_apply_state()
        QtWidgets.QMessageBox.warning(self, self.tr("Obstacle Removal"), message)

    def _get_source_layer_for_merge(self) -> QgsRasterLayer | None:
        if self._source_layer_id:
            layer = QgsProject.instance().mapLayer(self._source_layer_id)
            if isinstance(layer, QgsRasterLayer) and layer_is_usable(layer):
                return layer
        return self.get_raster_layer()

    def export_merged_imagery(self):
        from ..core.obstacle_merge import (
            default_merged_output_path,
            layer_source_path,
        )

        layer = self._get_source_layer_for_merge()
        if not layer:
            QtWidgets.QMessageBox.warning(
                self,
                self.tr("Export merged imagery"),
                self.tr("Select a valid source raster layer."),
            )
            return

        patches = []
        for box in self._boxes:
            if not (box.get("merge_checked") and box.get("patch_path")):
                continue
            patches.append(
                {
                    "path": box["patch_path"],
                    "inner_rect": box["rect_tuple"],
                }
            )
        for area in self._solar_areas:
            if not (area.get("merge_checked") and area.get("patch_path")):
                continue
            patches.append(
                {
                    "path": area["patch_path"],
                    "inner_rect": area["bbox_tuple"],
                }
            )
        if not patches:
            QtWidgets.QMessageBox.warning(
                self,
                self.tr("Export merged imagery"),
                self.tr("Check at least one edited obstacle to merge."),
            )
            return

        source_path = layer_source_path(layer)
        if not source_path:
            QtWidgets.QMessageBox.warning(
                self,
                self.tr("Export merged imagery"),
                self.tr("Could not read the source raster file path."),
            )
            return

        default_path = default_merged_output_path(source_path)
        output_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            self.tr("Save merged imagery"),
            default_path,
            self.tr("GeoTIFF (*.tif *.tiff);;All files (*.*)"),
        )
        if not output_path:
            return
        if not output_path.lower().endswith((".tif", ".tiff")):
            output_path += ".tif"

        layer_name = f"{layer.name()}_merged"
        self._merge_worker = ObstacleMergeWorker(
            source_path,
            patches,
            output_path,
            layer_name,
        )
        self._merge_worker.progress.connect(self._on_merge_progress)
        self._merge_worker.finished_ok.connect(self._on_merge_finished)
        self._merge_worker.error.connect(self._on_merge_error)

        self.progressBar.setVisible(True)
        self.progressBar.setRange(0, len(patches) + 1)
        self.progressBar.setValue(0)
        self.statusLabel.setText(self.tr("Preparing merge…"))
        self.applyButton.setEnabled(False)
        self.applySolarButton.setEnabled(False)
        self._merge_worker.start()

    def _on_merge_progress(self, current: int, total: int, message: str):
        self.progressBar.setMaximum(max(total, 1))
        self.progressBar.setValue(current)
        self.statusLabel.setText(message)

    def _on_merge_finished(self, path: str, layer_name: str):
        self.progressBar.setVisible(False)
        self._merge_worker = None
        merged = QgsRasterLayer(path, layer_name)
        if not merged.isValid():
            self._sync_apply_state()
            QtWidgets.QMessageBox.warning(
                self,
                self.tr("Export merged imagery"),
                self.tr("Merged GeoTIFF was written but could not be loaded."),
            )
            return
        QgsProject.instance().addMapLayer(merged)
        self.iface.mapCanvas().refresh()
        self._sync_apply_state()
        self.statusLabel.setText(
            self.tr("Merged imagery saved and added to the map.")
        )
        self.iface.messageBar().pushMessage(
            "FieldWatch",
            self.tr("Merged imagery saved: {name}").format(name=layer_name),
            level=0,
            duration=8,
        )

    def _on_merge_error(self, message: str):
        self.progressBar.setVisible(False)
        self._merge_worker = None
        self._sync_apply_state()
        QtWidgets.QMessageBox.warning(
            self,
            self.tr("Export merged imagery"),
            message,
        )

    def _on_back(self):
        """Return to the FieldWatch Pack landing page."""
        if self._is_busy():
            reply = QtWidgets.QMessageBox.question(
                self,
                self.tr("Obstacle Removal"),
                self.tr("A task is in progress. Cancel and go back?"),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if reply != QtWidgets.QMessageBox.Yes:
                return
            if self._worker and self._worker.isRunning():
                self._worker.cancel()
                self._worker.wait(3000)
            if self._merge_worker and self._merge_worker.isRunning():
                self._merge_worker.wait(3000)
        self._cleanup_map_tool()
        self.hide()
        if self._pack_dialog is not None:
            from .vec_plugin_dialog import _PAGE_LANDING

            self._pack_dialog._show_page(_PAGE_LANDING)
            self._pack_dialog._sync_ui_from_trial_access()
            self._pack_dialog._update_trial_quota_label()
            self._pack_dialog.show()
            self._pack_dialog.raise_()
            self._pack_dialog.activateWindow()

    def shutdown(self):
        """Safe teardown when plugin unloads or dialog closes."""
        if self._shutting_down:
            return
        self._shutting_down = True

        project = getattr(self, "_project", None)
        if project is not None:
            try:
                project.layersAdded.disconnect(self.populate_layers)
            except (TypeError, RuntimeError):
                pass
            try:
                project.layersRemoved.disconnect(self.populate_layers)
            except (TypeError, RuntimeError):
                pass

        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(2000)
        if self._merge_worker and self._merge_worker.isRunning():
            self._merge_worker.wait(2000)

        self._disconnect_worker(self._worker)
        self._disconnect_worker(self._merge_worker)
        self._worker = None
        self._merge_worker = None

        self._clear_overlay_bands()
        self._remove_shortcut_filter()
        self._cleanup_map_tool(restore_tool=False)

        if self._map_tool:
            try:
                self._map_tool.box_finished.disconnect()
            except (TypeError, RuntimeError):
                pass
        if self._poly_map_tool:
            try:
                self._poly_map_tool.polygon_finished.disconnect()
            except (TypeError, RuntimeError):
                pass

    @staticmethod
    def _disconnect_worker(worker):
        if worker is None:
            return
        for signal in (
            worker.progress,
            worker.box_done,
            worker.box_failed,
            worker.finished_all,
            worker.error,
            getattr(worker, "finished_ok", None),
        ):
            if signal is None:
                continue
            try:
                signal.disconnect()
            except (TypeError, RuntimeError):
                pass

    def closeEvent(self, event):
        if self._is_busy() and not self._shutting_down:
            reply = QtWidgets.QMessageBox.question(
                self,
                self.tr("AI Imagery Edit"),
                self.tr("A task is in progress. Cancel and close?"),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if reply != QtWidgets.QMessageBox.Yes:
                event.ignore()
                return
        self.shutdown()
        event.accept()

    def _cleanup_map_tool(self, restore_tool: bool = True):
        for tool in (self._map_tool, self._poly_map_tool):
            if tool:
                tool.release_space_pan()
        if self._map_tool:
            try:
                self._map_tool.deactivate()
            except RuntimeError:
                pass
        if self._poly_map_tool:
            try:
                self._poly_map_tool.deactivate()
            except RuntimeError:
                pass
        if not restore_tool or self._shutting_down:
            self._previous_map_tool = None
            return
        canvas = self.iface.mapCanvas() if self.iface else None
        if canvas and self._previous_map_tool:
            try:
                canvas.setMapTool(self._previous_map_tool)
            except RuntimeError:
                pass
        self._previous_map_tool = None
