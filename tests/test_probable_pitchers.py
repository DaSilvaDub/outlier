"""Unit tests for the MLB probable-pitchers scraper/normalizer."""
import json

import pytest

from outlier_scrapers.probable_pitchers import (
    export_probable_pitchers,
    load_probable_pitcher_lookup,
    normalize_probable_pitchers,
)


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


def test_normalize_probable_pitchers_confirms_both_sides():
    normalized = normalize_probable_pitchers(_raw_schedule())
    assert normalized["record_count"] == 1
    assert normalized["by_team"]["LAA"] == {
        "pitcher": "Yusei Kikuchi",
        "pitcher_id": 608337,
        "confirmed": True,
        "opponent": "BAL",
        "home_away": "AWAY",
        "game_pk": 12345,
        "scheduled_time": "2026-08-04T23:05:00Z",
    }
    assert normalized["by_team"]["BAL"]["confirmed"] is True
    assert normalized["by_team"]["BAL"]["pitcher"] == "Grayson Rodriguez"
    assert normalized["by_team"]["BAL"]["pitcher_id"] == 669302
    assert normalized["games"][0]["away_pitcher_id"] == 608337
    assert normalized["games"][0]["home_pitcher_id"] == 669302


def test_normalize_probable_pitchers_flags_unconfirmed_starter():
    normalized = normalize_probable_pitchers(_raw_schedule(home_confirmed=False))
    assert normalized["by_team"]["BAL"]["confirmed"] is False
    assert normalized["by_team"]["BAL"]["pitcher"] is None
    assert normalized["by_team"]["BAL"]["pitcher_id"] is None
    # The away side is unaffected by the home side being TBD.
    assert normalized["by_team"]["LAA"]["confirmed"] is True
    assert normalized["by_team"]["LAA"]["pitcher_id"] == 608337


def test_normalize_probable_pitchers_empty_schedule():
    normalized = normalize_probable_pitchers({"dates": []})
    assert normalized["record_count"] == 0
    assert normalized["by_team"] == {}


def test_normalize_probable_pitchers_keeps_both_doubleheader_games():
    payload = {
        "dates": [
            {
                "date": "2026-08-04",
                "games": [
                    {
                        "gamePk": 111,
                        "gameDate": "2026-08-04T17:05:00Z",
                        "teams": {
                            "away": {
                                "team": {"name": "New York Yankees"},
                                "probablePitcher": {"id": 1, "fullName": "Gerrit Cole"},
                            },
                            "home": {
                                "team": {"name": "Boston Red Sox"},
                                "probablePitcher": {"id": 2, "fullName": "Tanner Houck"},
                            },
                        },
                    },
                    {
                        "gamePk": 222,
                        "gameDate": "2026-08-04T23:15:00Z",
                        "teams": {
                            "away": {
                                "team": {"name": "New York Yankees"},
                                "probablePitcher": {"id": 3, "fullName": "Carlos Rodon"},
                            },
                            "home": {
                                "team": {"name": "Boston Red Sox"},
                                "probablePitcher": {"id": 4, "fullName": "Brayan Bello"},
                            },
                        },
                    },
                ],
            }
        ]
    }
    normalized = normalize_probable_pitchers(payload)
    assert normalized["record_count"] == 2
    yankees = normalized["by_team"]["NYY"]
    assert yankees["doubleheader"] is True
    pitchers = {entry["pitcher"] for entry in yankees["slate_games"]}
    assert pitchers == {"Gerrit Cole", "Carlos Rodon"}
    assert {entry["game_pk"] for entry in yankees["slate_games"]} == {111, 222}
    # Primary slot stays the first confirmed game; the nightcap is not dropped.
    assert yankees["pitcher"] == "Gerrit Cole"
    assert yankees["game_pk"] == 111
    red_sox = normalized["by_team"]["BOS"]
    assert {entry["pitcher"] for entry in red_sox["slate_games"]} == {
        "Tanner Houck",
        "Brayan Bello",
    }


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
    assert load_probable_pitcher_lookup("MLB") == {}


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
        json.dumps({"by_team": {"BAL": {"pitcher": None, "confirmed": False}}}),
        encoding="utf-8",
    )
    lookup = load_probable_pitcher_lookup("MLB")
    assert lookup == {"BAL": {"pitcher": None, "confirmed": False}}


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
    assert written["by_team"]["LAA"]["confirmed"] is True
    assert written["by_team"]["LAA"]["pitcher_id"] == 608337
    assert written["by_team"]["LAA"]["feature_source"] == "mlb_stats_gamelog"
    assert written["by_team"]["LAA"]["projected_bf"] == pytest.approx((24 + 22 + 23) / 3)


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
