"""Paths and environment-driven settings.

Holds NO trading thresholds (capital, risk %, caps, loss limits): those are open decisions in spec.md
section 7 and get added here only once confirmed. The one exception is the holdout window below, set once.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _path_from_env(name: str, default: Path) -> Path:
    """Return the Path in env var `name`, or `default`; relative values resolve against PROJECT_ROOT."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    p = Path(raw)
    return p if p.is_absolute() else PROJECT_ROOT / p


DATA_DIR = _path_from_env("ADVISOR_DATA_DIR", PROJECT_ROOT / "data")
DB_PATH = _path_from_env("ADVISOR_DB_PATH", DATA_DIR / "advisor.db")
LOG_DIR = _path_from_env("ADVISOR_LOG_DIR", DATA_DIR / "logs")
LOG_LEVEL = os.environ.get("ADVISOR_LOG_LEVEL", "INFO").strip().upper() or "INFO"

# Untouched final holdout (inclusive dates): the last 12 months of data as of 2026-11-23, the last bar of the
# Phase 0 baseline. Fixed calendar dates, NOT a rolling window, so the holdout never drifts as new bars arrive.
# Set once; do not print or compute metrics on it except in the final Phase 3 evaluation.
HOLDOUT_START = date(2025, 11, 24)
HOLDOUT_END = date(2026, 11, 23)
