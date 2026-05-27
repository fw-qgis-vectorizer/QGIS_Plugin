# -*- coding: utf-8 -*-
"""Background threads for one-click model setup."""

from qgis.PyQt.QtCore import QThread, pyqtSignal


class ModelDepsInstallWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            from .model_venv import create_venv_and_install

            ok, msg = create_venv_and_install(
                progress_callback=lambda p, m: self.progress.emit(p, m),
                cancel_check=lambda: self._cancelled,
            )
            self.finished.emit(ok, msg)
        except Exception as exc:
            import traceback

            self.finished.emit(False, f"{exc}\n{traceback.format_exc()}")


class ModelDownloadWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, str)

    def run(self):
        try:
            from .model_checkpoint import download_checkpoint

            ok, msg = download_checkpoint(
                progress_callback=lambda p, m: self.progress.emit(p, m),
            )
            self.finished.emit(ok, msg)
        except Exception as exc:
            self.finished.emit(False, str(exc))


class ModelPredictWorker(QThread):
    """Run model predict off the UI thread."""

    finished = pyqtSignal(object, float, str)
    error = pyqtSignal(str)

    def __init__(
        self,
        predictor,
        coords,
        labels,
        *,
        mask_input=None,
        multimask_output=None,
        parent=None,
    ):
        super().__init__(parent)
        self._predictor = predictor
        self._coords = coords
        self._labels = labels
        self._mask_input = mask_input
        self._multimask_output = multimask_output

    def run(self):
        try:
            mask, score = self._predictor.predict(
                self._coords,
                self._labels,
                mask_input=self._mask_input,
                multimask_output=self._multimask_output,
            )
            self.finished.emit(mask, score, "")
        except Exception as exc:
            self.error.emit(str(exc))


class ModelInitWorker(QThread):
    """Load the segmentation model subprocess (can take minutes on CPU)."""

    finished = pyqtSignal(object, str)
    error = pyqtSignal(str)

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self._config = config

    def run(self):
        try:
            from .model_predictor import ModelPredictor

            predictor = ModelPredictor(self._config)
            predictor.start()
            self.finished.emit(predictor, "")
        except Exception as exc:
            self.error.emit(str(exc))


class ModelEncodeWorker(QThread):
    """Encode raster crop with the segmentation model."""

    finished = pyqtSignal(bool, str)
    error = pyqtSignal(str)

    def __init__(self, predictor, image, parent=None):
        super().__init__(parent)
        self._predictor = predictor
        self._image = image

    def run(self):
        try:
            self._predictor.set_image(self._image)
            self.finished.emit(True, "")
        except Exception as exc:
            self.error.emit(str(exc))
