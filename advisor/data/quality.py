"""Data-quality checks on RAW prices_daily rows (Phase 1): unadjusted split/bonus events, split-like price
patterns, and the two bar-count corruption blocks.

Severity follows the approved scheme: these are per-symbol problems, so every finding logs `warn` (never
`error`) and the run continues. A rule only blocks or excludes anything if its name is in
`config.QUALITY_ENFORCED_RULES` (empty until the dry-run report is approved); otherwise the finding is logged
with outcome `not_enforced`. No row of prices_daily is ever changed or deleted here: exclusions are rows in
quality_quarantine, applied at read time by the prices_clean view.

Rules (names as used in QUALITY_ENFORCED_RULES, data_quality_log and quality_quarantine.check_name):
  corp_action_unadjusted  THE ONLY unadjusted-split evidence: a split/bonus row in corporate_actions whose ratio
                          the stored prices STILL jump by. For a row with ex-date E, let j0 be the first stored
                          bar on or after E (or one past the last bar). Every jump position j (bar j-1 -> bar j)
                          with |j - j0| <= EVENT_JUMP_WINDOW_SESSIONS is tested: close[j-1] / close[j] must match
                          the recorded ratio within SPLIT_RATIO_TOL_PCT. The match closest to j0 wins (earlier on
                          a tie); bar j is the REAL jump bar. The ratio is informational: compared with prices
                          as evidence, never used to adjust them. First detection opens ONE symbol block per
                          symbol. If the event is still unadjusted when re-checked right after a history
                          replacement that followed an earlier detection of the same event (the vendor's own
                          history is unadjusted, so a re-fetch cannot fix it), the block is converted into a
                          'span' quarantine from the first stored bar to the LAST BAR BEFORE THE REAL JUMP BAR
                          (no buffer: every later bar is on the correct basis), and the symbol block is resolved
                          as 'converted_to_span'. A detected jump always has a bar before it, so the span is
                          never empty; if it ever were, the block is resolved with no span row and logged.
  move_unclassified       a split-like price pattern (close-to-close move above MOVE_LARGE_PCT whose ratio
                          matches SPLIT_RATIOS) whose bar is not the real jump bar of a corp_action_unadjusted
                          finding (an event within the window explains it). Never blocks:
                          an 'entry' quarantine for that one day (the bar stays visible). Decided from bars t-1
                          and t only, so it is known at the close of day t (no look-ahead).
  nonpositive_recurring   >= NONPOSITIVE_BLOCK_COUNT open 'bar' quarantines from price_nonpositive within the
                          symbol's most recent NONPOSITIVE_BLOCK_WINDOW_SESSIONS stored sessions.
  bad_bar_cluster         >= BAD_BAR_CLUSTER_LIMIT open 'bar' quarantines from ohlc_inconsistent, move_bad_print,
                          volume_zero_price_moved or price_nonpositive within ANY BAD_BAR_CLUSTER_WINDOW_SESSIONS
                          consecutive stored sessions (rolling over the whole history).
The last two count only OPEN rows of scope 'bar' from those checks; resolved rows, 'entry' and 'span' scope, gap
and zero-volume runs, bar_nan_volume and flat_price_run never count. At most one open block per symbol per rule.
Volume is NOT split evidence (live Yahoo data: no volume shift at known unadjusted splits).

Sessions are counted in the symbol's stored bars (trading_calendar is not consulted yet).
"""
from __future__ import annotations

import bisect
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from advisor import config
from advisor.data.db import transaction

CHECK_CA_UNADJUSTED = "corp_action_unadjusted"
SPLIT_CHECKS = (CHECK_CA_UNADJUSTED,)
CHECK_MOVE_UNCLASSIFIED = "move_unclassified"
CHECK_CA_RATIO_MISSING = "corp_action_ratio_missing"
CHECK_SPLIT_RECHECK = "split_recheck"
RESOLUTION_RECHECK_CLEAN = "recheck_clean"
RESOLUTION_CONVERTED_TO_SPAN = "converted_to_span"
SPAN_REASON_NOTE = "re-fetch could not fix the unadjusted event (vendor history is unadjusted)"

CHECK_NONPOSITIVE_RECURRING = "nonpositive_recurring"
CHECK_BAD_BAR_CLUSTER = "bad_bar_cluster"
BAR_BLOCK_CHECKS = (CHECK_NONPOSITIVE_RECURRING, CHECK_BAD_BAR_CLUSTER)
# Bar-quarantine check names each rule counts.
NONPOSITIVE_SOURCE_CHECKS = ("price_nonpositive",)
BAD_BAR_SOURCE_CHECKS = ("ohlc_inconsistent", "move_bad_print", "volume_zero_price_moved", "price_nonpositive")


@dataclass(frozen=True)
class SplitFinding:
    """A split-like price jump. Prices in INR; raw_ratio = prev_close / close (price divisor). For
    corp_action_unadjusted `ex_date` is the event row's date; for move_unclassified it is None."""

    check_name: str
    ex_date: str | None
    bar_date: str
    prev_date: str
    prev_close: float
    close: float
    raw_ratio: float
    matched_ratio: float
    outcome: str = ""


@dataclass(frozen=True)
class BarBlockFinding:
    """A bar-count block rule whose trigger is met: `count` open bar quarantines between the two dates."""

    check_name: str
    count: int
    from_date: str
    to_date: str


@dataclass(frozen=True)
class RecheckResult:
    """Outcome of recheck_symbol. `findings` = corp_action_unadjusted findings (with outcome); `bar_blocks` =
    bar-count rules still triggered; `blocks_resolved` counts blocks/spans resolved as recheck_clean or
    converted_to_span; `spans_opened` = unfixable-event spans created; `still_blocked` = the symbol is in
    blocked_symbols (any check)."""

    symbol: str
    findings: tuple[SplitFinding, ...]
    blocks_resolved: int
    still_blocked: bool
    bar_blocks: tuple[BarBlockFinding, ...] = ()
    spans_opened: int = 0


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


def _log(conn, run_id, symbol, bar_date, check_name, severity, detail: dict) -> None:
    conn.execute(
        "INSERT INTO data_quality_log (ts, run_id, symbol, bar_date, check_name, severity, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (_now(), run_id, symbol, bar_date, check_name, severity, json.dumps(detail, sort_keys=True)),
    )


def _finding_detail(f: SplitFinding, enforced: bool, outcome: str) -> dict:
    return {"ex_date": f.ex_date, "prev_date": f.prev_date, "prev_close": f.prev_close, "close": f.close,
            "raw_ratio": f.raw_ratio, "matched_ratio": f.matched_ratio, "enforced": enforced, "outcome": outcome}


# --- detection (pure, no writes) ------------------------------------------------------------------------------
def _corp_action_findings(
    bars: list[sqlite3.Row], actions: list[sqlite3.Row]
) -> tuple[list[SplitFinding], list[sqlite3.Row]]:
    """For every split/bonus row: do the raw closes across the ex-date still jump by the recorded ratio?

    Returns (findings, rows that could not be checked because the ratio is missing or meaningless).
    """
    found: list[SplitFinding] = []
    unverifiable: list[sqlite3.Row] = []
    for a in actions:
        ratio = a["ratio"]
        if ratio is None or ratio <= 0 or _within(ratio, 1.0, config.SPLIT_RATIO_TOL_PCT):
            unverifiable.append(a)
            continue
        j0 = _first_index_on_or_after(bars, a["ex_date"])
        if j0 is None:
            j0 = len(bars)
        w = config.EVENT_JUMP_WINDOW_SESSIONS
        candidates = sorted(range(max(1, j0 - w), min(len(bars), j0 + w + 1)), key=lambda j: (abs(j - j0), j))
        for j in candidates:  # closest to the recorded ex-date first
            prev, cur = bars[j - 1], bars[j]
            if prev["close"] <= 0 or cur["close"] <= 0:
                continue  # non-positive prices are a different check
            raw = prev["close"] / cur["close"]
            if _within(raw, ratio, config.SPLIT_RATIO_TOL_PCT):
                found.append(SplitFinding(CHECK_CA_UNADJUSTED, a["ex_date"], cur["date"], prev["date"],
                                          prev["close"], cur["close"], raw, ratio))
                break
    return found, unverifiable


def _pattern_moves(bars: list[sqlite3.Row], skip_dates: set[str]) -> list[SplitFinding]:
    """Split-like moves: |close_t / close_t-1 - 1| > MOVE_LARGE_PCT and the ratio matches SPLIT_RATIOS.
    Uses bars t-1 and t only. Bars already explained by a corp_action_unadjusted finding are skipped."""
    out: list[SplitFinding] = []
    for i in range(1, len(bars)):
        prev, cur = bars[i - 1], bars[i]
        if cur["date"] in skip_dates or prev["close"] <= 0 or cur["close"] <= 0:
            continue
        if abs(cur["close"] / prev["close"] - 1.0) * 100.0 <= config.MOVE_LARGE_PCT:
            continue
        raw = prev["close"] / cur["close"]
        matched = _match_split_ratio(raw)
        if matched is not None:
            out.append(SplitFinding(CHECK_MOVE_UNCLASSIFIED, None, cur["date"], prev["date"], prev["close"],
                                    cur["close"], raw, matched))
    return out


# --- quarantine bookkeeping -----------------------------------------------------------------------------------
def _reason(row: sqlite3.Row) -> dict:
    try:
        return json.loads(row["reason"]) if row["reason"] else {}
    except (TypeError, ValueError):
        return {}


def _ca_rows(conn: sqlite3.Connection, symbol: str, scope: str, open_only: bool) -> list[sqlite3.Row]:
    q = ("SELECT id, reason, resolved_at FROM quality_quarantine WHERE symbol = ? AND check_name = ? AND scope = ?"
         + (" AND resolved_at IS NULL" if open_only else ""))
    return conn.execute(q, (symbol, CHECK_CA_UNADJUSTED, scope)).fetchall()


def _event_seen_before(conn: sqlite3.Connection, symbol: str, ex_date: str) -> bool:
    """Has this event been recorded in quality_quarantine before (block or span, open or resolved)?"""
    for scope in ("symbol", "span"):
        for r in _ca_rows(conn, symbol, scope, open_only=False):
            rs = _reason(r)
            if ex_date in rs.get("ex_dates", []) or rs.get("ex_date") == ex_date:
                return True
    return False


def _open_span_for(conn: sqlite3.Connection, symbol: str, ex_date: str) -> bool:
    return any(_reason(r).get("ex_date") == ex_date for r in _ca_rows(conn, symbol, "span", open_only=True))


def _ensure_block(conn, symbol: str, run_id: str | None, ex_date: str) -> str:
    """Open the symbol's single corp_action_unadjusted block, or add the event to the open one."""
    rows = _ca_rows(conn, symbol, "symbol", open_only=True)
    if rows:
        row, rs = rows[0], _reason(rows[0])
        ex_dates = set(rs.get("ex_dates", []))
        if ex_date not in ex_dates:
            rs["ex_dates"] = sorted(ex_dates | {ex_date})
            conn.execute("UPDATE quality_quarantine SET reason = ? WHERE id = ?",
                         (json.dumps(rs, sort_keys=True), row["id"]))
        return "block_already_open"
    reason = {"ex_dates": [ex_date]}
    conn.execute(
        "INSERT INTO quality_quarantine (run_id, symbol, scope, check_name, reason, created_at) "
        "VALUES (?, ?, 'symbol', ?, ?, ?)",
        (run_id, symbol, CHECK_CA_UNADJUSTED, json.dumps(reason, sort_keys=True), _now()),
    )
    return "block_opened"


def _open_span(conn, symbol: str, run_id: str | None, bars: list[sqlite3.Row], f: SplitFinding) -> bool:
    """Span from the first stored bar to the last bar before the REAL jump bar (f.bar_date). Returns False and
    writes nothing if no bar precedes the jump (nothing on the old basis to hide)."""
    j = _first_index_on_or_after(bars, f.bar_date)
    if j is None or j == 0:
        return False
    reason = {"ex_date": f.ex_date, "jump_date": f.bar_date, "ratio": f.matched_ratio, "note": SPAN_REASON_NOTE}
    conn.execute(
        "INSERT INTO quality_quarantine (run_id, symbol, scope, from_date, to_date, check_name, reason, created_at) "
        "VALUES (?, ?, 'span', ?, ?, ?, ?, ?)",
        (run_id, symbol, bars[0]["date"], bars[j - 1]["date"], CHECK_CA_UNADJUSTED,
         json.dumps(reason, sort_keys=True), _now()),
    )
    return True


def _record_entry(conn, symbol: str, run_id: str | None, f: SplitFinding) -> str:
    if CHECK_MOVE_UNCLASSIFIED not in config.QUALITY_ENFORCED_RULES:
        return "not_enforced"
    exists = conn.execute(
        "SELECT 1 FROM quality_quarantine WHERE symbol = ? AND scope = 'entry' AND check_name = ? "
        "AND from_date = ? AND resolved_at IS NULL", (symbol, CHECK_MOVE_UNCLASSIFIED, f.bar_date)).fetchone()
    if exists:
        return "entry_already_open"
    conn.execute(
        "INSERT INTO quality_quarantine (run_id, symbol, scope, from_date, to_date, check_name, reason, created_at) "
        "VALUES (?, ?, 'entry', ?, ?, ?, ?, ?)",
        (run_id, symbol, f.bar_date, f.bar_date, CHECK_MOVE_UNCLASSIFIED,
         json.dumps({"raw_ratio": f.raw_ratio, "matched_ratio": f.matched_ratio}, sort_keys=True), _now()),
    )
    return "entry_opened"


def _split_stage(
    conn: sqlite3.Connection, symbol: str, run_id: str | None, after_replacement: bool
) -> tuple[list[SplitFinding], int, int]:
    """Detect and record split findings. Returns (corp_action_unadjusted findings with outcome, spans opened,
    blocks resolved as converted_to_span). Caller owns the transaction."""
    bars = _bars(conn, symbol)
    actions = _split_actions(conn, symbol)
    ca_found, unverifiable = _corp_action_findings(bars, actions)
    for a in unverifiable:
        _log(conn, run_id, symbol, a["ex_date"], CHECK_CA_RATIO_MISSING, "info",
             {"action": a["action"], "ratio": a["ratio"]})

    for f in _pattern_moves(bars, {f.bar_date for f in ca_found}):
        outcome = _record_entry(conn, symbol, run_id, f)
        _log(conn, run_id, symbol, f.bar_date, f.check_name, "warn",
             _finding_detail(f, CHECK_MOVE_UNCLASSIFIED in config.QUALITY_ENFORCED_RULES, outcome))

    enforced = CHECK_CA_UNADJUSTED in config.QUALITY_ENFORCED_RULES
    out: list[SplitFinding] = []
    spans = 0
    for f in ca_found:
        if not enforced:
            outcome = "not_enforced"
        elif _open_span_for(conn, symbol, f.ex_date):
            outcome = "span_open"
        else:
            if after_replacement and _event_seen_before(conn, symbol, f.ex_date):
                if _open_span(conn, symbol, run_id, bars, f):
                    outcome, spans = "converted_to_span", spans + 1
                else:
                    outcome = "converted_no_span"  # nothing precedes the jump: nothing to hide
            else:
                outcome = _ensure_block(conn, symbol, run_id, f.ex_date)
        _log(conn, run_id, symbol, f.bar_date, f.check_name, "warn", _finding_detail(f, enforced, outcome))
        out.append(SplitFinding(**{**f.__dict__, "outcome": outcome}))

    converted = 0
    handled = ("span_open", "converted_to_span", "converted_no_span")
    if enforced and out and all(f.outcome in handled for f in out):
        converted = conn.execute(
            "UPDATE quality_quarantine SET resolved_at = ?, resolution = ? WHERE symbol = ? AND scope = 'symbol' "
            "AND check_name = ? AND resolved_at IS NULL",
            (_now(), RESOLUTION_CONVERTED_TO_SPAN, symbol, CHECK_CA_UNADJUSTED),
        ).rowcount
    return out, spans, converted


# --- bar-count blocks -----------------------------------------------------------------------------------------
def _open_bar_quarantine_positions(
    conn: sqlite3.Connection, symbol: str, dates: list[str], source_checks: tuple[str, ...]
) -> list[tuple[int, str]]:
    """(stored-bar index, date) of each OPEN scope-'bar' quarantine from `source_checks`, sorted, one per date.

    A quarantine date with no stored bar is placed where it would sort (bisect)."""
    q = (f"SELECT DISTINCT from_date FROM quality_quarantine WHERE symbol = ? AND scope = 'bar' "
         f"AND resolved_at IS NULL AND check_name IN ({','.join('?' * len(source_checks))}) ORDER BY from_date")
    rows = conn.execute(q, (symbol, *source_checks)).fetchall()
    return [(bisect.bisect_left(dates, r[0]), r[0]) for r in rows]


def _bar_block_findings(conn: sqlite3.Connection, symbol: str) -> list[BarBlockFinding]:
    dates = [r[0] for r in conn.execute("SELECT date FROM prices_daily WHERE symbol = ? ORDER BY date", (symbol,))]
    out: list[BarBlockFinding] = []

    pos = _open_bar_quarantine_positions(conn, symbol, dates, NONPOSITIVE_SOURCE_CHECKS)
    cutoff = len(dates) - config.NONPOSITIVE_BLOCK_WINDOW_SESSIONS  # index of the Nth most recent stored bar
    recent = [p for p in pos if p[0] >= cutoff]
    if len(recent) >= config.NONPOSITIVE_BLOCK_COUNT:
        out.append(BarBlockFinding(CHECK_NONPOSITIVE_RECURRING, len(recent), recent[0][1], recent[-1][1]))

    pos = _open_bar_quarantine_positions(conn, symbol, dates, BAD_BAR_SOURCE_CHECKS)
    k, w = config.BAD_BAR_CLUSTER_LIMIT, config.BAD_BAR_CLUSTER_WINDOW_SESSIONS
    best: tuple[int, int] | None = None  # (start, end) positions of the densest qualifying window
    for s in range(len(pos)):
        e = s
        while e + 1 < len(pos) and pos[e + 1][0] - pos[s][0] < w:
            e += 1
        if e - s + 1 >= k and (best is None or e - s > best[1] - best[0]):
            best = (s, e)
    if best is not None:
        s, e = best
        out.append(BarBlockFinding(CHECK_BAD_BAR_CLUSTER, e - s + 1, pos[s][1], pos[e][1]))
    return out


def _open_block_exists(conn: sqlite3.Connection, symbol: str, check_name: str) -> bool:
    q = ("SELECT 1 FROM quality_quarantine WHERE symbol = ? AND scope = 'symbol' AND resolved_at IS NULL "
         "AND check_name = ? LIMIT 1")
    return conn.execute(q, (symbol, check_name)).fetchone() is not None


def _evaluate_bar_blocks(conn: sqlite3.Connection, symbol: str, run_id: str | None) -> list[BarBlockFinding]:
    """Evaluate both bar-count rules, log each triggered one (warn), open at most one block per rule if enforced.
    Caller owns the transaction."""
    findings = _bar_block_findings(conn, symbol)
    for f in findings:
        enforced = f.check_name in config.QUALITY_ENFORCED_RULES
        if not enforced:
            block = "not_enforced"
        elif _open_block_exists(conn, symbol, f.check_name):
            block = "already_open"
        else:
            reason = {"count": f.count, "from_date": f.from_date, "to_date": f.to_date}
            conn.execute(
                "INSERT INTO quality_quarantine (run_id, symbol, scope, check_name, reason, created_at) "
                "VALUES (?, ?, 'symbol', ?, ?, ?)",
                (run_id, symbol, f.check_name, json.dumps(reason, sort_keys=True), _now()),
            )
            block = "opened"
        _log(conn, run_id, symbol, f.to_date, f.check_name, "warn", {
            "count": f.count, "from_date": f.from_date, "to_date": f.to_date, "enforced": enforced, "block": block,
        })
    return findings


# --- public API -----------------------------------------------------------------------------------------------
def detect_bar_blocks(conn: sqlite3.Connection, symbol: str, run_id: str | None = None) -> list[BarBlockFinding]:
    """Run nonpositive_recurring and bad_bar_cluster on the symbol's open bar quarantines.

    Logs each triggered rule (warn) and opens at most one symbol block per rule if the rule is enforced. Never
    resolves anything (recheck_symbol does). Uses the caller's transaction if there is one, else its own.
    """
    if conn.in_transaction:
        return _evaluate_bar_blocks(conn, symbol, run_id)
    with transaction(conn):
        return _evaluate_bar_blocks(conn, symbol, run_id)


def detect_unadjusted_split(
    conn: sqlite3.Connection, symbol: str, run_id: str | None = None
) -> list[SplitFinding]:
    """Run corp_action_unadjusted and the move_unclassified price-pattern rule on the symbol's raw bars.

    Returns the corp_action_unadjusted findings (each with its outcome). Logs every finding (warn, prices in
    detail). A first detection opens the symbol block; an event already handled by an open span stays a span.
    Never converts a block to a span by itself (only recheck_symbol right after a replacement does). Runs
    inside the caller's transaction if there is one, else in its own.
    """
    if conn.in_transaction:
        return _split_stage(conn, symbol, run_id, after_replacement=False)[0]
    with transaction(conn):
        return _split_stage(conn, symbol, run_id, after_replacement=False)[0]


def recheck_symbol(
    conn: sqlite3.Connection, symbol: str, run_id: str | None = None, *, after_replacement: bool = False
) -> RecheckResult:
    """Re-run every rule in this module on the current stored state. Must be called inside the caller's
    transaction (raises RuntimeError otherwise, so the re-check can never half-apply).

    `after_replacement=True` (passed by prices.replace_symbol_history) means the history was just re-fetched:
    an event that was detected before and is STILL unadjusted is converted from a symbol block to a span.
    No corp_action_unadjusted finding: the symbol's open corp_action_unadjusted blocks and spans are resolved
    as 'recheck_clean'. Each bar-count rule whose trigger no longer holds: its open block is resolved as
    'recheck_clean'. Blocks from any other check are never touched. Always logs an info 'split_recheck' row.
    """
    if not conn.in_transaction:
        raise RuntimeError("recheck_symbol must run inside the caller's transaction")
    findings, spans, converted = _split_stage(conn, symbol, run_id, after_replacement)
    bar_blocks = _evaluate_bar_blocks(conn, symbol, run_id)
    resolved = converted
    now = _now()
    if not findings:
        resolved += conn.execute(
            "UPDATE quality_quarantine SET resolved_at = ?, resolution = ? WHERE symbol = ? "
            "AND scope IN ('symbol','span') AND check_name = ? AND resolved_at IS NULL",
            (now, RESOLUTION_RECHECK_CLEAN, symbol, CHECK_CA_UNADJUSTED),
        ).rowcount
    triggered = {f.check_name for f in bar_blocks}
    clear = [c for c in BAR_BLOCK_CHECKS if c not in triggered]
    if clear:
        resolved += conn.execute(
            f"UPDATE quality_quarantine SET resolved_at = ?, resolution = ? WHERE symbol = ? AND scope = 'symbol' "
            f"AND resolved_at IS NULL AND check_name IN ({','.join('?' * len(clear))})",
            (now, RESOLUTION_RECHECK_CLEAN, symbol, *clear),
        ).rowcount
    still = conn.execute("SELECT 1 FROM blocked_symbols WHERE symbol = ?", (symbol,)).fetchone() is not None
    _log(conn, run_id, symbol, None, CHECK_SPLIT_RECHECK, "info", {
        "after_replacement": after_replacement, "findings": [f.outcome for f in findings],
        "bar_blocks": sorted(triggered), "spans_opened": spans, "blocks_resolved": resolved, "still_blocked": still,
    })
    return RecheckResult(symbol, tuple(findings), resolved, still, tuple(bar_blocks), spans)
