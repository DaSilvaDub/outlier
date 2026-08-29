"""Opponent-adjusted team-strength ridge model for full-game run totals.

Fits per-team offensive/defensive ratings from settled game results via
ridge regression (pure Python, no numpy dependency — consistent with
projections.py's stdlib-only distribution primitives). This module is
infrastructure, not a validated forecast: the totals-pipeline audit gated
a fundamentals model behind "only after >= 1 full season accumulates," and
the ledger currently holds a few weeks of settlements. Nothing here writes
model_prob, edge_pct, or drives sizing — that requires explicit promotion
(see ``promotion_status`` / ``config/fundamentals_promotion.json``), gated
on both a sample-size floor and out-of-sample skill demonstrated through
the totals OOS harness (``feedback.totals_paired_oos_loss``), the same
two-key pattern ``probability_blend`` already uses for the market/model
blend weight.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from outlier_scrapers import paths

SCHEMA_VERSION = 1
DEFAULT_ARTIFACT_PATH = paths.PROJECT_ROOT / "calibration" / "team_strength.json"
DEFAULT_PROMOTION_PATH = paths.PROJECT_ROOT / "config" / "fundamentals_promotion.json"
# Roughly a full MLB season's worth of team-games (30 teams x ~162 games,
# each game counted once => ~2430 team-side observations per full turn of
# the league, ~4860 counting both sides). Matches the totals-pipeline
# audit's own Phase 4 gate: "only after >= 1 full season accumulates."
DEFAULT_PROMOTION_MIN_GAMES = 2000
DEFAULT_RIDGE_LAMBDA = 25.0
DEFAULT_MIN_GAMES_TO_FIT = 30


def _team_key(value: Any) -> str:
    return str(value or "").strip().upper()


def _game_field(game: Any, name: str) -> Any:
    if isinstance(game, Mapping):
        return game.get(name)
    return getattr(game, name, None)


@dataclass(frozen=True)
class _Observation:
    scoring_team: str
    opponent_team: str
    is_home: bool
    score: float


def _observations_from_games(games: Iterable[Any]) -> tuple[list[_Observation], str]:
    """Two scoring observations per game (home and away sides).

    Accepts any object exposing away/home/away_score/home_score/event_date
    as attributes or mapping keys — a plain dict or a dataclass such as
    ``form_source.FinalEvent`` both work, so this module never needs to
    import a specific results-fetching module.
    """
    observations: list[_Observation] = []
    latest = ""
    for game in games:
        away = _team_key(_game_field(game, "away"))
        home = _team_key(_game_field(game, "home"))
        away_score = _game_field(game, "away_score")
        home_score = _game_field(game, "home_score")
        event_date = _game_field(game, "event_date")
        if not away or not home or away_score is None or home_score is None:
            continue
        try:
            away_score = float(away_score)
            home_score = float(home_score)
        except (TypeError, ValueError):
            continue
        observations.append(_Observation(home, away, True, home_score))
        observations.append(_Observation(away, home, False, away_score))
        stamp = str(event_date or "")
        if stamp > latest:
            latest = stamp
    return observations, latest


def _solve_linear_system(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting. Returns None if singular."""
    n = len(vector)
    augmented = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot_row = max(range(col, n), key=lambda r: abs(augmented[r][col]))
        if abs(augmented[pivot_row][col]) < 1e-12:
            return None
        augmented[col], augmented[pivot_row] = augmented[pivot_row], augmented[col]
        pivot = augmented[col][col]
        augmented[col] = [value / pivot for value in augmented[col]]
        for row in range(n):
            if row == col:
                continue
            factor = augmented[row][col]
            if factor == 0.0:
                continue
            augmented[row] = [
                value - factor * pivot_value
                for value, pivot_value in zip(augmented[row], augmented[col])
            ]
    return [augmented[row][n] for row in range(n)]


def _empty_artifact(
    status: str, *, generated_at: str | None, trained_through: str,
    n_games: int, min_games: int, ridge_lambda: float,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "trained_through": trained_through,
        "n_games": n_games,
        "min_games": min_games,
        "ridge_lambda": ridge_lambda,
        "league_avg_runs": None,
        "home_field_advantage": None,
        "teams": {},
        "model_version": "",
    }


def fit_team_strength(
    games: Iterable[Any],
    *,
    ridge_lambda: float = DEFAULT_RIDGE_LAMBDA,
    min_games: int = DEFAULT_MIN_GAMES_TO_FIT,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Fit opponent-adjusted offense/defense ratings via ridge regression.

    Model: ``score = mu + home_field * is_home + offense[team] - defense[opponent]``.
    Ridge (L2 shrinkage toward zero on the offense/defense terms only)
    regularizes two things at once: the offense/defense split is otherwise
    rank-deficient (only their difference is identified), and a team with
    few games shrinks toward league-average instead of an extreme estimate
    from a handful of results.
    """
    if ridge_lambda < 0:
        raise ValueError("ridge_lambda cannot be negative")
    if min_games < 1:
        raise ValueError("min_games must be at least 1")

    observations, trained_through = _observations_from_games(games)
    game_count = len(observations) // 2
    if game_count < min_games:
        return _empty_artifact(
            "insufficient_history", generated_at=generated_at, trained_through=trained_through,
            n_games=game_count, min_games=min_games, ridge_lambda=ridge_lambda,
        )

    teams = sorted(
        {obs.scoring_team for obs in observations} | {obs.opponent_team for obs in observations}
    )
    offense_index = {team: 2 + i for i, team in enumerate(teams)}
    defense_index = {team: 2 + len(teams) + i for i, team in enumerate(teams)}
    n = 2 + 2 * len(teams)
    xtx = [[0.0] * n for _ in range(n)]
    xty = [0.0] * n

    for obs in observations:
        indices = [0, 1, offense_index[obs.scoring_team], defense_index[obs.opponent_team]]
        values = [1.0, 1.0 if obs.is_home else 0.0, 1.0, -1.0]
        for a, va in zip(indices, values):
            xty[a] += va * obs.score
            for b, vb in zip(indices, values):
                xtx[a][b] += va * vb

    for idx in range(2, n):
        xtx[idx][idx] += ridge_lambda

    solution = _solve_linear_system(xtx, xty)
    if solution is None:
        return _empty_artifact(
            "fit_failed", generated_at=generated_at, trained_through=trained_through,
            n_games=game_count, min_games=min_games, ridge_lambda=ridge_lambda,
        )

    team_games: dict[str, int] = {team: 0 for team in teams}
    for obs in observations:
        team_games[obs.scoring_team] += 1

    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "active",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "trained_through": trained_through,
        "n_games": game_count,
        "min_games": min_games,
        "ridge_lambda": ridge_lambda,
        "league_avg_runs": round(solution[0], 6),
        "home_field_advantage": round(solution[1], 6),
        "teams": {
            team: {
                "offense": round(solution[offense_index[team]], 6),
                "defense": round(solution[defense_index[team]], 6),
                "n_games": team_games[team],
            }
            for team in teams
        },
    }
    fingerprint_payload = {key: value for key, value in artifact.items() if key != "generated_at"}
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:12]
    artifact["model_version"] = f"team-strength-ridge-v1-{fingerprint}"
    return artifact


def predict_team_run_mean(
    artifact: Mapping[str, Any] | None, team: Any, opponent: Any, *, is_home: bool
) -> float | None:
    """Projected mean runs for ``team`` hosting/visiting ``opponent``.

    None whenever the artifact isn't active or either team is unrated
    (never seen in the fitted window) — callers must not default a missing
    projection to some fallback mean, since that would silently fabricate a
    forecast for an unrated team.
    """
    if not artifact or artifact.get("status") != "active":
        return None
    teams = artifact.get("teams")
    if not isinstance(teams, Mapping):
        return None
    team_entry = teams.get(_team_key(team))
    opponent_entry = teams.get(_team_key(opponent))
    if not isinstance(team_entry, Mapping) or not isinstance(opponent_entry, Mapping):
        return None
    league_avg = artifact.get("league_avg_runs")
    home_field = artifact.get("home_field_advantage")
    if league_avg is None or home_field is None:
        return None
    mean = (
        float(league_avg)
        + (float(home_field) if is_home else 0.0)
        + float(team_entry["offense"])
        - float(opponent_entry["defense"])
    )
    return max(0.0, mean)


def predict_game_total_mean(
    artifact: Mapping[str, Any] | None, home_team: Any, away_team: Any
) -> float | None:
    """Projected combined game total from both teams' fitted means."""
    home_mean = predict_team_run_mean(artifact, home_team, away_team, is_home=True)
    away_mean = predict_team_run_mean(artifact, away_team, home_team, is_home=False)
    if home_mean is None or away_mean is None:
        return None
    return home_mean + away_mean


def write_artifact(artifact: Mapping[str, Any], output_path: Path = DEFAULT_ARTIFACT_PATH) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary.write_text(json.dumps(dict(artifact), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(output_path)
    return output_path


def load_artifact(path: Path = DEFAULT_ARTIFACT_PATH) -> dict[str, Any] | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        return None
    return payload


def load_promotion_policy(path: Path = DEFAULT_PROMOTION_PATH) -> dict[str, Any]:
    """Load the manual promotion policy.

    Shadow is the default and the fail-safe: an unreadable, malformed, or
    missing policy never lets the fundamentals model drive sizing.
    """
    policy: dict[str, Any] = {
        "mode": "shadow",
        "min_eligible_games": DEFAULT_PROMOTION_MIN_GAMES,
        "model_version": "",
        "promoted_by": "",
        "promoted_at": "",
    }
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return policy
    if not isinstance(payload, Mapping):
        return policy
    mode = str(payload.get("mode") or "shadow").strip().lower()
    policy["mode"] = mode if mode in {"shadow", "live"} else "shadow"
    minimum = payload.get("min_eligible_games")
    try:
        minimum = int(minimum) if minimum is not None else None
    except (TypeError, ValueError):
        minimum = None
    if minimum is not None and minimum >= 0:
        policy["min_eligible_games"] = minimum
    for key in ("model_version", "promoted_by", "promoted_at"):
        policy[key] = str(payload.get(key) or "")
    return policy


def promotion_status(
    artifact: Mapping[str, Any] | None, policy: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Whether the fitted team-strength model may drive live sizing, and why not."""
    resolved = dict(policy) if policy is not None else load_promotion_policy()
    minimum = int(resolved.get("min_eligible_games") or DEFAULT_PROMOTION_MIN_GAMES)
    games = int((artifact or {}).get("n_games") or 0)
    version = str((artifact or {}).get("model_version") or "")
    pinned = str(resolved.get("model_version") or "")
    reasons: list[str] = []
    if not artifact or artifact.get("status") != "active":
        reasons.append("artifact_inactive")
    if str(resolved.get("mode")) != "live":
        reasons.append("policy_shadow")
    if games < minimum:
        reasons.append(f"insufficient_games:{games}<{minimum}")
    if pinned and pinned != version:
        reasons.append("model_version_mismatch")
    return {
        "drives_sizing": not reasons,
        "mode": str(resolved.get("mode")),
        "n_games": games,
        "min_eligible_games": minimum,
        "model_version": version,
        "reasons": reasons,
    }
