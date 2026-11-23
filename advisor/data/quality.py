"""Data-quality checks on RAW prices_daily rows (Phase 1). Only the unadjusted split/bonus checks exist so far.

Severity follows the approved scheme: these are per-symbol problems, so they log `warn` and the run continues.
A detection is suspected data corruption (every return across the ex-date is on the wrong price basis), so it
opens ONE symbol-scope quarantine, which hides all the symbol's bars from prices_clean. That block is opened
only if the check is in `config.QUALITY_ENFORCED_RULES` (empty until the dry-run report is approved); until
then the detection is logged and reported, not enforced. No row of prices_daily is ever changed here.

Sessions are counted in the symbol's stored bars (trading_calendar is not consulted yet).
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone

from advisor import config
from advisor.data.db import transaction

CHECK_MOVE_SPLIT = "move_unadjusted_split"
CHECK_CA_UNADJUSTED = "corp_action_unadjusted"
SPLIT_CHECKS = (CHECK_MOVE_SPLIT, CHECK_CA_UNADJUSTED)
CHECK_CA_RATIO_MISSING = "corp_action_ratio_missing"
CHECK_SPLIT_RECHECK = "split_recheck"
RESOLUTION_RECHECK_CLEAN = "recheck_clean"


@dataclass(frozen=True)
class SplitFinding:
    """One suspected unadjusted split/bonus. Prices in INR; ratio = prev_close / close (price divisor)."""

    check_name: str
    bar_date: str
    prev_date: str
    prev_close: float
    close: float
    raw_ratio: float
    matched_ratio: float
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class RecheckResult:
    """Outcome of recheck_symbol. `still_blocked` = the symbol is in blocked_symbols (any check)."""

    symbol: str
    findings: tuple[SplitFinding, ...]
    blocks_resolved: int
    still_blocked: bool


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _within(a: float, b: float, tol_pct: float) -> bool:
    """True if a/b is within tol_pct % of 1."""
    return abs(a / b - 1.0) * 100.0 <= tol_pct


def _match_split_ratio(raw: float) -> float | None:
    """Nearest standard ratio within tolerance, in the same direction as `raw` (forward > 1, reverse < 1)."""
    mag = raw if raw >= 1.0 else 1.0 / raw
    for r in config.SPLIT_RATIOS:
        if _within(mag, r, config.SPLIT_RATIO_TOL_PCT):
            return r if raw >= 1.0 else 1.0 / r
    return None


def _bars(conn: sqlite3.Connection, symbol: str) -> list[sqlite3.Row]:
    q = "SELECT date, open, high, low, close, volume FROM prices_daily WHERE symbol = ? ORDER BY date"
    return conn.execute(q, (symbol,)).fetchall()


def _split_actions(conn: sqlite3.Connection, symbol: str) -> list[sqlite3.Row]:
    q = ("SELECT ex_date, action, ratio FROM corporate_actions WHERE symbol = ? AND action IN ('split','bonus') "
         "ORDER BY ex_date")
    return conn.execute(q, (symbol,)).fetchall()


def _first_index_on_or_after(bars: list[sqlite3.Row], day: str) -> int | None:
    for i, b in enumerate(bars):
        if b["date"] >= day:
            return i
    return None


def _volume_confirms(bars: list[sqlite3.Row], i: int, ratio: float) -> bool:
    """Median volume over the persistence window from bar i vs the full prior median window (NULLs skipped).

    Needs a complete prior window and complete post window of known volumes; otherwise no volume evidence.
    """
    w, p = config.VOLUME_MEDIAN_WINDOW, config.SPLIT_PERSIST_SESSIONS
    if i - w < 0:
        return False
    pre = [b["volume"] for b in bars[i - w:i] if b["volume"] is not None]
    post = [b["volume"] for b in bars[i:i + p] if b["volume"] is not None]
    if len(pre) < w or len(post) < p:
        return False
    pre_med = statistics.median(pre)
    if pre_med <= 0:
        return False
    factor = statistics.median(post) / pre_med
    frac = config.SPLIT_VOLUME_CONFIRM_FRAC
    return factor >= frac * ratio if ratio >= 1.0 else factor <= ratio / frac


def _move_findings(bars: list[sqlite3.Row], actions: list[sqlite3.Row]) -> list[SplitFinding]:
    """Classification step 2: a > MOVE_LARGE_PCT close-to-close move matching a standard ratio, holding for
    SPLIT_PERSIST_SESSIONS sessions, plus evidence (a split/bonus row near the date, or a volume jump)."""
    out: list[SplitFinding] = []
    p = config.SPLIT_PERSIST_SESSIONS
    action_idx = [(_first_index_on_or_after(bars, a["ex_date"]), a["ratio"]) for a in actions]
    for i in range(1, len(bars)):
        prev, cur = bars[i - 1], bars[i]
        if prev["close"] <= 0 or cur["close"] <= 0:
            continue  # non-positive prices are a different check
        if abs(cur["close"] / prev["close"] - 1.0) * 100.0 <= config.MOVE_LARGE_PCT:
            continue
        raw = prev["close"] / cur["close"]
        matched = _match_split_ratio(raw)
        if matched is None or i + p - 1 >= len(bars):
            continue  # no ratio match, or not enough sessions yet to see whether it holds
        held = all(
            bars[i + k]["close"] > 0
            and _within(bars[i + k]["close"], cur["close"], config.SPLIT_PERSIST_BAND_PCT)
            for k in range(1, p)
        )
        if not held:
            continue
        evidence = []
        for j, ratio in action_idx:
            if j is None or abs(j - i) > config.CORP_ACTION_WINDOW_SESSIONS:
                continue
            if ratio is None or (ratio > 0 and _within(raw, ratio, config.SPLIT_RATIO_TOL_PCT)):
                evidence.append("corp_action_row")
                break
        if _volume_confirms(bars, i, matched):
            evidence.append("volume_jump")
        if evidence:
            out.append(SplitFinding(CHECK_MOVE_SPLIT, cur["date"], prev["date"], prev["close"], cur["close"],
                                    raw, matched, tuple(evidence)))
    return out


def _corp_action_findings(
    bars: list[sqlite3.Row], actions: list[sqlite3.Row]
) -> tuple[list[SplitFinding], list[sqlite3.Row]]:
    """For every split/bonus row: is the raw close jump across the ex-date still about the recorded ratio?

    Returns (findings, rows that could not be checked because the ratio is missing or meaningless).
    """
    found: list[SplitFinding] = []
    unverifiable: list[sqlite3.Row] = []
    for a in actions:
        ratio = a["ratio"]
        if ratio is None or ratio <= 0 or _within(ratio, 1.0, config.SPLIT_RATIO_TOL_PCT):
            unverifiable.append(a)
            continue
        j = _first_index_on_or_after(bars, a["ex_date"])
        if j is None or j == 0:
            continue  # stored history does not span the ex-date
        prev, cur = bars[j - 1], bars[j]
        if prev["close"] <= 0 or cur["close"] <= 0:
            continue
        raw = prev["close"] / cur["close"]
        if _within(raw, ratio, config.SPLIT_RATIO_TOL_PCT):
            found.append(SplitFinding(CHECK_CA_UNADJUSTED, cur["date"], prev["date"], prev["close"], cur["close"],
                                      raw, ratio, (f"{a['action']}_row",)))
    return found, unverifiable


def _log(conn, run_id, symbol, bar_date, check_name, severity, detail: dict) -> None:
    conn.execute(
        "INSERT INTO data_quality_log (ts, run_id, symbol, bar_date, check_name, severity, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (_now(), run_id, symbol, bar_date, check_name, severity, json.dumps(detail, sort_keys=True)),
    )


def _open_split_block_exists(conn: sqlite3.Connection, symbol: str) -> bool:
    q = (f"SELECT 1 FROM quality_quarantine WHERE symbol = ? AND scope = 'symbol' AND resolved_at IS NULL "
         f"AND check_name IN ({','.join('?' * len(SPLIT_CHECKS))}) LIMIT 1")
    return conn.execute(q, (symbol, *SPLIT_CHECKS)).fetchone() is not None


def _detect_and_record(conn: sqlite3.Connection, symbol: str, run_id: str | None) -> list[SplitFinding]:
    """Detect, log every finding, open at most one symbol block. Caller owns the transaction."""
    bars = _bars(conn, symbol)
    actions = _split_actions(conn, symbol)
    ca_found, unverifiable = _corp_action_findings(bars, actions)
    findings = _move_findings(bars, actions) + ca_found
    for a in unverifiable:
        _log(conn, run_id, symbol, a["ex_date"], CHECK_CA_RATIO_MISSING, "info",
             {"action": a["action"], "ratio": a["ratio"]})

    enforced = [f for f in findings if f.check_name in config.QUALITY_ENFORCED_RULES]
    block = "none"
    if enforced:
        if _open_split_block_exists(conn, symbol):
            block = "already_open"
        else:
            first = enforced[0]
            reason = {"bar_date": first.bar_date, "matched_ratio": first.matched_ratio,
                      "checks": sorted({f.check_name for f in enforced})}
            conn.execute(
                "INSERT INTO quality_quarantine (run_id, symbol, scope, check_name, reason, created_at) "
                "VALUES (?, ?, 'symbol', ?, ?, ?)",
                (run_id, symbol, first.check_name, json.dumps(reason, sort_keys=True), _now()),
            )
            block = "opened"
    elif findings:
        block = "not_enforced"

    for f in findings:
        _log(conn, run_id, symbol, f.bar_date, f.check_name, "warn", {
            "prev_date": f.prev_date, "prev_close": f.prev_close, "close": f.close, "raw_ratio": f.raw_ratio,
            "matched_ratio": f.matched_ratio, "evidence": list(f.evidence),
            "enforced": f.check_name in config.QUALITY_ENFORCED_RULES, "block": block,
        })
    return findings


def detect_unadjusted_split(
    conn: sqlite3.Connection, symbol: str, run_id: str | None = None
) -> list[SplitFinding]:
    """Run move_unadjusted_split and corp_action_unadjusted on the symbol's raw stored bars.

    Logs each finding (warn, prices in detail) and opens ONE open symbol-scope quarantine if an enforced check
    fired and none is open yet. Runs inside the caller's transaction if there is one, else in its own.
    """
    if conn.in_transaction:
        return _detect_and_record(conn, symbol, run_id)
    with transaction(conn):
        return _detect_and_record(conn, symbol, run_id)


def recheck_symbol(conn: sqlite3.Connection, symbol: str, run_id: str | None = None) -> RecheckResult:
    """Re-run split detection on the current stored history. Must be called inside the caller's transaction.

    Clean: resolve the symbol's open symbol-scope blocks from the split checks as 'recheck_clean'.
    Still detected: keep (or open, if enforced) the block and log. Always logs an info 'split_recheck' row.
    Raises RuntimeError outside a transaction, so the re-check can never half-apply.
    """
    if not conn.in_transaction:
        raise RuntimeError("recheck_symbol must run inside the caller's transaction")
    findings = _detect_and_record(conn, symbol, run_id)
    resolved = 0
    if not findings:
        resolved = conn.execute(
            f"UPDATE quality_quarantine SET resolved_at = ?, resolution = ? WHERE symbol = ? AND scope = 'symbol' "
            f"AND resolved_at IS NULL AND check_name IN ({','.join('?' * len(SPLIT_CHECKS))})",
            (_now(), RESOLUTION_RECHECK_CLEAN, symbol, *SPLIT_CHECKS),
        ).rowcount
    still = conn.execute("SELECT 1 FROM blocked_symbols WHERE symbol = ?", (symbol,)).fetchone() is not None
    _log(conn, run_id, symbol, None, CHECK_SPLIT_RECHECK, "info",
         {"findings": len(findings), "blocks_resolved": resolved, "still_blocked": still})
    return RecheckResult(symbol, tuple(findings), resolved, still)
