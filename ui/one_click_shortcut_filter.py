# -*- coding: utf-8 -*-
"""Space-key pan toggle for map tools (works when dock/dialog has focus)."""

from qgis.PyQt.QtCore import QEvent, QObject, Qt
from qgis.PyQt.QtWidgets import QDoubleSpinBox, QLineEdit, QPlainTextEdit, QSpinBox, QTextEdit

try:
    _KEY_SPACE = Qt.Key.Key_Space
    _SHORTCUT_OVERRIDE = QEvent.Type.ShortcutOverride
    _KEY_PRESS = QEvent.Type.KeyPress
except AttributeError:
    _KEY_SPACE = Qt.Key_Space
    _SHORTCUT_OVERRIDE = QEvent.ShortcutOverride
    _KEY_PRESS = QEvent.KeyPress


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

        if event_type in (_SHORTCUT_OVERRIDE, _KEY_PRESS):
            if event.key() == _KEY_SPACE and map_tool and not event.isAutoRepeat():
                if event_type == _SHORTCUT_OVERRIDE:
                    if map_tool.isActive():
                        event.accept()
                        return True
                elif event_type == _KEY_PRESS and map_tool.isActive():
                    map_tool.toggle_space_pan()
                    return True

        if event_type != _KEY_PRESS:
            return False
        if not map_tool or not map_tool.isActive():
            return False

        focused = None
        try:
            from qgis.PyQt.QtWidgets import QApplication

            app = QApplication.instance()
            if app:
                focused = app.focusWidget()
        except Exception:
            pass
        if isinstance(focused, (QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox)):
            return False

        return False


OneClickShortcutFilter = SpacePanShortcutFilter
