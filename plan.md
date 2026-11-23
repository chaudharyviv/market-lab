# PLAN: Personal Swing-Trading Advisor

Work in small phases. Do not start a phase until the previous phase's exit criteria pass.
Each task = one commit with tests. Reuse from market-lab: cost model, regime filter, portfolio simulator.

## Phase 0: Repo prep
- [x] Fork `heysanjeevv/market-lab`; move original (fork = this repo; moved as pure renames) `lab/`, `sm/`, `sx/` into `legacy/` (do not edit).
- [x] Create package skeleton: `advisor/{config,data,signals,validation,portfolio,advisor,journal,alerts}`, `tests/`.
- [~] `config.py`, logging, `.env.example`, pytest, ruff done; pre-commit NOT installed (awaiting approval to add the dependency).
- [x] Run legacy app once (baseline in `baselines/legacy_swing/`) on a personal machine to confirm behaviour; save a baseline of its swing backtest output for comparison. Note: the baseline's last bar (2026-11-23) was fetched during market hours and is possibly partial.
Exit: `pytest` passes on an empty skeleton; baseline saved.

## Phase 1: Data layer
- [ ] Apply `advisor_schema.sql` (data tables first) via a migrations folder.
- [ ] Fetchers: Yahoo daily bars, NSE index lists, holidays. Timeout + retry + backoff; failures go to `data_quality_log`.
- [ ] Fetch the maximum available Yahoo history per symbol, not just 5 years. Attach the survivorship caveat to the older periods: the further back, the more of today's constituents are survivors, so reports label each period with its caveat (spec section 9.8).
- [ ] Quality checks: gaps vs trading calendar, zero/negative prices, >35% one-day moves, stale last bar, volume=0 runs.
- [ ] Exclude the current session's bar until after the close (`EOD_READY_IST`): the fetcher drops it and logs `bar_partial_today`; the updater re-fetches the overlap so a provisional last bar is replaced by the final one. Treat the baseline's 2026-11-23 bar as possibly partial.
- [ ] Universe: load current Nifty 500 + F&O; investigate historical membership; store `index_membership`; record the survivorship / membership caveat (spec section 9.8) wherever the universe is used.
- [ ] Incremental updater (fetch only new bars) and a freshness report.
Exit: the maximum available history (at least 5 years) of bars for the universe in SQLite; freshness report green; no bar for an unfinished session stored; data tests pass; known bad-print cases caught.

## Phase 2: Signals and backtest port
- [ ] Port `features`, strategy functions and `follow()` exit logic from `legacy/lab/swing.py` reading from SQLite.
- [ ] Port cost model with unit tests against hand-computed examples. Replace the flat 0.05% per side slippage with a liquidity-dependent slippage model (spec section 9.2).
- [ ] Add the minimum daily-turnover liquidity filter (close x volume, trailing window) to the tradable universe. Filter threshold, window and slippage schedule are constants in `config.py`, confirmed with the user first.
- [ ] Fix exit mechanics in the `follow()` port: exits triggered on a close fill at the next open; intraday stops and targets keep gap-aware fills. Unit tests for both.
- [ ] Portfolio simulator sized by risk (capital x risk % / stop distance), not equal weight; reports portfolio-level max drawdown, Sharpe and worst month.
- [ ] Add no-look-ahead tests (signal at close t, entry at open t+1; features use data up to t only).
- [ ] Reproduce the legacy backtest numbers within tolerance (regression test against the saved baseline) in a legacy-compatible mode (flat slippage, legacy exit fills, equal weight). Then report how much each deliberate change above moves the numbers.
Exit: ported engine matches baseline; look-ahead tests pass.

## Phase 3: Honest validation
- [ ] Walk-forward runner and untouched holdout (dates are fixed in `config.py`; `assert_no_holdout` guards training code). Strategies and parameters are chosen inside walk-forward windows only, never on the full period.
- [ ] Declare the strategy list and exit choices before testing (a file committed before any run); runs refuse to start without it.
- [ ] Record `variants_tested` per run; apply a multiple-testing penalty/report.
- [ ] Mark each run survivorship-biased or point-in-time, and store the universe / index-membership caveat text in every run's record and report.
- [ ] Random-data check: run the engine on randomised zero-edge data: permute blocks of 20 days with one permutation applied to all symbols, shuffle whole bars (gap, range, volume), set drift to zero, preserve costs, use many seeds and no holdout dates. Report the distribution of the same portfolio metrics, so the real result is read against what luck alone produces.
- [ ] Report every result at portfolio level (max drawdown, Sharpe, worst month) and state that results are pre-tax (spec section 9.1).
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
