"""Writes to prices_daily that must be all-or-nothing."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone

from advisor.data.db import transaction
from advisor.data.quality import RecheckResult, recheck_symbol

Bar = Sequence  # (date, open, high, low, close, volume); volume may be None (unknown)


@dataclass(frozen=True)
class ReplaceResult:
    """Outcome of replace_symbol_history. `still_blocked`: the symbol is in blocked_symbols after the re-check."""

    rows_inserted: int
    still_blocked: bool
    recheck: RecheckResult


def _iso(d: date | str) -> str:
    return d.isoformat() if isinstance(d, date) else str(d)


def replace_symbol_history(
    conn: sqlite3.Connection,
    symbol: str,
    bars: Sequence[Bar],
    *,
    source: str,
    fetched_at: str,
    run_id: str | None = None,
    split: tuple[date | str, float] | None = None,
) -> ReplaceResult:
    """Replace ALL stored bars for `symbol` with `bars` in one transaction; return a ReplaceResult.

    Used when a split makes the stored history stale (the vendor re-bases the whole past). `bars` rows are
    (date, open, high, low, close, volume) in INR and shares. An empty `bars` is refused before anything is
    touched, so a failed fetch can never wipe history. If anything fails (bad row, unknown symbol) the old
    history is left exactly as it was. In the same transaction: the optional `split` (ex_date, ratio) is
    recorded in corporate_actions, the symbol's open bar/span quarantines are resolved as 'history_replaced'
    (the re-check re-creates any that still apply), and an info row goes to data_quality_log. Last, and
    always (there is no flag to skip it), `quality.recheck_symbol(..., after_replacement=True)` re-runs the
    quality rules on the new history in the SAME transaction: a clean result resolves the symbol's blocks as
    'recheck_clean'; an event detected before and still unadjusted is converted to a span (the vendor cannot
    fix it). If the re-check raises, the whole replacement rolls back.
    """
    if not bars:
        raise ValueError(f"refusing to replace history for {symbol} with zero bars")
    now = datetime.now(timezone.utc).isoformat()
    rows = [(symbol, _iso(b[0]), b[1], b[2], b[3], b[4], b[5], source, fetched_at) for b in bars]
    with transaction(conn):
        deleted = conn.execute("DELETE FROM prices_daily WHERE symbol = ?", (symbol,)).rowcount
        conn.executemany(
            "INSERT INTO prices_daily (symbol, date, open, high, low, close, volume, source, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        if split is not None:
            ex_date, ratio = split
            conn.execute(
                "INSERT OR REPLACE INTO corporate_actions (symbol, ex_date, action, ratio, detail) "
                "VALUES (?, ?, 'split', ?, ?)",
                (symbol, _iso(ex_date), ratio, json.dumps({"source": source})),
            )
        conn.execute(
            "UPDATE quality_quarantine SET resolved_at = ?, resolution = 'history_replaced' "
            "WHERE symbol = ? AND scope IN ('bar','span') AND resolved_at IS NULL",
            (now, symbol),
        )
        conn.execute(
            "INSERT INTO data_quality_log (ts, run_id, symbol, check_name, severity, detail) "
            "VALUES (?, ?, ?, 'history_replaced', 'info', ?)",
            (now, run_id, symbol, json.dumps({"rows_deleted": deleted, "rows_inserted": len(rows)})),
        )
        recheck = recheck_symbol(conn, symbol, run_id, after_replacement=True)
    return ReplaceResult(rows_inserted=len(rows), still_blocked=recheck.still_blocked, recheck=recheck)
