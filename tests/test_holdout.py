from datetime import date

import pandas as pd
import pytest

from advisor import config
from advisor.validation.holdout import HoldoutViolation, assert_no_holdout


def test_holdout_dates_are_the_agreed_fixed_window():
    assert config.HOLDOUT_START == date(2025, 11, 24)
    assert config.HOLDOUT_END == date(2026, 11, 23)


def test_bars_before_holdout_pass():
    df = pd.DataFrame({"date": pd.to_datetime(["2021-11-23", "2025-11-21"]), "close": [1.0, 2.0]})
    assert_no_holdout(df, caller="test")


def test_empty_input_passes():
    assert_no_holdout(pd.DataFrame({"date": []}), caller="test")
    assert_no_holdout([], caller="test")


@pytest.mark.parametrize("d", ["2025-11-24", "2026-01-15", "2026-11-23"])
def test_holdout_dates_raise_including_both_boundaries(d):
    df = pd.DataFrame({"date": pd.to_datetime(["2025-11-21", d]), "close": [1.0, 2.0]})
    with pytest.raises(HoldoutViolation):
        assert_no_holdout(df, caller="test")


def test_one_day_before_start_passes_and_after_end_is_not_holdout():
    assert_no_holdout([date(2025, 11, 23)], caller="test")
    assert_no_holdout([date(2026, 11, 24)], caller="test")  # live OOS, not holdout


def test_accepts_datetime_index_and_series_and_iterables():
    idx = pd.DatetimeIndex(["2025-12-01"])
    with pytest.raises(HoldoutViolation):
        assert_no_holdout(pd.DataFrame({"close": [1.0]}, index=idx), caller="test")
    with pytest.raises(HoldoutViolation):
        assert_no_holdout(pd.Series(idx), caller="test")
    with pytest.raises(HoldoutViolation):
        assert_no_holdout(["2026-02-02"], caller="test")


def test_timezone_aware_timestamps_are_handled():
    idx = pd.DatetimeIndex(["2026-03-02 09:15"], tz="Asia/Kolkata")
    with pytest.raises(HoldoutViolation):
        assert_no_holdout(idx, caller="test")


def test_message_names_caller():
    with pytest.raises(HoldoutViolation, match="walk_forward"):
        assert_no_holdout(["2026-02-02"], caller="walk_forward")
