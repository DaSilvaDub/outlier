"""Authoritative pack snapshot for the deterministic verdict validator.

Builds a read-only index over an on-disk pack -- candidates, game totals, team
totals, players, injuries, event-lock timestamps, and the portfolio policy --
that outlier_scrapers.verdict_gate consults as the sole source of pack truth.
This module performs no gate logic itself (no locked-market, injury, or stake
checks): it only assembles a faithful snapshot. See
docs/plans/2026-08-12-structured-ai-verdicts.md 'Authoritative index'.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from outlier_scrapers import pack, portfolio
from outlier_scrapers import runner_common as rc
from outlier_scrapers.portfolio import PortfolioPolicy
from outlier_scrapers.utils import drop_locked_events

CANDIDATES_STREAM = "candidates"
GAME_TOTALS_STREAM = "game_totals"
TEAM_TOTALS_STREAM = "team_totals"


class PackIntegrityError(ValueError):
    """The pack on disk is internally inconsistent -- not a model error."""


@dataclass(frozen=True)
class IndexedRow:
    stream: str
    market_id: str
    outcome_id: str
    data: dict[str, str]


@dataclass(frozen=True)
class UnindexedTotal:
    totals_id: str
    stream: str
    data: dict[str, str]


@dataclass(frozen=True)
class PlayerInfo:
    name: str
    team: str
    event_id: str


@dataclass(frozen=True)
class PackIndex:
    candidates_sha256: str
    game_totals_sha256: str
    team_totals_sha256: str
    rows: dict[str, IndexedRow]
    dropped: dict[str, IndexedRow]
    unindexed_totals: tuple[UnindexedTotal, ...]
    players: dict[str, PlayerInfo]
    injuries: dict[str, str]
    locks: dict[str, str]
    policy: PortfolioPolicy


def _read_candidates_rows(pack_dir: Path) -> list[dict[str, str]]:
    path = pack_dir / "candidates.csv"
    if not path.exists():
        raise PackIntegrityError(f"candidates.csv does not exist at {path}")
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != pack.CANDIDATES_HEADER:
            raise PackIntegrityError("candidates.csv header does not match pack.CANDIDATES_HEADER")
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _parse_totals_stream(
    pack_dir: Path, *, filename: str, stream: str, parser
) -> tuple[list[dict[str, str]], str]:
    file_path = pack_dir / filename
    raw = file_path.read_bytes() if file_path.exists() else None
    sha256 = rc.sha256_bytes(raw) if raw is not None else (
        rc.empty_game_totals_hash() if stream == GAME_TOTALS_STREAM else rc.empty_team_totals_hash()
    )
    try:
        rows = parser(raw)
    except rc.RunnerError as exc:
        raise PackIntegrityError(str(exc)) from exc
    return rows, sha256


def _add_row(
    rows: dict[str, IndexedRow],
    unindexed_totals: list[UnindexedTotal],
    *,
    stream: str,
    outcome_id_key: str,
    row: dict[str, str],
) -> None:
    outcome_id = str(row.get(outcome_id_key) or "")
    market_id = str(row.get("market_id") or "")
    if not outcome_id:
        totals_id = str(row.get("totals_id") or market_id)
        unindexed_totals.append(UnindexedTotal(totals_id=totals_id, stream=stream, data=dict(row)))
        return
    if outcome_id in rows:
        raise PackIntegrityError(
            f"Duplicate outcome_id {outcome_id!r} across pack streams "
            f"(already indexed from stream {rows[outcome_id].stream!r}, "
            f"now also present in stream {stream!r})."
        )
    rows[outcome_id] = IndexedRow(stream=stream, market_id=market_id, outcome_id=outcome_id, data=dict(row))


def build_pack_index(
    pack_dir: Path,
    *,
    now: datetime | None = None,
    policy_path: Path | str | None = None,
) -> PackIndex:
    """Build the authoritative index for the pack at `pack_dir`.

    `now` governs the build-time lock filter applied to candidates.csv
    (defaults to the current time). This is a courtesy snapshot only --
    verdict_gate.py re-checks `locks` against its own `now` at validation
    time, since an event can lock between index build and gate evaluation.
    `policy_path` overrides where the portfolio policy is loaded from
    (primarily for test isolation); None uses the production default.
    """
    candidate_rows = _read_candidates_rows(pack_dir)
    kept, dropped_rows = drop_locked_events(candidate_rows, now=now)

    game_totals_rows, game_totals_sha256 = _parse_totals_stream(
        pack_dir, filename=rc.GAME_TOTALS_NAME, stream=GAME_TOTALS_STREAM, parser=rc.parse_game_totals
    )
    team_totals_rows, team_totals_sha256 = _parse_totals_stream(
        pack_dir, filename=rc.TEAM_TOTALS_NAME, stream=TEAM_TOTALS_STREAM, parser=rc.parse_team_totals
    )
    try:
        _, candidates_sha256 = rc.validate_candidates(pack_dir, allow_empty=True)
    except rc.RunnerError as exc:
        raise PackIntegrityError(str(exc)) from exc

    rows: dict[str, IndexedRow] = {}
    dropped: dict[str, IndexedRow] = {}
    unindexed_totals: list[UnindexedTotal] = []

    for row in kept:
        _add_row(rows, unindexed_totals, stream=CANDIDATES_STREAM, outcome_id_key="outcome_id", row=row)
    for row in dropped_rows:
        outcome_id = str(row.get("outcome_id") or "")
        market_id = str(row.get("market_id") or "")
        if outcome_id:
            dropped[outcome_id] = IndexedRow(
                stream=CANDIDATES_STREAM, market_id=market_id, outcome_id=outcome_id, data=dict(row)
            )
    for row in game_totals_rows:
        _add_row(rows, unindexed_totals, stream=GAME_TOTALS_STREAM, outcome_id_key="outcome_id", row=row)
    for row in team_totals_rows:
        _add_row(rows, unindexed_totals, stream=TEAM_TOTALS_STREAM, outcome_id_key="outcome_id", row=row)

    players: dict[str, PlayerInfo] = {}
    injuries: dict[str, str] = {}
    locks: dict[str, str] = {}
    for row in [*kept, *dropped_rows]:
        player_id = str(row.get("player_id") or "")
        if player_id and player_id not in players:
            players[player_id] = PlayerInfo(
                name=str(row.get("selection") or ""),
                team=str(row.get("team_name") or row.get("team") or ""),
                event_id=str(row.get("event_id") or ""),
            )
        event_id = str(row.get("event_id") or "")
        if event_id:
            injury_flags = str(row.get("injury_flags") or "")
            if injury_flags and event_id not in injuries:
                injuries[event_id] = injury_flags
            starts_at = str(row.get("_event_starts_at") or "")
            if starts_at and event_id not in locks:
                locks[event_id] = starts_at

    policy = portfolio.load_portfolio_policy(policy_path)

    return PackIndex(
        candidates_sha256=candidates_sha256,
        game_totals_sha256=game_totals_sha256,
        team_totals_sha256=team_totals_sha256,
        rows=rows,
        dropped=dropped,
        unindexed_totals=tuple(unindexed_totals),
        players=players,
        injuries=injuries,
        locks=locks,
        policy=policy,
    )
