# Decisions log

One line per non-obvious choice. Newest last.

- 2026-11-23 Phase 0: moved `lab/ sm/ sx/` to `legacy/` with `git mv` (100% renames, verified by `git show -M --stat`); no `legacy/__init__.py`.
- 2026-11-23 Phase 0: `app.py`/`run.*` left unedited, so the legacy app now needs `PYTHONPATH=legacy` (run `PYTHONPATH=legacy python app.py`; PowerShell: `$env:PYTHONPATH='legacy'`). `run.bat`/`run.sh` will fail until that is set; CLAUDE.md run line updated.
- 2026-11-23 Phase 0: legacy `DATA` is derived from `__file__`, so legacy caches now live in `legacy/data/` (git-ignored via `data/`). The new advisor uses root `data/` (config.py).
- 2026-11-23 Phase 0: `config.py` holds paths/env only. Capital, risk %, caps, loss limits and holdout dates (spec section 7) are unconfirmed and intentionally absent.
- 2026-11-23 Phase 0: pytest and ruff go in `requirements-dev.txt` (user named them); `requirements.txt` untouched. ruff targets py310, excludes `legacy/` and `app.py` (legacy code with 34 style findings; not edited).
- 2026-11-23 Phase 0: pre-commit NOT installed (new dependency, not approved); inert `.pre-commit-config.yaml` only. Plan item left unticked.
- 2026-11-23 Phase 0 baseline: ran `PYTHONPATH=legacy python -m lab.setup` (daily bars only) on Python 3.12.10 / pandas 2.3.3 / numpy 2.5.3: 500 stocks + 16 indices, bars 2021-11-23..2026-11-23, 629,060 trades, 141 strategy/exit combos. Windows ran single-process in 6 min. Universe is today's list, so the baseline is survivorship-biased.
- 2026-11-23 Phase 0 baseline: legacy swallowed 2 symbol failures (BAGMANE 404, DUMMYHEG); no 429s, no backtest errors. Recorded in manifest. Yahoo's 5y window slides daily, so the exact inputs are frozen in local-only `data/baseline_snapshot/` (bars zip + trades pkl + log; hashes in manifest). Phase 2 must regress against that snapshot, not a fresh download.
- 2026-11-23 Phase 0: legacy app smoke test passes with `PYTHONPATH=legacy` (`/api/status` ready).
