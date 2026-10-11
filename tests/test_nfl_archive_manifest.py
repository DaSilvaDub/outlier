"""#231 part c: archive manifest definition (typed and validated)."""

from __future__ import annotations

import json

import pytest

from outlier_nfl import archive as ar

SHA = "a" * 64


def _pregame(**kw):
    base = {"slate": "2026-10-04", "role": "pregame_player_props",
            "path": "runs/R1/raw/player_props.json", "sha256": SHA, "size": 10,
            "captured_at": "2026-10-04T15:00:00+00:00", "source": "outlier_api",
            "season": 2026, "run_id": "R1"}
    base.update(kw)
    return base


def _nflverse(**kw):
    base = {"slate": None, "role": "nflverse_boxscore",
            "path": "/home/d/.cache/outlier_nflverse/stats_player_week_2025.csv",
            "sha256": SHA, "size": 10, "captured_at": "2026-01-06T12:00:00+00:00",
            "source": "nflverse", "season": 2025, "run_id": None,
            "fetched_at": "2026-01-06T12:00:00+00:00"}
    base.update(kw)
    return base


def test_valid_entries_round_trip():
    for raw in (_pregame(), _nflverse(), _pregame(slate="2026-10-04_early")):
        e = ar.parse_entry(raw)
        assert ar.parse_entry(json.loads(e.to_json())) == e


@pytest.mark.parametrize(("bad", "field"), [
    ({"role": "box_scores"}, "role"),
    ({"slate": None}, "slate"),
    ({"slate": "10/04/2026"}, "slate"),
    ({"path": ""}, "path"),
    ({"path": "runs\\R1\\raw.json"}, "path"),
    ({"sha256": "ABC"}, "sha256"),
    ({"size": -1}, "size"),
    ({"size": True}, "size"),
    ({"season": "2026"}, "season"),
    ({"source": "scraper"}, "source"),
    ({"run_id": None}, "run_id"),
    ({"captured_at": "2026-10-04T15:00:00"}, "captured_at"),  # naive
    ({"captured_at": "yesterday"}, "captured_at"),
    ({"fetched_at": "2026-10-04T15:00:00+00:00"}, "fetched_at"),  # pregame has none
    ({"extra": 1}, "unknown"),
])
def test_invalid_pregame_fields_are_named(bad, field):
    with pytest.raises(ar.ArchiveError, match=field):
        ar.parse_entry(_pregame(**bad))


def test_nflverse_role_requires_fetched_at():
    with pytest.raises(ar.ArchiveError, match="fetched_at"):
        ar.parse_entry(_nflverse(fetched_at=None))


def test_read_manifest_reports_bad_lines_without_dropping_good_ones(tmp_path):
    p = tmp_path / "manifest.jsonl"
    p.write_text("\n".join([json.dumps(_pregame()), "{not json", json.dumps(_pregame(size=-2)),
                            "", json.dumps(_nflverse())]) + "\n", encoding="utf-8")
    before = p.read_bytes()
    entries, problems = ar.read_manifest(p)
    assert [e.role for e in entries] == ["pregame_player_props", "nflverse_boxscore"]
    assert [pr.line for pr in problems] == [2, 3]
    assert p.read_bytes() == before  # reading never writes


def test_missing_manifest_is_empty_not_an_error(tmp_path):
    assert ar.read_manifest(tmp_path / "nope.jsonl") == ([], [])
