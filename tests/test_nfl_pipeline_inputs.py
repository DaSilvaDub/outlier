"""#232 part a: ``NflPipeline(inputs=...)`` routes external metrics, usage and weather
through the given offline inputs and never to the network loaders' defaults."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from outlier_nfl.pipeline import NflPipeline
from outlier_nfl.run_context import parse_utc
from scripts import nfl_snapshot_diff as snap

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "nfl"


def real_loaders(monkeypatch) -> None:
    """Undo conftest's offline stubs so the real loaders run (sockets stay blocked)."""
    from outlier_nfl import external, usage, weather

    monkeypatch.setattr("outlier_nfl.pipeline.load_external_metrics",
                        external.load_external_metrics)
    monkeypatch.setattr("outlier_nfl.pipeline.load_slate_weather", weather.load_slate_weather)
    monkeypatch.setattr("outlier_nfl.pipeline.load_usage", usage.load_usage)


class _Inputs:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.tape_known_at: dict[str, Any] = {}

    def external_client(self) -> _Inputs:
        return self

    def fetch_csv(self, url: str, *_a: Any, **_k: Any) -> list[dict[str, str]]:
        self.calls.append(f"external {url}")
        raise OSError("offline")

    def fetch_rows(self, url: str, *_a: Any, **_k: Any) -> list[dict[str, str]]:
        self.calls.append(f"usage {url}")
        raise OSError("offline")

    def weather(self, *_a: Any, **_k: Any) -> dict[str, Any]:
        self.calls.append("weather")
        raise OSError("offline")


def test_inputs_hook_receives_every_external_usage_and_weather_request(tmp_path, monkeypatch):
    import socket

    def deny(*_a: Any, **_k: Any) -> None:
        raise AssertionError("network used despite inputs=")

    monkeypatch.setattr(socket.socket, "connect", deny)
    real_loaders(monkeypatch)
    tape = tmp_path / "NFL" / "tape" / "prior_week.json"
    tape.parent.mkdir(parents=True)
    tape.write_text(json.dumps(snap.frozen_tape(FIXTURES_DIR)), encoding="utf-8")
    inputs = _Inputs()
    now = parse_utc("2026-10-04T15:00:00+00:00")
    NflPipeline(client=snap.FrozenOutlierClient(FIXTURES_DIR), data_dir=tmp_path,
                clock=lambda: now, inputs=inputs).run(
        date="2026-10-04", as_of_utc=now, reports_dir=tmp_path / "reports")
    assert any(c.startswith("external ") and "games.csv" in c for c in inputs.calls)
    assert "weather" in inputs.calls



def test_drive_simulation_in_offline_run_bundle(tmp_path, monkeypatch):
    """Exercise the PBP adapter and pipeline writer with a frozen pregame cutoff."""
    from datetime import timedelta

    from outlier_nfl.run_context import event_kickoff_utc

    real_loaders(monkeypatch)
    schedule = snap.FrozenOutlierClient(FIXTURES_DIR).fetch_schedule()["events"]
    event = next(row for row in schedule if row["startTime"].startswith("2026-10-04"))
    kickoff = event_kickoff_utc(event)
    assert kickoff is not None
    cutoff = kickoff - timedelta(hours=1)
    home, away = (event[side]["alias"] for side in ("home", "away"))
    team_rows = []
    play_rows = []
    for index in range(5):
        day = "2026-09-01" if index < 4 else "2026-10-04"
        game = f"history-{index}"
        team_rows.append({
            "game_id": game, "season": "2026", "game_type": "REG", "week": str(1 if index < 4 else 5),
            "gameday": day, "gametime": "23:30", "home_team": home, "away_team": away,
            "home_score": "21", "away_score": "3",
        })
        for offense, defense, points in ((home, away, 7), (away, home, 3)):
            for drive in range(8):
                play_rows.append({
                    "game_id": game, "season": "2026", "season_type": "REG", "week": str(1 if index < 4 else 5),
                    "qtr": "1", "posteam": offense, "defteam": defense,
                    "play_id": str(drive + (0 if offense == home else 8)),
                    "fixed_drive": str(drive + (0 if offense == home else 8)),
                    "posteam_score": "0", "posteam_score_post": str(points),
                    "defteam_score": "0", "defteam_score_post": "0",
                })
    team_rows.append({
        "game_id": "target", "season": "2026", "game_type": "REG", "week": "5",
        "gameday": "2026-10-04", "gametime": "13:00", "home_team": home, "away_team": away,
    })

    class FrozenDrives(_Inputs):
        def fetch_csv(self, url, *_a, **_k):
            if "games.csv" in url:
                return team_rows
            if "play_by_play_" in url:
                return play_rows
            return []

    result = NflPipeline(
        client=snap.FrozenOutlierClient(FIXTURES_DIR), data_dir=tmp_path,
        clock=lambda: kickoff + timedelta(hours=4), inputs=FrozenDrives(),
    ).run(date="2026-10-04", as_of_utc=cutoff, reports_dir=tmp_path / "reports")
    assert result["run_mode"] == "retrospective"
    bundle = tmp_path / "NFL" / "runs" / result["run_id"]
    artifact = json.loads((bundle / "nfl_drive_simulations.json").read_text())
    record = next(row for row in artifact["records"] if row["event_id"] == event["eventId"])
    assert record["status"] == "RESEARCH_ONLY"
    assert record["historical_games"][home] == [f"history-{i}" for i in range(4)]
    assert record["simulation"]["joint_regulation_scores"] == [[56, 24, 50_000]]
