# -*- coding: utf-8 -*-
"""Modal: FieldWatch Sign In / Sign Up before first use."""

from __future__ import annotations

from qgis.PyQt import QtWidgets

from ..core import trial_helpers
from ..core.account_login import login_form_tooltip
from ..core.account_register import (
    DEFAULT_REASON_FOR_USE,
    MIN_PASSWORD_LENGTH,
    registration_form_tooltip,
)
from ..core.api_config import INFERENCE_BASE_URL
from ..core.qt_compat import (
    FrameNoFrame,
    LineEditPassword,
    PointingHandCursor,
    TextBrowserInteraction,
    WaitCursor,
)

FIELDWATCH_TERMS_URL = "https://usefieldwatch.com/terms"

_MODE_SIGN_UP = "sign_up"
_MODE_SIGN_IN = "sign_in"


class OnboardingDialog(QtWidgets.QDialog):
    """Collect credentials: create a FieldWatch account or sign in to an existing one."""

    def __init__(self, parent=None, install_key=None, inference_base_url=None, trial_id=None):
        super().__init__(parent)
        self._install_key = install_key
        self._inference_base_url = inference_base_url or INFERENCE_BASE_URL
        self._trial_id = trial_id
        self._worker = None
        self._register_values = None
        self._busy = False
        self._mode = _MODE_SIGN_UP
        self.setWindowTitle(self.tr("Welcome to FieldWatch"))
        self.setModal(True)
        self.resize(520, 560)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(12)

        self._title = QtWidgets.QLabel(self.tr("Welcome to FieldWatch"))
        self._title.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(self._title)

        self._intro = QtWidgets.QLabel("")
        self._intro.setWordWrap(True)
        layout.addWidget(self._intro)

        mode_row = QtWidgets.QHBoxLayout()
        mode_row.setSpacing(8)
        self._sign_up_tab = QtWidgets.QPushButton(self.tr("Sign up"))
        self._sign_in_tab = QtWidgets.QPushButton(self.tr("Sign in"))
        for btn in (self._sign_up_tab, self._sign_in_tab):
            btn.setCheckable(True)
            btn.setCursor(PointingHandCursor)
            btn.setFlat(True)
            mode_row.addWidget(btn)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(FrameNoFrame)
        form_host = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(form_host)
        form.setContentsMargins(0, 0, 0, 0)

        self._first_name_edit = QtWidgets.QLineEdit()
        self._first_name_edit.setPlaceholderText(self.tr("Ada"))
        stored_first = trial_helpers.get_stored_user_first_name()
        if stored_first:
            self._first_name_edit.setText(stored_first)
        form.addRow(self.tr("First name:"), self._first_name_edit)
        self._first_name_label = form.labelForField(self._first_name_edit)

        self._last_name_edit = QtWidgets.QLineEdit()
        self._last_name_edit.setPlaceholderText(self.tr("Lovelace"))
        stored_last = trial_helpers.get_stored_user_last_name()
        if stored_last:
            self._last_name_edit.setText(stored_last)
        form.addRow(self.tr("Last name:"), self._last_name_edit)
        self._last_name_label = form.labelForField(self._last_name_edit)

        self._email_edit = QtWidgets.QLineEdit()
        self._email_edit.setPlaceholderText(self.tr("you@company.com"))
        stored = trial_helpers.get_stored_user_email()
        if stored:
            self._email_edit.setText(stored)
        form.addRow(self.tr("Email:"), self._email_edit)

        self._password_edit = QtWidgets.QLineEdit()
        self._password_edit.setEchoMode(LineEditPassword)
        self._password_edit.setPlaceholderText(
            self.tr("At least {0} characters").format(MIN_PASSWORD_LENGTH)
        )
        form.addRow(self.tr("Password:"), self._password_edit)

        self._company_edit = QtWidgets.QLineEdit()
        self._company_edit.setPlaceholderText(self.tr("Your organisation"))
        form.addRow(self.tr("Company:"), self._company_edit)
        self._company_label = form.labelForField(self._company_edit)

        self._reason_edit = QtWidgets.QPlainTextEdit()
        self._reason_edit.setPlaceholderText(self.tr(DEFAULT_REASON_FOR_USE))
        self._reason_edit.setFixedHeight(72)
        form.addRow(self.tr("Reason for use:"), self._reason_edit)
        self._reason_label = form.labelForField(self._reason_edit)

        scroll.setWidget(form_host)
        layout.addWidget(scroll)

        self._marketing_check = QtWidgets.QCheckBox(
            self.tr("I'd like to receive product updates and GIS resources by email.")
        )
        layout.addWidget(self._marketing_check)

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
        self._terms_link.setTextInteractionFlags(TextBrowserInteraction)
        self._terms_link.setCursor(PointingHandCursor)
        terms_row.addWidget(self._terms_check)
        terms_row.addWidget(self._terms_link)
        terms_row.addStretch()
        self._terms_row_widget = QtWidgets.QWidget()
        self._terms_row_widget.setLayout(terms_row)
        layout.addWidget(self._terms_row_widget)

        footer = QtWidgets.QHBoxLayout()
        self._cancel_button = QtWidgets.QPushButton(self.tr("Cancel"))
        self._ok_button = QtWidgets.QPushButton(self.tr("Create account"))
        self._ok_button.setDefault(True)
        self._ok_button.setEnabled(False)
        footer.addStretch()
        footer.addWidget(self._cancel_button)
        footer.addWidget(self._ok_button)
        layout.addLayout(footer)

        for widget in (
            self._first_name_edit,
            self._last_name_edit,
            self._email_edit,
            self._password_edit,
            self._company_edit,
        ):
            widget.textChanged.connect(self._sync_ok_state)
        self._reason_edit.textChanged.connect(self._sync_ok_state)
        self._terms_check.toggled.connect(self._sync_ok_state)
        self._cancel_button.clicked.connect(self.reject)
        self._ok_button.clicked.connect(self._on_ok)
        self._sign_up_tab.clicked.connect(lambda: self._set_mode(_MODE_SIGN_UP))
        self._sign_in_tab.clicked.connect(lambda: self._set_mode(_MODE_SIGN_IN))

        self._set_mode(_MODE_SIGN_UP)

    def _set_mode(self, mode: str):
        if self._busy:
            return
        self._mode = mode if mode in (_MODE_SIGN_UP, _MODE_SIGN_IN) else _MODE_SIGN_UP
        is_sign_up = self._mode == _MODE_SIGN_UP

        self._sign_up_tab.setChecked(is_sign_up)
        self._sign_in_tab.setChecked(not is_sign_up)

        for widget in (
            self._first_name_edit,
            self._first_name_label,
            self._last_name_edit,
            self._last_name_label,
            self._company_edit,
            self._company_label,
            self._reason_edit,
            self._reason_label,
            self._marketing_check,
            self._terms_row_widget,
        ):
            if widget is not None:
                widget.setVisible(is_sign_up)

        if is_sign_up:
            self._title.setText(self.tr("Welcome to FieldWatch"))
            self._intro.setText(
                self.tr(
                    "Create your FieldWatch account to get started with your free plugin trial."
                )
            )
            self._password_edit.setPlaceholderText(
                self.tr("At least {0} characters").format(MIN_PASSWORD_LENGTH)
            )
            self._ok_button.setText(self.tr("Create account"))
            self.resize(520, 560)
        else:
            self._title.setText(self.tr("Sign in to FieldWatch"))
            self._intro.setText(
                self.tr(
                    "Already have a FieldWatch account (from the website or another device)? "
                    "Sign in with your email and password."
                )
            )
            self._password_edit.setPlaceholderText(self.tr("Your password"))
            self._ok_button.setText(self.tr("Sign in"))
            self.resize(520, 320)

        self._sync_ok_state()

    def _form_values(self):
        return {
            "first_name": self._first_name_edit.text(),
            "last_name": self._last_name_edit.text(),
            "email": self._email_edit.text(),
            "password": self._password_edit.text(),
            "company": self._company_edit.text(),
            "reason_for_use": self._reason_edit.toPlainText(),
            "terms_accepted": self._terms_check.isChecked(),
        }

    def _sync_ok_state(self, *_args):
        if self._mode == _MODE_SIGN_IN:
            values = self._form_values()
            tip = login_form_tooltip(
                email=values["email"],
                password=values["password"],
            )
        else:
            tip = registration_form_tooltip(**self._form_values())
        enabled = not tip
        self._ok_button.setEnabled(enabled and not self._busy)
        self._ok_button.setToolTip("" if enabled else tip)

    def _on_ok(self):
        if self._busy:
            return
        if self._mode == _MODE_SIGN_IN:
            self._start_login()
        else:
            self._start_register()

    def _start_register(self):
        values = self._form_values()
        tip = registration_form_tooltip(**values)
        if tip:
            QtWidgets.QMessageBox.warning(self, self.tr("Registration"), tip)
            return

        from ..core.trial_workers import RegisterAccountWorker

        self._register_values = values
        self._set_busy(True, busy_label=self.tr("Creating account\u2026"))
        self._worker = RegisterAccountWorker(
            values,
            self._marketing_check.isChecked(),
            parent=self,
        )
        self._worker.finished_ok.connect(self._on_register_ok)
        self._worker.failed.connect(self._on_register_failed)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _start_login(self):
        values = self._form_values()
        tip = login_form_tooltip(email=values["email"], password=values["password"])
        if tip:
            QtWidgets.QMessageBox.warning(self, self.tr("Sign in"), tip)
            return

        from ..core.trial_workers import LoginAccountWorker

        self._register_values = values
        self._set_busy(True, busy_label=self.tr("Signing in\u2026"))
        self._worker = LoginAccountWorker(
            values["email"],
            values["password"],
            inference_base_url=self._inference_base_url,
            parent=self,
        )
        self._worker.finished_ok.connect(self._on_login_ok)
        self._worker.failed.connect(self._on_login_failed)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _set_busy(self, busy: bool, *, busy_label: str | None = None):
        """Toggle waiting state (buttons/cursor/label)."""
        self._busy = busy
        self._sign_up_tab.setEnabled(not busy)
        self._sign_in_tab.setEnabled(not busy)
        if busy:
            self._ok_button.setEnabled(False)
            self._cancel_button.setEnabled(False)
            self._ok_button.setText(busy_label or self.tr("Please wait\u2026"))
            QtWidgets.QApplication.setOverrideCursor(WaitCursor)
        else:
            QtWidgets.QApplication.restoreOverrideCursor()
            self._cancel_button.setEnabled(True)
            self._set_mode(self._mode)

    def _on_register_ok(self, _data):
        values = self._register_values or self._form_values()
        self._persist_onboarding(values)
        self._set_busy(False)
        self.accept()

    def _on_login_ok(self, data):
        data = data if isinstance(data, dict) else {}
        values = self._register_values or self._form_values()
        try:
            trial_helpers.save_user_session(
                token=data.get("token") or "",
                email=data.get("email") or values.get("email") or "",
                first_name=data.get("first_name") or "",
                last_name=data.get("last_name") or "",
                user_id=data.get("user_id") or "",
                expires_at=data.get("expires_at") or "",
            )
        except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
            from qgis.core import QgsMessageLog, Qgis

            QgsMessageLog.logMessage(
                f"Failed to persist session locally after sign-in: {exc}",
                "FieldWatch",
                Qgis.MessageLevel.Warning,
            )
            # Still accept if we at least stored onboarding via email.
            try:
                trial_helpers.save_onboarding(
                    data.get("email") or values.get("email") or "",
                    first_name=data.get("first_name") or "",
                    last_name=data.get("last_name") or "",
                )
            except (OSError, RuntimeError, TypeError, ValueError, KeyError) as fallback_exc:
                QgsMessageLog.logMessage(
                    f"Failed to persist onboarding after sign-in: {fallback_exc}",
                    "FieldWatch",
                    Qgis.MessageLevel.Warning,
                )
        self._set_busy(False)
        self.accept()

    @staticmethod
    def _persist_onboarding(values: dict) -> bool:
        try:
            trial_helpers.save_onboarding(
                values["email"],
                first_name=values["first_name"],
                last_name=values["last_name"],
            )
            return True
        except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
            from qgis.core import QgsMessageLog, Qgis

            QgsMessageLog.logMessage(
                f"Failed to persist onboarding locally after registration: {exc}",
                "FieldWatch",
                Qgis.MessageLevel.Warning,
            )
            return False

    def _on_register_failed(self, message: str):
        self._set_busy(False)
        QtWidgets.QMessageBox.warning(
            self,
            self.tr("Registration failed"),
            (message or self.tr("Registration failed."))[:2000],
        )

    def _on_login_failed(self, message: str):
        self._set_busy(False)
        QtWidgets.QMessageBox.warning(
            self,
            self.tr("Sign in failed"),
            (message or self.tr("Sign in failed."))[:2000],
        )

    def reject(self):
        # Block closing while a request is in flight so the worker is not orphaned.
        if self._busy:
            return
        super().reject()
