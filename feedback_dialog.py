# -*- coding: utf-8 -*-
"""Modal feedback dialog: large text area, 1500 char cap, POST /qgis/feedback."""

from qgis.PyQt import QtWidgets
from qgis.core import QgsMessageLog, Qgis

from .api_config import INFERENCE_BASE_URL
from . import feedback_helpers


class FeedbackDialog(QtWidgets.QDialog):
    """Collect free-text feedback and submit to the inference API."""

    def __init__(self, parent=None, install_key=None, trial_id=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Send feedback"))
        self.setMinimumSize(520, 420)
        self.resize(560, 440)
        self._install_key = install_key
        self._trial_id = trial_id

        layout = QtWidgets.QVBoxLayout(self)

        intro = QtWidgets.QLabel(
            self.tr("Your message is sent to FieldWatch (max {0} characters).").format(
                feedback_helpers.FEEDBACK_MESSAGE_MAX_LEN
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._text = QtWidgets.QTextEdit()
        self._text.setPlaceholderText(
            self.tr("Describe your experience, issues, or suggestions…")
        )
        self._text.setMinimumHeight(220)
        layout.addWidget(self._text)

        self._counter = QtWidgets.QLabel("0 / {}".format(feedback_helpers.FEEDBACK_MESSAGE_MAX_LEN))
        layout.addWidget(self._counter)

        self._text.textChanged.connect(self._on_text_changed)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Cancel | QtWidgets.QDialogButtonBox.Save
        )
        buttons.button(QtWidgets.QDialogButtonBox.Save).setText(self.tr("Send"))
        buttons.accepted.connect(self._submit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_text_changed(self):
        doc = self._text.document().toPlainText()
        if len(doc) > feedback_helpers.FEEDBACK_MESSAGE_MAX_LEN:
            trimmed = doc[: feedback_helpers.FEEDBACK_MESSAGE_MAX_LEN]
            cursor = self._text.textCursor()
            pos = min(cursor.position(), len(trimmed))
            self._text.blockSignals(True)
            self._text.setPlainText(trimmed)
            cursor.setPosition(pos)
            self._text.setTextCursor(cursor)
            self._text.blockSignals(False)
            doc = trimmed
        self._counter.setText(
            "{} / {}".format(len(doc), feedback_helpers.FEEDBACK_MESSAGE_MAX_LEN)
        )

    def _submit(self):
        msg = self._text.toPlainText().strip()
        if not msg:
            QtWidgets.QMessageBox.warning(
                self,
                self.tr("Feedback"),
                self.tr("Please enter a message before sending."),
            )
            return
        try:
            result = feedback_helpers.post_feedback(
                INFERENCE_BASE_URL,
                msg,
                install_key=self._install_key,
                trial_id=self._trial_id,
            )
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self,
                self.tr("Feedback failed"),
                str(e)[:800],
            )
            QgsMessageLog.logMessage(str(e), "VEC Plugin", Qgis.Warning)
            return

        submitted = result.get("submitted_at") or result.get("created_at") or ""
        fid = result.get("id")
        parts = [self.tr("Your feedback was received.")]
        if fid is not None:
            parts.append(self.tr("Reference id: {0}").format(fid))
        if submitted:
            parts.append(self.tr("Submitted at (server, UTC): {0}").format(submitted))

        QtWidgets.QMessageBox.information(
            self,
            self.tr("Thank you"),
            "\n\n".join(parts),
        )
        self.accept()
