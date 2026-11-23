import json

import pytest

from advisor import config
from advisor.data.runs import affected_symbol_counts, finish_run, start_run

FULL = {k: 0 for k in config.RUN_METADATA_REQUIRED_KEYS}


def test_finish_run_requires_every_affected_count(db):
    rid = start_run(db, "data_update")
    bad_cases = [{}, {"symbols_blocked": 0}]
    bad_cases += [{**FULL, key: -1} for key in FULL]  # negative
    bad_cases += [{**FULL, key: True} for key in FULL]  # bool is not a count
    bad_cases += [{**FULL, key: "0"} for key in FULL]  # string
    bad_cases += [{k: v for k, v in FULL.items() if k != key} for key in FULL]  # one key missing
    for bad in bad_cases:
        with pytest.raises(ValueError):
            finish_run(db, rid, "ok", bad)
    assert db.execute("SELECT status FROM runs WHERE run_id=?", (rid,)).fetchone()[0] == "running"


def test_finish_run_stores_metadata_and_rejects_unknown_run(db):
    rid = start_run(db, "daily")
    finish_run(db, rid, "ok", {**FULL, "symbols_blocked": 2, "symbols_entry_suppressed": 5, "extra": "x"})
    row = db.execute("SELECT status, finished_at, metadata_json FROM runs WHERE run_id=?", (rid,)).fetchone()
    assert row["status"] == "ok" and row["finished_at"]
    assert json.loads(row["metadata_json"])["symbols_entry_suppressed"] == 5
    with pytest.raises(ValueError):
        finish_run(db, "nope", "ok", FULL)


def _q(db, symbol, scope, a=None, b=None, resolved=None, check="c"):
    db.execute(
        "INSERT INTO quality_quarantine (symbol, scope, from_date, to_date, check_name, created_at, resolved_at) "
        "VALUES (?, ?, ?, ?, ?, 't', ?)", (symbol, scope, a, b, check, resolved))


def test_affected_symbol_counts(db):
    _q(db, "AAA", "symbol")
    _q(db, "BBB", "symbol", resolved="r")  # resolved: not counted
    _q(db, "AAA", "entry", "2026-01-06", "2026-01-06")
    _q(db, "BBB", "entry", "2026-01-06", "2026-01-06")
    _q(db, "BBB", "entry", "2026-01-07", "2026-01-07")  # other day
    first = affected_symbol_counts(db, "2026-01-06")
    assert (first["symbols_blocked"], first["symbols_entry_suppressed"]) == (1, 2)
    later = affected_symbol_counts(db, "2026-01-08")
    assert (later["symbols_blocked"], later["symbols_entry_suppressed"]) == (1, 0)
    assert set(first) == set(config.RUN_METADATA_REQUIRED_KEYS)


def _bars(db, n, start_ordinal=1):
    """n consecutive daily bars for AAA, dated 2024-01-01 + i days (distinct stored dates)."""
    from datetime import date, timedelta
    d0 = date(2024, 1, 1)
    for i in range(start_ordinal - 1, start_ordinal - 1 + n):
        day = (d0 + timedelta(days=i)).isoformat()
        db.execute("INSERT INTO prices_daily VALUES (?,?,?,?,?,?,?,?,?)",
                   ("AAA", day, 1.0, 1.0, 1.0, 1.0, 1.0, "yahoo", "t"))
    return d0


def test_move_unclassified_counts_total_and_unreviewed_within_the_review_window(db, monkeypatch):
    monkeypatch.setattr(config, "MOVE_UNCLASSIFIED_REVIEW_SESSIONS", 5)
    _bars(db, 10)  # dates 2024-01-01 .. 2024-01-10; the last 5 are 01-06 .. 01-10
    mu = "move_unclassified"
    _q(db, "AAA", "entry", "2024-01-02", "2024-01-02", check=mu)  # old, open: counted in total, outside window
    _q(db, "AAA", "entry", "2024-01-06", "2024-01-06", check=mu)  # inside window, open: unreviewed
    _q(db, "BBB", "entry", "2024-01-09", "2024-01-09", check=mu)  # inside window, open: unreviewed
    _q(db, "BBB", "entry", "2024-01-10", "2024-01-10", resolved="r", check=mu)  # inside window, reviewed
    _q(db, "BBB", "entry", "2024-01-10", "2024-01-10", check="move_extreme")  # different check: not counted
    counts = affected_symbol_counts(db, "2024-01-10")
    assert counts["move_unclassified_events"] == 4
    assert counts["move_unclassified_unreviewed_recent"] == 2
    as_of_07 = affected_symbol_counts(db, "2024-01-07")  # window is 01-03..01-07; later events do not exist yet
    assert (as_of_07["move_unclassified_events"], as_of_07["move_unclassified_unreviewed_recent"]) == (2, 1)


def test_unreviewed_counts_everything_when_fewer_sessions_are_stored_than_the_window(db):
    _bars(db, 3)
    _q(db, "AAA", "entry", "2024-01-01", "2024-01-01", check="move_unclassified")
    assert affected_symbol_counts(db, "2024-01-03")["move_unclassified_unreviewed_recent"] == 1
