# -*- coding: utf-8 -*-
"""
Shared trial quota for large-area vectorisation and one-click export.

Option B: one-click may export offline using cached uses_remaining; deferred
server usage sync when online (stable idempotency_key per export).
"""

from __future__ import annotations

import json
import uuid
from typing import Callable

from qgis.core import QgsMessageLog, Qgis

from .api_config import INFERENCE_BASE_URL
from . import trial_helpers

LOG_CHANNEL = "FieldWatch"

# One-click: trial users need full quota (3/3) unless they validate a paid licence.
ONE_CLICK_TRIAL_MIN_REMAINING = 3


def _log(msg: str, level=Qgis.MessageLevel.Info) -> None:
    QgsMessageLog.logMessage(msg, LOG_CHANNEL, level=level)


class TrialAccess:
    """Profile-wide trial state (QSettings) shared across plugin workflows."""

    ACTION_INFER = "infer"
    ACTION_ONE_CLICK_SEGMENT = "one_click_segment"
    ACTION_ONE_CLICK_EXPORT = "one_click_export"

    def __init__(self) -> None:
        self.install_key = trial_helpers.ensure_install_key()
        self.trial_id: str | None = trial_helpers.get_stored_trial_id() or None
        self.uses_total: int | None = None
        self.uses_remaining: int | None = None
        self.trial_status: str | None = None
        self.jwt_token: str | None = None
        self.license_key: str | None = None
        self.token_expiry = None
        self._billable_idempotency_key: str | None = None
        self.load_from_settings()

    def load_from_settings(self) -> None:
        cache = trial_helpers.load_cached_trial_state()
        self.uses_total = cache.get("uses_total")
        self.uses_remaining = cache.get("uses_remaining")
        self.trial_status = cache.get("trial_status")
        tid = trial_helpers.get_stored_trial_id()
        self.trial_id = tid or self.trial_id
        paid = trial_helpers.load_paid_license()
        self.jwt_token = paid.get("jwt_token") or None
        self.license_key = paid.get("license_key") or None
        self.token_expiry = paid.get("token_expiry")

    def save_cache_to_settings(self) -> None:
        trial_helpers.save_cached_trial_state(
            self.uses_total,
            self.uses_remaining,
            self.trial_status,
        )

    def apply_server_state(self, data: dict) -> None:
        if not data:
            return
        data = trial_helpers.normalize_trial_response(dict(data))
        tid = trial_helpers.trial_id_from_response(data)
        if tid:
            self.trial_id = str(tid)
            trial_helpers.set_stored_trial_id(self.trial_id)
        if data.get("uses_total") is not None:
            self.uses_total = int(data["uses_total"])
        if data.get("uses_remaining") is not None:
            self.uses_remaining = int(data["uses_remaining"])
        if data.get("trial_state"):
            self.trial_status = str(data["trial_state"])
        self.save_cache_to_settings()

    def set_paid_license(self, jwt_token: str, license_key: str, token_expiry=None) -> None:
        self.jwt_token = jwt_token
        self.license_key = license_key
        self.token_expiry = token_expiry
        trial_helpers.save_paid_license(jwt_token, license_key, token_expiry)
        self.clear_billable_idempotency_key()

    def clear_paid_license(self) -> None:
        self.jwt_token = None
        self.license_key = None
        self.token_expiry = None
        trial_helpers.clear_paid_license()

    def has_paid_license(self) -> bool:
        """True only when both a stored licence key and JWT exist (validated paid user)."""
        return bool(
            (self.jwt_token or "").strip() and (self.license_key or "").strip()
        )

    def trial_can_run(self) -> bool:
        if self.has_paid_license():
            return False
        return (
            self.trial_status == "active"
            and self.uses_remaining is not None
            and self.uses_remaining > 0
        )

    def is_trial_unlocked(self) -> bool:
        """Trial may be used after the server returns an active trial for this install/profile."""
        if self.has_paid_license():
            return True
        if self.trial_id and self.trial_can_run():
            return True
        if not trial_helpers.is_trial_established() or not self.trial_id:
            return False
        return self.trial_can_run()

    def can_use_one_click(self) -> bool:
        """Paid JWT or established trial with runs remaining."""
        return self.has_paid_license() or self.is_trial_unlocked()

    def can_start_one_click_segmentation(self) -> bool:
        """
        Paid licence, or established trial with full quota (>= 3 runs remaining).
        """
        if self.has_paid_license():
            return True
        if not trial_helpers.is_trial_established() or not self.trial_id:
            return False
        rem = self.uses_remaining
        if rem is None or rem < ONE_CLICK_TRIAL_MIN_REMAINING:
            return False
        if self.trial_status not in (None, "active"):
            return False
        return rem >= ONE_CLICK_TRIAL_MIN_REMAINING

    def can_run_large_area(self) -> bool:
        """Paid licence or active trial with at least one run remaining."""
        if self.has_paid_license():
            return True
        if self.trial_id and self.trial_can_run():
            return True
        return self.is_trial_unlocked()

    def prepare_trial_for_billable_run(self, timeout: int = 15) -> tuple[bool, str]:
        """
        Sync trial state from the server before billing (GET /qgis/trial/state).
        Marks the profile established when the server returns a trial_id.
        """
        if self.has_paid_license():
            return True, ""
        if not self.install_key:
            return False, "Install key missing. Restart the plugin."
        if not trial_helpers.network_reachable(timeout=3):
            return False, "No internet connection. Large area vectorisation requires online trial verification."
        try:
            data = trial_helpers.fetch_trial_state(
                INFERENCE_BASE_URL,
                self.install_key,
                trial_id=self.trial_id,
                timeout=timeout,
            )
            self.apply_server_state(data)
        except Exception as exc:
            return False, f"Could not load trial status: {exc}"[:500]
        if self.trial_id:
            trial_helpers.mark_trial_established()
        if not self.trial_id:
            return (
                False,
                "Trial quota is not available on this device. Wait for trial status to load or validate a paid licence key.",
            )
        if not self.trial_can_run():
            return False, self._trial_blocked_message()
        return True, ""

    def _trial_blocked_message(self) -> str:
        if self.trial_status in ("exhausted", "revoked"):
            return "No trial runs remaining. Register for a licence at usefieldwatch.com."
        return "Trial quota is not available. Validate a paid licence key or refresh trial status."

    def build_infer_client_for_large_area(
        self,
        service_url: str,
        upload_url: str | None = None,
    ) -> tuple[object | None, str, dict | None]:
        """
        Build a VecInferenceClient for large-area infer.

        Paid: re-validate licence key and use Bearer auth.
        Trial: POST /qgis/trial/usage, then attach X-Trial-* headers on all QGIS routes.
        """
        from .vec_inference_client import VecInferenceClient

        self.load_from_settings()
        self.sync_pending_usages()

        license_key = (self.license_key or "").strip()
        jwt = (self.jwt_token or "").strip()

        if license_key:
            client = VecInferenceClient(
                service_url,
                upload_url=upload_url,
                jwt_token=jwt or None,
                license_key=license_key,
            )
            token, expiry = client.validate_license_key(license_key)
            if token:
                self.set_paid_license(token, license_key, expiry)
                client.jwt_token = token
                return client, "", None
            if jwt:
                self.clear_paid_license()
        elif jwt and not license_key:
            self.clear_paid_license()

        if not self.get_billable_idempotency_key():
            self.get_billable_idempotency_key()

        ok, msg, usage, receipt = self.consume_for_large_area_infer()
        if not ok:
            return None, msg or "Trial usage denied.", usage

        self.clear_billable_idempotency_key()
        client = VecInferenceClient(
            service_url,
            upload_url=upload_url,
            jwt_token=None,
            license_key=None,
            trial_receipt=receipt,
            trial_install_key=self.install_key,
            trial_server_id=self.trial_id,
        )
        return client, "", usage

    def cached_can_export_offline(self) -> bool:
        if self.has_paid_license():
            return True
        if not trial_helpers.is_trial_established() or not self.trial_id:
            return False
        rem = self.uses_remaining
        if rem is not None and rem > 0:
            return self.trial_status in (None, "active")
        return False

    def is_online(self) -> bool:
        return trial_helpers.network_reachable()

    def sync_pending_usages(self) -> tuple[int, str | None]:
        """
        Flush deferred export usage POSTs. Returns (synced_count, last_error).
        """
        if not self.is_online() or not self.trial_id:
            return 0, None
        pending = trial_helpers.load_pending_usage_keys()
        if not pending:
            return 0, None
        synced = 0
        last_err = None
        remaining = []
        for i, entry in enumerate(pending):
            idem = (entry.get("idempotency_key") or "")[:128]
            action = entry.get("action") or self.ACTION_ONE_CLICK_EXPORT
            if not idem:
                continue
            try:
                data, clear = trial_helpers.post_trial_usage(
                    INFERENCE_BASE_URL,
                    self.trial_id,
                    self.install_key,
                    idem,
                    action=action,
                    timeout=30,
                )
            except Exception as exc:
                last_err = str(exc)
                remaining = pending[i:]
                break
            if not data.get("allowed", False):
                self.apply_server_state(data)
                remaining = pending[i:]
                trial_helpers.save_pending_usage_keys(remaining)
                return synced, data.get("message") or last_err
            self.apply_server_state(data)
            if clear:
                synced += 1
            else:
                remaining = pending[i:]
                break
        else:
            remaining = []
        trial_helpers.save_pending_usage_keys(remaining)
        return synced, last_err

    def consume_for_one_click_session(self) -> tuple[bool, str, dict | None]:
        """
        Reserve one trial run when starting one-click segmentation (online POST).
        Paid licence: no trial debit.
        """
        if self.has_paid_license():
            return True, "", None

        self.sync_pending_usages()

        if not trial_helpers.is_trial_established() or not self.trial_id:
            return (
                False,
                "Trial quota is not available yet. Open the plugin to auto-activate your trial (or validate a paid licence key).",
                None,
            )
        if not self.trial_can_run():
            return (
                False,
                "No trial runs remaining. Validate a paid licence key.",
                None,
            )
        if not self.is_online():
            return (
                False,
                "One-click segmentation requires an internet connection to verify trial quota.",
                None,
            )

        idem = str(uuid.uuid4())[:128]
        try:
            data, _clear = trial_helpers.post_trial_usage(
                INFERENCE_BASE_URL,
                self.trial_id,
                self.install_key,
                idem,
                action=self.ACTION_ONE_CLICK_SEGMENT,
                timeout=45,
            )
        except Exception as exc:
            return False, str(exc)[:500], None

        if not data.get("allowed", False):
            self.apply_server_state(data)
            msg = data.get("message") or data.get("error_code") or "Trial usage denied."
            return False, str(msg)[:500], data

        self.apply_server_state(data)
        return True, "", data

    def consume_for_one_click_export(self) -> tuple[bool, str, dict | None]:
        """
        Reserve one trial use for export. Returns (ok, user_message, usage_response).

        Online: synchronous POST before export.
        Offline: allow if cache says runs left; queue deferred POST.
        """
        if self.has_paid_license():
            return True, "", None

        self.sync_pending_usages()

        if not self.trial_can_run() and not self.cached_can_export_offline():
            return (
                False,
                "No trial runs remaining. Validate a paid licence key.",
                None,
            )

        idem = str(uuid.uuid4())[:128]

        if self.is_online() and self.trial_id:
            try:
                data, _clear = trial_helpers.post_trial_usage(
                    INFERENCE_BASE_URL,
                    self.trial_id,
                    self.install_key,
                    idem,
                    action=self.ACTION_ONE_CLICK_EXPORT,
                    timeout=45,
                )
            except Exception as exc:
                if self.cached_can_export_offline():
                    return self._defer_export_usage(idem, str(exc))
                return False, str(exc)[:500], None

            if not data.get("allowed", False):
                self.apply_server_state(data)
                msg = data.get("message") or data.get("error_code") or "Trial usage denied."
                return False, str(msg)[:500], data

            self.apply_server_state(data)
            return True, "", data

        if self.cached_can_export_offline():
            return self._defer_export_usage(idem, None)

        return (
            False,
            "Connect to the internet to verify trial quota, or validate a paid licence on the landing page.",
            None,
        )

    def _defer_export_usage(self, idempotency_key: str, note: str | None) -> tuple[bool, str, dict | None]:
        trial_helpers.append_pending_usage(idempotency_key, self.ACTION_ONE_CLICK_EXPORT)
        if self.uses_remaining is not None and self.uses_remaining > 0:
            self.uses_remaining -= 1
            if self.uses_remaining <= 0:
                self.trial_status = "exhausted"
        self.save_cache_to_settings()
        msg = "Export saved. Trial usage will sync when you are back online."
        if note:
            msg = f"{msg} ({note[:120]})"
        return True, msg, None

    def consume_for_large_area_infer(self) -> tuple[bool, str, dict | None, str | None]:
        """
        Synchronous trial debit for cloud infer. Returns (ok, message, usage_data, receipt).
        """
        if self.has_paid_license():
            return True, "", None, None

        self.sync_pending_usages()

        ready, prep_msg = self.prepare_trial_for_billable_run()
        if not ready:
            return False, prep_msg, None, None
        if not self.is_online():
            return False, "Large area vectorisation requires an internet connection.", None, None

        idem = self.get_billable_idempotency_key()
        try:
            data, clear = trial_helpers.post_trial_usage(
                INFERENCE_BASE_URL,
                self.trial_id,
                self.install_key,
                idem,
                action=self.ACTION_INFER,
                timeout=45,
            )
        except Exception as exc:
            return False, str(exc)[:500], None, None

        if clear:
            self.clear_billable_idempotency_key()

        if not data.get("allowed", False):
            self.apply_server_state(data)
            msg = data.get("message") or data.get("error_code") or "Trial usage denied."
            return False, str(msg)[:500], data, None

        self.apply_server_state(data)
        receipt = data.get("receipt")
        if not receipt:
            return False, "Trial usage succeeded but no receipt was returned.", data, None
        return True, "", data, str(receipt)

    def get_billable_idempotency_key(self) -> str:
        if not self._billable_idempotency_key:
            self._billable_idempotency_key = str(uuid.uuid4())[:128]
        return self._billable_idempotency_key

    def clear_billable_idempotency_key(self) -> None:
        self._billable_idempotency_key = None

    def quota_summary_text(self) -> str:
        if self.has_paid_license():
            return "Using paid licence — shared across large area and one-click."
        rem = self.uses_remaining
        total = self.uses_total
        pending = len(trial_helpers.load_pending_usage_keys())
        if total is not None and rem is not None:
            txt = f"{rem} of {total} trial runs remaining."
        elif rem is not None:
            txt = f"{rem} trial runs remaining."
        else:
            txt = f"Trial status: {self.trial_status or 'unknown'}"
        if pending:
            txt += f" ({pending} export(s) waiting to sync.)"
        return txt


_shared: TrialAccess | None = None


def shared() -> TrialAccess:
    global _shared
    if _shared is None:
        _shared = TrialAccess()
    return _shared


def refresh_trial_state_async(
    on_success: Callable[[dict], None],
    on_failed: Callable[[str], None],
    parent=None,
):
    """Start background trial state fetch. Returns the worker thread."""
    from .trial_workers import TrialStateFetchWorker

    acc = shared()
    worker = TrialStateFetchWorker(acc.install_key, acc.trial_id, parent=parent)

    def _ok(data):
        acc.apply_server_state(data)
        on_success(data)

    worker.finished_ok.connect(_ok)
    worker.failed.connect(on_failed)
    worker.start()
    return worker
