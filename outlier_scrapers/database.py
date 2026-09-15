import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, event, Column, Integer, String, Float, DateTime, JSON, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.environ.get(
    "OUTLIER_DATABASE_URL", 
    "postgresql://postgres:postgres@localhost:5432/outlier"
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FALLBACK_SQLITE_PATH = Path(
    os.environ.get("OUTLIER_SQLITE_FALLBACK_PATH")
    or PROJECT_ROOT / "data" / "outlier-pipeline.sqlite3"
)


def _create_fallback_engine():
    """Build the local SQLite engine used when Postgres is unreachable.

    File-backed rather than ``:memory:`` on purpose. An in-memory SQLite
    database is private to a single connection, so every pooled connection --
    and every one of the pipeline's concurrent extraction workers -- would get
    its own empty database and silently lose the others' writes. WAL plus a
    generous busy timeout is what lets those writers actually share the file.
    """
    FALLBACK_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fallback = create_engine(
        f"sqlite:///{FALLBACK_SQLITE_PATH}",
        echo=False,
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(fallback, "connect")
    def _apply_sqlite_pragmas(dbapi_connection, _connection_record):  # pragma: no cover - driver hook
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()

    return fallback


# Attempt to connect to Postgres, fallback to a shared local SQLite file.
try:
    engine = create_engine(DATABASE_URL, echo=False)
    engine.connect().close()
except Exception:
    engine = _create_fallback_engine()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
class Base(DeclarativeBase):
    pass

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


class PackArtifact(Base):
    """Lossless database mirror of a canonical pack artifact.

    Pack schemas evolve frequently, so storing the complete row dictionaries is
    safer than projecting them into a partial relational model. The immutable
    files in ``packs/<date>`` remain the publication and hashing authority.
    """

    __tablename__ = "pack_artifacts"
    __table_args__ = (UniqueConstraint("pack_date", "stream", name="uq_pack_artifact"),)

    id = Column(Integer, primary_key=True, index=True)
    pack_date = Column(String, index=True, nullable=False)
    stream = Column(String, index=True, nullable=False)
    payload = Column(JSON, nullable=False)
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

