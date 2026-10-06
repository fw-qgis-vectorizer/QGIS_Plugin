# -*- coding: utf-8 -*-
"""FieldWatch platform account registration (QGIS plugin signup source)."""

from __future__ import annotations

import time

import requests

from .api_config import FIELDWATCH_REGISTER_URL

FIELDWATCH_SIGNUP_SOURCE = "qgis_plugin"
DEFAULT_REASON_FOR_USE = (
    "Using the FieldWatch QGIS plugin for vectorization workflows."
)
MIN_PASSWORD_LENGTH = 12


def normalize_register_email(raw_email: str) -> str:
    return (raw_email or "").strip().lower()


def is_valid_register_email(email: str) -> bool:
    email = normalize_register_email(email)
    if not email or "@" not in email:
        return False
    local, _, domain = email.partition("@")
    return bool(local and domain and "." in domain)


def _strip(value: str) -> str:
    return (value or "").strip()


def registration_form_tooltip(
    *,
    first_name: str = "",
    last_name: str = "",
    email: str = "",
    password: str = "",
    company: str = "",
    reason_for_use: str = "",
    terms_accepted: bool = False,
) -> str:
    """Tooltip for the onboarding OK button when it stays disabled."""
    if not _strip(first_name):
        return "Please enter your first name."
    if not _strip(last_name):
        return "Please enter your last name."
    if not _strip(email):
        return "Please enter your email address."
    if not is_valid_register_email(email):
        return "Please enter a valid email address."
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if not _strip(company):
        return "Please enter your company name."
    if not _strip(reason_for_use):
        return "Please tell us how you plan to use FieldWatch."
    if not terms_accepted:
        return "You must accept the terms and conditions."
    return ""


def is_registration_form_valid(
    *,
    first_name: str,
    last_name: str,
    email: str,
    password: str,
    company: str,
    reason_for_use: str,
    terms_accepted: bool,
) -> bool:
    return not registration_form_tooltip(
        first_name=first_name,
        last_name=last_name,
        email=email,
        password=password,
        company=company,
        reason_for_use=reason_for_use,
        terms_accepted=terms_accepted,
    )


def build_registration_payload(
    *,
    first_name: str,
    last_name: str,
    email: str,
    password: str,
    company: str,
    reason_for_use: str,
    marketing_consent: bool = False,
) -> dict:
    return {
        "firstName": _strip(first_name),
        "lastName": _strip(last_name),
        "email": normalize_register_email(email),
        "password": password or "",
        "company": _strip(company),
        "reasonForUse": _strip(reason_for_use) or DEFAULT_REASON_FOR_USE,
        "marketingConsent": bool(marketing_consent),
        "signupSource": FIELDWATCH_SIGNUP_SOURCE,
    }


def _parse_register_error(data, status_code: int, response_text: str = "") -> str:
    if isinstance(data, dict):
        err = data.get("error") or data.get("message") or data.get("detail")
        if isinstance(err, list):
            err = "; ".join(str(x) for x in err)
        if err:
            return str(err)
    text = (response_text or "")[:400]
    if text:
        return text
    return f"Registration failed (HTTP {status_code})."


def post_fieldwatch_register(
    *,
    first_name: str,
    last_name: str,
    email: str,
    password: str,
    company: str,
    reason_for_use: str,
    marketing_consent: bool = False,
    timeout: int = 30,
    connect_timeout: int = 10,
    max_transient_retries: int = 3,
) -> dict:
    """
    Create a FieldWatch user account via POST /api/auth/register.

    QGIS plugin signups use ``signupSource=qgis_plugin`` for auto-approval.

    Network handling: a split ``(connect, read)`` timeout makes an unreachable
    host fail fast (``connect_timeout``) instead of blocking for the full read
    window. Connection failures and *connect* timeouts (the request never
    reached the server) are retried with linear backoff. A *read* timeout is
    NOT retried: registration is not idempotent server-side, so retrying after
    the server may have already created the account would surface a spurious
    "email already exists" error.
    """
    if not is_registration_form_valid(
        first_name=first_name,
        last_name=last_name,
        email=email,
        password=password,
        company=company,
        reason_for_use=reason_for_use,
        terms_accepted=True,
    ):
        raise Exception("Please complete all required fields.")

    body = build_registration_payload(
        first_name=first_name,
        last_name=last_name,
        email=email,
        password=password,
        company=company,
        reason_for_use=reason_for_use,
        marketing_consent=marketing_consent,
    )

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "QGIS-VEC-Plugin/1.0",
    }

    attempts = max(1, int(max_transient_retries))
    response = None
    for attempt in range(attempts):
        try:
            response = requests.post(
                FIELDWATCH_REGISTER_URL,
                json=body,
                headers=headers,
                timeout=(connect_timeout, timeout),
            )
            break
        except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as exc:
            # Request never reached the server -> safe to retry.
            if attempt + 1 < attempts:
                time.sleep(1.0 * (attempt + 1))
                continue
            raise Exception(
                "Could not reach the FieldWatch server to create your account. "
                "Check your internet connection and try again."
            ) from exc
        except requests.exceptions.ReadTimeout as exc:
            # Server may already have processed the signup -> do NOT retry.
            raise Exception(
                "The server took too long to respond while creating your account. "
                "Your account may already have been created. Please wait a moment, "
                "then try signing in or try again."
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise Exception(f"Registration request failed: {exc}") from exc

    try:
        data = response.json() if response.text else {}
    except ValueError:
        data = {}

    if 200 <= response.status_code < 300:
        return data if isinstance(data, dict) else {}

    raise Exception(
        _parse_register_error(data, response.status_code, response.text)
    )
