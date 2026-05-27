# -*- coding: utf-8 -*-
"""Map tool: left click adds point, right click deletes mask at cursor."""

from qgis.core import QgsPointXY
from qgis.gui import QgsMapCanvas, QgsMapTool, QgsVertexMarker
from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtGui import QColor, QCursor

from ..core.qt_compat import (
    CrossCursor,
    LeftButton,
    MiddleButton,
    OpenHandCursor,
    RightButton,
)


class OneClickMapTool(QgsMapTool):
    """Point prompts for one-click segmentation.

    Pan while the tool is active:
    - Space toggles grab mode; move mouse or use trackpad to pan; Space again to release
    - Middle mouse button drag
    """

    positive_click = pyqtSignal(QgsPointXY)
    mask_delete_click = pyqtSignal(QgsPointXY)
    tool_deactivated = pyqtSignal()

    POSITIVE_COLOR = QColor(0, 200, 0)
    MARKER_SIZE = 10
    MARKER_PEN_WIDTH = 2

    def __init__(self, canvas: QgsMapCanvas):
        super().__init__(canvas)
        self.canvas = canvas
        self._active = False
        self._markers: list[QgsVertexMarker] = []
        self._space_panning = False
        self._middle_panning = False
        self._pan_last_point = None

    def activate(self):
        super().activate()
        self._active = True
        self._space_panning = False
        self._middle_panning = False
        self._pan_last_point = None
        self.canvas.setCursor(QCursor(CrossCursor))

    def deactivate(self):
        super().deactivate()
        self._active = False
        self._space_panning = False
        self._middle_panning = False
        self._pan_last_point = None
        self.tool_deactivated.emit()

    def space_pan_active(self) -> bool:
        return self._space_panning

    def add_marker(self, point: QgsPointXY, is_positive: bool) -> QgsVertexMarker:
        marker = QgsVertexMarker(self.canvas)
        marker.setCenter(point)
        marker.setIconSize(self.MARKER_SIZE)
        marker.setPenWidth(self.MARKER_PEN_WIDTH)
        marker.setIconType(QgsVertexMarker.IconType.ICON_CIRCLE)
        marker.setColor(self.POSITIVE_COLOR)
        marker.setFillColor(QColor(0, 200, 0, 100))
        self._markers.append(marker)
        return marker

    def remove_last_marker(self) -> bool:
        if not self._markers:
            return False
        marker = self._markers.pop()
        try:
            scene = self.canvas.scene()
            if scene is not None:
                scene.removeItem(marker)
        except RuntimeError:
            pass
        self.canvas.refresh()
        return True

    def clear_markers(self):
        for marker in self._markers:
            try:
                scene = self.canvas.scene()
                if scene is not None:
                    scene.removeItem(marker)
            except RuntimeError:
                pass
        self._markers.clear()
        self.canvas.refresh()

    def _is_panning(self) -> bool:
        return self._space_panning or self._middle_panning

    def _apply_pan_delta(self, current_pos) -> None:
        if self._pan_last_point is None:
            self._pan_last_point = current_pos
            return
        start_map = self.toMapCoordinates(self._pan_last_point)
        end_map = self.toMapCoordinates(current_pos)
        center = self.canvas.center()
        self.canvas.setCenter(
            QgsPointXY(
                center.x() + (start_map.x() - end_map.x()),
                center.y() + (start_map.y() - end_map.y()),
            )
        )
        self.canvas.refresh()
        self._pan_last_point = current_pos

    def _pan_by_wheel(self, delta) -> None:
        dx = delta.x()
        dy = delta.y()
        if dx == 0 and dy == 0:
            return
        extent = self.canvas.extent()
        w = max(self.canvas.width(), 1)
        h = max(self.canvas.height(), 1)
        center = self.canvas.center()
        # Trackpad / wheel deltas are in 1/8-degree steps; scale to map units.
        scale = 1.0 / 120.0
        self.canvas.setCenter(
            QgsPointXY(
                center.x() - (dx * scale) * extent.width() / w,
                center.y() + (dy * scale) * extent.height() / h,
            )
        )
        self.canvas.refresh()

    def _set_pan_cursor(self) -> None:
        self.canvas.setCursor(QCursor(OpenHandCursor))

    def _restore_segmentation_cursor(self) -> None:
        if self._active:
            self.canvas.setCursor(QCursor(CrossCursor))

    def canvasPressEvent(self, event):
        if not self._active:
            return
        if self._space_panning:
            return

        if event.button() == MiddleButton:
            self._middle_panning = True
            self._pan_last_point = event.pos()
            self._set_pan_cursor()
            return

        point = self.toMapCoordinates(event.pos())
        if event.button() == LeftButton:
            self.add_marker(point, is_positive=True)
            self.positive_click.emit(point)
        elif event.button() == RightButton:
            self.mask_delete_click.emit(point)

    def canvasReleaseEvent(self, event):
        if event.button() == MiddleButton and self._middle_panning:
            self._middle_panning = False
            self._pan_last_point = None
            if not self._space_panning:
                self._restore_segmentation_cursor()

    def canvasMoveEvent(self, event):
        if not self._is_panning():
            return
        self._apply_pan_delta(event.pos())

    def toggle_space_pan(self) -> None:
        """Space once: grab map for panning; Space again: release."""
        if self._space_panning:
            self._space_panning = False
            self._pan_last_point = None
            if not self._middle_panning:
                self._restore_segmentation_cursor()
            return
        self._space_panning = True
        self._pan_last_point = self.canvas.mapFromGlobal(QCursor.pos())
        self._set_pan_cursor()

    def release_space_pan(self) -> None:
        if self._space_panning:
            self.toggle_space_pan()

    def wheelEvent(self, event):
        if self._space_panning:
            self._pan_by_wheel(event.angleDelta())
            event.accept()
            return
        event.ignore()

    def gestureEvent(self, event):
        if self._space_panning:
            event.accept()
            return True
        event.ignore()
        return False

    def keyPressEvent(self, event):
        event.ignore()

    def isActive(self) -> bool:
        return self._active
