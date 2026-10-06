# -*- coding: utf-8 -*-
"""FieldWatch account login (QGIS plugin sign-in against inference /auth/login)."""

from __future__ import annotations

import time

import requests

from .account_register import is_valid_register_email, normalize_register_email
from .api_config import INFERENCE_BASE_URL, ApiRoutes


def login_form_tooltip(*, email: str = "", password: str = "") -> str:
    """Tooltip for the Sign In button when it stays disabled."""
    if not (email or "").strip():
        return "Please enter your email address."
    if not is_valid_register_email(email):
        return "Please enter a valid email address."
    if not (password or ""):
        return "Please enter your password."
    return ""


def is_login_form_valid(*, email: str, password: str) -> bool:
    return not login_form_tooltip(email=email, password=password)


def _parse_login_error(data, status_code: int, response_text: str = "") -> str:
    if isinstance(data, dict):
        err = data.get("error") or data.get("message") or data.get("detail")
        if isinstance(err, list):
            err = "; ".join(str(x) for x in err)
        if err:
            return str(err)
    text = (response_text or "")[:400]
    if text:
        return text
    return f"Sign in failed (HTTP {status_code})."


def post_fieldwatch_login(
    *,
    email: str,
    password: str,
    inference_base_url: str | None = None,
    timeout: int = 30,
    connect_timeout: int = 10,
    max_transient_retries: int = 3,
) -> dict:
    """
    Sign in via POST /auth/login on the inference backend.

    Returns the parsed JSON body on success (token, email, names, …).
    Raises Exception with a user-facing message on failure.
    """
    if not is_login_form_valid(email=email, password=password):
        raise Exception("Please enter your email and password.")

    base = (inference_base_url or INFERENCE_BASE_URL or "").strip() or INFERENCE_BASE_URL
    url = ApiRoutes.auth_login(base)
    body = {
        "email": normalize_register_email(email),
        "password": password or "",
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "QGIS-VEC-Plugin/1.0",
    }

    attempts = max(1, int(max_transient_retries))
    response = None
    for attempt in range(attempts):
        try:
            response = requests.post(
                url,
                json=body,
                headers=headers,
                timeout=(connect_timeout, timeout),
            )
            break
        except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as exc:
            if attempt + 1 < attempts:
                time.sleep(1.0 * (attempt + 1))
                continue
            raise Exception(
                "Could not reach the FieldWatch server to sign in. "
                "Check your internet connection and try again."
            ) from exc
        except requests.exceptions.ReadTimeout as exc:
            raise Exception(
                "The server took too long to respond while signing in. Please try again."
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise Exception(f"Sign in request failed: {exc}") from exc

    try:
        data = response.json() if response.text else {}
    except ValueError:
        data = {}

    if 200 <= response.status_code < 300:
        if not isinstance(data, dict) or not data.get("token"):
            raise Exception("Sign in succeeded but the server returned an incomplete response.")
        return data

    raise Exception(_parse_login_error(data, response.status_code, response.text))
