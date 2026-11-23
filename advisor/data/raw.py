"""Raw price reads for VALUATION of held positions only.

Signals and backtests must read `prices_clean`. A position you hold still has to be valued when its bar or
its whole symbol is quarantined or blocked, so valuation reads the raw bars here (the only sanctioned way
outside this package) and is told whether the bar is excluded from `prices_clean`. Never use this for signals,
features or backtests, and never silently present an excluded price as trustworthy.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class RawQuote:
    """Latest stored raw bar for a symbol. close in INR per share. `excluded` = the bar is not in prices_clean."""

    symbol: str
    date: str
    close: float
    excluded: bool  # True: bar quarantined or symbol blocked; show valuation with a warning
    source: str


def latest_raw_quotes_for_valuation(
    conn: sqlite3.Connection,
    symbols: Sequence[str],
    *,
    as_of: date | str | None = None,
) -> dict[str, RawQuote]:
    """Latest raw bar on or before `as_of` (default: latest stored) for each of `symbols`, for valuation only.

    Symbols with no stored bar are absent from the result; the caller must flag them, not assume zero.
    The bar's age is the caller's concern (compare `date` with the latest expected session).
    """
    day = as_of.isoformat() if isinstance(as_of, date) else as_of
    out: dict[str, RawQuote] = {}
    for symbol in symbols:
        row = conn.execute(
            "SELECT p.symbol, p.date, p.close, p.source, NOT EXISTS ("
            "  SELECT 1 FROM prices_clean c WHERE c.symbol = p.symbol AND c.date = p.date) AS excluded "
            "FROM prices_daily p WHERE p.symbol = ? AND (? IS NULL OR p.date <= ?) "
            "ORDER BY p.date DESC LIMIT 1",
            (symbol, day, day),
        ).fetchone()
        if row is not None:
            out[symbol] = RawQuote(row["symbol"], row["date"], row["close"], bool(row["excluded"]), row["source"])
    return out
