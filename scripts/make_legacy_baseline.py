"""Freeze a baseline of the legacy swing backtest (run AFTER `PYTHONPATH=legacy python -m lab.setup`).

Writes small, committable files to baselines/legacy_swing/ (summary.csv, manifest.json) and copies the big,
local-only inputs/outputs (bars snapshot, trades pickle) to data/baseline_snapshot/ (git-ignored; back it up).
Usage: PYTHONPATH=legacy python scripts/make_legacy_baseline.py [setup.log]
"""
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "legacy"))
from lab import bars as B  # noqa: E402
from lab import swing_an as A  # noqa: E402

OUT = ROOT / "baselines" / "legacy_swing"
SNAP = ROOT / "data" / "baseline_snapshot"


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(log_path: str | None) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    SNAP.mkdir(parents=True, exist_ok=True)
    trades_p = B.ROOT.parent / "swing_trades.pkl"
    t = pd.read_pickle(trades_p)
    ew, _ = A.ew_index()
    tab, _ = A.table(t, ew)
    tab = tab.reset_index()
    tab.to_csv(OUT / "summary.csv", index=False)

    files = sorted(p for p in (B.ROOT / "1d").glob("*.pkl"))
    hashes = {p.stem: sha256(p) for p in files}
    bars_digest = hashlib.sha256("".join(f"{k}:{v}" for k, v in sorted(hashes.items())).encode()).hexdigest()
    last = {}
    for p in files:
        d = pd.read_pickle(p)
        last[p.stem] = (str(d.index.min().date()), str(d.index.max().date()), len(d))
    stocks = [k for k in hashes if k not in B.INDICES]
    missing_idx = [k for k in B.INDICES if k not in hashes]

    log_counts = {}
    if log_path and Path(log_path).exists():
        txt = Path(log_path).read_text(encoding="utf-8", errors="replace")
        log_counts = {"skip_lines": len(re.findall(r"^\s*skip ", txt, re.M)),
                      "err_lines": len(re.findall(r"^err ", txt, re.M)),
                      "http_429_waits": txt.count("429 from Yahoo")}
        shutil.copy(log_path, SNAP / "setup.log")
        log_counts["skipped_symbols"] = sorted(set(re.findall(r"^\s*skip (\S+) 1d", txt, re.M)))

    manifest = {
        "created": date.today().isoformat(),
        "git_commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                     text=True).stdout.strip(),
        "python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__,
        "command": "PYTHONUTF8=1 PYTHONPATH=legacy python -m lab.setup  (daily bars + lab.swing.run_all)",
        "stocks_with_bars": len(stocks), "indices_with_bars": len(hashes) - len(stocks), "missing_indices": missing_idx,
        "bar_first_date_min": min(v[0] for v in last.values()), "bar_last_date_max": max(v[1] for v in last.values()),
        "trades": int(len(t)), "stocks_with_trades": int(t.sym.nunique()),
        "trade_d_in_range": [str(t.d_in.min()), str(t.d_in.max())],
        "strategy_exit_combos": int(len(tab)),
        "bars_digest_sha256": bars_digest, "trades_pkl_sha256": sha256(trades_p),
        "summary_csv_sha256": sha256(OUT / "summary.csv"),
        "survivorship": "BIASED: universe is today's Nifty 500 + F&O list applied to past dates",
        "legacy_log_counts": log_counts,
        "local_only_files": "data/baseline_snapshot/ (bars_1d.zip, swing_trades.pkl, setup.log); back these up",
        "per_symbol_bars": {k: {"sha256": hashes[k], "first": last[k][0], "last": last[k][1], "rows": last[k][2]}
                            for k in sorted(hashes)},
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    shutil.copy(trades_p, SNAP / "swing_trades.pkl")
    shutil.make_archive(str(SNAP / "bars_1d"), "zip", B.ROOT / "1d")
    print(json.dumps({k: v for k, v in manifest.items() if k != "per_symbol_bars"}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
