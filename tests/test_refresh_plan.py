from __future__ import annotations

import pytest

from outlier_scrapers.refresh_plan import (
    RefreshTask,
    all_required_ok,
    execute_refresh,
    ordered_tasks,
)


def test_ordered_tasks_respects_dependencies_and_declaration_ties() -> None:
    names = [task.name for task in ordered_tasks()]
    assert names.index("props") < names.index("line_movement") < names.index("cards")
    assert names.index("games") < names.index("probable_pitchers")
    assert names.index("games") < names.index("game_line_movement") < names.index("game_cards")
    assert names.index("props") < names.index("insights")
    assert names.index("insights") < names.index("games")


def test_ordered_tasks_rejects_cycles() -> None:
    tasks = (
        RefreshTask("a", "--a", depends_on=("b",)),
        RefreshTask("b", "--b", depends_on=("a",)),
    )
    with pytest.raises(ValueError, match="cycle"):
        ordered_tasks(tasks)


def test_execute_refresh_concurrent_fail_fast() -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> int:
        calls.append(argv)
        if "--line-movement" in argv:
            return 1
        return 0

    results = execute_refresh(["MLB"], target_date="2026-08-30", runner=runner)
    flags = [argv[-3] if "--date" in argv else argv[-1] for argv in calls]
    
    assert "--props" in flags
    assert "--line-movement" in flags
    assert "--cards" not in flags
    
    failed = [result for result in results if result.name == "line_movement"][0]
    assert failed.ok is False
    skipped_cards = [result for result in results if result.name == "cards"][0]
    assert skipped_cards.skipped is True
    assert all_required_ok(results) is False


def test_execute_refresh_independent_leagues_continue_after_failure() -> None:
    seen: list[str] = []

    def runner(argv: list[str]) -> int:
        seen.append(argv[1])
        # Fail immediately for MLB tasks, but let WNBA succeed
        if argv[1] == "MLB":
            return 1
        return 0

    results = execute_refresh(["MLB", "WNBA"], runner=runner)
    
    # MLB and WNBA should both have been seen
    assert "MLB" in seen
    assert "WNBA" in seen
    
    # WNBA tasks should succeed
    wnba = [result for result in results if result.league == "WNBA"]
    assert all(result.ok for result in wnba if not result.skipped)
    
    # MLB tasks should have failures or skips
    mlb = [result for result in results if result.league == "MLB"]
    assert all(result.skipped or not result.ok for result in mlb)
    
    assert all_required_ok(results) is False


def test_execute_refresh_passes_date_and_league() -> None:
    seen: list[list[str]] = []

    def runner(argv: list[str]) -> int:
        seen.append(argv)
        return 0

    tasks = (RefreshTask("props", "--props"),)
    results = execute_refresh(["WNBA"], target_date="2026-08-29", runner=runner, tasks=tasks)
    assert results[0].ok is True
    assert seen == [["--league", "WNBA", "--props", "--date", "2026-08-29"]]


def test_projections_depend_on_the_feeds_they_read() -> None:
    """Projections read props_normalized_latest() and the probable-pitcher lookup.

    Declared with no dependencies they run concurrently with the producers, so
    they read the previous slate's props and fail the slate-date check with
    zero records -- which is what aborted the 2026-09-01 refresh.
    """
    from outlier_scrapers.refresh_plan import TASK_BY_NAME

    assert set(TASK_BY_NAME["projections"].depends_on) >= {"props", "probable_pitchers"}
    names = [task.name for task in ordered_tasks()]
    assert names.index("props") < names.index("projections")
    assert names.index("probable_pitchers") < names.index("projections")
