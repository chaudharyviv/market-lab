# CLAUDE.md

Personal swing-trading advisor for NSE cash-segment stocks. Advisory only: it never places orders.

## Read first, every session
1. `agent.md` (working rules, follow them strictly)
2. `spec.md` (what we are building and the promotion gates)
3. `plan.md` (phases and tasks; work on ONE unchecked task at a time, in order)

## Stack and commands
- Python 3.10+, SQLite, pandas, numpy. Dependencies are in `requirements.txt`; ask before adding any.
- Run tests: `pytest`
- Lint: `ruff check .`
- Legacy app (reference only): `python app.py`, then open http://localhost:8810

## Layout
- `advisor/`: the new code (data, signals, validation, portfolio, advisor, journal, alerts)
- `legacy/`: original market-lab code (lab/, sm/, sx/). Do NOT edit; port by copying into `advisor/` with tests.
- `advisor_schema.sql` and `migrations/`: database schema
- `docs/decisions.md`: log every non-obvious decision here

## Non-negotiables (details in agent.md)
- No order placement, no broker APIs.
- No look-ahead: signal at close of day t, entry at open of day t+1.
- Never loosen validation gates to make a strategy pass.
- The rules engine decides; the LLM only explains from the engine's JSON.
- No silent failures; log to `data_quality_log`.
- Ask before changing the schema, thresholds or dependencies.

## Before saying a task is done
Tests pass, ruff is clean, the phase exit criteria still hold, the checkbox in `plan.md` is ticked, and a one-line
entry is added to `docs/decisions.md` if a choice was made.

## Attribution
Parts of this codebase derive from `heysanjeevv/market-lab` (MIT). Keep `LICENSE` and its copyright notice.
