# -*- coding: utf-8 -*-
"""Isolated Python environment for one-click segmentation."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess  # nosec B404
import sys
import tempfile
from typing import Callable

from qgis.core import Qgis, QgsMessageLog

from .one_click_log import sanitize_log_line
from .model_config import (
    DEPS_VERIFIED_STAMP,
    LOG_CHANNEL,
    VENV_DIR,
    engine_site_packages_markers,
    pip_install_steps,
    python_version_ok,
)
from .model_engine import deps_verify_script
from .one_click_python import (
    ensure_standalone_python,
    get_standalone_python_path,
    standalone_python_exists,
    verify_standalone_python,
)
from .safe_http import GET_PIP_DOWNLOAD_HOSTS, download_https_to_file
from .subprocess_utils import get_clean_env, popen_hidden, run_hidden

GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"


def _plugin_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _is_disqualified_base_python(path: str) -> bool:
    """Reject QGIS/OSGeo embedded interpreters — they cannot host a normal venv."""
    if not path or not os.path.isfile(path):
        return True
    norm = os.path.normpath(path).lower()
    for token in ("qgis", "osgeo4w", "grass", "saga"):
        if token in norm:
            return True
    try:
        if os.path.samefile(path, sys.executable):
            return True
    except OSError:
        if os.path.normcase(path) == os.path.normcase(sys.executable):
            return True
    return False


def _python_version_tuple(exe: str) -> tuple[int, int] | None:
    try:
        result = run_hidden(
            [exe, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
            capture_output=True,
            text=True,
            timeout=30,
            env=get_clean_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    parts = result.stdout.strip().split()
    if len(parts) >= 2:
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            pass
    return None


def _iter_system_python_candidates():
    """Optional system Pythons (never QGIS). Portable runtime is preferred."""
    override = os.environ.get("FIELDWATCH_MODEL_PYTHON", "").strip()
    if override and os.path.isfile(override):
        yield override

    if sys.platform == "win32":
        # 3.12 only — NumPy 1.26 / PyTorch CPU wheels are not reliable on 3.13 here.
        for ver in ("3.12",):
            try:
                result = run_hidden(
                    ["py", f"-{ver}", "-c", "import sys; print(sys.executable)"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env=get_clean_env(),
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if result.returncode == 0:
                line = result.stdout.strip().splitlines()[-1] if result.stdout else ""
                if line and os.path.isfile(line):
                    yield line

        local = os.environ.get("LOCALAPPDATA", "")
        for folder in ("Python312",):
            candidate = os.path.join(local, "Programs", "Python", folder, "python.exe")
            if os.path.isfile(candidate):
                yield candidate
    else:
        for cmd in (["python3.13"], ["python3.12"]):
            try:
                result = run_hidden(
                    cmd + ["-c", "import sys; print(sys.executable)"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env=get_clean_env(),
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if result.returncode == 0:
                line = result.stdout.strip().splitlines()[-1] if result.stdout else ""
                if line and os.path.isfile(line):
                    yield line


def find_base_python() -> str | None:
    """
    Python 3.12+ for venv creation.

    Prefer the bundled portable runtime, then an explicit override or
    system install. Never QGIS's embedded Python.
    """
    if standalone_python_exists():
        path = get_standalone_python_path()
        if not _is_disqualified_base_python(path):
            ok, _ = verify_standalone_python()
            if ok:
                return path

    seen = set()
    for candidate in _iter_system_python_candidates():
        norm = os.path.normcase(os.path.abspath(candidate))
        if norm in seen:
            continue
        seen.add(norm)
        if _is_disqualified_base_python(candidate):
            continue
        version = _python_version_tuple(candidate)
        if not version or not python_version_ok(version):
            continue
        # Windows wheels target our bundled Python 3.12 (not 3.13).
        if sys.platform == "win32" and version[:2] != (3, 12):
            continue
        return candidate
    return None


def get_venv_python() -> str:
    if platform.system() == "Windows":
        return os.path.join(VENV_DIR, "Scripts", "python.exe")
    return os.path.join(VENV_DIR, "bin", "python")


def venv_exists() -> bool:
    return os.path.isfile(get_venv_python())


def _venv_has_pip() -> bool:
    if not venv_exists():
        return False
    try:
        result = run_hidden(
            [get_venv_python(), "-m", "pip", "--version"],
            capture_output=True,
            text=True,
            timeout=60,
            env=get_clean_env(),
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def remove_venv() -> None:
    clear_deps_verified()
    if os.path.isdir(VENV_DIR):
        shutil.rmtree(VENV_DIR, ignore_errors=True)


def mark_deps_verified() -> None:
    os.makedirs(os.path.dirname(DEPS_VERIFIED_STAMP), exist_ok=True)
    with open(DEPS_VERIFIED_STAMP, "w", encoding="utf-8") as handle:
        handle.write("ok\n")


def clear_deps_verified() -> None:
    try:
        os.remove(DEPS_VERIFIED_STAMP)
    except OSError:
        pass


def _package_dir_present(site_packages: str, name: str) -> bool:
    if os.path.isdir(os.path.join(site_packages, name)):
        return True
    try:
        prefix = name.replace("_", "-").lower()
        for entry in os.listdir(site_packages):
            low = entry.lower()
            if low.endswith(".dist-info") and low.startswith(prefix):
                return True
    except OSError:
        pass
    return False


def _venv_site_packages() -> str | None:
    win_sp = os.path.join(VENV_DIR, "Lib", "site-packages")
    if os.path.isdir(win_sp):
        return win_sp
    lib_root = os.path.join(VENV_DIR, "lib")
    if not os.path.isdir(lib_root):
        return None
    for name in os.listdir(lib_root):
        if name.startswith("python"):
            sp = os.path.join(lib_root, name, "site-packages")
            if os.path.isdir(sp):
                return sp
    return None


def _bootstrap_pip(venv_py: str, base_py: str) -> tuple[bool, str]:
    env = get_clean_env()

    for cmd in (
        [venv_py, "-m", "ensurepip", "--upgrade"],
        [base_py, "-m", "ensurepip", "--upgrade", "--default-pip"],
    ):
        try:
            result = run_hidden(
                cmd,
                capture_output=True,
                text=True,
                timeout=180,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            continue
        if result.returncode == 0 and _venv_has_pip():
            return True, ""

    try:
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as tmp:
            get_pip_path = tmp.name
        download_https_to_file(
            GET_PIP_URL,
            get_pip_path,
            GET_PIP_DOWNLOAD_HOSTS,
            timeout=300,
        )
        result = run_hidden(
            [venv_py, get_pip_path],
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
    except Exception as exc:
        return False, f"Failed to bootstrap pip: {exc}"
    finally:
        try:
            if "get_pip_path" in locals() and os.path.isfile(get_pip_path):
                os.remove(get_pip_path)
        except OSError:
            pass

    if result.returncode == 0 and _venv_has_pip():
        return True, ""

    err = (result.stderr or result.stdout or "pip bootstrap failed.")[:500]
    return False, err


def _create_venv(base: str) -> tuple[bool, str]:
    if venv_exists():
        if _venv_has_pip():
            return True, ""
        remove_venv()

    os.makedirs(os.path.dirname(VENV_DIR), exist_ok=True)
    env = get_clean_env()
    strategies = ["--without-pip", ""] if sys.platform == "win32" else [""]

    last_error = ""
    for flag in strategies:
        cmd = [base, "-m", "venv"]
        if flag:
            cmd.append(flag)
        cmd.append(VENV_DIR)
        try:
            result = run_hidden(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_error = str(exc)
            remove_venv()
            continue

        if result.returncode != 0:
            last_error = (result.stderr or result.stdout or last_error)[:500]
            remove_venv()
            continue

        venv_py = get_venv_python()
        if not os.path.isfile(venv_py):
            last_error = "Virtual environment was created but python.exe is missing."
            remove_venv()
            continue

        if flag == "--without-pip" or not _venv_has_pip():
            ok, msg = _bootstrap_pip(venv_py, base)
            if not ok:
                remove_venv()
                return False, msg
        return True, ""

    return False, last_error or "Failed to create virtual environment."


def deps_installed() -> bool:
    """
    Fast check for UI refresh (no subprocess, no torch import).

    Full import verification runs once at the end of create_venv_and_install().
    """
    if not venv_exists():
        return False
    site_packages = _venv_site_packages()
    if not site_packages:
        return False
    markers_ok = all(
        _package_dir_present(site_packages, name) for name in engine_site_packages_markers()
    )
    if markers_ok:
        if not os.path.isfile(DEPS_VERIFIED_STAMP):
            mark_deps_verified()
        return True
    clear_deps_verified()
    return False


def _verify_deps_import() -> tuple[bool, str]:
    """Heavy check: import torch + engine in the venv (used after pip install only)."""
    if not venv_exists():
        return False, "Virtual environment not found."
    py = get_venv_python()
    try:
        result = run_hidden(
            [py, "-c", deps_verify_script(_plugin_root())],
            capture_output=True,
            text=True,
            timeout=300,
            env=get_clean_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)

    if result.returncode == 0:
        return True, ""

    detail = (result.stderr or result.stdout or "Import verification failed.").strip()
    tail = "\n".join(detail.splitlines()[-12:])
    return False, tail or detail


def _run_pip(
    py: str,
    args: list[str],
    progress_callback: Callable[[int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[bool, str]:
    cmd = [py, "-m", "pip"] + args
    proc = None
    try:
        try:
            proc = popen_hidden(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=get_clean_env(),
            )
        except OSError as exc:
            return False, str(exc)

        lines = []
        while True:
            if cancel_check and cancel_check():
                proc.kill()
                proc.wait()
                return False, "Installation cancelled."
            line = proc.stdout.readline() if proc.stdout else ""
            if not line and proc.poll() is not None:
                break
            if line:
                lines.append(line.rstrip())
                if progress_callback and len(lines) % 5 == 0:
                    progress_callback(
                        50, sanitize_log_line(line.strip()[:120])
                    )
        code = proc.wait()
        if code != 0:
            tail = sanitize_log_line("\n".join(lines[-15:]))
            return False, tail or f"pip exited with code {code}"
        return True, "Dependencies installed."
    finally:
        if proc is not None:
            if proc.stdout is not None:
                try:
                    proc.stdout.close()
                except Exception:
                    pass
            if proc.stderr is not None and proc.stderr is not proc.stdout:
                try:
                    proc.stderr.close()
                except Exception:
                    pass


def _ensure_base_python(
    progress_callback: Callable[[int, str], None] | None,
    cancel_check: Callable[[], bool] | None,
) -> tuple[bool, str]:
    base = find_base_python()
    if base:
        return True, base

    def download_progress(percent: int, message: str):
        if progress_callback:
            mapped = 5 + int(percent * 35 / 100)
            progress_callback(min(40, mapped), sanitize_log_line(message))

    ok, msg = ensure_standalone_python(download_progress, cancel_check)
    if not ok:
        return False, (
            f"{msg}\n\n"
            "Could not download the Python runtime. Check your internet connection, "
            "proxy, or firewall, then try again."
        )

    return True, get_standalone_python_path()


def create_venv_and_install(
    progress_callback: Callable[[int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[bool, str]:
    """Download portable Python if needed, create venv, install packages."""
    if progress_callback:
        progress_callback(2, "Preparing Python runtime…")

    ok, base_or_msg = _ensure_base_python(progress_callback, cancel_check)
    if not ok:
        return False, base_or_msg
    base = base_or_msg

    os.makedirs(os.path.dirname(VENV_DIR), exist_ok=True)
    if progress_callback:
        progress_callback(42, "Creating Python environment…")

    ok, msg = _create_venv(base)
    if not ok:
        return False, msg

    py = get_venv_python()
    py_version = _python_version_tuple(py)

    def install_progress(percent: int, message: str):
        if progress_callback:
            progress_callback(45 + int(percent * 0.5), sanitize_log_line(message))

    if progress_callback:
        progress_callback(45, "Upgrading pip…")
    ok, msg = _run_pip(py, ["install", "--upgrade", "pip", "wheel"], install_progress, cancel_check)
    if not ok:
        return False, msg

    steps = pip_install_steps(py_version)
    total = len(steps)
    for index, (label, args) in enumerate(steps):
        if progress_callback:
            pct = 55 + int((index / max(total, 1)) * 35)
            progress_callback(pct, sanitize_log_line(label))
        ok, msg = _run_pip(py, args, install_progress, cancel_check)
        if not ok:
            hint = ""
            if platform.system() == "Windows" and (
                "numpy" in msg.lower() or "resolutionimpossible" in msg.lower()
            ):
                hint = (
                    "\n\nOn Windows, install uses pre-built wheels only (Python 3.12). "
                    "Delete the venv folder, restart QGIS, and try again:\n"
                    f"{VENV_DIR}\n\n"
                    f"Python used: {py_version or 'unknown'}"
                )
            return False, sanitize_log_line(msg) + hint

    if progress_callback:
        progress_callback(95, "Verifying installation…")
    ok, verify_detail = _verify_deps_import()
    if ok:
        mark_deps_verified()
        if progress_callback:
            progress_callback(100, "Ready.")
        QgsMessageLog.logMessage(
            "Segmentation dependencies installed.",
            LOG_CHANNEL,
            level=Qgis.MessageLevel.Info,
        )
        return True, "Dependencies installed successfully."

    safe_detail = sanitize_log_line(verify_detail)
    QgsMessageLog.logMessage(
        f"Segmentation dependency verification failed:\n{safe_detail}",
        LOG_CHANNEL,
        level=Qgis.MessageLevel.Critical,
    )
    return False, (
        "Install finished but the segmentation engine could not be imported.\n\n"
        f"{safe_detail}\n\n"
        "Try Install dependencies again. If it persists, delete the folder "
        f"{VENV_DIR} and reinstall."
    )
