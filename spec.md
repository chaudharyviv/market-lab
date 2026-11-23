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
| F1 | Fetch, validate and store daily OHLCV for the universe in SQLite, with a data-quality log. |
| F2 | Keep point-in-time index membership and delisted names where obtainable; otherwise flag every backtest as survivorship-biased. |
| F3 | Backtest swing strategies with the existing delivery cost model, next-open entries and gap-aware stops. |
| F4 | Validate with walk-forward splits and an untouched final holdout; record the number of variants tried. |
| F5 | A strategy may advise only after passing the promotion gates in section 6. |
| F6 | Store my profile (capital, risk per trade, limits) and my transactions; derive open positions. |
| F7 | Size each new trade from capital, stop distance and risk per trade; refuse trades that breach guardrails. |
| F8 | Daily run produces recommendations: BUY / HOLD / TRIM / EXIT / AVOID, each with entry, stop, target, rupee risk. |
| F9 | Rules engine decides; an LLM only writes the explanation from the engine's JSON facts. |
| F10 | Log every signal and recommendation; paper-trade them; report live hit rate, R-multiple and drawdown vs the backtest. |
| F11 | Alert on triggers, stop/target hits, data failures and guardrail breaches. |

## 5. Non-functional requirements
- Reproducible: the same data snapshot and config give the same signals.
- Every external fetch has timeout, retry with backoff, and a logged failure.
- Test coverage on cost model, sizing, guardrails and backtest mechanics (no look-ahead).
- No secrets in the repo. Config in `config.py` / `.env`.

## 6. Promotion gates (a strategy advises only if ALL hold)
1. Positive net expectancy after costs in at least 4 of 5 calendar years.
2. Positive on the untouched holdout period, not used for any choice.
3. Beats the equal-weight universe and Nifty on risk-adjusted return, with the variants-tested count reported.
4. At least 30 days of paper-trading with live results within tolerance of the backtest.
Thresholds are initial and may be tightened, never loosened to rescue a strategy.

## 7. Open decisions
- Starting capital: ____ INR
- Risk per trade (default 0.5%), max open risk (5%), max positions (10), sector cap (30%), daily/weekly loss limits (1.5% / 4%): confirm or change.
- Point-in-time membership source: investigate NSE archives; fall back to biased flag.
- LLM provider for explanations.

## 8. Success criteria
- A daily report I can act on in 10 minutes.
- Live paper results track the validated backtest within tolerance for 3 months before I commit real capital.
