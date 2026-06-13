# -*- coding: utf-8 -*-
"""Space / middle-mouse pan helpers for custom QgsMapTool subclasses."""

from qgis.core import QgsPointXY
from qgis.PyQt.QtGui import QCursor

from ..core.qt_compat import OpenHandCursor


class SpacePanMixin:
    """Mixin: Space toggles grab-pan; middle button drag; wheel pan while grabbed."""

    def _init_space_pan(self) -> None:
        self._space_panning = False
        self._middle_panning = False
        self._pan_last_point = None
        self._pan_tool_active = False

    def _space_pan_on_activate(self) -> None:
        self._pan_tool_active = True
        self._space_panning = False
        self._middle_panning = False
        self._pan_last_point = None

    def _space_pan_on_deactivate(self) -> None:
        self._pan_tool_active = False
        self._space_panning = False
        self._middle_panning = False
        self._pan_last_point = None

    def isActive(self) -> bool:
        return self._pan_tool_active

    def space_pan_active(self) -> bool:
        return self._space_panning

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

    def _space_pan_restore_cursor(self) -> None:
        pass

    def _middle_pan_press(self, event) -> bool:
        from ..core.qt_compat import MiddleButton

        if event.button() != MiddleButton:
            return False
        self._middle_panning = True
        self._pan_last_point = event.pos()
        self._set_pan_cursor()
        return True

    def _middle_pan_release(self, event) -> bool:
        from ..core.qt_compat import MiddleButton

        if event.button() != MiddleButton or not self._middle_panning:
            return False
        self._middle_panning = False
        self._pan_last_point = None
        if not self._space_panning:
            self._space_pan_restore_cursor()
        return True

    def _space_pan_move(self, event) -> bool:
        if not self._is_panning():
            return False
        self._apply_pan_delta(event.pos())
        return True

    def toggle_space_pan(self) -> None:
        if self._space_panning:
            self._space_panning = False
            self._pan_last_point = None
            if not self._middle_panning:
                self._space_pan_restore_cursor()
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
