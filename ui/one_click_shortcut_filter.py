# -*- coding: utf-8 -*-
"""Space-key pan toggle for one-click segmentation (works when dock has focus)."""

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


class OneClickShortcutFilter(QObject):
    """Space toggles pan mode on the main window during segmentation."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._controller = controller

    def eventFilter(self, _obj, event):
        event_type = event.type()
        controller = self._controller
        map_tool = controller.map_tool

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
