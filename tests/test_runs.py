import json

import pytest

from advisor.data.runs import affected_symbol_counts, finish_run, start_run


def test_finish_run_requires_affected_symbol_counts(db):
    rid = start_run(db, "data_update")
    for bad in ({}, {"symbols_blocked": 0}, {"symbols_blocked": 0, "symbols_entry_suppressed": -1},
                {"symbols_blocked": True, "symbols_entry_suppressed": 0},
                {"symbols_blocked": "0", "symbols_entry_suppressed": 0}):
        with pytest.raises(ValueError):
            finish_run(db, rid, "ok", bad)
    assert db.execute("SELECT status FROM runs WHERE run_id=?", (rid,)).fetchone()[0] == "running"


def test_finish_run_stores_metadata_and_rejects_unknown_run(db):
    rid = start_run(db, "daily")
    finish_run(db, rid, "ok", {"symbols_blocked": 2, "symbols_entry_suppressed": 5, "extra": "x"})
    row = db.execute("SELECT status, finished_at, metadata_json FROM runs WHERE run_id=?", (rid,)).fetchone()
    assert row["status"] == "ok" and row["finished_at"]
    assert json.loads(row["metadata_json"])["symbols_entry_suppressed"] == 5
    with pytest.raises(ValueError):
        finish_run(db, "nope", "ok", {"symbols_blocked": 0, "symbols_entry_suppressed": 0})


def test_affected_symbol_counts(db):
    q = ("INSERT INTO quality_quarantine (symbol, scope, from_date, to_date, check_name, created_at, resolved_at) "
         "VALUES (?, ?, ?, ?, 'c', 't', ?)")
    db.execute(q, ("AAA", "symbol", None, None, None))
    db.execute(q, ("BBB", "symbol", None, None, "r"))            # resolved: not counted
    db.execute(q, ("AAA", "entry", "2026-01-06", "2026-01-06", None))
    db.execute(q, ("BBB", "entry", "2026-01-06", "2026-01-06", None))
    db.execute(q, ("BBB", "entry", "2026-01-07", "2026-01-07", None))  # other day
    assert affected_symbol_counts(db, "2026-01-06") == {"symbols_blocked": 1, "symbols_entry_suppressed": 2}
    assert affected_symbol_counts(db, "2026-01-08") == {"symbols_blocked": 1, "symbols_entry_suppressed": 0}
