"""Unadjusted split/bonus events (corp_action_unadjusted), split-like price patterns without an event row
(move_unclassified -> one-day entry suppression), the mandatory re-check after a history replacement, and the
conversion of an event a re-fetch cannot fix into a span.

Synthetic series: PRE bars near 1000 INR, then an ex-date where the stored price drops by the ratio. Volume is
irrelevant to every rule here (it is not split evidence), so it is flat unless a test says otherwise."""
import json
from datetime import date, timedelta

import pytest

from advisor import config
from advisor.data import quality
from advisor.data.db import transaction
from advisor.data.prices import replace_symbol_history
from advisor.data.quality import CHECK_CA_UNADJUSTED, CHECK_MOVE_UNCLASSIFIED, detect_unadjusted_split, recheck_symbol

PRE, POST = 60, 10
BASE_PX, BASE_VOL = 1000.0, 10_000.0


@pytest.fixture
def enforce(monkeypatch):
    """Turn the rules on. In the shipped config nothing is enforced until the dry run is approved."""
    monkeypatch.setattr(config, "QUALITY_ENFORCED_RULES", frozenset({CHECK_CA_UNADJUSTED, CHECK_MOVE_UNCLASSIFIED}))


def _days(n, start=date(2015, 1, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _bar(day, px, vol=BASE_VOL):
    return (day, px, px * 1.01, px * 0.99, px, vol)


def _px(i):
    return BASE_PX * (1 + 0.004 * ((i % 5) - 2))  # +/-0.8% wiggle, never a large move


def _series(ratio=2.0, adjusted=False, pre=PRE, post=POST):
    """Bars around an ex-date at index `pre`. Unadjusted: price / ratio from the ex-date on. Adjusted: the
    pre-split bars are re-based instead, so there is no jump."""
    days = _days(pre + post)
    bars = [_bar(d, _px(i) / ratio if (i >= pre or adjusted) else _px(i)) for i, d in enumerate(days)]
    return bars, days[pre]


def _store(db, symbol, bars):
    db.executemany("INSERT INTO prices_daily VALUES (?,?,?,?,?,?,?,'yahoo','t')", [(symbol, *b) for b in bars])


def _corp_action(db, symbol, ex_date, ratio, action="split"):
    db.execute("INSERT INTO corporate_actions (symbol, ex_date, action, ratio) VALUES (?,?,?,?)",
               (symbol, ex_date, action, ratio))


def _blocked(db):
    return [r[0] for r in db.execute("SELECT symbol FROM blocked_symbols ORDER BY symbol")]


def _clean_dates(db, symbol):
    return [r[0] for r in db.execute("SELECT date FROM prices_clean WHERE symbol=? ORDER BY date", (symbol,))]


def _entries(db, symbol):
    return [r[0] for r in db.execute("SELECT date FROM entry_suppressed WHERE symbol=? ORDER BY date", (symbol,))]


def _open(db, symbol, scope):
    return db.execute("SELECT from_date, to_date, reason FROM quality_quarantine WHERE symbol=? AND scope=? "
                      "AND check_name=? AND resolved_at IS NULL", (symbol, scope, CHECK_CA_UNADJUSTED)).fetchall()


# --- detection with an event row ------------------------------------------------------------------------------
@pytest.mark.parametrize("ratio,action", [(2.0, "split"), (10.0, "split"), (1.5, "bonus"), (0.1, "split"),
                                          (1.25, "bonus")])
def test_event_row_with_unadjusted_prices_blocks_the_symbol(db, enforce, ratio, action):
    bars, ex = _series(ratio)
    _store(db, "AAA", bars)
    _corp_action(db, "AAA", ex, ratio, action)
    found = detect_unadjusted_split(db, "AAA")
    assert [(f.check_name, f.ex_date, f.outcome) for f in found] == [(CHECK_CA_UNADJUSTED, ex, "block_opened")]
    assert found[0].matched_ratio == ratio
    assert _blocked(db) == ["AAA"] and _clean_dates(db, "AAA") == []
    assert _entries(db, "AAA") == []  # the event explains the jump: no move_unclassified for that bar
    (row,) = db.execute("SELECT severity, bar_date, detail FROM data_quality_log WHERE check_name=?",
                        (CHECK_CA_UNADJUSTED,)).fetchall()
    assert row["severity"] == "warn" and row["bar_date"] == ex
    detail = json.loads(row["detail"])
    assert detail["prev_close"] / detail["close"] == pytest.approx(ratio, rel=0.03)
    assert db.execute("SELECT COUNT(*) FROM prices_daily WHERE symbol='AAA'").fetchone()[0] == PRE + POST


@pytest.mark.parametrize("ratio", [2.0, 10.0, 1.5, 0.1])
def test_split_pattern_without_event_row_only_suppresses_entry(db, enforce, ratio):
    bars, ex = _series(ratio)
    _store(db, "AAA", bars)
    assert detect_unadjusted_split(db, "AAA") == []
    assert _blocked(db) == [] and len(_clean_dates(db, "AAA")) == PRE + POST  # every bar visible
    assert _entries(db, "AAA") == [ex]
    sev = db.execute("SELECT severity FROM data_quality_log WHERE check_name=?", (CHECK_MOVE_UNCLASSIFIED,))
    assert [r[0] for r in sev] == ["warn"]


def test_pattern_is_decided_from_day_t_alone(db, enforce):
    """No look-ahead: the entry suppression exists at the close of the ex-date, before any later bar."""
    bars, ex = _series(2.0)
    _store(db, "AAA", bars[:PRE + 1])
    detect_unadjusted_split(db, "AAA")
    assert _entries(db, "AAA") == [ex]


def test_event_row_with_adjusted_prices_does_nothing(db, enforce):
    bars, ex = _series(2.0, adjusted=True)
    _store(db, "AAA", bars)
    _corp_action(db, "AAA", ex, 2.0)
    assert detect_unadjusted_split(db, "AAA") == []
    assert _blocked(db) == [] and _entries(db, "AAA") == []


# --- re-check after replacement -------------------------------------------------------------------------------
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
    assert _blocked(db) == [] and len(_clean_dates(db, "AAA")) == PRE + POST


def test_first_detection_inside_a_replacement_opens_the_block(db, enforce):
    bars, ex = _series(2.0, post=POST)
    res = replace_symbol_history(db, "AAA", bars, source="yahoo", fetched_at="new", split=(ex, 2.0))
    assert [f.outcome for f in res.recheck.findings] == ["block_opened"]
    assert res.still_blocked is True and _open(db, "AAA", "span") == []


def _unfixable(db):
    """Detected once, then re-fetched with the same unadjusted vendor history."""
    bars, ex = _series(2.0, post=POST)
    _store(db, "AAA", bars)
    _corp_action(db, "AAA", ex, 2.0)
    detect_unadjusted_split(db, "AAA")
    res = replace_symbol_history(db, "AAA", bars, source="yahoo", fetched_at="new", split=(ex, 2.0))
    return bars, ex, res


def test_still_unadjusted_after_refetch_converts_the_block_to_a_span(db, enforce):  # (c)
    bars, ex, res = _unfixable(db)
    assert [f.outcome for f in res.recheck.findings] == ["converted_to_span"]
    assert res.still_blocked is False and res.recheck.spans_opened == 1
    assert _blocked(db) == []
    (blk,) = db.execute("SELECT resolution FROM quality_quarantine WHERE scope='symbol'").fetchall()
    assert blk[0] == "converted_to_span"
    (span,) = _open(db, "AAA", "span")
    assert (span["from_date"], span["to_date"]) == (bars[0][0], bars[PRE - 1][0])  # up to the last bar before
    reason = json.loads(span["reason"])
    assert reason["ex_date"] == ex and reason["jump_date"] == ex and "could not fix" in reason["note"]
    assert _clean_dates(db, "AAA") == [b[0] for b in bars[PRE:]]  # old-basis bars hidden, ex-date on visible


def test_span_is_idempotent_across_rechecks_and_detections(db, enforce):
    _unfixable(db)
    for _ in range(3):
        detect_unadjusted_split(db, "AAA")
        with transaction(db):
            res = recheck_symbol(db, "AAA")
        assert [f.outcome for f in res.findings] == ["span_open"]
    assert len(_open(db, "AAA", "span")) == 1 and _open(db, "AAA", "symbol") == [] and _blocked(db) == []


def test_later_replacement_recreates_the_span_instead_of_stacking(db, enforce):
    bars, ex, _ = _unfixable(db)
    res = replace_symbol_history(db, "AAA", bars, source="yahoo", fetched_at="newer", split=(ex, 2.0))
    assert [f.outcome for f in res.recheck.findings] == ["converted_to_span"] and res.still_blocked is False
    spans = db.execute("SELECT resolution FROM quality_quarantine WHERE scope='span' ORDER BY id").fetchall()
    assert [r[0] for r in spans] == ["history_replaced", None]  # old one resolved by the replacement, one open


def test_later_adjusted_replacement_removes_the_span(db, enforce):
    _, ex, _ = _unfixable(db)
    adjusted, _ = _series(2.0, adjusted=True, post=POST)
    res = replace_symbol_history(db, "AAA", adjusted, source="yahoo", fetched_at="newer", split=(ex, 2.0))
    assert res.recheck.findings == () and _open(db, "AAA", "span") == [] and _blocked(db) == []
    assert len(_clean_dates(db, "AAA")) == PRE + POST


def test_plain_recheck_without_refetch_never_converts(db, enforce):
    bars, ex = _series(2.0, post=POST)
    _store(db, "AAA", bars)
    _corp_action(db, "AAA", ex, 2.0)
    detect_unadjusted_split(db, "AAA")
    with transaction(db):
        res = recheck_symbol(db, "AAA")
    assert [f.outcome for f in res.findings] == ["block_already_open"] and _blocked(db) == ["AAA"]


# --- event ex-date offset from the real jump ---------------------------------------------------------------
@pytest.mark.parametrize("offset", [-2, -1, 1, 2], ids=["2_early", "1_early", "1_late", "2_late"])
def test_event_date_within_the_window_matches_the_real_jump(db, enforce, offset):
    bars, jump = _series(2.0)
    days = [b[0] for b in bars]
    recorded = days[PRE + offset]
    _store(db, "AAA", bars)
    _corp_action(db, "AAA", recorded, 2.0)
    (f,) = detect_unadjusted_split(db, "AAA")
    assert (f.ex_date, f.bar_date, f.outcome) == (recorded, jump, "block_opened")
    assert _blocked(db) == ["AAA"] and _entries(db, "AAA") == []  # explained by the event: not unclassified
    res = replace_symbol_history(db, "AAA", bars, source="yahoo", fetched_at="new")  # re-fetch, same vendor data
    assert [x.outcome for x in res.recheck.findings] == ["converted_to_span"] and res.still_blocked is False
    (span,) = _open(db, "AAA", "span")
    assert span["to_date"] == days[PRE - 1]  # follows the REAL jump bar, not the recorded ex-date
    assert _clean_dates(db, "AAA") == days[PRE:]


@pytest.mark.parametrize("offset", [-3, 3], ids=["3_early", "3_late"])
def test_event_date_outside_the_window_does_not_match(db, enforce, offset):
    bars, jump = _series(2.0)
    _store(db, "AAA", bars)
    _corp_action(db, "AAA", bars[PRE + offset][0], 2.0)
    assert detect_unadjusted_split(db, "AAA") == []
    assert _blocked(db) == [] and _entries(db, "AAA") == [jump]  # only move_unclassified, one-day suppression


def test_unrelated_event_in_the_window_does_not_match(db, enforce):
    bars, jump = _series(2.0)
    _store(db, "AAA", bars)
    _corp_action(db, "AAA", bars[PRE - 1][0], 5.0)  # an event one session away, but a different ratio
    assert detect_unadjusted_split(db, "AAA") == []
    assert _blocked(db) == [] and _entries(db, "AAA") == [jump]


def test_jump_right_after_the_first_bar_hides_only_that_bar(db, enforce):
    bars, jump = _series(2.0, pre=1)
    _store(db, "AAA", bars)
    _corp_action(db, "AAA", jump, 2.0)
    detect_unadjusted_split(db, "AAA")
    res = replace_symbol_history(db, "AAA", bars, source="yahoo", fetched_at="new")
    assert [x.outcome for x in res.recheck.findings] == ["converted_to_span"]
    (span,) = _open(db, "AAA", "span")
    assert (span["from_date"], span["to_date"]) == (bars[0][0], bars[0][0])
    assert _clean_dates(db, "AAA") == [b[0] for b in bars[1:]]


def test_event_on_the_first_stored_bar_has_nothing_to_hide(db, enforce):
    """History starts on the ex-date (only post-event bars stored): no jump is observable, so nothing happens."""
    bars, ex = _series(2.0)
    post_only = bars[PRE:]
    _store(db, "AAA", post_only)
    _corp_action(db, "AAA", ex, 2.0)
    assert detect_unadjusted_split(db, "AAA") == []
    res = replace_symbol_history(db, "AAA", post_only, source="yahoo", fetched_at="new")
    assert res.recheck.findings == () and res.still_blocked is False
    assert db.execute("SELECT COUNT(*) FROM quality_quarantine").fetchone()[0] == 0


# --- crashes: never a symbol block ----------------------------------------------------------------------------
SPIKE = (6.0, 3.0, 1.5, 1.1)  # volume multiples on the crash day and after, then back to normal
SLOW = tuple(6.0 - 5.0 * k / 19 for k in range(20))  # 6x down to 1x over 20 sessions, then normal
CRASHES = [(25, SPIKE), (33, SPIKE), (50, SPIKE), (50, SLOW)]
CRASH_IDS = ["-25%_spike", "-33%_spike", "-50%_spike", "-50%_slow_decay"]


def _crash(drop_pct, profile, post=30):
    days = _days(PRE + post)
    out = []
    for i, d in enumerate(days):
        if i < PRE:
            out.append(_bar(d, _px(i)))
        else:
            k = i - PRE
            out.append(_bar(d, _px(i) * (1 - drop_pct / 100), BASE_VOL * (profile[k] if k < len(profile) else 1)))
    return out, days


@pytest.mark.parametrize("drop_pct,profile", CRASHES, ids=CRASH_IDS)
def test_crash_without_event_row_is_entry_suppression_only(db, enforce, drop_pct, profile):
    bars, days = _crash(drop_pct, profile)
    _store(db, "BBB", bars)
    assert detect_unadjusted_split(db, "BBB") == []
    with transaction(db):
        assert recheck_symbol(db, "BBB").still_blocked is False
    assert _blocked(db) == [] and len(_clean_dates(db, "BBB")) == len(bars)  # the crash bar stays visible
    assert _entries(db, "BBB") == [days[PRE]]  # each drop matches a ratio (4/3, 1.5, 2): one-day suppression


@pytest.mark.parametrize("drop_pct,profile", CRASHES, ids=CRASH_IDS)
@pytest.mark.parametrize("row", ["earlier_adjusted_event", "event_on_crash_day_other_ratio"])
def test_crash_with_an_event_row_but_no_unadjusted_jump_never_blocks(db, enforce, drop_pct, profile, row):
    bars, days = _crash(drop_pct, profile)
    _store(db, "BBB", bars)
    if row == "earlier_adjusted_event":
        _corp_action(db, "BBB", days[30], 2.0)  # a real split whose history is already adjusted (no jump)
    else:
        _corp_action(db, "BBB", days[PRE], 5.0)  # an event that day, but prices did not jump by its ratio
    assert detect_unadjusted_split(db, "BBB") == []
    assert _blocked(db) == [] and len(_clean_dates(db, "BBB")) == len(bars)


# --- other guarantees -----------------------------------------------------------------------------------------
def test_clean_symbol_is_never_touched(db, enforce):
    bars, _ = _series(1.0)
    _store(db, "BBB", bars)
    assert detect_unadjusted_split(db, "BBB") == []
    with transaction(db):
        assert recheck_symbol(db, "BBB").still_blocked is False
    assert _blocked(db) == [] and _entries(db, "BBB") == [] and len(_clean_dates(db, "BBB")) == PRE + POST


def test_no_duplicate_blocks_or_entries(db, enforce):  # (e)
    bars, ex = _series(2.0)
    _store(db, "AAA", bars)
    _corp_action(db, "AAA", ex, 2.0)
    _store(db, "BBB", bars)  # same pattern, no event row
    for _ in range(3):
        detect_unadjusted_split(db, "AAA")
        detect_unadjusted_split(db, "BBB")
    with transaction(db):
        recheck_symbol(db, "AAA")
    assert len(_open(db, "AAA", "symbol")) == 1
    assert db.execute("SELECT COUNT(*) FROM quality_quarantine WHERE symbol='BBB' AND scope='entry'").fetchone()[0] == 1


def test_not_enforced_by_default_logs_but_excludes_nothing(db):
    assert config.QUALITY_ENFORCED_RULES == frozenset()
    bars, ex = _series(2.0)
    _store(db, "AAA", bars)
    _corp_action(db, "AAA", ex, 2.0)
    _store(db, "BBB", bars)
    assert [f.outcome for f in detect_unadjusted_split(db, "AAA")] == ["not_enforced"]
    detect_unadjusted_split(db, "BBB")
    assert db.execute("SELECT COUNT(*) FROM quality_quarantine").fetchone()[0] == 0
    q = "SELECT detail FROM data_quality_log WHERE check_name IN (?,?)"
    rows = db.execute(q, (CHECK_CA_UNADJUSTED, CHECK_MOVE_UNCLASSIFIED))
    outcomes = {json.loads(r[0])["outcome"] for r in rows}
    assert outcomes == {"not_enforced"}


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


def test_event_ratio_is_never_used_to_adjust_prices(db, enforce):
    bars, ex = _series(2.0)
    _store(db, "AAA", bars)
    _corp_action(db, "AAA", ex, 2.0)
    before = db.execute("SELECT * FROM prices_daily ORDER BY date").fetchall()
    detect_unadjusted_split(db, "AAA")
    with transaction(db):
        recheck_symbol(db, "AAA")
    assert [tuple(r) for r in db.execute("SELECT * FROM prices_daily ORDER BY date")] == [tuple(r) for r in before]


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
