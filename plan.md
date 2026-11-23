# PLAN: Personal Swing-Trading Advisor

Work in small phases. Do not start a phase until the previous phase's exit criteria pass.
Each task = one commit with tests. Reuse from market-lab: cost model, regime filter, portfolio simulator.

## Phase 0: Repo prep
- [x] Fork `heysanjeevv/market-lab`; move original (fork = this repo; moved as pure renames) `lab/`, `sm/`, `sx/` into `legacy/` (do not edit).
- [x] Create package skeleton: `advisor/{config,data,signals,validation,portfolio,advisor,journal,alerts}`, `tests/`.
- [~] `config.py`, logging, `.env.example`, pytest, ruff done; pre-commit NOT installed (awaiting approval to add the dependency).
- [x] Run legacy app once (baseline in `baselines/legacy_swing/`) on a personal machine to confirm behaviour; save a baseline of its swing backtest output for comparison.
Exit: `pytest` passes on an empty skeleton; baseline saved.

## Phase 1: Data layer
- [ ] Apply `advisor_schema.sql` (data tables first) via a migrations folder.
- [ ] Fetchers: Yahoo daily bars, NSE index lists, holidays. Timeout + retry + backoff; failures go to `data_quality_log`.
- [ ] Quality checks: gaps vs trading calendar, zero/negative prices, >35% one-day moves, stale last bar, volume=0 runs.
- [ ] Universe: load current Nifty 500 + F&O; investigate historical membership; store `index_membership`.
- [ ] Incremental updater (fetch only new bars) and a freshness report.
Exit: 5 years of bars for the universe in SQLite; freshness report green; data tests pass; known bad-print cases caught.

## Phase 2: Signals and backtest port
- [ ] Port `features`, strategy functions and `follow()` exit logic from `legacy/lab/swing.py` reading from SQLite.
- [ ] Port cost model with unit tests against hand-computed examples.
- [ ] Add no-look-ahead tests (signal at close t, entry at open t+1; features use data up to t only).
- [ ] Reproduce the legacy backtest numbers within tolerance (regression test against the saved baseline).
Exit: ported engine matches baseline; look-ahead tests pass.

## Phase 3: Honest validation
- [ ] Walk-forward runner and untouched holdout (define dates once, store in config).
- [ ] Record `variants_tested` per run; apply a multiple-testing penalty/report.
- [ ] Mark each run survivorship-biased or point-in-time.
- [ ] Evaluate the strategy shortlist against the promotion gates in spec section 6.
Exit: a written validation report. If nothing passes, stop and decide: new ideas or abandon. Do not build advice on a failed strategy.

## Phase 4: Portfolio and risk engine
- [ ] Profile and transactions CRUD (CLI first); positions view; P&L.
- [ ] Position sizing from capital, risk %, stop distance; round to whole shares and check cash.
- [ ] Guardrails: max positions, sector cap, open risk, daily/weekly loss halt; every check logged to `risk_events`.
- [ ] Tests with edge cases (tiny stop distance, gap risk, sector overflow).
Exit: sizing and guardrail test suite green.

## Phase 5: Advisor
- [ ] Rules engine: for each holding (HOLD/TRIM/EXIT) and each candidate (BUY/AVOID) emit structured JSON facts.
- [ ] Recommendation writer stores facts + guardrail results + profile snapshot.
- [ ] LLM explanation from JSON only; tests that the text introduces no numbers absent from the JSON.
- [ ] Daily report (markdown/HTML) and CLI `advisor run`.
Exit: end-to-end daily run on historical dates produces sensible, reproducible reports.

## Phase 6: Paper trading and alerts
- [ ] Log signals and recommendations; auto paper-trade them; score outcomes (R-multiple, hit rate, drawdown).
- [ ] Live-vs-backtest comparison report.
- [ ] Discord/Telegram alerts: triggers, stops/targets, data failures, guardrail breaches.
Exit: 30+ days live paper results; drift report reviewed.

## Phase 7: Review and decide
- [ ] Review paper results against promotion gate 4. Only then consider small real capital, manually executed.

## Later (separate specs)
Long-term investing mode, richer fundamentals (ROCE, cash flow, promoter pledging, FII/DII), event-risk flags.
