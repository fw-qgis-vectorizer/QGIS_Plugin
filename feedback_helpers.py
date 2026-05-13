# -*- coding: utf-8 -*-
"""POST /qgis/feedback — server sets submitted_at; client never sends a clock field."""

from __future__ import annotations

import requests
from qgis.core import QgsMessageLog, Qgis

from .api_config import ApiRoutes
from .trial_helpers import client_telemetry

FEEDBACK_MESSAGE_MAX_LEN = 1500


def post_feedback(
    inference_base_url,
    message,
    install_key=None,
    trial_id=None,
    timeout=25,
):
    """
    POST /qgis/feedback.

    Body: ``message`` (required, max 1500 chars). Optional: ``install_key``, ``trial_id``,
    ``plugin_version``, ``qgis_version``. Do not send submitted_at.

    :returns: Parsed 201 JSON (e.g. id, submitted_at, ok).
    :raises: Exception on network or non-success HTTP.
    """
    base = inference_base_url.rstrip("/")
    url = ApiRoutes.qgis_feedback(base)
    text = (message or "").strip()
    if not text:
        raise ValueError("Feedback message is empty.")
    if len(text) > FEEDBACK_MESSAGE_MAX_LEN:
        text = text[:FEEDBACK_MESSAGE_MAX_LEN]

    body = {"message": text}
    if install_key:
        body["install_key"] = str(install_key).strip()
    tid = (trial_id or "").strip()
    if tid:
        body["trial_id"] = tid
    body.update(client_telemetry())

    try:
        r = requests.post(url, json=body, timeout=timeout)
    except requests.exceptions.RequestException as e:
        raise Exception(f"Feedback request failed: {e}") from e

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
    QgsMessageLog.logMessage(f"Feedback HTTP {r.status_code}: {err}", "VEC Plugin", Qgis.Warning)
    raise Exception(err)
