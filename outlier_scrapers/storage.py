from typing import Any
from datetime import datetime
from outlier_scrapers.database import get_db, ExtractionPayload, PackCandidate, PackTotal

def save_extraction(league: str, data_type: str, date_str: str, payload: dict[str, Any]) -> None:
    db = next(get_db())
    # Delete existing for idempotency
    db.query(ExtractionPayload).filter_by(
        league=league, data_type=data_type, date=date_str
    ).delete()
    record = ExtractionPayload(
        league=league,
        data_type=data_type,
        date=date_str,
        payload=payload
    )
    db.add(record)
    db.commit()

def load_extraction(league: str, data_type: str, date_str: str) -> dict[str, Any] | None:
    db = next(get_db())
    record = db.query(ExtractionPayload).filter_by(
        league=league, data_type=data_type, date=date_str
    ).order_by(ExtractionPayload.id.desc()).first()
    return record.payload if record else None

def save_candidates(pack_date: str, rows: list[dict[str, Any]]) -> None:
    db = next(get_db())
    db.query(PackCandidate).filter_by(pack_date=pack_date).delete()
    candidates = []
    for row in rows:
        sport = row.get("sport") or "UNKNOWN"
        def _float_or_none(val: Any) -> float | None:
            if val in (None, ""):
                return None
            return float(val)
        
        candidates.append(PackCandidate(
            pack_date=pack_date,
            sport=sport,
            event_id=row.get("event_id"),
            market_id=row.get("market_id"),
            outcome_id=row.get("outcome_id"),
            player_id=row.get("player_id"),
            selection=row.get("selection"),
            line=str(row.get("line")) if row.get("line") is not None else None,
            price=row.get("price"),
            book=row.get("book"),
            market_consensus_prob=_float_or_none(row.get("market_consensus_prob")),
            independent_model_prob=_float_or_none(row.get("independent_model_prob")),
            final_blended_prob=_float_or_none(row.get("final_blended_prob")),
            push_prob=_float_or_none(row.get("push_prob")),
            edge=_float_or_none(row.get("edge")),
            data_quality_flags=row.get("data_quality_flags"),
            data_quality_tier=row.get("data_quality_tier"),
            event_starts_at=row.get("_event_starts_at") or row.get("event_starts_at"),
            market_type=row.get("market_type"),
            decimal_price=_float_or_none(row.get("decimal_price")),
            board=row.get("board"),
            recommended_units_pre_news=_float_or_none(row.get("recommended_units_pre_news")),
            actionable=str(row.get("actionable")) if row.get("actionable") is not None else None,
        ))
    db.bulk_save_objects(candidates)
    db.commit()

def load_candidates(pack_date: str, sport: str | None = None) -> list[dict[str, Any]]:
    db = next(get_db())
    query = db.query(PackCandidate).filter_by(pack_date=pack_date)
    if sport:
        query = query.filter_by(sport=sport)
    records = query.all()
    rows = []
    for record in records:
        rows.append({
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
            "event_starts_at": record.event_starts_at,
            "market_type": record.market_type,
            "decimal_price": record.decimal_price,
            "board": record.board,
            "recommended_units_pre_news": record.recommended_units_pre_news,
            "actionable": record.actionable,
        })
    return rows
