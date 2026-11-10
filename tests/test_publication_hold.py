"""Tests for outlier_scrapers.publication_hold (Python twin of Assert-PublicationHold)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from outlier_scrapers.publication_hold import (
    PublicationHoldError,
    hold_paths,
    publication_hold_active,
)


def _write_hold(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_no_hold_files_means_inactive(tmp_path: Path) -> None:
    assert publication_hold_active(tmp_path, extra_paths=()) is False


def test_active_hold_in_repo(tmp_path: Path) -> None:
    _write_hold(tmp_path / "docs" / "remediation" / "publication_hold.json", {"active": True})
    assert publication_hold_active(tmp_path, extra_paths=()) is True


def test_inactive_hold_in_repo(tmp_path: Path) -> None:
    _write_hold(tmp_path / "docs" / "remediation" / "publication_hold.json", {"active": False})
    assert publication_hold_active(tmp_path, extra_paths=()) is False


def test_extra_path_hold_wins_over_inactive_repo_hold(tmp_path: Path) -> None:
    _write_hold(tmp_path / "docs" / "remediation" / "publication_hold.json", {"active": False})
    quarantine = _write_hold(tmp_path / "quarantine" / "publication_hold.json", {"active": True})
    assert publication_hold_active(tmp_path, extra_paths=(quarantine,)) is True


@pytest.mark.parametrize("payload", [{}, {"active": "yes"}, {"active": 1}, ["active"]])
def test_malformed_hold_fails_closed(tmp_path: Path, payload: object) -> None:
    _write_hold(tmp_path / "docs" / "remediation" / "publication_hold.json", payload)
    with pytest.raises(PublicationHoldError):
        publication_hold_active(tmp_path, extra_paths=())


def test_unparseable_hold_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "docs" / "remediation" / "publication_hold.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(PublicationHoldError):
        publication_hold_active(tmp_path, extra_paths=())


def test_hold_paths_includes_repo_file_first(tmp_path: Path) -> None:
    paths = hold_paths(tmp_path)
    assert paths[0] == tmp_path / "docs" / "remediation" / "publication_hold.json"
