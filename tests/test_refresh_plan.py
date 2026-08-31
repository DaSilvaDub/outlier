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


def test_execute_refresh_is_sequential_and_fail_fast() -> None:
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
    assert "--game-cards" not in flags or any(
        not r.ok and r.skipped for r in results if r.name == "cards"
    )
    failed = [result for result in results if result.name == "line_movement"][0]
    assert failed.ok is False
    skipped_cards = [result for result in results if result.name == "cards"][0]
    assert skipped_cards.skipped is True
    assert all_required_ok(results) is False
    assert calls[0][:4] == ["--league", "MLB", "--props", "--date"] or calls[0][:3] == [
        "--league",
        "MLB",
        "--props",
    ]


def test_execute_refresh_does_not_start_later_leagues_after_failure() -> None:
    seen: list[str] = []

    def runner(argv: list[str]) -> int:
        seen.append(argv[1])
        return 1

    results = execute_refresh(["MLB", "WNBA"], runner=runner)
    assert seen == ["MLB"]
    assert all(result.league == "MLB" or result.skipped for result in results)
    wnba = [result for result in results if result.league == "WNBA"]
    assert wnba
    assert all(result.skipped and not result.ok for result in wnba)
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
