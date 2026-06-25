# -*- coding: utf-8 -*-
"""First-run onboarding: welcome dialog on open and gate before billable runs."""

from __future__ import annotations

from typing import Callable

from qgis.PyQt import QtWidgets

from .api_config import INFERENCE_BASE_URL
from . import trial_helpers

ONBOARDING_REQUIRED_TOOLTIP = (
    "You must accept the terms and conditions before using this feature."
)


def onboarding_required_tooltip() -> str:
    return ONBOARDING_REQUIRED_TOOLTIP


def onboarding_ok_tooltip(email_text: str, terms_accepted: bool) -> str:
    """Tooltip for the welcome dialog OK button when it stays disabled."""
    email = (email_text or "").strip()
    if not email:
        return "Please enter your email address."
    if not trial_helpers.is_valid_email(email):
        return "Please enter a valid email address."
    if not terms_accepted:
        return "You must accept the terms and conditions."
    return ""


def set_process_button_enabled(button, would_enable: bool) -> bool:
    """
    Enable a workflow action button only when onboarding is complete and
    ``would_enable`` is true. Shows a terms tooltip while onboarding is pending.
    """
    if not trial_helpers.is_onboarding_complete():
        button.setEnabled(False)
        button.setToolTip(onboarding_required_tooltip())
        return False
    button.setEnabled(would_enable)
    if would_enable:
        button.setToolTip("")
    return would_enable


def _resolve_trial_id(trial_id=None):
    if trial_id:
        return trial_id
    from .trial_access import shared as trial_shared

    acc = trial_shared()
    return acc.trial_id or trial_helpers.get_stored_trial_id()


def _show_onboarding_dialog(parent, install_key, inference_base_url, trial_id=None):
    from ..dialogs.onboarding_dialog import OnboardingDialog
    from .qt_compat import dialog_exec

    dlg = OnboardingDialog(
        parent=parent,
        install_key=install_key,
        inference_base_url=inference_base_url,
        trial_id=_resolve_trial_id(trial_id),
    )
    return dialog_exec(dlg) == dlg.Accepted


def _finish_onboarding(
    parent,
    install_key,
    inference_base_url,
    on_registered: Callable[[], None] | None = None,
    on_success: Callable[[], None] | None = None,
) -> bool:
    from .trial_access import shared as trial_shared

    try:
        trial_helpers.bootstrap_trial_if_needed(
            inference_base_url,
            install_key,
            trial_shared(),
        )
    except Exception as e:
        QtWidgets.QMessageBox.warning(
            parent,
            parent.tr("Trial unavailable") if parent else "Trial unavailable",
            str(e)[:2000],
        )
        return False

    if on_registered:
        on_registered()
    if on_success:
        on_success()
    return True


def show_onboarding_dialog(
    parent,
    install_key,
    on_registered: Callable[[], None] | None = None,
    inference_base_url=None,
    trial_id=None,
) -> bool:
    """
    Open the welcome modal (e.g. pack landing Terms button).
    Runs trial bootstrap when the user completes onboarding for the first time.
    """
    base = inference_base_url or INFERENCE_BASE_URL
    was_complete = trial_helpers.is_onboarding_complete()
    if not _show_onboarding_dialog(parent, install_key, base, trial_id):
        return False
    if was_complete:
        return True
    return _finish_onboarding(parent, install_key, base, on_registered=on_registered)


def prompt_onboarding_if_needed(
    parent,
    install_key,
    on_registered: Callable[[], None] | None = None,
    inference_base_url=None,
    trial_id=None,
) -> bool:
    """
    Show the welcome dialog on first plugin open.
    Returns True when onboarding is complete (already done or just finished).
    """
    if trial_helpers.is_onboarding_complete():
        return True

    base = inference_base_url or INFERENCE_BASE_URL
    if not _show_onboarding_dialog(parent, install_key, base, trial_id):
        return False

    return _finish_onboarding(parent, install_key, base, on_registered=on_registered)


def require_onboarding(
    parent,
    install_key,
    on_success: Callable[[], None],
    on_registered: Callable[[], None] | None = None,
    inference_base_url=None,
    trial_id=None,
):
    """
    Run ``on_success`` immediately if onboarding is complete; otherwise show the modal.
    Returns True when ``on_success`` runs.
    """
    if trial_helpers.is_onboarding_complete():
        on_success()
        return True

    base = inference_base_url or INFERENCE_BASE_URL
    if not _show_onboarding_dialog(parent, install_key, base, trial_id):
        return False

    return _finish_onboarding(
        parent,
        install_key,
        base,
        on_registered=on_registered,
        on_success=on_success,
    )
