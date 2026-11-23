import sqlite3
from datetime import date

import pytest

from advisor.data.prices import ReplaceResult, replace_symbol_history


def _seed(db, symbol, days, base=100.0):
    for d in days:
        db.execute("INSERT INTO prices_daily VALUES (?,?,?,?,?,?,?,?,?)",
                   (symbol, d, base, base + 1, base - 1, base, 500.0, "yahoo", "old"))


def _bars(db, symbol):
    q = "SELECT date, close FROM prices_daily WHERE symbol=? ORDER BY date"
    return [tuple(r) for r in db.execute(q, (symbol,))]


def test_replaces_whole_history_and_records_the_split(db):
    _seed(db, "AAA", ["2026-01-05", "2026-01-06", "2026-01-07"])
    _seed(db, "BBB", ["2026-01-05"], base=7.0)
    new = [(date(2026, 1, 6), 50.0, 51.0, 49.0, 50.0, 1000.0), ("2026-01-08", 52.0, 53.0, 51.0, 52.0, None)]
    res = replace_symbol_history(db, "AAA", new, source="yahoo", fetched_at="new", split=(date(2026, 1, 7), 2.0))
    assert isinstance(res, ReplaceResult)
    assert (res.rows_inserted, res.still_blocked) == (2, False)
    assert _bars(db, "AAA") == [("2026-01-06", 50.0), ("2026-01-08", 52.0)]  # 01-05 and 01-07 are gone
    assert db.execute("SELECT volume FROM prices_daily WHERE date='2026-01-08'").fetchone()[0] is None
    assert _bars(db, "BBB") == [("2026-01-05", 7.0)]  # other symbols untouched
    ca = db.execute("SELECT ex_date, action, ratio FROM corporate_actions").fetchall()
    assert [tuple(r) for r in ca] == [("2026-01-07", "split", 2.0)]
    sev = db.execute("SELECT severity FROM data_quality_log WHERE check_name='history_replaced'").fetchone()[0]
    assert sev == "info"


def test_failure_mid_insert_leaves_old_history_unchanged(db):
    _seed(db, "AAA", ["2026-01-05", "2026-01-06"])
    bad = [("2026-01-07", 1.0, 2.0, 0.5, 1.5, 10.0), ("2026-01-08", None, 2.0, 0.5, 1.5, 10.0)]  # NULL open
    with pytest.raises(sqlite3.IntegrityError):
        replace_symbol_history(db, "AAA", bad, source="yahoo", fetched_at="new", split=("2026-01-07", 2.0))
    assert _bars(db, "AAA") == [("2026-01-05", 100.0), ("2026-01-06", 100.0)]
    assert db.execute("SELECT COUNT(*) FROM corporate_actions").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM data_quality_log").fetchone()[0] == 0
    assert not db.in_transaction


def test_duplicate_dates_in_new_history_roll_back(db):
    _seed(db, "AAA", ["2026-01-05"])
    dup = [("2026-01-06", 1.0, 2.0, 0.5, 1.5, 1.0), ("2026-01-06", 1.0, 2.0, 0.5, 1.5, 1.0)]
    with pytest.raises(sqlite3.IntegrityError):
        replace_symbol_history(db, "AAA", dup, source="yahoo", fetched_at="new")
    assert _bars(db, "AAA") == [("2026-01-05", 100.0)]


def test_empty_history_is_refused_before_touching_anything(db):
    _seed(db, "AAA", ["2026-01-05"])
    with pytest.raises(ValueError):
        replace_symbol_history(db, "AAA", [], source="yahoo", fetched_at="new")
    assert _bars(db, "AAA") == [("2026-01-05", 100.0)]


def test_unknown_symbol_rolls_back(db):
    with pytest.raises(sqlite3.IntegrityError):
        replace_symbol_history(db, "ZZZ", [("2026-01-06", 1.0, 2.0, 0.5, 1.5, 1.0)], source="yahoo", fetched_at="n")


def test_open_bar_quarantines_are_resolved_but_symbol_blocks_are_not(db):
    _seed(db, "AAA", ["2026-01-05"])
    for scope, a in (("bar", "2026-01-05"), ("symbol", None)):
        db.execute("INSERT INTO quality_quarantine (symbol, scope, from_date, to_date, check_name, created_at) "
                   "VALUES ('AAA', ?, ?, ?, 'c', 't')", (scope, a, a))
    out = replace_symbol_history(db, "AAA", [("2026-01-06", 1.0, 2.0, 0.5, 1.5, 1.0)], source="yahoo",
                                 fetched_at="n")
    res = {r["scope"]: r["resolution"] for r in db.execute("SELECT scope, resolution FROM quality_quarantine")}
    assert res == {"bar": "history_replaced", "symbol": None}  # the re-check only clears its own split blocks
    assert out.still_blocked is True
