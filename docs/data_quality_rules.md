# Data-quality rules (Phase 1)

Approved 2026-11-23, with the changes recorded in `docs/decisions.md`. This is what the quality checks are built
from. **Every number lives in `advisor/config.py` and is pinned by `tests/test_config_quality.py`**; the values
below were generated from config, and a change there needs a deliberate edit of that test, a line in
`docs/decisions.md`, and (for thresholds) tightening only. Rule semantics are owned by the `market-logic` agent.

## 1. Status: nothing is enforced yet

`QUALITY_ENFORCED_RULES` is an empty set. Until the dry-run report (counts per rule on real data, split
pre-holdout versus holdout, counts only) is produced and approved, every rule only logs and reports; none
blocks or excludes. Thresholds are tuned on the pre-holdout counts. Cleaning reads of holdout-period bars are
allowed, using only these mechanical rules, frozen before any holdout evaluation.

Implemented in `advisor/data/quality.py` (rule names as used in `QUALITY_ENFORCED_RULES`):
`corp_action_unadjusted`, `move_unclassified`, `nonpositive_recurring`, `bad_bar_cluster`. With the shipped
config a finding is logged with outcome `not_enforced` and nothing is blocked, suppressed or hidden; the tests
switch the rules on with a fixture. The other checks in this document are specified but not written yet.

## 2. Severity scheme

| Severity | Effect on the daily run | Used for |
|---|---|---|
| `error` | **Blocks the run**: no signals or recommendations, report RED, alert | Whole-dataset problems only |
| `warn` | Logged and reported; the run continues. The bar, span or symbol may be excluded when read | One bar or one symbol, including suspected corruption |
| `info` | Logged only | Expected events |

No rule deletes or edits a row in `prices_daily`. Exclusion is a read-time filter through `prices_clean`.

### Checks that block the run (`error`)
| Check | Trigger |
|---|---|
| `calendar_unavailable` | No current-year holiday list, or the stored one is older than `CALENDAR_MAX_AGE_DAYS` |
| `calendar_suspect_holiday` | More than `MARKET_WIDE_PCT` of symbols have a bar on a listed holiday |
| `calendar_market_wide_gap` (last `MARKET_WIDE_RECENT_SESSIONS` sessions) | Fewer than `MARKET_WIDE_PCT` of symbols have a bar on an expected session |
| `stale_benchmark` | `FRESHNESS_BENCHMARK` is a session or more behind |
| `stale_universe` | More than `STALE_SYMBOL_PCT_MAX` of active symbols are stale (report-only at `STALE_SYMBOL_PCT_REPORT` until enforced) |

### Held or signalling symbols that are stale
Once `stale_universe` is enforced, a held or signalling symbol without a fresh bar is **withheld**: no
recommendation for that symbol, and a red flag at the top of the report. It does not block the run.

### Checks that warn (`warn`)
| Group | Check: what happens |
|---|---|
| Rejected before insert (logged, re-fetched next run) | `bar_nan_price` on an ordinary trading day (a null-price row on a special session is `info`, below), `bar_future_date`, `bar_date_mapping`, differing `bar_duplicate_date` |
| Bar hidden from `prices_clean` (scope `bar`) | `price_nonpositive`, `ohlc_inconsistent`, `bar_nan_volume`, `volume_zero_price_moved`, `bar_on_non_session`, `move_bad_print` |
| Run of bars hidden (scope `span`) | `volume_zero_run` (at `VOL0_RUN_ERROR`), `flat_price_run` (at `FLAT_RUN_ERROR`) |
| Span hidden plus lookback buffer | `gap_run` (at `GAP_RUN_ERROR`) |
| Symbol blocked (scope `symbol`) | Corruption only, see section 4 |
| Entry suppressed that day, bar kept (scope `entry`) | `move_extreme`, `move_unclassified` (implemented, including the split-like price pattern with no event row) |
| No signal for that symbol today | `stale_last_bar` |
| Flagged and kept | `move_large`, `bar_revised` |
| Report only | `volume_zero_ratio`, `gap_ratio`, `gap_missing_session`, short gap / zero-volume / flat runs, `bar_unsorted`, `fetch_symbol_not_found` (error after `NOT_FOUND_RUNS_ESCALATE` runs), `fetch_symbol_lost`, `calendar_year_missing` |

### Checks that only inform (`info`)
`universe_placeholder_excluded`, `bar_partial_today`, identical `bar_duplicate_date`, `ohlc_flat_traded`,
`history_readjusted`, `move_corp_action_explained`, `history_replaced`, `split_recheck`,
`corp_action_ratio_missing`, `special_session_no_bar`.

A null-price row on a date that `trading_calendar` marks as a special session (Muhurat, special Saturday, Budget
day) logs `special_session_no_bar` (`info`) and is excluded from gap counts. A null-price row on an ordinary
trading day stays `warn` (`bar_nan_price`).

## 3. Move classification (one-day move above `MOVE_EXTREME_PCT`, or a split-like move above `MOVE_LARGE_PCT`)

First match wins.
1. **Corporate action explains it.** A demerger, rights or scheme row within `CORP_ACTION_WINDOW_SESSIONS` of the
   move: `move_corp_action_explained` (info, bar kept).
2. **Unadjusted split or bonus: `corp_action_unadjusted` is the ONLY evidence.** A split or bonus row in
   `corporate_actions` whose ratio the stored prices still jump by: the raw close before a bar divided by that
   bar's close matches the recorded ratio within `SPLIT_RATIO_TOL_PCT` (or its inverse for a reverse split). The
   vendor's ex-date can be off by a session, so the jump is searched at every bar within
   `EVENT_JUMP_WINDOW_SESSIONS` (2) stored sessions of the first bar on or after the recorded ex-date; the closest
   match wins (earlier on a tie) and is the **real jump bar**. Three sessions off is not matched. **Volume is not evidence** (live Yahoo data shows volume is split-adjusted and did not shift at
   the known unadjusted splits). The ratio is informational: it is compared with prices and never used to adjust
   them. It also catches a 1:4 bonus (ratio 1.25), which moves the price less than `MOVE_LARGE_PCT`.
3. **Bad print.** The move reverses the next day (over `BAD_PRINT_REVERT_PCT`, back within
   `BAD_PRINT_RETURN_BAND_PCT` of the old close), or the wick is beyond `MOVE_EXTREME_PCT` while the close is
   within `BAD_PRINT_WICK_CLOSE_MAX_PCT`, or volume is below `BAD_PRINT_LOW_VOLUME_FRAC` of the
   `VOLUME_MEDIAN_WINDOW` median: `move_bad_print` (bar hidden). Not yet implemented.
4. **Otherwise `move_unclassified`.** This includes a price pattern that looks like a split (close-to-close move
   above `MOVE_LARGE_PCT` whose ratio matches `SPLIT_RATIOS`) but has no event row. It does NOT block: a
   one-day `entry` quarantine suppresses new entries, the bar stays visible, `move_unclassified` is logged
   (`warn`). It is decided from bars t-1 and t only, so it is known at the close of day t.

A bar flagged `move_extreme` on day t never produces an entry signal for that symbol on day t, whatever its later
verdict. Hiding a real crash would flatter every backtest, so crashes stay visible.

## 4. When a whole symbol is blocked (decided by market-logic, implemented)

A symbol block (scope `symbol`) hides every bar of that symbol from `prices_clean`. It is used **only for suspected
data corruption**; all of these log `warn` and the run continues. Each rule has at most one open block per symbol
and sits behind `QUALITY_ENFORCED_RULES`.

| Rule | Trigger |
|---|---|
| `corp_action_unadjusted` | One detection (section 3, step 2): every return across the ex-date is on the wrong price basis. If a re-fetch cannot fix it, the block becomes a span (section 5) |
| `nonpositive_recurring` | At least `NONPOSITIVE_BLOCK_COUNT` open `price_nonpositive` bar quarantines within the symbol's most recent `NONPOSITIVE_BLOCK_WINDOW_SESSIONS` stored sessions. One such bar is only a bar quarantine |
| `bad_bar_cluster` | At least `BAD_BAR_CLUSTER_LIMIT` open bar quarantines from `ohlc_inconsistent`, `move_bad_print`, `volume_zero_price_moved` and `price_nonpositive` within any `BAD_BAR_CLUSTER_WINDOW_SESSIONS` consecutive stored sessions (rolling over the whole history, so an old cluster blocks until a re-check or your verdict clears it). Resolved rows, `entry` and `span` scope, gap or zero-volume runs, `bar_nan_volume` and `flat_price_run` are not counted |

Sessions are counted in the symbol's stored bars, so missing bars shrink the windows.

**Not symbol blocks:**
- `volume_zero_ratio` is report-only. It is a liquidity question for the Phase 2 liquidity filter, computed
  point-in-time over a trailing window; over the whole history it would use future data to exclude a stock.
- `gap_ratio` is report-only. The broken lookbacks are handled by the `gap_run` span plus `GAP_LOOKBACK_BUFFER`.
- A genuine-looking unexplained big move, or a split-like pattern with no event row (section 3, step 4).

A block is cleared by a clean `recheck_symbol` (resolution `recheck_clean`), or by your manual verdict.
**A held symbol that gets blocked must be surfaced by the advisor, never dropped silently.**

## 5. Splits and history replacement

- Dividends are not stored. Splits (and bonus, rights, demerger rows) go to `corporate_actions`; `ratio` is the
  price divisor, old divided by new (2 for 1:2 or a 1:1 bonus, 1.5 for a 1:2 bonus, 0.1 for a 10:1 consolidation).
  Yahoo's event gives `numerator`/`denominator`; the ratio is numerator / denominator. **The event ratio is
  informational only and is never used to adjust prices.** It can also understate the real factor (BAJFINANCE
  2025-06-16: Yahoo lists 2:1; NSE raw closes went 9,331.00 to 938.00, a factor of about 10).
  Mismatches against NSE bhavcopy raw closes are logged when bhavcopy is available (not built yet).
- On a split event the symbol's whole history is replaced in one transaction
  (`advisor.data.prices.replace_symbol_history`). An empty replacement is refused, and any failure leaves the old
  history untouched.
- **A re-check is a required step after any history replacement.** `replace_symbol_history` runs
  `quality.recheck_symbol` inside the same transaction, with no way to skip it. A clean result clears the block
  (`recheck_clean`); if it raises, the replacement rolls back.
- **Unfixable events become a span, not a permanent block.** If the re-check right after a replacement still finds
  `corp_action_unadjusted` for an event that was already recorded before that replacement, the vendor's own history
  is unadjusted and a re-fetch cannot fix it. The symbol block is then resolved as `converted_to_span` and a `span`
  quarantine hides the bars **from the first stored bar to the last bar before the real jump bar**. There is no
  buffer: once every old-basis bar is hidden, the series starts at the jump like a new listing. A first detection
  still opens the symbol block; a plain re-check never converts. An event on the first stored bar leaves nothing
  to detect; a jump right after the first bar gives a one-bar span. Repeated re-checks never stack spans
  (`span_open`); a later replacement resolves the span as `history_replaced` and the re-check re-creates one only
  if the event is still unadjusted. Because features need history, Phase 2 must not use a series with fewer than the
  minimum number of bars since its start (see `plan.md`).
- Every run re-fetches the last `OVERLAP_BARS` bars of each active symbol and compares them with the stored
  bars. A difference above `REVISION_TOL_PCT` is logged as `bar_revised` (old and new values). A uniform ratio
  shift across the overlap (`READJUST_RATIO_TOL_PCT`) means the vendor re-based the history: replace and re-check.

## 5b. Run metadata and the Phase 1 exit

Every run's metadata must carry `symbols_blocked`, `symbols_entry_suppressed`, `move_unclassified_events` (every
`move_unclassified` event logged up to the session date, reviewed or not) and `move_unclassified_unreviewed_recent`
(the open ones within the last `MOVE_UNCLASSIFIED_REVIEW_SESSIONS` stored dates). `finish_run` refuses a run without
them. **Phase 1 exit:** `move_unclassified_unreviewed_recent` is 0, i.e. every `move_unclassified` event in the last
252 sessions has been reviewed (the entry quarantine is resolved with a verdict).

## 6. Freshness and calendar

- Latest expected session: the most recent `trading` or `special` date in `trading_calendar` on or before today
  (IST). Today counts only after `EOD_READY_IST`.
- A symbol's lag is the number of expected sessions after its last bar; it is stale if the lag exceeds
  `STALE_LAG_SESSIONS_MAX`. Delisted symbols are exempt.
- The calendar is weekdays minus the fetched NSE holidays, plus special sessions. It is never built from bars
  (bars are only a cross-check), and never assumes every weekday is a trading day. With a stored list no older
  than `CALENDAR_MAX_AGE_DAYS` a failed holiday fetch warns and uses it; otherwise `calendar_unavailable` blocks the run.

## 7. Price sources, history window and the universe

- History starts at `HISTORY_START` (2005-01-01). The fetcher requests daily bars with explicit `period1` and
  `period2` timestamps and `interval=1d`, **never `range=max`** (live check: it silently returns monthly bars),
  passes `events=split`, and fails loudly if the median gap between returned bars exceeds 4 days. Before 2005 the
  live check found unadjusted splits (RELIANCE 1997, WIPRO 1997 and 1999) and many zero-volume bars.

- Stored source: Yahoo only (`PRICE_SOURCE_ORDER`). NSE bhavcopy is raw and unadjusted, so it is only a
  cross-check (`PRICE_CROSSCHECK_SOURCES`), never stored.
- Placeholder symbols matching `UNIVERSE_PLACEHOLDER_REGEX` (for example DUMMYHEG) are removed at universe load,
  before any fetch, with one `info` row per symbol per build. A 404 for a symbol with no stored bars is
  `fetch_symbol_not_found`; a 404 for one with stored bars is `fetch_symbol_lost` (history is kept).

## 8. Thresholds (values generated from `advisor/config.py`)

| Constant | Value | Meaning |
|---|---|---|
| **Universe and clock** | | |
| `HISTORY_START` | `2005-01-01` | First date of stored history; the fetcher requests daily bars from here (never range=max) |
| `UNIVERSE_PLACEHOLDER_REGEX` | `^DUMMY[A-Z0-9]*$` | Symbols matching this are dropped at universe load and logged (`info`), never fetched |
| `EOD_READY_IST` | `19:00` | Today's bar is not stored before this time (IST) |
| `RUN_DEADLINE_IST` | `21:30` | A trading day's update must have succeeded by now, else `check-run` errors |
| `SESSION_OPEN_IST` | `09:15` | Expected local time of a daily bar (date-mapping check) |
| `SESSION_OPEN_TOL_MIN` | `30` | Minutes either side of the open |
| **Bar sanity and moves** | | |
| `OHLC_TOL_PCT` | `0.05` | Tolerance (%) on high >= max(o,c,l) and low <= min(o,c,h) |
| `MOVE_LARGE_PCT` | `20.0` | |one-day move| above this: warn, then the split-ratio test |
| `MOVE_EXTREME_PCT` | `35.0` | Above this (close-to-close or wick): flag, keep the bar, no new entry that day |
| `SPLIT_RATIOS` | `1.25, 1.333, 1.5, 2, 2.5, 3, 4, 5, 10, 20` | Standard split/bonus price ratios; also tested inverted (reverse split) |
| `SPLIT_RATIO_TOL_PCT` | `3.0` | How close the observed ratio must be to a standard one |
| `CORP_ACTION_WINDOW_SESSIONS` | `3` | +/- sessions around an ex-date that can explain a move (demerger, rights, scheme) |
| `EVENT_JUMP_WINDOW_SESSIONS` | `2` | +/- stored sessions around a recorded ex-date searched for the event's own price jump |
| `MOVE_UNCLASSIFIED_REVIEW_SESSIONS` | `252` | move_unclassified events newer than this many stored sessions must be reviewed |
| `BAD_PRINT_REVERT_PCT` | `25.0` | Next-day reversal size that marks a bad print |
| `BAD_PRINT_RETURN_BAND_PCT` | `10.0` | Next close within this % of the pre-move close |
| `BAD_PRINT_WICK_CLOSE_MAX_PCT` | `20.0` | Extreme wick with a close inside this % |
| `BAD_PRINT_LOW_VOLUME_FRAC` | `0.1` | Volume below this fraction of the median |
| `VOLUME_MEDIAN_WINDOW` | `20` | Sessions in the volume median |
| **Symbol blocks (suspected corruption only)** | | |
| `NONPOSITIVE_BLOCK_COUNT` | `2` | Open price_nonpositive bar quarantines that block the symbol ... |
| `NONPOSITIVE_BLOCK_WINDOW_SESSIONS` | `252` | ... within the symbol's most recent N stored sessions |
| `BAD_BAR_CLUSTER_LIMIT` | `3` | Open bad-print-type bar quarantines that block the symbol ... |
| `BAD_BAR_CLUSTER_WINDOW_SESSIONS` | `63` | ... within any N consecutive stored sessions |
| **Volume and flat runs** | | |
| `VOL0_RUN_WARN` | `3` | Consecutive zero-volume bars (stocks only): warn |
| `VOL0_RUN_ERROR` | `5` | ...at this length the run is hidden from prices_clean (span) |
| `VOL0_RATIO_WINDOW` | `252` | Sessions for the zero-volume share |
| `VOL0_RATIO_MAX_PCT` | `2.0` | Zero-volume share above this: REPORT ONLY (liquidity, not corruption) |
| `FLAT_RUN_WARN` | `5` | Consecutive identical-OHLC bars: warn |
| `FLAT_RUN_ERROR` | `10` | ...at this length the run is hidden (span) |
| **Calendar gaps** | | |
| `GAP_RUN_WARN` | `3` | Consecutive missing expected sessions: warn |
| `GAP_RUN_ERROR` | `10` | ...at this length the span is hidden, plus GAP_LOOKBACK_BUFFER sessions after it |
| `GAP_RATIO_WARN_PCT` | `1.0` | Share of missing sessions: warn |
| `GAP_RATIO_ERROR_PCT` | `3.0` | Share above this: REPORT ONLY (the broken lookbacks are already handled by gap runs) |
| `GAP_LOOKBACK_BUFFER` | `252` | Sessions hidden after a long gap run (one trading year) |
| `MARKET_WIDE_PCT` | `50.0` | Share of symbols that makes a gap or holiday mismatch market-wide |
| `MARKET_WIDE_RECENT_SESSIONS` | `5` | A market-wide gap within this many sessions blocks the run |
| `CALENDAR_MAX_AGE_DAYS` | `7` | Age limit for a stored holiday list |
| **Freshness** | | |
| `FRESHNESS_BENCHMARK` | `^NSEI` | Benchmark symbol (an `instruments` row with kind='index') |
| `STALE_LAG_SESSIONS_MAX` | `0` | Any lag behind the latest closed session is stale |
| `STALE_SYMBOL_PCT_REPORT` | `2.0` | Report-only stale share, used until `stale_universe` is enforced |
| `STALE_SYMBOL_PCT_MAX` | `5.0` | Stale share that blocks the run once enforced |
| `STALE_REQUIRE_FRESH_HELD_OR_SIGNALLING` | `True` | Once enforced: stale held/signalling symbol is withheld and flagged red |
| **Revisions and fetch hygiene** | | |
| `OVERLAP_BARS` | `10` | Bars re-fetched and compared on every run |
| `REVISION_TOL_PCT` | `0.5` | A stored-vs-fetched difference above this is logged as `bar_revised` |
| `READJUST_RATIO_TOL_PCT` | `1.0` | Uniform ratio shift across the overlap => `history_readjusted` |
| `NOT_FOUND_RUNS_ESCALATE` | `3` | Consecutive 404 runs before `fetch_symbol_not_found` becomes an error |
| `PRICE_SOURCE_ORDER` | `yahoo` | Stored price sources. Yahoo only (split-adjusted) |
| `PRICE_CROSSCHECK_SOURCES` | `nse_bhavcopy` | Raw NSE bhavcopy: cross-check only, never stored |
| **Fetcher settings (fetchers not written yet)** | | |
| `FETCH_TIMEOUT_S` | `20` | Per-request timeout |
| `FETCH_MAX_RETRIES` | `4` | Retries after the first attempt |
| `FETCH_BACKOFF_BASE_S` | `2` | Backoff base: waits of 2, 4, 8, 16 s |
| `FETCH_DELAY_S` | `0.5` | Pause between successive requests |
| `FETCH_429_WAIT_S` | `60` | Wait after HTTP 429, then retry the same request |
| `FETCH_429_MAX_CONSECUTIVE` | `3` | Consecutive 429s that stop the run |
| `FETCH_BATCH_SIZE` | `1` | Symbols per request (sequential) |

## 9. Verified and unverified

Verified on live Yahoo data (read-only, 2026-11-23): daily bars need explicit period1/period2; prices are
split- and bonus-adjusted from 2004 on in all tested symbols; volume is split-adjusted; split events carry
`numerator`/`denominator`/ex-date at 09:15 IST; 1:1 bonuses appear as 2:1 splits; today's bar is partial during
market hours; null rows fall mostly on weekend special sessions; `^NSEI` has zero volume on many bars.

Known limits of the rules as built:
- A crash on the ex-date of a recorded event whose ratio equals the crash is blocked as an unadjusted split (the
  prices cannot tell them apart).
- The event-row date may be off by up to `EVENT_JUMP_WINDOW_SESSIONS` stored sessions. Further off, the jump shows
  only as `move_unclassified` entry suppression and the fake jump stays in `prices_clean`. The window counts stored
  bars, so missing bars stretch it over more real trading days. If two jumps near one event both match the ratio,
  only the closest is used; the other gets entry suppression only.
- A combined split and bonus may be recorded with a smaller ratio than the real factor, so an unadjusted
  combined event would be missed (it cannot false-block).
- Yahoo's old unadjusted events (1997, 1999) fall before `HISTORY_START`; whether any unadjusted events exist
  after 2005 is not yet known.

Not yet verified: when Yahoo's daily candle for an NSE stock becomes final (`EOD_READY_IST` is a cautious guess; the
overlap re-fetch is the safety net); whether NSE's holiday endpoint gives past years; every threshold is reasoned,
not tested on real data, which is what the dry-run report is for.
