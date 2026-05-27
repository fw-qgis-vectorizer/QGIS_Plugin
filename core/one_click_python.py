# -*- coding: utf-8 -*-
"""
Download a portable Python 3.12 runtime for one-click segmentation.

Uses astral-sh/python-build-standalone so users do not need a separate
Python install. Works on Windows, macOS, and Linux (x86_64 / arm64).
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess  # nosec B404
import sys
import tarfile
import tempfile
import time
from typing import Callable

from qgis.core import Qgis, QgsBlockingNetworkRequest, QgsMessageLog
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest

from .archive_utils import safe_extract_tar
from .model_config import CACHE_DIR, LOG_CHANNEL, MODEL_MIN_PYTHON
from .subprocess_utils import get_clean_env, run_hidden

# python-build-standalone release (install-only archives).
RELEASE_TAG = "20251014"
TARGET_PYTHON_VERSION = "3.12.12"
TARGET_MAJOR, TARGET_MINOR = MODEL_MIN_PYTHON

STANDALONE_DIR = os.path.join(CACHE_DIR, "python_standalone")


def get_standalone_python_path() -> str:
    python_dir = os.path.join(STANDALONE_DIR, "python")
    if sys.platform == "win32":
        return os.path.join(python_dir, "python.exe")
    return os.path.join(python_dir, "bin", "python3")


def standalone_python_exists() -> bool:
    return os.path.isfile(get_standalone_python_path())


def verify_standalone_python() -> tuple[bool, str]:
    python_path = get_standalone_python_path()
    if not os.path.isfile(python_path):
        return False, "Portable Python not found."

    if sys.platform != "win32":
        try:
            import stat

            os.chmod(
                python_path,
                stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH,
            )
        except OSError:
            pass

    try:
        result = run_hidden(
            [python_path, "-c", "import sys; print(sys.version_info[:2])"],
            capture_output=True,
            text=True,
            timeout=60,
            env=get_clean_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "unknown error")[:200]
        return False, err

    try:
        parts = result.stdout.strip().strip("()").replace(" ", "").split(",")
        major, minor = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return False, "Could not read portable Python version."

    if (major, minor) < MODEL_MIN_PYTHON:
        return False, f"Portable Python {major}.{minor} is below required {TARGET_MAJOR}.{TARGET_MINOR}."

    return True, f"Python {major}.{minor}"


def _get_platform_archive_suffix() -> tuple[str, str]:
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        if machine in ("arm64", "aarch64"):
            return "aarch64-apple-darwin", ".tar.gz"
        return "x86_64-apple-darwin", ".tar.gz"
    if sys.platform == "win32":
        return "x86_64-pc-windows-msvc", ".tar.gz"
    if machine in ("arm64", "aarch64"):
        return "aarch64-unknown-linux-gnu", ".tar.gz"
    return "x86_64-unknown-linux-gnu", ".tar.gz"


def get_download_url() -> str:
    platform_str, ext = _get_platform_archive_suffix()
    filename = f"cpython-{TARGET_PYTHON_VERSION}+{RELEASE_TAG}-{platform_str}-install_only{ext}"
    return (
        f"https://github.com/astral-sh/python-build-standalone/releases/download/"
        f"{RELEASE_TAG}/{filename}"
    )


def remove_standalone_python() -> None:
    if os.path.isdir(STANDALONE_DIR):
        shutil.rmtree(STANDALONE_DIR, ignore_errors=True)


def download_standalone_python(
    progress_callback: Callable[[int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[bool, str]:
    """Download and extract portable Python 3.12 (proxy-aware via QGIS network)."""
    if standalone_python_exists():
        ok, msg = verify_standalone_python()
        if ok:
            return True, msg
        remove_standalone_python()

    if sys.platform == "win32":
        release = platform.release() or ""
        if release in ("7", "Vista", "XP"):
            return (
                False,
                f"Windows {release} is not supported. Windows 10 or later is required.",
            )

    url = get_download_url()
    QgsMessageLog.logMessage(
        f"Downloading portable Python from {url}",
        LOG_CHANNEL,
        level=Qgis.MessageLevel.Info,
    )

    fd, temp_path = tempfile.mkstemp(suffix=".tar.gz")
    os.close(fd)

    try:
        if cancel_check and cancel_check():
            return False, "Download cancelled."

        if progress_callback:
            progress_callback(2, f"Downloading Python {TARGET_PYTHON_VERSION} runtime…")

        qurl = QUrl(url)
        max_retries = 3
        error_msg = ""
        content = None

        for attempt in range(max_retries):
            if cancel_check and cancel_check():
                return False, "Download cancelled."

            request = QgsBlockingNetworkRequest()
            err = request.get(QNetworkRequest(qurl))
            if err == QgsBlockingNetworkRequest.NoError:
                reply = request.reply()
                content = reply.content()
                break
            error_msg = request.errorMessage()
            if "404" in error_msg or "Not Found" in error_msg:
                return False, (
                    f"Python {TARGET_PYTHON_VERSION} is not available for this platform. "
                    "Please report this to FieldWatch support."
                )
            if attempt < max_retries - 1:
                wait = 5 * (2**attempt)
                if progress_callback:
                    progress_callback(5, f"Network error, retrying in {wait}s…")
                time.sleep(wait)

        if content is None:
            return False, f"Download failed: {error_msg[:200]}"

        size = len(content)
        if size < 10 * 1024 * 1024:
            return False, (
                f"Download too small ({size / (1024 * 1024):.1f} MB). "
                "Check firewall or proxy settings."
            )

        if progress_callback:
            progress_callback(30, f"Downloaded {size / (1024 * 1024):.0f} MB, extracting…")

        with open(temp_path, "wb") as fh:
            fh.write(content.data())

        magic = open(temp_path, "rb").read(2)
        if magic != b"\x1f\x8b":
            return False, "Download is not a valid archive (firewall or proxy issue)."

        remove_standalone_python()
        os.makedirs(STANDALONE_DIR, exist_ok=True)

        with tarfile.open(temp_path, "r:gz") as tar:
            safe_extract_tar(tar, STANDALONE_DIR)

        if progress_callback:
            progress_callback(35, "Verifying portable Python…")

        ok, msg = verify_standalone_python()
        if not ok:
            remove_standalone_python()
            return False, f"Portable Python verification failed: {msg}"

        if progress_callback:
            progress_callback(38, msg)

        return True, f"Python {TARGET_PYTHON_VERSION} runtime ready."

    except Exception as exc:
        remove_standalone_python()
        return False, str(exc)[:500]
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


def ensure_standalone_python(
    progress_callback: Callable[[int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[bool, str]:
    """Ensure portable Python exists; download if missing."""
    if standalone_python_exists():
        ok, msg = verify_standalone_python()
        if ok:
            return True, msg
        remove_standalone_python()
    return download_standalone_python(progress_callback, cancel_check)
