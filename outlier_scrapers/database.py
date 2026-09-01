import os
from typing import Any
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, JSON
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.environ.get(
    "OUTLIER_DATABASE_URL", 
    "postgresql://postgres:postgres@localhost:5432/outlier"
)

# Attempt to connect to Postgres, fallback to SQLite for local tests
try:
    engine = create_engine(DATABASE_URL, echo=False)
    engine.connect().close()
except Exception:
    engine = create_engine("sqlite:///:memory:", echo=False)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class ExtractionPayload(Base):
    """
    Replaces `_latest.json` files for raw/normalized extractions.
    Indexed by league, dataType (e.g. 'games', 'props', 'line_movement', 'projections'), and date.
    """
    __tablename__ = "extraction_payloads"

    id = Column(Integer, primary_key=True, index=True)
    league = Column(String, index=True, nullable=False)
    data_type = Column(String, index=True, nullable=False)
    date = Column(String, index=True, nullable=False)  # ISO date string e.g. '2026-08-31'
    created_at = Column(DateTime, default=datetime.utcnow)
    payload = Column(JSON, nullable=False)

class PackCandidate(Base):
    """
    Replaces `candidates.csv`.
    """
    __tablename__ = "pack_candidates"

    id = Column(Integer, primary_key=True, index=True)
    pack_date = Column(String, index=True, nullable=False)
    sport = Column(String, index=True, nullable=False)
    event_id = Column(String, index=True)
    market_id = Column(String, index=True)
    outcome_id = Column(String, index=True)
    player_id = Column(String, index=True)
    selection = Column(String)
    line = Column(String)
    price = Column(Float)
    book = Column(String)
    market_consensus_prob = Column(Float)
    independent_model_prob = Column(Float)
    final_blended_prob = Column(Float)
    push_prob = Column(Float)
    edge = Column(Float)
    data_quality_flags = Column(String)
    data_quality_tier = Column(String)
    event_starts_at = Column(String)
    market_type = Column(String)
    decimal_price = Column(Float)
    board = Column(String)
    recommended_units_pre_news = Column(Float)
    actionable = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

class PackTotal(Base):
    """
    Replaces `team_totals.csv` and `game_totals.csv`.
    """
    __tablename__ = "pack_totals"

    id = Column(Integer, primary_key=True, index=True)
    pack_date = Column(String, index=True, nullable=False)
    sport = Column(String, index=True, nullable=False)
    event_id = Column(String, index=True)
    team_id = Column(String, index=True, nullable=True) # None for game totals
    market_id = Column(String, index=True)
    outcome_id = Column(String, index=True)
    selection = Column(String)
    line = Column(String)
    price = Column(Float)
    book = Column(String)
    market_consensus_prob = Column(Float)
    final_blended_prob = Column(Float)
    push_prob = Column(Float)
    edge = Column(Float)
    data_quality_flags = Column(String)
    data_quality_tier = Column(String)
    event_starts_at = Column(String)
    market_type = Column(String)
    decimal_price = Column(Float)
    board = Column(String)
    recommended_units_pre_news = Column(Float)
    actionable = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

class VerdictRecord(Base):
    """
    Replaces verdict_records in SQLite.
    """
    __tablename__ = "verdict_records"

    decision_id = Column(String, primary_key=True, index=True)
    pass_name = Column(String, primary_key=True, index=True)
    publication_id = Column(String, primary_key=True, index=True)
    record_id = Column(String, primary_key=True, index=True)
    outcome_id = Column(String)
    stream = Column(String)
    verdict = Column(String)
    confidence = Column(Float)
    recommended_units_model = Column(Float)
    recommended_units_adjusted = Column(Float)
    sizing_rationale = Column(String)
    market_consensus_prob = Column(Float)
    final_blended_prob = Column(Float)
    edge_pct = Column(Float)
    implied_prob = Column(Float)
    synthesis_source = Column(String)
    created_at = Column(String)

class MarketSnapshot(Base):
    """
    Replaces MARKET_SNAPSHOT_FIELDS in feedback SQLite.
    """
    __tablename__ = "market_snapshots"

    snapshot_id = Column(String, primary_key=True, index=True)
    captured_at = Column(String)
    sport = Column(String, index=True)
    event_id = Column(String)
    market_id = Column(String)
    outcome_id = Column(String)
    player_id = Column(String)
    selection = Column(String)
    line = Column(String)
    price = Column(Float)
    book = Column(String)
    market_consensus_prob = Column(Float)
    independent_model_prob = Column(Float)
    final_blended_prob = Column(Float)
    blend_market_weight = Column(Float)
    blend_model_weight = Column(Float)
    blend_weight_source = Column(String)
    blend_model_version = Column(String)
    blend_segment = Column(String)
    push_prob = Column(Float)
    edge = Column(Float)
    data_quality_flags = Column(String)
    data_quality_tier = Column(String)
    event_starts_at = Column(String)
    hours_before_game = Column(Float)
    odds_range = Column(String)
    time_before_game = Column(String)
    market_type = Column(String)
    model_prob_source = Column(String)
    decimal_price = Column(Float)

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

init_db()

