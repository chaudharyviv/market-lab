# AGENT.md: working rules for Claude Code on this repo

Read `spec.md` and `plan.md` first. Work on one plan task at a time. Reference this file from `CLAUDE.md`
(for example, a single line: "Follow agent.md").

## Project in one line
Local, advisory-only swing-trading advisor for NSE cash-segment stocks. Python 3.10+, SQLite, pandas/numpy. No broker APIs.

## Hard rules
1. **Never place orders or add broker integrations.** Advisory only.
2. **Never weaken validation to make a strategy pass.** Promotion gates in spec section 6 change only by explicit instruction from me, and only tighter.
3. **No look-ahead.** Signals use data up to the close of day t; entries are at the open of day t+1. Add or keep a test for this whenever you touch features or backtests.
4. **Rules decide; the LLM only explains.** Explanation text may use only numbers present in the recommendation JSON.
5. **Fail loudly.** No bare `except: pass`. Log failures to `data_quality_log` and surface them in reports. Never serve stale prices silently.
6. **Do not edit `legacy/`.** Port by copying into `advisor/` with tests.
7. **No secrets in the repo.** Use `.env`; keep `.env.example` current.
8. **Ask before** adding a dependency, changing the schema, changing a threshold, or deleting data.

## Conventions
- Package layout as in plan.md Phase 0. Type hints on public functions; short docstrings that state units (INR, %, R).
- Money in INR; percentages as plain numbers with the unit in the name (`risk_pct`).
- Schema changes go through numbered migrations, never ad-hoc `ALTER`.
- Config values live in `config.py`; no magic numbers in logic.
- Every task: tests first or alongside; run `pytest` and `ruff` before declaring done.

## Definition of done (per task)
- Tests added and passing; no unrelated files changed.
- Exit criteria of the current phase still pass.
- Short note appended to `docs/decisions.md` for any non-obvious choice.
- Commit message states what changed and why.

## Data sources and etiquette
- Yahoo Finance (daily bars) and NSE public endpoints, fetched one at a time with delays; on HTTP 429 wait, never work around it.
- Treat both as unreliable: validate every response, retry with backoff, log every failure.

## Testing priorities (highest risk first)
Cost model, position sizing, guardrails, backtest entry/exit mechanics, data quality checks, then report formatting.

## When unsure
Stop and ask. A wrong guess in sizing, costs or validation is more expensive than a question.

## Optional MCP servers (add only if useful)
- GitHub: PRs and issues.
- A library-docs server (such as Context7) for pandas/SQLite API lookups.
Not needed: any trading or market-data MCP. Keep the data path in our own tested code.
