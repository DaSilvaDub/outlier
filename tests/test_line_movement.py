import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from outlier_scrapers.api import AuthRequiredError, OutlierApiError
from outlier_scrapers.line_movement import (
    StalePropsError,
    build_line_movement_payload,
    export_line_movement_for_league,
    normalize_ev_records,
    normalize_market_detail,
)


def _game_market_detail(market_id="gm1"):
    """Games market-detail payload: team sides (HOME/AWAY) and odds as a list."""
    return {
        "market": {
            "eventId": "e1",
            "marketId": market_id,
            "leagueId": "MLB",
            "marketType": "GAMELINE",
            "proposition": "MONEYLINE",
            "label": "Moneyline",
            "isActive": True,
            "outcomes": [
                {
                    "outcomeId": f"{market_id}-home",
                    "position": "HOME",
                    "odds": [{"book": "DraftKings", "american": -150, "decimal": 1.67}],
                    "primary": True,
                },
                {
                    "outcomeId": f"{market_id}-away",
                    "position": "AWAY",
                    "odds": [{"book": "DraftKings", "american": 130, "decimal": 2.3}],
                    "primary": True,
                },
            ],
            "evOutcomes": [
                {
                    "outcomeId": f"{market_id}-home",
                    "calculatedEV": {
                        "AVERAGE": {"noVigOdds": {"american": "-145", "decimal": 1.69}, "ev": 0.05, "kelly": 0.02}
                    },
                    "deVigOdds": {"american": "-145", "decimal": 1.69},
                    "vig": 0.02,
                    "width": 0.03,
                    "books": {"DRAFTKINGS": {"american": "-150", "decimal": 1.67, "book": {"name": "DraftKings"}}},
                }
            ],
        },
        "marketHistory": {
            "marketMovements": [
                {
                    "updated": "2026-06-20T10:00:00Z",
                    "movementTypes": ["ODDS"],
                    "value": {
                        "HOME": {"odds": "-150", "decimalOdds": 1.67},
                        "AWAY": {"odds": "130", "decimalOdds": 2.3},
                    },
                }
            ]
        },
    }


def test_build_line_movement_payload_games_keeps_team_sides():
    """In games mode, moneyline/spread (HOME/AWAY/DRAW) sides must survive in
    both the movement records and the EV records (Finding #1)."""
    result = build_line_movement_payload(
        league="MLB",
        market_payloads=[_game_market_detail()],
        props_context={},
        source_url_template="t",
        props_latest="x",
        source="games",
    )
    sides = {r["side"] for r in result["records"]}
    assert {"HOME", "AWAY"} <= sides
    home = next(r for r in result["records"] if r["side"] == "HOME")
    assert home["current_odds"] == -150
    assert "HOME" in {r["side"] for r in result["ev_records"]}


def _market_detail(*, market_id="m1", market_history=True, ev_outcomes=False):
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
                    "outcomeId": f"{market_id}-over",
                    "position": "OVER",
                    "line": 1.5,
                    "odds": "-120",
                    "primary": True,
                    "books": ["DRAFTKINGS", "FANDUEL"],
                },
                {
                    "outcomeId": f"{market_id}-under",
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
    if ev_outcomes:
        payload["market"]["evOutcomes"] = [
            {
                "outcomeId": f"{market_id}-over",
                "calculatedEV": {
                    "AVERAGE": {"noVigOdds": {"american": "-115", "decimal": 1.86}, "ev": 0.125, "kelly": 0.13},
                    "MULTIPLICATIVE": {"noVigOdds": {"american": "-120"}, "ev": 0.11, "kelly": 0.115},
                },
                "deVigOdds": {"american": "-115", "decimal": 1.86, "fraction": "20/23"},
                "vig": 0.02,
                "width": 0.04,
                "books": {
                    "DRAFTKINGS": {
                        "american": "+110",
                        "decimal": 2.1,
                        "state": "NY",
                        "maxBet": 250.0,
                        "book": {"name": "DraftKings"},
                    },
                    "FANDUEL": {
                        "american": "-105",
                        "decimal": 1.95,
                        "state": "NJ",
                        "book": {"name": "FanDuel"},
                    },
                },
            },
            {
                "outcomeId": f"{market_id}-under",
                "calculatedEV": {
                    "ADDITIVE": {"ev": 0.03, "kelly": 0.035},
                },
                "deVigOdds": {"american": "+110", "decimal": 2.1, "fraction": "11/10"},
                "vig": 0.01,
                "width": 0.02,
                "books": {},
            },
        ]
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
    assert over["ev_available"] is False
    assert over["ev_book_count"] == 0


def test_normalize_market_detail_maps_ev_outcomes_to_side_rows():
    rows = normalize_market_detail(league="MLB", payload=_market_detail(ev_outcomes=True))

    over = next(row for row in rows if row["side"] == "OVER")
    under = next(row for row in rows if row["side"] == "UNDER")
    assert over["outcome_id"] == "m1-over"
    assert over["ev_available"] is True
    assert over["ev_outcome_id"] == "m1-over"
    assert over["ev_book_count"] == 2
    assert over["ev_books"] == ["DraftKings", "FanDuel"]
    assert over["ev_calculated_ev_pct"] == 12.5
    assert over["ev_calculated_ev_method"] == "AVERAGE"
    assert over["ev_kelly_pct"] == 13.0
    assert over["ev_devig_odds"] == -115
    assert over["ev_devig_decimal"] == 1.86
    assert over["ev_vig_pct"] == 2.0
    assert over["ev_width_pct"] == 4.0
    assert over["sport_context"]["selected_ev_method"] == "AVERAGE"
    assert over["sport_context"]["calculated_ev_methods"]["MULTIPLICATIVE"]["ev"] == 0.11
    assert under["ev_available"] is True
    assert under["ev_calculated_ev_method"] == "ADDITIVE"
    assert under["ev_book_count"] == 0
    assert under["ev_books"] == []


def test_normalize_market_detail_matches_ev_by_unique_side_when_outcome_id_missing():
    payload = _market_detail(ev_outcomes=True)
    for outcome in payload["market"]["outcomes"]:
        outcome.pop("outcomeId", None)
    payload["market"]["evOutcomes"] = [
        {
            "position": "OVER",
            "calculatedEV": {
                "AVERAGE": {"noVigOdds": {"american": "-110", "decimal": 1.91}, "ev": 0.07, "kelly": 0.08}
            },
            "vig": 0.01,
            "width": 0.03,
            "books": {
                "DRAFTKINGS": {
                    "american": "+105",
                    "decimal": 2.05,
                    "state": "NY",
                    "book": {"name": "DraftKings"},
                }
            },
        }
    ]

    rows = normalize_market_detail(league="MLB", payload=payload)
    ev_records = normalize_ev_records(league="MLB", payload=payload)

    over = next(row for row in rows if row["side"] == "OVER")
    under = next(row for row in rows if row["side"] == "UNDER")
    assert over["outcome_id"] is None
    assert over["ev_available"] is True
    assert over["ev_calculated_ev_pct"] == 7.0
    assert over["ev_calculated_ev_method"] == "AVERAGE"
    assert over["ev_devig_odds"] == -110
    assert over["ev_book_count"] == 1
    assert under["ev_available"] is False
    assert len(ev_records) == 1
    assert ev_records[0]["side"] == "OVER"
    assert ev_records[0]["current_line"] == 1.5
    assert ev_records[0]["sport_context"]["calculated_ev_methods"]["AVERAGE"]["kelly"] == 0.08


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
    assert over["ev_available"] is False


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
    assert status["markets_with_ev_count"] == 0
    assert status["ev_outcome_count"] == 0
    assert status["ev_record_count"] == 0
    normalized = json.loads(Path(status["normalized_latest"]).read_text(encoding="utf-8"))
    assert normalized["data_contract"]["version"] == "1.2"
    assert normalized["data_contract"]["row_grain"] == "one row per market_id+side"
    assert normalized["data_contract"]["secondary_record_arrays"]["ev_records"].startswith("one row")
    assert normalized["markets_without_records"] == []
    assert normalized["ev_records"] == []
    assert normalized["ev_record_count"] == 0
    assert {row["side"] for row in normalized["records"]} == {"OVER", "UNDER"}
    assert Path(status["raw_latest"]).exists()


def test_export_line_movement_writes_ev_records_and_status(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    _write_props_latest(tmp_path, generated_at=FRESH_PROPS_GENERATED_AT)

    status = export_line_movement_for_league(
        FakeClient({"m1": _market_detail(ev_outcomes=True)}),
        "MLB",
        now=FRESH_NOW,
    )

    assert status["status"] == "ok"
    assert status["markets_with_ev_count"] == 1
    assert status["ev_outcome_count"] == 2
    assert status["ev_record_count"] == 3
    normalized = json.loads(Path(status["normalized_latest"]).read_text(encoding="utf-8"))
    assert normalized["markets_with_ev_count"] == 1
    assert normalized["ev_outcome_count"] == 2
    assert normalized["ev_record_count"] == 3
    assert len(normalized["ev_records"]) == 3

    over_dk = next(row for row in normalized["ev_records"] if row["side"] == "OVER" and row["book"] == "DraftKings")
    assert over_dk["market_id"] == "m1"
    assert over_dk["outcome_id"] == "m1-over"
    assert over_dk["calculated_ev_pct"] == 12.5
    assert over_dk["calculated_ev_method"] == "AVERAGE"
    assert over_dk["kelly_pct"] == 13.0
    assert over_dk["devig_odds"] == -115
    assert over_dk["devig_decimal"] == 1.86
    assert over_dk["sport_context"]["selected_ev_method"] == "AVERAGE"
    assert over_dk["sport_context"]["calculated_ev_methods"]["MULTIPLICATIVE"]["ev"] == 0.11
    assert over_dk["book_odds"] == 110
    assert over_dk["book_decimal_odds"] == 2.1
    assert over_dk["max_bet"] == 250.0

    under_no_book = next(row for row in normalized["ev_records"] if row["side"] == "UNDER")
    assert under_no_book["book"] is None
    assert under_no_book["book_odds"] is None
    assert under_no_book["calculated_ev_pct"] == 3.0
    assert under_no_book["calculated_ev_method"] == "ADDITIVE"


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


def test_export_line_movement_mop_up_retries_residual_403_for_bounded_rounds(
    tmp_path, monkeypatch
):
    from outlier_scrapers import line_movement as line_movement_mod
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    sleeps: list[float] = []
    monkeypatch.setattr(line_movement_mod.time, "sleep", sleeps.append)
    _write_props_latest_multi(tmp_path)
    client = SequenceClient(
        {
            "m1": [_market_detail(market_id="m1")],
            "m2": [
                OutlierApiError("HTTP 403 for market m2"),
                OutlierApiError("HTTP 403 for market m2"),
                OutlierApiError("HTTP 403 for market m2"),
                _market_detail(market_id="m2"),
            ],
        }
    )

    status = export_line_movement_for_league(
        client,
        "MLB",
        workers=2,
        retry_403_cooldown_seconds=2.5,
        retry_403_max_rounds=3,
    )

    assert status["status"] == "ok"
    assert status["retry_403_recovered"] == 1
    assert status["retry_403_residual_errors"] == 0
    assert status["retry_403_max_rounds"] == 3
    assert status["retry_403_rounds_attempted"] == 3
    assert client.calls["m2"] == 4
    assert sleeps == [2.5, 2.5, 2.5]


def test_export_line_movement_mop_up_stops_at_round_cap_when_still_failing(
    tmp_path, monkeypatch
):
    from outlier_scrapers import line_movement as line_movement_mod
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(line_movement_mod.time, "sleep", lambda *_: None)
    _write_props_latest_multi(tmp_path)
    persistent_403 = OutlierApiError("HTTP 403 for market m2")
    client = SequenceClient(
        {
            "m1": [_market_detail(market_id="m1")],
            # first pass + 3 retry rounds, all 403, never recovers
            "m2": [persistent_403, persistent_403, persistent_403, persistent_403],
        }
    )

    status = export_line_movement_for_league(
        client,
        "MLB",
        workers=2,
        retry_403_cooldown_seconds=0,
        retry_403_max_rounds=3,
    )

    assert status["status"] == "partial"
    assert status["retry_403_rounds_attempted"] == 3  # capped, no 4th retry attempt
    assert status["retry_403_recovered"] == 0
    assert status["retry_403_residual_errors"] == 1
    assert client.calls["m2"] == 4  # 1 first pass + 3 retry rounds, then stop


def test_export_line_movement_mop_up_disabled_via_zero_max_rounds(tmp_path, monkeypatch):
    from outlier_scrapers import line_movement as line_movement_mod
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    sleeps: list[float] = []
    monkeypatch.setattr(line_movement_mod.time, "sleep", sleeps.append)
    _write_props_latest_multi(tmp_path)
    client = SequenceClient(
        {
            "m1": [_market_detail(market_id="m1")],
            "m2": [OutlierApiError("HTTP 403 for market m2")],
        }
    )

    status = export_line_movement_for_league(
        client,
        "MLB",
        workers=2,
        retry_403_cooldown_seconds=15,  # nonzero, to prove no cooldown sleep fires
        retry_403_max_rounds=0,
    )

    assert status["status"] == "partial"
    assert status["retry_403_rounds_attempted"] == 0
    assert status["retry_403_residual_errors"] == 1
    assert status["retry_403_recovered"] == 0
    assert client.calls["m2"] == 1  # first pass only, no retry attempt
    assert sleeps == []  # loop body never ran, so cooldown never slept


def test_export_line_movement_mop_up_stops_when_403_becomes_404(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    _write_props_latest_multi(tmp_path)
    client = SequenceClient(
        {
            "m1": [_market_detail(market_id="m1")],
            "m2": [
                OutlierApiError("HTTP 403 for market m2"),
                OutlierApiError("HTTP 404 for market m2"),
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
    assert status["retry_403_recovered"] == 0
    assert status["retry_403_residual_errors"] == 1
    assert status["retry_403_rounds_attempted"] == 1
    assert client.calls["m2"] == 2
    normalized = json.loads(Path(status["normalized_latest"]).read_text(encoding="utf-8"))
    assert normalized["fetch_errors"] == [
        {
            "market_id": "m2",
            "status": "error",
            "error": "HTTP 404 for market m2",
            "http_status": 404,
        }
    ]


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


def test_main_error_path_writes_status_without_nameerror(tmp_path, monkeypatch):
    """A failing export must return 1 and write the status report; the except
    blocks previously raised a secondary NameError on bare ``source`` (Finding #3)."""
    import outlier_scrapers.line_movement as lm
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(lm, "OutlierApiClient", lambda *a, **k: object())

    def boom(*a, **k):
        raise FileNotFoundError("missing props")

    monkeypatch.setattr(lm, "export_line_movement_for_league", boom)

    rc = lm.main(["--league", "MLB", "--source", "games"])

    assert rc == 1
    reports = list(tmp_path.rglob("games_line_movement_status_latest.json"))
    assert reports, "status report must be written"
    data = json.loads(reports[0].read_text(encoding="utf-8"))
    assert data["status"] == "error"


def test_main_auth_error_path_writes_status_without_nameerror(tmp_path, monkeypatch):
    """AuthRequiredError path must also avoid the bare ``source`` NameError."""
    import outlier_scrapers.line_movement as lm
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(lm, "OutlierApiClient", lambda *a, **k: object())

    def boom(*a, **k):
        raise AuthRequiredError("denied")

    monkeypatch.setattr(lm, "export_line_movement_for_league", boom)

    rc = lm.main(["--league", "MLB", "--source", "games"])

    assert rc == 1
    reports = list(tmp_path.rglob("games_line_movement_status_latest.json"))
    assert reports, "status report must be written"
    assert json.loads(reports[0].read_text(encoding="utf-8"))["status"] == "auth_required"
