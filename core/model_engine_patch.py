# -*- coding: utf-8 -*-
"""
Patch upstream engine imports for FieldWatch one-click segmentation.

- CPU / Windows: no Triton, pycocotools stub, position-encoding precompute on CPU.
- CUDA: used automatically when PyTorch was built with CUDA and a GPU is available.
"""

from __future__ import annotations

import math
import os
import sys
import types
from typing import Optional


def cuda_is_usable() -> bool:
    """True only when PyTorch was built with CUDA and a device works."""
    import torch

    if not torch.cuda.is_available():
        return False
    try:
        torch.zeros(1, device="cuda")
        return True
    except (AssertionError, RuntimeError):
        return False


def normalize_torch_device(device):
    """Map hardcoded ``cuda`` to the resolved device when CUDA is not usable."""
    import torch

    if isinstance(device, str) and device.startswith("cuda"):
        return resolve_torch_device() if not cuda_is_usable() else device
    if isinstance(device, torch.device) and device.type == "cuda":
        return torch.device(resolve_torch_device()) if not cuda_is_usable() else device
    return device


def resolve_torch_device() -> str:
    """
    Pick cuda, mps, or cpu.

    Override with FIELDWATCH_MODEL_DEVICE=cpu|cuda|mps|auto (default auto).
    """
    forced = os.environ.get("FIELDWATCH_MODEL_DEVICE", "auto").strip().lower()
    if forced in ("cpu", "cuda", "mps"):
        return forced

    import torch

    if forced != "cpu" and cuda_is_usable():
        return "cuda"

        if sys.platform == "darwin":
            try:
                if torch.backends.mps.is_available() and torch.backends.mps.is_built():
                    torch.zeros(1, device="mps")
                    return "mps"
            except (AssertionError, RuntimeError):
                pass

    return "cpu"


def _edt_triton_cpu(data):
    """Euclidean distance transform on CPU (scipy), matching edt.py semantics."""
    import numpy as np
    import torch
    from scipy.ndimage import distance_transform_edt

    if data.dim() != 3:
        raise ValueError("Expected (B, H, W) tensor")
    device = data.device
    batch = []
    for b in range(data.shape[0]):
        plane = data[b].detach().cpu().numpy()
        fg = plane != 0
        dt = distance_transform_edt(~fg)
        batch.append(dt.astype(np.float32))
    out = torch.from_numpy(np.stack(batch)).to(device=device)
    return out


def _training_api_unavailable(*_args, **_kwargs):
    raise RuntimeError(
        "Training-only dependency is not available in one-click segmentation mode."
    )


def _install_pycocotools_stub() -> None:
    if "pycocotools" in sys.modules:
        return

    pycocotools = types.ModuleType("pycocotools")
    mask = types.ModuleType("pycocotools.mask")
    for name in ("frPyObjects", "merge", "decode", "encode", "area", "toBbox"):
        setattr(mask, name, _training_api_unavailable)
    pycocotools.mask = mask
    sys.modules["pycocotools"] = pycocotools
    sys.modules["pycocotools.mask"] = mask


def _install_edt_patch() -> None:
    if "sam3.model.edt" in sys.modules:
        return

    edt_mod = types.ModuleType("sam3.model.edt")
    edt_mod.edt_triton = _edt_triton_cpu
    edt_mod.__doc__ = "FieldWatch CPU fallback for upstream edt (no Triton)."
    sys.modules["sam3.model.edt"] = edt_mod


def _patch_position_embedding_sine() -> None:
    """Upstream precomputes pos enc on hardcoded CUDA; use resolve_torch_device() instead."""
    import importlib

    pe = importlib.import_module("sam3.model.position_encoding")
    if getattr(pe, "_fieldwatch_pe_patch", False):
        return

    import torch
    from torch import nn

    def __init__(
        self,
        num_pos_feats,
        temperature: int = 10000,
        normalize: bool = True,
        scale: Optional[float] = None,
        precompute_resolution: Optional[int] = None,
    ):
        nn.Module.__init__(self)
        assert num_pos_feats % 2 == 0, "Expecting even model width"
        self.num_pos_feats = num_pos_feats // 2
        self.temperature = temperature
        self.normalize = normalize
        if scale is not None and normalize is False:
            raise ValueError("normalize should be True if scale is passed")
        if scale is None:
            scale = 2 * math.pi
        self.scale = scale

        self.cache = {}
        if precompute_resolution is not None:
            device = resolve_torch_device()
            precompute_sizes = [
                (int(precompute_resolution // 3.5), int(precompute_resolution // 3.5)),
                (precompute_resolution // 4, precompute_resolution // 4),
                (int(precompute_resolution // 7), int(precompute_resolution // 7)),
                (precompute_resolution // 8, precompute_resolution // 8),
                (int(precompute_resolution // 14), int(precompute_resolution // 14)),
                (precompute_resolution // 16, precompute_resolution // 16),
                (int(precompute_resolution // 28), int(precompute_resolution // 28)),
                (precompute_resolution // 32, precompute_resolution // 32),
            ]
            for size in precompute_sizes:
                tensors = torch.zeros((1, 1) + size, device=device)
                self.forward(tensors)
                self.cache[size] = self.cache[size].clone().detach()

    pe.PositionEmbeddingSine.__init__ = __init__
    pe._fieldwatch_pe_patch = True

    mb = sys.modules.get("sam3.model_builder")
    if mb is not None:
        mb.PositionEmbeddingSine = pe.PositionEmbeddingSine


def _patch_transformer_decoder_coords() -> None:
    """Decoder precomputes boxRPB coords on hardcoded CUDA at __init__."""
    import importlib

    dec = importlib.import_module("sam3.model.decoder")
    if getattr(dec, "_fieldwatch_dec_patch", False):
        return

    orig = dec.TransformerDecoder._get_coords

    @staticmethod
    def _get_coords(H, W, device):
        return orig(H, W, normalize_torch_device(device))

    dec.TransformerDecoder._get_coords = _get_coords
    dec._fieldwatch_dec_patch = True


def _patch_module_cuda() -> None:
    """No-op .cuda() on CPU-only PyTorch builds (upstream model_builder uses it)."""
    import torch
    import torch.nn as nn

    if getattr(nn.Module, "_fieldwatch_cuda_patch", False):
        return

    orig_module_cuda = nn.Module.cuda
    orig_tensor_cuda = torch.Tensor.cuda

    def module_cuda(self, device=None):
        if not cuda_is_usable():
            return self.to("cpu")
        return orig_module_cuda(self, device)

    def tensor_cuda(self, device=None, non_blocking=False):
        if not cuda_is_usable():
            return self.to("cpu", non_blocking=non_blocking)
        return orig_tensor_cuda(self, device, non_blocking=non_blocking)

    nn.Module.cuda = module_cuda
    torch.Tensor.cuda = tensor_cuda
    nn.Module._fieldwatch_cuda_patch = True


def _patch_model_builder_setup_device() -> None:
    """build_sam3_image_model calls model.cuda() when device=='cuda'."""
    import importlib

    mb = importlib.import_module("sam3.model_builder")
    if getattr(mb, "_fieldwatch_setup_device_patch", False):
        return

    orig = mb._setup_device_and_mode

    def _setup_device_and_mode(model, device, eval_mode):
        if device == "cuda" and not cuda_is_usable():
            device = "cpu"
        return orig(model, device, eval_mode)

    mb._setup_device_and_mode = _setup_device_and_mode
    mb._fieldwatch_setup_device_patch = True


def _patch_model_builder_tf32() -> None:
    """Skip TF32 setup when CUDA is not actually usable (CPU-only PyTorch builds)."""
    if "sam3.model_builder" in sys.modules:
        mb = sys.modules["sam3.model_builder"]
    else:
        import importlib

        mb = importlib.import_module("sam3.model_builder")

    import torch

    def _setup_tf32_safe() -> None:
        if not cuda_is_usable():
            return
        device_props = torch.cuda.get_device_properties(0)
        if device_props.major >= 8:
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True

    mb._setup_tf32 = _setup_tf32_safe


def apply_engine_import_patches() -> None:
    _install_pycocotools_stub()
    _install_edt_patch()
    _patch_module_cuda()
    _patch_position_embedding_sine()
    _patch_transformer_decoder_coords()
    _patch_model_builder_tf32()
    _patch_model_builder_setup_device()
