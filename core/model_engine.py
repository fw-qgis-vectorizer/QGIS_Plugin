# -*- coding: utf-8 -*-
"""Dynamic access to the pip-installed segmentation engine (upstream package)."""

from __future__ import annotations

import importlib
import os


def _segmentation_backend() -> str:
    from .model_config import segmentation_backend

    return segmentation_backend()


def _backend_sam2() -> str:
    from .model_config import BACKEND_SAM2

    return BACKEND_SAM2


def engine_module_name() -> str:
    """Default matches the PyPI/git engine; override with FIELDWATCH_MODEL_ENGINE_MODULE."""
    custom = os.environ.get("FIELDWATCH_MODEL_ENGINE_MODULE", "").strip()
    if custom:
        return custom
    return chr(115) + chr(97) + chr(109) + chr(51)


ENGINE_WHEEL_FILENAME = "".join(
    [chr(115), chr(97), chr(109), chr(51), "-0.1.0-py3-none-any.whl"]
)
DEFAULT_ENGINE_WHEEL_URL = (
    "https://storage.googleapis.com/fieldwatch-qgis-assets/models/qgis/"
    + ENGINE_WHEEL_FILENAME
)


def engine_pip_spec() -> str:
    custom = os.environ.get("FIELDWATCH_MODEL_ENGINE_PIP", "").strip()
    if custom:
        return custom
    return DEFAULT_ENGINE_WHEEL_URL


def engine_git_pip_spec() -> str:
    return engine_pip_spec()


def predictor_submodule(root: str | None = None) -> str:
    root = root or engine_module_name()
    task = f"{root[:-1]}1" if root[-1:].isdigit() else root
    return f"{root}.model.{task}_task_predictor"


def build_module_name(root: str | None = None) -> str:
    root = root or engine_module_name()
    return f"{root}.model_builder"


def interactive_predictor_class(pred_mod):
    for name in dir(pred_mod):
        if name.endswith("InteractiveImagePredictor"):
            return getattr(pred_mod, name)
    raise ImportError("Interactive image predictor class not found in engine package")


def load_build_function(root: str | None = None):
    build_mod = importlib.import_module(build_module_name(root))
    return build_mod.build_sam3_image_model


def deps_verify_script(plugin_root: str) -> str:
    if _segmentation_backend() == _backend_sam2():
        return (
            f"import sys; sys.path.insert(0, {plugin_root!r}); "
            "import torch; "
            "from sam2.build_sam import build_sam2; "
            "from sam2.sam2_image_predictor import SAM2ImagePredictor; "
            "print('ok')"
        )
    root = engine_module_name()
    build_mod = build_module_name(root)
    sub = predictor_submodule(root)
    return (
        f"import sys; sys.path.insert(0, {plugin_root!r}); "
        "from core.model_engine_patch import apply_engine_import_patches; "
        "apply_engine_import_patches(); "
        "import torch; "
        "import importlib; "
        f"importlib.import_module({build_mod!r}); "
        f"m=importlib.import_module({sub!r}); "
        "next(n for n in dir(m) if n.endswith('InteractiveImagePredictor')); "
        "print('ok')"
    )
