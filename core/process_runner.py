# -*- coding: utf-8 -*-
"""Child process helpers (Windows console hiding, venv-safe env, validated calls)."""

from __future__ import annotations

import os
import subprocess  # nosec B404
import sys

PIPE = subprocess.PIPE
STDOUT = subprocess.STDOUT
TimeoutExpired = subprocess.TimeoutExpired


def get_clean_env() -> dict:
    """Environment for venv / portable Python children (avoid QGIS Python paths)."""
    env = os.environ.copy()
    for key in (
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONEXECUTABLE",
        "VIRTUAL_ENV",
        "QGIS_PREFIX_PATH",
        "QGIS_PLUGINPATH",
    ):
        env.pop(key, None)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def get_subprocess_kwargs() -> dict:
    """Platform kwargs so Windows does not flash console windows."""
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "startupinfo": startupinfo,
        "creationflags": subprocess.CREATE_NO_WINDOW,
    }


def _merge_kwargs(**kwargs):
    merged = get_subprocess_kwargs()
    merged.update(kwargs)
    merged.setdefault("shell", False)
    return merged


def run_hidden(cmd, **kwargs):
    """subprocess.run without a visible console on Windows."""
    merged = _merge_kwargs(**kwargs)
    return subprocess.run(cmd, shell=False, **merged)  # nosec B603


def popen_hidden(cmd, **kwargs):
    """subprocess.Popen without a visible console on Windows."""
    merged = _merge_kwargs(**kwargs)
    return subprocess.Popen(cmd, shell=False, **merged)  # nosec B603


def check_call(cmd, **kwargs):
    """subprocess.check_call with shell=False and Windows console hidden."""
    merged = _merge_kwargs(**kwargs)
    return subprocess.check_call(cmd, shell=False, **merged)  # nosec B603
