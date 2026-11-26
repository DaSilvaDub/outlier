"""Regression checks for current-slate, pre-tip NBA quote publication."""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import export_nba_extra_pack as exp  # noqa: E402

NOW = datetime(2026, 11, 23, 12, tzinfo=UTC)
DATE = "2026-11-23"


def _row(**changes):
    row = {
        "slate_date": DATE,
        "player": "Current Player",
        "market": "PTS",
        "line": "20.5",
        "edge_pct": "7.5",
        "recommended_side": "OVER",
        "recommended_odds": "-115",
        "over_odds": "-115",
        "game_time": "2026-11-24T00:00:00Z",
        "quote_timestamp": "2026-11-23T11:45:00Z",
    }
    return row | changes


def _write(path, rows):
    fields = list(dict.fromkeys(field for row in rows for field in row)) or exp.FIELDNAMES
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _read(path):
    with path.open(encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


@pytest.fixture(autouse=True)
def no_host_disk_check(monkeypatch):
    monkeypatch.setattr(exp, "check_disk_space", lambda **kwargs: None)


def test_requested_date_wins_and_after_midnight_utc_tip_is_correct(tmp_path):
    src, out = tmp_path / "src.csv", tmp_path / "nba_only.csv"
    _write(src, [_row(slate_date="2026-11-22", player="Yesterday"), _row()])
    assert exp.export_nba_pack(src, out, DATE, now=NOW) == 1
    assert [row["player"] for row in _read(out)] == ["Current Player"]
    status = json.loads(out.with_suffix(".status.json").read_text())
    assert status["status"] == "ready"
    assert status["row_count"] == status["actionable_rows"] == 1
    assert status["csv_sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()


@pytest.mark.parametrize("fallback", [True, False])
def test_stale_slate_never_falls_back_even_with_legacy_flag(tmp_path, fallback):
    src, out = tmp_path / "src.csv", tmp_path / "nba_only.csv"
    _write(src, [_row(slate_date="2026-11-22")])
    assert exp.export_nba_pack(src, out, DATE, allow_latest_fallback=fallback, now=NOW) == 0
    assert _read(out) == []
    status = json.loads(out.with_suffix(".status.json").read_text())
    assert status["status"] == "awaiting_odds"
    assert status["slate_date"] == DATE
    assert status["actionable_rows"] == 0


@pytest.mark.parametrize(
    "changes, reason",
    [
        ({"game_time": "2026-11-23T12:00:00Z"}, "game_started"),
        ({"game_time": "2026-11-23T11:59:59Z"}, "game_started"),
        ({"game_time": "2026-11-23T20:00:00"}, "unverified_game_time"),
        ({"game_time": ""}, "unverified_game_time"),
        ({"game_time": "bad"}, "unverified_game_time"),
        ({"game_time": "2026-11-24T06:00:00Z"}, "wrong_game_date"),
        ({"game_status": "FINAL"}, "game_unavailable"),
        ({"recommended_odds": ""}, "awaiting_odds"),
        ({"quote_timestamp": ""}, "awaiting_odds"),
        ({"recommended_odds": "NaN"}, "awaiting_odds"),
        ({"recommended_odds": "-99"}, "awaiting_odds"),
        ({"recommended_odds": "-115.5"}, "awaiting_odds"),
        ({"recommended_side": ""}, "awaiting_odds"),
        ({"line": "inf"}, "awaiting_odds"),
        ({"line": ""}, "awaiting_odds"),
        ({"over_odds": "-120"}, "price_mismatch"),
        ({"odds_stale": "true"}, "stale_odds"),
        ({"quote_timestamp": "2026-11-22T23:59:59Z"}, "stale_odds"),
        ({"quote_timestamp": "2026-11-23T12:01:00Z"}, "stale_odds"),
        ({"quote_timestamp": "2026-11-23T11:00:00"}, "unverified_quote_time"),
    ],
)
def test_publication_rejects_unverifiable_or_unavailable_quote(changes, reason):
    row = _row(**changes)
    assert exp.publication_rejection(row, DATE, NOW) == reason
    assert not exp.publication_eligible(row, DATE, NOW)


def test_under_uses_exact_under_quote():
    row = _row(recommended_side="UNDER", recommended_odds="105", under_odds="105")
    assert exp.publication_eligible(row, DATE, NOW)


def test_bettingpros_requires_individual_book_and_selected_offer():
    row = _row(
        provider="bettingpros",
        source_name="FanDuel",
        provider_book="FanDuel",
        provider_event_id="123",
        provider_quote_id="456",
        over_odds="-115",
        recommended_side="OVER",
        recommended_odds="-115",
    )
    assert exp.publication_eligible(row, DATE, NOW)
    for field in (
        "provider_event_id",
        "provider_quote_id",
        "provider_book",
        "over_odds",
        "quote_timestamp",
    ):
        assert not exp.publication_eligible({**row, field: ""}, DATE, NOW)
    assert not exp.publication_eligible({**row, "source_name": "BettingPros Consensus"}, DATE, NOW)


def test_empty_overwrites_old_pack_and_status_before_fail_on_empty(tmp_path):
    src, out = tmp_path / "src.csv", tmp_path / "nba_only.csv"
    out.write_text("yesterdays stale rows")
    _write(src, [_row(recommended_odds="")])
    with pytest.raises(RuntimeError, match="0 candidate rows"):
        exp.export_nba_pack(src, out, DATE, now=NOW, fail_on_empty=True)
    assert _read(out) == []
    assert json.loads(out.with_suffix(".status.json").read_text())["status"] == "awaiting_odds"


@pytest.mark.parametrize("bad_edge", ["-9.0", "7.5%", "nan", "inf", ""])
def test_min_edge_excludes_negative_nonfinite_and_unreadable_edges(tmp_path, bad_edge):
    src, out = tmp_path / "src.csv", tmp_path / "nba_only.csv"
    _write(src, [_row(player="Bad", edge_pct=bad_edge), _row(player="Good")])
    assert exp.export_nba_pack(src, out, DATE, min_edge_pct=5, now=NOW) == 1
    assert [(row["rank"], row["player"]) for row in _read(out)] == [("1", "Good")]


@pytest.mark.parametrize("flags", [[], ["--allow-latest-fallback"], ["--no-allow-latest-fallback"]])
def test_cli_legacy_flags_cannot_publish_another_slate(tmp_path, monkeypatch, flags):
    src, out = tmp_path / "src.csv", tmp_path / "nba_only.csv"
    _write(src, [_row(slate_date="2026-11-22")])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "export_nba_extra_pack.py",
            "--date",
            DATE,
            "--src",
            str(src),
            "--out",
            str(out),
            "--no-auto-refresh",
            *flags,
        ],
    )
    assert exp.main() == 0
    assert _read(out) == []


def test_missing_source_publishes_explicit_awaiting_odds(tmp_path):
    out = tmp_path / "nba_only.csv"
    assert exp.export_nba_pack(None, out, DATE, now=NOW) == 0
    assert json.loads(out.with_suffix(".status.json").read_text())["actionable_rows"] == 0


def test_quote_timestamp_is_preserved_for_destination_revalidation(tmp_path):
    src, out = tmp_path / "src.csv", tmp_path / "nba_only.csv"
    _write(src, [_row()])
    assert exp.export_nba_pack(src, out, DATE, now=NOW) == 1
    assert _read(out)[0]["quote_timestamp"] == "2026-11-23T11:45:00Z"
    assert exp.publication_eligible(_read(out)[0], DATE, NOW)
