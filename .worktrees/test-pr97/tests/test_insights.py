import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from outlier_scrapers.api import OutlierApiClient
from outlier_scrapers.insights import export_insights_for_league, normalize_insights
from outlier_scrapers.registry import get_sport_config


def _insight(
    *,
    insight_id="i1",
    proposition="HITS",
    market_label="Aaron Judge - Hits",
    prop_label="Hits",
    position="OVER",
    subject="player",
    line=1.5,
    hit=0.73,
    last_n=(True, False, True, True, True, True, False, True, True, True, True),
    away_alias="NYY",
    away_team_id="10",
    home_alias="BOS",
    home_team_id="20",
):
    return {
        "insightId": insight_id,
        "subjectType": subject,
        "eventId": "e1",
        "marketId": "m1",
        "marketOutcomeId": "mo1",
        "playerId": "p1",
        "playerPosition": "RF",
        "marketLabel": market_label,
        "propLabel": prop_label,
        "proposition": proposition,
        "position": position,
        "line": line,
        "hitRate": hit,
        "relevancy": 95,
        "lastN": list(last_n),
        "bestOdds": "-120",
        "text": "Aaron Judge has hit the over in 9 of his last 11.",
        "outcomeLabel": "Over 1.5",
        "lineLabel": "1.5",
        "marketType": "PLAYER_PROP",
        "marketActive": True,
        "includeOvertime": False,
        "splits": [{"k": "v"}],
        "playerData": {"items": []},
        "event": {
            "eventId": "e1",
            "away": {"alias": away_alias, "teamId": away_team_id},
            "home": {"alias": home_alias, "teamId": home_team_id},
            "scheduledTime": "2026-06-21T18:00:00Z",
        },
        "teamId": away_team_id,
    }


def test_normalize_insights_full_game_and_scoped():
    payload = {"insights": [_insight(), _insight(insight_id="i2", market_label="Aaron Judge - 1st Inning Hits")]}
    rows = normalize_insights(payload, get_sport_config("MLB"))
    assert len(rows) == 2
    by_id = {r["insight_id"]: r for r in rows}

    fg = by_id["i1"]
    assert fg["market"] == "H"
    assert fg["scope"] == "full_game"
    assert fg["player_raw"] == "Aaron Judge"
    assert fg["team"] == "NYY" and fg["opponent"] == "BOS"
    assert fg["matchup_raw"] == "NYY @ BOS"
    assert fg["side"] == "OVER"
    assert fg["hit_rate_pct"] == 73.0
    assert fg["last_n_record"] == "9/11"
    assert fg["relevancy"] == 95
    assert fg["best_odds"] == -120

    scoped = by_id["i2"]
    assert scoped["market"] is None
    assert scoped["market_raw"] == "1st Inning Hits"
    assert scoped["scope"] == "first_inning"


def test_insights_dedupe_by_insight_id():
    payload = {"insights": [_insight(), _insight()]}
    rows = normalize_insights(payload, get_sport_config("MLB"))
    assert len(rows) == 1


def test_team_insight_resolves_team_without_player():
    team_insight = _insight(insight_id="t1", subject="team", market_label="Yankees Team Total")
    team_insight["playerId"] = None
    rows = normalize_insights({"insights": [team_insight]}, get_sport_config("MLB"))
    row = rows[0]
    assert row["subject_type"] == "team"
    assert row["team"] == "NYY"
    assert row["player_raw"] is None  # no " - " in label -> no player


def test_player_name_falls_back_to_payload_name_fields():
    insight = _insight(market_label="Hits", prop_label="Hits")
    insight["playerName"] = "Aaron Judge"

    rows = normalize_insights({"insights": [insight]}, get_sport_config("MLB"))

    assert rows[0]["player"] == "Aaron Judge"
    assert rows[0]["player_raw"] == "Aaron Judge"


def test_insight_known_full_game_markets_normalize():
    payload = {
        "insights": [
            _insight(
                insight_id="m1",
                subject="team",
                proposition="TOTAL",
                prop_label="Runs",
                market_label="Total O/U",
            ),
            _insight(
                insight_id="m2",
                proposition="FANTASY_SCORE_UD",
                prop_label="Fantasy Score (UD)",
                market_label="Sabrina Ionescu - Fantasy Score (UD)",
            ),
        ]
    }

    mlb_row, wnba_row = (
        normalize_insights({"insights": [payload["insights"][0]]}, get_sport_config("MLB"))[0],
        normalize_insights({"insights": [payload["insights"][1]]}, get_sport_config("WNBA"))[0],
    )
    assert mlb_row["market"] == "TOTAL"
    assert mlb_row["market_raw"] == "Total"
    assert wnba_row["market"] == "FANTASY_UD"
    assert wnba_row["market_raw"] == "Fantasy Score (UD)"


def test_wnba_toronto_tempo_team_alias_resolves():
    insight = _insight(
        market_label="Marina Mabrey - Three Pointers",
        proposition="THREE_POINTERS",
        prop_label="Three Pointers",
        away_alias="TOR",
        away_team_id="30",
        home_alias="ATL",
        home_team_id="20",
    )
    rows = normalize_insights({"insights": [insight]}, get_sport_config("WNBA"))
    row = rows[0]
    assert row["team"] == "TOR"
    assert row["team_raw"] == "TOR"
    assert row["opponent"] == "ATL"


class FakeClient:
    def __init__(self, payload):
        self.payload = payload

    def fetch_insights(self, league):
        return self.payload

    def url_for(self, path):
        return f"https://api.test{path}"


def test_export_insights_writes_outputs(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    payload = {
        "insights": [_insight(), _insight(insight_id="i2", subject="team", market_label="Yankees Team Total")],
        "_page_summary": {"merged_records": 2, "pages_fetched": 1, "stopped_reason": "single_page"},
    }
    status = export_insights_for_league(FakeClient(payload), "MLB")

    assert status["status"] == "ok"
    assert status["record_count"] == 2
    assert status["subject_type_counts"].get("player") == 1
    assert status["subject_type_counts"].get("team") == 1
    normalized = json.loads(Path(status["normalized_latest"]).read_text(encoding="utf-8"))
    assert normalized["data_contract"]["dedupe_key"] == "league+insight_id"
    assert normalized["pagination"]["pages_fetched"] == 1
    assert Path(status["raw_latest"]).exists()


def test_fetch_insights_paginates_top_level_token():
    client = OutlierApiClient(storage_state={})

    def fetch(path):
        token = parse_qs(urlparse("http://x" + path).query).get("pageToken", [None])[0]
        if token is None:
            return {"insights": [{"insightId": "a"}], "nextPageToken": "t2"}
        if token == "t2":
            return {"insights": [{"insightId": "b"}], "nextPageToken": "t3"}
        if token == "t3":
            return {"insights": [{"insightId": "c"}]}  # no token -> last page
        return {"insights": []}

    client.fetch_json = fetch  # type: ignore[assignment]
    out = client.fetch_insights("MLB")
    assert [i["insightId"] for i in out["insights"]] == ["a", "b", "c"]
    assert out["_page_summary"]["pages_fetched"] == 3
    assert out["_page_summary"]["param_used"] == "pageToken"
