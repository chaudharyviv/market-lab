import pytest

from advisor.data.db import connect
from advisor.data.migrate import apply_migrations


@pytest.fixture
def db(tmp_path):
    """A fresh, fully migrated database in a temp file (never the real DB_PATH)."""
    conn = connect(tmp_path / "t.db")
    apply_migrations(conn)
    conn.execute("INSERT INTO instruments (symbol, name) VALUES ('AAA', 'Alpha'), ('BBB', 'Beta')")
    conn.execute("INSERT INTO instruments (symbol, kind) VALUES ('^NSEI', 'index')")
    yield conn
    conn.close()
