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
- [ ] Fetch Yahoo daily history from `HISTORY_START` (2005-01-01) to now with explicit `period1`/`period2` timestamps and `interval=1d`, never `range=max` (it silently returns monthly bars). Pass `events=split`. Fail loudly if the median gap between returned bars exceeds 4 days. Attach the survivorship caveat to the older periods: the further back, the more of today's constituents are survivors, so reports label each period with its caveat (spec section 9.8). Why 2005: live probe found unadjusted splits in Yahoo history for 1997-1999 and many zero-volume bars before 2005.
- [ ] Quality checks: gaps vs trading calendar, zero/negative prices, >35% one-day moves, stale last bar, volume=0 runs.
- [ ] Exclude the current session's bar until after the close (`EOD_READY_IST`): the fetcher drops it and logs `bar_partial_today`; the updater re-fetches the overlap so a provisional last bar is replaced by the final one. Treat the baseline's 2026-11-23 bar as possibly partial.
- [ ] Universe: load current Nifty 500 + F&O; investigate historical membership; store `index_membership`; record the survivorship / membership caveat (spec section 9.8) wherever the universe is used.
- [ ] Incremental updater (fetch only new bars) and a freshness report.
- [ ] `advisor update` CLI: one command that runs fetch, quality checks and the freshness report, in that order, under one `run_id` (the run's metadata carries the affected-symbol counts). Resumable and safe to re-run: per-symbol transactions; an interrupted run continues where it stopped without duplicating or losing bars. A re-run on a finished day adds nothing new but is NOT a skip: it still re-fetches the last `OVERLAP_BARS` (10) bars of every active symbol, compares them with what is stored, and logs a `bar_revised` row (old and new values) only for real changes beyond `REVISION_TOL_PCT`; identical bars write nothing and log nothing, so a second identical run leaves the database and the log unchanged. A uniform ratio shift across the overlap triggers `history_readjusted` and the full-history replacement with its required re-check. Symbols behind the latest expected session additionally fetch what is missing. The cost is one request per active symbol on every run, at `FETCH_DELAY_S` apart. Exit code is 0 for a clean or report-only run and non-zero when the run is blocked, so a scheduler can see failure.
- [ ] Daily schedule (Windows Task Scheduler; the machine's clock must be set to IST, and the CLI also checks it and fails loudly if not). Task `advisor-update`: runs `advisor update` at 19:15 IST Mon-Fri (15 minutes after `EOD_READY_IST`). Settings: restart on failure every 15 minutes, up to 3 times; run only while the user is logged on (logon type interactive, no password stored); not restricted to AC power (do not start-only-on-AC, do not stop on battery); run as soon as possible after a missed start. A restart is safe because the command is re-runnable. The CLI decides whether the day is a trading day from `trading_calendar`: on a holiday it logs one `info` row and exits 0 quietly (so no restart); if the calendar is missing or too old (`calendar_unavailable`) it does NOT skip quietly, it fails loudly. The task definitions are saved in the repo (exported XML or a setup script) so they can be recreated. Known limit: while the user is logged off nothing runs, and the two checks below are what notice.
- [ ] Missed-run checks: `advisor check-run`, a separate command and separate scheduled tasks (same settings: interactive logon, no stored password, no AC-power restriction), because a task that never starts cannot report itself. (1) Task `advisor-check-evening` at `RUN_DEADLINE_IST` (21:30 IST) Mon-Fri: on a trading day, errors if `runs` has no `ok` `data_update` run for today's session. (2) Task `advisor-check-morning` at 08:30 IST Mon-Fri: on a trading day, errors if the previous session's data is not updated, where previous session is the latest `trading` or `special` date before today in `trading_calendar` (so Monday checks Friday) and 'updated' means an `ok` `data_update` run that finished after that session's `EOD_READY_IST` and a fresh bar for the benchmark and for all but at most `STALE_SYMBOL_PCT_REPORT` percent of active symbols. Both log an `error` row to `data_quality_log`, exit non-zero, and stay quiet on non-trading days and when all is well. In Phase 1 the alert is that error row plus the exit code; delivery to Discord/Telegram is wired in Phase 6.
Exit: daily bars from `HISTORY_START` (2005-01-01) for the universe in SQLite; every `move_unclassified` event is counted in the run metadata (`move_unclassified_events`) and those within the last 252 sessions are reviewed (`move_unclassified_unreviewed_recent` is 0); freshness report green; no bar for an unfinished session stored; data tests pass; known bad-print cases caught; `advisor update` can be killed mid-run and re-run to the same result; the scheduled task and the missed-run check have each been exercised once.

## Phase 2: Signals and backtest port
- [ ] Port `features`, strategy functions and `follow()` exit logic from `legacy/lab/swing.py` reading from SQLite.
- [ ] Era-aware cost model: STT, stamp duty, exchange and SEBI charges changed over the years since 2005, so one flat modern cost table is wrong for old periods. Either make the costs date-dependent (a dated schedule with sources, tested against hand-computed examples for several eras) or flag every result that spans a cost-regime change before the schedule is complete. Decide before any pre-2020 result is quoted.
- [ ] Minimum-history rule: a symbol is not used for signals or backtests until it has about 260 stored (clean) bars since its series start, where the series start is the first bar after any hidden span or symbol block (a recent listing, a gap, or an unfixable split event restarts the count). The constant lives in `config.py`; a test checks that a symbol with 259 bars yields no features or signals and one with 261 does.
- [ ] Features are computed on the contiguous series only: no rolling window, lookback or return may span a hidden span, a symbol-block boundary or a calendar gap. Tests build a series with a hidden middle span and check that no feature value uses bars from both sides.
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
- [ ] Value held positions from raw prices via `advisor.data.raw.latest_raw_quotes_for_valuation` (never for signals); a held symbol whose bar is excluded, whose symbol is blocked, or whose latest bar is stale is withheld from recommendations and shown in a red flag at the top of the report, not dropped silently. The run itself is not blocked.
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
