"""Paths and environment-driven settings.

Holds NO trading thresholds (capital, risk %, caps, loss limits, liquidity): those are not in config yet.
It does hold the holdout window (set once), the Phase 1 data-quality thresholds and the fetcher settings.
"""
from __future__ import annotations

import os
from datetime import date, time
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

# First date of daily history the fetcher requests (explicit period1, never range=max).
HISTORY_START = date(2005, 1, 1)

# ---------------------------------------------------------------------------------------------------------
# Database
DB_BUSY_TIMEOUT_S = 30  # seconds a connection waits on a locked database before raising

# ---------------------------------------------------------------------------------------------------------
# Data-quality thresholds (Phase 1). Approved 2026-11-23. They change only on explicit instruction and only
# to tighten. Cleaning reads of holdout-period bars are allowed, but ONLY with these mechanical rules, frozen
# before any holdout evaluation. tests/test_config_quality.py pins every value.
UNIVERSE_PLACEHOLDER_REGEX = r"^DUMMY[A-Z0-9]*$"  # placeholder symbols: dropped at universe load, logged
EOD_READY_IST = time(19, 0)  # today's bar is not stored before this time (IST)
RUN_DEADLINE_IST = time(21, 30)  # a trading day's data update must have succeeded by now, else check-run errors
SESSION_OPEN_IST = time(9, 15)
SESSION_OPEN_TOL_MIN = 30  # minutes either side of SESSION_OPEN_IST for the date-mapping check
OHLC_TOL_PCT = 0.05  # % tolerance on high >= max(o, c, l) and low <= min(o, c, h)

MOVE_LARGE_PCT = 20.0  # |one-day move| above this: warn + split-ratio test
MOVE_EXTREME_PCT = 35.0  # above this (close-to-close or wick): flag, keep the bar, no new entry that day
SPLIT_RATIOS = (1.25, 4 / 3, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 10.0, 20.0)  # also tested inverted (reverse split)
SPLIT_RATIO_TOL_PCT = 3.0
CORP_ACTION_WINDOW_SESSIONS = 3  # +/- sessions around ex-date that explain a move
EVENT_JUMP_WINDOW_SESSIONS = 2  # +/- stored sessions around a recorded ex-date searched for its unadjusted jump
BAD_PRINT_REVERT_PCT = 25.0  # next-day reversal size
BAD_PRINT_RETURN_BAND_PCT = 10.0  # next close within this % of the pre-move close
BAD_PRINT_WICK_CLOSE_MAX_PCT = 20.0  # extreme wick with a close inside this %
BAD_PRINT_LOW_VOLUME_FRAC = 0.10  # volume below this fraction of the median
VOLUME_MEDIAN_WINDOW = 20  # sessions

# Symbol blocks for suspected corruption from repeated bad bars (counted in OPEN 'bar' quarantines, by stored bar).
NONPOSITIVE_BLOCK_COUNT = 2  # price_nonpositive bars ...
NONPOSITIVE_BLOCK_WINDOW_SESSIONS = 252  # ... within the symbol's most recent N stored sessions
BAD_BAR_CLUSTER_LIMIT = 3  # bad-print-type bars ...
BAD_BAR_CLUSTER_WINDOW_SESSIONS = 63  # ... within any N consecutive stored sessions

VOL0_RUN_WARN = 3  # consecutive zero-volume bars (stocks only)
VOL0_RUN_ERROR = 5  # run hidden from prices_clean
VOL0_RATIO_WINDOW = 252  # sessions
VOL0_RATIO_MAX_PCT = 2.0
FLAT_RUN_WARN = 5  # consecutive identical-OHLC bars
FLAT_RUN_ERROR = 10

GAP_RUN_WARN = 3  # consecutive missing expected sessions
GAP_RUN_ERROR = 10
GAP_RATIO_WARN_PCT = 1.0
GAP_RATIO_ERROR_PCT = 3.0
GAP_LOOKBACK_BUFFER = 252  # sessions hidden after a gap run (one trading year)

MARKET_WIDE_PCT = 50.0  # share of symbols that makes a gap / holiday mismatch market-wide
MARKET_WIDE_RECENT_SESSIONS = 5
CALENDAR_MAX_AGE_DAYS = 7  # age limit for a stored holiday list

FRESHNESS_BENCHMARK = "^NSEI"  # stored in instruments with kind='index'
STALE_LAG_SESSIONS_MAX = 0  # any lag behind the latest closed session is stale
STALE_SYMBOL_PCT_REPORT = 2.0  # report-only threshold, used until stale_universe is enforced
STALE_SYMBOL_PCT_MAX = 5.0  # threshold once stale_universe is enforced
# When stale_universe is enforced: a held or signalling symbol without a fresh bar is WITHHELD (no recommendation
# for that symbol) and the report carries a red flag at the top. It does not block the whole run.
STALE_REQUIRE_FRESH_HELD_OR_SIGNALLING = True

OVERLAP_BARS = 10  # bars re-fetched every run
REVISION_TOL_PCT = 0.5
READJUST_RATIO_TOL_PCT = 1.0
NOT_FOUND_RUNS_ESCALATE = 3

# Rules allowed to block or exclude. EMPTY until the dry-run report (counts per rule on real data, split
# pre-holdout / holdout, counts only) has been produced and approved. Everything else is report-only.
QUALITY_ENFORCED_RULES: frozenset[str] = frozenset()

# move_unclassified events within this many stored sessions must be reviewed (Phase 1 exit: none left open).
MOVE_UNCLASSIFIED_REVIEW_SESSIONS = 252
# Every run's metadata must carry these counts (see advisor.data.runs.finish_run).
RUN_METADATA_REQUIRED_KEYS = (
    "symbols_blocked",
    "symbols_entry_suppressed",
    "move_unclassified_events",  # every move_unclassified event ever logged (reviewed or not)
    "move_unclassified_unreviewed_recent",  # open ones within MOVE_UNCLASSIFIED_REVIEW_SESSIONS; Phase 1 exit: 0
)

# ---------------------------------------------------------------------------------------------------------
# Fetchers (Phase 1; not written yet). Sequential, one symbol per request. On HTTP 429: wait, never work around.
FETCH_TIMEOUT_S = 20
FETCH_MAX_RETRIES = 4  # retries after the first attempt (5 attempts in all)
FETCH_BACKOFF_BASE_S = 2  # waits of 2, 4, 8, 16 s
FETCH_DELAY_S = 0.5  # pause between successive requests
FETCH_429_WAIT_S = 60
FETCH_429_MAX_CONSECUTIVE = 3  # then stop the run and log an error
FETCH_BATCH_SIZE = 1
FETCH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)  # browser-like, generic: no name or email
# Stored price sources, in order. Yahoo only: it is split-adjusted. NSE bhavcopy is RAW and unadjusted, so it
# is never stored as a price; it may only be used as a cross-check (compare closes, flag differences).
PRICE_SOURCE_ORDER = ("yahoo",)
PRICE_CROSSCHECK_SOURCES = ("nse_bhavcopy",)
