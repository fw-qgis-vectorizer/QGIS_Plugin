# -*- coding: utf-8 -*-
"""Small settings window for one-click map segmentation."""

from __future__ import annotations

from qgis.core import QgsProject, QgsRasterLayer
from qgis.PyQt import QtWidgets, QtCore

from .license_trial_panel import LicenseTrialPanel
from ..core.qgis_layer_utils import layer_is_usable, list_project_raster_layers
from ..core.qt_compat import (
    FrameHLine,
    FrameSunken,
    ItemDataUserRole,
    MsgBoxNo,
    MsgBoxOk,
    MsgBoxWarning,
    MsgBoxYes,
    dialog_exec,
)


class OneClickSettingsDialog(QtWidgets.QDialog):
    """Compact dialog: layer, deps/model status, download, start/stop, save."""

    def __init__(self, iface, parent=None, pack_dialog=None):
        super().__init__(parent or iface.mainWindow())
        self.iface = iface
        self._pack_dialog = pack_dialog
        self._controller = None
        self._deps_worker = None
        self._download_worker = None
        self._setup_busy = False
        self._segmentation_active = False
        self.setWindowTitle(self.tr("One-Click Segmentation"))
        self.setModal(False)
        self.resize(440, 580)
        self._build_ui()
        self.populate_layers()
        # Defer status check so the window opens immediately (no UI-thread subprocess).
        QtCore.QTimer.singleShot(0, self.refresh_all)

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QtWidgets.QLabel(self.tr("Interactive segmentation on maps"))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.depsHintLabel = QtWidgets.QLabel(
            self.tr(
                "Install dependencies downloads a portable Python runtime (if needed) "
                "and sets up PyTorch and the segmentation engine (pip). Download model fetches weights "
                "from FieldWatch hosting (no account required)."
            )
        )
        self.depsHintLabel.setWordWrap(True)
        self.depsHintLabel.setStyleSheet("color: palette(mid); font-size: 11px;")
        layout.addWidget(self.depsHintLabel)

        self.depsStatusLabel = QtWidgets.QLabel()
        self.modelStatusLabel = QtWidgets.QLabel()
        layout.addWidget(self.depsStatusLabel)
        layout.addWidget(self.modelStatusLabel)

        setup_row = QtWidgets.QHBoxLayout()
        self.installDepsButton = QtWidgets.QPushButton(self.tr("Install dependencies"))
        self.downloadModelButton = QtWidgets.QPushButton(self.tr("Download model"))
        setup_row.addWidget(self.installDepsButton)
        setup_row.addWidget(self.downloadModelButton)
        layout.addLayout(setup_row)

        self.progressBar = QtWidgets.QProgressBar()
        self.progressBar.setVisible(False)
        self.progressBar.setTextVisible(True)
        layout.addWidget(self.progressBar)

        self.statusLabel = QtWidgets.QLabel()
        self.statusLabel.setWordWrap(True)
        self.statusLabel.setStyleSheet("color: palette(mid);")
        self.statusLabel.setVisible(False)
        layout.addWidget(self.statusLabel)

        layout.addWidget(QtWidgets.QLabel(self.tr("Input raster layer")))
        self.layerCombo = QtWidgets.QComboBox()
        layout.addWidget(self.layerCombo)

        layout.addWidget(QtWidgets.QLabel(self.tr("Output layer name")))
        self.outputNameEdit = QtWidgets.QLineEdit("OneClick_Segmentation")
        layout.addWidget(self.outputNameEdit)

        seg_row = QtWidgets.QHBoxLayout()
        self.startButton = QtWidgets.QPushButton(self.tr("Start segmentation"))
        self.stopButton = QtWidgets.QPushButton(self.tr("Stop segmentation"))
        self.stopButton.setEnabled(False)
        seg_row.addWidget(self.startButton)
        seg_row.addWidget(self.stopButton)
        layout.addLayout(seg_row)

        self.saveLayerButton = QtWidgets.QPushButton(self.tr("Save all to layer"))
        self.saveLayerButton.setEnabled(False)
        layout.addWidget(self.saveLayerButton)

        layout.addStretch(1)

        separator = QtWidgets.QFrame()
        separator.setFrameShape(FrameHLine)
        separator.setFrameShadow(FrameSunken)
        separator.setStyleSheet("margin-top: 8px; margin-bottom: 8px;")
        layout.addWidget(separator)

        layout.addSpacing(12)

        self.licensePanel = LicenseTrialPanel(
            self,
            allow_get_licence_button=True,
            mask_license_input=False,
            prefill_license_key=False,
            cache_trial_display_only=True,
        )
        layout.addWidget(self.licensePanel)

        footer = QtWidgets.QHBoxLayout()
        self.backButton = QtWidgets.QPushButton(self.tr("Back"))
        self.refreshButton = QtWidgets.QPushButton(self.tr("Refresh"))
        self.closeButton = QtWidgets.QPushButton(self.tr("Close"))
        footer.addWidget(self.backButton)
        footer.addWidget(self.refreshButton)
        footer.addStretch()
        footer.addWidget(self.closeButton)
        layout.addLayout(footer)

        self.installDepsButton.clicked.connect(self._on_install_deps)
        self.downloadModelButton.clicked.connect(self._on_download_model)
        self.startButton.clicked.connect(self._on_start)
        self.stopButton.clicked.connect(self._on_stop)
        self.saveLayerButton.clicked.connect(self._on_save)
        self.backButton.clicked.connect(self._on_back)
        self.refreshButton.clicked.connect(self.refresh_all)
        self.closeButton.clicked.connect(self.close)
        self.layerCombo.currentIndexChanged.connect(self._on_layer_combo_changed)

        project = QgsProject.instance()
        project.layersAdded.connect(self._on_project_layers_changed)
        project.layersRemoved.connect(self._on_project_layers_changed)

    def _on_project_layers_changed(self, *_args):
        if not self.isVisible() or self._segmentation_active or self._setup_busy:
            return
        self.refresh_all()

    def showEvent(self, event):
        super().showEvent(event)
        QtCore.QTimer.singleShot(0, self._on_page_opened)

    def _on_page_opened(self):
        # Trial is auto-bootstrapped on first open.
        self.refresh_ui_local()
        from ..core import trial_helpers

        if not trial_helpers.is_trial_established():
            self.licensePanel.refresh_trial_state(force=True)

    def set_controller(self, controller):
        self._controller = controller

    def _on_back(self):
        """Return to the FieldWatch Pack landing page."""
        if self._segmentation_active:
            reply = QtWidgets.QMessageBox.question(
                self,
                self.tr("Segmentation active"),
                self.tr("Stop segmentation and go back to the home page?"),
                MsgBoxYes | MsgBoxNo,
                MsgBoxNo,
            )
            if reply != MsgBoxYes:
                return
        if self._controller:
            self._controller.shutdown()
        self.hide()
        if self._pack_dialog is not None:
            from .vec_plugin_dialog import _PAGE_LANDING

            self._pack_dialog._show_page(_PAGE_LANDING)
            self._pack_dialog._sync_ui_from_trial_access()
            self._pack_dialog._update_trial_quota_label()
            self._pack_dialog.show()
            self._pack_dialog.raise_()
            self._pack_dialog.activateWindow()

    def _on_layer_combo_changed(self, _index: int):
        if self._controller:
            self._controller.on_layer_changed()
        else:
            self.refresh_status(repopulate_layers=False)

    def populate_layers(self):
        # Filling the combo emits currentIndexChanged per item; block to avoid
        # refresh_status() storms (subprocess / UI freeze on Windows).
        previous_id = None
        current = self.get_raster_layer()
        if current is not None:
            try:
                previous_id = current.id()
            except Exception:
                previous_id = None

        self.layerCombo.blockSignals(True)
        try:
            self.layerCombo.clear()
            rasters = list_project_raster_layers()
            active = self.iface.activeLayer()
            if (
                isinstance(active, QgsRasterLayer)
                and active.isValid()
                and active not in rasters
            ):
                rasters.insert(0, active)

            if rasters:
                restore_index = 0
                for index, lyr in enumerate(rasters):
                    self.layerCombo.addItem(lyr.name())
                    self.layerCombo.setItemData(index, lyr, ItemDataUserRole)
                    if previous_id and lyr.id() == previous_id:
                        restore_index = index
                self.layerCombo.setCurrentIndex(restore_index)
                self.layerCombo.setEnabled(True)
            else:
                self.layerCombo.addItem(self.tr("No raster layers available"))
                self.layerCombo.setItemData(0, None, ItemDataUserRole)
                self.layerCombo.setEnabled(False)
        finally:
            self.layerCombo.blockSignals(False)

    def get_raster_layer(self):
        data = self.layerCombo.currentData(ItemDataUserRole)
        if data is None:
            data = self.layerCombo.currentData()
        if isinstance(data, QgsRasterLayer) and layer_is_usable(data):
            return data
        name = self.layerCombo.currentText()
        for lyr in list_project_raster_layers():
            if lyr.name() == name and layer_is_usable(lyr):
                return lyr
        return None

    def get_output_layer_name(self) -> str:
        name = self.outputNameEdit.text().strip()
        return name or "OneClick_Segmentation"

    def _apply_setup_visibility(self, deps_ok: bool, model_ok: bool):
        """Show setup buttons only for steps that are still required."""
        if self._setup_busy or self._segmentation_active:
            self.depsHintLabel.setVisible(False)
            self.installDepsButton.setVisible(False)
            self.downloadModelButton.setVisible(False)
            return

        self.depsHintLabel.setVisible(not deps_ok)
        self.installDepsButton.setVisible(not deps_ok)
        self.downloadModelButton.setVisible(deps_ok and not model_ok)

    def update_status(self, deps_ok: bool, model_ok: bool):
        from ..core import trial_helpers
        from ..core.trial_access import ONE_CLICK_TRIAL_MIN_REMAINING, shared as trial_shared

        has_raster = self.get_raster_layer() is not None
        acc = trial_shared()
        can_start = acc.can_start_one_click_segmentation()
        if deps_ok and model_ok:
            self.depsStatusLabel.setVisible(False)
            if not can_start:
                if acc.has_paid_license():
                    msg = self.tr("Validate a licence key below before starting.")
                elif (
                    acc.uses_remaining is not None
                    and acc.uses_remaining < ONE_CLICK_TRIAL_MIN_REMAINING
                ):
                    msg = self.tr("Add licence key to use.")
                else:
                    msg = (
                        self.tr("Loading trial status…")
                        if not trial_helpers.is_trial_established()
                        else self.tr("Validate a paid licence key before starting.")
                    )
                self.modelStatusLabel.setText(msg)
                self.modelStatusLabel.setStyleSheet("color: #b45309;")
            elif has_raster:
                self.modelStatusLabel.setText(self.tr("Ready for segmentation."))
                self.modelStatusLabel.setStyleSheet("color: green;")
            else:
                self.modelStatusLabel.setText(
                    self.tr(
                        "Ready — add a raster to the map, then Start segmentation."
                    )
                )
                self.modelStatusLabel.setStyleSheet("color: green;")
        else:
            self.depsStatusLabel.setVisible(True)
            self.depsStatusLabel.setText(
                self.tr("Dependencies: installed")
                if deps_ok
                else self.tr("Dependencies: not installed")
            )
            self.depsStatusLabel.setStyleSheet(
                "color: green;" if deps_ok else "color: #b45309;"
            )
            self.modelStatusLabel.setText(
                self.tr("Weights: ready")
                if model_ok
                else self.tr("Weights: not downloaded")
            )
            self.modelStatusLabel.setStyleSheet(
                "color: green;" if model_ok else "color: #b45309;"
            )

        self._apply_setup_visibility(deps_ok, model_ok)
        self.installDepsButton.setEnabled(not deps_ok and not self._setup_busy)
        self.downloadModelButton.setEnabled(
            deps_ok and not model_ok and not self._setup_busy
        )

        self._update_start_button_enabled()

    def _update_start_button_enabled(self):
        from ..core.trial_access import shared as trial_shared
        from ..core.onboarding_helpers import set_process_button_enabled

        can_start = trial_shared().can_start_one_click_segmentation()
        set_process_button_enabled(
            self.startButton,
            can_start and not self._segmentation_active and not self._setup_busy,
        )

    def set_action_enabled(self, action: str, enabled: bool):
        if action == "start":
            self._update_start_button_enabled()

    def set_segmentation_active(self, active: bool, *, unloading: bool = False):
        self._segmentation_active = active
        if unloading:
            self.stopButton.setEnabled(False)
            return
        self._update_start_button_enabled()
        self.stopButton.setEnabled(active)
        has_layer = self.get_raster_layer() is not None
        self.layerCombo.setEnabled(not active and has_layer)
        from ..core.onboarding_helpers import set_process_button_enabled

        set_process_button_enabled(self.saveLayerButton, active)
        self.refreshButton.setEnabled(not active)

        if active:
            self.installDepsButton.setVisible(False)
            self.downloadModelButton.setVisible(False)
            self.depsHintLabel.setVisible(False)
        else:
            self.refresh_all()

    def _begin_setup_progress(self, message: str):
        self._setup_busy = True
        self.progressBar.setVisible(True)
        self.progressBar.setRange(0, 0)
        self.progressBar.setValue(0)
        self.progressBar.setFormat(message)
        self.statusLabel.setVisible(True)
        self.statusLabel.setText(message)
        self.installDepsButton.setVisible(False)
        self.downloadModelButton.setVisible(False)
        self.depsHintLabel.setVisible(False)
        self._update_start_button_enabled()

    def _update_setup_progress(self, percent: int, message: str):
        from ..core.one_click_log import sanitize_log_line

        message = sanitize_log_line(message or "")
        if percent > 0:
            self.progressBar.setRange(0, 100)
            self.progressBar.setValue(max(0, min(100, percent)))
            self.progressBar.setFormat(f"{percent}%")
        else:
            self.progressBar.setRange(0, 0)
            self.progressBar.setFormat(message)
        if message:
            self.statusLabel.setText(message)

    def _end_setup_progress(self):
        self._setup_busy = False
        self.progressBar.setVisible(False)
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)
        self.progressBar.setFormat("")
        self.statusLabel.clear()
        self.statusLabel.setVisible(False)
        self._update_start_button_enabled()

    def set_busy(self, busy: bool, message: str = ""):
        """Used during segmentation (encode/predict), not setup downloads."""
        if busy:
            self.progressBar.setVisible(True)
            self.progressBar.setRange(0, 0)
            self.progressBar.setFormat(message or "")
            if message:
                self.statusLabel.setVisible(True)
                self.statusLabel.setText(message)
        else:
            if not self._setup_busy:
                self.progressBar.setVisible(False)
                self.progressBar.setRange(0, 100)
                self.progressBar.setValue(0)
                self.progressBar.setFormat("")
                if not self._segmentation_active:
                    self.statusLabel.clear()
                    self.statusLabel.setVisible(False)
        self._update_start_button_enabled()

    def set_status(self, text: str):
        if text:
            self.statusLabel.setVisible(True)
            self.statusLabel.setText(text)

    def refresh_ui_local(self):
        """Refresh layers, deps/weights, and cached licence/trial (no network poll)."""
        if self._setup_busy:
            return

        from ..core.model_config import checkpoint_exists
        from ..core.model_venv import deps_installed

        self.licensePanel._acc.load_from_settings()
        self.licensePanel._sync_from_access()
        self.licensePanel._update_quota_label()

        self.populate_layers()
        self.update_status(deps_installed(), checkpoint_exists())

        if self._controller is not None and self._controller._plugin is not None:
            predictor = getattr(
                self._controller._plugin, "_segmentation_predictor", None
            )
            self._controller.apply_shared_predictor(predictor)

        self._update_start_button_enabled()

    def refresh_all(self):
        """One-click: local refresh only — trial quota from profile cache, not live poll."""
        self.refresh_ui_local()

    def refresh_status(self, *, repopulate_layers: bool = True):
        """Backward-compatible alias."""
        del repopulate_layers
        self.refresh_ui_local()

    def run_dependency_install(self, on_complete):
        """Install deps in background; on_complete(ok: bool)."""
        from ..core.one_click_workers import ModelDepsInstallWorker

        if self._deps_worker and self._deps_worker.isRunning():
            return
        self._begin_setup_progress(
            self.tr("Installing dependencies (may take several minutes)…")
        )
        self._deps_worker = ModelDepsInstallWorker(self)
        self._deps_worker.progress.connect(
            lambda p, m: self._update_setup_progress(p, m)
        )

        def _finished(ok, message):
            self._end_setup_progress()
            if not ok:
                box = QtWidgets.QMessageBox(self)
                box.setIcon(MsgBoxWarning)
                box.setWindowTitle(self.tr("Dependencies"))
                box.setText(self.tr("Dependency installation failed."))
                from ..core.one_click_log import sanitize_log_line

                box.setInformativeText(sanitize_log_line((message or "")[:4000]))
                box.setStandardButtons(MsgBoxOk)
                dialog_exec(box)
            self.refresh_all()
            on_complete(ok)

        self._deps_worker.finished.connect(_finished)
        self._deps_worker.start()

    def run_weight_download(self, on_complete):
        """Download weights in background; on_complete(ok: bool)."""
        from ..core.one_click_workers import ModelDownloadWorker

        if self._download_worker and self._download_worker.isRunning():
            return
        self._begin_setup_progress(self.tr("Downloading weights…"))
        self._download_worker = ModelDownloadWorker(self)
        self._download_worker.progress.connect(
            lambda p, m: self._update_setup_progress(p, m)
        )

        def _finished(ok, message):
            self._end_setup_progress()
            if not ok:
                box = QtWidgets.QMessageBox(self)
                box.setIcon(MsgBoxWarning)
                box.setWindowTitle(self.tr("Weights"))
                box.setText(self.tr("Download failed."))
                from ..core.one_click_log import sanitize_log_line

                box.setInformativeText(sanitize_log_line((message or "")[:4000]))
                box.setStandardButtons(MsgBoxOk)
                dialog_exec(box)
            self.refresh_all()
            on_complete(ok)

        self._download_worker.finished.connect(_finished)
        self._download_worker.start()

    def _on_install_deps(self):
        from ..core.one_click_workers import ModelDepsInstallWorker

        if self._deps_worker and self._deps_worker.isRunning():
            return
        self._begin_setup_progress(
            self.tr("Installing dependencies (may take several minutes)…")
        )
        self._deps_worker = ModelDepsInstallWorker(self)
        self._deps_worker.progress.connect(
            lambda p, m: self._update_setup_progress(p, m)
        )
        self._deps_worker.finished.connect(self._on_deps_finished)
        self._deps_worker.start()

    def _on_deps_finished(self, ok, message):
        self._end_setup_progress()
        if ok:
            QtWidgets.QMessageBox.information(self, self.tr("Dependencies"), message)
        else:
            box = QtWidgets.QMessageBox(self)
            box.setIcon(MsgBoxWarning)
            box.setWindowTitle(self.tr("Dependencies"))
            box.setText(self.tr("Dependency installation failed."))
            box.setInformativeText(message[:4000])
            box.setStandardButtons(MsgBoxOk)
            dialog_exec(box)
        self.refresh_all()

    def _on_download_model(self):
        from ..core.one_click_workers import ModelDownloadWorker

        if self._download_worker and self._download_worker.isRunning():
            return
        self._begin_setup_progress(self.tr("Downloading weights…"))
        self._download_worker = ModelDownloadWorker(self)
        self._download_worker.progress.connect(
            lambda p, m: self._update_setup_progress(p, m)
        )
        self._download_worker.finished.connect(self._on_download_finished)
        self._download_worker.start()

    def _on_download_finished(self, ok, message):
        self._end_setup_progress()
        if ok:
            QtWidgets.QMessageBox.information(self, self.tr("Weights"), message)
        else:
            box = QtWidgets.QMessageBox(self)
            box.setIcon(MsgBoxWarning)
            box.setWindowTitle(self.tr("Weights"))
            box.setText(self.tr("Download failed."))
            box.setInformativeText(message[:4000])
            box.setStandardButtons(MsgBoxOk)
            dialog_exec(box)
        self.refresh_all()

    def _on_start(self):
        from ..core.trial_access import shared as trial_shared
        from ..core.onboarding_helpers import require_onboarding

        acc = trial_shared()

        def proceed():
            if self._controller:
                self._controller.start_segmentation()

        require_onboarding(
            self,
            acc.install_key,
            proceed,
            on_registered=self._after_onboarding_registered,
        )

    def _after_onboarding_registered(self):
        self.licensePanel.refresh_trial_state(force=True)
        self._update_start_button_enabled()

    def _on_stop(self):
        if self._controller:
            self._controller.stop_segmentation()

    def _on_save(self):
        if self._controller:
            self._controller.save_all_to_layer()

    def closeEvent(self, event):
        if self._controller:
            self._controller.shutdown()
        super().closeEvent(event)
