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
_FIELDWATCH_ROOT = os.path.join(os.path.expanduser("~"), ".qgis_fieldwatch")
_INSTALL_KEY_FILENAME = "install_key"


def _fieldwatch_root() -> str:
    os.makedirs(_FIELDWATCH_ROOT, exist_ok=True)
    return _FIELDWATCH_ROOT


def _install_key_file_path() -> str:
    return os.path.join(_fieldwatch_root(), _INSTALL_KEY_FILENAME)


def _is_valid_install_key(value: str) -> bool:
    try:
        uuid.UUID(str(value or "").strip())
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _read_install_key_file() -> str:
    path = _install_key_file_path()
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return (f.read() or "").strip()
    except OSError:
        return ""


def _write_install_key_file(key: str) -> None:
    path = _install_key_file_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write(key.strip())


def _read_install_key_qsettings() -> str:
    s = QgsSettings()
    s.beginGroup(SETTINGS_GROUP)
    key = s.value("install_key", "", type=str) or ""
    s.endGroup()
    return key.strip()


def _write_install_key_qsettings(key: str) -> None:
    s = QgsSettings()
    s.beginGroup(SETTINGS_GROUP)
    s.setValue("install_key", key.strip())
    s.endGroup()


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
    """
    Return stable machine-wide install_key (creates and stores if missing).

    Source of truth: ~/.qgis_fieldwatch/install_key
    Legacy profiles migrate from QSettings on first run after update.
    """
    key = _read_install_key_file()
    if _is_valid_install_key(key):
        return key

    legacy = _read_install_key_qsettings()
    if _is_valid_install_key(legacy):
        _write_install_key_file(legacy)
        QgsMessageLog.logMessage(
            "Migrated install_key from QSettings to ~/.qgis_fieldwatch/install_key",
            "FieldWatch",
            Qgis.Info,
        )
        return legacy

    key = str(uuid.uuid4())
    _write_install_key_file(key)
    _write_install_key_qsettings(key)
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


_ONBOARDING_COMPLETE_KEY = "onboarding_complete"
_USER_EMAIL_KEY = "user_email"
_ONBOARDING_COMPLETED_AT_KEY = "onboarding_completed_at"
_ONBOARDING_LEGACY_SKIP_KEY = "onboarding_legacy_skip"


def _settings_bool(raw) -> bool:
    return raw in (True, "true", "True", "1", 1)


def is_valid_email(email: str) -> bool:
    """Minimal email check for onboarding (non-empty local@domain.tld)."""
    email = normalize_trial_email(email)
    if not email or "@" not in email:
        return False
    local, _, domain = email.partition("@")
    return bool(local and domain and "." in domain)


def normalize_trial_email(raw_email: str) -> str:
    """Server-normalized email (strip + lowercase)."""
    return (raw_email or "").strip().lower()


def is_onboarding_complete() -> bool:
    """
    True when this QGIS profile finished onboarding: terms accepted, email posted,
    and OK clicked (``onboarding_complete`` + stored email). Legacy upgrades may
    skip without email via ``onboarding_legacy_skip``.
    """
    s = QgsSettings()
    s.beginGroup(SETTINGS_GROUP)
    complete = _settings_bool(s.value(_ONBOARDING_COMPLETE_KEY, False))
    legacy_skip = _settings_bool(s.value(_ONBOARDING_LEGACY_SKIP_KEY, False))
    email = (s.value(_USER_EMAIL_KEY, "", type=str) or "").strip()
    s.endGroup()
    if not complete:
        return False
    if legacy_skip:
        return True
    return is_valid_email(email)


def get_onboarding_completed_at() -> str:
    s = QgsSettings()
    s.beginGroup(SETTINGS_GROUP)
    value = (s.value(_ONBOARDING_COMPLETED_AT_KEY, "", type=str) or "").strip()
    s.endGroup()
    return value


def get_stored_user_email() -> str:
    s = QgsSettings()
    s.beginGroup(SETTINGS_GROUP)
    email = s.value(_USER_EMAIL_KEY, "", type=str) or ""
    s.endGroup()
    return email.strip()


def save_onboarding(email: str) -> None:
    """Persist onboarding completion after successful POST /qgis/trial."""
    from datetime import datetime, timezone

    email = normalize_trial_email(email)
    completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = QgsSettings()
    s.beginGroup(SETTINGS_GROUP)
    s.setValue(_USER_EMAIL_KEY, email)
    s.setValue(_ONBOARDING_COMPLETE_KEY, True)
    s.setValue(_ONBOARDING_COMPLETED_AT_KEY, completed_at)
    s.remove(_ONBOARDING_LEGACY_SKIP_KEY)
    s.endGroup()
    s.sync()


_ONBOARDING_MIGRATION_KEY = "onboarding_migrated_v1"


def maybe_migrate_legacy_onboarding() -> None:
    """
    One-time upgrade path: existing users keep skipping onboarding; new profiles do not.
    Must run before auto trial bootstrap on first dialog open.
    """
    s = QgsSettings()
    s.beginGroup(SETTINGS_GROUP)
    if s.value(_ONBOARDING_MIGRATION_KEY, False) in (True, "true", "True", "1", 1):
        s.endGroup()
        return
    if not _settings_bool(s.value(_ONBOARDING_COMPLETE_KEY, False)):
        if is_trial_established() or load_paid_license().get("jwt_token"):
            s.setValue(_ONBOARDING_COMPLETE_KEY, True)
            s.setValue(_ONBOARDING_LEGACY_SKIP_KEY, True)
    s.setValue(_ONBOARDING_MIGRATION_KEY, True)
    s.endGroup()
    s.sync()


def bootstrap_trial_if_needed(inference_base_url, install_key, acc, timeout=15) -> None:
    """After onboarding, ensure trial state exists before a billable workflow runs."""
    if acc.has_paid_license():
        return
    if (acc.trial_id or get_stored_trial_id()) and acc.uses_remaining is not None:
        return
    data = request_trial_generate(inference_base_url, install_key, timeout=timeout)
    data = normalize_trial_response(dict(data))
    acc.apply_server_state(data)
    if trial_id_from_response(data):
        mark_trial_established()


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
    """Bootstrap trial for this install_key via GET /qgis/trial/state (server issues trial_id)."""
    if not (install_key or "").strip():
        raise Exception("MISSING_INSTALL_KEY: install_key is required")

    data = fetch_trial_state(
        inference_base_url, install_key, trial_id=None, timeout=timeout
    )
    if not trial_id_from_response(data):
        raise Exception(
            "Trial bootstrap succeeded but the server did not return a trial_id."
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


def post_trial_email(
    inference_base_url,
    install_key,
    email,
    trial_id=None,
    timeout=25,
    ensure_trial_id=True,
):
    """
    POST /qgis/trial — store email lead (first-run signup or trial exhausted).

    See qgis_trial.md endpoint 3.
    """
    key = (install_key or "").strip()
    if not key:
        raise Exception("MISSING_INSTALL_KEY: install_key is required")
    addr = normalize_trial_email(email)
    if not is_valid_email(addr):
        raise Exception("INVALID_EMAIL: Enter a valid email address.")

    tid = (trial_id or get_stored_trial_id() or "").strip()
    if ensure_trial_id and not tid:
        try:
            state = fetch_trial_state(
                inference_base_url, key, trial_id=None, timeout=timeout
            )
            tid = trial_id_from_response(state)
            if tid:
                set_stored_trial_id(tid)
        except Exception:
            pass

    url = ApiRoutes.qgis_trial_email(inference_base_url)
    body = {
        "email": addr,
        "install_key": key,
    }
    if tid:
        body["trial_id"] = tid
    body.update(client_telemetry())

    try:
        r = requests.post(url, json=body, timeout=timeout)
    except requests.exceptions.RequestException as e:
        raise Exception(f"Email signup request failed: {e}") from e

    try:
        data = r.json() if r.text else {}
    except ValueError:
        data = {}

    if r.status_code == 201:
        return data

    err = data.get("message") or data.get("detail")
    if isinstance(err, list):
        err = "; ".join(str(x) for x in err)
    err = err or r.text[:400] or f"HTTP {r.status_code}"
    raise Exception(err)


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
