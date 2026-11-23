"""Holdout guard: training, walk-forward and tuning code must never see bars inside the holdout window.

The window is fixed in `advisor.config` (HOLDOUT_START..HOLDOUT_END, both inclusive). Call `assert_no_holdout`
on any bars or dates before they are used for fitting, tuning or walk-forward scoring. Only the final Phase 3
evaluation may read holdout bars, and it will use its own explicit reader, not this guard.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime

import pandas as pd

from advisor import config


class HoldoutViolation(RuntimeError):
    """Raised when data inside the holdout window reaches code that must not see it."""


def _as_dates(data: pd.DataFrame | pd.Series | pd.Index | Iterable) -> pd.Series:
    """Extract dates as a Series of datetime64 (tz-naive, normalized). A 'date' column wins over the index."""
    if isinstance(data, pd.DataFrame):
        raw = data["date"] if "date" in data.columns else data.index
    elif isinstance(data, (pd.Series, pd.Index)):
        raw = data
    else:
        raw = list(data)
    s = pd.Series(pd.to_datetime(pd.Index(raw) if not isinstance(raw, pd.Series) else raw.values))
    if s.dt.tz is not None:
        s = s.dt.tz_localize(None)
    return s.dt.normalize()


def assert_no_holdout(
    data: pd.DataFrame | pd.Series | pd.Index | Iterable[date | datetime | str],
    *,
    caller: str,
) -> None:
    """Raise HoldoutViolation if any date in `data` lies in [HOLDOUT_START, HOLDOUT_END] (inclusive).

    `data` is a DataFrame (with a 'date' column or a date index), a Series/Index of dates, or any iterable of
    dates. Dates after HOLDOUT_END are live out-of-sample data and are not a training input either, but that is
    a separate concern; this guard only protects the holdout. `caller` names the code path for the message.
    """
    dates = _as_dates(data)
    if dates.empty:
        return
    start = pd.Timestamp(config.HOLDOUT_START)
    end = pd.Timestamp(config.HOLDOUT_END)
    inside = dates[(dates >= start) & (dates <= end)]
    if not inside.empty:
        raise HoldoutViolation(
            f"{caller}: {len(inside)} date(s) inside holdout {config.HOLDOUT_START}..{config.HOLDOUT_END} "
            f"(first {inside.min().date()}, last {inside.max().date()})"
        )
