from outlier_scrapers.normalizer import game_sides, normalize_games
from outlier_scrapers.registry import get_sport_config


def _schedule_e1():
    return {
        "events": [
            {
                "id": "e1",
                "status": "scheduled",
                "home": {"id": "h1", "name": "Yankees"},
                "away": {"id": "a1", "name": "Red Sox"},
            }
        ]
    }


def test_normalize_games_extracts_per_book_odds_from_odds_list():
    """Games API returns per-book prices as odds:[{book, american, decimal}];
    the normalized row must carry them (Finding #2)."""
    config = get_sport_config("MLB")
    events_payloads = [
        {
            "eventId": "e1",
            "markets": [
                {
                    "marketId": "m1",
                    "marketType": "GAMELINE",
                    "proposition": "MONEYLINE",
                    "label": "Moneyline",
                    "outcomes": [
                        {
                            "id": "o_home",
                            "position": "HOME",
                            "odds": [
                                {"book": "DraftKings", "american": -150, "decimal": 1.67},
                                {"book": "FanDuel", "american": -145, "decimal": 1.69},
                            ],
                        },
                        {
                            "id": "o_away",
                            "position": "AWAY",
                            "odds": [{"book": "DraftKings", "american": 130, "decimal": 2.3}],
                        },
                    ],
                }
            ],
        }
    ]
    res = normalize_games(
        config=config,
        schedule_payload=_schedule_e1(),
        events_payloads=events_payloads,
        source_url="api",
    )
    home = next(r for r in res["records"] if r["position"] == "HOME")
    assert len(home["books"]) == 2
    assert {b["odds"] for b in home["books"]} == {-150, -145}
    assert {b["book"] for b in home["books"]} == {"DraftKings", "FanDuel"}


def test_best_american_price_reads_normalized_book_odds_key():
    """cards._best_american_price must read the same book key the normalizer
    emits (Finding #2 second mismatch)."""
    from outlier_scrapers.cards import _best_american_price

    row = {"books": [{"book": "DraftKings", "odds": -150}, {"book": "FanDuel", "odds": -140}]}
    assert _best_american_price(row) == -140  # max (least juice)


def test_game_sides():
    assert set(game_sides("SPREAD")) == {"HOME", "AWAY"}
    assert set(game_sides("MONEYLINE")) == {"HOME", "AWAY"}
    assert set(game_sides("MONEYLINE_THREE_WAY")) == {"HOME", "AWAY", "DRAW"}
    assert set(game_sides("TOTAL")) == {"OVER", "UNDER"}
    assert game_sides("WINNING_MARGIN") == ()


def test_normalize_games_preserves_nway_margin():
    config = get_sport_config("MLB")
    schedule = {
        "events": [
            {
                "id": "e1",
                "status": "scheduled",
                "home": {"id": "h1", "name": "Yankees"},
                "away": {"id": "a1", "name": "Red Sox"},
            }
        ]
    }
    events_payloads = [
        {
            "eventId": "e1",
            "markets": [
                {
                    "marketId": "m1",
                    "marketType": "GAMELINE",
                    "proposition": "WINNING_MARGIN",
                    "label": "Winning Margin",
                    "outcomes": [
                        {"id": "o1", "position": "Home by 1"},
                        {"id": "o2", "position": "Home by 2+"},
                        {"id": "o3", "position": "Away by 1"},
                        {"id": "o4", "position": "Away by 2+"},
                    ],
                }
            ],
        }
    ]
    res = normalize_games(
        config=config, schedule_payload=schedule, events_payloads=events_payloads, source_url="api"
    )
    records = res["records"]
    assert len(records) == 4
    for r in records:
        assert r["market_id"] == "m1"
        assert r["proposition"] == "WINNING_MARGIN"
        assert r["position"] in ["HOME BY 1", "HOME BY 2+", "AWAY BY 1", "AWAY BY 2+"]


def test_normalize_games_team_prop_resolution():
    config = get_sport_config("MLB")
    schedule = {
        "events": [
            {
                "id": "e1",
                "status": "scheduled",
                "home": {"id": "h1", "name": "Yankees"},
                "away": {"id": "a1", "name": "Red Sox"},
            }
        ]
    }
    events_payloads = [
        {
            "eventId": "e1",
            "markets": [
                {
                    "marketId": "m1",
                    "marketType": "TEAM_PROP",
                    "proposition": "RUNS",
                    "teamId": "h1",
                    "outcomes": [
                        {"id": "o1", "position": "OVER", "line": 4.5},
                    ],
                },
                {
                    "marketId": "m2",
                    "marketType": "TEAM_PROP",
                    "proposition": "RUNS",
                    "label": "Red Sox Total Runs",
                    "outcomes": [
                        {"id": "o2", "position": "OVER", "line": 3.5},
                    ],
                },
            ],
        }
    ]
    res = normalize_games(
        config=config, schedule_payload=schedule, events_payloads=events_payloads, source_url="api"
    )
    records = res["records"]
    assert len(records) == 2
    r1 = next(r for r in records if r["market_id"] == "m1")
    r2 = next(r for r in records if r["market_id"] == "m2")
    assert "Yankees" in r1["team_raw"] or r1["team"] == "NYY"
    assert "Red Sox" in r2["team_raw"] or r2["team"] == "BOS"


def test_mlb_non_whitelisted_team_prop_dropped_gameline_preserved():
    """TEAM_PROP outside ALLOWED_MLB_TEAM_PROPS drops; GAMELINE still admitted."""
    config = get_sport_config("MLB")
    schedule = {
        "events": [
            {
                "id": "e1",
                "status": "scheduled",
                "home": {"id": "h1", "name": "Yankees"},
                "away": {"id": "a1", "name": "Red Sox"},
            }
        ]
    }
    events_payloads = [
        {
            "eventId": "e1",
            "markets": [
                {
                    "marketId": "m-team-points",
                    "marketType": "TEAM_PROP",
                    "proposition": "POINTS",
                    "teamId": "h1",
                    "outcomes": [
                        {"id": "o-tp", "position": "OVER", "line": 4.5},
                    ],
                },
                {
                    "marketId": "m-ml",
                    "marketType": "GAMELINE",
                    "proposition": "MONEYLINE",
                    "outcomes": [
                        {"id": "o-ml-h", "position": "HOME"},
                        {"id": "o-ml-a", "position": "AWAY"},
                    ],
                },
                {
                    "marketId": "m-total",
                    "marketType": "GAMELINE",
                    "proposition": "TOTAL",
                    "outcomes": [
                        {"id": "o-tot-o", "position": "OVER", "line": 8.5},
                        {"id": "o-tot-u", "position": "UNDER", "line": 8.5},
                    ],
                },
            ],
        }
    ]
    res = normalize_games(
        config=config, schedule_payload=schedule, events_payloads=events_payloads, source_url="api"
    )
    records = res["records"]
    assert not any(r.get("market_id") == "m-team-points" for r in records)
    assert any(r.get("market_id") == "m-ml" for r in records)
    assert any(r.get("market_id") == "m-total" for r in records)


def test_scope_metadata_preserved():
    config = get_sport_config("MLB")
    schedule = {"events": [{"id": "e1"}]}
    events_payloads = [
        {
            "eventId": "e1",
            "markets": [
                {
                    "marketId": "m1",
                    "marketType": "GAMELINE",
                    "proposition": "SPREAD",
                    "label": "1st Half Spread",
                    "outcomes": [{"id": "o1", "position": "Home"}],
                }
            ],
        }
    ]
    res = normalize_games(
        config=config, schedule_payload=schedule, events_payloads=events_payloads, source_url="api"
    )
    records = res["records"]
    assert len(records) == 1
    assert records[0]["scope"] == "first_half"


def test_build_game_cards_payload_generates_sides(monkeypatch):
    from outlier_scrapers.cards import build_game_cards_payload

    # Mock load_latest to return a realistic game payload
    games_payload = {
        "generated_at": "2026-06-22T22:00:00Z",
        "records": [
            {
                "league": "MLB",
                "event_id": "e1",
                "market_id": "m_spread",
                "outcome_id": "o_home",
                "proposition": "SPREAD",
                "position": "HOME",
                "line": -1.5,
                "best_odds": 150,
                "books": [{"book": "DraftKings", "american": 150}],
                "public_money": {"position": "HOME", "percentage": 60, "money": 55},
            },
            {
                "league": "MLB",
                "event_id": "e1",
                "market_id": "m_spread",
                "outcome_id": "o_away",
                "proposition": "SPREAD",
                "position": "AWAY",
                "line": 1.5,
                "best_odds": -170,
                "books": [{"book": "DraftKings", "american": -170}],
                "public_money": {"position": "AWAY", "percentage": 40, "money": 45},
            },
        ],
        "context": {},
    }

    def mock_load_latest(lg, name):
        if name == "games":
            return games_payload
        return None

    monkeypatch.setattr("outlier_scrapers.cards.load_latest", mock_load_latest)

    payload = build_game_cards_payload("MLB")

    assert len(payload["board_b"]) == 1
    card = payload["board_b"][0]
    assert card["card_id"] == "m_spread"
    assert "HOME" in card["sides"]
    assert "AWAY" in card["sides"]
    assert card["sides"]["HOME"]["line"] == -1.5
    assert card["sides"]["HOME"]["public_money"]["percentage"] == 60


# --------------------------------------------------------------------------- #
# Partial-failure surfacing (Finding #5)
# --------------------------------------------------------------------------- #


def _games_event(scheduled_time, target_date):
    return {
        "id": "e1",
        "eventId": "e1",
        "status": "scheduled",
        "scheduledTime": scheduled_time,
        "home": {"id": "h1", "teamId": "h1", "name": "Yankees"},
        "away": {"id": "a1", "teamId": "a1", "name": "Red Sox"},
    }


class FakeGamesClient:
    def __init__(self, *, markets_error=False, matchup_error=False):
        self.markets_error = markets_error
        self.matchup_error = matchup_error

    def fetch_schedule(self, league_id):
        from datetime import datetime

        local_now = datetime.now().astimezone()
        self._scheduled = local_now.replace(hour=18, minute=0, second=0, microsecond=0).isoformat()
        self.target_date = local_now.date()
        return {"events": [_games_event(self._scheduled, self.target_date)]}

    def fetch_event_matchup(self, event_id):
        from outlier_scrapers.api import OutlierApiError

        if self.matchup_error:
            raise OutlierApiError("HTTP 403 matchup")
        return {"matchupType": "regular"}

    def fetch_event_insights(self, event_id):
        return {"insights": []}

    def fetch_event_markets(self, event_id, market_type):
        from outlier_scrapers.api import OutlierApiError

        if self.markets_error:
            raise OutlierApiError("HTTP 403 markets")
        if market_type == "GAMELINE":
            return {
                "markets": [
                    {
                        "marketId": "m1",
                        "marketType": "GAMELINE",
                        "proposition": "MONEYLINE",
                        "label": "Moneyline",
                        "outcomes": [
                            {
                                "id": "o_home",
                                "position": "HOME",
                                "odds": [{"book": "DraftKings", "american": -150}],
                            },
                            {
                                "id": "o_away",
                                "position": "AWAY",
                                "odds": [{"book": "DraftKings", "american": 130}],
                            },
                        ],
                    }
                ]
            }
        return {"markets": []}

    def fetch_team_injuries(self, league_id, team_id):
        return {"players": []}


def test_games_export_flags_markets_failure_as_error(tmp_path, monkeypatch):
    """A failed markets fetch (the core payload) must surface as status=error
    with the error recorded, not a silent ok (Finding #5)."""
    from outlier_scrapers import paths as paths_mod
    from outlier_scrapers.games import export_games_for_league

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    client = FakeGamesClient(markets_error=True)
    client.fetch_schedule("MLB")  # prime target_date

    status = export_games_for_league(client, "MLB", target_date=client.target_date)

    assert status["status"] == "error"
    assert status["record_count"] == 0
    assert status["fetch_errors"], "markets failure must be recorded"
    assert any(e.get("step") == "markets" for e in status["fetch_errors"])


def test_games_export_flags_partial_on_matchup_failure(tmp_path, monkeypatch):
    """A failed matchup fetch but healthy markets is a partial, not an error or
    a silent ok (Finding #5)."""
    from outlier_scrapers import paths as paths_mod
    from outlier_scrapers.games import export_games_for_league

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    client = FakeGamesClient(matchup_error=True)
    client.fetch_schedule("MLB")

    status = export_games_for_league(client, "MLB", target_date=client.target_date)

    assert status["status"] == "partial"
    assert status["record_count"] >= 2
    assert any(e.get("step") == "matchup" for e in status["fetch_errors"])


# --------------------------------------------------------------------------- #
# Game card identity (Finding #4)
# --------------------------------------------------------------------------- #


def test_game_card_carries_identity_and_computes_best_odds(monkeypatch):
    """A game card must expose its market identity (proposition/event_id/matchup,
    a non-empty group_key) and compute best_odds from per-book odds, using
    realistic normalized rows (no pre-set best_odds)."""
    from outlier_scrapers.cards import build_game_cards_payload

    games_payload = {
        "generated_at": "2026-06-22T22:00:00Z",
        "records": [
            {
                "league": "MLB",
                "event_id": "e1",
                "market_id": "m_ml",
                "outcome_id": "o_home",
                "proposition": "MONEYLINE",
                "position": "HOME",
                "market": "ML",
                "market_raw": "Moneyline",
                "scope": "full_game",
                "matchup": "NYY @ BOS",
                "books": [{"book": "DraftKings", "odds": -150}, {"book": "FanDuel", "odds": -140}],
            },
            {
                "league": "MLB",
                "event_id": "e1",
                "market_id": "m_ml",
                "outcome_id": "o_away",
                "proposition": "MONEYLINE",
                "position": "AWAY",
                "market": "ML",
                "market_raw": "Moneyline",
                "scope": "full_game",
                "matchup": "NYY @ BOS",
                "books": [{"book": "DraftKings", "odds": 130}],
            },
        ],
        "context": {},
    }
    monkeypatch.setattr(
        "outlier_scrapers.cards.load_latest",
        lambda lg, name: games_payload if name == "games" else None,
    )

    payload = build_game_cards_payload("MLB")
    cards = payload["board_a"] + payload["board_b"]
    assert len(cards) == 1
    card = cards[0]
    assert card["proposition"] == "MONEYLINE"
    assert card["event_id"] == "e1"
    assert card["matchup"] == "NYY @ BOS"
    assert card["group_key"]  # non-empty once identity resolves
    assert set(card["sides"]) == {"HOME", "AWAY"}
    assert card["sides"]["HOME"]["best_odds"] == -140  # max(-150, -140)


def test_games_main_returns_nonzero_on_error_status(monkeypatch):
    """The games CLI must exit non-zero when the export reports status=error,
    so the shell/orchestration sees the failure (Finding #2)."""
    import outlier_scrapers.games as games_mod

    monkeypatch.setattr(games_mod, "OutlierApiClient", lambda *a, **k: object())
    monkeypatch.setattr(
        games_mod,
        "export_games_for_league",
        lambda *a, **k: {
            "status": "error",
            "record_count": 0,
            "enrichment_count": 0,
            "fetch_errors": [{"step": "markets"}],
            "fetch_error_count": 1,
        },
    )

    rc = games_mod.main(["--league", "MLB"])
    assert rc == 1


def test_game_card_group_key_is_event_scoped():
    """Two different games' same-proposition cards must not collapse to one
    group_key (game rows have no player_id)."""
    from outlier_scrapers.cards import _group_key

    id1 = {"event_id": "e1", "proposition": "MONEYLINE", "market": "ML", "scope": "full_game"}
    id2 = {"event_id": "e2", "proposition": "MONEYLINE", "market": "ML", "scope": "full_game"}
    assert _group_key(id1) != _group_key(id2)
    assert "e1" in _group_key(id1)


def test_prop_group_key_shape_unchanged():
    """Regression guard: player cards keep the player_id|market|scope key."""
    from outlier_scrapers.cards import _group_key

    ident = {"player": "Aaron Judge", "player_id": "p1", "market": "H", "scope": "full_game"}
    assert _group_key(ident) == "p1|H|full_game"


# --------------------------------------------------------------------------- #
# Matchup + injuries context (live-verified field shapes 2026-06-22)
# --------------------------------------------------------------------------- #


def test_normalize_games_matchup_and_injuries_context():
    """matchup_type is snake_case in the payload (not matchupType), there is no
    teamRankings field, and injury players carry no teamId of their own. Verify
    the normalizer reads matchup_type, keeps lineups, drops team_rankings, and
    keys injuries by the stamped teamId."""
    config = get_sport_config("MLB")
    schedule = {
        "events": [
            {
                "id": "e1",
                "status": "scheduled",
                "home": {"id": "h1", "teamId": "h1", "name": "Yankees"},
                "away": {"id": "a1", "teamId": "a1", "name": "Red Sox"},
            }
        ]
    }
    events_payloads = [
        {
            "eventId": "e1",
            "matchup": {
                "matchup_type": "BasketballMatchup",
                "lineups": {"home": {"teamId": "h1"}, "away": {"teamId": "a1"}},
            },
            "injuries": [
                {
                    "playerId": "p1",
                    "lastName": "Judge",
                    "teamId": "h1",
                    "injury": {"status": "OUT"},
                },
            ],
            "markets": [],
        }
    ]
    res = normalize_games(
        config=config, schedule_payload=schedule, events_payloads=events_payloads, source_url="api"
    )
    ctx = res["context"]
    assert ctx["events"]["e1"]["matchup_type"] == "BasketballMatchup"
    assert "team_rankings" not in ctx["events"]["e1"]
    assert ctx["events"]["e1"]["lineups"]["home"]["teamId"] == "h1"
    assert ctx["teams"]["h1"]["injuries"][0]["playerId"] == "p1"


def test_normalize_games_stamps_home_away_team_id_on_event():
    """build_injuries joins an event to its teams' injuries via
    event.home_team_id / event.away_team_id, so normalize_games must stamp those
    ids onto each event context. Uses empty lineups (as MLB emits) to prove the
    ids come from the schedule, not from lineups."""
    config = get_sport_config("MLB")
    schedule = {
        "events": [
            {
                "id": "e1",
                "status": "scheduled",
                "home": {"id": "h1", "teamId": "h1", "name": "Yankees"},
                "away": {"id": "a1", "teamId": "a1", "name": "Red Sox"},
            }
        ]
    }
    events_payloads = [
        {
            "eventId": "e1",
            "matchup": {"matchup_type": "BaseballMatchup", "lineups": {}},
            "markets": [],
        }
    ]
    res = normalize_games(
        config=config, schedule_payload=schedule, events_payloads=events_payloads, source_url="api"
    )
    ev = res["context"]["events"]["e1"]
    assert ev["home_team_id"] == "h1"
    assert ev["away_team_id"] == "a1"


def test_injury_flags_join_end_to_end():
    """Regression: normalize_games output must let build_injuries populate
    injury_flags. Uses the real injury schema (firstName/lastName + nested
    injury.status) and empty lineups (as MLB emits), so the join relies on the
    schedule-sourced team ids."""
    from outlier_scrapers.pack import build_injuries

    config = get_sport_config("MLB")
    schedule = {
        "events": [
            {
                "id": "e1",
                "status": "scheduled",
                "home": {"id": "h1", "teamId": "h1", "name": "Yankees"},
                "away": {"id": "a1", "teamId": "a1", "name": "Red Sox"},
            }
        ]
    }
    events_payloads = [
        {
            "eventId": "e1",
            "matchup": {"matchup_type": "BaseballMatchup", "lineups": {}},
            "injuries": [
                {
                    "playerId": "p1",
                    "firstName": "Aaron",
                    "lastName": "Judge",
                    "injury": {"status": "OUT", "injury": "Toe"},
                    "teamId": "h1",
                }
            ],
            "markets": [],
        }
    ]
    res = normalize_games(
        config=config, schedule_payload=schedule, events_payloads=events_payloads, source_url="api"
    )
    flags = build_injuries(res)
    assert "e1" in flags
    assert "Aaron Judge" in flags["e1"]
    assert "OUT" in flags["e1"]
    assert "playerId" not in flags["e1"]  # legible, not a raw dict dump


def test_schedule_index_reads_scheduled_time():
    """Schedule events expose the lock time as ``scheduledTime`` (startTime is
    absent live); event_starts_at depends on it for the pack's date filtering."""
    from outlier_scrapers.normalizer import build_schedule_index

    config = get_sport_config("MLB")
    schedule = {
        "events": [
            {
                "id": "e1",
                "eventId": "e1",
                "scheduledTime": "2026-06-23T01:38:00+00:00",
                "home": {"id": "h1", "name": "Yankees"},
                "away": {"id": "a1", "name": "Red Sox"},
            }
        ]
    }
    idx = build_schedule_index(schedule, config)
    assert idx["e1"]["starts_at"] == "2026-06-23T01:38:00+00:00"


def test_games_export_stamps_injury_team_id(tmp_path, monkeypatch):
    """End-to-end: the injuries endpoint returns players with no teamId, so
    export_games_for_league must stamp it for context.teams to populate."""
    import json
    from outlier_scrapers import paths as paths_mod
    from outlier_scrapers.games import export_games_for_league
    from outlier_scrapers.paths import league_paths

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")

    class InjuryClient(FakeGamesClient):
        def fetch_event_matchup(self, event_id):
            return {"matchup_type": "BasketballMatchup", "lineups": {"home": {"teamId": "h1"}}}

        def fetch_team_injuries(self, league_id, team_id):
            return {
                "players": [{"playerId": "p1", "lastName": "Judge", "injury": {"status": "OUT"}}]
            }

    client = InjuryClient()
    client.fetch_schedule("MLB")  # prime target_date
    export_games_for_league(client, "MLB", target_date=client.target_date)

    latest = league_paths("MLB").normalized / "mlb_games_latest.json"
    data = json.loads(latest.read_text(encoding="utf-8"))
    teams_ctx = data["context"]["teams"]
    assert "h1" in teams_ctx
    assert teams_ctx["h1"]["injuries"][0]["playerId"] == "p1"
    assert teams_ctx["h1"]["injuries"][0]["teamId"] == "h1"  # stamped upstream
