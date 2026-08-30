"""Pack capture, snapshot identity, and decision import for the feedback ledger."""

from __future__ import annotations

import csv
import json
import sqlite3
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from outlier_scrapers import probability_blend
from outlier_scrapers.feedback_db import (
    DEFAULT_DB_PATH,
    DECISION_FIELDS,
    FeedbackError,
    MARKET_SNAPSHOT_FIELDS,
    SELECT_DECISION_BY_ID_SQL,
    _append_flag,
    _coalesce,
    _connect,
    _float,
    _probability,
    _stable_id,
    _text,
    _timestamp_extreme,
    _truthy,
    _utc_now,
)
from outlier_scrapers.utils import _american_to_decimal, _write_csv

@dataclass(frozen=True)
class CaptureStats:
    snapshots: int
    decisions: int


@dataclass(frozen=True)
class ImportStats:
    imported: int
    unlinked: int = 0


def _validate_decision_snapshot_identities(conn: sqlite3.Connection) -> None:
    invalid_decision = conn.execute(
        """
        SELECT snapshot_id
        FROM decisions
        WHERE decision_id IS NULL
           OR TRIM(CAST(decision_id AS TEXT)) = ''
        ORDER BY snapshot_id
        LIMIT 1
        """
    ).fetchone()
    if invalid_decision is not None:
        raise FeedbackError(
            "Cannot safely migrate decisions: blank or missing decision_id "
            f"for snapshot {_text(invalid_decision[0])!r}"
        )

    invalid = conn.execute(
        """
        SELECT d.decision_id
        FROM decisions d
        LEFT JOIN market_snapshots s ON s.snapshot_id = d.snapshot_id
        WHERE d.snapshot_id IS NULL
           OR TRIM(CAST(d.snapshot_id AS TEXT)) = ''
           OR s.snapshot_id IS NULL
        ORDER BY d.decision_id
        LIMIT 1
        """
    ).fetchone()
    if invalid is not None:
        raise FeedbackError(
            "Cannot safely migrate decisions: blank, missing, or unknown snapshot_id "
            f"for decision {_text(invalid[0])!r}"
        )

def _read_csv(path: Path, required: Iterable[str] = ()) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        missing = [field for field in required if field not in fieldnames]
        if missing:
            raise FeedbackError(f"{path} is missing required columns: {', '.join(missing)}")
        return list(reader)


def _pack_fallback_timestamp(pack_dir: Path) -> str:
    manifest = pack_dir / "manifest.json"
    if manifest.exists():
        try:
            timestamp = json.loads(manifest.read_text(encoding="utf-8")).get("timestamp")
            if timestamp:
                return str(timestamp)
        except (OSError, json.JSONDecodeError):
            pass
    return _utc_now()


def _selection_side(row: dict[str, Any]) -> str:
    explicit = _text(row.get("best_side")).upper()
    if explicit in {"OVER", "UNDER"}:
        return explicit
    selection = f" {_text(row.get('selection')).upper()} "
    if " UNDER " in selection:
        return "UNDER"
    if " OVER " in selection:
        return "OVER"
    return ""


def _total_representation_key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        _text(row.get("sport")),
        _text(row.get("event_id")),
        _text(row.get("market_id")),
        _text(row.get("line")),
        _selection_side(row),
    )


def _load_pack_rows(pack_dir: Path) -> list[tuple[str, dict[str, Any]]]:
    opportunities_path = pack_dir / "opportunities.csv"
    candidates_path = pack_dir / "candidates.csv"
    base_source = "opportunities" if opportunities_path.exists() else "candidates"
    base_rows = _read_csv(opportunities_path if opportunities_path.exists() else candidates_path)
    if base_source == "candidates":
        for row in base_rows:
            row["selected"] = "true"
    event_starts_by_id: dict[str, str] = {}
    for row in base_rows:
        event_id = _text(row.get("event_id"))
        event_start = _text(row.get("_event_starts_at") or row.get("event_starts_at"))
        if event_id and event_start:
            event_starts_by_id.setdefault(event_id, event_start)

    specialized: list[tuple[str, dict[str, Any]]] = []
    for filename, source in (
        ("game_totals.csv", "game_totals"),
        ("team_totals.csv", "team_totals"),
    ):
        for row in _read_csv(pack_dir / filename):
            row["selected"] = "true"
            if not _text(row.get("_event_starts_at") or row.get("event_starts_at")):
                row["_event_starts_at"] = event_starts_by_id.get(
                    _text(row.get("event_id")), ""
                )
            specialized.append((source, row))

    for row in _read_csv(pack_dir / "ultimate_alt.csv"):
        row["selected"] = (
            "true" if _text(row.get("shadow_status")).upper() == "QUALIFIED" else "false"
        )
        specialized.append(("ultimate_alt", row))

    specialized_keys = {
        _total_representation_key(row)
        for source, row in specialized
        if source in {"game_totals", "team_totals"}
    }
    output: list[tuple[str, dict[str, Any]]] = []
    for row in base_rows:
        # A specialized totals ledger is the authoritative representation for a
        # selected total.  Keep non-selected raw opportunities for auditability.
        if _truthy(row.get("selected")) and _total_representation_key(row) in specialized_keys:
            continue
        output.append((base_source, row))
    output.extend(specialized)
    return output


def _signal_fields(
    row: dict[str, Any],
) -> tuple[str, float | None, float | None, float | None, float | None]:
    hit = _float(row.get("hit_rate_component"), field="hit_rate_component")
    insight = _float(row.get("insight_component"), field="insight_component")
    movement = _float(row.get("movement_component"), field="movement_component")
    orf = _float(row.get("orf_component"), field="orf_component")
    flags = _text(row.get("signal_flags"))
    return flags, hit, insight, movement, orf


def _snapshot_from_pack_row(
    source: str,
    row: dict[str, Any],
    pack_dir: Path,
    fallback_timestamp: str,
    recorded_pack_path: Path | None = None,
) -> dict[str, Any]:
    is_totals = source in {"game_totals", "team_totals"}
    is_ultimate_alt = source == "ultimate_alt"
    event_id = _text(row.get("event_id"))
    market_id = _text(row.get("market_id"))
    selection = _text(row.get("selection"))
    if not event_id or not market_id or not selection:
        raise FeedbackError(
            f"{source} row needs event_id, market_id, and selection: "
            f"event={event_id!r} market={market_id!r} selection={selection!r}"
        )

    line = _text(row.get("line"))
    outcome_id = _text(row.get("outcome_id") or row.get("totals_id"))
    data_quality_flags = _text(row.get("data_quality_flags") or row.get("quality_flags"))
    if not outcome_id:
        outcome_id = _stable_id("outcome", event_id, market_id, selection, line)
        data_quality_flags = _append_flag(data_quality_flags, "synthetic_outcome_id")

    captured_at = _text(row.get("as_of")) or fallback_timestamp
    event_starts_at = _text(row.get("_event_starts_at") or row.get("event_starts_at"))
    hours_to_game = _float(row.get("hours_before_game"), field="hours_before_game")
    if hours_to_game is None:
        hours_to_game = probability_blend.hours_before_game(captured_at, event_starts_at)
    market_type = _text(row.get("market_type"))
    board = _text(row.get("board"))
    model_prob_source = _text(row.get("model_prob_source"))
    signal_flags, hit, insight, movement, orf = _signal_fields(row)

    if is_totals:
        total_kind = _text(row.get("total_kind")).lower()
        market_type = market_type or ("TEAM_PROP" if total_kind == "team" else "GAMELINE")
        board = board or ("TEAM_TOTALS" if total_kind == "team" else "GAME_TOTALS")
        best_side = _text(row.get("best_side")).upper()
        probability_value = (
            row.get("projected_under_prob")
            if best_side == "UNDER"
            else row.get("projected_over_prob")
        )
        market_consensus = _probability(
            _coalesce(row.get("market_consensus_prob"), probability_value),
            field="market_consensus_prob",
        )
        final_blended = _probability(
            _coalesce(row.get("final_blended_prob"), probability_value),
            field="final_blended_prob",
        )
        model_prob_source = model_prob_source or _text(row.get("devig_source"))
        shadow_status = (
            "QUALIFIED" if _truthy(row.get("shadow_actionable_4pct")) else "REJECTED"
        )
        signal_flags = ";".join(
            filter(
                None,
                (
                    signal_flags or data_quality_flags,
                    f"totals_shadow_4pct:{shadow_status}",
                    (
                        f"totals_shadow_reason:{_text(row.get('shadow_gate_reasons'))}"
                        if row.get("shadow_gate_reasons")
                        else ""
                    ),
                ),
            )
        )
    elif is_ultimate_alt:
        market_consensus = _probability(row.get("estimated_prob"), field="market_consensus_prob")
        final_blended = _probability(row.get("conservative_prob"), field="final_blended_prob")
        model_prob_source = "ultimate_alt_shadow_conservative"
        data_quality_flags = _text(row.get("rejection_reasons"))
        signal_flags = ";".join(
            filter(
                None,
                (
                    f"ultimate_alt:{_text(row.get('alt_type')).upper()}",
                    data_quality_flags,
                ),
            )
        )
    else:
        market_consensus = _probability(
            _coalesce(row.get("market_consensus_prob"), row.get("model_prob")),
            field="market_consensus_prob",
        )
        final_blended = _probability(
            _coalesce(row.get("final_blended_prob"), row.get("model_prob")),
            field="final_blended_prob",
        )

    independent = _probability(row.get("independent_model_prob"), field="independent_model_prob")
    recency_hit_prob = _probability(row.get("recency_hit_prob"), field="recency_hit_prob")
    price = _float(_coalesce(row.get("price"), row.get("best_price")), field="price")
    decimal_price = _float(row.get("decimal_price"), field="decimal_price")
    if decimal_price is None and price is not None:
        decimal_price = _american_to_decimal(price)
    implied_prob = _probability(row.get("implied_prob"), field="implied_prob")
    if implied_prob is None and decimal_price:
        implied_prob = 1.0 / decimal_price

    snapshot_id = _stable_id(
        "snapshot",
        captured_at,
        _text(row.get("sport")),
        event_id,
        market_id,
        outcome_id,
        selection,
        line,
        price,
        _text(row.get("book")),
    )
    return {
        "snapshot_id": snapshot_id,
        "captured_at": captured_at,
        "sport": _text(row.get("sport")),
        "event_id": event_id,
        "market_id": market_id,
        "outcome_id": outcome_id,
        "player_id": _text(row.get("player_id")),
        "selection": selection,
        "line": line,
        "price": price,
        "book": _text(row.get("book")),
        "market_consensus_prob": market_consensus,
        "independent_model_prob": independent,
        "final_blended_prob": final_blended,
        "blend_market_weight": _probability(
            row.get("blend_market_weight"), field="blend_market_weight"
        ),
        "blend_model_weight": _probability(
            row.get("blend_model_weight"), field="blend_model_weight"
        ),
        "blend_weight_source": _text(row.get("blend_weight_source")),
        "blend_model_version": _text(row.get("blend_model_version")),
        "blend_segment": _text(row.get("blend_segment")),
        "push_prob": _probability(row.get("push_prob"), field="push_prob"),
        "edge": _float(_coalesce(row.get("edge"), row.get("edge_pct")), field="edge"),
        "data_quality_flags": data_quality_flags,
        "data_quality_tier": _text(row.get("data_quality_tier"))
        or probability_blend.data_quality_tier(
            data_quality_flags, row.get("projection_quality_flags")
        ),
        "event_starts_at": event_starts_at,
        "hours_before_game": hours_to_game,
        "odds_range": _text(row.get("odds_range")) or probability_blend.odds_range(price),
        "time_before_game": _text(row.get("time_before_game"))
        or probability_blend.time_before_game_bucket(hours_to_game),
        "market_type": market_type,
        "model_prob_source": model_prob_source,
        "decimal_price": decimal_price,
        "implied_prob": implied_prob,
        "board": board,
        "selected": 1 if _truthy(row.get("selected")) else 0,
        "signal_flags": signal_flags,
        "hit_rate_component": hit,
        "insight_component": insight,
        "movement_component": movement,
        "orf_component": orf,
        "projection_feature_hash": _text(row.get("projection_feature_hash")),
        "projection_quality_flags": _text(row.get("projection_quality_flags")),
        "pack_path": str((recorded_pack_path or pack_dir).resolve()),
        "policy_fingerprint": _text(row.get("_policy_fingerprint")),
        "portfolio_mode": _text(row.get("_portfolio_mode"))
        or ("shadow" if is_ultimate_alt else ""),
        "pre_cap_units": _float(
            _coalesce(
                row.get("_pre_cap_units"),
                row.get("recommended_units_pre_news") if is_ultimate_alt else None,
            ),
            field="pre_cap_units",
        ),
        "portfolio_units": _float(
            _coalesce(
                row.get("_portfolio_units"),
                row.get("portfolio_shadow_units") if is_ultimate_alt else None,
            ),
            field="portfolio_units",
        ),
        "cap_reasons": _text(
            row.get("_cap_reasons") or (row.get("cap_reasons") if is_ultimate_alt else "")
        ),
        "recency_hit_prob": recency_hit_prob,
    }


def _decision_seed(snapshot: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    units = _float(row.get("recommended_units_pre_news"), field="units") or 0.0
    play = bool(snapshot["selected"]) and _truthy(row.get("actionable")) and units > 0
    decision_id = _stable_id("decision", snapshot["snapshot_id"])
    t30_status = _text(row.get("t30_status")).upper()
    return {
        "decision_id": decision_id,
        "snapshot_id": snapshot["snapshot_id"],
        "pipeline_verdict": "PLAY" if play else "STAND_DOWN",
        "A_verdict": "",
        "B_verdict": "",
        "C_verdict": "",
        "D_verdict": "",
        "final_verdict": "",
        "units": units if play else 0.0,
        "kill_reason": ""
        if play
        else (t30_status or snapshot["data_quality_flags"] or "not_selected_or_actionable"),
        "news_override": t30_status,
        "policy_fingerprint": snapshot.get("policy_fingerprint", ""),
        "portfolio_mode": snapshot.get("portfolio_mode", ""),
        "pre_cap_units": snapshot.get("pre_cap_units"),
        "portfolio_units": snapshot.get("portfolio_units"),
        "cap_reasons": snapshot.get("cap_reasons", ""),
    }


def capture_pack(
    pack_dir: Path,
    db_path: Path = DEFAULT_DB_PATH,
    *,
    recorded_pack_path: Path | None = None,
    connection: sqlite3.Connection | None = None,
    source_filename: str | None = None,
    snapshot_namespace: str = "",
    decision_filename: str = "decisions.csv",
) -> CaptureStats:
    """Persist a dated pack's opportunity snapshots and seed pipeline decisions.

    Re-capturing an identical source timestamp is idempotent.  A new line, price,
    source timestamp, side, or alternate outcome creates a new snapshot.
    """

    pack_dir = Path(pack_dir)
    if not pack_dir.exists():
        raise FeedbackError(f"Pack directory does not exist: {pack_dir}")

    fallback_timestamp = _pack_fallback_timestamp(pack_dir)
    recorded_path = str((recorded_pack_path or pack_dir).resolve())
    if source_filename:
        source_path = pack_dir / source_filename
        if not source_path.exists():
            raise FeedbackError(f"Pack source does not exist: {source_path}")
        source_rows = []
        for row in _read_csv(source_path):
            row["selected"] = "true"
            source_rows.append(("candidates", row))
    else:
        source_rows = _load_pack_rows(pack_dir)
    pack_timestamp = fallback_timestamp
    if not (pack_dir / "manifest.json").exists():
        pack_timestamp = _timestamp_extreme(
            (row.get("as_of") for _, row in source_rows), latest=True
        ) or fallback_timestamp
    pack_capture_id = _stable_id("pack-capture", recorded_path, pack_timestamp)
    captured_rows: list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    snapshots: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for source, row in source_rows:
        snapshot = _snapshot_from_pack_row(
            source, row, pack_dir, fallback_timestamp, recorded_pack_path
        )
        if snapshot_namespace:
            snapshot["snapshot_id"] = _stable_id(
                snapshot_namespace, snapshot["snapshot_id"]
            )
        snapshots.append(snapshot)
        decision = _decision_seed(snapshot, row)
        decisions.append(decision)
        captured_rows.append((source, row, snapshot, decision))

    now = _utc_now()
    connection_context = (
        nullcontext(connection) if connection is not None else _connect(Path(db_path))
    )
    with connection_context as conn:
        if conn is None:
            raise FeedbackError("capture_pack requires a valid SQLite connection")
        for snapshot in snapshots:
            values = [snapshot[field] for field in MARKET_SNAPSHOT_FIELDS]
            conn.execute(
                """
                INSERT INTO market_snapshots (
                    snapshot_id, captured_at, sport, event_id, market_id, outcome_id,
                    player_id, selection, line, price, book, market_consensus_prob,
                    independent_model_prob, final_blended_prob, blend_market_weight,
                    blend_model_weight, blend_weight_source, blend_model_version,
                    blend_segment, push_prob, edge, data_quality_flags,
                    data_quality_tier, event_starts_at, hours_before_game, odds_range,
                    time_before_game, market_type, model_prob_source, decimal_price,
                    implied_prob, board, selected, signal_flags, hit_rate_component,
                    insight_component, movement_component, orf_component,
                    projection_feature_hash, projection_quality_flags, pack_path,
                    policy_fingerprint, portfolio_mode, pre_cap_units, portfolio_units, cap_reasons,
                    recency_hit_prob, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    market_consensus_prob = COALESCE(
                        excluded.market_consensus_prob, market_snapshots.market_consensus_prob
                    ),
                    independent_model_prob = COALESCE(
                        excluded.independent_model_prob, market_snapshots.independent_model_prob
                    ),
                    final_blended_prob = COALESCE(
                        excluded.final_blended_prob, market_snapshots.final_blended_prob
                    ),
                    blend_market_weight = excluded.blend_market_weight,
                    blend_model_weight = excluded.blend_model_weight,
                    blend_weight_source = excluded.blend_weight_source,
                    blend_model_version = excluded.blend_model_version,
                    blend_segment = excluded.blend_segment,
                    edge = excluded.edge,
                    data_quality_flags = excluded.data_quality_flags,
                    data_quality_tier = excluded.data_quality_tier,
                    event_starts_at = excluded.event_starts_at,
                    hours_before_game = excluded.hours_before_game,
                    odds_range = excluded.odds_range,
                    time_before_game = excluded.time_before_game,
                    market_type = excluded.market_type,
                    model_prob_source = excluded.model_prob_source,
                    decimal_price = excluded.decimal_price,
                    implied_prob = excluded.implied_prob,
                    board = excluded.board,
                    selected = excluded.selected,
                    policy_fingerprint = excluded.policy_fingerprint,
                    portfolio_mode = excluded.portfolio_mode,
                    pre_cap_units = excluded.pre_cap_units,
                    portfolio_units = excluded.portfolio_units,
                    cap_reasons = excluded.cap_reasons,
                    push_prob = COALESCE(excluded.push_prob, market_snapshots.push_prob),
                    signal_flags = excluded.signal_flags,
                    hit_rate_component = excluded.hit_rate_component,
                    insight_component = excluded.insight_component,
                    movement_component = excluded.movement_component,
                    orf_component = excluded.orf_component,
                    projection_feature_hash = excluded.projection_feature_hash,
                    projection_quality_flags = excluded.projection_quality_flags,
                    recency_hit_prob = COALESCE(
                        excluded.recency_hit_prob, market_snapshots.recency_hit_prob
                    )
                WHERE NOT EXISTS (
                    SELECT 1 FROM decisions
                    WHERE decisions.snapshot_id = market_snapshots.snapshot_id
                      AND COALESCE(decisions.final_verdict, '') <> ''
                )
                  AND NOT EXISTS (
                    SELECT 1 FROM settlements
                    WHERE settlements.snapshot_id = market_snapshots.snapshot_id
                )
                """,
                [*values, now],
            )

        for decision in decisions:
            conn.execute(
                """
                INSERT INTO decisions (
                    decision_id, snapshot_id, pipeline_verdict,
                    A_verdict, B_verdict, C_verdict, D_verdict, final_verdict,
                    units, kill_reason, news_override,
                    policy_fingerprint, portfolio_mode, pre_cap_units, portfolio_units, cap_reasons,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(decision_id) DO UPDATE SET
                    pipeline_verdict = excluded.pipeline_verdict,
                    units = CASE
                        WHEN COALESCE(decisions.final_verdict, '') = '' THEN excluded.units
                        ELSE decisions.units
                    END,
                    kill_reason = CASE
                        WHEN COALESCE(decisions.final_verdict, '') = '' THEN excluded.kill_reason
                        ELSE decisions.kill_reason
                    END,
                    policy_fingerprint = excluded.policy_fingerprint,
                    portfolio_mode = excluded.portfolio_mode,
                    pre_cap_units = excluded.pre_cap_units,
                    portfolio_units = excluded.portfolio_units,
                    cap_reasons = excluded.cap_reasons,
                    updated_at = excluded.updated_at
                WHERE COALESCE(decisions.final_verdict, '') = ''
                  AND NOT EXISTS (
                    SELECT 1 FROM settlements
                    WHERE settlements.snapshot_id = decisions.snapshot_id
                )
                """,
                [decision[field] for field in DECISION_FIELDS] + [now, now],
            )

        for source, row, snapshot, decision in captured_rows:
            conn.execute(
                """
                INSERT INTO pack_snapshot_memberships (
                    pack_capture_id, snapshot_id, pack_path, pack_timestamp,
                    source, pipeline_verdict, units, selected, actionable, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pack_capture_id, snapshot_id) DO NOTHING
                """,
                (
                    pack_capture_id,
                    snapshot["snapshot_id"],
                    snapshot["pack_path"],
                    pack_timestamp,
                    source,
                    decision["pipeline_verdict"],
                    decision["units"],
                    snapshot["selected"],
                    1 if _truthy(row.get("actionable")) else 0,
                    now,
                ),
            )

        decision_ids = [decision["decision_id"] for decision in decisions]
        current: list[dict[str, Any]] = []
        for decision_id in sorted(set(decision_ids)):
            row = conn.execute(SELECT_DECISION_BY_ID_SQL, (decision_id,)).fetchone()
            if row is not None:
                current.append(dict(row))

    _write_csv(pack_dir / decision_filename, DECISION_FIELDS, current)
    return CaptureStats(snapshots=len(snapshots), decisions=len(current))


def capture_t30_pack(
    pack_dir: Path,
    db_path: Path = DEFAULT_DB_PATH,
    *,
    recorded_pack_path: Path | None = None,
    connection: sqlite3.Connection | None = None,
) -> CaptureStats:
    """Persist only the dedicated T-30 output under a separate snapshot namespace."""

    return capture_pack(
        pack_dir,
        db_path,
        recorded_pack_path=recorded_pack_path,
        connection=connection,
        source_filename="t30_reprice.csv",
        snapshot_namespace="t30",
        decision_filename="t30_decisions.csv",
    )


def import_decisions(input_path: Path, db_path: Path = DEFAULT_DB_PATH) -> ImportStats:
    rows = _read_csv(Path(input_path), DECISION_FIELDS)
    now = _utc_now()
    with _connect(Path(db_path)) as conn:
        for row_number, row in enumerate(rows, start=2):
            snapshot_id = _text(row.get("snapshot_id"))
            snapshot = conn.execute(
                "SELECT snapshot_id FROM market_snapshots WHERE snapshot_id = ?", (snapshot_id,)
            ).fetchone()
            if snapshot is None:
                raise FeedbackError(
                    f"{input_path}:{row_number} references unknown snapshot_id {snapshot_id!r}"
                )
            expected_decision_id = _stable_id("decision", snapshot_id)
            supplied_decision_id = _text(row.get("decision_id"))
            if supplied_decision_id and supplied_decision_id != expected_decision_id:
                raise FeedbackError(
                    f"{input_path}:{row_number} decision_id {supplied_decision_id!r} does not "
                    f"match snapshot_id {snapshot_id!r} ({expected_decision_id!r})"
                )
            decision_id = expected_decision_id
            values: dict[str, Any] = {
                field: (
                    _text(row.get(field)).upper() if "verdict" in field else _text(row.get(field))
                )
                for field in DECISION_FIELDS
            }
            values["decision_id"] = decision_id
            values["snapshot_id"] = snapshot_id
            values["units"] = _float(row.get("units"), field="units") or 0.0
            existing = conn.execute(
                SELECT_DECISION_BY_ID_SQL,
                (decision_id,),
            ).fetchone()
            settled = conn.execute(
                "SELECT 1 FROM settlements WHERE snapshot_id = ? LIMIT 1",
                (snapshot_id,),
            ).fetchone()
            if existing is not None and (
                _text(existing["final_verdict"]) != "" or settled is not None
            ):
                changed = any(
                    (
                        float(existing[field] or 0.0) != float(values[field] or 0.0)
                        if field == "units"
                        else _text(existing[field]) != _text(values[field])
                    )
                    for field in DECISION_FIELDS
                )
                if changed:
                    raise FeedbackError(
                        f"{input_path}:{row_number} cannot change finalized or settled "
                        f"decision {decision_id!r}"
                    )
            conn.execute(
                """
                INSERT INTO decisions (
                    decision_id, snapshot_id, pipeline_verdict,
                    A_verdict, B_verdict, C_verdict, D_verdict, final_verdict,
                    units, kill_reason, news_override,
                    policy_fingerprint, portfolio_mode, pre_cap_units, portfolio_units, cap_reasons,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(decision_id) DO UPDATE SET
                    snapshot_id = excluded.snapshot_id,
                    pipeline_verdict = excluded.pipeline_verdict,
                    A_verdict = excluded.A_verdict,
                    B_verdict = excluded.B_verdict,
                    C_verdict = excluded.C_verdict,
                    D_verdict = excluded.D_verdict,
                    final_verdict = excluded.final_verdict,
                    units = excluded.units,
                    kill_reason = excluded.kill_reason,
                    news_override = excluded.news_override,
                    policy_fingerprint = excluded.policy_fingerprint,
                    portfolio_mode = excluded.portfolio_mode,
                    pre_cap_units = excluded.pre_cap_units,
                    portfolio_units = excluded.portfolio_units,
                    cap_reasons = excluded.cap_reasons,
                    updated_at = excluded.updated_at
                WHERE COALESCE(decisions.final_verdict, '') = ''
                  AND NOT EXISTS (
                    SELECT 1 FROM settlements
                    WHERE settlements.snapshot_id = decisions.snapshot_id
                )
                """,
                [values[field] for field in DECISION_FIELDS] + [now, now],
            )
    return ImportStats(imported=len(rows))
