"""Explicit refresh DAG for the daily job.

Models producer dependencies first. Execution stays sequential in topological
order; concurrency is a later change, not the first one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Sequence

from . import refresh

logger = logging.getLogger(__name__)

RefreshRunner = Callable[[list[str]], int]


@dataclass(frozen=True)
class RefreshTask:
    name: str
    flag: str
    depends_on: tuple[str, ...] = ()
    required: bool = True


@dataclass(frozen=True)
class RefreshTaskResult:
    name: str
    league: str
    ok: bool
    exit_code: int
    error: str = ""
    skipped: bool = False
    flag: str = ""
    started_at: str = ""
    finished_at: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "league": self.league,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "error": self.error,
            "skipped": self.skipped,
            "flag": self.flag,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


# Declaration order is the tie-break for independent ready tasks.
REFRESH_TASKS: tuple[RefreshTask, ...] = (
    RefreshTask(name="props", flag="--props"),
    RefreshTask(name="insights", flag="--insights"),
    RefreshTask(name="games", flag="--games"),
    RefreshTask(name="probable_pitchers", flag="--probable-pitchers", depends_on=("games",)),
    # Projections read props_normalized_latest() and the probable-pitcher
    # lookup. Without these edges they run alongside their producers and
    # project the previous slate, which fails the slate-date check.
    RefreshTask(
        name="projections",
        flag="--projections",
        depends_on=("props", "probable_pitchers"),
    ),
    RefreshTask(name="line_movement", flag="--line-movement", depends_on=("props",)),
    RefreshTask(
        name="game_line_movement",
        flag="--game-line-movement",
        depends_on=("games",),
    ),
    RefreshTask(name="cards", flag="--cards", depends_on=("props", "line_movement")),
    RefreshTask(
        name="game_cards",
        flag="--game-cards",
        depends_on=("games", "game_line_movement"),
    ),
)

TASK_BY_NAME = {task.name: task for task in REFRESH_TASKS}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ordered_tasks(tasks: Sequence[RefreshTask] | None = None) -> list[RefreshTask]:
    """Stable topological order; declaration order wins among ready tasks."""

    items = list(tasks if tasks is not None else REFRESH_TASKS)
    names = [task.name for task in items]
    if len(set(names)) != len(names):
        raise ValueError("duplicate refresh task names")
    remaining_deps = {task.name: set(task.depends_on) for task in items}
    unknown = [dep for task in items for dep in task.depends_on if dep not in remaining_deps]
    if unknown:
        raise ValueError(f"unknown refresh dependency: {unknown[0]}")
    ready = [task for task in items if not remaining_deps[task.name]]
    ordered: list[RefreshTask] = []
    placed: set[str] = set()
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        placed.add(current.name)
        for task in items:
            if task.name in placed or task in ready:
                continue
            remaining_deps[task.name].discard(current.name)
            if not remaining_deps[task.name]:
                ready.append(task)
    if len(ordered) != len(items):
        raise ValueError("cycle in refresh DAG")
    return ordered


def all_required_ok(results: Sequence[RefreshTaskResult]) -> bool:
    return all(result.ok for result in results)


def execute_refresh(
    leagues: Sequence[str],
    *,
    target_date: str | None = None,
    runner: RefreshRunner | None = None,
    tasks: Sequence[RefreshTask] | None = None,
) -> list[RefreshTaskResult]:
    """Run the DAG concurrently across leagues and independent tasks.
    
    Independent tasks (e.g. games and props) run simultaneously.
    If a task fails, subsequent tasks for that league are skipped,
    but independent leagues continue.
    """

    import concurrent.futures

    run = runner or refresh.main
    plan = ordered_tasks(tasks)
    
    pending_nodes = {(league, task) for league in leagues for task in plan}
    running_futures: dict[concurrent.futures.Future[RefreshTaskResult], tuple[str, RefreshTask]] = {}
    completed_results: dict[tuple[str, str], RefreshTaskResult] = {}
    
    failed_leagues: set[str] = set()
    
    # 16 workers allows a full slate of independent tasks across 2 leagues to run at once.
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        while pending_nodes or running_futures:
            ready_to_submit = []
            for league, task in pending_nodes:
                if league in failed_leagues:
                    ready_to_submit.append((league, task))
                    continue
                
                deps_ready = True
                for dep_name in task.depends_on:
                    if (league, dep_name) not in completed_results:
                        deps_ready = False
                        break
                if deps_ready:
                    ready_to_submit.append((league, task))
                    
            for league, task in ready_to_submit:
                pending_nodes.remove((league, task))
                
                if league in failed_leagues:
                    res = RefreshTaskResult(
                        name=task.name,
                        league=league,
                        ok=False,
                        exit_code=1,
                        error="skipped because an earlier refresh task failed",
                        skipped=True,
                        flag=task.flag,
                    )
                    completed_results[(league, task.name)] = res
                    continue
                    
                def _do_work(lg=league, t=task) -> RefreshTaskResult:
                    argv = ["--league", lg, t.flag]
                    if target_date:
                        argv.extend(["--date", target_date])
                    logger.info("Running %s for %s...", t.name.replace("_", " "), lg)
                    started = _utc_now()
                    try:
                        exit_code = int(run(argv))
                        error = "" if exit_code == 0 else f"refresh {t.name} exited {exit_code}"
                    except Exception as exc:
                        exit_code = 1
                        error = str(exc)[:300]
                    finished = _utc_now()
                    return RefreshTaskResult(
                        name=t.name,
                        league=lg,
                        ok=exit_code == 0,
                        exit_code=exit_code,
                        error=error,
                        skipped=False,
                        flag=t.flag,
                        started_at=started,
                        finished_at=finished,
                    )
                
                future = executor.submit(_do_work)
                running_futures[future] = (league, task)
            
            if running_futures:
                done, _ = concurrent.futures.wait(
                    running_futures.keys(),
                    return_when=concurrent.futures.FIRST_COMPLETED
                )
                
                for fut in done:
                    league, task = running_futures.pop(fut)
                    res = fut.result()
                    completed_results[(league, task.name)] = res
                    if not res.ok:
                        logger.error("Failed at step: %s for %s", task.name.replace("_", " "), league)
                        failed_leagues.add(league)

    # Return results in a stable order (original sequential iteration order)
    final_results = []
    for league in leagues:
        for task in plan:
            final_results.append(completed_results[(league, task.name)])
            
    return final_results
