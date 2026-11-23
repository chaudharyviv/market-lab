"""The single SQLite connection factory. Every connection in the project comes from `connect()`.

Sets PRAGMA foreign_keys=ON and journal_mode=WAL on each connection, and fails loudly if either does not
stick (for example WAL on a filesystem that cannot do it). Connections run with isolation_level=None, so
transactions are explicit: use `transaction()`.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from advisor import config


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open the advisor database (default `config.DB_PATH`) with foreign keys on and WAL journaling.

    Creates the parent directory if needed. Rows come back as `sqlite3.Row`. Raises RuntimeError if a
    required PRAGMA is not in effect. In-memory databases are not supported (they cannot use WAL).
    """
    db_path = Path(path) if path is not None else config.DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=config.DB_BUSY_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if fk != 1:
            raise RuntimeError("could not enable PRAGMA foreign_keys")
        if str(mode).lower() != "wal":
            raise RuntimeError(f"could not enable WAL journaling (journal_mode is {mode!r}) for {db_path}")
    except Exception:
        conn.close()
        raise
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block in one write transaction: commit on success, roll back on any exception."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
