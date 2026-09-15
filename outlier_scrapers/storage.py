from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from outlier_scrapers.database import (
    ExtractionPayload,
    PackArtifact,
    PackCandidate,
    PackTotal,
    SessionLocal,
)

PACK_STREAM_CANDIDATES = "candidates"
PACK_STREAM_OPPORTUNITIES = "opportunities"
PACK_STREAM_GAME_TOTALS = "game_totals"
PACK_STREAM_TEAM_TOTALS = "team_totals"
PACK_STREAM_DECISIONS = "decisions"


def save_extraction(
    league: str,
    data_type: str,
    date_str: str,
    payload: dict[str, Any],
) -> None:
    with SessionLocal.begin() as db:
        db.query(ExtractionPayload).filter_by(
            league=league,
            data_type=data_type,
            date=date_str,
        ).delete(synchronize_session=False)
        db.add(
            ExtractionPayload(
                league=league,
                data_type=data_type,
                date=date_str,
                payload=payload,
            )
        )


def load_extraction(league: str, data_type: str, date_str: str) -> dict[str, Any] | None:
    with SessionLocal() as db:
        record = (
            db.query(ExtractionPayload)
            .filter_by(league=league, data_type=data_type, date=date_str)
            .order_by(ExtractionPayload.id.desc())
            .first()
        )
        return dict(cast(dict[str, Any], record.payload)) if record else None


def _copy_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def save_pack_artifacts(
    pack_date: str,
    artifacts: Mapping[str, Sequence[Mapping[str, Any]]],
) -> None:
    """Atomically replace database mirrors for the supplied pack streams."""
    if not pack_date:
        raise ValueError("pack_date is required")
    with SessionLocal.begin() as db:
        for stream, rows in artifacts.items():
            if not stream:
                raise ValueError("pack artifact stream is required")
            db.query(PackArtifact).filter_by(
                pack_date=pack_date,
                stream=stream,
            ).delete(synchronize_session=False)
            db.add(
                PackArtifact(
                    pack_date=pack_date,
                    stream=stream,
                    payload=_copy_rows(rows),
                )
            )


def _load_pack_artifact_optional(
    pack_date: str,
    stream: str,
) -> list[dict[str, Any]] | None:
    with SessionLocal() as db:
        record = (
            db.query(PackArtifact)
            .filter_by(pack_date=pack_date, stream=stream)
            .order_by(PackArtifact.id.desc())
            .first()
        )
        if record is None:
            return None
        payload = record.payload
        if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
            raise ValueError(
                f"Invalid database payload for pack_date={pack_date!r}, stream={stream!r}"
            )
        return [dict(row) for row in payload]


def load_pack_artifact(pack_date: str, stream: str) -> list[dict[str, Any]]:
    """Load a lossless database mirror, returning an empty list when absent."""
    return _load_pack_artifact_optional(pack_date, stream) or []


def save_candidates(pack_date: str, rows: list[dict[str, Any]]) -> None:
    save_pack_artifacts(pack_date, {PACK_STREAM_CANDIDATES: rows})


def _load_legacy_candidates(pack_date: str) -> list[dict[str, Any]]:
    """Read rows written by the short-lived partial relational migration."""
    with SessionLocal() as db:
        records = db.query(PackCandidate).filter_by(pack_date=pack_date).all()
        return [
            {
                "sport": record.sport,
                "event_id": record.event_id,
                "market_id": record.market_id,
                "outcome_id": record.outcome_id,
                "player_id": record.player_id,
                "selection": record.selection,
                "line": record.line,
                "price": record.price,
                "book": record.book,
                "market_consensus_prob": record.market_consensus_prob,
                "independent_model_prob": record.independent_model_prob,
                "final_blended_prob": record.final_blended_prob,
                "push_prob": record.push_prob,
                "edge": record.edge,
                "data_quality_flags": record.data_quality_flags,
                "data_quality_tier": record.data_quality_tier,
                "_event_starts_at": record.event_starts_at,
                "market_type": record.market_type,
                "decimal_price": record.decimal_price,
                "board": record.board,
                "recommended_units_pre_news": record.recommended_units_pre_news,
                "actionable": record.actionable,
            }
            for record in records
        ]


def load_candidates(pack_date: str, sport: str | None = None) -> list[dict[str, Any]]:
    rows = _load_pack_artifact_optional(pack_date, PACK_STREAM_CANDIDATES)
    if rows is None:
        rows = _load_legacy_candidates(pack_date)
    if sport:
        rows = [row for row in rows if row.get("sport") == sport]
    return rows


def save_totals(
    pack_date: str,
    game_totals_rows: list[dict[str, Any]],
    team_totals_rows: list[dict[str, Any]],
) -> None:
    save_pack_artifacts(
        pack_date,
        {
            PACK_STREAM_GAME_TOTALS: game_totals_rows,
            PACK_STREAM_TEAM_TOTALS: team_totals_rows,
        },
    )


def _load_legacy_totals(
    pack_date: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read rows written by the short-lived partial relational migration."""
    with SessionLocal() as db:
        records = db.query(PackTotal).filter_by(pack_date=pack_date).all()
        game_totals: list[dict[str, Any]] = []
        team_totals: list[dict[str, Any]] = []
        for record in records:
            row: dict[str, Any] = {
                "sport": record.sport,
                "event_id": record.event_id,
                "market_id": record.market_id,
                "outcome_id": record.outcome_id,
                "selection": record.selection,
                "line": record.line,
                "price": record.price,
                "book": record.book,
                "market_consensus_prob": record.market_consensus_prob,
                "final_blended_prob": record.final_blended_prob,
                "push_prob": record.push_prob,
                "edge": record.edge,
                "data_quality_flags": record.data_quality_flags,
                "data_quality_tier": record.data_quality_tier,
                "_event_starts_at": record.event_starts_at,
                "market_type": record.market_type,
                "decimal_price": record.decimal_price,
                "board": record.board,
                "recommended_units_pre_news": record.recommended_units_pre_news,
                "actionable": record.actionable,
            }
            team_id = cast(str | None, record.team_id)
            if team_id:
                row["team_id"] = team_id
                team_totals.append(row)
            else:
                game_totals.append(row)
        return game_totals, team_totals


def load_totals(
    pack_date: str,
    sport: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    game_totals = _load_pack_artifact_optional(pack_date, PACK_STREAM_GAME_TOTALS)
    team_totals = _load_pack_artifact_optional(pack_date, PACK_STREAM_TEAM_TOTALS)
    if game_totals is None and team_totals is None:
        game_totals, team_totals = _load_legacy_totals(pack_date)
    else:
        game_totals = game_totals or []
        team_totals = team_totals or []
    if sport:
        game_totals = [row for row in game_totals if row.get("sport") == sport]
        team_totals = [row for row in team_totals if row.get("sport") == sport]
    return game_totals, team_totals
