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
    ([_inj("A", None)], AS_OF, "unstamped"),
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
