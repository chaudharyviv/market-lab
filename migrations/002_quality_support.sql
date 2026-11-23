-- 002: support for the data-quality rules. No PRAGMAs (see 001).

CREATE TABLE trading_calendar (
  date         DATE PRIMARY KEY,
  kind         TEXT NOT NULL CHECK (kind IN ('trading','holiday','special')),
  description  TEXT,
  source       TEXT NOT NULL,
  fetched_at   TEXT NOT NULL
);

-- One table for every exclusion. Scope says how far it reaches:
--   bar     one bar hidden from prices_clean
--   span    a date range hidden from prices_clean
--   symbol  every bar of the symbol hidden from prices_clean; ONLY for suspected data corruption (blocked_symbols view)
--   entry   no NEW entry for that symbol on that one day; the bar stays visible (entry_suppressed view)
-- Rows are never deleted: a manual verdict sets resolved_at and resolution.
CREATE TABLE quality_quarantine (
  id          INTEGER PRIMARY KEY,
  run_id      TEXT REFERENCES runs(run_id),
  symbol      TEXT NOT NULL REFERENCES instruments(symbol),
  scope       TEXT NOT NULL CHECK (scope IN ('bar','span','symbol','entry')),
  from_date   DATE,
  to_date     DATE,
  check_name  TEXT NOT NULL,
  reason      TEXT,
  created_at  TEXT NOT NULL,
  resolved_at TEXT,
  resolution  TEXT,                                  -- e.g. genuine | bad_print | history_replaced
  CHECK (scope = 'symbol' OR (from_date IS NOT NULL AND to_date IS NOT NULL)),
  CHECK (scope <> 'entry' OR from_date = to_date)
);
CREATE INDEX ix_quarantine_active ON quality_quarantine(symbol, scope) WHERE resolved_at IS NULL;

-- Backtests and signals read this, never prices_daily: the single chokepoint. Hides open bar and span
-- quarantines (by date) and every bar of a blocked symbol. Does NOT hide 'entry' scope: a genuine-looking big
-- move keeps its bar. (A held symbol that gets blocked must be surfaced by the advisor, not silently dropped.)
CREATE VIEW prices_clean AS
  SELECT p.* FROM prices_daily p
  WHERE NOT EXISTS (
    SELECT 1 FROM quality_quarantine q
    WHERE q.symbol = p.symbol AND q.resolved_at IS NULL
      AND (q.scope = 'symbol' OR (q.scope IN ('bar','span') AND p.date BETWEEN q.from_date AND q.to_date)));

-- Symbols excluded until cleared by hand (suspected corruption only).
CREATE VIEW blocked_symbols AS
  SELECT DISTINCT symbol FROM quality_quarantine WHERE scope = 'symbol' AND resolved_at IS NULL;

-- (symbol, date) pairs on which no new entry may be generated; the bar itself stays in prices_clean.
CREATE VIEW entry_suppressed AS
  SELECT DISTINCT symbol, from_date AS date FROM quality_quarantine WHERE scope = 'entry' AND resolved_at IS NULL;
