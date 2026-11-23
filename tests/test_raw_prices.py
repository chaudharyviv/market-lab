from advisor.data.raw import RawQuote, latest_raw_quotes_for_valuation


def _bar(db, symbol, d, close):
    db.execute("INSERT INTO prices_daily VALUES (?,?,?,?,?,?,?,?,?)",
               (symbol, d, close, close, close, close, 100.0, "yahoo", "t"))


def _q(db, symbol, scope, a=None, b=None):
    db.execute("INSERT INTO quality_quarantine (symbol, scope, from_date, to_date, check_name, created_at) "
               "VALUES (?, ?, ?, ?, 'c', 't')", (symbol, scope, a, b))


def test_returns_latest_bar_on_or_before_as_of(db):
    for d, c in (("2026-01-05", 10.0), ("2026-01-06", 11.0), ("2026-01-07", 12.0)):
        _bar(db, "AAA", d, c)
    assert latest_raw_quotes_for_valuation(db, ["AAA"])["AAA"] == RawQuote("AAA", "2026-01-07", 12.0, False, "yahoo")
    assert latest_raw_quotes_for_valuation(db, ["AAA"], as_of="2026-01-06")["AAA"].close == 11.0


def test_quarantined_bar_is_still_valued_but_flagged_excluded(db):
    _bar(db, "AAA", "2026-01-05", 10.0)
    _bar(db, "AAA", "2026-01-06", 99.0)
    _q(db, "AAA", "bar", "2026-01-06", "2026-01-06")
    quote = latest_raw_quotes_for_valuation(db, ["AAA"])["AAA"]
    assert (quote.date, quote.close, quote.excluded) == ("2026-01-06", 99.0, True)
    assert db.execute("SELECT COUNT(*) FROM prices_clean WHERE symbol='AAA'").fetchone()[0] == 1


def test_blocked_symbol_is_still_valued_but_flagged_excluded(db):
    _bar(db, "AAA", "2026-01-05", 10.0)
    _q(db, "AAA", "symbol")
    assert db.execute("SELECT COUNT(*) FROM prices_clean").fetchone()[0] == 0
    assert latest_raw_quotes_for_valuation(db, ["AAA"])["AAA"].excluded is True


def test_entry_suppression_does_not_flag_excluded(db):
    _bar(db, "AAA", "2026-01-05", 10.0)
    _q(db, "AAA", "entry", "2026-01-05", "2026-01-05")
    assert latest_raw_quotes_for_valuation(db, ["AAA"])["AAA"].excluded is False


def test_missing_symbol_is_absent_not_zero(db):
    _bar(db, "AAA", "2026-01-05", 10.0)
    out = latest_raw_quotes_for_valuation(db, ["AAA", "BBB"])
    assert set(out) == {"AAA"}
    assert latest_raw_quotes_for_valuation(db, ["AAA"], as_of="2025-12-31") == {}
