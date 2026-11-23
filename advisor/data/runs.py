"""Run bookkeeping: every run gets a run_id and finishes with metadata that includes affected-symbol counts."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date, datetime, timezone

from advisor import config
from advisor.data.db import transaction


def start_run(conn: sqlite3.Connection, kind: str) -> str:
    """Create a 'running' run and return its run_id."""
    run_id = f"{kind}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
    with transaction(conn):
        conn.execute(
            "INSERT INTO runs (run_id, kind, started_at) VALUES (?, ?, ?)",
            (run_id, kind, datetime.now(timezone.utc).isoformat()),
        )
    return run_id


def affected_symbol_counts(conn: sqlite3.Connection, session_date: date | str) -> dict[str, int]:
    """Counts every run must record: symbols blocked now, and symbols with new entries suppressed on the date."""
    day = session_date.isoformat() if isinstance(session_date, date) else session_date
    blocked = conn.execute("SELECT COUNT(*) FROM blocked_symbols").fetchone()[0]
    suppressed = conn.execute(
        "SELECT COUNT(DISTINCT symbol) FROM entry_suppressed WHERE date = ?", (day,)
    ).fetchone()[0]
    return {"symbols_blocked": blocked, "symbols_entry_suppressed": suppressed}


def finish_run(conn: sqlite3.Connection, run_id: str, status: str, metadata: dict) -> None:
    """Close a run. `metadata` must carry the affected-symbol counts (config.RUN_METADATA_REQUIRED_KEYS).

    Raises ValueError if a required count is missing or not a non-negative integer, so a run can never be
    recorded without them.
    """
    for key in config.RUN_METADATA_REQUIRED_KEYS:
        value = metadata.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"run metadata must include {key} as a non-negative integer, got {value!r}")
    with transaction(conn):
        cur = conn.execute(
            "UPDATE runs SET finished_at = ?, status = ?, metadata_json = ? WHERE run_id = ?",
            (datetime.now(timezone.utc).isoformat(), status, json.dumps(metadata, sort_keys=True), run_id),
        )
        if cur.rowcount != 1:
            raise ValueError(f"unknown run_id {run_id!r}")
