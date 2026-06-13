# -*- coding: utf-8 -*-
"""Polygon capture map tool for solar panel AI edit regions."""

from __future__ import annotations

from qgis.core import QgsGeometry, QgsPointXY, QgsWkbTypes
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtGui import QColor, QCursor

from ..core.qt_compat import CrossCursor, LeftButton, RightButton
from .space_pan_mixin import SpacePanMixin


class ObstaclePolygonMapTool(SpacePanMixin, QgsMapTool):
    """Left-click vertices; right-click finishes polygon."""

    polygon_finished = pyqtSignal(object)

    _FILL = QColor(60, 180, 255, 70)
    _LINE = QColor(60, 180, 255, 220)

    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self._points: list[QgsPointXY] = []
        self._band: QgsRubberBand | None = None
        self._init_space_pan()

    def activate(self):
        super().activate()
        self._space_pan_on_activate()
        self.canvas.setCursor(QCursor(CrossCursor))

    def _space_pan_restore_cursor(self) -> None:
        if self._pan_tool_active:
            self.canvas.setCursor(QCursor(CrossCursor))

    def canvasPressEvent(self, event):
        if self._space_panning:
            return
        self._middle_pan_press(event)

    def canvasReleaseEvent(self, event):
        if self._middle_pan_release(event):
            return
        if self._space_panning:
            return
        if event.button() == RightButton:
            if len(self._points) >= 3:
                ring = [QgsPointXY(p) for p in self._points] + [QgsPointXY(self._points[0])]
                self.polygon_finished.emit(QgsGeometry.fromPolygonXY([ring]))
            self._reset_drawing()
            return
        if event.button() != LeftButton:
            return
        self._points.append(QgsPointXY(self.toMapCoordinates(event.pos())))
        if self._band is None:
            self._band = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
            self._band.setColor(self._LINE)
            self._band.setFillColor(self._FILL)
            self._band.setWidth(2)
        self._update_band()

    def canvasMoveEvent(self, event):
        self._space_pan_move(event)

    def _update_band(self):
        if not self._band or len(self._points) < 2:
            return
        self._band.reset(QgsWkbTypes.PolygonGeometry)
        for pt in self._points:
            self._band.addPoint(pt, False)
        self._band.addPoint(self._points[0], True)

    def _reset_drawing(self):
        self._points.clear()
        if self._band:
            self._band.reset(QgsWkbTypes.PolygonGeometry)

    def deactivate(self):
        self._reset_drawing()
        if self._band:
            try:
                self._band.reset(QgsWkbTypes.LineGeometry)
                self._band.setVisible(False)
            except RuntimeError:
                pass
            try:
                self.canvas.scene().removeItem(self._band)
            except RuntimeError:
                pass
            self._band = None
        self._space_pan_on_deactivate()
        super().deactivate()
