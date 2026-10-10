"""F02 remainder (#227): injury date_modified cutoff, wrong-season rows, stale tapes.

Phase 1 (#235) already enforces the TapeEnvelope rules (season match, ``before``
on/before the slate, written by as_of for retrospective/replay, full-timestamp
depth cut, refused tape disables the injury pillar); these tests cover only what
5b adds, plus a lock-in that whole-day tape builds stay off the pipeline path.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

import outlier_nfl.tape_nflverse as tn
from outlier_nfl import pipeline as nfl_pipeline
from outlier_nfl.matchup import TAPE_MAX_AGE_DAYS, tape_inadmissible_reason
from outlier_nfl.run_context import RunContext

AS_OF = datetime(2026, 10, 4, 14, 0, tzinfo=UTC)
GAMES = [{"season": "2026", "game_type": "REG", "week": "4", "gameday": "2026-10-04"}]


def _inj(name: str, modified: str | None, season: str = "2026", week: str = "4") -> dict[str, str]:
    r = {"season": season, "season_type": "REG", "week": week, "team": "KC", "full_name": name,
         "gsis_id": "", "position": "WR", "report_status": "Out"}
    if modified is not None:
        r["date_modified"] = modified
    return r


@pytest.mark.parametrize(("rows", "as_of", "status"), [
    ([_inj("A", "2026-10-02T20:00:00Z")], AS_OF, "point_in_time"),
    ([_inj("A", "2026-10-02 20:00:00")], AS_OF, "point_in_time"),  # naive reads as UTC
    ([_inj("A", "2026-10-02T20:00:00Z"), _inj("B", "2026-10-04T18:00:00Z")], AS_OF,
     "revised_after_as_of"),
    ([_inj("A", None)], AS_OF, "unstamped"),  # no run mode: fails closed
    ([_inj("A", "2026-10-09T20:00:00Z", week="5")], AS_OF, "point_in_time"),  # other week
    ([_inj("A", "2026-10-09T20:00:00Z", season="2025")], AS_OF, "point_in_time"),
    ([_inj("A", None)], None, "no_cutoff"),
])
def test_injury_report_status(rows: list[dict[str, str]], as_of: datetime | None, status: str) -> None:
    assert tn.injury_report_status(rows, 2026, 4, as_of) == status


def test_wrong_season_rows_are_not_inactive() -> None:
    rows = [_inj("A", "2026-10-02T20:00:00Z"), _inj("Old", "2025-10-02T20:00:00Z", season="2025")]
    out = tn.inactive_players(rows, 4, season=2026)
    assert [p["name"] for p in out["KC"]] == ["A"]


@pytest.mark.parametrize(("rows", "loaded", "status"), [
    ([_inj("A", "2026-10-02T20:00:00Z")], True, "point_in_time"),
    ([_inj("A", "2026-10-02T20:00:00Z"), _inj("B", "2026-10-04T18:00:00Z")], False,
     "revised_after_as_of"),
    ([_inj("A", None)], False, "unstamped"),
])
def test_tape_injury_report_requires_point_in_time_rows(
    monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, str]], loaded: bool, status: str
) -> None:
    monkeypatch.setattr(tn, "fetch_csv", lambda url, timeout=60.0: rows if "injuries" in url else [])
    payload = tn.build_tape_payload(2026, before=date(2026, 10, 4), team_rows=[], game_rows=GAMES,
                                    advanced=False, as_of_utc=AS_OF)
    assert payload["injury_report_loaded"] is loaded
    assert payload["injury_report_status"] == status
    if not loaded:
        assert payload["inactive"] == {}


def _ctx() -> RunContext:
    return RunContext(slate_date="2026-10-04", window=None, season=2026, as_of_utc=AS_OF,
                      mode="live", first_kickoff_utc=None)


@pytest.mark.parametrize(("before", "refused"), [
    ("2026-10-04", False), ("2026-09-27", False), ("2026-09-26", True), ("2026-09-13", True),
])
def test_stale_tape_is_refused(tmp_path: Path, before: str, refused: bool) -> None:
    reason = tape_inadmissible_reason({"season": 2026, "before": before}, tmp_path / "t.json", _ctx())
    assert (reason is not None) is refused
    if refused:
        assert reason.startswith("stale") and str(TAPE_MAX_AGE_DAYS) in reason


def test_pipeline_tape_refresh_always_passes_a_full_timestamp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The whole-day ``before`` cut (no as_of_utc) is offline-only (pull_nflverse_tape, tests).

    The pipeline's refresh always builds with an aware as_of, explicit or now.
    """
    seen: list[Any] = []
    monkeypatch.setattr(nfl_pipeline, "refresh_prior_week_tape",
                        lambda *a, **k: seen.append(k.get("as_of_utc")))
    nfl_pipeline._refresh_tape(tmp_path, "2026-10-04", None)
    nfl_pipeline._refresh_tape(tmp_path, "2026-10-04", None, "2026-10-04T14:00:00Z")
    assert len(seen) == 2
    assert all(isinstance(t, datetime) and t.tzinfo is not None for t in seen)
    assert seen[1] == AS_OF


# Real nflverse injuries_2026.csv header (checked against the release file on
# 2026-10-10): no date_modified column; only the 2024 file has one.
INJURIES_2026_COLUMNS = (
    "season", "season_type", "game_type", "team", "week", "gsis_id", "position", "full_name",
    "first_name", "last_name", "report_primary_injury", "report_secondary_injury",
    "report_status", "practice_primary_injury", "practice_secondary_injury", "practice_status",
)
KICKOFF_GAMES = [{"season": "2026", "game_type": "REG", "week": "4", "gameday": "2026-10-04",
                  "gametime": "13:00"}]  # 17:00Z


def _row_2026(name: str) -> dict[str, str]:
    r = dict.fromkeys(INJURIES_2026_COLUMNS, "")
    r.update({"season": "2026", "season_type": "REG", "game_type": "REG", "team": "KC",
              "week": "4", "position": "TE", "full_name": name, "report_status": "Out"})
    return r


def _build_2026(monkeypatch: pytest.MonkeyPatch, as_of: datetime, started: datetime | None
                ) -> dict[str, Any]:
    rows = [_row_2026("Travis Kelce")]
    assert "date_modified" not in rows[0]
    monkeypatch.setattr(tn, "fetch_csv", lambda url, timeout=60.0: rows if "injuries" in url else [])
    return tn.build_tape_payload(2026, before=date(2026, 10, 4), team_rows=[],
                                 game_rows=KICKOFF_GAMES, advanced=False, as_of_utc=as_of,
                                 run_started_utc=started)


def test_live_run_admits_the_unstamped_2026_report(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _build_2026(monkeypatch, AS_OF, AS_OF)  # 14:00Z, before the 17:00Z kickoff
    assert payload["run_mode"] == "live"
    assert payload["injury_report_status"] == "live_unstamped"
    assert payload["injury_report_loaded"] is True
    assert payload["inactive"] == {"KC": ["Travis Kelce"]}


@pytest.mark.parametrize(("as_of", "started", "mode"), [
    # Replay with a recent --as-of: 20 minutes behind the wall clock is past the
    # 15-minute tolerance, so it is a replay even though as_of is close to now.
    (datetime(2026, 10, 4, 13, 40, tzinfo=UTC), AS_OF, "replay"),
    (datetime(2026, 9, 27, 14, 0, tzinfo=UTC), AS_OF, "replay"),
    (datetime(2026, 10, 4, 17, 30, tzinfo=UTC), datetime(2026, 10, 4, 17, 30, tzinfo=UTC),
     "retrospective"),
    (AS_OF, None, None),  # no wall clock: cannot claim live
])
def test_non_live_runs_refuse_the_unstamped_2026_report(
    monkeypatch: pytest.MonkeyPatch, as_of: datetime, started: datetime | None, mode: str | None
) -> None:
    payload = _build_2026(monkeypatch, as_of, started)
    assert payload["run_mode"] == mode
    assert payload["injury_report_status"] == "unstamped"
    assert payload["injury_report_loaded"] is False
    assert payload["inactive"] == {}


def test_live_mode_still_refuses_a_row_revised_after_as_of() -> None:
    rows = [_inj("A", "2026-10-04T18:00:00Z"), _row_2026("B")]
    assert tn.injury_report_status(rows, 2026, 4, AS_OF, "live") == "revised_after_as_of"
