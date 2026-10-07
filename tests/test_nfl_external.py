"""External nflverse adapters (NGS, PBP team EPA, schedule) and their cached client."""

from __future__ import annotations

import gzip
import os
from pathlib import Path
import time
from datetime import datetime, timezone

import pytest

from outlier_nfl import external
from outlier_nfl import pipeline as nfl_pipeline
from outlier_nfl.external import common, ngs, pbp, schedule


class FakeClient:
    """Serves canned rows by URL substring; records requested URLs."""

    def __init__(self, routes: dict[str, list[dict[str, str]] | Exception]):
        self.routes = routes
        self.urls: list[str] = []

    def fetch_csv(self, url: str, timeout: float = 120.0) -> list[dict[str, str]]:
        self.urls.append(url)
        for key, rows in self.routes.items():
            if key in url:
                if isinstance(rows, Exception):
                    raise rows
                return rows
        raise AssertionError(f"unexpected url {url}")


def _ngs(week: str, name: str, **extra: str) -> dict[str, str]:
    row = {"season": "2026", "season_type": "REG", "week": week, "player_display_name": name,
           "player_gsis_id": "00-1", "player_position": "QB", "team_abbr": "LA"}
    row.update(extra)
    return row


def test_ngs_filters_season_type_and_week0_aggregates() -> None:
    rows = [
        _ngs("1", "Matthew Stafford", avg_time_to_throw="2.86", attempts="25", aggressiveness="NA"),
        _ngs("0", "Matthew Stafford", avg_time_to_throw="2.7"),  # season aggregate row
        _ngs("4", "Matthew Stafford", avg_time_to_throw="3.0"),  # at/after before_week
        {**_ngs("1", "Old"), "season": "2025"},
        {**_ngs("1", "Post"), "season_type": "POST"},
    ]
    client = FakeClient({"ngs_passing": rows})
    out = ngs.fetch(client, 2026, "passing", before_week=4)["records"]  # type: ignore[arg-type]
    assert len(out) == 1
    rec = out[0]
    assert rec["team"] == "LAR" and rec["week"] == 1 and rec["kind"] == "passing"
    assert rec["avg_time_to_throw"] == 2.86 and rec["attempts"] == 25.0
    assert rec["aggressiveness"] is None  # NA -> None
    with pytest.raises(ValueError):
        ngs.fetch(client, 2026, "kicking", before_week=4)  # type: ignore[arg-type]


def _play(week: str, off: str, deff: str, ptype: str, epa: str, success: str, poe: str = "",
          two_pt: str = "0") -> dict[str, str]:
    return {"season": "2026", "season_type": "REG", "week": week, "posteam": off, "defteam": deff,
            "play_type": ptype, "epa": epa, "success": success, "pass_oe": poe,
            "two_point_attempt": two_pt}


def test_pbp_team_epa_offense_and_defense() -> None:
    rows = [
        _play("1", "LA", "SF", "pass", "1.0", "1", "40"),
        _play("1", "LA", "SF", "pass", "-0.5", "0", "20"),
        _play("1", "LA", "SF", "run", "0.2", "1", "-30"),
        _play("1", "LA", "SF", "run", "NA", "0"),  # missing EPA dropped
        _play("1", "LA", "SF", "pass", "3.0", "1", two_pt="1"),  # two-point try dropped
        _play("1", "LA", "SF", "punt", "0.9", "0"),  # not a scrimmage pass/run
        _play("1", "SF", "LA", "run", "-1.0", "0"),
    ]
    recs = pbp.aggregate(rows, 2026, before_week=18)
    lar_off = next(r for r in recs if r["kind"] == "team_offense" and r["team"] == "LAR")
    assert lar_off["plays"] == 3
    assert lar_off["epa_per_play"] == round(0.7 / 3, 4)
    assert lar_off["success_rate"] == round(2 / 3, 4)
    assert lar_off["pass_epa_per_play"] == 0.25 and lar_off["rush_epa_per_play"] == 0.2
    assert lar_off["pass_rate"] == round(2 / 3, 4)
    assert lar_off["pass_rate_over_expected"] == 10.0
    sf_def = next(r for r in recs if r["kind"] == "team_defense" and r["team"] == "SF")
    assert sf_def["epa_per_play"] == lar_off["epa_per_play"]  # same plays, defensive view
    lar_def = next(r for r in recs if r["kind"] == "team_defense" and r["team"] == "LAR")
    assert lar_def["plays"] == 1 and lar_def["pass_epa_per_play"] is None
    assert pbp.aggregate(rows, 2026, before_week=1) == []


def test_schedule_records_environment_and_lines() -> None:
    rows = [
        {"season": "2026", "game_type": "REG", "week": "3", "game_id": "2026_03_LA_DEN",
         "gameday": "2026-09-27", "gametime": "16:05",
         "home_team": "DEN", "away_team": "LA", "spread_line": "1.5", "total_line": "43.5",
         "home_moneyline": "-125", "away_moneyline": "105", "roof": "outdoors", "temp": "NA",
         "wind": "", "home_score": "", "away_score": "", "div_game": "0",
         "home_qb_name": "Bo Nix", "away_qb_name": "Matthew Stafford"},
        {"season": "2026", "game_type": "POST", "week": "19", "game_id": "p"},
        {"season": "2025", "game_type": "REG", "week": "3", "game_id": "old"},
    ]
    out = schedule.fetch(  # type: ignore[arg-type]
        FakeClient({"games.csv": rows}), 2026, as_of_utc=datetime(2026, 9, 28, tzinfo=timezone.utc)
    )["records"]
    assert len(out) == 1
    g = out[0]
    assert g["away_team"] == "LAR" and g["home_team"] == "DEN" and g["div_game"] is False
    assert g["spread_line"] == 1.5 and g["total_line"] == 43.5 and g["home_moneyline"] == -125.0
    assert g["temp"] is None and g["wind"] is None and g["home_score"] is None
    assert g["referee"] is None and g["away_qb_name"] == "Matthew Stafford"


def _games(*weeks_days: tuple[str, str]) -> list[dict[str, str]]:
    return [{"season": "2026", "game_type": "REG", "week": w, "gameday": d, "gametime": "13:00",
             "home_team": "LA", "away_team": "SF"} for w, d in weeks_days]


AS_OF = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)


def test_load_external_metrics_isolates_failing_sources() -> None:
    client = FakeClient({
        "ngs_passing": [_ngs("1", "Matthew Stafford")],
        "ngs_rushing": OSError("blocked"),
        "ngs_receiving": [],
        "play_by_play": [_play("1", "LA", "SF", "run", "0.1", "1")],
        "games.csv": _games(("1", "2026-09-13"), ("2", "2026-09-20"), ("3", "2026-09-27")),
    })
    loaded = external.load_external_metrics(  # type: ignore[arg-type]
        2026, slate_date="2026-09-27", as_of_utc=AS_OF, client=client
    )
    kinds = sorted(r["kind"] for r in loaded.records if r["source"] != "schedule")
    assert kinds == ["passing", "team_defense", "team_offense"]
    assert loaded.before_week == 3
    status = {s.name: s.status for s in loaded.sources}
    assert status["external:ngs:rushing"] == "UNAVAILABLE"
    assert status["external:ngs:passing"] == "AVAILABLE"
    assert len(client.urls) == 5


def test_load_external_metrics_without_schedule_has_no_cutoff_and_loads_no_weeks() -> None:
    """F01: an unavailable schedule means no verified cutoff, not "every week"."""
    client = FakeClient({
        "ngs_passing": [_ngs("1", "Matthew Stafford"), _ngs("9", "Matthew Stafford")],
        "ngs_rushing": [], "ngs_receiving": [],
        "play_by_play": [_play("9", "LA", "SF", "run", "0.1", "1")],
        "games.csv": OSError("blocked"),
    })
    loaded = external.load_external_metrics(  # type: ignore[arg-type]
        2026, slate_date="2026-09-27", as_of_utc=AS_OF, client=client
    )
    assert loaded.records == [] and loaded.before_week is None
    assert {s.name: s.status for s in loaded.sources} == {
        "external:schedule": "UNAVAILABLE",
        "external:ngs:passing": "UNAVAILABLE",
        "external:ngs:rushing": "UNAVAILABLE",
        "external:ngs:receiving": "UNAVAILABLE",
        "external:pbp": "UNAVAILABLE",
    }
    assert client.urls == [u for u in client.urls if "games.csv" in u]  # nothing week-bound fetched


def test_client_cache_reuses_fresh_and_refetches_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_fetch_bytes(url: str, timeout: float = 60.0) -> bytes:
        calls.append(url)
        return gzip.compress(f"a,b\n{len(calls)},x\n".encode())

    monkeypatch.setattr(common, "fetch_bytes", fake_fetch_bytes)
    client = common.Client(cache_dir=tmp_path, ttl=3600)
    url = "https://example.test/file.csv.gz"
    assert client.fetch_csv(url) == [{"a": "1", "b": "x"}]
    assert client.fetch_csv(url) == [{"a": "1", "b": "x"}]  # served from cache
    assert len(calls) == 1

    cached = client._cache_path(url)
    old = time.time() - 7200
    os.utime(cached, (old, old))
    assert client.fetch_csv(url) == [{"a": "2", "b": "x"}]  # stale -> refetched
    assert len(calls) == 2
    assert common.Client(cache_dir=tmp_path, ttl=0).fetch_csv(url)[0]["a"] == "3"


def test_pipeline_fixture_mode_never_loads_external_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        raise AssertionError("fixture replay must not fetch external metrics")

    monkeypatch.setattr(nfl_pipeline, "load_external_metrics", forbidden)
    fixtures = Path(__file__).parent / "fixtures" / "nfl"
    summary = nfl_pipeline.NflPipeline(data_dir=tmp_path).run(
        date="2026-09-13",
        offline_fixtures_dir=fixtures,
        reports_dir=tmp_path / "reports" / "NFL",
    )
    assert summary["status"] == "OK"
