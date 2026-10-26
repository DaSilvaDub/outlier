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
