"""Symbol blocks from repeated bad bars: nonpositive_recurring and bad_bar_cluster. Both count only OPEN scope-'bar'
quarantines from their source checks, by position in the symbol's stored bars."""
import json
from datetime import date, timedelta

import pytest

from advisor import config
from advisor.data.db import transaction
from advisor.data.prices import replace_symbol_history
from advisor.data.quality import BAR_BLOCK_CHECKS, detect_bar_blocks, recheck_symbol

N = 300  # stored bars, more than NONPOSITIVE_BLOCK_WINDOW_SESSIONS
NP, CL = "nonpositive_recurring", "bad_bar_cluster"


@pytest.fixture
def enforce(monkeypatch):
    monkeypatch.setattr(config, "QUALITY_ENFORCED_RULES", frozenset(BAR_BLOCK_CHECKS))


def _days(n, start=date(2023, 1, 2)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


DAYS = _days(N)
BARS = [(d, 100.0, 101.0, 99.0, 100.0, 1000.0) for d in DAYS]


@pytest.fixture
def aaa(db):
    db.executemany("INSERT INTO prices_daily VALUES ('AAA',?,?,?,?,?,?,'yahoo','t')", BARS)
    return db


def _q(db, idx, check, scope="bar", resolved=False):
    d = DAYS[idx]
    db.execute("INSERT INTO quality_quarantine (symbol, scope, from_date, to_date, check_name, created_at, "
               "resolved_at, resolution) VALUES ('AAA', ?, ?, ?, ?, 't', ?, ?)",
               (scope, d, d, check, "r" if resolved else None, "manual" if resolved else None))


def _open_blocks(db, check=None):
    q = "SELECT check_name FROM quality_quarantine WHERE symbol='AAA' AND scope='symbol' AND resolved_at IS NULL"
    names = [r[0] for r in db.execute(q)]
    return names if check is None else names.count(check)


def _fired(aaa):
    return {f.check_name for f in detect_bar_blocks(aaa, "AAA")}


# --- nonpositive_recurring ------------------------------------------------------------------------------------
def test_one_nonpositive_bar_does_not_block(aaa, enforce):
    _q(aaa, N - 1, "price_nonpositive")
    assert _fired(aaa) == set() and _open_blocks(aaa) == []


def test_two_nonpositive_bars_block(aaa, enforce):
    _q(aaa, N - 10, "price_nonpositive")
    _q(aaa, N - 1, "price_nonpositive")
    assert NP in _fired(aaa)
    assert _open_blocks(aaa, NP) == 1
    assert aaa.execute("SELECT COUNT(*) FROM prices_clean WHERE symbol='AAA'").fetchone()[0] == 0


@pytest.mark.parametrize("age,blocks", [(252, True), (253, False)])
def test_nonpositive_window_edge(aaa, enforce, age, blocks):
    """age = how many stored sessions back (1 = latest bar). The 252nd counts, the 253rd does not."""
    _q(aaa, N - age, "price_nonpositive")
    _q(aaa, N - 1, "price_nonpositive")
    assert (NP in _fired(aaa)) is blocks


# --- bad_bar_cluster ------------------------------------------------------------------------------------------
def test_two_bad_bars_do_not_block(aaa, enforce):
    _q(aaa, 100, "ohlc_inconsistent")
    _q(aaa, 110, "move_bad_print")
    assert _fired(aaa) == set()


def test_three_bad_bars_of_mixed_checks_block(aaa, enforce):
    for i, c in zip((100, 110, 120), ("ohlc_inconsistent", "move_bad_print", "volume_zero_price_moved"), strict=True):
        _q(aaa, i, c)
    assert _fired(aaa) == {CL} and _open_blocks(aaa, CL) == 1


@pytest.mark.parametrize("last,blocks", [(162, True), (163, False)])
def test_cluster_window_edge(aaa, enforce, last, blocks):
    """Three bars spanning 63 stored sessions (100..162) block; spanning 64 (100..163) do not. The window
    rolls over the whole history, so this cluster is old (about 140 sessions before the latest bar)."""
    for i in (100, 130, last):
        _q(aaa, i, "move_bad_print")
    assert (CL in _fired(aaa)) is blocks


def test_nonpositive_bars_count_toward_the_cluster_anywhere_in_history(aaa, enforce):
    for i in (10, 20, 30):  # far outside the 252-session nonpositive window
        _q(aaa, i, "price_nonpositive")
    assert _fired(aaa) == {CL}


@pytest.mark.parametrize("kind", ["resolved", "entry", "span", "other_checks"])
def test_rows_that_must_not_count(aaa, enforce, kind):
    for i in (N - 3, N - 2, N - 1):
        if kind == "resolved":
            _q(aaa, i, "price_nonpositive", resolved=True)
        elif kind == "entry":
            _q(aaa, i, "price_nonpositive", scope="entry")
        elif kind == "span":
            _q(aaa, i, "move_bad_print", scope="span")
        else:
            for c in ("bar_nan_volume", "flat_price_run", "volume_zero_run", "gap_run"):
                _q(aaa, i, c)
    assert _fired(aaa) == set() and _open_blocks(aaa) == []


# --- enforcement, logging, duplicates -------------------------------------------------------------------------
def test_not_enforced_by_default_logs_warn_and_opens_nothing(aaa):
    assert config.QUALITY_ENFORCED_RULES == frozenset()
    for i in (N - 3, N - 2, N - 1):
        _q(aaa, i, "price_nonpositive")
    assert _fired(aaa) == {NP, CL}
    assert _open_blocks(aaa) == []
    rows = aaa.execute("SELECT check_name, severity, detail FROM data_quality_log").fetchall()
    assert {r["check_name"] for r in rows} == {NP, CL}
    assert {r["severity"] for r in rows} == {"warn"}
    assert {json.loads(r["detail"])["block"] for r in rows} == {"not_enforced"}


def test_no_duplicate_blocks_and_never_error(aaa, enforce):
    for i in (N - 3, N - 2, N - 1):
        _q(aaa, i, "price_nonpositive")
    for _ in range(3):
        detect_bar_blocks(aaa, "AAA")
    with transaction(aaa):
        recheck_symbol(aaa, "AAA")
    assert sorted(_open_blocks(aaa)) == [CL, NP]
    sev = {r[0] for r in aaa.execute("SELECT severity FROM data_quality_log WHERE check_name IN (?,?)", (NP, CL))}
    assert sev == {"warn"}


# --- re-check -------------------------------------------------------------------------------------------------
def _block_everything(aaa):
    for i in (N - 3, N - 2, N - 1):
        _q(aaa, i, "price_nonpositive")
    detect_bar_blocks(aaa, "AAA")
    aaa.execute("INSERT INTO quality_quarantine (symbol, scope, check_name, created_at) "
                "VALUES ('AAA', 'symbol', 'manual_review', 't')")
    assert sorted(_open_blocks(aaa)) == [CL, "manual_review", NP]


def test_history_replacement_clears_bar_blocks_but_not_other_blocks(aaa, enforce):
    _block_everything(aaa)
    res = replace_symbol_history(aaa, "AAA", BARS, source="yahoo", fetched_at="new")
    assert res.recheck.bar_blocks == () and res.recheck.blocks_resolved == 2
    rows = {r["check_name"]: r["resolution"] for r in aaa.execute(
        "SELECT check_name, resolution FROM quality_quarantine WHERE scope='symbol'")}
    assert rows == {NP: "recheck_clean", CL: "recheck_clean", "manual_review": None}
    assert res.still_blocked is True  # the manual_review block is untouched


def test_recheck_clears_once_only_the_other_block_remains(aaa, enforce):
    for i in (N - 3, N - 2, N - 1):
        _q(aaa, i, "price_nonpositive")
    detect_bar_blocks(aaa, "AAA")
    res = replace_symbol_history(aaa, "AAA", BARS, source="yahoo", fetched_at="new")
    assert res.still_blocked is False and _open_blocks(aaa) == []
    assert aaa.execute("SELECT COUNT(*) FROM prices_clean WHERE symbol='AAA'").fetchone()[0] == N


def test_recheck_keeps_a_block_whose_trigger_still_holds(aaa, enforce):
    _block_everything(aaa)
    with transaction(aaa):
        res = recheck_symbol(aaa, "AAA")
    assert {f.check_name for f in res.bar_blocks} == {NP, CL} and res.blocks_resolved == 0
    assert sorted(_open_blocks(aaa)) == [CL, "manual_review", NP]


def test_recheck_resolves_only_the_rule_no_longer_met(aaa, enforce):
    for i in (N - 3, N - 2, N - 1):
        _q(aaa, i, "price_nonpositive")
    detect_bar_blocks(aaa, "AAA")
    # A manual verdict clears one of the three bars: 2 left, so nonpositive_recurring (needs 2) still holds and
    # bad_bar_cluster (needs 3) no longer does.
    aaa.execute("UPDATE quality_quarantine SET resolved_at='r', resolution='genuine' "
                "WHERE scope='bar' AND from_date=?", (DAYS[N - 3],))
    with transaction(aaa):
        res = recheck_symbol(aaa, "AAA")
    assert {f.check_name for f in res.bar_blocks} == {NP}
    assert _open_blocks(aaa) == [NP]
