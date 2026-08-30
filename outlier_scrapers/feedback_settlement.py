"""Settlement import and closing-line value (CLV) for the feedback ledger."""

from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from outlier_scrapers import paths
from outlier_scrapers.feedback_capture import _read_csv, _selection_side
from outlier_scrapers.feedback_db import (
    DEFAULT_DB_PATH,
    FeedbackError,
    SETTLEMENT_DECISION_SELECT_SQL,
    SETTLEMENT_SNAPSHOT_SELECT_SQL,
    _connect,
    _float,
    _normal_result,
    _parse_utc,
    _stable_id,
    _text,
    _utc_now,
)
from outlier_scrapers.utils import _american_to_decimal

def compute_clv_line(selection: str, taken_line: Any, closing_line: Any) -> float | None:
    """Return positive line CLV when the selected side beat the closing number."""

    taken = _float(taken_line, field="taken_line")
    closing = _float(closing_line, field="closing_line")
    if taken is None or closing is None:
        return None
    selection_upper = selection.upper()
    if " UNDER " in f" {selection_upper} ":
        return taken - closing
    if " OVER " in f" {selection_upper} ":
        return closing - taken
    # Spread convention: +3 taken vs +2 close, or -2.5 taken vs -3 close,
    # is positive CLV for the selected team.
    return taken - closing


def compute_clv_price(taken_decimal: Any, closing_price: Any) -> float | None:
    taken = _float(taken_decimal, field="taken_decimal")
    closing_decimal = _american_to_decimal(closing_price)
    if taken is None or closing_decimal is None or taken <= 1.0 or closing_decimal <= 1.0:
        return None
    return taken / closing_decimal - 1.0


def find_distinct_closing_snapshot(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    outcome_id: str,
    market_id: str,
    selection: str,
    book: str,
    exclude_snapshot_id: str,
    after_captured_at: str,
) -> tuple[Any, Any]:
    """Best available *distinct, later* pre-start snapshot for CLV.

    Prefers same book + outcome_id, falls back to any book on the same
    event/outcome, then market_id + selection. ``exclude_snapshot_id`` is
    always excluded: a single capture is not its own close, and returning it
    as one silently manufactures zero CLV on every row that was only ever
    captured once. Excluding the taken snapshot is not enough on its own,
    though: without also requiring ``captured_at > after_captured_at``, an
    outcome whose *only* other history is an *older* quote would have that
    stale, pre-take price returned as the "close" -- fabricating CLV from a
    snapshot that isn't a close at all, just an earlier one. A close has to
    be a later observation, not merely a different one.
    """
    params_tail = (exclude_snapshot_id, after_captured_at)
    close = conn.execute(
        """
        SELECT line, price FROM market_snapshots
        WHERE event_id = ? AND outcome_id = ? AND book = ?
          AND snapshot_id != ? AND captured_at > ?
          AND captured_at <= event_starts_at
        ORDER BY captured_at DESC LIMIT 1
        """,
        (event_id, outcome_id, book, *params_tail),
    ).fetchone()
    if close:
        return close["line"], close["price"]

    close = conn.execute(
        """
        SELECT line, price FROM market_snapshots
        WHERE event_id = ? AND outcome_id = ?
          AND snapshot_id != ? AND captured_at > ?
          AND captured_at <= event_starts_at
        ORDER BY captured_at DESC LIMIT 1
        """,
        (event_id, outcome_id, *params_tail),
    ).fetchone()
    if close:
        return close["line"], close["price"]

    if market_id and selection:
        close = conn.execute(
            """
            SELECT line, price FROM market_snapshots
            WHERE event_id = ? AND market_id = ? AND selection = ?
              AND snapshot_id != ? AND captured_at > ?
              AND captured_at <= event_starts_at
            ORDER BY captured_at DESC LIMIT 1
            """,
            (event_id, market_id, selection, *params_tail),
        ).fetchone()
        if close:
            return close["line"], close["price"]
    return None, None


def closing_line_from_movement_export(
    *,
    sport: str,
    event_id: str,
    market_id: str,
    outcome_id: str,
    selection: str,
    event_starts_at: str,
    _export_cache: dict[str, list[tuple[datetime, dict, dict]]] | None = None,
) -> tuple[float | None, float | None]:
    """Best-effort closing line/price from the current line-movement export.

    The line-movement feed only keeps a single ``latest`` snapshot per league
    (no dated history), so this is only a genuine close when the export was
    generated at or after the event started; an earlier export is just
    another pregame quote and using it would repeat the zero-CLV bug it is
    meant to fix.
    """
    if not sport or not event_id or not market_id:
        return None, None
    starts = _parse_utc(event_starts_at)
    if starts is None:
        return None, None
    cache = _export_cache if _export_cache is not None else {}
    if sport not in cache:
        exports: list[tuple[datetime, dict, dict]] = []
        league_paths = paths.league_paths(sport)
        for filename in (f"{sport.lower()}_line_movement_latest.json", f"{sport.lower()}_games_line_movement_latest.json"):
            export_path = league_paths.normalized / filename
            if not export_path.exists():
                continue
            try:
                payload = json.loads(export_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            generated = _parse_utc(payload.get("generated_at"))
            if generated is None:
                continue
            by_outcome: dict[tuple[str, str, str], dict[str, Any]] = {}
            by_side: dict[tuple[str, str, str], dict[str, Any]] = {}
            for record in payload.get("records") or []:
                if not isinstance(record, dict):
                    continue
                event_key, market_key = _text(record.get("event_id")), _text(record.get("market_id"))
                outcome_key = _text(record.get("outcome_id"))
                if outcome_key:
                    by_outcome.setdefault((event_key, market_key, outcome_key), record)
                else:
                    side_key = _text(record.get("side")).upper()
                    if side_key:
                        by_side.setdefault((event_key, market_key, side_key), record)
            exports.append((generated, by_outcome, by_side))
        cache[sport] = exports
    side = _selection_side({"selection": selection})
    for generated, by_outcome, by_side in cache[sport]:
        if generated < starts:
            continue
        record = by_outcome.get((event_id, market_id, outcome_id)) if outcome_id else None
        if record is None and side:
            record = by_side.get((event_id, market_id, side))
        if record is None:
            continue
        try:
            line = _float(record.get("current_line"), field="current_line")
        except FeedbackError:
            line = None
        try:
            price = _float(record.get("current_odds"), field="current_odds")
        except FeedbackError:
            price = None
        if line is not None or price is not None:
            return line, price
    return None, None


def _find_distinct_closes_for_settlements(conn: sqlite3.Connection, rows: Sequence[sqlite3.Row]) -> dict[str, tuple[Any, Any]]:
    """Resolve all local closes with three indexed joins instead of N*3 selects."""
    if not rows:
        return {}
    conn.execute("DROP TABLE IF EXISTS temp.clv_recompute_targets")
    conn.execute("CREATE TEMP TABLE clv_recompute_targets (settlement_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, event_id TEXT NOT NULL, outcome_id TEXT NOT NULL, market_id TEXT NOT NULL, selection TEXT NOT NULL, book TEXT NOT NULL, captured_at TEXT NOT NULL)")
    try:
        conn.executemany("INSERT INTO clv_recompute_targets VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [(_text(r["settlement_id"]), _text(r["snapshot_id"]), _text(r["event_id"]), _text(r["outcome_id"]), _text(r["market_id"]), _text(r["selection"]), _text(r["book"]), _text(r["captured_at"])) for r in rows])
        closes = conn.execute(
            """
            WITH candidates AS (
                SELECT b.settlement_id, c.line, c.price, c.captured_at, 1 AS priority
                FROM clv_recompute_targets b JOIN market_snapshots c
                  ON c.event_id=b.event_id AND c.outcome_id=b.outcome_id AND c.book=b.book
                 AND c.captured_at>b.captured_at AND c.captured_at<=c.event_starts_at AND c.snapshot_id!=b.snapshot_id
                UNION ALL
                SELECT b.settlement_id, c.line, c.price, c.captured_at, 2 AS priority
                FROM clv_recompute_targets b JOIN market_snapshots c
                  ON c.event_id=b.event_id AND c.outcome_id=b.outcome_id
                 AND c.captured_at>b.captured_at AND c.captured_at<=c.event_starts_at AND c.snapshot_id!=b.snapshot_id
                UNION ALL
                SELECT b.settlement_id, c.line, c.price, c.captured_at, 3 AS priority
                FROM clv_recompute_targets b JOIN market_snapshots c
                  ON c.event_id=b.event_id AND c.market_id=b.market_id AND c.selection=b.selection
                 AND b.market_id!='' AND b.selection!='' AND c.captured_at>b.captured_at
                 AND c.captured_at<=c.event_starts_at AND c.snapshot_id!=b.snapshot_id
            ), ranked AS (
                SELECT settlement_id, line, price, ROW_NUMBER() OVER (PARTITION BY settlement_id ORDER BY priority, captured_at DESC) AS rank FROM candidates
            ) SELECT settlement_id, line, price FROM ranked WHERE rank=1
            """
        ).fetchall()
        return {_text(row[0]): (row[1], row[2]) for row in closes}
    finally:
        conn.execute("DROP TABLE IF EXISTS temp.clv_recompute_targets")


def recompute_settlement_clv(
    db_path: Path = DEFAULT_DB_PATH, *, dry_run: bool = False
) -> dict[str, int]:
    """Recompute CLV for settlements whose recorded close was actually the take.

    Historically ``_latest_local_close`` picked the *taken* snapshot as its
    own close whenever no second observation had ever been captured for that
    outcome, so ``clv_line``/``clv_price`` read as a false, precise 0.0
    instead of "unknown". A settlement is only touched when its stored
    ``closing_line``/``closing_price`` exactly match the matched snapshot's
    own ``line``/``price`` -- that match is the fingerprint the bug leaves
    behind. Each flagged row is re-resolved against a genuinely distinct
    local snapshot or, failing that, the line-movement export; if neither
    yields a real close the row is cleared to unknown rather than left wrong.
    """
    summary = {"inspected": 0, "corrected": 0, "cleared": 0, "unchanged": 0}
    conn = _connect(Path(db_path))
    try:
        rows = conn.execute(
            """
            SELECT t.settlement_id, t.snapshot_id, t.outcome_id,
                   t.closing_line, t.closing_price,
                   s.event_id, s.market_id, s.selection, s.line, s.price,
                   s.decimal_price, s.book, s.sport, s.event_starts_at,
                   s.captured_at
            FROM settlements t
            JOIN market_snapshots s ON s.snapshot_id = t.snapshot_id
            """
        ).fetchall()
        bogus_rows = []
        for row in rows:
            summary["inspected"] += 1
            bogus = (
                _text(row["closing_line"]) != ""
                and _text(row["closing_line"]) == _text(row["line"])
                and row["closing_price"] == row["price"]
            )
            if not bogus:
                summary["unchanged"] += 1
                continue
            bogus_rows.append(row)
        local_closes = _find_distinct_closes_for_settlements(conn, bogus_rows)
        export_cache: dict[str, list[tuple[datetime, dict, dict]]] = {}
        updates = []
        for row in bogus_rows:
            new_line, new_price = local_closes.get(_text(row["settlement_id"]), (None, None))
            if new_line is None and new_price is None:
                new_line, new_price = closing_line_from_movement_export(
                    sport=_text(row["sport"]),
                    event_id=_text(row["event_id"]),
                    market_id=_text(row["market_id"]),
                    outcome_id=_text(row["outcome_id"]),
                    selection=_text(row["selection"]),
                    event_starts_at=_text(row["event_starts_at"]),
                    _export_cache=export_cache,
                )

            if new_line is None and new_price is None:
                summary["cleared"] += 1
                closing_line, closing_price, clv_line, clv_price = None, None, None, None
            else:
                summary["corrected"] += 1
                closing_line, closing_price = new_line, new_price
                clv_line = compute_clv_line(_text(row["selection"]), row["line"], closing_line)
                clv_price = compute_clv_price(row["decimal_price"], closing_price)

            updates.append((closing_line, closing_price, clv_line, clv_price, row["settlement_id"]))
        if not dry_run:
            conn.executemany("UPDATE settlements SET closing_line=?, closing_price=?, clv_line=?, clv_price=? WHERE settlement_id=?", updates)
            conn.commit()
    finally:
        conn.close()
    return summary


def _is_play(final_verdict: Any, pipeline_verdict: Any, units: Any) -> bool:
    verdict = (_text(final_verdict) or _text(pipeline_verdict)).upper()
    return verdict in {"PLAY", "BET"} and (_float(units, field="units") or 0.0) > 0


def _computed_pnl(result: str, units: float, decimal_price: float | None, *, play: bool) -> float:
    if not play or units <= 0:
        return 0.0
    if result == "L":
        return -units
    if result == "PUSH":
        return 0.0
    if decimal_price is None:
        raise FeedbackError("pnl is blank and the matched snapshot has no decimal_price")
    return units * (decimal_price - 1.0)


def import_settlements(
    db_path_or_conn: Path | str | sqlite3.Connection,
    settlement_data: list[dict[str, Any]],
) -> dict[str, int]:
    now = _utc_now()
    summary = {
        "unmatched_count": 0,
        "ambiguous_count": 0,
        "duplicate_count": 0,
        "updated_count": 0,
    }
    connection_context = (
        nullcontext(db_path_or_conn)
        if isinstance(db_path_or_conn, sqlite3.Connection)
        else _connect(Path(db_path_or_conn))
    )
    with connection_context as conn:
        for row in settlement_data:
            result = _normal_result(row.get("win_loss_push") or "")

            decision_id = _text(row.get("decision_id"))
            snapshot_id = _text(row.get("snapshot_id"))
            outcome_id = _text(row.get("outcome_id"))

            sport = _text(row.get("sport"))
            event_id = _text(row.get("event_id"))
            market_id = _text(row.get("market_id")) or _text(row.get("market_family"))
            player_id = _text(row.get("player_id")) or _text(row.get("subject_id"))
            line = _text(row.get("line"))
            book = _text(row.get("book"))

            # Robust matching logic
            matches = []
            if decision_id:
                matches = conn.execute(SETTLEMENT_DECISION_SELECT_SQL, (decision_id,)).fetchall()
            elif snapshot_id:
                matches = conn.execute(SETTLEMENT_SNAPSHOT_SELECT_SQL, (snapshot_id,)).fetchall()
            else:
                # Match by durable identity
                query = """
                    SELECT d.*, s.event_id, s.market_id, s.outcome_id, s.selection, s.line,
                           s.decimal_price, s.sport, s.player_id, s.book
                    FROM decisions d
                    JOIN market_snapshots s ON s.snapshot_id = d.snapshot_id
                    WHERE COALESCE(s.sport, '') = CASE WHEN ? != '' THEN ? ELSE COALESCE(s.sport, '') END
                      AND s.event_id = ? AND s.market_id = ? 
                      AND COALESCE(s.outcome_id, '') = CASE WHEN ? != '' THEN ? ELSE COALESCE(s.outcome_id, '') END
                      AND COALESCE(s.player_id, '') = CASE WHEN ? != '' THEN ? ELSE COALESCE(s.player_id, '') END
                      AND COALESCE(s.line, '') = CASE WHEN ? != '' THEN ? ELSE COALESCE(s.line, '') END
                      AND COALESCE(s.book, '') = CASE WHEN ? != '' THEN ? ELSE COALESCE(s.book, '') END
                """
                matches = conn.execute(
                    query,
                    (
                        sport,
                        sport,
                        event_id,
                        market_id,
                        outcome_id,
                        outcome_id,
                        player_id,
                        player_id,
                        line,
                        line,
                        book,
                        book,
                    ),
                ).fetchall()

            if not matches:
                summary["unmatched_count"] += 1
                continue
            elif len(matches) > 1:
                summary["ambiguous_count"] += 1
                continue

            matched = matches[0]

            matched_decision_id = _text(matched["decision_id"])
            matched_snapshot_id = _text(matched["snapshot_id"])
            matched_outcome_id = _text(matched["outcome_id"])
            matched_event_id = _text(matched["event_id"])
            matched_market_id = _text(matched["market_id"])

            # Validate that provided row identity does not contradict matched identity
            contradicts = False
            if event_id and event_id != matched_event_id:
                contradicts = True
            if market_id and market_id != matched_market_id:
                contradicts = True
            if outcome_id and outcome_id != matched_outcome_id:
                contradicts = True

            if contradicts:
                summary["unmatched_count"] += 1
                continue

            settlement_id = _text(row.get("settlement_id")) or _stable_id(
                "settlement",
                matched_decision_id
                or matched_snapshot_id
                or (matched_event_id, matched_market_id, matched_outcome_id),
            )

            # Idempotent re-import check
            existing = conn.execute(
                "SELECT 1 FROM settlements WHERE settlement_id = ?", (settlement_id,)
            ).fetchone()
            if existing:
                summary["duplicate_count"] += 1
                continue

            clv_line = _float(row.get("clv_line"), field="clv_line")
            clv_price = _float(row.get("clv_price"), field="clv_price")
            pnl = _float(row.get("pnl"), field="pnl")
            closing_line = _text(row.get("closing_line"))
            closing_price = _float(row.get("closing_price"), field="closing_price")

            if clv_line is None:
                clv_line = compute_clv_line(
                    _text(matched["selection"]), matched["line"], closing_line
                )
            if clv_price is None:
                clv_price = compute_clv_price(matched["decimal_price"], closing_price)
            if pnl is None:
                units = _float(matched["units"], field="units") or 0.0
                pnl = _computed_pnl(
                    result,
                    units,
                    _float(matched["decimal_price"], field="decimal_price"),
                    play=_is_play(matched["final_verdict"], matched["pipeline_verdict"], units),
                )

            would_have = _normal_result(
                row.get("would_have_result") or result, field="would_have_result"
            )

            conn.execute(
                """
                INSERT INTO settlements (
                    settlement_id, decision_id, snapshot_id, outcome_id,
                    event_id, market_id, actual_result, win_loss_push,
                    closing_line, closing_price, clv_line, clv_price, pnl,
                    would_have_result, settled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    settlement_id,
                    matched_decision_id or None,
                    matched_snapshot_id or None,
                    matched_outcome_id,
                    matched_event_id,
                    matched_market_id,
                    _text(row.get("actual_result")),
                    result,
                    closing_line,
                    closing_price,
                    clv_line,
                    clv_price,
                    pnl,
                    would_have,
                    now,
                ),
            )
            summary["updated_count"] += 1

    return summary


def import_settlement_file(
    input_path: Path,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, int]:
    """Read and import one settlement CSV using the public CLI contract."""
    rows = _read_csv(Path(input_path), required=("win_loss_push",))
    return import_settlements(db_path, rows)


def import_settlement_inbox(
    inbox_dir: Path,
    db_path: Path = DEFAULT_DB_PATH,
    *,
    archive_dir: Path | None = None,
) -> dict[str, Any]:
    """Import pending settlement CSVs, archiving only fully matched files.

    Ambiguous or unmatched files stay in the inbox so an operator can repair
    identity fields without losing the original result feed.
    """
    inbox = Path(inbox_dir)
    archive = Path(archive_dir) if archive_dir else inbox.parent / "processed"
    summary: dict[str, Any] = {
        "file_count": 0,
        "processed_count": 0,
        "retained_count": 0,
        "unmatched_count": 0,
        "ambiguous_count": 0,
        "duplicate_count": 0,
        "updated_count": 0,
    }
    if not inbox.exists():
        return summary
    for input_path in sorted(inbox.glob("*.csv")):
        summary["file_count"] += 1
        stats = import_settlement_file(input_path, db_path)
        for key in ("unmatched_count", "ambiguous_count", "duplicate_count", "updated_count"):
            summary[key] += stats[key]
        if stats["unmatched_count"] or stats["ambiguous_count"]:
            summary["retained_count"] += 1
            continue
        archive.mkdir(parents=True, exist_ok=True)
        destination = archive / input_path.name
        if destination.exists():
            destination = (
                archive / f"{input_path.stem}-{_stable_id('file', input_path.resolve())[:12]}.csv"
            )
        input_path.replace(destination)
        summary["processed_count"] += 1
    return summary
