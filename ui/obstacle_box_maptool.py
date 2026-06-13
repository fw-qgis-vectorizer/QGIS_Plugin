# -*- coding: utf-8 -*-
"""Rectangle drag map tool for Obstacle Removal boxes."""

from __future__ import annotations

from qgis.core import QgsGeometry, QgsPointXY, QgsRectangle, QgsWkbTypes
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtGui import QColor, QCursor

from ..core.qt_compat import CrossCursor, LeftButton
from .space_pan_mixin import SpacePanMixin


class ObstacleBoxMapTool(SpacePanMixin, QgsMapTool):
    """Drag a rectangle on the map; emits normalized QgsRectangle on release."""

    box_finished = pyqtSignal(QgsRectangle)

    _FILL = QColor(255, 120, 40, 70)
    _LINE = QColor(255, 120, 40, 220)

    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self._start: QgsPointXY | None = None
        self._band: QgsRubberBand | None = None
        self._init_space_pan()

    def activate(self):
        super().activate()
        self._space_pan_on_activate()
        self.canvas.setCursor(QCursor(CrossCursor))

    def deactivate(self):
        if self._band:
            try:
                self.canvas.scene().removeItem(self._band)
            except RuntimeError:
                pass
            self._band = None
        self._start = None
        self._space_pan_on_deactivate()
        super().deactivate()

    def _space_pan_restore_cursor(self) -> None:
        if self._pan_tool_active:
            self.canvas.setCursor(QCursor(CrossCursor))

    def canvasPressEvent(self, event):
        if self._space_panning:
            return
        if self._middle_pan_press(event):
            return
        if event.button() != LeftButton:
            return
        self._start = self.toMapCoordinates(event.pos())
        if self._band is None:
            self._band = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
            self._band.setColor(self._LINE)
            self._band.setFillColor(self._FILL)
            self._band.setWidth(2)

    def canvasMoveEvent(self, event):
        if self._space_pan_move(event):
            return
        if self._start is None or self._band is None:
            return
        end = self.toMapCoordinates(event.pos())
        rect = QgsRectangle(self._start, end)
        rect.normalize()
        self._band.setToGeometry(QgsGeometry.fromRect(rect), None)

    def canvasReleaseEvent(self, event):
        if self._middle_pan_release(event):
            return
        if event.button() != LeftButton or self._start is None:
            return
        end = self.toMapCoordinates(event.pos())
        rect = QgsRectangle(self._start, end)
        rect.normalize()
        self._start = None
        if self._band:
            self._band.reset(QgsWkbTypes.PolygonGeometry)
        if rect.width() > 0 and rect.height() > 0:
            self.box_finished.emit(rect)
