"""Numbered SQL migrations. Each file in `migrations/` runs once, in order, atomically with its ledger row."""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from advisor import config

MIGRATIONS_DIR = config.PROJECT_ROOT / "migrations"
_NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")

_LEDGER_DDL = "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"


def apply_migrations(conn: sqlite3.Connection, directory: Path | None = None) -> list[str]:
    """Apply every migration not yet in `schema_migrations`; return the versions applied this call.

    A file runs inside BEGIN..COMMIT together with its ledger row, so a failing file leaves nothing behind
    and raises. Files must be named NNN_name.sql; a gap in numbering or a bad name raises.
    """
    folder = directory if directory is not None else MIGRATIONS_DIR
    files = sorted(p for p in folder.glob("*.sql"))
    numbers = []
    for p in files:
        m = _NAME.match(p.name)
        if not m:
            raise ValueError(f"bad migration file name: {p.name} (expected NNN_name.sql)")
        numbers.append(int(m.group(1)))
    if numbers != list(range(1, len(numbers) + 1)):
        raise ValueError(f"migration numbers must be 1..N with no gaps or repeats, got {numbers}")

    conn.execute(_LEDGER_DDL)
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    applied: list[str] = []
    for p in files:
        version = p.stem
        if version in done:
            continue
        stamp = datetime.now(timezone.utc).isoformat()
        script = (
            "BEGIN;\n"
            + p.read_text(encoding="utf-8")
            + f"\nINSERT INTO schema_migrations (version, applied_at) VALUES ('{version}', '{stamp}');\nCOMMIT;\n"
        )
        try:
            conn.executescript(script)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        applied.append(version)
    return applied
