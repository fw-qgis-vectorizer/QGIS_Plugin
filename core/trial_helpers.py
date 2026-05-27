# -*- coding: utf-8 -*-
"""Trial API helpers: install key, server quota state, key issuance, usage debit."""

from __future__ import annotations

import json
import os
import time
import uuid
from urllib.parse import urlencode

import requests
from qgis.core import QgsApplication, QgsMessageLog, QgsSettings, Qgis

from .api_config import ApiRoutes


SETTINGS_GROUP = "vec_plugin"


def _plugin_version():
    try:
        meta = os.path.join(os.path.dirname(__file__), "..", "metadata.txt")
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
_TRIAL_ESTABLISHED_KEY = "trial_established"


def mark_trial_established() -> None:
    """User explicitly generated or validated a trial key (not silent server bootstrap)."""
    s = QgsSettings()
    s.beginGroup(SETTINGS_GROUP)
    s.setValue(_TRIAL_ESTABLISHED_KEY, True)
    s.endGroup()


def is_trial_established() -> bool:
    s = QgsSettings()
    s.beginGroup(SETTINGS_GROUP)
    value = s.value(_TRIAL_ESTABLISHED_KEY, False, type=bool)
    s.endGroup()
    return bool(value)


def clear_trial_established() -> None:
    s = QgsSettings()
    s.beginGroup(SETTINGS_GROUP)
    s.remove(_TRIAL_ESTABLISHED_KEY)
    s.endGroup()


def set_trial_generate_locked_trial_id(trial_id: str) -> None:
    """Lock the current trial id after first successful activation until exhausted."""
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


def trial_id_from_response(data: dict | None) -> str:
    """Extract trial UUID from server JSON (supports alternate key names)."""
    if not isinstance(data, dict):
        return ""
    for key in ("trial_id", "trial_key", "trial_key_id"):
        value = data.get(key)
        if value:
            return str(value).strip()
    return ""


def normalize_trial_response(data: dict) -> dict:
    """Ensure canonical trial_id + trial_state keys for apply_server_state."""
    if not isinstance(data, dict):
        return data
    tid = trial_id_from_response(data)
    if tid:
        data["trial_id"] = tid
    state = data.get("trial_state") or data.get("trial_status")
    if state:
        data["trial_state"] = str(state)
    return data


def request_trial_generate(inference_base_url, install_key, timeout=15) -> dict:
    """Ask the server to issue a new trial for this install_key (must return trial_id)."""
    if not (install_key or "").strip():
        raise Exception("MISSING_INSTALL_KEY: install_key is required")

    url = ApiRoutes.qgis_trial_generate(inference_base_url)
    body = {"install_key": install_key.strip()}
    body.update(client_telemetry())
    try:
        r = requests.post(url, json=body, timeout=timeout)
    except requests.exceptions.RequestException as e:
        raise Exception(f"Trial generate request failed: {e}") from e

    if r.status_code == 404:
        return fetch_trial_state(
            inference_base_url, install_key, trial_id=None, timeout=timeout
        )

    if r.status_code == 503:
        raise Exception(
            "Trials are temporarily unavailable (server). Please try again later or use a paid license key."
        )
    if r.status_code >= 400:
        err = _parse_error_body(r)
        raise Exception(err or f"Trial generate failed (HTTP {r.status_code}).")

    try:
        data = r.json()
    except ValueError as e:
        raise Exception("Trial generate returned invalid JSON.") from e

    data = normalize_trial_response(data)
    if not trial_id_from_response(data):
        raise Exception(
            "Trial generate succeeded but the server did not return a trial_id."
        )
    return data


def fetch_trial_state(inference_base_url, install_key, trial_id=None, timeout=5):
    """Read trial quota from the server (never decrements uses)."""
    if not (install_key or "").strip():
        raise Exception("MISSING_INSTALL_KEY: install_key is required")

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
    url = f"{ApiRoutes.qgis_trial_state(inference_base_url)}?{urlencode(params)}"
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

    return normalize_trial_response(data)


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


def network_reachable(timeout=3):
    """Quick connectivity check (inference host HEAD/GET)."""
    try:
        from .api_config import INFERENCE_BASE_URL

        base = (INFERENCE_BASE_URL or "").rstrip("/")
        if not base:
            return False
        r = requests.get(f"{base}/health", timeout=timeout)
        return r.status_code < 500
    except Exception:
        try:
            from .api_config import INFERENCE_BASE_URL

            base = (INFERENCE_BASE_URL or "").rstrip("/")
            if not base:
                return False
            r = requests.get(base, timeout=timeout)
            return r.status_code < 500
        except Exception:
            return False


def save_cached_trial_state(uses_total, uses_remaining, trial_status):
    s = QgsSettings()
    s.beginGroup(SETTINGS_GROUP)
    if uses_total is not None:
        s.setValue("cached_uses_total", int(uses_total))
    if uses_remaining is not None:
        s.setValue("cached_uses_remaining", int(uses_remaining))
    if trial_status is not None:
        s.setValue("cached_trial_status", str(trial_status))
    s.endGroup()


def load_cached_trial_state():
    s = QgsSettings()
    s.beginGroup(SETTINGS_GROUP)
    total = s.value("cached_uses_total", None)
    rem = s.value("cached_uses_remaining", None)
    status = s.value("cached_trial_status", "", type=str) or None
    s.endGroup()
    out = {}
    if total is not None and str(total).strip() != "":
        try:
            out["uses_total"] = int(total)
        except (TypeError, ValueError):
            pass
    if rem is not None and str(rem).strip() != "":
        try:
            out["uses_remaining"] = int(rem)
        except (TypeError, ValueError):
            pass
    if status:
        out["trial_status"] = status
    return out


def load_pending_usage_keys():
    s = QgsSettings()
    s.beginGroup(SETTINGS_GROUP)
    raw = s.value("pending_usage_keys", "[]", type=str) or "[]"
    s.endGroup()
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def save_pending_usage_keys(entries):
    s = QgsSettings()
    s.beginGroup(SETTINGS_GROUP)
    s.setValue("pending_usage_keys", json.dumps(entries or []))
    s.endGroup()


def append_pending_usage(idempotency_key, action="one_click_export"):
    pending = load_pending_usage_keys()
    key = (idempotency_key or "")[:128]
    if not key:
        return
    if any(e.get("idempotency_key") == key for e in pending):
        return
    pending.append({"idempotency_key": key, "action": action})
    save_pending_usage_keys(pending)


def save_paid_license(jwt_token, license_key, token_expiry=None):
    s = QgsSettings()
    s.beginGroup(SETTINGS_GROUP)
    s.setValue("paid_jwt_token", jwt_token or "")
    s.setValue("paid_license_key", license_key or "")
    if token_expiry is not None:
        s.setValue("paid_token_expiry", str(token_expiry))
    s.endGroup()


def load_paid_license():
    s = QgsSettings()
    s.beginGroup(SETTINGS_GROUP)
    jwt = s.value("paid_jwt_token", "", type=str) or ""
    key = s.value("paid_license_key", "", type=str) or ""
    expiry_raw = s.value("paid_token_expiry", "", type=str) or ""
    s.endGroup()
    out = {"jwt_token": jwt or None, "license_key": key or None, "token_expiry": expiry_raw or None}
    return out


def clear_paid_license():
    s = QgsSettings()
    s.beginGroup(SETTINGS_GROUP)
    for k in ("paid_jwt_token", "paid_license_key", "paid_token_expiry"):
        s.remove(k)
    s.endGroup()


def post_trial_usage(
    inference_base_url,
    trial_id,
    install_key,
    idempotency_key,
    action=None,
    timeout=30,
    max_transient_retries=3,
):
    """
    Debit one trial use on the server (atomic allow/deny).

    Returns ``(data, safe_to_clear_local_idempotency_key)``.
    """
    url = ApiRoutes.qgis_trial_usage(inference_base_url)
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
    if action:
        body["action"] = str(action)
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
