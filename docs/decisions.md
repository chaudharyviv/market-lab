# Decisions log

One line per non-obvious choice. Newest last.

- 2026-11-23 Phase 0: moved `lab/ sm/ sx/` to `legacy/` with `git mv` (100% renames, verified by `git show -M --stat`); no `legacy/__init__.py`.
- 2026-11-23 Phase 0: `app.py`/`run.*` left unedited, so the legacy app now needs `PYTHONPATH=legacy` (run `PYTHONPATH=legacy python app.py`; PowerShell: `$env:PYTHONPATH='legacy'`). `run.bat`/`run.sh` will fail until that is set; CLAUDE.md run line updated.
- 2026-11-23 Phase 0: legacy `DATA` is derived from `__file__`, so legacy caches now live in `legacy/data/` (git-ignored via `data/`). The new advisor uses root `data/` (config.py).
- 2026-11-23 Phase 0: `config.py` holds paths/env only. Capital, risk %, caps, loss limits and holdout dates (spec section 7) are unconfirmed and intentionally absent.
- 2026-11-23 Phase 0: pytest and ruff go in `requirements-dev.txt` (user named them); `requirements.txt` untouched. ruff targets py310, excludes `legacy/` and `app.py` (legacy code with 34 style findings; not edited).
- 2026-11-23 Phase 0: pre-commit NOT installed (new dependency, not approved); inert `.pre-commit-config.yaml` only. Plan item left unticked.
