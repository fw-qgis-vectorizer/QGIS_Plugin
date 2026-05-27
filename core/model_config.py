# -*- coding: utf-8 -*-
"""Paths and package configuration for one-click segmentation."""

from __future__ import annotations

import os
import shutil

# Segmentation engine needs Python 3.12+ and recent PyTorch.
MODEL_MIN_PYTHON = (3, 12)

_CACHE_ROOT = os.path.join(os.path.expanduser("~"), ".qgis_fieldwatch")

# Interactive segmentation: SAM 2.1 only (pip sam2 + sam2.1_hiera_base_plus.pt).
BACKEND_SAM2 = "sam2"
BACKEND_SAM3 = "sam3"  # Legacy alias; same engine as BACKEND_SAM2.

SAM2_CHECKPOINT_FILENAME = "sam2.1_hiera_base_plus.pt"
SAM2_GCS_OBJECT = "models/qgis/" + SAM2_CHECKPOINT_FILENAME
SAM2_CHECKPOINT_URL_DEFAULT = (
    "https://storage.googleapis.com/fieldwatch-qgis-assets/" + SAM2_GCS_OBJECT
)

def _default_cache_dir() -> str:
    return os.path.join(_CACHE_ROOT, "model")


def _migrate_legacy_cache_dirs() -> None:
    """Move checkpoints/venv from older cache folders into ~/.qgis_fieldwatch/model/."""
    target = _default_cache_dir()
    if os.path.isdir(target):
        return
    if not os.path.isdir(_CACHE_ROOT):
        return
    for name in os.listdir(_CACHE_ROOT):
        if name == "model":
            continue
        legacy = os.path.join(_CACHE_ROOT, name)
        if not os.path.isdir(legacy):
            continue
        if not os.path.isdir(os.path.join(legacy, "checkpoints")) and not os.path.isdir(
            os.path.join(legacy, "venv")
        ):
            continue
        try:
            os.makedirs(_CACHE_ROOT, exist_ok=True)
            shutil.move(legacy, target)
        except OSError:
            pass
        return


_migrate_legacy_cache_dirs()

CACHE_DIR = os.environ.get("FIELDWATCH_MODEL_CACHE_DIR") or _default_cache_dir()
CHECKPOINTS_DIR = os.path.join(CACHE_DIR, "checkpoints")
VENV_DIR = os.path.join(CACHE_DIR, "venv")
# Written after import verification succeeds (fast UI refresh without re-importing).
DEPS_VERIFIED_STAMP = os.path.join(CACHE_DIR, ".deps_verified")

CHECKPOINT_FILENAME = SAM2_CHECKPOINT_FILENAME

FIELDWATCH_MODEL_GCS_OBJECT = SAM2_GCS_OBJECT
CHECKPOINT_URL = SAM2_CHECKPOINT_URL_DEFAULT

ENGINE_RUNTIME_PACKAGES = (
    "timm>=1.0.17",
    "tqdm",
    "ftfy==6.1.1",
    "regex",
    "iopath>=0.1.9",
    "typing_extensions",
    "huggingface_hub",
    "einops",
    "scipy",
    "psutil",
)

SAM2_RUNTIME_PACKAGES = ("huggingface_hub", "scipy")

LOG_CHANNEL = "FieldWatch One-Click"

# Max side sent to the worker for encoding (smaller = faster on CPU, less IPC).
ENCODE_MAX_SIDE = 768
# Legacy alias
ENCODE_MAX_SIDE_FAST = ENCODE_MAX_SIDE


def encode_max_side() -> int:
    return ENCODE_MAX_SIDE


def segmentation_backend() -> str:
    """Active segmentation engine: SAM 2.1 (sam2 package). No configuration required."""
    return BACKEND_SAM2


def backend_label() -> str:
    """User-facing model label (no version/vendor in UI strings)."""
    return "Interactive"


def checkpoint_filename() -> str:
    return SAM2_CHECKPOINT_FILENAME


def worker_script_basename() -> str:
    return "sam2_prediction_worker.py"


def sam2_checkpoint_url() -> str:
    """Public GCS URL for segmentation weights (FieldWatch hosting)."""
    return SAM2_CHECKPOINT_URL_DEFAULT


# PyTorch CPU wheels (avoid CUDA builds and source compiles on end-user machines).
PYTORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"

# NumPy 1.x has cp312 wheels; NumPy 2.x is required for Python 3.13+.
NUMPY_SPEC_PY312 = "numpy>=1.26.0,<2"
NUMPY_SPEC_PY313 = "numpy>=2.1.0,<2.3"


def numpy_spec_for_python(version: tuple[int, int] | None) -> str:
    if version and version >= (3, 13):
        return NUMPY_SPEC_PY313
    return NUMPY_SPEC_PY312


def pip_packages(numpy_spec: str | None = None) -> tuple[str, ...]:
    numpy_req = numpy_spec or NUMPY_SPEC_PY312
    base = (
        "torch>=2.5.1",
        "torchvision>=0.20.1",
        numpy_req,
        "pillow>=10.0.0",
    )
    return base + SAM2_RUNTIME_PACKAGES + ("sam2>=1.0",)


def pip_constraints_path(numpy_spec: str) -> str:
    """Write/update constraint file (upper bound only — do not duplicate install specs)."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, "pip_constraints.txt")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"{numpy_spec}\n")
    return path


def pip_install_steps(python_version: tuple[int, int] | None = None) -> list[tuple[str, list[str]]]:
    """
    Ordered pip phases: (user message, pip arguments after ``python -m pip``).

    On Windows, install pre-built wheels only (no clang/Meson numpy build).
    """
    import sys

    numpy_spec = numpy_spec_for_python(python_version)
    constraints = ["-c", pip_constraints_path(numpy_spec)]
    prefer_binary = ["--prefer-binary"]

    if sys.platform == "win32":
        only_numpy = ["--only-binary", "numpy"]
        return [
            (
                "Installing NumPy (pre-built wheel)…",
                ["install"] + only_numpy + [numpy_spec],
            ),
            (
                "Installing PyTorch CPU wheels…",
                [
                    "install",
                    "--only-binary",
                    "torch",
                    "--only-binary",
                    "torchvision",
                    "--index-url",
                    PYTORCH_CPU_INDEX,
                    "--extra-index-url",
                    "https://pypi.org/simple",
                ]
                + constraints
                + ["torch>=2.5.1", "torchvision>=0.20.1"],
            ),
            (
                "Installing model engine and dependencies…",
                ["install"]
                + prefer_binary
                + constraints
                + ["sam2>=1.0"]
                + list(SAM2_RUNTIME_PACKAGES),
            ),
        ]

    return [
        (
            "Installing PyTorch and segmentation engine (may take several minutes)…",
            [
                "install",
                "--no-cache-dir",
            ]
            + prefer_binary
            + constraints
            + list(pip_packages(numpy_spec)),
        ),
    ]


# Backward-compatible name used by model_venv.
PIP_PACKAGES = pip_packages()


def engine_site_packages_markers() -> tuple[str, ...]:
    """Top-level package dirs checked by deps_installed() (must match pip install)."""
    return ("torch", "sam2")


def get_checkpoints_dir() -> str:
    os.makedirs(CHECKPOINTS_DIR, exist_ok=True)
    return CHECKPOINTS_DIR


def get_checkpoint_path() -> str:
    return os.path.join(get_checkpoints_dir(), checkpoint_filename())


def _checkpoint_size_ok(path: str) -> bool:
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 1_000_000
    except OSError:
        return False


def checkpoint_exists() -> bool:
    if _checkpoint_size_ok(get_checkpoint_path()):
        return True
    # Backend-specific file missing: still accept other large .pt (e.g. switched backend).
    try:
        for name in os.listdir(get_checkpoints_dir()):
            if name.endswith(".pt") and _checkpoint_size_ok(
                os.path.join(get_checkpoints_dir(), name)
            ):
                return True
    except OSError:
        pass
    return False


def resolve_checkpoint_path() -> str | None:
    primary = get_checkpoint_path()
    if _checkpoint_size_ok(primary):
        return primary
    preferred = SAM2_CHECKPOINT_FILENAME
    try:
        for name in sorted(os.listdir(get_checkpoints_dir())):
            if not name.endswith(".pt"):
                continue
            path = os.path.join(get_checkpoints_dir(), name)
            if _checkpoint_size_ok(path):
                if name == preferred:
                    return path
        for name in sorted(os.listdir(get_checkpoints_dir())):
            if name.endswith(".pt"):
                path = os.path.join(get_checkpoints_dir(), name)
                if _checkpoint_size_ok(path):
                    return path
    except OSError:
        pass
    return None


def python_version_ok(version_info) -> bool:
    return version_info[:2] >= MODEL_MIN_PYTHON
