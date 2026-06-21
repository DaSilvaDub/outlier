import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from outlier_scrapers.api import AuthRequiredError, OutlierApiError
from outlier_scrapers.line_movement import (
    StalePropsError,
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


class CountingClient(FakeClient):
    def __init__(self, payloads):
        super().__init__(payloads)
        self.fetch_count = 0

    def fetch_market(self, market_id):
        self.fetch_count += 1
        return super().fetch_market(market_id)


class SequenceClient:
    def __init__(self, payloads):
        self.payloads = {market_id: list(results) for market_id, results in payloads.items()}
        self.calls: dict[str, int] = {}

    def fetch_market(self, market_id):
        self.calls[market_id] = self.calls.get(market_id, 0) + 1
        results = self.payloads[market_id]
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def url_for(self, path):
        return f"https://api.test{path}"


def _write_props_latest(tmp_path: Path, league="MLB", generated_at: str | None = None):
    path = tmp_path / "data" / league / "normalized" / f"{league.lower()}_props_latest.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "generated_at": generated_at or datetime.now().astimezone().isoformat(),
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


FRESH_NOW = datetime(2026, 6, 21, 15, 0, tzinfo=timezone.utc)
FRESH_PROPS_GENERATED_AT = "2026-06-21T10:00:00+00:00"
STALE_PROPS_GENERATED_AT = "2026-06-20T11:00:00+00:00"
SLIGHTLY_OLD_PROPS_GENERATED_AT = "2026-06-21T02:59:59+00:00"


def test_export_line_movement_writes_raw_normalized_and_status(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    _write_props_latest(tmp_path, generated_at=FRESH_PROPS_GENERATED_AT)

    status = export_line_movement_for_league(
        FakeClient({"m1": _market_detail()}),
        "MLB",
        now=FRESH_NOW,
    )

    assert status["status"] == "ok"
    assert status["markets_requested"] == 1
    assert status["markets_fetched"] == 1
    assert status["markets_with_records"] == 1
    assert status["markets_without_record_count"] == 0
    assert status["record_count"] == 2
    assert status["props_generated_at"] == FRESH_PROPS_GENERATED_AT
    assert status["props_age_hours"] == 5.0
    assert status["props_is_stale"] is False
    assert status["props_stale_reason"] is None
    normalized = json.loads(Path(status["normalized_latest"]).read_text(encoding="utf-8"))
    assert normalized["data_contract"]["row_grain"] == "one row per market_id+side"
    assert normalized["markets_without_records"] == []
    assert {row["side"] for row in normalized["records"]} == {"OVER", "UNDER"}
    assert Path(status["raw_latest"]).exists()


def test_export_line_movement_warns_and_records_stale_props(tmp_path, monkeypatch, capsys):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    _write_props_latest(tmp_path, generated_at=STALE_PROPS_GENERATED_AT)

    status = export_line_movement_for_league(
        FakeClient({"m1": _market_detail()}),
        "MLB",
        now=FRESH_NOW,
    )

    captured = capsys.readouterr()
    assert "WARNING: MLB props_latest is stale" in captured.err
    assert status["status"] == "ok"
    assert status["props_generated_at"] == STALE_PROPS_GENERATED_AT
    assert status["props_age_hours"] == 28.0
    assert status["props_is_stale"] is True
    assert "props date 2026-06-20 != today 2026-06-21" in status["props_stale_reason"]
    assert "props_freshness_warning" in status


def test_export_line_movement_warns_when_props_are_just_over_age_limit(
    tmp_path,
    monkeypatch,
    capsys,
):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    _write_props_latest(tmp_path, generated_at=SLIGHTLY_OLD_PROPS_GENERATED_AT)

    status = export_line_movement_for_league(
        FakeClient({"m1": _market_detail()}),
        "MLB",
        now=FRESH_NOW,
    )

    captured = capsys.readouterr()
    assert "WARNING: MLB props_latest is stale" in captured.err
    assert status["props_is_stale"] is True
    assert "props age" in status["props_stale_reason"]


def test_export_line_movement_require_fresh_props_hard_stops(tmp_path, monkeypatch, capsys):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    _write_props_latest(tmp_path, generated_at=STALE_PROPS_GENERATED_AT)
    client = CountingClient({"m1": _market_detail()})

    with pytest.raises(StalePropsError):
        export_line_movement_for_league(
            client,
            "MLB",
            require_fresh_props=True,
            now=FRESH_NOW,
        )

    captured = capsys.readouterr()
    assert "WARNING: MLB props_latest is stale" in captured.err
    assert client.fetch_count == 0
    report = json.loads(
        (tmp_path / "data" / "MLB" / "reports" / "line_movement_status_latest.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["status"] == "stale_props"
    assert report["props_generated_at"] == STALE_PROPS_GENERATED_AT
    assert report["props_age_hours"] == 28.0
    assert report["props_is_stale"] is True
    assert not list((tmp_path / "data").glob("**/*line_movement_latest.json"))


def _write_props_latest_multi(tmp_path: Path, league="MLB", generated_at: str | None = None):
    path = tmp_path / "data" / league / "normalized" / f"{league.lower()}_props_latest.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "generated_at": generated_at or datetime.now().astimezone().isoformat(),
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


def test_export_line_movement_threaded_records_fetch_errors_as_partial(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    _write_props_latest_multi(tmp_path)
    client = FakeClient(
        {
            "m1": _market_detail(market_id="m1"),
            "m2": OutlierApiError("HTTP 403 for https://api.test/sportsdata/markets/m2: body_len=0"),
        }
    )

    status = export_line_movement_for_league(
        client,
        "MLB",
        workers=2,
        retry_403_cooldown_seconds=0,
    )

    assert status["status"] == "partial"
    assert status["markets_requested"] == 2
    assert status["markets_fetched"] == 1
    assert status["fetch_error_count"] == 1
    assert status["retry_403_markets"] == 1
    assert status["retry_403_recovered"] == 0
    assert status["retry_403_residual_errors"] == 1
    normalized = json.loads(Path(status["normalized_latest"]).read_text(encoding="utf-8"))
    assert normalized["fetch_errors"][0]["market_id"] == "m2"
    assert normalized["fetch_errors"][0]["http_status"] == 403
    assert normalized["records"]


def test_export_line_movement_mop_up_recovers_first_pass_403(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    _write_props_latest_multi(tmp_path)
    client = SequenceClient(
        {
            "m1": [_market_detail(market_id="m1")],
            "m2": [
                OutlierApiError(
                    "HTTP 403 for https://api.test/sportsdata/markets/m2: body_len=0"
                ),
                _market_detail(market_id="m2"),
            ],
        }
    )

    status = export_line_movement_for_league(
        client,
        "MLB",
        workers=2,
        retry_403_cooldown_seconds=0,
    )

    assert status["status"] == "ok"
    assert status["markets_requested"] == 2
    assert status["markets_fetched"] == 2
    assert status["fetch_error_count"] == 0
    assert status["retry_403_markets"] == 1
    assert status["retry_403_recovered"] == 1
    assert status["retry_403_residual_errors"] == 0
    assert client.calls["m2"] == 2
    raw = json.loads(Path(status["raw_latest"]).read_text(encoding="utf-8"))
    assert raw["retry_403_markets"] == 1
    assert raw["retry_403_recovered"] == 1
    normalized = json.loads(Path(status["normalized_latest"]).read_text(encoding="utf-8"))
    assert normalized["fetch_errors"] == []
    assert {row["market_id"] for row in normalized["records"]} == {"m1", "m2"}


def test_export_line_movement_can_disable_403_mop_up(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    _write_props_latest_multi(tmp_path)
    client = SequenceClient(
        {
            "m1": [_market_detail(market_id="m1")],
            "m2": [
                OutlierApiError(
                    "HTTP 403 for https://api.test/sportsdata/markets/m2: body_len=0"
                )
            ],
        }
    )

    status = export_line_movement_for_league(
        client,
        "MLB",
        workers=2,
        retry_failed_403=False,
        retry_403_cooldown_seconds=0,
    )

    assert status["status"] == "partial"
    assert status["fetch_error_count"] == 1
    assert status["retry_403_enabled"] is False
    assert status["retry_403_markets"] == 0
    assert client.calls["m2"] == 1


def test_export_line_movement_mop_up_does_not_retry_404(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    _write_props_latest_multi(tmp_path)
    client = SequenceClient(
        {
            "m1": [_market_detail(market_id="m1")],
            "m2": [
                OutlierApiError(
                    "HTTP 404 for https://api.test/sportsdata/markets/m2: body_len=0"
                )
            ],
        }
    )

    status = export_line_movement_for_league(
        client,
        "MLB",
        workers=2,
        retry_403_cooldown_seconds=0,
    )

    assert status["status"] == "partial"
    assert status["fetch_error_count"] == 1
    assert status["retry_403_markets"] == 0
    assert status["retry_403_recovered"] == 0
    assert client.calls["m2"] == 1
    normalized = json.loads(Path(status["normalized_latest"]).read_text(encoding="utf-8"))
    assert normalized["fetch_errors"][0]["http_status"] == 404


def test_export_line_movement_auth_error_writes_no_success_artifacts(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    _write_props_latest(tmp_path)

    with pytest.raises(AuthRequiredError):
        export_line_movement_for_league(FakeClient({"m1": AuthRequiredError("denied")}), "MLB")

    assert not list((tmp_path / "data").glob("**/*line_movement_latest.json"))
