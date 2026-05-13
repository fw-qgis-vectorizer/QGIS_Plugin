# -*- coding: utf-8 -*-
"""Trial API helpers: install_key persistence, GET /qgis/trial/state, POST /qgis/trial/usage."""

from __future__ import annotations

import os
import time
import uuid
from urllib.parse import urlencode

import requests
from qgis.core import QgsApplication, QgsMessageLog, QgsSettings, Qgis


SETTINGS_GROUP = "vec_plugin"


def _plugin_version():
    try:
        meta = os.path.join(os.path.dirname(__file__), "metadata.txt")
        with open(meta, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("version="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return "unknown"


def _safe_qgis_version():
    """
    QGIS version string for server telemetry. Never raises.

    Do not use QgsApplication.version — it does not exist in PyQGIS (AttributeError).
    Prefer QgsApplication.qgisVersion() (static), then Qgis.QGIS_VERSION, then env.
    """
    # Static API (QGIS 3.x)
    qgis_version_fn = getattr(QgsApplication, "qgisVersion", None)
    if callable(qgis_version_fn):
        try:
            qv = qgis_version_fn() or ""
            if isinstance(qv, str) and qv.strip():
                return qv.strip()
        except Exception:
            pass
    inst = getattr(QgsApplication, "instance", lambda: None)()
    if inst is not None:
        inst_fn = getattr(inst, "qgisVersion", None)
        if callable(inst_fn):
            try:
                qv = inst_fn() or ""
                if isinstance(qv, str) and qv.strip():
                    return qv.strip()
            except Exception:
                pass
    try:
        qv = str(getattr(Qgis, "QGIS_VERSION", "") or "").strip()
        if qv:
            return qv
    except Exception:
        pass
    for envk in ("QGIS_VERSION", "RELEASE_NAME"):
        ev = (os.environ.get(envk) or "").strip()
        if ev:
            return ev
    return "unknown"


def client_telemetry():
    """plugin_version + qgis_version for trial/feedback APIs. Must not break HTTP helpers."""
    try:
        return {
            "plugin_version": _plugin_version(),
            "qgis_version": _safe_qgis_version(),
        }
    except Exception:
        return {"plugin_version": _plugin_version(), "qgis_version": "unknown"}


def ensure_install_key():
    """Return stable per-profile install_key (creates and stores if missing)."""
    s = QgsSettings()
    s.beginGroup(SETTINGS_GROUP)
    key = s.value("install_key", "", type=str) or ""
    if not key:
        key = str(uuid.uuid4())
        s.setValue("install_key", key)
    s.endGroup()
    return key


def get_stored_trial_id():
    s = QgsSettings()
    s.beginGroup(SETTINGS_GROUP)
    tid = s.value("trial_id", "", type=str) or ""
    s.endGroup()
    return tid


def set_stored_trial_id(trial_id):
    if not trial_id:
        return
    s = QgsSettings()
    s.beginGroup(SETTINGS_GROUP)
    s.setValue("trial_id", trial_id)
    s.endGroup()


def clear_stored_trial_id():
    """Remove persisted server trial_id (e.g. after failed paste validation)."""
    s = QgsSettings()
    s.beginGroup(SETTINGS_GROUP)
    s.remove("trial_id")
    s.endGroup()


_TRIAL_GENERATE_LOCK_KEY = "trial_generate_locked_trial_id"


def set_trial_generate_locked_trial_id(trial_id: str) -> None:
    """After user uses Generate Trial Key once for this trial_id, disable repeat until exhausted."""
    tid = (trial_id or "").strip()
    if not tid:
        return
    s = QgsSettings()
    s.beginGroup(SETTINGS_GROUP)
    s.setValue(_TRIAL_GENERATE_LOCK_KEY, tid)
    s.endGroup()


def clear_trial_generate_locked_trial_id() -> None:
    s = QgsSettings()
    s.beginGroup(SETTINGS_GROUP)
    s.remove(_TRIAL_GENERATE_LOCK_KEY)
    s.endGroup()


def is_trial_generate_locked_for_current_trial(current_trial_id: str | None) -> bool:
    tid = (current_trial_id or "").strip()
    if not tid:
        return False
    s = QgsSettings()
    s.beginGroup(SETTINGS_GROUP)
    locked = (s.value(_TRIAL_GENERATE_LOCK_KEY, "", type=str) or "").strip()
    s.endGroup()
    return locked == tid


def fetch_trial_state(inference_base_url, install_key, trial_id=None, timeout=5):
    """
    GET /qgis/trial/state — read-only; never decrements uses.

    Query: install_key (required), trial_id (optional UUID), plugin_version, qgis_version.
    Success 200 JSON keys: trial_id, uses_total, uses_remaining, trial_state, server_time.
    Response may include Cache-Control: no-store (server); auth none.

    :returns: dict with trial_id, uses_total, uses_remaining, trial_state, server_time
    :raises: Exception on HTTP/network errors
    """
    if not (install_key or "").strip():
        raise Exception("MISSING_INSTALL_KEY: install_key is required")

    base = inference_base_url.rstrip("/")
    params = {"install_key": install_key.strip()}
    tid = (trial_id or "").strip()
    if tid:
        try:
            uuid.UUID(tid)
            params["trial_id"] = tid
        except (ValueError, TypeError):
            # Ignore corrupt stored trial_id; bootstrap by install_key only.
            pass
    params.update(client_telemetry())
    url = f"{base}/qgis/trial/state?{urlencode(params)}"
    try:
        r = requests.get(url, timeout=timeout)
    except requests.exceptions.RequestException as e:
        raise Exception(f"Trial status request failed: {e}") from e

    if r.status_code == 503:
        raise Exception(
            "Trials are temporarily unavailable (server). Please try again later or use a paid license key."
        )
    if r.status_code >= 400:
        err = _parse_error_body(r)
        raise Exception(err or f"Trial status failed (HTTP {r.status_code}).")

    try:
        data = r.json()
    except ValueError as e:
        raise Exception("Trial status returned invalid JSON.") from e

    return data


def _usage_terminal_safe_to_clear_client_idempotency_key(status_code, data):
    """
    When True, the plugin may discard its stored billable idempotency key.

    Conservative rule: only after a definitive successful HTTP tier (2xx) or an explicit
    denial (4xx with ``allowed`` exactly false). Never after 5xx (server may have debited
    and still returned an error/gateway page).
    """
    if not isinstance(data, dict):
        return False
    if 200 <= status_code < 300:
        return True
    if status_code >= 500:
        return False
    if 400 <= status_code < 500 and data.get("allowed") is False:
        return True
    return False


def post_trial_usage(
    inference_base_url,
    trial_id,
    install_key,
    idempotency_key,
    timeout=30,
    max_transient_retries=3,
):
    """
    POST /qgis/trial/usage — atomic consume / deny.

    JSON body: trial_id, install_key, idempotency_key (max 128 chars on server), optional
    plugin_version, qgis_version. ``action`` omitted (server defaults to infer).
    Content-Type: application/json. Auth: none.

    Idempotency: same (trial_id, idempotency_key) must receive the same JSON as the first
    response; on transient network errors we retry with the **same** idempotency_key.

    :returns: ``(data, safe_to_clear_local_idempotency_key)`` where ``data`` is the parsed
        JSON body. ``safe_to_clear_local_idempotency_key`` is True only for definitive
        outcomes (2xx, or 4xx with ``allowed: false``); False on connection/timeout
        exhaustion (caller raised), and never True for 5xx (caller raises after retries).
    """
    base = inference_base_url.rstrip("/")
    url = f"{base}/qgis/trial/usage"
    # Server max length 128; standard UUID is 36 chars.
    idem = (idempotency_key or "")[:128]
    if not idem:
        raise Exception("idempotency_key is required")

    tid = (trial_id or "").strip()
    try:
        uuid.UUID(tid)
    except (ValueError, TypeError):
        raise Exception("trial_id must be a valid UUID string")

    body = {
        "trial_id": tid,
        "install_key": (install_key or "").strip(),
        "idempotency_key": idem,
    }
    body.update(client_telemetry())

    attempts = max(1, max_transient_retries)
    _retryable_post = (
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        requests.exceptions.ChunkedEncodingError,
        requests.exceptions.ContentDecodingError,
    )
    for attempt in range(attempts):
        try:
            r = requests.post(url, json=body, timeout=timeout)
        except _retryable_post as e:
            if attempt + 1 < attempts:
                time.sleep(1.0 * (attempt + 1))
                continue
            raise Exception(f"Trial usage request failed: {e}") from e

        if r.status_code == 503:
            raise Exception(
                "Trials are temporarily unavailable (server). Please try again later or use a paid license key."
            )

        try:
            data = r.json()
        except ValueError:
            raise Exception(f"Trial usage returned invalid JSON (HTTP {r.status_code}).")

        # 5xx: retry while attempts remain; on last attempt always raise (never "return" a
        # body that might follow a committed debit — client keeps idempotency key).
        if r.status_code >= 500:
            if attempt + 1 < attempts:
                time.sleep(1.0 * (attempt + 1))
                continue
            err = _parse_error_body_from_dict(data) or f"HTTP {r.status_code}"
            QgsMessageLog.logMessage(f"Trial usage error: {err}", "VEC Plugin", Qgis.Warning)
            raise Exception(err)

        # 4xx: explicit denial with allowed == false → definitive; keep other 4xx as errors.
        if r.status_code >= 400:
            if data.get("allowed") is False:
                return data, _usage_terminal_safe_to_clear_client_idempotency_key(r.status_code, data)
            err = _parse_error_body_from_dict(data) or f"HTTP {r.status_code}"
            QgsMessageLog.logMessage(f"Trial usage error: {err}", "VEC Plugin", Qgis.Warning)
            raise Exception(err)

        return data, _usage_terminal_safe_to_clear_client_idempotency_key(r.status_code, data)


def _parse_error_body(response):
    try:
        data = response.json()
        return _parse_error_body_from_dict(data)
    except Exception:
        return (response.text or "")[:300]


def _parse_error_body_from_dict(data):
    if not isinstance(data, dict):
        return None
    code = data.get("error_code") or data.get("error")
    msg = data.get("message") or ""
    retry = data.get("retry_after_seconds")
    suffix = ""
    if retry is not None:
        try:
            suffix = f" (retry_after_seconds={int(retry)})"
        except (TypeError, ValueError):
            suffix = f" (retry_after_seconds={retry!r})"
    if code and msg:
        return f"{code}: {msg}{suffix}"
    if msg or code:
        return f"{msg or ''}{code or ''}{suffix}".strip() or None
    return None
