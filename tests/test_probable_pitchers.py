"""Unit tests for the MLB probable-pitchers scraper/normalizer."""
import json

import pytest

from outlier_scrapers.probable_pitchers import (
    export_probable_pitchers,
    load_probable_pitcher_lookup,
    normalize_probable_pitchers,
    resolve_starter,
)
from outlier_scrapers.projections import mlb_so_projection_record


def _raw_schedule(*, home_confirmed: bool = True) -> dict:
    home_side = {"team": {"name": "Baltimore Orioles"}}
    if home_confirmed:
        home_side["probablePitcher"] = {"id": 669302, "fullName": "Grayson Rodriguez"}
    return {
        "dates": [
            {
                "date": "2026-08-04",
                "games": [
                    {
                        "gamePk": 12345,
                        "gameDate": "2026-08-04T23:05:00Z",
                        "teams": {
                            "away": {
                                "team": {"name": "Los Angeles Angels"},
                                "probablePitcher": {"id": 608337, "fullName": "Yusei Kikuchi"},
                            },
                            "home": home_side,
                        },
                    }
                ],
            }
        ]
    }


def _side(payload: dict, team: str, *, game_pk: int | None = None) -> dict:
    rows = [
        row
        for row in payload["starters"]
        if row["team"] == team and (game_pk is None or row["game_pk"] == game_pk)
    ]
    assert len(rows) == 1
    return rows[0]


def test_normalize_probable_pitchers_confirms_both_sides():
    normalized = normalize_probable_pitchers(_raw_schedule())
    assert normalized["record_count"] == 1
    assert _side(normalized, "LAA") == {
        "team": "LAA",
        "pitcher": "Yusei Kikuchi",
        "pitcher_id": 608337,
        "confirmed": True,
        "opponent": "BAL",
        "home_away": "AWAY",
        "game_pk": 12345,
        "scheduled_time": "2026-08-04T23:05:00Z",
    }
    assert _side(normalized, "BAL")["confirmed"] is True
    assert _side(normalized, "BAL")["pitcher"] == "Grayson Rodriguez"
    assert _side(normalized, "BAL")["pitcher_id"] == 669302
    assert normalized["games"][0]["away_pitcher_id"] == 608337
    assert normalized["games"][0]["home_pitcher_id"] == 669302


def test_normalize_probable_pitchers_flags_unconfirmed_starter():
    normalized = normalize_probable_pitchers(_raw_schedule(home_confirmed=False))
    assert _side(normalized, "BAL")["confirmed"] is False
    assert _side(normalized, "BAL")["pitcher"] is None
    assert _side(normalized, "BAL")["pitcher_id"] is None
    # The away side is unaffected by the home side being TBD.
    assert _side(normalized, "LAA")["confirmed"] is True
    assert _side(normalized, "LAA")["pitcher_id"] == 608337


def test_normalize_probable_pitchers_empty_schedule():
    normalized = normalize_probable_pitchers({"dates": []})
    assert normalized["record_count"] == 0
    assert normalized["starters"] == []


def _dh_game(
    pk: int, when: str, away_name: str, away_id: int, home_name: str, home_id: int
) -> dict:
    return {
        "gamePk": pk,
        "gameDate": when,
        "teams": {
            "away": {
                "team": {"name": "New York Yankees"},
                "probablePitcher": {"id": away_id, "fullName": away_name},
            },
            "home": {
                "team": {"name": "Boston Red Sox"},
                "probablePitcher": {"id": home_id, "fullName": home_name},
            },
        },
    }


def test_doubleheader_so_row_resolves_to_its_own_starter():
    """17:05 Cole (game_pk 1001) must not resolve as the night game's Rodon."""
    normalized = normalize_probable_pitchers(
        {
            "dates": [
                {
                    "date": "2026-08-04",
                    "games": [
                        _dh_game(
                            1001,
                            "2026-08-04T17:05:00-04:00",
                            "Gerrit Cole",
                            543037,
                            "Brayan Bello",
                            678906,
                        ),
                        _dh_game(
                            1002,
                            "2026-08-04T19:10:00-04:00",
                            "Carlos Rodon",
                            607074,
                            "Garrett Crochet",
                            676979,
                        ),
                    ],
                }
            ]
        }
    )
    lookup = normalized["starters"]

    def so(player: str, outcome_id: str, game_pk: int) -> dict:
        return {
            "sport": "MLB",
            "market_type": "SO",
            "player": player,
            "team": "NYY",
            "line": 5.5,
            "headline_side": "OVER",
            "outcome_id": outcome_id,
            "game_pk": game_pk,
        }

    cole = mlb_so_projection_record(so("Gerrit Cole", "cole-1705", 1001), lookup)
    rodon = mlb_so_projection_record(so("Carlos Rodon", "rodon-1910", 1002), lookup)
    assert cole is not None and cole["row_id"] == "cole-1705"
    assert rodon is not None and rodon["row_id"] == "rodon-1910"


def test_doubleheader_so_row_resolves_without_a_game_pk():
    """Normalized rows carry no gamePk, so the name has to separate the two games.

    ``outlier_scrapers.normalizer`` emits ``event_id``, never the MLB Stats API
    ``gamePk``, so every production SO row reaches ``resolve_starter`` with
    ``game_pk=None``. Matching on game_pk alone left both halves of a
    doubleheader ambiguous and dropped the projection for each of them.
    """
    normalized = normalize_probable_pitchers(
        {
            "dates": [
                {
                    "date": "2026-08-04",
                    "games": [
                        _dh_game(
                            1001,
                            "2026-08-04T17:05:00-04:00",
                            "Gerrit Cole",
                            543037,
                            "Brayan Bello",
                            678906,
                        ),
                        _dh_game(
                            1002,
                            "2026-08-04T19:10:00-04:00",
                            "Carlos Rodon",
                            607074,
                            "Garrett Crochet",
                            676979,
                        ),
                    ],
                }
            ]
        }
    )
    lookup = normalized["starters"]

    def so(player: str, outcome_id: str) -> dict:
        # Exactly the shape normalize_props emits: no game_pk anywhere.
        return {
            "sport": "MLB",
            "market_type": "SO",
            "player": player,
            "team": "NYY",
            "event_id": "evt-nyy-bos",
            "market_id": "m1",
            "line": 5.5,
            "headline_side": "OVER",
            "outcome_id": outcome_id,
        }

    cole = mlb_so_projection_record(so("Gerrit Cole", "cole-1705"), lookup)
    rodon = mlb_so_projection_record(so("Carlos Rodon", "rodon-1910"), lookup)
    assert cole is not None and cole["row_id"] == "cole-1705"
    assert rodon is not None and rodon["row_id"] == "rodon-1910"

    # Still fail-closed: a pitcher who is not one of this team's starters, and a
    # starter attached to the wrong team, both stay unresolved.
    assert mlb_so_projection_record(so("Clay Holmes", "holmes"), lookup) is None
    bello_on_nyy = {**so("Brayan Bello", "bello"), "team": "NYY"}
    assert mlb_so_projection_record(bello_on_nyy, lookup) is None


def test_resolve_starter_keeps_game_pk_authoritative():
    """A supplied game_pk that matches nothing must not fall back to the name."""
    starters = [
        {"team": "NYY", "pitcher": "Gerrit Cole", "confirmed": True, "game_pk": 1001},
        {"team": "NYY", "pitcher": "Carlos Rodon", "confirmed": True, "game_pk": 1002},
    ]
    assert resolve_starter(starters, team="NYY", game_pk=1002)["pitcher"] == "Carlos Rodon"
    assert resolve_starter(starters, team="NYY", game_pk=9999, pitcher="Gerrit Cole") is None
    assert resolve_starter(starters, team="NYY") is None
    assert resolve_starter(starters, team="NYY", pitcher="Gerrit Cole")["game_pk"] == 1001
    # An ambiguous name resolves nothing rather than picking one.
    same_name = [{**row, "pitcher": "Gerrit Cole"} for row in starters]
    assert resolve_starter(same_name, team="NYY", pitcher="Gerrit Cole") is None


def test_export_probable_pitchers_skips_non_mlb():
    status = export_probable_pitchers("WNBA")
    assert status["status"] == "skipped"
    assert status["record_count"] == 0


def test_load_probable_pitcher_lookup_missing_file_returns_empty(tmp_path, monkeypatch):
    import outlier_scrapers.probable_pitchers as pp
    from outlier_scrapers.paths import LeaguePaths

    fake_paths = LeaguePaths(
        league="MLB",
        root=tmp_path,
        raw=tmp_path / "raw",
        normalized=tmp_path / "normalized",
        reports=tmp_path / "reports",
    )
    monkeypatch.setattr(pp, "league_paths", lambda league: fake_paths)
    assert load_probable_pitcher_lookup("MLB") == []


def test_load_probable_pitcher_lookup_reads_written_file(tmp_path, monkeypatch):
    import outlier_scrapers.probable_pitchers as pp
    from outlier_scrapers.paths import LeaguePaths

    fake_paths = LeaguePaths(
        league="MLB",
        root=tmp_path,
        raw=tmp_path / "raw",
        normalized=tmp_path / "normalized",
        reports=tmp_path / "reports",
    )
    monkeypatch.setattr(pp, "league_paths", lambda league: fake_paths)
    fake_paths.normalized.mkdir(parents=True, exist_ok=True)
    fake_paths.probable_pitchers_latest().write_text(
        json.dumps({"starters": [{"team": "BAL", "pitcher": None, "confirmed": False}]}),
        encoding="utf-8",
    )
    lookup = load_probable_pitcher_lookup("MLB")
    assert lookup == [{"team": "BAL", "pitcher": None, "confirmed": False}]


def test_export_probable_pitchers_writes_normalized_and_raw(tmp_path, monkeypatch):
    import outlier_scrapers.probable_pitchers as pp
    from outlier_scrapers.paths import LeaguePaths
    from datetime import date

    fake_paths = LeaguePaths(
        league="MLB",
        root=tmp_path,
        raw=tmp_path / "raw",
        normalized=tmp_path / "normalized",
        reports=tmp_path / "reports",
    )
    monkeypatch.setattr(pp, "league_paths", lambda league: fake_paths)
    monkeypatch.setattr(pp, "fetch_probable_pitchers_raw", lambda target_date: _raw_schedule())

    def _fake_stats(_url: str) -> dict:
        return {
            "stats": [
                {
                    "splits": [
                        {
                            "date": "2026-07-20",
                            "stat": {
                                "strikeOuts": 6,
                                "battersFaced": 24,
                                "gamesStarted": 1,
                            },
                        },
                        {
                            "date": "2026-07-14",
                            "stat": {
                                "strikeOuts": 5,
                                "battersFaced": 22,
                                "gamesStarted": 1,
                            },
                        },
                        {
                            "date": "2026-07-08",
                            "stat": {
                                "strikeOuts": 7,
                                "battersFaced": 23,
                                "gamesStarted": 1,
                            },
                        },
                    ]
                }
            ]
        }

    status = export_probable_pitchers(
        "MLB", target_date=date(2026, 8, 4), fetch_json=_fake_stats
    )
    assert status == {"status": "ok", "record_count": 1}
    assert fake_paths.probable_pitchers_latest().exists()
    written = json.loads(fake_paths.probable_pitchers_latest().read_text(encoding="utf-8"))
    laa = _side(written, "LAA")
    assert laa["confirmed"] is True
    assert laa["pitcher_id"] == 608337
    assert laa["feature_source"] == "mlb_stats_gamelog"
    assert laa["projected_bf"] == pytest.approx((24 + 22 + 23) / 3)


def _raw_schedule_with_status(*, abstract_game_state: str) -> dict:
    payload = _raw_schedule()
    payload["dates"][0]["games"][0]["status"] = {"abstractGameState": abstract_game_state}
    return payload


def test_slate_is_complete_treats_empty_schedule_as_not_complete():
    """A day with zero scheduled games (off day) must not look "complete".

    Matches games._today_slate_is_complete, which also returns False when
    there are no events for the date — otherwise an unpinned run on an off
    day would auto-advance past a real off day instead of just showing an
    empty board for it.
    """
    from outlier_scrapers.probable_pitchers import _slate_is_complete

    assert _slate_is_complete({"dates": []}) is False
    assert _slate_is_complete({"dates": [{"date": "2026-08-04", "games": []}]}) is False


def test_slate_is_complete_accepts_detailed_state_fallback_tokens():
    """GAMEOVER/COMPLETEDEARLY (detailedState) must count as final too.

    Mirrors results._mlb_events, which checks the same MLB Stats API payload
    shape against abstractGameState falling back to detailedState.
    """
    from outlier_scrapers.probable_pitchers import _slate_is_complete

    payload = {
        "dates": [
            {
                "games": [
                    {"status": {"detailedState": "Game Over"}},
                    {"status": {"detailedState": "Completed Early"}},
                ]
            }
        ]
    }
    assert _slate_is_complete(payload) is True


def test_slate_is_complete_handles_malformed_payload_without_crashing():
    """Null/non-dict entries in the schedule payload must fail closed, not raise."""
    from outlier_scrapers.probable_pitchers import _slate_is_complete

    assert _slate_is_complete({"dates": [None]}) is False
    assert _slate_is_complete({"dates": [{"games": [None, "not a game"]}]}) is False
    assert _slate_is_complete({"dates": "not a list"}) is False


def test_export_probable_pitchers_auto_advances_when_slate_complete(tmp_path, monkeypatch):
    """Unpinned runs late in the day must roll onto tomorrow's slate.

    Regression for the near-zero independent_model_prob coverage bug: a
    nightly job running after today's games finish was matching SO markets
    (already built for tomorrow's board by games.py's own auto-advance)
    against today's already-final starters, so no pitcher ever matched.
    """
    import outlier_scrapers.probable_pitchers as pp
    from outlier_scrapers.paths import LeaguePaths
    from datetime import date

    fake_paths = LeaguePaths(
        league="MLB",
        root=tmp_path,
        raw=tmp_path / "raw",
        normalized=tmp_path / "normalized",
        reports=tmp_path / "reports",
    )
    monkeypatch.setattr(pp, "league_paths", lambda league: fake_paths)

    import datetime as datetime_module

    class _FrozenDatetime(datetime_module.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 4, 23, 39)

    monkeypatch.setattr(pp, "datetime", _FrozenDatetime)

    today_complete = _raw_schedule_with_status(abstract_game_state="Final")
    tomorrow_payload = _raw_schedule()
    tomorrow_payload["dates"][0]["date"] = "2026-08-05"

    def _fake_fetch(target_date):
        if target_date == date(2026, 8, 4):
            return today_complete
        if target_date == date(2026, 8, 5):
            return tomorrow_payload
        raise AssertionError(f"unexpected date {target_date}")

    monkeypatch.setattr(pp, "fetch_probable_pitchers_raw", _fake_fetch)

    status = export_probable_pitchers("MLB")
    assert status == {"status": "ok", "record_count": 1}
    written = json.loads(fake_paths.probable_pitchers_latest().read_text(encoding="utf-8"))
    assert written["date"] == "2026-08-05"


def test_export_probable_pitchers_does_not_auto_advance_when_pinned(tmp_path, monkeypatch):
    """An explicit --date pin must be honored even if that slate is final."""
    import outlier_scrapers.probable_pitchers as pp
    from outlier_scrapers.paths import LeaguePaths
    from datetime import date

    fake_paths = LeaguePaths(
        league="MLB",
        root=tmp_path,
        raw=tmp_path / "raw",
        normalized=tmp_path / "normalized",
        reports=tmp_path / "reports",
    )
    monkeypatch.setattr(pp, "league_paths", lambda league: fake_paths)

    today_complete = _raw_schedule_with_status(abstract_game_state="Final")
    calls: list = []

    def _fake_fetch(target_date):
        calls.append(target_date)
        return today_complete

    monkeypatch.setattr(pp, "fetch_probable_pitchers_raw", _fake_fetch)

    status = export_probable_pitchers("MLB", target_date=date(2026, 8, 4))
    assert status == {"status": "ok", "record_count": 1}
    assert calls == [date(2026, 8, 4)]
    written = json.loads(fake_paths.probable_pitchers_latest().read_text(encoding="utf-8"))
    assert written["date"] == "2026-08-04"


def test_export_probable_pitchers_handles_fetch_error(tmp_path, monkeypatch):
    import outlier_scrapers.probable_pitchers as pp
    from outlier_scrapers.paths import LeaguePaths
    from urllib.error import URLError
    from datetime import date

    fake_paths = LeaguePaths(
        league="MLB",
        root=tmp_path,
        raw=tmp_path / "raw",
        normalized=tmp_path / "normalized",
        reports=tmp_path / "reports",
    )
    monkeypatch.setattr(pp, "league_paths", lambda league: fake_paths)

    def _raise(target_date):
        raise URLError("boom")

    monkeypatch.setattr(pp, "fetch_probable_pitchers_raw", _raise)
    status = export_probable_pitchers("MLB", target_date=date(2026, 8, 4))
    assert status["status"] == "error"
    assert status["record_count"] == 0
