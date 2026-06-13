# -*- coding: utf-8 -*-
"""HTTP client for POST /qgis/edit (nano banana / Gemini obstacle removal)."""

from __future__ import annotations

import os

import requests

from qgis.core import Qgis, QgsMessageLog

from .api_config import ApiRoutes, INFERENCE_BASE_URL
from .one_click_log import sanitize_log_line

LOG_CHANNEL = "FieldWatch"
DEFAULT_TIMEOUT = 120


def _parse_error_response(response: requests.Response) -> str:
    try:
        data = response.json()
        detail = data.get("detail", response.text)
        if isinstance(detail, list):
            detail = "; ".join(str(x) for x in detail)
        return str(detail)
    except ValueError:
        return (response.text or f"HTTP {response.status_code}")[:500]


def post_edit(
    crop_path: str,
    prompt: str,
    mask_geojson: str | None,
    *,
    auth_headers: dict | None = None,
    auth_client=None,
    base_url: str = INFERENCE_BASE_URL,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[bytes | None, dict, str]:
    """
    POST one crop to /qgis/edit.

    Pass ``auth_client`` (VecInferenceClient) when possible so 401 can retry
    after JWT refresh. ``auth_headers`` is used when no client is provided.

    Returns (tif_bytes, response_headers_dict, error_message).
    """
    url = ApiRoutes.qgis_edit(base_url)
    if not os.path.isfile(crop_path):
        return None, {}, "Crop file not found."

    data = {"prompt": prompt.strip()}
    if mask_geojson:
        data["mask"] = mask_geojson

    def _request(headers: dict) -> requests.Response:
        headers = dict(headers or {})
        headers.setdefault("User-Agent", "QGIS-FieldWatch-Plugin/1.0")
        with open(crop_path, "rb") as crop_file:
            files = {
                "file": (os.path.basename(crop_path), crop_file, "image/tiff"),
            }
            return requests.post(
                url,
                files=files,
                data=data,
                headers=headers,
                timeout=timeout,
            )

    def _headers() -> dict:
        if auth_client is not None:
            return auth_client._get_auth_headers()
        return dict(auth_headers or {})

    try:
        response = _request(_headers())
    except requests.Timeout:
        return None, {}, "Edit request timed out."
    except OSError as exc:
        return None, {}, str(exc)

    if response.status_code == 401 and auth_client is not None:
        if auth_client._try_revalidate_token():
            try:
                response = _request(_headers())
            except requests.Timeout:
                return None, {}, "Edit request timed out."
            except OSError as exc:
                return None, {}, str(exc)

    hdrs = {
        "edit_id": response.headers.get("X-Edit-Id", ""),
        "model": response.headers.get("X-Edit-Model", ""),
        "processing_seconds": response.headers.get("X-Processing-Seconds", ""),
        "status_code": response.status_code,
    }

    QgsMessageLog.logMessage(
        sanitize_log_line(
            f"POST /qgis/edit → {response.status_code}"
            + (f" ({hdrs['processing_seconds']}s)" if hdrs["processing_seconds"] else "")
        ),
        LOG_CHANNEL,
        Qgis.MessageLevel.Info,
    )

    if response.status_code == 200:
        content = response.content
        if not content:
            return None, hdrs, "Server returned an empty GeoTIFF."
        return content, hdrs, ""

    if response.status_code == 401:
        if auth_client is not None and auth_client._using_trial_headers():
            return (
                None,
                hdrs,
                "Trial authentication failed for edit — validate a paid licence key "
                "or refresh trial status and try again.",
            )
        return None, hdrs, "Session expired — click Validate on your licence key and try again."

    messages = {
        400: _parse_error_response(response),
        413: "Crop too large — draw a smaller box or reduce padding.",
        422: "Crop must be a georeferenced GeoTIFF.",
        503: "Edit service unavailable.",
        500: _parse_error_response(response),
    }
    return None, hdrs, messages.get(response.status_code, _parse_error_response(response))
