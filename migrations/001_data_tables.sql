-- 001: Phase 1 data tables. Source of truth for the schema from here on (advisor_schema.sql is the original
-- proposal and is no longer edited). Deviations from advisor_schema.sql section 1, all approved or flagged:
--   * runs table (run_id target; holds each run's metadata)            [added, flag for review]
--   * instruments.kind ('stock' | 'index') so the benchmark can be an instruments row
--   * prices_daily.volume is nullable (NULL = unknown, never 0)
--   * data_quality_log: bar_date, run_id, severity CHECK, indexes
--   * index_membership range index
--   * corporate_actions.action CHECK (the fetcher stores splits only)
-- No PRAGMAs here: db.connect() sets foreign_keys and WAL on every connection. The migration runner owns
-- schema_migrations and the surrounding transaction.

CREATE TABLE runs (
  run_id        TEXT PRIMARY KEY,
  kind          TEXT NOT NULL,                       -- e.g. data_update | daily | dry_run
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  status        TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','ok','failed','blocked')),
  metadata_json TEXT NOT NULL DEFAULT '{}'           -- must carry symbols_blocked and symbols_entry_suppressed
);

CREATE TABLE instruments (
  symbol       TEXT PRIMARY KEY,                     -- NSE symbol, or an index ticker such as ^NSEI
  name         TEXT,
  sector       TEXT,
  industry     TEXT,
  kind         TEXT NOT NULL DEFAULT 'stock' CHECK (kind IN ('stock','index')),
  listed_on    DATE,
  delisted_on  DATE,                                 -- keep delisted names: avoids survivorship bias
  active       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE index_membership (                      -- point-in-time universe
  index_name   TEXT NOT NULL,
  symbol       TEXT NOT NULL REFERENCES instruments(symbol),
  from_date    DATE NOT NULL,
  to_date      DATE,                                 -- NULL = still a member
  PRIMARY KEY (index_name, symbol, from_date)
);
CREATE INDEX ix_membership_range ON index_membership(index_name, from_date, to_date);

CREATE TABLE prices_daily (                          -- RAW vendor bars. Signals and backtests read prices_clean.
  symbol       TEXT NOT NULL REFERENCES instruments(symbol),
  date         DATE NOT NULL,
  open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
  volume       REAL,                                 -- NULL = unknown (not 0)
  source       TEXT NOT NULL,                        -- 'yahoo' only; NSE bhavcopy is raw, a cross-check never stored
  fetched_at   TEXT NOT NULL,
  PRIMARY KEY (symbol, date)
);
CREATE INDEX ix_prices_date ON prices_daily(date);

CREATE TABLE corporate_actions (
  symbol       TEXT NOT NULL REFERENCES instruments(symbol),
  ex_date      DATE NOT NULL,
  action       TEXT NOT NULL CHECK (action IN ('split','bonus','rights','demerger')),  -- no dividends stored
  ratio        REAL,
  detail       TEXT,
  PRIMARY KEY (symbol, ex_date, action)
);

CREATE TABLE data_quality_log (                      -- every failed fetch, stale file, suspect bar
  id        INTEGER PRIMARY KEY,
  ts        TEXT NOT NULL,
  run_id    TEXT REFERENCES runs(run_id),            -- NULL when logged outside a run
  symbol    TEXT,
  bar_date  DATE,
  check_name TEXT NOT NULL,
  severity  TEXT NOT NULL CHECK (severity IN ('info','warn','error')),   -- error = blocks the run
  detail    TEXT
);
CREATE INDEX ix_dql_symbol_check ON data_quality_log(symbol, check_name, ts);
CREATE INDEX ix_dql_run ON data_quality_log(run_id);
