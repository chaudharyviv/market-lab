# SPEC: Personal Swing-Trading Advisor (Indian stocks, cash segment)

Status: draft v1 · Owner: Vivikt · Base: fork of `heysanjeevv/market-lab` (MIT)

## 1. Goal
A personal, local tool that tells me, after each market close, what to do with my swing-trade book and what new
setups qualify, with position sizes that respect my risk limits and a plain-language reason for each call.
**Advisory only. It never places orders.**

## 2. Scope
In scope
- NSE cash-segment stocks (Nifty 500 + F&O universe), delivery (CNC), **long-only swing** (days to ~8 weeks).
- Daily bars; end-of-day decisions; next-open entries.
- My real positions (manual entry), position sizing, portfolio guardrails.
- Honest validation of every strategy before it can advise me.
- Paper-trade log of every recommendation, scored against what actually happened.
- Alerts to Discord/Telegram.

Out of scope (v1)
- Mutual funds, SIPs, bonds, gold, tax planning.
- Groww API or any broker API. No order placement.
- Intraday, F&O, shorting, options.
- Long-term investing mode (later, separate spec).

## 3. Users and constraints
- Single user, runs locally (Windows/Linux). Python 3.10+, SQLite, no paid services required.
- Data: Yahoo Finance (prices) and NSE public endpoints (lists, filings). Both unofficial and may break, so the
  data layer must fail loudly and never silently serve stale data.
- Outputs are decision support, not investment advice. Any LLM text must be grounded in rules-engine output.

## 4. Functional requirements
| ID | Requirement |
|----|-------------|
| F1 | Fetch, validate and store daily OHLCV for the universe in SQLite, with a data-quality log. The current session's bar is excluded until after the close (section 9.7). |
| F2 | Keep point-in-time index membership and delisted names where obtainable; otherwise flag every backtest as survivorship-biased. |
| F3 | Backtest swing strategies with the delivery cost model, liquidity-dependent slippage (replaces the legacy flat 0.05% per side), next-open entries, gap-aware stops, and close-triggered exits filled at the next open (section 9). |
| F4 | Validate with walk-forward splits and an untouched final holdout. Strategies and parameters are chosen inside walk-forward windows only; the strategy list and exit choices are declared before testing; the number of variants tried is recorded. |
| F5 | A strategy may advise only after passing the promotion gates in section 6. |
| F6 | Store my profile (capital, risk per trade, limits) and my transactions; derive open positions. |
| F7 | Size each new trade from capital, stop distance and risk per trade; refuse trades that breach guardrails. |
| F8 | Daily run produces recommendations: BUY / HOLD / TRIM / EXIT / AVOID, each with entry, stop, target, rupee risk. |
| F9 | Rules engine decides; an LLM only writes the explanation from the engine's JSON facts. |
| F10 | Log every signal and recommendation; paper-trade them; report live hit rate, R-multiple and drawdown vs the backtest. |
| F11 | Alert on triggers, stop/target hits, data failures and guardrail breaches. |
| F12 | Apply a minimum daily-turnover filter to the tradable universe and a slippage assumption that depends on liquidity. Thresholds live in `config.py`. |
| F13 | Evaluate every strategy at portfolio level (max drawdown, Sharpe, worst month), with risk-based position sizing, not only per trade. |

## 5. Non-functional requirements
- Reproducible: the same data snapshot and config give the same signals.
- Every external fetch has timeout, retry with backoff, and a logged failure.
- Test coverage on cost model, sizing, guardrails and backtest mechanics (no look-ahead).
- No secrets in the repo. Config in `config.py` / `.env`.

## 6. Promotion gates (a strategy advises only if ALL hold)
1. Positive net expectancy after costs in at least 4 of 5 buckets: 2022, 2023, 2024, 2025 (1 Jan to 2025-11-23, the day before the holdout starts), and the holdout year (2025-11-24 to 2026-11-23). Each bucket is reported separately.
2. Positive on the untouched holdout period, not used for any choice.
3. Beats the equal-weight universe and Nifty on risk-adjusted return, with the variants-tested count reported.
4. At least 30 days of paper-trading with live results within tolerance of the backtest.
Thresholds are initial and may be tightened, never loosened to rescue a strategy.

## 7. Decisions and open items
Confirmed (2026-11-23). These replace the earlier defaults (max open risk 5%, max positions 10) and the blank capital.
| Item | Value | In INR at the starting capital |
|---|---|---|
| Starting capital | 5,00,000 INR | 5,00,000 |
| Risk per trade | 0.5% of capital | 2,500 |
| Max open positions | 6 | n/a |
| Max open risk (sum of risk across open positions) | 4% of capital | 20,000 |
| Sector cap | 30% of capital in one sector | n/a |
| Daily loss limit | 1.5% of capital | 7,500 |
| Weekly loss limit | 4% of capital | 20,000 |
| Horizon | swing | n/a |

Rupee amounts are for the starting capital only; the engine computes them from current capital. These values are not in `config.py` or the database yet.

Still open:
- Point-in-time membership source: investigate NSE archives; fall back to biased flag.
- LLM provider for explanations.
- Liquidity: minimum daily turnover (INR) and the slippage schedule by liquidity band (values go in `config.py`, to be confirmed before Phase 2).

## 8. Success criteria
- A daily report I can act on in 10 minutes.
- Live paper results track the validated backtest within tolerance for 3 months before I commit real capital.

## 9. Modelling assumptions and known caveats
Decided 2026-11-23. Each applies to every backtest and report unless stated otherwise.
1. **Pre-tax results.** All results are before tax. The fixed 20% short-term capital gains tax is ignored on purpose, so reported returns overstate what is kept after tax. Transaction costs (brokerage, STT, exchange and other charges) are still modelled.
2. **Liquidity and slippage.** A minimum daily-turnover filter (close x volume, INR, over a trailing window) decides which symbols are tradable on a given day. Slippage per side depends on liquidity (more for thinner names) and replaces the legacy flat 0.05% per side. The filter threshold, window and slippage schedule are constants in `config.py`, to be confirmed with the user before Phase 2.
3. **Sizing and portfolio evaluation.** Positions are sized by risk (capital x risk per trade / stop distance), not equal weight. Every strategy is evaluated at portfolio level (max drawdown, Sharpe, worst calendar month) as well as per trade.
4. **Exits.** An exit triggered by a close (signal at the close of day t) fills at the open of day t+1. Stops and targets hit intraday keep gap-aware fills. This is fixed in the Phase 2 port of the legacy `follow()` logic.
5. **Selection discipline.** Strategies and parameters are chosen inside walk-forward windows only, never on the full period. The strategy list and exit choices are declared before testing, in a file committed before any run, and the variants-tested count is recorded per run. Section 6 is not changed by this section.
6. **Zero-edge random-data check.** Phase 3 runs the engine on randomised zero-edge data to show how much of a result luck alone can produce. Method: cut the days into blocks of 20 days and permute the blocks with one permutation applied to all symbols (so cross-sectional structure is kept); shuffle whole bars (gap, range and volume move together); set drift to zero; keep costs; run many seeds; use no holdout dates. The real result is read against the resulting distribution.
7. **Partial bars.** The current session's bar is excluded until after the close (`EOD_READY_IST`); Phase 1 drops it. The Phase 0 baseline's last day (2026-11-23) was fetched during market hours and is treated as possibly partial. It is also the last day of the holdout window (`config.HOLDOUT_END`).
8. **Universe caveat (survivorship and membership).** The universe is today's Nifty 500 + F&O list, so every backtest on it is survivorship-biased and not point-in-time for index membership. Delisted names and historical membership are included only where obtainable. Every backtest run records this caveat (`point_in_time_universe` and a caveat note in its metrics) and every report that quotes a result repeats it.
