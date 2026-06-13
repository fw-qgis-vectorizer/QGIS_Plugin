# -*- coding: utf-8 -*-
"""Map tool: left click adds point, right click deletes mask at cursor."""

from qgis.core import QgsPointXY
from qgis.gui import QgsMapCanvas, QgsMapTool, QgsVertexMarker
from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtGui import QColor, QCursor

from ..core.qt_compat import CrossCursor, LeftButton, RightButton
from .space_pan_mixin import SpacePanMixin


class OneClickMapTool(SpacePanMixin, QgsMapTool):
    """Point prompts for one-click segmentation.

    Pan while the tool is active:
    - Space toggles grab mode; move mouse or use trackpad to pan; Space again to release
    - Middle mouse button drag
    """

    positive_click = pyqtSignal(QgsPointXY)
    mask_delete_click = pyqtSignal(QgsPointXY)
    tool_deactivated = pyqtSignal()

    MARKER_COLOR = QColor(0, 200, 0)
    MARKER_SIZE = 10
    MARKER_PEN_WIDTH = 2

    def __init__(self, canvas: QgsMapCanvas):
        super().__init__(canvas)
        self.canvas = canvas
        self._markers: list[QgsVertexMarker] = []
        self._init_space_pan()

    def activate(self):
        super().activate()
        self._space_pan_on_activate()
        self.canvas.setCursor(QCursor(CrossCursor))

    def deactivate(self):
        super().deactivate()
        self._space_pan_on_deactivate()
        self.tool_deactivated.emit()

    def _space_pan_restore_cursor(self) -> None:
        if self._pan_tool_active:
            self.canvas.setCursor(QCursor(CrossCursor))

    def add_marker(self, point: QgsPointXY, is_positive: bool = True) -> QgsVertexMarker:
        marker = QgsVertexMarker(self.canvas)
        marker.setCenter(point)
        marker.setIconSize(self.MARKER_SIZE)
        marker.setPenWidth(self.MARKER_PEN_WIDTH)
        marker.setIconType(QgsVertexMarker.IconType.ICON_CIRCLE)
        marker.setColor(self.MARKER_COLOR)
        marker.setFillColor(
            QColor(self.MARKER_COLOR.red(), self.MARKER_COLOR.green(), self.MARKER_COLOR.blue(), 100)
        )
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

    def canvasPressEvent(self, event):
        if not self._pan_tool_active:
            return
        if self._space_panning:
            return
        if self._middle_pan_press(event):
            return

        point = self.toMapCoordinates(event.pos())
        if event.button() == LeftButton:
            self.add_marker(point, is_positive=True)
            self.positive_click.emit(point)
        elif event.button() == RightButton:
            self.mask_delete_click.emit(point)

    def canvasReleaseEvent(self, event):
        self._middle_pan_release(event)

    def canvasMoveEvent(self, event):
        self._space_pan_move(event)

    def keyPressEvent(self, event):
        event.ignore()
