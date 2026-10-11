"""Append-only intraweek price snapshots for NFL market-movement tracing.

Every pipeline run appends the slate's props to one JSONL file per NFL week
(``<nfl_dir>/snapshots/nfl_prop_snapshots_<week_start>.jsonl``). The week starts
on Tuesday, so the scheduled Tuesday baseline runs and the Thursday / Sunday /
Monday game-day refreshes all land in the same history. ``movement_index``
compares the first-seen price of each prop with the latest one.

Nothing here invents an opening line: a prop seen in only one run has no
movement record, and the best-bets trace reports market movement as MISSING.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import logging
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from outlier_nfl.run_context import try_parse_utc
from outlier_nfl.tape_nflverse import _name_key
from outlier_nfl.utils import file_lock

logger = logging.getLogger(__name__)

TUESDAY = 1  # date.weekday()

SNAPSHOT_FIELDS = (
    "event_id",
    "event_starts_at",
    "matchup",
    "team",
    "player_name",
    "market",
    "position",
    "line",
    "scope",
    "best_odds",
    "implied_probability",
    "is_consensus_line",
)


def week_start(day: date | str) -> date:
    """Tuesday on or before ``day``: the first day of the NFL betting week."""
    if isinstance(day, str):
        day = date.fromisoformat(day[:10])
    return day - timedelta(days=(day.weekday() - TUESDAY) % 7)


def snapshot_path(nfl_dir: Path | str, day: date | str) -> Path:
    return Path(nfl_dir) / "snapshots" / f"nfl_prop_snapshots_{week_start(day).isoformat()}.jsonl"


def prop_key(record: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    """Line-independent identity of a prop side (the consensus line itself can move)."""
    return (
        str(record.get("event_id") or ""),
        _name_key(record.get("player_name")),
        str(record.get("market") or "").upper(),
        str(record.get("position") or "").upper(),
        str(record.get("scope") or "full_game"),
    )


def _books(record: Mapping[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for entry in record.get("books") or []:
        if isinstance(entry, Mapping) and entry.get("book") and entry.get("odds") is not None:
            try:
                out[str(entry["book"]).lower()] = int(entry["odds"])
            except (TypeError, ValueError):
                continue
    return out


def append_snapshot(
    nfl_dir: Path | str,
    slate_date: str,
    props: Iterable[Mapping[str, Any]],
    taken_at: str,
    run_id: str | None = None,
) -> Path:
    """Append one run's consensus full-game props; returns the JSONL path.

    ``run_id`` ties each row to the run bundle that captured it.
    """
    path = snapshot_path(nfl_dir, slate_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for rec in props:
        if not rec.get("is_consensus_line"):
            continue
        if str(rec.get("scope") or "full_game") != "full_game":
            continue
        row = {k: rec.get(k) for k in SNAPSHOT_FIELDS}
        row["slate_date"] = slate_date
        row["taken_at"] = taken_at
        if run_id is not None:
            row["run_id"] = run_id
        row["books"] = _books(rec)
        lines.append(json.dumps(row, sort_keys=True))
    if lines:
        with file_lock(path):
            if run_id is not None:
                # The same run never records the same prop twice (F27): a
                # retried or re-published run ID appends only what is new.
                seen = _run_keys(path, run_id)
                lines = [x for x in lines if prop_key(json.loads(x)) not in seen]
            if lines:
                with open(path, "a", encoding="utf-8") as handle:
                    handle.write("\n".join(lines) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
    return path


def _run_keys(path: Path, run_id: str) -> set[tuple[str, str, str, str, str]]:
    """prop keys already in ``path`` for ``run_id`` (torn lines skipped)."""
    keys: set[tuple[str, str, str, str, str]] = set()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return keys
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("run_id") == run_id:
            keys.add(prop_key(row))
    return keys


def load_snapshots(path: Path | str, as_of_utc: datetime | None = None) -> list[dict[str, Any]]:
    """Snapshot rows; a torn trailing line from a crashed run is skipped.

    With ``as_of_utc`` only rows whose parsed ``taken_at`` is at or before the
    cutoff are returned (rows without a readable aware timestamp are dropped),
    so a replay never sees prices captured after its prediction time (F01).
    """
    rows: list[dict[str, Any]] = []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Skipping unreadable snapshot line in %s", path)
            continue
        if not isinstance(row, dict):
            continue
        if as_of_utc is not None:
            taken = try_parse_utc(row.get("taken_at"))
            if taken is None or taken > as_of_utc:
                continue
        rows.append(row)
    return rows


def movement_index(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[str, ...], dict[str, Any]]:
    """First-seen vs latest price per prop side, over distinct runs (``taken_at``)."""
    grouped: dict[tuple[str, ...], list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(prop_key(row), []).append(row)
    out: dict[tuple[str, ...], dict[str, Any]] = {}
    for key, items in grouped.items():
        items = sorted(items, key=lambda r: str(r.get("taken_at") or ""))
        first, last = items[0], items[-1]
        runs = len({str(r.get("taken_at") or "") for r in items})
        out[key] = {
            "snapshots": runs,
            "open_taken_at": first.get("taken_at"),
            "open_line": first.get("line"),
            "open_odds": first.get("best_odds"),
            "open_implied": first.get("implied_probability"),
            "open_books": first.get("books") or {},
            "last_taken_at": last.get("taken_at"),
            "last_line": last.get("line"),
            "last_odds": last.get("best_odds"),
            "last_implied": last.get("implied_probability"),
            "last_books": last.get("books") or {},
        }
    return out
