"""Unadjusted split/bonus detection, the symbol block it opens, and the mandatory re-check after a history
replacement. Synthetic series: 60 bars near 1000 INR, then an ex-date where the stored price drops by the ratio."""
import json
from datetime import date, timedelta

import pytest

from advisor import config
from advisor.data import quality
from advisor.data.db import transaction
from advisor.data.prices import replace_symbol_history
from advisor.data.quality import SPLIT_CHECKS, detect_unadjusted_split, recheck_symbol

PRE, POST = 60, 10
BASE_PX, BASE_VOL = 1000.0, 10_000.0


@pytest.fixture
def enforce(monkeypatch):
    """Turn the two split checks on. In the shipped config nothing is enforced until the dry run is approved."""
    monkeypatch.setattr(config, "QUALITY_ENFORCED_RULES", frozenset(SPLIT_CHECKS))


def _days(n, start=date(2024, 1, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _bar(day, px, vol):
    return (day, px, px * 1.01, px * 0.99, px, vol)


def _series(ratio=2.0, adjusted=False, pre=PRE, post=POST):
    """Bars around an ex-date at index `pre`. Unadjusted: price / ratio and volume * ratio from the ex-date.
    Adjusted: the pre-split bars re-based instead (price / ratio, volume * ratio), so there is no jump."""
    days = _days(pre + post)
    bars = []
    for i, d in enumerate(days):
        px = BASE_PX * (1 + 0.004 * ((i % 5) - 2))  # +/-0.8% wiggle, never a large move
        if i >= pre or adjusted:
            bars.append(_bar(d, px / ratio, BASE_VOL * ratio))
        else:
            bars.append(_bar(d, px, BASE_VOL))
    return bars, days[pre] if pre < len(days) else None


def _store(db, symbol, bars):
    db.executemany("INSERT INTO prices_daily VALUES (?,?,?,?,?,?,?,'yahoo','t')", [(symbol, *b) for b in bars])


def _corp_action(db, symbol, ex_date, ratio, action="split"):
    db.execute("INSERT INTO corporate_actions (symbol, ex_date, action, ratio) VALUES (?,?,?,?)",
               (symbol, ex_date, action, ratio))


def _blocked(db):
    return [r[0] for r in db.execute("SELECT symbol FROM blocked_symbols ORDER BY symbol")]


def _clean_rows(db, symbol):
    return db.execute("SELECT COUNT(*) FROM prices_clean WHERE symbol=?", (symbol,)).fetchone()[0]


def _open_blocks(db, symbol):
    return db.execute("SELECT COUNT(*) FROM quality_quarantine WHERE symbol=? AND scope='symbol' "
                      "AND resolved_at IS NULL", (symbol,)).fetchone()[0]


@pytest.mark.parametrize("ratio,action", [(2.0, "split"), (10.0, "split"), (1.5, "bonus"), (0.1, "split")])
def test_unadjusted_split_is_detected_and_blocks_the_symbol(db, enforce, ratio, action):  # (a), plus 1:10
    bars, ex = _series(ratio)
    _store(db, "AAA", bars)
    _corp_action(db, "AAA", ex, ratio, action)
    found = detect_unadjusted_split(db, "AAA")
    assert {f.check_name for f in found} == set(SPLIT_CHECKS)
    assert all(f.bar_date == ex and f.matched_ratio == pytest.approx(ratio) for f in found)
    assert _blocked(db) == ["AAA"]
    assert _clean_rows(db, "AAA") == 0
    assert _clean_rows(db, "BBB") == 0 and _blocked(db) == ["AAA"]  # nothing else touched
    rows = db.execute("SELECT severity, bar_date, detail FROM data_quality_log WHERE check_name IN (?,?)",
                      SPLIT_CHECKS).fetchall()
    assert len(rows) == 2 and {r["severity"] for r in rows} == {"warn"} and {r["bar_date"] for r in rows} == {ex}
    detail = json.loads(rows[0]["detail"])
    assert detail["prev_close"] / detail["close"] == pytest.approx(ratio, rel=0.03)
    assert db.execute("SELECT COUNT(*) FROM prices_daily WHERE symbol='AAA'").fetchone()[0] == PRE + POST  # kept


def test_adjusted_replacement_clears_the_block_automatically(db, enforce):  # (b)
    bars, ex = _series(2.0)
    _store(db, "AAA", bars)
    _corp_action(db, "AAA", ex, 2.0)
    detect_unadjusted_split(db, "AAA")
    assert _blocked(db) == ["AAA"]

    adjusted, _ = _series(2.0, adjusted=True)
    res = replace_symbol_history(db, "AAA", adjusted, source="yahoo", fetched_at="new", split=(ex, 2.0))
    assert (res.rows_inserted, res.still_blocked, res.recheck.findings) == (PRE + POST, False, ())
    assert res.recheck.blocks_resolved == 1
    row = db.execute("SELECT resolution, resolved_at FROM quality_quarantine WHERE scope='symbol'").fetchone()
    assert row["resolution"] == "recheck_clean" and row["resolved_at"]
    assert _blocked(db) == []
    assert _clean_rows(db, "AAA") == PRE + POST
    assert db.execute("SELECT COUNT(*) FROM data_quality_log WHERE check_name='split_recheck'").fetchone()[0] == 1


def test_still_unadjusted_replacement_keeps_the_block(db, enforce):  # (c)
    bars, ex = _series(2.0)
    _store(db, "AAA", bars)
    _corp_action(db, "AAA", ex, 2.0)
    detect_unadjusted_split(db, "AAA")
    res = replace_symbol_history(db, "AAA", bars, source="yahoo", fetched_at="new", split=(ex, 2.0))
    assert res.still_blocked is True and res.recheck.blocks_resolved == 0 and res.recheck.findings
    assert _open_blocks(db, "AAA") == 1
    assert _blocked(db) == ["AAA"] and _clean_rows(db, "AAA") == 0


def test_replacement_that_reveals_a_split_opens_the_block(db, enforce):
    bars, ex = _series(2.0)
    res = replace_symbol_history(db, "AAA", bars, source="yahoo", fetched_at="new", split=(ex, 2.0))
    assert res.still_blocked is True and _blocked(db) == ["AAA"]


def test_clean_symbol_is_never_blocked(db, enforce):  # (d)
    bars, _ = _series(1.0)
    _store(db, "BBB", bars)
    assert detect_unadjusted_split(db, "BBB") == []
    with transaction(db):
        res = recheck_symbol(db, "BBB")
    assert res.still_blocked is False and _blocked(db) == []
    assert _clean_rows(db, "BBB") == PRE + POST


def _with_move(factor, vol_factor=1.0, revert=False):
    bars, _ = _series(1.0)
    out = []
    for i, (d, o, h, low, c, v) in enumerate(bars):
        if i >= PRE and not (revert and i > PRE):
            out.append(_bar(d, c * factor, v * vol_factor))
        else:
            out.append((d, o, h, low, c, v))
    return out


@pytest.mark.parametrize("bars", [
    _with_move(0.6, vol_factor=5.0),       # -40% crash on 5x volume: ratio 1.67 matches no standard ratio
    _with_move(0.5, vol_factor=1.0),       # -50% that holds, but no corporate action and no volume jump
    _with_move(0.5, vol_factor=2.0, revert=True),  # -50% that reverts next day: a bad print, not a split
], ids=["crash_no_ratio", "no_evidence", "reverts"])
def test_genuine_or_bad_print_moves_are_not_split_blocks(db, enforce, bars):
    _store(db, "BBB", bars)
    assert detect_unadjusted_split(db, "BBB") == []
    assert _blocked(db) == [] and _clean_rows(db, "BBB") == PRE + POST


def test_volume_jump_alone_is_evidence(db, enforce):
    bars, _ = _series(2.0)
    _store(db, "AAA", bars)
    found = detect_unadjusted_split(db, "AAA")
    assert [(f.check_name, f.evidence) for f in found] == [("move_unadjusted_split", ("volume_jump",))]


def test_move_needs_enough_sessions_to_show_it_holds(db, enforce):
    bars, _ = _series(2.0, post=config.SPLIT_PERSIST_SESSIONS - 1)
    _store(db, "AAA", bars)
    assert detect_unadjusted_split(db, "AAA") == []


def test_no_duplicate_open_blocks(db, enforce):  # (e)
    bars, ex = _series(2.0)
    _store(db, "AAA", bars)
    _corp_action(db, "AAA", ex, 2.0)
    for _ in range(3):
        detect_unadjusted_split(db, "AAA")
    with transaction(db):
        recheck_symbol(db, "AAA")
    replace_symbol_history(db, "AAA", bars, source="yahoo", fetched_at="new")
    assert _open_blocks(db, "AAA") == 1


def test_not_enforced_by_default_logs_but_does_not_block(db):
    assert config.QUALITY_ENFORCED_RULES == frozenset()
    bars, ex = _series(2.0)
    _store(db, "AAA", bars)
    _corp_action(db, "AAA", ex, 2.0)
    assert detect_unadjusted_split(db, "AAA")
    assert _blocked(db) == [] and _clean_rows(db, "AAA") == PRE + POST
    blocks = {json.loads(r[0])["block"] for r in db.execute(
        "SELECT detail FROM data_quality_log WHERE check_name IN (?,?)", SPLIT_CHECKS)}
    assert blocks == {"not_enforced"}


def test_recheck_refuses_to_run_outside_a_transaction(db):
    with pytest.raises(RuntimeError):
        recheck_symbol(db, "AAA")


def test_corporate_action_without_ratio_is_logged_not_ignored(db, enforce):
    bars, ex = _series(1.0)
    _store(db, "AAA", bars)
    _corp_action(db, "AAA", ex, None)
    assert detect_unadjusted_split(db, "AAA") == []
    assert db.execute("SELECT severity FROM data_quality_log WHERE check_name=?",
                      (quality.CHECK_CA_RATIO_MISSING,)).fetchone()[0] == "info"


def test_failed_recheck_rolls_back_the_whole_replacement(db, monkeypatch):
    bars, _ = _series(1.0)
    _store(db, "AAA", bars)

    def boom(*a, **k):
        raise RuntimeError("recheck failed")

    monkeypatch.setattr("advisor.data.prices.recheck_symbol", boom)
    with pytest.raises(RuntimeError):
        replace_symbol_history(db, "AAA", bars[:5], source="yahoo", fetched_at="new")
    assert db.execute("SELECT COUNT(*) FROM prices_daily WHERE symbol='AAA'").fetchone()[0] == PRE + POST
    assert not db.in_transaction
