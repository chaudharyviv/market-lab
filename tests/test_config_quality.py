"""Pins the approved Phase 1 data-quality constants. Changing one means a deliberate edit here, a line in
docs/decisions.md, and (for thresholds) tightening only. Cleaning reads of holdout bars rely on these staying
frozen until the Phase 3 evaluation."""
from datetime import time

import pytest

from advisor import config

PINNED = {
    "UNIVERSE_PLACEHOLDER_REGEX": r"^DUMMY[A-Z0-9]*$",
    "EOD_READY_IST": time(19, 0), "RUN_DEADLINE_IST": time(21, 30),
    "SESSION_OPEN_IST": time(9, 15), "SESSION_OPEN_TOL_MIN": 30,
    "OHLC_TOL_PCT": 0.05, "MOVE_LARGE_PCT": 20.0, "MOVE_EXTREME_PCT": 35.0,
    "SPLIT_RATIO_TOL_PCT": 3.0, "SPLIT_PERSIST_SESSIONS": 3, "CORP_ACTION_WINDOW_SESSIONS": 3,
    "SPLIT_PERSIST_BAND_PCT": 10.0, "SPLIT_VOLUME_CONFIRM_FRAC": 0.8,
    "BAD_PRINT_REVERT_PCT": 25.0, "BAD_PRINT_RETURN_BAND_PCT": 10.0, "BAD_PRINT_WICK_CLOSE_MAX_PCT": 20.0,
    "BAD_PRINT_LOW_VOLUME_FRAC": 0.10, "VOLUME_MEDIAN_WINDOW": 20,
    "VOL0_RUN_WARN": 3, "VOL0_RUN_ERROR": 5, "VOL0_RATIO_WINDOW": 252, "VOL0_RATIO_MAX_PCT": 2.0,
    "FLAT_RUN_WARN": 5, "FLAT_RUN_ERROR": 10,
    "GAP_RUN_WARN": 3, "GAP_RUN_ERROR": 10, "GAP_RATIO_WARN_PCT": 1.0, "GAP_RATIO_ERROR_PCT": 3.0,
    "GAP_LOOKBACK_BUFFER": 252,
    "MARKET_WIDE_PCT": 50.0, "MARKET_WIDE_RECENT_SESSIONS": 5, "CALENDAR_MAX_AGE_DAYS": 7,
    "FRESHNESS_BENCHMARK": "^NSEI", "STALE_LAG_SESSIONS_MAX": 0,
    "STALE_SYMBOL_PCT_REPORT": 2.0, "STALE_SYMBOL_PCT_MAX": 5.0, "STALE_REQUIRE_FRESH_HELD_OR_SIGNALLING": True,
    "OVERLAP_BARS": 10, "REVISION_TOL_PCT": 0.5, "READJUST_RATIO_TOL_PCT": 1.0, "NOT_FOUND_RUNS_ESCALATE": 3,
    "FETCH_TIMEOUT_S": 20, "FETCH_MAX_RETRIES": 4, "FETCH_BACKOFF_BASE_S": 2, "FETCH_DELAY_S": 0.5,
    "FETCH_429_WAIT_S": 60, "FETCH_429_MAX_CONSECUTIVE": 3, "FETCH_BATCH_SIZE": 1,
    "PRICE_SOURCE_ORDER": ("yahoo",), "PRICE_CROSSCHECK_SOURCES": ("nse_bhavcopy",),
    "RUN_METADATA_REQUIRED_KEYS": ("symbols_blocked", "symbols_entry_suppressed"),
}


@pytest.mark.parametrize("name", sorted(PINNED))
def test_pinned_value(name):
    assert getattr(config, name) == PINNED[name]


def test_split_ratios():
    assert config.SPLIT_RATIOS == pytest.approx((1.25, 4 / 3, 1.5, 2, 2.5, 3, 4, 5, 10, 20))


def test_no_rule_is_enforced_until_the_dry_run_report_is_approved():
    assert config.QUALITY_ENFORCED_RULES == frozenset()


def test_thresholds_are_ordered_sensibly():
    assert config.MOVE_LARGE_PCT < config.MOVE_EXTREME_PCT
    assert config.VOL0_RUN_WARN < config.VOL0_RUN_ERROR
    assert config.FLAT_RUN_WARN < config.FLAT_RUN_ERROR
    assert config.GAP_RUN_WARN < config.GAP_RUN_ERROR
    assert config.GAP_RATIO_WARN_PCT < config.GAP_RATIO_ERROR_PCT
    assert config.STALE_SYMBOL_PCT_REPORT < config.STALE_SYMBOL_PCT_MAX


def test_holdout_is_the_fixed_window():
    assert (config.HOLDOUT_START.isoformat(), config.HOLDOUT_END.isoformat()) == ("2025-11-24", "2026-11-23")


def test_user_agent_is_generic_browser_string():
    ua = config.FETCH_USER_AGENT
    assert ua.startswith("Mozilla/5.0") and "@" not in ua
