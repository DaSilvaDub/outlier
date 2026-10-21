"""Turn an NBA slate into SHADOW ledger rows and write them to the shared ledger (D3).

Every row is board ``SHADOW``, units 0, ``actionable=false``: Phase 1 only records
what the market looked like at decision time so evidence can accumulate. The
probability stored is the vig-free consensus itself (blend weight: market 1.0,
model 0.0); no model output exists yet.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from outlier_nba.ingest.outlier import OutcomeRecord
from outlier_nba.market.consensus import (
    DFS_BOOKS,
    BookQuote,
    Consensus,
    book_key,
    consensus_two_way,
)
from outlier_nba.market.devig import DEFAULT_METHOD, METHODS, american_to_prob
from outlier_nba.market.ladders import push_win_loss

logger = structlog.get_logger("outlier_nba.capture")

SHADOW_BOARD = "SHADOW"
BLEND_SOURCE = "nba_market_anchored_v0"
CSV_FIELDS = [
    "sport",
    "event_id",
    "market_id",
    "outcome_id",
    "player_id",
    "selection",
    "line",
    "book",
    "price",
    "market_consensus_prob",
    "final_blended_prob",
    "blend_market_weight",
    "blend_model_weight",
    "blend_weight_source",
    "push_prob",
    "edge",
    "data_quality_flags",
    "event_starts_at",
    "market_type",
    "board",
    "model_prob_source",
    "actionable",
    "recommended_units_pre_news",
    "as_of",
    "selected",
]
_MARKET_TYPE = {"PROP": "PLAYER_PROP", "GAME": "GAMELINE", "TEAM": "TEAM_PROP"}


@dataclass
class ShadowCapture:
    rows: list[dict[str, Any]] = field(default_factory=list)
    # snapshot-agnostic per-method consensus, keyed by outcome_id
    method_probs: dict[str, dict[str, float]] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)


def _fmt_line(line: float | None) -> str:
    if line is None:
        return "0"
    return str(int(line)) if float(line).is_integer() else f"{line:g}"


def orientation(rec: OutcomeRecord) -> tuple[float | None, str] | None:
    """``(threshold x, "over"|"under")``: every two-way outcome as a bet on a threshold.

    OVER/UNDER ``l`` -> ``(l, over/under)``; a HOME spread ``h`` is "home margin > -h"
    -> ``(-h, over)``; the AWAY ``a`` is "home margin < a" -> ``(a, under)``. Moneylines
    carry no threshold. Pairing on ``(market_id, x)`` then matches the two sides of
    totals, props and spreads alike, and one ladder orientation serves integer pushes.
    """
    if rec.market == "ML":
        return (None, "over" if rec.side == "HOME" else "under")
    if rec.line is None:
        return None
    if rec.side == "OVER":
        return rec.line, "over"
    if rec.side == "UNDER":
        return rec.line, "under"
    if rec.side == "HOME":
        return -rec.line, "over"
    if rec.side == "AWAY":
        return rec.line, "under"
    return None


def selection_text(rec: OutcomeRecord) -> str:
    """Selection string in the grammar ``outlier_scrapers.results`` can grade."""
    away, home = rec.away or "?", rec.home or "?"
    if rec.kind == "PROP":
        return f"{rec.player} - {rec.market} {rec.side} {_fmt_line(rec.line)}"
    if rec.kind == "TEAM":
        return f"{rec.team} Team Total {rec.side} {_fmt_line(rec.line)}"
    if rec.market == "TOTAL":
        return f"{away} @ {home} Total O/U {rec.side} {_fmt_line(rec.line)}"
    if rec.market == "SPREAD":
        return f"{away} @ {home} Spread {rec.side} {_fmt_line(rec.line)}"
    return f"{away} @ {home} Money Line {rec.side}"


def _group_rungs(
    records: Sequence[OutcomeRecord],
) -> dict[tuple[str, str], dict[float | None, dict[str, OutcomeRecord]]]:
    """market -> threshold -> {"over": rec, "under": rec}; first record wins a duplicate slot."""
    grouped: dict[tuple[str, str], dict[float | None, dict[str, OutcomeRecord]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for rec in records:
        key = orientation(rec)
        if key is None:
            continue
        x, side = key
        grouped[(rec.event_id, rec.market_id)][x].setdefault(side, rec)
    return grouped


def _rung_consensus(
    over: OutcomeRecord | None, under: OutcomeRecord | None, method: str
) -> Consensus | None:
    if over is None or under is None:
        return None
    over_by_book, under_by_book = dict(over.quotes), dict(under.quotes)
    quotes = [
        BookQuote(book, over_by_book[book], under_by_book[book])
        for book in over_by_book
        if book in under_by_book
    ]
    return consensus_two_way(quotes, method=method) if quotes else None


def _best_price(rec: OutcomeRecord) -> tuple[str, int] | None:
    best: tuple[str, int] | None = None
    best_dec = 0.0
    for book, odds in rec.quotes:
        if book_key(book) in DFS_BOOKS:  # flat pick'em payouts are not comparable prices
            continue
        prob = american_to_prob(odds)
        if prob is not None and 1.0 / prob > best_dec:
            best_dec, best = 1.0 / prob, (book, odds)
    return best


def build_shadow_capture(
    records: Sequence[OutcomeRecord], *, captured_at: datetime, method: str = DEFAULT_METHOD
) -> ShadowCapture:
    capture = ShadowCapture()
    stats = {
        "outcomes": 0,
        "two_sided": 0,
        "one_sided": 0,
        "thin_market": 0,
        "no_price": 0,
        "unorientable": sum(1 for r in records if orientation(r) is None),
    }
    for (_event, _market), rungs in _group_rungs(records).items():
        ladder: dict[float, float] = {}
        per_rung: dict[float | None, Consensus | None] = {}
        for x, pair in rungs.items():
            cons = _rung_consensus(pair.get("over"), pair.get("under"), method)
            per_rung[x] = cons
            if x is not None and cons is not None and cons.p_a is not None:
                ladder[x] = cons.p_a
        for x, pair in rungs.items():
            cons = per_rung[x]
            for side, rec in pair.items():
                stats["outcomes"] += 1
                capture.rows.append(
                    _row(rec, side, x, cons, ladder, pair, captured_at, method, stats)
                )
                if cons is not None and cons.p_a is not None:
                    capture.method_probs[rec.outcome_id] = _all_method_probs(rec, side, pair, cons)
    capture.summary = {"rows": len(capture.rows), "method": method, **stats}
    return capture


def _all_method_probs(
    rec: OutcomeRecord, side: str, pair: Mapping[str, OutcomeRecord], primary: Consensus
) -> dict[str, float]:
    out: dict[str, float] = {}
    for method in METHODS:
        cons = _rung_consensus(pair.get("over"), pair.get("under"), method)
        if cons is not None and cons.p_a is not None and cons.p_b is not None:
            out[method] = cons.p_a if side == "over" else cons.p_b
    return out


def _row(
    rec: OutcomeRecord,
    side: str,
    x: float | None,
    cons: Consensus | None,
    ladder: Mapping[float, float],
    pair: Mapping[str, OutcomeRecord],
    captured_at: datetime,
    method: str,
    stats: dict[str, int],
) -> dict[str, Any]:
    flags = [SHADOW_BOARD]
    if rec.primary is False:
        flags.append("ALT_LINE")
    p_side: float | None = None
    push: float | None = None
    if cons is None or cons.p_a is None:
        flags.append("ONE_SIDED_NO_DEVIG" if len(pair) < 2 else "NO_USABLE_BOOKS")
        stats["one_sided"] += 1
    else:
        stats["two_sided"] += 1
        p_side = cons.p_a if side == "over" else cons.p_b
        flags.extend(cons.flags)
        if cons.thin_market:
            stats["thin_market"] += 1
    if x is not None and float(x).is_integer():
        wlp = push_win_loss(x, ladder)
        if wlp is None:
            flags.append("PUSH_CAPABLE_NO_PROB")
        else:
            push = wlp[1]
    elif x is not None:
        push = 0.0
    best = _best_price(rec)
    if best is None:
        stats["no_price"] += 1
        flags.append("NO_PRICE")
    edge = None
    if p_side is not None and best is not None:
        implied = american_to_prob(best[1])
        edge = None if implied is None else round(p_side - implied, 6)
    return {
        "sport": "NBA",
        "event_id": rec.event_id,
        "market_id": rec.market_id,
        "outcome_id": rec.outcome_id,
        "player_id": rec.player_id or "",
        "selection": selection_text(rec),
        "line": _fmt_line(rec.line),
        "book": best[0] if best else "",
        "price": best[1] if best else "",
        "market_consensus_prob": "" if p_side is None else f"{p_side:.6f}",
        "final_blended_prob": "" if p_side is None else f"{p_side:.6f}",
        "blend_market_weight": 1.0,
        "blend_model_weight": 0.0,
        "blend_weight_source": BLEND_SOURCE,
        "push_prob": "" if push is None else f"{push:.6f}",
        "edge": "" if edge is None else edge,
        "data_quality_flags": ";".join(dict.fromkeys(flags)),
        "event_starts_at": rec.tip.isoformat(),
        "market_type": _MARKET_TYPE[rec.kind],
        "board": SHADOW_BOARD,
        "model_prob_source": f"nba_consensus:{method}",
        "actionable": "false",
        "recommended_units_pre_news": 0,
        "as_of": captured_at.isoformat(),
        "selected": "true",
    }


def rows_to_csv(rows: Sequence[Mapping[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


METHOD_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS nba_consensus_methods (
    snapshot_id TEXT NOT NULL,
    method TEXT NOT NULL,
    prob REAL NOT NULL,
    PRIMARY KEY (snapshot_id, method)
)
"""


def write_to_ledger(
    rows: Sequence[Mapping[str, Any]],
    method_probs: Mapping[str, Mapping[str, float]],
    *,
    db_path: Path,
    work_dir: Path,
    recorded_path: Path,
) -> dict[str, int]:
    """Persist SHADOW rows through the shared ``capture_pack`` (decisions stay STAND_DOWN)."""
    from outlier_scrapers import feedback
    from outlier_scrapers.feedback_capture import capture_pack

    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "candidates.csv").write_text(rows_to_csv(rows), encoding="utf-8")
    feedback.initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(METHOD_TABLE_SQL)
    stats = capture_pack(
        work_dir, db_path, recorded_pack_path=recorded_path, source_filename="candidates.csv"
    )
    try:
        link_methods(db_path, rows, method_probs)
    except sqlite3.Error as exc:  # auxiliary table: the snapshots are already committed
        logger.warning("nba_method_link_failed", error=str(exc))
    return {"snapshots": stats.snapshots, "decisions": stats.decisions}


def link_methods(
    db_path: Path,
    rows: Sequence[Mapping[str, Any]],
    method_probs: Mapping[str, Mapping[str, float]],
) -> None:
    """Store every devig method's probability beside the snapshot (method choice is OOS later)."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(METHOD_TABLE_SQL)
        for row in rows:
            probs = method_probs.get(str(row["outcome_id"]))
            if not probs:
                continue
            snap = conn.execute(
                "SELECT snapshot_id FROM market_snapshots WHERE sport='NBA' AND event_id=? "
                "AND outcome_id=? AND captured_at=? ORDER BY created_at DESC LIMIT 1",
                (row["event_id"], row["outcome_id"], row["as_of"]),
            ).fetchone()
            if snap is None:
                continue
            conn.executemany(
                "INSERT OR REPLACE INTO nba_consensus_methods VALUES (?, ?, ?)",
                [(snap[0], m, p) for m, p in probs.items()],
            )


def capture_summary_json(capture: ShadowCapture, extra: Mapping[str, Any]) -> str:
    return json.dumps({**capture.summary, **dict(extra)}, indent=2, sort_keys=True, default=str)
