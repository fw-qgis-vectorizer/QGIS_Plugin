# -*- coding: utf-8 -*-
"""Shared licence key + trial quota panel (profile-wide via trial_access)."""

from __future__ import annotations

import uuid

from qgis.PyQt import QtWidgets, QtCore, QtGui
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices

from ..core.api_config import INFERENCE_BASE_URL
from ..core import trial_helpers
from ..core.qt_compat import LineEditPassword, SizePolicyExpanding, SizePolicyFixed
from ..core.trial_access import shared as trial_shared, ONE_CLICK_TRIAL_MIN_REMAINING
from ..core.trial_workers import TrialGenerateWorker, TrialStateFetchWorker

FIELDWATCH_HOME_URL = "https://usefieldwatch.com/"


class LicenseTrialPanel(QtWidgets.QGroupBox):
    """Licence key, validate, and trial quota display (auto trial bootstrap)."""

    def __init__(
        self,
        parent=None,
        *,
        allow_get_licence_button: bool = True,
        mask_license_input: bool = False,
        prefill_license_key: bool = True,
        cache_trial_display_only: bool = False,
    ):
        super().__init__(parent)
        self.setTitle(self.tr("License"))
        self._allow_get_licence = allow_get_licence_button
        self._mask_license_input = mask_license_input
        self._prefill_license_key = prefill_license_key
        self._cache_trial_display_only = cache_trial_display_only
        self._acc = trial_shared()
        self._trial_state_worker = None
        self._trial_generate_worker = None
        self._refresh_show_error_dialog = False
        self._refresh_show_success_dialog = False
        self._build_ui()
        self._sync_from_access()
        self._update_quota_label()
        self._update_generate_button()

    def _sync_license_status_from_cache(self):
        """Reflect cached paid/trial state in the status label (one-click page)."""
        if self._acc.has_paid_license():
            self.licenseStatusLabel.setText(self.tr("✓ Valid"))
            self.licenseStatusLabel.setStyleSheet("color: green;")
        elif self._acc.can_start_one_click_segmentation():
            self.licenseStatusLabel.setText(self.tr("✓ Trial active"))
            self.licenseStatusLabel.setStyleSheet("color: green;")
        elif (
            self._acc.uses_remaining is not None
            and self._acc.uses_remaining < ONE_CLICK_TRIAL_MIN_REMAINING
        ):
            self.licenseStatusLabel.setText(self.tr("Add licence key to use."))
            self.licenseStatusLabel.setStyleSheet("color: #b45309;")
        elif (
            self._acc.trial_status is None
            and self._acc.uses_remaining is None
        ):
            self.licenseStatusLabel.setText(self.tr("Loading trial status…"))
            self.licenseStatusLabel.setStyleSheet("color: blue;")
        elif self._acc.trial_status == "exhausted":
            self.licenseStatusLabel.setText(self.tr("Trial exhausted"))
            self.licenseStatusLabel.setStyleSheet("color: red;")
        elif not self.licenseKeyLineEdit.text().strip():
            self.licenseStatusLabel.setText(self.tr("Not validated"))
            self.licenseStatusLabel.setStyleSheet("color: gray;")
        self._apply_license_input_visibility()

    def _build_ui(self):
        form = QtWidgets.QFormLayout(self)
        form.setContentsMargins(8, 12, 8, 8)

        key_row = QtWidgets.QHBoxLayout()
        self.licenseKeyLineEdit = QtWidgets.QLineEdit()
        self.licenseKeyLineEdit.setPlaceholderText(
            self.tr("Input license or trial key")
        )
        if self._mask_license_input:
            self.licenseKeyLineEdit.setEchoMode(LineEditPassword)
        self.validateLicenseButton = QtWidgets.QPushButton(self.tr("Validate"))
        key_row.addWidget(self.licenseKeyLineEdit)
        key_row.addWidget(self.validateLicenseButton)
        form.addRow(self.tr("License Key:"), key_row)

        self.generateTrialButton = QtWidgets.QPushButton(self.tr("Get Licence"))
        self.generateTrialButton.setSizePolicy(
            QtWidgets.QSizePolicy(SizePolicyExpanding, SizePolicyFixed)
        )
        form.addRow("", self.generateTrialButton)

        self.licenseStatusLabel = QtWidgets.QLabel(self.tr("Not validated"))
        self.licenseStatusLabel.setStyleSheet("color: gray;")
        form.addRow("", self.licenseStatusLabel)

        self.trialQuotaLabel = QtWidgets.QLabel("")
        self.trialQuotaLabel.setWordWrap(True)
        self._trial_quota_spinner = QtWidgets.QProgressBar()
        self._trial_quota_spinner.setRange(0, 0)
        self._trial_quota_spinner.setFixedWidth(100)
        self._trial_quota_spinner.setFixedHeight(14)
        self._trial_quota_spinner.setTextVisible(False)
        self._trial_quota_spinner.setVisible(False)
        quota_wrap = QtWidgets.QWidget()
        quota_row = QtWidgets.QHBoxLayout(quota_wrap)
        quota_row.setContentsMargins(0, 0, 0, 0)
        quota_row.addWidget(self._trial_quota_spinner)
        quota_row.addWidget(self.trialQuotaLabel, 1)
        form.addRow(self.tr("Trial quota:"), quota_wrap)

        self.validateLicenseButton.clicked.connect(self.validate_license)
        self.generateTrialButton.clicked.connect(self._on_generate_trial_clicked)

    def _sync_from_access(self):
        acc = self._acc
        if (
            self._prefill_license_key
            and acc.license_key
            and not self.licenseKeyLineEdit.text().strip()
        ):
            self.licenseKeyLineEdit.setText(acc.license_key)
        self._update_quota_label()
        self._update_generate_button()
        if self._cache_trial_display_only:
            self._sync_license_status_from_cache()

    def _set_quota_loading(self, loading: bool):
        self._trial_quota_spinner.setVisible(bool(loading))
        if loading:
            self.trialQuotaLabel.setText(self.tr("Loading trial status…"))
            self.trialQuotaLabel.setStyleSheet("color: blue;")

    @staticmethod
    def _looks_like_trial_uuid(text):
        try:
            uuid.UUID(str(text).strip())
            return True
        except (ValueError, TypeError, AttributeError):
            return False

    def _license_action_is_generate_trial(self):
        if self._acc.has_paid_license():
            return False
        if self._acc.trial_status == "exhausted":
            return False
        if self._acc.trial_status == "revoked":
            return False
        rem = self._acc.uses_remaining
        if rem is not None and rem <= 0:
            return False
        return True

    def _update_generate_button(self):
        # Trial is auto-bootstrapped on first open (no user "generate trial" action).
        if not self._allow_get_licence:
            self.generateTrialButton.setVisible(False)
            return

        if self._acc.has_paid_license():
            self.generateTrialButton.setVisible(False)
            return

        # Trial is active -> hide CTA.
        if self._acc.can_start_one_click_segmentation():
            self.generateTrialButton.setVisible(False)
            return

        # Only show CTA when trial is clearly exhausted/revoked/zero.
        trial_exhausted = (
            self._acc.trial_status in ("exhausted", "revoked")
            or (self._acc.uses_remaining is not None and self._acc.uses_remaining <= 0)
        )
        if not trial_exhausted:
            self.generateTrialButton.setVisible(False)
            return

        self.generateTrialButton.setVisible(True)
        self.generateTrialButton.setText(self.tr("Get Licence"))
        self.generateTrialButton.setToolTip(
            self.tr("Open FieldWatch in your browser to get a paid licence key.")
        )
        self.generateTrialButton.setEnabled(True)

    def _apply_license_input_visibility(self) -> None:
        """Hide licence inputs while an active trial exists (trial-only UX)."""
        trial_exhausted = (
            self._acc.trial_status in ("exhausted", "revoked")
            or (self._acc.uses_remaining is not None and self._acc.uses_remaining <= 0)
        )
        show_inputs = trial_exhausted and not self._acc.has_paid_license()

        self.licenseKeyLineEdit.setVisible(show_inputs)
        self.validateLicenseButton.setVisible(show_inputs)

    def _update_quota_label(self):
        if self._acc.has_paid_license():
            self.trialQuotaLabel.setText(
                self.tr("Using paid license — exports use your key, not trial quota.")
            )
            self.trialQuotaLabel.setStyleSheet("color: gray;")
        elif self._acc.uses_remaining is None and self._acc.trial_status is None:
            if self._trial_quota_spinner.isVisible():
                self.trialQuotaLabel.setText(self.tr("Loading trial status…"))
                self.trialQuotaLabel.setStyleSheet("color: blue;")
            else:
                self.trialQuotaLabel.setText("")
                self.trialQuotaLabel.setStyleSheet("color: gray;")
        else:
            total = self._acc.uses_total
            rem = self._acc.uses_remaining
            status = self._acc.trial_status
            pending = len(trial_helpers.load_pending_usage_keys())
            if total is not None and rem is not None:
                used = max(0, total - rem)
                txt = self.tr("{0} of {1} trial runs remaining (used {2}).").format(
                    rem, total, used
                )
            elif rem is not None:
                txt = self.tr("{0} trial runs remaining.").format(rem)
            else:
                txt = self.tr("Trial status: {0}").format(status or "unknown")
            if pending:
                txt += " " + self.tr("({0} export(s) waiting to sync.)").format(pending)
            if status == "exhausted" or (rem is not None and rem <= 0):
                self.trialQuotaLabel.setStyleSheet("color: red;")
                txt += " " + self.tr("Register at usefieldwatch.com for a full licence.")
            elif status == "revoked":
                self.trialQuotaLabel.setStyleSheet("color: red;")
            else:
                self.trialQuotaLabel.setStyleSheet("color: green;")
            self.trialQuotaLabel.setText(txt)
        self._apply_license_input_visibility()
        self._update_generate_button()

    def refresh_trial_state(
        self, show_error_dialog=False, show_success_dialog=False, *, force=False
    ):
        if self._cache_trial_display_only and not force:
            self._set_quota_loading(False)
            self._acc.load_from_settings()
            self._sync_from_access()
            return
        self._refresh_show_error_dialog = show_error_dialog
        self._refresh_show_success_dialog = show_success_dialog
        if (
            (self._trial_state_worker and self._trial_state_worker.isRunning())
            or (self._trial_generate_worker and self._trial_generate_worker.isRunning())
        ):
            return

        self._set_quota_loading(True)

        # Auto bootstrap on first open (no user clicks).
        if not trial_helpers.is_trial_established():
            self._trial_generate_worker = TrialGenerateWorker(
                self._acc.install_key, parent=self
            )
            self._trial_generate_worker.finished_ok.connect(self._on_trial_generated)
            self._trial_generate_worker.failed.connect(self._on_trial_generate_failed)
            self._trial_generate_worker.finished.connect(self._on_trial_generate_finished)
            self._trial_generate_worker.start()
        else:
            self._trial_state_worker = TrialStateFetchWorker(
                self._acc.install_key, self._acc.trial_id, parent=self
            )
            self._trial_state_worker.finished_ok.connect(self._on_trial_fetched)
            self._trial_state_worker.failed.connect(self._on_trial_fetch_failed)
            self._trial_state_worker.finished.connect(self._on_trial_worker_finished)
            self._trial_state_worker.start()

    def _on_trial_worker_finished(self):
        self._trial_state_worker = None

    def _show_trial_generate_success(self, data: dict) -> None:
        trial_helpers.mark_trial_established()
        tid = trial_helpers.trial_id_from_response(data) or (self._acc.trial_id or "")
        if tid:
            self.licenseKeyLineEdit.setText(tid)
            QtWidgets.QApplication.clipboard().setText(tid)
            trial_helpers.set_trial_generate_locked_trial_id(tid)
            self.licenseStatusLabel.setText(self.tr("✓ Trial key generated"))
            self.licenseStatusLabel.setStyleSheet("color: green;")
        rem = data.get("uses_remaining")
        tot = data.get("uses_total")
        rem_s = str(rem) if rem is not None else "?"
        tot_s = str(tot) if tot is not None else "?"
        if tid:
            body = self.tr(
                "Your trial key:\n\n{0}\n\n"
                "Runs remaining: {1} of {2}.\n\n"
                "The key has been copied to the clipboard and filled in above."
            ).format(tid, rem_s, tot_s)
        else:
            body = self.tr(
                "Trial activated.\n\nRuns remaining: {0} of {1}."
            ).format(rem_s, tot_s)
        QtWidgets.QMessageBox.information(self, self.tr("Trial key"), body)

    def _on_trial_fetched(self, data):
        self._set_quota_loading(False)
        self._acc.apply_server_state(data)
        if trial_helpers.trial_id_from_response(data) and not trial_helpers.is_trial_established():
            # Trial was created/returned by the server; treat it as established without user clicks.
            trial_helpers.mark_trial_established()
        self._sync_from_access()
        if (
            self._prefill_license_key
            and self._acc.trial_id
            and not self.licenseKeyLineEdit.text().strip()
        ):
            self.licenseKeyLineEdit.setText(self._acc.trial_id)
        self._notify_parent_refresh()

    def _on_trial_fetch_failed(self, err_text):
        self._set_quota_loading(False)
        self._acc.load_from_settings()
        self._sync_from_access()
        self.trialQuotaLabel.setText(
            self.tr("Could not refresh trial status — using cached quota.")
        )
        self.trialQuotaLabel.setStyleSheet("color: orange;")
        if self._refresh_show_error_dialog:
            QtWidgets.QMessageBox.warning(
                self, self.tr("Trial status"), err_text[:2000]
            )
        self._notify_parent_refresh()

    def _on_trial_generate_finished(self):
        self._trial_generate_worker = None
        self._update_generate_button()

    def _on_trial_generated(self, data):
        self._set_quota_loading(False)
        self._acc.apply_server_state(data)
        if trial_helpers.trial_id_from_response(data) and not trial_helpers.is_trial_established():
            trial_helpers.mark_trial_established()
        self._sync_from_access()
        self._notify_parent_refresh()
        if self._refresh_show_success_dialog:
            self._show_trial_generate_success(data)

    def _on_trial_generate_failed(self, err_text):
        self._set_quota_loading(False)
        self._acc.load_from_settings()
        self._sync_from_access()
        self.trialQuotaLabel.setText(self.tr("Could not generate trial key."))
        self.trialQuotaLabel.setStyleSheet("color: red;")
        if self._refresh_show_error_dialog:
            QtWidgets.QMessageBox.warning(
                self, self.tr("Trial key"), err_text[:2000]
            )
        self._notify_parent_refresh()

    def generate_trial_key(
        self, show_error_dialog=False, show_success_dialog=True
    ):
        """Request a new trial key from the server (not a silent quota refresh)."""
        if self._trial_generate_worker and self._trial_generate_worker.isRunning():
            return
        if self._trial_state_worker and self._trial_state_worker.isRunning():
            return
        self._refresh_show_error_dialog = show_error_dialog
        self._refresh_show_success_dialog = show_success_dialog
        self._set_quota_loading(True)
        self.generateTrialButton.setEnabled(False)
        self._trial_generate_worker = TrialGenerateWorker(
            self._acc.install_key, parent=self
        )
        self._trial_generate_worker.finished_ok.connect(self._on_trial_generated)
        self._trial_generate_worker.failed.connect(self._on_trial_generate_failed)
        self._trial_generate_worker.finished.connect(self._on_trial_generate_finished)
        self._trial_generate_worker.start()

    def _notify_parent_refresh(self):
        parent = self.parent()
        if parent is None:
            return
        if self._cache_trial_display_only:
            if hasattr(parent, "refresh_ui_local"):
                parent.refresh_ui_local()
            return
        if hasattr(parent, "refresh_all"):
            parent.refresh_all()

    def _on_generate_trial_clicked(self):
        # UX: "Get Licence" only (trial is auto-bootstrapped on first open).
        if self._allow_get_licence:
            QDesktopServices.openUrl(QUrl(FIELDWATCH_HOME_URL))

    def validate_license(self):
        license_key = self.licenseKeyLineEdit.text().strip()
        if not license_key:
            QtWidgets.QMessageBox.warning(
                self,
                self.tr("License Required"),
                self.tr("Please enter a license key."),
            )
            return

        if self._looks_like_trial_uuid(license_key):
            self._validate_pasted_trial_id(license_key)
            return

        self.validateLicenseButton.setEnabled(False)
        self.validateLicenseButton.setText(self.tr("Validating…"))
        self.licenseStatusLabel.setText(self.tr("Validating…"))
        self.licenseStatusLabel.setStyleSheet("color: blue;")
        try:
            from ..core.vec_inference_client import VecInferenceClient

            client = VecInferenceClient(INFERENCE_BASE_URL)
            token, expiry = client.validate_license_key(license_key)
            if token:
                self._acc.set_paid_license(token, license_key, expiry)
                self._sync_from_access()
                self.licenseStatusLabel.setText(self.tr("✓ Valid"))
                self.licenseStatusLabel.setStyleSheet("color: green;")
            else:
                self._acc.clear_paid_license()
                self._sync_from_access()
                self.licenseStatusLabel.setText(self.tr("✗ Invalid"))
                self.licenseStatusLabel.setStyleSheet("color: red;")
                QtWidgets.QMessageBox.warning(
                    self,
                    self.tr("Invalid License"),
                    self.tr("The license key is invalid or expired."),
                )
        except Exception as e:
            self._acc.clear_paid_license()
            self._sync_from_access()
            self.licenseStatusLabel.setText(self.tr("✗ Error"))
            self.licenseStatusLabel.setStyleSheet("color: red;")
            QtWidgets.QMessageBox.critical(
                self,
                self.tr("Validation Error"),
                self.tr("Failed to validate license: {0}").format(str(e)),
            )
        finally:
            self.validateLicenseButton.setEnabled(True)
            self.validateLicenseButton.setText(self.tr("Validate"))
            self._notify_parent_refresh()

    def _validate_pasted_trial_id(self, license_key):
        self.validateLicenseButton.setEnabled(False)
        self.validateLicenseButton.setText(self.tr("Validating…"))
        self.licenseStatusLabel.setText(self.tr("Validating trial key…"))
        self.licenseStatusLabel.setStyleSheet("color: blue;")
        old_tid = self._acc.trial_id
        self._acc.clear_paid_license()
        try:
            tid = str(uuid.UUID(license_key.strip()))
            trial_helpers.set_stored_trial_id(tid)
            data = trial_helpers.fetch_trial_state(
                INFERENCE_BASE_URL,
                self._acc.install_key,
                trial_id=tid,
                timeout=10,
            )
            self._acc.apply_server_state(data)
            self._sync_from_access()
            if self._acc.trial_can_run():
                trial_helpers.mark_trial_established()
                self.licenseStatusLabel.setText(self.tr("✓ Trial key valid"))
                self.licenseStatusLabel.setStyleSheet("color: green;")
            else:
                self.licenseStatusLabel.setText(self.tr("✗ Trial exhausted"))
                self.licenseStatusLabel.setStyleSheet("color: red;")
        except Exception as e:
            if old_tid:
                trial_helpers.set_stored_trial_id(old_tid)
            else:
                trial_helpers.clear_stored_trial_id()
            self._acc.load_from_settings()
            self._sync_from_access()
            self.licenseStatusLabel.setText(self.tr("✗ Trial key invalid"))
            self.licenseStatusLabel.setStyleSheet("color: red;")
            QtWidgets.QMessageBox.warning(
                self,
                self.tr("Trial key"),
                self.tr("Invalid trial key.\n\n{0}").format(str(e)[:500]),
            )
        finally:
            self.validateLicenseButton.setEnabled(True)
            self.validateLicenseButton.setText(self.tr("Validate"))
            self._notify_parent_refresh()
