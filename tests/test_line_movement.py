import json
from pathlib import Path

import pytest

from outlier_scrapers.api import AuthRequiredError
from outlier_scrapers.line_movement import (
    export_line_movement_for_league,
    normalize_market_detail,
)


def _market_detail(*, market_id="m1", market_history=True):
    payload = {
        "market": {
            "eventId": "e1",
            "marketId": market_id,
            "label": "Aaron Judge - Hits",
            "leagueId": "MLB",
            "marketType": "PLAYER_PROP",
            "propType": "PLAYER",
            "proposition": "HITS",
            "isActive": True,
            "player": {"fullName": "Aaron Judge", "playerId": "p1", "teamId": "nyy-id"},
            "outcomes": [
                {
                    "position": "OVER",
                    "line": 1.5,
                    "odds": "-120",
                    "primary": True,
                    "books": ["DRAFTKINGS", "FANDUEL"],
                },
                {
                    "position": "UNDER",
                    "line": 1.5,
                    "odds": "+100",
                    "primary": True,
                    "books": ["DRAFTKINGS"],
                },
            ],
        },
        "marketHistory": None,
    }
    if market_history:
        payload["marketHistory"] = {
            "marketId": market_id,
            "eventId": "e1",
            "marketActive": True,
            "marketMovements": [
                {
                    "updated": "2026-06-20T10:00:00Z",
                    "movementTypes": ["LINE"],
                    "value": {
                        "OVER": {"line": 0.5, "odds": "-110", "decimalOdds": 1.91},
                        "UNDER": {"line": 0.5, "odds": "-110", "decimalOdds": 1.91},
                    },
                },
                {
                    "updated": "2026-06-20T11:00:00Z",
                    "movementTypes": ["ODDS"],
                    "value": {
                        "OVER": {"line": 1.5, "odds": "-115", "decimalOdds": 1.87},
                        "UNDER": {"line": 1.5, "odds": "-105", "decimalOdds": 1.95},
                    },
                },
            ],
        }
    return payload


def test_normalize_market_detail_exports_open_current_and_delta():
    rows = normalize_market_detail(
        league="MLB",
        payload=_market_detail(),
        props_context={
            "team": "NYY",
            "team_raw": "NYY",
            "opponent": "BOS",
            "opponent_raw": "BOS",
            "matchup": "NYY @ BOS",
            "matchup_raw": "NYY @ BOS",
        },
    )
    assert len(rows) == 2
    over = next(row for row in rows if row["side"] == "OVER")
    assert over["market"] == "H"
    assert over["player_raw"] == "Aaron Judge"
    assert over["current_line"] == 1.5
    assert over["current_odds"] == -120  # live best book price
    assert over["open_line"] == 0.5
    assert over["open_odds"] == -110
    assert over["latest_history_line"] == 1.5
    assert over["latest_history_odds"] == -115  # last consensus odds
    # Deltas follow the consensus history path (latest - open), not best-book.
    assert over["line_delta_from_open"] == 1.0
    assert over["odds_delta_from_open"] == -5
    assert over["movement_count"] == 2
    assert over["movement_types"] == ["LINE", "ODDS"]
    assert over["latest_movement_at"] == "2026-06-20T11:00:00Z"
    assert over["history_available"] is True
    assert over["books"] == ["DraftKings", "FanDuel"]


def test_normalize_market_detail_gates_scoped_market_to_none():
    payload = _market_detail()
    payload["market"]["label"] = "Aaron Judge - 1st Inning Hits"
    rows = normalize_market_detail(league="MLB", payload=payload)
    over = next(row for row in rows if row["side"] == "OVER")
    assert over["market"] is None  # scoped markets are not canonicalized
    assert over["market_raw"] == "1st Inning Hits"  # raw preserved
    assert over["scope"] == "first_inning"


def test_normalize_market_detail_preserves_market_with_no_history():
    rows = normalize_market_detail(league="WNBA", payload=_market_detail(market_history=False))
    over = next(row for row in rows if row["side"] == "OVER")
    assert over["history_available"] is False
    assert over["movement_count"] == 0
    assert over["open_line"] is None
    assert over["open_odds"] is None
    assert over["current_line"] == 1.5
    assert over["current_odds"] == -120


class FakeClient:
    def __init__(self, payloads):
        self.payloads = payloads

    def fetch_market(self, market_id):
        result = self.payloads[market_id]
        if isinstance(result, Exception):
            raise result
        return result

    def url_for(self, path):
        return f"https://api.test{path}"


def _write_props_latest(tmp_path: Path, league="MLB"):
    path = tmp_path / "data" / league / "normalized" / f"{league.lower()}_props_latest.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "league": league,
                        "event_id": "e1",
                        "market_id": "m1",
                        "player": "Aaron Judge",
                        "player_raw": "Aaron Judge",
                        "team": "NYY",
                        "team_raw": "NYY",
                        "opponent": "BOS",
                        "opponent_raw": "BOS",
                        "matchup": "NYY @ BOS",
                        "matchup_raw": "NYY @ BOS",
                        "market": "H",
                        "market_raw": "Hits",
                    },
                    {"league": league, "market_id": "m1"},  # duplicate market ID
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def test_export_line_movement_writes_raw_normalized_and_status(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    _write_props_latest(tmp_path)

    status = export_line_movement_for_league(FakeClient({"m1": _market_detail()}), "MLB")

    assert status["status"] == "ok"
    assert status["markets_requested"] == 1
    assert status["markets_fetched"] == 1
    assert status["markets_with_records"] == 1
    assert status["markets_without_record_count"] == 0
    assert status["record_count"] == 2
    normalized = json.loads(Path(status["normalized_latest"]).read_text(encoding="utf-8"))
    assert normalized["data_contract"]["row_grain"] == "one row per market_id+side"
    assert normalized["markets_without_records"] == []
    assert {row["side"] for row in normalized["records"]} == {"OVER", "UNDER"}
    assert Path(status["raw_latest"]).exists()


def _write_props_latest_multi(tmp_path: Path, league="MLB"):
    path = tmp_path / "data" / league / "normalized" / f"{league.lower()}_props_latest.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "records": [
                    {"league": league, "event_id": "e1", "market_id": "m1",
                     "player_raw": "Aaron Judge", "matchup_raw": "NYY @ BOS", "market_raw": "Hits"},
                    {"league": league, "event_id": "e1", "market_id": "m2",
                     "player_raw": "Empty Guy", "matchup_raw": "NYY @ BOS", "market_raw": "Hits"},
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def test_export_line_movement_threaded_reports_markets_without_records(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    _write_props_latest_multi(tmp_path)

    # m1 has OVER/UNDER; m2 fetches fine but yields no OVER/UNDER rows.
    empty_market = {"market": {"marketId": "m2", "outcomes": []}, "marketHistory": None}
    client = FakeClient({"m1": _market_detail(market_id="m1"), "m2": empty_market})

    status = export_line_movement_for_league(client, "MLB", workers=2)

    assert status["markets_requested"] == 2
    assert status["markets_fetched"] == 2
    assert status["markets_with_records"] == 1
    assert status["markets_without_record_count"] == 1
    assert status["record_count"] == 2
    normalized = json.loads(Path(status["normalized_latest"]).read_text(encoding="utf-8"))
    assert normalized["markets_without_records"] == ["m2"]


def test_export_line_movement_auth_error_writes_no_success_artifacts(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    _write_props_latest(tmp_path)

    with pytest.raises(AuthRequiredError):
        export_line_movement_for_league(FakeClient({"m1": AuthRequiredError("denied")}), "MLB")

    assert not list((tmp_path / "data").glob("**/*line_movement_latest.json"))
