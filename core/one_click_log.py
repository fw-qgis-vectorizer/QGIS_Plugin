# -*- coding: utf-8 -*-
"""Sanitize third-party messages before showing in UI or QGIS log."""

from __future__ import annotations

import re

_SAM_RE = re.compile(
    r"\b(?:SAM\s*[\d.+]*|sam\d+(?:\.\d+)?|SAM\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)
_SAM2_RE = re.compile(
    r"\bsam2(?:[.\w_-]*\b)?",
    re.IGNORECASE,
)
_SAM2_CHECKPOINT_RE = re.compile(
    r"sam2\.1_hiera_base_plus\.pt",
    re.IGNORECASE,
)
_SAM_CFG_RE = re.compile(
    r"configs/sam[\d.]+/[\w.+-]+(?:\.yaml)?",
    re.IGNORECASE,
)
_SEGMENT_ANYTHING_RE = re.compile(r"segment[_\s-]?anything[\w.-]*", re.IGNORECASE)
_HF_SAM_REPO_RE = re.compile(r"facebook/sam[\w.-]+", re.IGNORECASE)
# Pip lines: "Collecting sam2", "Requirement already satisfied: sam2"
_PIP_SAM2_RE = re.compile(
    r"(Collecting|Downloading|Installing|Requirement\s+already\s+satisfied:\s*)sam2[\w.-]*",
    re.IGNORECASE,
)


def sanitize_log_line(text: str) -> str:
    """Replace vendor model names with neutral terms (UI + Log Messages)."""
    if not text:
        return text
    out = _PIP_SAM2_RE.sub(r"\1model", text)
    out = _SAM2_CHECKPOINT_RE.sub("model.pt", out)
    out = _SAM_CFG_RE.sub("configs/model", out)
    out = _SAM2_RE.sub("model", out)
    out = _SAM_RE.sub("model", out)
    out = _SEGMENT_ANYTHING_RE.sub("segmentation", out)
    out = _HF_SAM_REPO_RE.sub("model repository", out)
    return out
