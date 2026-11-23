"""Paths and environment-driven settings.

Deliberately holds NO trading thresholds (capital, risk %, caps, loss limits, holdout dates): those are open
decisions in spec.md section 7 and get added here only once confirmed.
"""
from __future__ import annotations

import os
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
