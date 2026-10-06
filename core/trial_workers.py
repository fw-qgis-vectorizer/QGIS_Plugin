# -*- coding: utf-8 -*-
"""Background workers for trial API calls."""

from qgis.PyQt import QtCore

from .api_config import INFERENCE_BASE_URL
from . import trial_helpers


class TrialStateFetchWorker(QtCore.QThread):
    """Fetch trial quota from the server off the UI thread."""

    finished_ok = QtCore.pyqtSignal(dict)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, install_key, trial_id=None, parent=None):
        super().__init__(parent)
        self._install_key = install_key
        self._trial_id = trial_id

    def run(self):
        try:
            data = trial_helpers.fetch_trial_state(
                INFERENCE_BASE_URL,
                self._install_key,
                trial_id=self._trial_id,
                timeout=5,
                include_stored_contact=True,
            )
            self.finished_ok.emit(data)
        except Exception as e:
            self.failed.emit(str(e))


class TrialGenerateWorker(QtCore.QThread):
    """Request a new trial key from the server off the UI thread."""

    finished_ok = QtCore.pyqtSignal(dict)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, install_key, parent=None):
        super().__init__(parent)
        self._install_key = install_key

    def run(self):
        try:
            data = trial_helpers.request_trial_generate(
                INFERENCE_BASE_URL,
                self._install_key,
                timeout=15,
            )
            self.finished_ok.emit(data)
        except Exception as e:
            self.failed.emit(str(e))


class RegisterAccountWorker(QtCore.QThread):
    """Create a FieldWatch account off the UI thread (keeps QGIS responsive)."""

    finished_ok = QtCore.pyqtSignal(dict)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, form_values: dict, marketing_consent: bool, parent=None):
        super().__init__(parent)
        self._values = dict(form_values or {})
        self._marketing_consent = bool(marketing_consent)

    def run(self):
        from .account_register import post_fieldwatch_register

        try:
            data = post_fieldwatch_register(
                first_name=self._values.get("first_name", ""),
                last_name=self._values.get("last_name", ""),
                email=self._values.get("email", ""),
                password=self._values.get("password", ""),
                company=self._values.get("company", ""),
                reason_for_use=self._values.get("reason_for_use", ""),
                marketing_consent=self._marketing_consent,
            )
            self.finished_ok.emit(data if isinstance(data, dict) else {})
        except Exception as e:
            self.failed.emit(str(e))


class LoginAccountWorker(QtCore.QThread):
    """Sign in to an existing FieldWatch account off the UI thread."""

    finished_ok = QtCore.pyqtSignal(dict)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, email: str, password: str, inference_base_url=None, parent=None):
        super().__init__(parent)
        self._email = email or ""
        self._password = password or ""
        self._inference_base_url = inference_base_url

    def run(self):
        from .account_login import post_fieldwatch_login

        try:
            data = post_fieldwatch_login(
                email=self._email,
                password=self._password,
                inference_base_url=self._inference_base_url,
            )
            self.finished_ok.emit(data if isinstance(data, dict) else {})
        except Exception as e:
            self.failed.emit(str(e))
