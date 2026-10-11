"""Player usage context for NFL props: vacated volume and efficiency regression.

Implements the runbook's personnel-vacancy re-basing and multi-window pillars
from nflverse ``stats_player_week`` plus ffopportunity expected stats. Hit rates
earned in an old role are stale once a teammate is out, so these signals
re-base projections; they do not claim the market missed public injury news.

Backtest (2023-2025 regular season, WR/TE/RB with >= 2 prior games), vs. each
player's prior per-game average:

- a >= 18% target-share teammate newly out (played the team's previous game):
  remaining targets +15% (mean) vs -2.5%;
- a >= 35% carry-share RB out: remaining RB carries 1.83x vs 1.03x;
- receiving yards > 25% above expected: next week 0.70x median (0.86x others),
  > 25% below expected: 1.01x; rushing 0.78x / 0.98x vs 0.89x;
- last-2-week targets predicted worse than the season average, so there is
  deliberately no recent-trend signal.

Signals are named ``PropSignal``s so ``apply_matchup_signals`` applies them.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, replace
import logging
from typing import Any, Callable, Iterable, Mapping

from outlier_nfl.matchup import MatchupScript, PropSignal
from outlier_nfl.tape_nflverse import NFLVERSE_RELEASES, _name_key, _team, fetch_csv

logger = logging.getLogger(__name__)

PLAYER_WEEK_URL = NFLVERSE_RELEASES + "/stats_player/stats_player_week_{season}.csv"
EXPECTED_URL = (
    "https://github.com/ffverse/ffopportunity/releases/download/latest-data/ep_weekly_{season}.csv"
)

SKILL_POSITIONS = ("WR", "TE", "RB")
MIN_GAMES = 2

VACATED_TARGET_SHARE = 0.18
TARGET_BENEFICIARY_SHARE = 0.08
VACATED_TARGETS_ADJ = 0.10

VACATED_CARRY_SHARE = 0.35
CARRY_BENEFICIARY_SHARE = 0.10
VACATED_CARRIES_ADJ = 0.25

EFFICIENCY_GAP = 0.25
MIN_EXPECTED_REC_YDS = 15.0
MIN_EXPECTED_RUSH_YDS = 20.0
HOT_ADJ = -0.10
COLD_ADJ = 0.08

TARGET_MARKETS = ("RECEIVING_TARGETS", "REC", "REC_YDS")
CARRY_MARKETS = ("RUSH_ATT", "RUSH_YDS")

FetchRows = Callable[[str], list[dict[str, str]]]


@dataclass(frozen=True)
class PlayerUsage:
    """Per-game usage before the slate for one skill player."""

    player: str
    player_id: str
    team: str
    position: str
    games: int
    target_share: float
    carry_share: float
    targets_pg: float
    carries_pg: float
    rec_yds_pg: float
    rush_yds_pg: float
    rec_yds_exp_pg: float | None = None
    rush_yds_exp_pg: float | None = None
    last_week: int = 0
    team_last_week: int = 0
    # F22: games with expected stats paired on the same season/week/game, the
    # share of games that had them, and the current-team segment size. The
    # shares/per-game volume above are current-team; *_all keep full history.
    expected_games: int = 0
    expected_coverage: float = 0.0
    team_games: int = 0
    target_share_all: float = 0.0
    carry_share_all: float = 0.0
    targets_pg_all: float = 0.0
    carries_pg_all: float = 0.0
    rec_yds_paired_pg: float | None = None
    rush_yds_paired_pg: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _f(value: Any) -> float:
    try:
        return float(value) if value not in (None, "", "NA") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _game_key(pid: str, r: Mapping[str, str]) -> tuple[str, str, int, str]:
    return (pid, str(r.get("season") or ""), int(_f(r.get("week"))), str(r.get("game_id") or ""))


def _expected_by_game(
    expected_rows: Iterable[Mapping[str, str]],
) -> dict[tuple[str, str, int, str], Mapping[str, str]]:
    """Expected rows keyed by player/season/week/game (ffopportunity columns).

    Conflicting duplicates for one player-game drop that game from pairing;
    identical duplicates count once (F22).
    """
    out: dict[tuple[str, str, int, str], Mapping[str, str]] = {}
    conflicted: set[tuple[str, str, int, str]] = set()
    for e in expected_rows:
        pid = str(e.get("player_id") or "")
        if not pid:
            continue
        key = _game_key(pid, e)
        prior = out.get(key)
        if prior is not None and dict(prior) != dict(e):
            conflicted.add(key)
        out.setdefault(key, e)
    for key in conflicted:
        out.pop(key, None)
    return out


def build_profiles(
    player_rows: Iterable[Mapping[str, str]],
    expected_rows: Iterable[Mapping[str, str]] = (),
    before_week: int | None = None,
) -> dict[str, PlayerUsage]:
    """Usage per player id from regular-season games before ``before_week``.

    Carry share is the player's carries over all of his team's carries that
    week; players need ``MIN_GAMES`` games. Team is the latest team played for;
    shares and per-game volume cover games with that team only (F22), with full
    history in the ``*_all`` fields. Expected yards are compared with actual
    yards over the same season/week/game only.
    """
    rows: list[Mapping[str, str]] = []
    seen_games: set[tuple[str, str, int]] = set()
    for r in player_rows:
        if r.get("season_type") != "REG":
            continue
        week = int(_f(r.get("week")))
        if before_week is not None and week >= before_week:
            continue
        # One row per player-game: a duplicated player-week counts once (F22).
        key = (str(r.get("player_id") or ""), str(r.get("season") or ""), week)
        if key[0] and key in seen_games:
            continue
        seen_games.add(key)
        rows.append(r)
    team_carries: dict[tuple[str, str, int], float] = defaultdict(float)
    team_last: dict[str, int] = defaultdict(int)
    for r in rows:
        team, week = _team(r.get("team")), int(_f(r.get("week")))
        team_carries[(team, str(r.get("season") or ""), week)] += _f(r.get("carries"))
        team_last[team] = max(team_last[team], week)
    expected = _expected_by_game(expected_rows)

    games: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for r in rows:
        if r.get("position") in SKILL_POSITIONS and r.get("player_id"):
            games[str(r["player_id"])].append(r)

    def share(r: Mapping[str, str]) -> float:
        total = team_carries[(_team(r.get("team")), str(r.get("season") or ""),
                              int(_f(r.get("week"))))]
        return _f(r.get("carries")) / total if total else 0.0

    def mean(values: list[float], digits: int) -> float:
        return round(sum(values) / len(values), digits) if values else 0.0

    out: dict[str, PlayerUsage] = {}
    for pid, g in games.items():
        if len(g) < MIN_GAMES:
            continue
        g.sort(key=lambda r: (str(r.get("season") or ""), int(_f(r.get("week")))))
        n = len(g)
        latest = g[-1]
        team = _team(latest.get("team"))
        # Current-team segment: a traded player's old-team volume is not his
        # role now (F22). Full history is kept separately in the *_all fields.
        seg = [r for r in g if _team(r.get("team")) == team]
        # Actual and expected over the same games only (F22).
        paired = [(r, expected[k]) for r in g if (k := _game_key(pid, r)) in expected]
        rec_exp = rush_exp = None
        rec_act = rush_act = 0.0
        if len(paired) >= MIN_GAMES:
            rec_exp = mean([_f(e.get("rec_yards_gained_exp")) for _, e in paired], 1)
            rush_exp = mean([_f(e.get("rush_yards_gained_exp")) for _, e in paired], 1)
            rec_act = mean([_f(r.get("receiving_yards")) for r, _ in paired], 1)
            rush_act = mean([_f(r.get("rushing_yards")) for r, _ in paired], 1)
        out[pid] = PlayerUsage(
            player=str(latest.get("player_display_name") or latest.get("player_name") or pid),
            player_id=pid,
            team=team,
            position=str(latest.get("position") or ""),
            games=n,
            target_share=mean([_f(r.get("target_share")) for r in seg], 4),
            carry_share=mean([share(r) for r in seg], 4),
            targets_pg=mean([_f(r.get("targets")) for r in seg], 2),
            carries_pg=mean([_f(r.get("carries")) for r in seg], 2),
            rec_yds_pg=mean([_f(r.get("receiving_yards")) for r in g], 1),
            rush_yds_pg=mean([_f(r.get("rushing_yards")) for r in g], 1),
            rec_yds_exp_pg=rec_exp,
            rush_yds_exp_pg=rush_exp,
            last_week=int(_f(latest.get("week"))),
            team_last_week=team_last[team],
            expected_games=len(paired),
            expected_coverage=round(len(paired) / n, 4),
            team_games=len(seg),
            target_share_all=mean([_f(r.get("target_share")) for r in g], 4),
            carry_share_all=mean([share(r) for r in g], 4),
            targets_pg_all=mean([_f(r.get("targets")) for r in g], 2),
            carries_pg_all=mean([_f(r.get("carries")) for r in g], 2),
            rec_yds_paired_pg=rec_act if rec_exp is not None else None,
            rush_yds_paired_pg=rush_act if rush_exp is not None else None,
        )
    return out


def _signal(event_id: str, p: PlayerUsage, market: str, side: str, tag: str, reason: str,
            confidence: str, adj: float) -> PropSignal:
    return PropSignal(
        event_id=event_id, player_name=p.player, team=p.team, market=market, side=side,
        tag=tag, reason=reason, confidence=confidence, volume_adjustment=adj,
    )


def usage_signals(
    profiles: Mapping[str, PlayerUsage],
    inactive_by_team: Mapping[str, Iterable[str]],
    event_by_team: Mapping[str, str],
) -> list[PropSignal]:
    """Vacated-volume and efficiency-regression signals for slate teams."""
    inactive = {t: {_name_key(n) for n in names} for t, names in inactive_by_team.items()}
    by_team: dict[str, list[PlayerUsage]] = defaultdict(list)
    for p in profiles.values():
        if p.team in event_by_team:
            by_team[p.team].append(p)

    signals: list[PropSignal] = []
    for team, players in by_team.items():
        event_id = event_by_team[team]
        out_names = inactive.get(team, set())
        # Only newly absent players vacate volume: if a player also missed the
        # team's last game, recent shares already reflect his absence (backtest rule).
        out = [
            p for p in players
            if _name_key(p.player) in out_names and p.last_week == p.team_last_week
            and p.team_games >= MIN_GAMES
        ]
        active = [p for p in players if _name_key(p.player) not in out_names]
        # Shares from fewer than MIN_GAMES games with this team aren't a role (F22).
        role = [p for p in active if p.team_games >= MIN_GAMES]

        lost_targets = [p for p in out if p.target_share >= VACATED_TARGET_SHARE]
        if lost_targets:
            who = ", ".join(f"{p.player} {p.target_share:.0%}" for p in lost_targets)
            for p in role:
                if p.target_share >= TARGET_BENEFICIARY_SHARE:
                    for market in TARGET_MARKETS:
                        signals.append(_signal(
                            event_id, p, market, "OVER", "VACATED_TARGETS",
                            f"{who} target share out; hit rates earned in a smaller role",
                            "MEDIUM", VACATED_TARGETS_ADJ,
                        ))
        lost_carries = [p for p in out if p.position == "RB" and p.carry_share >= VACATED_CARRY_SHARE]
        if lost_carries:
            who = ", ".join(f"{p.player} {p.carry_share:.0%}" for p in lost_carries)
            for p in role:
                if p.position == "RB" and p.carry_share >= CARRY_BENEFICIARY_SHARE:
                    for market in CARRY_MARKETS:
                        signals.append(_signal(
                            event_id, p, market, "OVER", "VACATED_CARRIES",
                            f"{who} carry share out; backup inherits early-down work",
                            "HIGH", VACATED_CARRIES_ADJ,
                        ))

        for p in active:
            for market, actual, exp, floor in (
                ("REC_YDS", p.rec_yds_paired_pg, p.rec_yds_exp_pg, MIN_EXPECTED_REC_YDS),
                ("RUSH_YDS", p.rush_yds_paired_pg, p.rush_yds_exp_pg, MIN_EXPECTED_RUSH_YDS),
            ):
                if actual is None or exp is None or exp < floor:
                    continue
                gap = actual / exp - 1
                if gap > EFFICIENCY_GAP:
                    signals.append(_signal(
                        event_id, p, market, "UNDER", "EFFICIENCY_HOT",
                        f"{actual:.0f} yds/g vs {exp:.0f} expected ({gap:+.0%}); regression risk",
                        "MEDIUM", HOT_ADJ,
                    ))
                elif gap < -EFFICIENCY_GAP:
                    signals.append(_signal(
                        event_id, p, market, "OVER", "EFFICIENCY_COLD",
                        f"{actual:.0f} yds/g vs {exp:.0f} expected ({gap:+.0%}); due to rebound",
                        "MEDIUM", COLD_ADJ,
                    ))
    return signals


def append_signals(
    scripts: Iterable[MatchupScript], signals: Iterable[PropSignal]
) -> list[MatchupScript]:
    """Attach signals to their event's script, with a one-line usage note."""
    grouped: dict[str, list[PropSignal]] = defaultdict(list)
    for s in signals:
        grouped[s.event_id].append(s)
    out: list[MatchupScript] = []
    for script in scripts:
        extra = grouped.get(script.event_id, [])
        if not extra:
            out.append(script)
            continue
        tags = sorted({s.tag for s in extra})
        note = f"Usage: {len({s.player_name for s in extra})} players tagged ({', '.join(tags)})."
        out.append(replace(
            script,
            prop_signals=script.prop_signals + tuple(extra),
            notes=script.notes + (note,),
        ))
    return out


MIN_SEASON, MAX_SEASON = 1999, 2100


def fetch_player_weeks(season: int, fetch_rows: FetchRows = fetch_csv) -> list[dict[str, str]]:
    """nflverse player-week stats for a validated season (URL built here, not by callers)."""
    season = int(season)
    if not MIN_SEASON <= season <= MAX_SEASON:
        raise ValueError(f"Season out of range: {season}")
    return fetch_rows(PLAYER_WEEK_URL.format(season=season))


def load_usage(
    season: int,
    before_week: int | None,
    fetch_rows: FetchRows = fetch_csv,
) -> dict[str, PlayerUsage]:
    """Fetch nflverse player weeks (+ ffopportunity expected stats when available).

    ``before_week`` is the run's as-of cutoff (F01) and is required: ``None``
    used to mean "every week of the season", which admitted games played after
    the slate. Callers without a verified cutoff must skip usage instead.
    """
    if before_week is None:
        raise ValueError("load_usage requires a verified before_week cutoff")
    players = fetch_player_weeks(season, fetch_rows)
    try:
        expected = fetch_rows(EXPECTED_URL.format(season=season))
    except Exception as exc:  # regression signals simply drop out without it
        logger.warning("ffopportunity expected stats unavailable: %s", exc)
        expected = []
    return build_profiles(players, expected, before_week)
