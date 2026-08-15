"""Unit tests for the MLB probable-pitchers scraper/normalizer."""
import json

from outlier_scrapers.probable_pitchers import (
    export_probable_pitchers,
    load_probable_pitcher_lookup,
    normalize_probable_pitchers,
)


def _raw_schedule(*, home_confirmed: bool = True) -> dict:
    home_side = {"team": {"name": "Baltimore Orioles"}}
    if home_confirmed:
        home_side["probablePitcher"] = {"fullName": "Grayson Rodriguez"}
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
                                "probablePitcher": {"fullName": "Yusei Kikuchi"},
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
        "confirmed": True,
        "opponent": "BAL",
        "home_away": "AWAY",
        "game_pk": 12345,
        "scheduled_time": "2026-08-04T23:05:00Z",
    }
    assert normalized["by_team"]["BAL"]["confirmed"] is True
    assert normalized["by_team"]["BAL"]["pitcher"] == "Grayson Rodriguez"


def test_normalize_probable_pitchers_flags_unconfirmed_starter():
    normalized = normalize_probable_pitchers(_raw_schedule(home_confirmed=False))
    assert normalized["by_team"]["BAL"]["confirmed"] is False
    assert normalized["by_team"]["BAL"]["pitcher"] is None
    # The away side is unaffected by the home side being TBD.
    assert normalized["by_team"]["LAA"]["confirmed"] is True


def test_normalize_probable_pitchers_empty_schedule():
    normalized = normalize_probable_pitchers({"dates": []})
    assert normalized["record_count"] == 0
    assert normalized["by_team"] == {}


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

    status = export_probable_pitchers("MLB", target_date=date(2026, 8, 4))
    assert status == {"status": "ok", "record_count": 1}
    assert fake_paths.probable_pitchers_latest().exists()
    written = json.loads(fake_paths.probable_pitchers_latest().read_text(encoding="utf-8"))
    assert written["by_team"]["LAA"]["confirmed"] is True


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
