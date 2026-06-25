# -*- coding: utf-8 -*-
"""Modal: email + terms acceptance before first use."""

from __future__ import annotations

from qgis.PyQt import QtWidgets, QtCore
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices

from ..core import trial_helpers
from ..core.onboarding_helpers import onboarding_ok_tooltip
from ..core.trial_helpers import normalize_trial_email, post_trial_email
from ..core.qt_compat import PointingHandCursor

FIELDWATCH_TERMS_URL = "https://usefieldwatch.com/terms"


class OnboardingDialog(QtWidgets.QDialog):
    """Collect email and terms acceptance; POST once per QGIS profile."""

    def __init__(self, parent=None, install_key=None, inference_base_url=None, trial_id=None):
        super().__init__(parent)
        self._install_key = install_key
        self._inference_base_url = inference_base_url
        self._trial_id = trial_id
        self.setWindowTitle(self.tr("Welcome to FieldWatch"))
        self.setModal(True)
        self.resize(460, 300)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(14)

        title = QtWidgets.QLabel(self.tr("Welcome to FieldWatch"))
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(title)

        intro = QtWidgets.QLabel(
            self.tr("Please enter your email and accept our terms to get started.")
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QtWidgets.QFormLayout()
        self._email_edit = QtWidgets.QLineEdit()
        self._email_edit.setPlaceholderText(self.tr("you@company.com"))
        stored = trial_helpers.get_stored_user_email()
        if stored:
            self._email_edit.setText(stored)
        form.addRow(self.tr("Email:"), self._email_edit)
        layout.addLayout(form)

        terms_row = QtWidgets.QHBoxLayout()
        terms_row.setSpacing(6)
        terms_row.setContentsMargins(0, 0, 0, 0)
        self._terms_check = QtWidgets.QCheckBox(self.tr("I accept the"))
        self._terms_link = QtWidgets.QLabel(
            '<a href="{url}">{text}</a>'.format(
                url=FIELDWATCH_TERMS_URL,
                text=self.tr("terms and conditions"),
            )
        )
        self._terms_link.setOpenExternalLinks(True)
        self._terms_link.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
        self._terms_link.setCursor(PointingHandCursor)
        terms_row.addWidget(self._terms_check)
        terms_row.addWidget(self._terms_link)
        terms_row.addStretch()
        layout.addLayout(terms_row)

        layout.addStretch()

        footer = QtWidgets.QHBoxLayout()
        self._cancel_button = QtWidgets.QPushButton(self.tr("Cancel"))
        self._ok_button = QtWidgets.QPushButton(self.tr("OK"))
        self._ok_button.setDefault(True)
        self._ok_button.setEnabled(False)
        footer.addStretch()
        footer.addWidget(self._cancel_button)
        footer.addWidget(self._ok_button)
        layout.addLayout(footer)

        self._email_edit.textChanged.connect(self._sync_ok_state)
        self._terms_check.toggled.connect(self._sync_ok_state)
        self._cancel_button.clicked.connect(self.reject)
        self._ok_button.clicked.connect(self._on_ok)
        self._sync_ok_state()

    def _sync_ok_state(self, *_args):
        terms_ok = self._terms_check.isChecked()
        email_ok = trial_helpers.is_valid_email(self._email_edit.text())
        enabled = email_ok and terms_ok
        self._ok_button.setEnabled(enabled)
        if enabled:
            self._ok_button.setToolTip("")
        else:
            self._ok_button.setToolTip(
                onboarding_ok_tooltip(self._email_edit.text(), terms_ok)
            )

    def _on_ok(self):
        email = normalize_trial_email(self._email_edit.text())
        if not trial_helpers.is_valid_email(email):
            QtWidgets.QMessageBox.warning(
                self,
                self.tr("Email required"),
                self.tr("Please enter a valid email address."),
            )
            return
        if not self._terms_check.isChecked():
            QtWidgets.QMessageBox.warning(
                self,
                self.tr("Terms required"),
                self.tr("Please accept the terms and conditions to continue."),
            )
            return

        self._ok_button.setEnabled(False)
        self._cancel_button.setEnabled(False)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            post_trial_email(
                self._inference_base_url,
                self._install_key,
                email,
                trial_id=self._trial_id,
                ensure_trial_id=False,
            )
            trial_helpers.save_onboarding(email)
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self,
                self.tr("Registration failed"),
                str(e)[:2000],
            )
            self._sync_ok_state()
            self._cancel_button.setEnabled(True)
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        self.accept()
