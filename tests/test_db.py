import sqlite3

import pytest

from advisor.data.db import connect, transaction


def test_every_connection_has_foreign_keys_on_and_wal(tmp_path):
    path = tmp_path / "x.db"
    for _ in range(2):  # the PRAGMAs must hold on EACH connection, not just the first
        conn = connect(path)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        conn.close()


def test_foreign_key_violation_raises(tmp_path):
    conn = connect(tmp_path / "x.db")
    conn.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE child (pid INTEGER REFERENCES parent(id))")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO child VALUES (99)")
    conn.close()


def test_creates_missing_parent_directory(tmp_path):
    conn = connect(tmp_path / "a" / "b" / "x.db")
    assert (tmp_path / "a" / "b" / "x.db").exists()
    conn.close()


def test_transaction_commits_and_rolls_back(tmp_path):
    conn = connect(tmp_path / "x.db")
    conn.execute("CREATE TABLE t (v INTEGER)")
    with transaction(conn):
        conn.execute("INSERT INTO t VALUES (1)")
    with pytest.raises(RuntimeError), transaction(conn):
        conn.execute("INSERT INTO t VALUES (2)")
        raise RuntimeError("boom")
    assert [r[0] for r in conn.execute("SELECT v FROM t")] == [1]
    assert not conn.in_transaction
    conn.close()
