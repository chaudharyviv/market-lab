"""Architecture rules, enforced by scanning source: signals/backtests read prices_clean, never prices_daily,
and every connection comes from advisor.data.db.connect."""
import re
from pathlib import Path

from advisor import config

ADVISOR_DIR = config.PROJECT_ROOT / "advisor"
RAW_TABLE = re.compile(r"\bprices_daily\b")
DIRECT_CONNECT = re.compile(r"\bsqlite3\.connect\b")


def find_violations(root: Path, pattern: re.Pattern, exclude: tuple[Path, ...]) -> list[str]:
    """Return 'path:line' for every line under `root` (*.py and *.sql) matching `pattern`, skipping `exclude`."""
    hits = []
    for p in sorted([*root.rglob("*.py"), *root.rglob("*.sql")]):
        if any(p == e or e in p.parents for e in exclude):
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{p.relative_to(root).as_posix()}:{n}")
    return hits


def test_no_code_outside_the_data_package_reads_prices_daily():
    hits = find_violations(ADVISOR_DIR, RAW_TABLE, exclude=(ADVISOR_DIR / "data",))
    assert hits == [], f"read prices_clean, not prices_daily: {hits}"


def test_only_db_connect_opens_sqlite_connections():
    hits = find_violations(ADVISOR_DIR, DIRECT_CONNECT, exclude=(ADVISOR_DIR / "data" / "db.py",))
    assert hits == [], f"use advisor.data.db.connect(): {hits}"


def test_scanner_catches_planted_offenders(tmp_path):
    """Guards against the scan passing vacuously (signals/ is empty today)."""
    (tmp_path / "signals").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "signals" / "bad.py").write_text('q = "SELECT * FROM prices_daily"\n', encoding="utf-8")
    (tmp_path / "signals" / "bad.sql").write_text("select 1 from prices_daily;\n", encoding="utf-8")
    (tmp_path / "signals" / "ok.py").write_text('q = "SELECT * FROM prices_clean"\n', encoding="utf-8")
    (tmp_path / "signals" / "conn.py").write_text("import sqlite3\nc = sqlite3.connect('x')\n", encoding="utf-8")
    (tmp_path / "data" / "writer.py").write_text('q = "INSERT INTO prices_daily"\n', encoding="utf-8")
    raw = find_violations(tmp_path, RAW_TABLE, exclude=(tmp_path / "data",))
    assert raw == ["signals/bad.py:1", "signals/bad.sql:1"]
    assert find_violations(tmp_path, DIRECT_CONNECT, exclude=()) == ["signals/conn.py:2"]
