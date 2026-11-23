-- Personal Stock Advisor (India, cash-segment stocks only) - SQLite schema, v1 proposal.
-- Principles: every signal and recommendation is logged and later scored against reality;
-- backtests record how many variants were tried; index membership is point-in-time;
-- the rules engine decides, the LLM only explains (recommendations.explanation).
PRAGMA foreign_keys = ON;

-- ============ 1. Market data ============
CREATE TABLE instruments (
  symbol       TEXT PRIMARY KEY,            -- NSE symbol
  name         TEXT,
  sector       TEXT,
  industry     TEXT,
  listed_on    DATE,
  delisted_on  DATE,                        -- keep delisted names: avoids survivorship bias
  active       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE index_membership (             -- point-in-time universe (e.g. NIFTY500 on any past date)
  index_name   TEXT NOT NULL,
  symbol       TEXT NOT NULL REFERENCES instruments(symbol),
  from_date    DATE NOT NULL,
  to_date      DATE,                        -- NULL = still a member
  PRIMARY KEY (index_name, symbol, from_date)
);

CREATE TABLE prices_daily (
  symbol       TEXT NOT NULL REFERENCES instruments(symbol),
  date         DATE NOT NULL,
  open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
  volume       REAL NOT NULL,
  source       TEXT NOT NULL,               -- 'yahoo' | 'nse' ...
  fetched_at   TEXT NOT NULL,
  PRIMARY KEY (symbol, date)
);

CREATE TABLE corporate_actions (
  symbol       TEXT NOT NULL REFERENCES instruments(symbol),
  ex_date      DATE NOT NULL,
  action       TEXT NOT NULL,               -- split | bonus | dividend | rights | demerger
  ratio        REAL,
  detail       TEXT,
  PRIMARY KEY (symbol, ex_date, action)
);

CREATE TABLE fundamentals_quarterly (
  symbol       TEXT NOT NULL REFERENCES instruments(symbol),
  quarter_end  DATE NOT NULL,
  consolidated INTEGER NOT NULL DEFAULT 1,
  income_cr REAL, pbt_cr REAL, profit_cr REAL, eps REAL,
  fetched_at   TEXT NOT NULL,
  PRIMARY KEY (symbol, quarter_end, consolidated)
);

CREATE TABLE data_quality_log (             -- every failed fetch, stale file, suspect bar
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, symbol TEXT, check_name TEXT NOT NULL,
  severity TEXT NOT NULL,                   -- info | warn | error
  detail TEXT
);

-- ============ 2. You ============
CREATE TABLE investor_profile (
  id INTEGER PRIMARY KEY CHECK (id = 1),    -- single user
  capital_inr          REAL NOT NULL,
  risk_per_trade_pct   REAL NOT NULL DEFAULT 0.5,
  max_open_risk_pct    REAL NOT NULL DEFAULT 5.0,
  max_positions        INTEGER NOT NULL DEFAULT 10,
  max_sector_pct       REAL NOT NULL DEFAULT 30,
  daily_loss_limit_pct REAL NOT NULL DEFAULT 1.5,
  weekly_loss_limit_pct REAL NOT NULL DEFAULT 4.0,
  horizon              TEXT NOT NULL DEFAULT 'swing',   -- swing | positional | long_term
  updated_at           TEXT NOT NULL
);

CREATE TABLE transactions (                 -- your real trades; positions are derived
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  symbol TEXT NOT NULL REFERENCES instruments(symbol),
  side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
  qty INTEGER NOT NULL CHECK (qty > 0),
  price REAL NOT NULL,
  fees REAL NOT NULL DEFAULT 0,
  recommendation_id INTEGER REFERENCES recommendations(id),
  stop REAL, target REAL,                   -- plan at entry
  note TEXT
);

CREATE VIEW positions AS
  SELECT symbol,
         SUM(CASE side WHEN 'BUY' THEN qty ELSE -qty END) AS qty,
         SUM(CASE side WHEN 'BUY' THEN qty*price ELSE 0 END) * 1.0
           / NULLIF(SUM(CASE side WHEN 'BUY' THEN qty ELSE 0 END),0) AS avg_buy
  FROM transactions GROUP BY symbol
  HAVING SUM(CASE side WHEN 'BUY' THEN qty ELSE -qty END) > 0;

-- ============ 3. Strategies and honest validation ============
CREATE TABLE strategies (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL, version TEXT NOT NULL,
  params_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'candidate', -- candidate | paper | live | retired
  UNIQUE (name, version)
);

CREATE TABLE backtest_runs (
  id INTEGER PRIMARY KEY,
  strategy_id INTEGER NOT NULL REFERENCES strategies(id),
  run_at TEXT NOT NULL,
  universe TEXT NOT NULL, start_date DATE NOT NULL, end_date DATE NOT NULL,
  train_end DATE,                           -- in-sample / walk-forward boundary
  holdout_start DATE,                       -- untouched final period
  cost_model_ver TEXT NOT NULL,
  variants_tested INTEGER NOT NULL,         -- how many strategies/exits were tried (snooping penalty)
  point_in_time_universe INTEGER NOT NULL,  -- 0 = survivorship-biased, 1 = clean
  metrics_json TEXT NOT NULL                -- CAGR, maxDD, Sharpe, by-year, IS vs OOS vs holdout
);

-- ============ 4. Signals, advice, paper trading ============
CREATE TABLE signals (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,                         -- close the signal was generated on
  symbol TEXT NOT NULL REFERENCES instruments(symbol),
  strategy_id INTEGER NOT NULL REFERENCES strategies(id),
  trigger_px REAL, stop REAL, target REAL, risk_pct REAL,
  regime_ok INTEGER,                        -- Nifty above 200DMA etc.
  rs63 REAL,
  status TEXT NOT NULL DEFAULT 'open'       -- open | triggered | expired | stopped | target
);

CREATE TABLE recommendations (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  symbol TEXT NOT NULL REFERENCES instruments(symbol),
  signal_id INTEGER REFERENCES signals(id),
  action TEXT NOT NULL CHECK (action IN ('BUY','HOLD','TRIM','EXIT','AVOID')),
  qty INTEGER, entry REAL, stop REAL, target REAL,
  rupee_risk REAL,                          -- qty * (entry-stop)
  guardrails_json TEXT NOT NULL,            -- which limits were checked and their result
  reasoning_json TEXT NOT NULL,             -- rules-engine facts the explanation must be grounded in
  explanation TEXT,                         -- LLM text, generated from reasoning_json only
  profile_snapshot_json TEXT NOT NULL
);

CREATE TABLE paper_trades (                 -- every recommendation scored as if taken
  id INTEGER PRIMARY KEY,
  recommendation_id INTEGER NOT NULL REFERENCES recommendations(id),
  entry_ts TEXT, entry_px REAL, exit_ts TEXT, exit_px REAL,
  exit_reason TEXT,                         -- stop | target | time | manual
  net_pnl REAL, r_multiple REAL
);

CREATE TABLE journal (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL,
  transaction_id INTEGER REFERENCES transactions(id),
  symbol TEXT, note TEXT NOT NULL, tags TEXT
);

CREATE TABLE risk_events (                  -- guardrail breaches and halts
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, rule TEXT NOT NULL, detail TEXT
);

CREATE TABLE alerts (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, channel TEXT NOT NULL,   -- discord | telegram | email
  kind TEXT NOT NULL, payload TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX ix_prices_date ON prices_daily(date);
CREATE INDEX ix_signals_ts ON signals(ts, status);
CREATE INDEX ix_tx_symbol ON transactions(symbol, ts);
