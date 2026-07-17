# -*- coding: utf-8 -*-
"""Qt5 / Qt6 enum compatibility for QGIS 3 (PyQt5) and QGIS 4 (PyQt6)."""

from qgis.PyQt.QtCore import Qt, QEvent
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLineEdit,
    QMessageBox,
    QSizePolicy,
)
from qgis.core import (
    Qgis,
    QgsBlockingNetworkRequest,
    QgsRasterFileWriter,
    QgsWkbTypes,
)
from qgis.gui import QgsMapToolCapture


def _qt(enum_group: str, name: str):
    """
    Resolve a Qt enum on Qt6 (nested enum class) or Qt5 (flat Qt namespace).

    Example: _qt("CursorShape", "PointingHandCursor")
    """
    try:
        group = getattr(Qt, enum_group)
        return getattr(group, name)
    except AttributeError:
        return getattr(Qt, name)


def _enum(cls, enum_group: str, name: str):
    """Resolve a QGIS / Qt class enum on Qt6 (nested) or Qt5 (flat)."""
    try:
        group = getattr(cls, enum_group)
        return getattr(group, name)
    except AttributeError:
        return getattr(cls, name)


# Mouse
LeftButton = _qt("MouseButton", "LeftButton")
RightButton = _qt("MouseButton", "RightButton")
MiddleButton = _qt("MouseButton", "MiddleButton")

# Cursors
PointingHandCursor = _qt("CursorShape", "PointingHandCursor")
CrossCursor = _qt("CursorShape", "CrossCursor")
OpenHandCursor = _qt("CursorShape", "OpenHandCursor")
WaitCursor = _qt("CursorShape", "WaitCursor")

# Keys / events
KeySpace = _qt("Key", "Key_Space")
EventShortcutOverride = _enum(QEvent, "Type", "ShortcutOverride")
EventKeyPress = _enum(QEvent, "Type", "KeyPress")

# Text interaction
TextBrowserInteraction = _qt("TextInteractionFlag", "TextBrowserInteraction")

# Layout / widgets
RightToLeft = _qt("LayoutDirection", "RightToLeft")
ScrollBarAsNeeded = _qt("ScrollBarPolicy", "ScrollBarAsNeeded")
AlignLeft = _qt("AlignmentFlag", "AlignLeft")
AlignTop = _qt("AlignmentFlag", "AlignTop")
PlainText = _qt("TextFormat", "PlainText")
ItemDataUserRole = _qt("ItemDataRole", "UserRole")
CheckStateChecked = _qt("CheckState", "Checked")
AlignCenter = _qt("AlignmentFlag", "AlignCenter")

# Global colors (prefer QColor in new code; these are for QgsRubberBand.setColor)
red = _qt("GlobalColor", "red")
blue = _qt("GlobalColor", "blue")

# QGIS log levels
QgisInfo = _enum(Qgis, "MessageLevel", "Info")
QgisWarning = _enum(Qgis, "MessageLevel", "Warning")
QgisCritical = _enum(Qgis, "MessageLevel", "Critical")
QgisSuccess = _enum(Qgis, "MessageLevel", "Success")

# Geometry types
PolygonGeometry = _enum(QgsWkbTypes, "GeometryType", "PolygonGeometry")
LineGeometry = _enum(QgsWkbTypes, "GeometryType", "LineGeometry")
PointGeometry = _enum(QgsWkbTypes, "GeometryType", "PointGeometry")

# Map tools / IO
CapturePolygon = _enum(QgsMapToolCapture, "CaptureMode", "CapturePolygon")
RasterWriterNoError = _enum(QgsRasterFileWriter, "WriterError", "NoError")
NetworkNoError = _enum(QgsBlockingNetworkRequest, "ErrorCode", "NoError")


def _widget_enum(cls, enum_group: str, name: str):
    try:
        group = getattr(cls, enum_group)
        return getattr(group, name)
    except AttributeError:
        return getattr(cls, name)


FrameHLine = _widget_enum(QFrame, "Shape", "HLine")
FrameNoFrame = _widget_enum(QFrame, "Shape", "NoFrame")
FrameSunken = _widget_enum(QFrame, "Shadow", "Sunken")

LineEditPassword = _widget_enum(QLineEdit, "EchoMode", "Password")
SizePolicyExpanding = _widget_enum(QSizePolicy, "Policy", "Expanding")
SizePolicyFixed = _widget_enum(QSizePolicy, "Policy", "Fixed")

MsgBoxWarning = _widget_enum(QMessageBox, "Icon", "Warning")
MsgBoxInformation = _widget_enum(QMessageBox, "Icon", "Information")
MsgBoxCritical = _widget_enum(QMessageBox, "Icon", "Critical")
MsgBoxQuestion = _widget_enum(QMessageBox, "Icon", "Question")

MsgBoxOk = _widget_enum(QMessageBox, "StandardButton", "Ok")
MsgBoxYes = _widget_enum(QMessageBox, "StandardButton", "Yes")
MsgBoxNo = _widget_enum(QMessageBox, "StandardButton", "No")

DialogAccepted = _widget_enum(QDialog, "DialogCode", "Accepted")
DialogRejected = _widget_enum(QDialog, "DialogCode", "Rejected")

TableSelectRows = _widget_enum(QAbstractItemView, "SelectionBehavior", "SelectRows")
TableNoEditTriggers = _widget_enum(QAbstractItemView, "EditTrigger", "NoEditTriggers")

DialogButtonBoxCancel = _widget_enum(QDialogButtonBox, "StandardButton", "Cancel")
DialogButtonBoxSave = _widget_enum(QDialogButtonBox, "StandardButton", "Save")


def dialog_exec(dialog) -> int:
    """QDialog.exec() on Qt6, exec_() on Qt5."""
    fn = getattr(dialog, "exec", None) or getattr(dialog, "exec_", None)
    if fn is None:
        raise AttributeError("Dialog has no exec/exec_ method")
    return fn()


def event_loop_exec(loop) -> int:
    """QEventLoop.exec() on Qt6, exec_() on Qt5."""
    fn = getattr(loop, "exec", None) or getattr(loop, "exec_", None)
    if fn is None:
        raise AttributeError("QEventLoop has no exec/exec_ method")
    return fn()


def safe_disconnect(signal, slot=None) -> None:
    """Disconnect a Qt signal; ignore if not connected."""
    try:
        if slot is None:
            signal.disconnect()
        else:
            signal.disconnect(slot)
    except (RuntimeError, TypeError):
        return
