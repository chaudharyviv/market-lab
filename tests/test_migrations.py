import sqlite3

import pytest

from advisor.data.db import connect
from advisor.data.migrate import MIGRATIONS_DIR, apply_migrations

BAR = ("AAA", "2026-01-05", 10.0, 11.0, 9.0, 10.5, 1000.0, "yahoo", "2026-01-06T00:00:00Z")


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}


def test_both_migrations_apply_and_rerun_is_a_noop(tmp_path):
    conn = connect(tmp_path / "m.db")
    assert apply_migrations(conn) == ["001_data_tables", "002_quality_support"]
    assert apply_migrations(conn) == []
    assert {"runs", "instruments", "index_membership", "prices_daily", "corporate_actions", "data_quality_log",
            "trading_calendar", "quality_quarantine", "prices_clean", "blocked_symbols", "entry_suppressed",
            "schema_migrations"} <= _tables(conn)
    assert [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")] == [
        "001_data_tables", "002_quality_support"]
    conn.close()


def test_migration_files_contain_no_pragma():
    for p in MIGRATIONS_DIR.glob("*.sql"):
        code = [ln for ln in p.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("--")]
        assert "PRAGMA" not in " ".join(code).upper(), p.name


def test_failing_migration_leaves_nothing_behind(tmp_path):
    d = tmp_path / "mig"
    d.mkdir()
    (d / "001_ok.sql").write_text("CREATE TABLE a (x INTEGER);", encoding="utf-8")
    (d / "002_bad.sql").write_text("CREATE TABLE b (x INTEGER); CREATE TABLE a (x INTEGER);", encoding="utf-8")
    conn = connect(tmp_path / "m.db")
    with pytest.raises(sqlite3.OperationalError):
        apply_migrations(conn, d)
    assert "a" in _tables(conn) and "b" not in _tables(conn)  # 001 stays, 002 rolled back entirely
    assert [r[0] for r in conn.execute("SELECT version FROM schema_migrations")] == ["001_ok"]
    assert not conn.in_transaction
    conn.close()


def test_bad_names_and_numbering_are_rejected(tmp_path):
    conn = connect(tmp_path / "m.db")
    d = tmp_path / "mig"
    d.mkdir()
    (d / "002_x.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(ValueError):
        apply_migrations(conn, d)
    (d / "002_x.sql").rename(d / "x.sql")
    with pytest.raises(ValueError):
        apply_migrations(conn, d)
    conn.close()


def test_volume_is_nullable_but_prices_are_not(db):
    db.execute("INSERT INTO prices_daily VALUES (?,?,?,?,?,?,?,?,?)", BAR[:6] + (None,) + BAR[7:])
    assert db.execute("SELECT volume FROM prices_daily").fetchone()[0] is None
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO prices_daily VALUES (?,?,?,?,?,?,?,?,?)",
                   ("AAA", "2026-01-06", None, 11.0, 9.0, 10.5, 1.0, "yahoo", "t"))


def test_prices_daily_requires_an_instrument(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO prices_daily VALUES (?,?,?,?,?,?,?,?,?)", ("ZZZ",) + BAR[1:])


def test_check_constraints(db):
    with pytest.raises(sqlite3.IntegrityError):  # severity
        db.execute("INSERT INTO data_quality_log (ts, check_name, severity) VALUES ('t','c','fatal')")
    with pytest.raises(sqlite3.IntegrityError):  # dividends are not stored
        db.execute("INSERT INTO corporate_actions (symbol, ex_date, action) VALUES ('AAA','2026-01-01','dividend')")
    with pytest.raises(sqlite3.IntegrityError):  # instrument kind
        db.execute("INSERT INTO instruments (symbol, kind) VALUES ('X', 'etf')")
    with pytest.raises(sqlite3.IntegrityError):  # bar scope needs dates
        db.execute("INSERT INTO quality_quarantine (symbol, scope, check_name, created_at) "
                   "VALUES ('AAA','bar','c','t')")
    with pytest.raises(sqlite3.IntegrityError):  # entry scope is a single day
        db.execute("INSERT INTO quality_quarantine (symbol, scope, from_date, to_date, check_name, created_at) "
                   "VALUES ('AAA','entry','2026-01-05','2026-01-06','c','t')")


def test_run_id_column_index_and_membership_index_exist(db):
    cols = {r[1] for r in db.execute("PRAGMA table_info(data_quality_log)")}
    assert {"run_id", "bar_date"} <= cols
    idx = {r[1]: [c[2] for c in db.execute(f"PRAGMA index_info({r[1]})")]
           for r in db.execute("PRAGMA index_list(index_membership)")}
    assert ["index_name", "from_date", "to_date"] in idx.values()
    db.execute("INSERT INTO runs (run_id, kind, started_at) VALUES ('r1','t','now')")
    db.execute("INSERT INTO data_quality_log (ts, run_id, check_name, severity) VALUES ('t','r1','c','info')")
    with pytest.raises(sqlite3.IntegrityError):  # run_id must reference a real run
        db.execute("INSERT INTO data_quality_log (ts, run_id, check_name, severity) VALUES ('t','nope','c','info')")


def _quarantine(db, scope, a, b, resolved=False):
    db.execute(
        "INSERT INTO quality_quarantine (symbol, scope, from_date, to_date, check_name, created_at, resolved_at) "
        "VALUES ('AAA', ?, ?, ?, 'c', 't', ?)", (scope, a, b, "r" if resolved else None))


def _three_bars(db):
    for d in ("2026-01-05", "2026-01-06", "2026-01-07"):
        db.execute("INSERT INTO prices_daily VALUES (?,?,?,?,?,?,?,?,?)", ("AAA", d) + BAR[2:])


def _clean_dates(db):
    return [r[0] for r in db.execute("SELECT date FROM prices_clean WHERE symbol='AAA' ORDER BY date")]


def test_prices_clean_hides_bar_and_span_by_date(db):
    _three_bars(db)
    assert len(_clean_dates(db)) == 3
    _quarantine(db, "bar", "2026-01-05", "2026-01-05")
    assert _clean_dates(db) == ["2026-01-06", "2026-01-07"]
    _quarantine(db, "span", "2026-01-06", "2026-01-07")
    assert _clean_dates(db) == []


def test_entry_scope_keeps_the_bar_visible(db):
    _three_bars(db)
    _quarantine(db, "entry", "2026-01-06", "2026-01-06")
    assert len(_clean_dates(db)) == 3  # a genuine-looking big move keeps its bar
    assert [tuple(r) for r in db.execute("SELECT symbol, date FROM entry_suppressed")] == [("AAA", "2026-01-06")]
    assert db.execute("SELECT COUNT(*) FROM blocked_symbols").fetchone()[0] == 0


def test_symbol_block_hides_every_bar_of_that_symbol_only(db):
    _three_bars(db)
    db.execute("INSERT INTO prices_daily VALUES (?,?,?,?,?,?,?,?,?)", ("BBB", "2026-01-05") + BAR[2:])
    _quarantine(db, "symbol", None, None)
    assert _clean_dates(db) == []  # suspected corruption: prices_clean is the single chokepoint
    assert db.execute("SELECT COUNT(*) FROM prices_clean WHERE symbol='BBB'").fetchone()[0] == 1
    assert [r[0] for r in db.execute("SELECT symbol FROM blocked_symbols")] == ["AAA"]


def test_resolved_quarantines_hide_and_block_nothing(db):
    _three_bars(db)
    for scope, a, b in (("bar", "2026-01-05", "2026-01-05"), ("symbol", None, None),
                        ("entry", "2026-01-06", "2026-01-06")):
        _quarantine(db, scope, a, b, resolved=True)
    assert len(_clean_dates(db)) == 3
    assert db.execute("SELECT COUNT(*) FROM blocked_symbols").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM entry_suppressed").fetchone()[0] == 0
