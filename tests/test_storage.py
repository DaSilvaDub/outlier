from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from outlier_scrapers import storage
from outlier_scrapers.database import Base, PackArtifact
from outlier_scrapers.utils import _write_csv, safe_write_text


def _sharing_violation() -> PermissionError:
    error = PermissionError(13, "sharing violation")
    error.winerror = 32
    return error


@pytest.fixture
def isolated_storage(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(storage, "SessionLocal", sessions)
    return sessions


def test_pack_artifacts_round_trip_losslessly_and_replace_atomically(isolated_storage):
    candidate = {
        "sport": "MLB",
        "outcome_id": "candidate-1",
        "projection_quantiles": {"0.1": 4, "0.9": 11},
        "source_timestamps": ["odds", "projection"],
    }
    game_total = {
        "totals_id": "total-1",
        "sport": "MLB",
        "total_kind": "game",
        "edge_pct": 0.081,
        "quality_flags": "",
    }
    team_total = {
        "totals_id": "total-2",
        "sport": "WNBA",
        "total_kind": "team",
        "team": "NYL",
        "fair_total": 86.5,
    }

    storage.save_pack_artifacts(
        "2026-08-31",
        {
            storage.PACK_STREAM_CANDIDATES: [candidate],
            storage.PACK_STREAM_GAME_TOTALS: [game_total],
            storage.PACK_STREAM_TEAM_TOTALS: [team_total],
            storage.PACK_STREAM_DECISIONS: [],
        },
    )

    assert storage.load_candidates("2026-08-31") == [candidate]
    assert storage.load_totals("2026-08-31") == ([game_total], [team_total])
    assert storage.load_totals("2026-08-31", sport="MLB") == ([game_total], [])
    assert storage.load_pack_artifact(
        "2026-08-31", storage.PACK_STREAM_DECISIONS
    ) == []

    replacement = {**candidate, "outcome_id": "candidate-2"}
    storage.save_candidates("2026-08-31", [replacement])

    assert storage.load_candidates("2026-08-31") == [replacement]
    with isolated_storage() as db:
        assert (
            db.query(PackArtifact)
            .filter_by(pack_date="2026-08-31", stream=storage.PACK_STREAM_CANDIDATES)
            .count()
            == 1
        )
        assert db.query(PackArtifact).filter_by(pack_date="2026-08-31").count() == 4


def test_pack_artifact_transaction_rolls_back_all_streams(isolated_storage):
    storage.save_pack_artifacts(
        "2026-08-31",
        {
            storage.PACK_STREAM_CANDIDATES: [{"outcome_id": "original"}],
            storage.PACK_STREAM_GAME_TOTALS: [{"totals_id": "original"}],
        },
    )

    with pytest.raises(StatementError):
        storage.save_pack_artifacts(
            "2026-08-31",
            {
                storage.PACK_STREAM_CANDIDATES: [{"outcome_id": "replacement"}],
                storage.PACK_STREAM_GAME_TOTALS: [{"invalid": object()}],
            },
        )

    assert storage.load_candidates("2026-08-31") == [{"outcome_id": "original"}]
    assert storage.load_pack_artifact(
        "2026-08-31", storage.PACK_STREAM_GAME_TOTALS
    ) == [{"totals_id": "original"}]


def test_safe_write_text_retries_transient_replace_lock(tmp_path, monkeypatch):
    destination = tmp_path / "artifact.md"
    destination.write_text("old", encoding="utf-8")
    original_replace = Path.replace
    attempts = 0

    def flaky_replace(source, target):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise _sharing_violation()
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    safe_write_text(destination, "new", retries=3, delay=0)

    assert attempts == 3
    assert destination.read_text(encoding="utf-8") == "new"


def test_write_csv_fails_closed_and_preserves_previous_file(tmp_path, monkeypatch):
    destination = tmp_path / "artifact.csv"
    destination.write_text("old\n", encoding="utf-8")

    attempts = 0

    def locked_replace(source, target):
        nonlocal attempts
        attempts += 1
        raise _sharing_violation()

    monkeypatch.setattr(Path, "replace", locked_replace)

    with pytest.raises(PermissionError):
        _write_csv(
            destination,
            ["value"],
            [{"value": "new"}],
            retries=2,
            delay=0,
        )

    assert destination.read_text(encoding="utf-8") == "old\n"
    assert attempts == 2
    assert list(tmp_path.glob(".artifact_tmp_*.csv")) == []


def test_safe_write_text_does_not_retry_non_sharing_errors(tmp_path, monkeypatch):
    destination = tmp_path / "artifact.md"
    attempts = 0

    def denied_replace(source, target):
        nonlocal attempts
        attempts += 1
        raise PermissionError(13, "access denied")

    monkeypatch.setattr(Path, "replace", denied_replace)

    with pytest.raises(PermissionError):
        safe_write_text(destination, "new", retries=5, delay=0)

    assert attempts == 1
