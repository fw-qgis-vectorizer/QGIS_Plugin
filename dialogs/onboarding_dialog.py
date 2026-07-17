# -*- coding: utf-8 -*-
"""Modal: FieldWatch account registration before first use."""

from __future__ import annotations

from qgis.PyQt import QtWidgets

from ..core import trial_helpers
from ..core.account_register import (
    DEFAULT_REASON_FOR_USE,
    MIN_PASSWORD_LENGTH,
    post_fieldwatch_register,
    registration_form_tooltip,
)
from ..core.qt_compat import (
    FrameNoFrame,
    LineEditPassword,
    PointingHandCursor,
    TextBrowserInteraction,
    WaitCursor,
)

FIELDWATCH_TERMS_URL = "https://usefieldwatch.com/terms"


class OnboardingDialog(QtWidgets.QDialog):
    """Collect account details and create a FieldWatch user on OK."""

    def __init__(self, parent=None, install_key=None, inference_base_url=None, trial_id=None):
        super().__init__(parent)
        self._install_key = install_key
        self._inference_base_url = inference_base_url
        self._trial_id = trial_id
        self.setWindowTitle(self.tr("Welcome to FieldWatch"))
        self.setModal(True)
        self.resize(520, 560)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(12)

        title = QtWidgets.QLabel(self.tr("Welcome to FieldWatch"))
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(title)

        intro = QtWidgets.QLabel(
            self.tr(
                "Create your FieldWatch account to get started with your free plugin trial."
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

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

        self._last_name_edit = QtWidgets.QLineEdit()
        self._last_name_edit.setPlaceholderText(self.tr("Lovelace"))
        stored_last = trial_helpers.get_stored_user_last_name()
        if stored_last:
            self._last_name_edit.setText(stored_last)
        form.addRow(self.tr("Last name:"), self._last_name_edit)

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

        self._reason_edit = QtWidgets.QPlainTextEdit()
        self._reason_edit.setPlaceholderText(self.tr(DEFAULT_REASON_FOR_USE))
        self._reason_edit.setFixedHeight(72)
        form.addRow(self.tr("Reason for use:"), self._reason_edit)

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
        layout.addLayout(terms_row)

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
        values = self._form_values()
        enabled = not registration_form_tooltip(**values)
        self._ok_button.setEnabled(enabled)
        if enabled:
            self._ok_button.setToolTip("")
        else:
            self._ok_button.setToolTip(registration_form_tooltip(**values))

    def _on_ok(self):
        values = self._form_values()
        tooltip = registration_form_tooltip(**values)
        if tooltip:
            QtWidgets.QMessageBox.warning(self, self.tr("Registration"), tooltip)
            return

        self._ok_button.setEnabled(False)
        self._cancel_button.setEnabled(False)
        QtWidgets.QApplication.setOverrideCursor(WaitCursor)
        try:
            post_fieldwatch_register(
                first_name=values["first_name"],
                last_name=values["last_name"],
                email=values["email"],
                password=values["password"],
                company=values["company"],
                reason_for_use=values["reason_for_use"],
                marketing_consent=self._marketing_check.isChecked(),
            )
            trial_helpers.save_onboarding(
                values["email"],
                first_name=values["first_name"],
                last_name=values["last_name"],
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self,
                self.tr("Registration failed"),
                str(exc)[:2000],
            )
            self._sync_ok_state()
            self._cancel_button.setEnabled(True)
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        self.accept()
