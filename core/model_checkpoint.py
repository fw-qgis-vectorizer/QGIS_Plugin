# -*- coding: utf-8 -*-
"""Model weights presence checks and download helpers."""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
from typing import Callable

from qgis.core import Qgis, QgsMessageLog

from .model_config import (
    LOG_CHANNEL,
    get_checkpoint_path,
    get_checkpoints_dir,
    sam2_checkpoint_url,
)
from .one_click_log import sanitize_log_line

_CHUNK_BYTES = 1024 * 1024
_MIN_CHECKPOINT_BYTES = 1_000_000
_USER_AGENT = "FieldWatch-QGIS-Plugin/1.0"


def _http_download_stream(
    url: str,
    temp_path: str,
    *,
    progress_callback: Callable[[int, str], None] | None = None,
) -> tuple[bool, str]:
    """Stream URL to temp_path with optional resume (Range). Returns (ok, error)."""
    resume_offset = 0
    if os.path.isfile(temp_path):
        resume_offset = os.path.getsize(temp_path)

    headers = {"User-Agent": _USER_AGENT}
    if resume_offset > 0:
        headers["Range"] = f"bytes={resume_offset}-"

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            status = getattr(response, "status", response.getcode())
            if resume_offset > 0 and status == 200:
                # Server ignored Range; restart from scratch.
                resume_offset = 0
                if os.path.isfile(temp_path):
                    os.remove(temp_path)

            content_length = response.headers.get("Content-Length")
            total_bytes = 0
            if content_length:
                try:
                    total_bytes = int(content_length)
                    if status == 206 and resume_offset > 0:
                        total_bytes += resume_offset
                except ValueError:
                    total_bytes = 0

            mode = "ab" if resume_offset > 0 else "wb"
            downloaded = resume_offset
            with open(temp_path, mode) as handle:
                while True:
                    chunk = response.read(_CHUNK_BYTES)
                    if not chunk:
                        break
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        if total_bytes > 0:
                            pct = int((downloaded / total_bytes) * 90) + 5
                            progress_callback(
                                min(pct, 95),
                                f"Downloading: {downloaded / (1024 * 1024):.1f} MB",
                            )
                        else:
                            progress_callback(
                                50,
                                f"Downloading: {downloaded / (1024 * 1024):.1f} MB",
                            )
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and resume_offset > 0:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            return False, "resume_reset"
        return False, f"HTTP {exc.code}: {exc.reason}"
    except urllib.error.URLError as exc:
        return False, str(exc.reason if exc.reason else exc)
    except OSError as exc:
        return False, str(exc)

    try:
        size = os.path.getsize(temp_path)
    except OSError as exc:
        return False, str(exc)

    if size < _MIN_CHECKPOINT_BYTES:
        return False, "Downloaded file is too small."
    return True, ""


def download_checkpoint_http(
    url: str,
    dest_path: str,
    progress_callback: Callable[[int, str], None] | None = None,
) -> tuple[bool, str]:
    """Download checkpoint over HTTPS (stdlib; safe inside QThread workers)."""
    temp_path = dest_path + ".tmp"
    os.makedirs(get_checkpoints_dir(), exist_ok=True)

    if progress_callback:
        progress_callback(0, "Connecting to download server…")

    last_error = ""
    for attempt in range(1, 4):
        if progress_callback and attempt > 1:
            progress_callback(5, f"Retrying download ({attempt}/3)…")

        ok, err = _http_download_stream(
            url,
            temp_path,
            progress_callback=progress_callback,
        )
        if err == "resume_reset":
            ok, err = _http_download_stream(
                url,
                temp_path,
                progress_callback=progress_callback,
            )

        if ok:
            try:
                if os.path.exists(dest_path):
                    os.remove(dest_path)
                os.replace(temp_path, dest_path)
            except OSError as exc:
                return False, str(exc)
            if progress_callback:
                progress_callback(100, "Download complete.")
            return True, "Weights downloaded successfully."

        last_error = err or "Download failed."
        try:
            if os.path.isfile(temp_path) and os.path.getsize(temp_path) < _MIN_CHECKPOINT_BYTES:
                os.remove(temp_path)
        except OSError:
            pass

        if attempt < 3:
            time.sleep(2 * attempt)

    return False, last_error


def download_checkpoint(
    progress_callback: Callable[[int, str], None] | None = None,
) -> tuple[bool, str]:
    """Download segmentation weights from FieldWatch GCS hosting only."""
    dest = get_checkpoint_path()
    os.makedirs(get_checkpoints_dir(), exist_ok=True)

    gcs_url = sam2_checkpoint_url()
    if progress_callback:
        progress_callback(5, "Downloading weights from FieldWatch…")
    ok, msg = download_checkpoint_http(gcs_url, dest, progress_callback)
    if ok:
        return ok, msg

    safe_msg = sanitize_log_line(msg or "")
    QgsMessageLog.logMessage(
        f"Weight download failed: {safe_msg}",
        LOG_CHANNEL,
        Qgis.MessageLevel.Warning,
    )
    return (
        False,
        safe_msg or "Download failed. Check your network connection and try again.",
    )
