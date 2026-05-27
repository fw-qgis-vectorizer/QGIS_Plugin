# -*- coding: utf-8 -*-
"""Hide console windows and run child processes in the background on Windows."""

from __future__ import annotations

import os
import subprocess  # nosec B404
import sys


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


def run_hidden(cmd, **kwargs):
    """subprocess.run without a visible console on Windows."""
    merged = get_subprocess_kwargs()
    merged.update(kwargs)
    return subprocess.run(cmd, **merged)  # nosec B603


def popen_hidden(cmd, **kwargs):
    """subprocess.Popen without a visible console on Windows."""
    merged = get_subprocess_kwargs()
    merged.update(kwargs)
    return subprocess.Popen(cmd, **merged)  # nosec B603
