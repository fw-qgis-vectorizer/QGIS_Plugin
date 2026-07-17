# -*- coding: utf-8 -*-
"""Space-key pan toggle for map tools (works when dock/dialog has focus)."""

from qgis.PyQt.QtCore import QObject
from qgis.PyQt.QtWidgets import QDoubleSpinBox, QLineEdit, QPlainTextEdit, QSpinBox, QTextEdit

from ..core.qt_compat import EventKeyPress, EventShortcutOverride, KeySpace


class SpacePanShortcutFilter(QObject):
    """Space toggles pan mode on the main window while a map tool is active."""

    def __init__(self, map_tool_getter, parent=None):
        super().__init__(parent)
        self._map_tool_getter = map_tool_getter

    def _current_map_tool(self):
        if callable(self._map_tool_getter):
            return self._map_tool_getter()
        return getattr(self._map_tool_getter, "map_tool", None)

    def eventFilter(self, _obj, event):
        event_type = event.type()
        map_tool = self._current_map_tool()

        if event_type in (EventShortcutOverride, EventKeyPress):
            if event.key() == KeySpace and map_tool and not event.isAutoRepeat():
                if event_type == EventShortcutOverride:
                    if map_tool.isActive():
                        event.accept()
                        return True
                elif event_type == EventKeyPress and map_tool.isActive():
                    map_tool.toggle_space_pan()
                    return True

        if event_type != EventKeyPress:
            return False
        if not map_tool or not map_tool.isActive():
            return False

        focused = None
        try:
            from qgis.PyQt.QtWidgets import QApplication

            app = QApplication.instance()
            if app:
                focused = app.focusWidget()
        except (ImportError, RuntimeError, AttributeError):
            None
        if isinstance(focused, (QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox)):
            return False

        return False


OneClickShortcutFilter = SpacePanShortcutFilter
