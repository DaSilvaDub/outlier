"""Historical regulation drive counts from admitted nflverse play-by-play."""
from collections import Counter, defaultdict
from typing import Any

from outlier_nfl.config import normalize_team
from outlier_nfl.drive_sim import OUTCOMES, simulation_receipt
from outlier_nfl.run_context import event_kickoff_utc, game_finished_by, parse_utc

MIN_GAMES = 4


def aggregate_drives(rows, season, before_week, completed):
    """Group by possession and measure scoreboard changes including conversions."""
    drives: dict[tuple[str, int], dict[int, tuple[str, str, int, int]]] = defaultdict(dict)
    for row in rows:
        game = row.get("game_id")
        if not game or game not in completed or row.get("season_type") != "REG":
            continue
        try:
            if int(row["season"]) != season or not 1 <= int(row["week"]) < before_week:
                continue
            if not 1 <= int(row["qtr"]) <= 4:
                continue
            play = int(row["play_id"])
            drive = int(row.get("fixed_drive") or row["drive"])
            scores = tuple(int(float(row[key])) for key in (
                "posteam_score", "posteam_score_post", "defteam_score", "defteam_score_post",
            ))
        except (ValueError, KeyError, TypeError, OverflowError):
            continue
        offense = normalize_team(row.get("posteam"))
        defense = normalize_team(row.get("defteam"))
        if not offense or not defense or {offense, defense} != set(completed[game]):
            continue
        drives[game, drive][play] = (
            offense, defense, scores[1] - scores[0], scores[3] - scores[2],
        )

    games: dict[str, dict[tuple[str, str], Counter[tuple[int, int]]]] = defaultdict(dict)
    for (game, _), plays in drives.items():
        offense, defense, _, _ = plays[min(plays)]
        # A defensive TD and the ensuing PAT can share a fixed drive ID
        # even when the team in possession changes. Keep the original orientation.
        own_points = opponent_points = 0
        for team, _, own_delta, opp_delta in plays.values():
            if team == offense:
                own_points += own_delta
                opponent_points += opp_delta
            else:
                own_points += opp_delta
                opponent_points += own_delta
        outcome = (own_points, opponent_points)
        games[game].setdefault((offense, defense), Counter())[outcome] += 1
    return games


def matchup_receipts(events, games, schedule, as_of, team_codes):
    records: list[dict[str, Any]] = []
    by_id = {row["game_id"]: row for row in schedule if row.get("game_id")}
    for event in events:
        event_id = str(event.get("eventId") or event.get("id") or "")
        kickoff = event_kickoff_utc(event)
        home, away = team_codes(event)
        cutoff = min(kickoff, as_of) if kickoff else None
        result: dict[str, Any] = {"event_id": event_id, "status": "NOT_EXECUTED"}
        records.append(result)
        attack: dict[str, Counter[tuple[int, int]]] = defaultdict(Counter)
        defense: dict[str, Counter[tuple[int, int]]] = defaultdict(Counter)
        samples = defaultdict(set)
        pace: Counter[int] = Counter()
        for game, teams in games.items():
            row = by_id.get(game)
            if not cutoff or not row or row.get("home_score") is None or row.get("away_score") is None:
                continue
            try:
                finished = game_finished_by(parse_utc(row["kickoff_utc"]), cutoff)
            except (ValueError, TypeError, KeyError):
                continue
            if not finished or len(teams) != 2:
                continue
            counts = [sum(outcomes.values()) for outcomes in teams.values()]
            if min(counts) < 5:
                continue
            for (off, opp), outcomes in teams.items():
                attack[off].update(outcomes)
                defense[opp].update(outcomes)
                samples[off].add(game)
            if {home, away}.intersection((row.get("home_team"), row.get("away_team"))):
                pace[min(14, max(8, round(sum(counts) / 2)))] += 1

        if not event_id or not cutoff or min(len(samples[home]), len(samples[away])) < MIN_GAMES or not pace:
            result["reason"] = "Insufficient completed pregame drive history"
            continue

        coverage = {}

        def weights(team, opponent):
            own = [attack[team][outcome] for outcome in OUTCOMES]
            allowed = [defense[opponent][outcome] for outcome in OUTCOMES]
            own_count, allowed_count = sum(own), sum(allowed)
            coverage[team] = {
                "offense_supported": own_count,
                "offense_excluded": attack[team].total() - own_count,
                "opposing_defense_supported": allowed_count,
                "opposing_defense_excluded": defense[opponent].total() - allowed_count,
            }
            if min(own_count, allowed_count) < MIN_GAMES * 5:
                raise ValueError("Insufficient supported outcomes")
            return [(a / own_count + b / allowed_count) / 2 for a, b in zip(own, allowed)]

        try:
            inputs = {
                "event_id": event_id, "as_of": cutoff.isoformat(),
                "source": "nflverse regulation PBP, equal offense/defense blend",
                "home": weights(home, away), "away": weights(away, home),
                "drives": [pace[i] / pace.total() for i in range(8, 15)],
            }
            receipt = simulation_receipt(inputs, seed=int.from_bytes(event_id.encode(), "little"))
        except (ValueError, ZeroDivisionError):
            result["reason"] = "Invalid or unsupported drive outcomes"
            continue
        result.update(status="RESEARCH_ONLY", home_team=home, away_team=away,
                      historical_games={team: sorted(samples[team]) for team in (home, away)},
                      drive_coverage=coverage, simulation=receipt)
    return records
