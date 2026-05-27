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
