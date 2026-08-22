"""Deterministic, provider-independent projection primitives.

Core distribution contracts stay local and training-free. Optional MLB Stats API
adapters attach starter game-log BF/K features for SO projections; league-average
stubs remain audit-only and must not fill independent_model_prob.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError, URLError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProjectionDistribution:
    """A bounded discrete distribution with explicit omitted tail mass."""

    pmf: dict[int, float]
    tail_mass: float
    model_version: str = "projection-v1"

    def __post_init__(self) -> None:
        if any(outcome < 0 or not math.isfinite(prob) or prob < 0 for outcome, prob in self.pmf.items()):
            raise ValueError("PMF outcomes must be nonnegative and probabilities must be finite")
        if not math.isfinite(self.tail_mass) or self.tail_mass < 0:
            raise ValueError("tail_mass must be finite and nonnegative")
        total = sum(self.pmf.values()) + self.tail_mass
        if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError(f"PMF plus tail mass must sum to one, got {total}")

    @property
    def mean(self) -> float:
        return sum(outcome * probability for outcome, probability in self.pmf.items())

    @property
    def variance(self) -> float:
        mean = self.mean
        return sum((outcome - mean) ** 2 * probability for outcome, probability in self.pmf.items())

    def quantile(self, probability: float) -> int:
        if not 0 <= probability <= 1:
            raise ValueError("quantile probability must be between zero and one")
        cumulative = 0.0
        for outcome in sorted(self.pmf):
            cumulative += self.pmf[outcome]
            if cumulative >= probability:
                return outcome
        return max(self.pmf, default=0)

    def partition(self, line: float, side: str) -> dict[str, float]:
        """Return selected-side win, push, and loss probabilities."""

        line = float(line)
        normalized_side = side.strip().upper()
        if normalized_side not in {"OVER", "UNDER"}:
            raise ValueError("side must be OVER or UNDER")
        push = self.pmf.get(int(line), 0.0) if line.is_integer() else 0.0
        maximum = max(self.pmf, default=0)
        if self.tail_mass > 1e-12 and line > maximum:
            raise ValueError("line exceeds bounded PMF support while tail mass remains")
        if normalized_side == "OVER":
            win = self.tail_mass + sum(
                probability for outcome, probability in self.pmf.items() if outcome > line
            )
        else:
            win = sum(probability for outcome, probability in self.pmf.items() if outcome < line)
        return {"win_prob": win, "push_prob": push, "loss_prob": max(0.0, 1.0 - win - push)}

    def to_record(self, *, line: float | None = None, side: str | None = None) -> dict[str, object]:
        record: dict[str, object] = {
            "pmf": {str(outcome): probability for outcome, probability in sorted(self.pmf.items())},
            "tail_mass": self.tail_mass,
            "mean": self.mean,
            "variance": self.variance,
            "quantiles": {str(level): self.quantile(level) for level in (0.5, 0.9, 0.95)},
            "model_version": self.model_version,
        }
        if line is not None and side is not None:
            record["line"] = line
            record["side"] = side.upper()
            record.update(self.partition(line, side))
        return record


def _bounded_distribution(weights: Mapping[int, float], *, model_version: str = "projection-v1") -> ProjectionDistribution:
    cleaned = {int(outcome): max(0.0, float(weight)) for outcome, weight in weights.items()}
    total = sum(cleaned.values())
    if total <= 0:
        raise ValueError("distribution has no positive mass")
    normalized = {outcome: weight / total for outcome, weight in cleaned.items()}
    return ProjectionDistribution(normalized, 0.0, model_version)


def _negative_binomial_pmf(mean: float, dispersion: float, maximum: int) -> dict[int, float]:
    if mean < 0 or dispersion <= 0 or maximum < 0:
        raise ValueError("mean must be nonnegative, dispersion positive, and maximum nonnegative")
    if mean == 0:
        return {0: 1.0}
    p = dispersion / (dispersion + mean)
    return {
        count: math.exp(
            math.lgamma(count + dispersion)
            - math.lgamma(dispersion)
            - math.lgamma(count + 1)
            + dispersion * math.log(p)
            + count * math.log1p(-p)
        )
        for count in range(maximum + 1)
    }


def negative_binomial_distribution(
    mean: float, dispersion: float, *, maximum: int | None = None
) -> ProjectionDistribution:
    """Create a bounded NB2 distribution and retain omitted tail mass."""

    variance = mean + mean * mean / dispersion
    maximum = maximum or max(20, math.ceil(mean + 10 * math.sqrt(max(variance, 1e-12))))
    weights = _negative_binomial_pmf(mean, dispersion, maximum)
    mass = sum(weights.values())
    return ProjectionDistribution(weights, max(0.0, 1.0 - mass))


def poisson_distribution(mean: float, *, maximum: int | None = None) -> ProjectionDistribution:
    """Create a bounded Poisson distribution using only the stdlib."""

    if mean < 0:
        raise ValueError("mean must be nonnegative")
    maximum = maximum or max(20, math.ceil(mean + 10 * math.sqrt(max(mean, 1e-12))))
    if mean == 0:
        return ProjectionDistribution({0: 1.0}, 0.0)
    weights = {0: math.exp(-mean)}
    for outcome in range(1, maximum + 1):
        weights[outcome] = weights[outcome - 1] * mean / outcome
    mass = sum(weights.values())
    return ProjectionDistribution(weights, max(0.0, 1.0 - mass))


def mlb_first_inning_run_distribution(
    home_run_mean: float, away_run_mean: float
) -> ProjectionDistribution:
    """Project total first-inning runs from the two starter/top-order means."""

    home = poisson_distribution(home_run_mean)
    away = poisson_distribution(away_run_mean)
    total: dict[int, float] = {}
    for home_runs, home_probability in home.pmf.items():
        for away_runs, away_probability in away.pmf.items():
            total[home_runs + away_runs] = total.get(home_runs + away_runs, 0.0) + (
                home_probability * away_probability
            )
    mass = sum(total.values())
    return ProjectionDistribution(total, max(0.0, 1.0 - mass))


def binomial_distribution(trials: int, probability: float) -> ProjectionDistribution:
    if trials < 0 or not 0 <= probability <= 1:
        raise ValueError("trials must be nonnegative and probability must be in [0, 1]")
    weights = {
        successes: math.comb(trials, successes)
        * probability**successes
        * (1.0 - probability) ** (trials - successes)
        for successes in range(trials + 1)
    }
    return _bounded_distribution(weights)


def mlb_strikeout_distribution(
    projected_bf: float,
    strikeout_rate: float,
    *,
    workload_dispersion: float = 20.0,
    maximum_bf: int | None = None,
) -> ProjectionDistribution:
    """Mix conditional binomial strikeouts over a stochastic BF workload."""

    if projected_bf < 0 or not 0 <= strikeout_rate <= 1:
        raise ValueError("projected_bf must be nonnegative and strikeout_rate must be in [0, 1]")
    bf_variance = projected_bf + projected_bf**2 / workload_dispersion
    maximum_bf = maximum_bf or max(1, math.ceil(projected_bf + 10 * math.sqrt(max(bf_variance, 1e-12))))
    workload = _negative_binomial_pmf(projected_bf, workload_dispersion, maximum_bf)
    strikeouts: dict[int, float] = {}
    for bf, workload_probability in workload.items():
        for strikeout, conditional_probability in binomial_distribution(bf, strikeout_rate).pmf.items():
            strikeouts[strikeout] = strikeouts.get(strikeout, 0.0) + workload_probability * conditional_probability
    mass = sum(strikeouts.values())
    return ProjectionDistribution(strikeouts, max(0.0, 1.0 - mass))


def mlb_hits_allowed_distribution(
    projected_bf: float, hit_rate: float, *, dispersion: float = 8.0
) -> ProjectionDistribution:
    """Project hits allowed with an NB2 mean driven by workload."""

    if projected_bf < 0 or hit_rate < 0:
        raise ValueError("projected_bf and hit_rate must be nonnegative")
    return negative_binomial_distribution(projected_bf * hit_rate, dispersion)


def mlb_total_bases_distribution(
    plate_appearances: int,
    zero_rate: float,
    positive_outcome_probs: Mapping[int, float],
) -> ProjectionDistribution:
    """Project total bases with a zero hurdle and compound PA outcomes."""

    if plate_appearances < 0 or not 0 <= zero_rate <= 1:
        raise ValueError("plate_appearances must be nonnegative and zero_rate must be in [0, 1]")
    outcomes = {int(value): float(probability) for value, probability in positive_outcome_probs.items()}
    if not outcomes or any(value <= 0 or probability < 0 for value, probability in outcomes.items()):
        raise ValueError("positive outcomes must have positive values and nonnegative probabilities")
    positive_total = sum(outcomes.values())
    if not math.isclose(positive_total, 1.0, abs_tol=1e-9):
        raise ValueError("positive outcome probabilities must sum to one")
    pa = {0: zero_rate, **{value: (1.0 - zero_rate) * probability for value, probability in outcomes.items()}}
    aggregate = {0: 1.0}
    for _ in range(plate_appearances):
        next_aggregate: dict[int, float] = {}
        for current, current_probability in aggregate.items():
            for value, probability in pa.items():
                next_aggregate[current + value] = next_aggregate.get(current + value, 0.0) + current_probability * probability
        aggregate = next_aggregate
    return _bounded_distribution(aggregate)


STARTER_PROJECTED_BF = 22.0
LEAGUE_STRIKEOUT_RATE = 0.225
LEAGUE_AVG_SO_HASH = "so-starter-league-avg-v1"
GAMELOG_SO_HASH = "so-starter-gamelog-v1"
WNBA_MINUTES_HASH = "wnba-minutes-ppm-v1"
AUDIT_ONLY_PROJECTION_HASHES = frozenset({LEAGUE_AVG_SO_HASH, WNBA_MINUTES_HASH})
MLB_STATS_API_BASE = "https://statsapi.mlb.com/api/v1"
ESPN_SEARCH_URL = "https://site.web.api.espn.com/apis/common/v3/search"
ESPN_WNBA_GAMELOG_URL = (
    "https://site.web.api.espn.com/apis/common/v3/sports/basketball/wnba/athletes"
)
DEFAULT_SO_MIN_STARTS = 3
DEFAULT_SO_MAX_STARTS = 8
DEFAULT_SO_MIN_TOTAL_BF = 45

# statsapi.mlb.com teamId keyed by Outlier canonical abbreviations (2026).
MLB_TEAM_STATS_IDS: dict[str, int] = {
    "ATH": 133,
    "ATL": 144,
    "AZ": 109,
    "BAL": 110,
    "BOS": 111,
    "CHC": 112,
    "CIN": 113,
    "CLE": 114,
    "COL": 115,
    "CWS": 145,
    "DET": 116,
    "HOU": 117,
    "KC": 118,
    "LAA": 108,
    "LAD": 119,
    "MIA": 146,
    "MIL": 158,
    "MIN": 142,
    "NYM": 121,
    "NYY": 147,
    "PHI": 143,
    "PIT": 134,
    "SD": 135,
    "SEA": 136,
    "SF": 137,
    "STL": 138,
    "TB": 139,
    "TEX": 140,
    "TOR": 141,
    "WSH": 120,
}
# Curated home-park SO multipliers (1.0 = league average). Source: Statcast-style
# SO park factors compressed to multipliers; missing teams default to 1.0.
HOME_PARK_K_FACTORS: dict[str, float] = {
    "ATH": 1.00,
    "ATL": 1.02,
    "AZ": 0.99,
    "BAL": 1.01,
    "BOS": 0.98,
    "CHC": 1.01,
    "CIN": 1.03,
    "CLE": 1.02,
    "COL": 0.93,
    "CWS": 1.01,
    "DET": 1.02,
    "HOU": 1.01,
    "KC": 0.99,
    "LAA": 1.00,
    "LAD": 1.01,
    "MIA": 1.03,
    "MIL": 1.02,
    "MIN": 1.01,
    "NYM": 1.02,
    "NYY": 1.01,
    "PHI": 1.02,
    "PIT": 1.00,
    "SD": 1.05,
    "SEA": 1.03,
    "SF": 1.04,
    "STL": 0.99,
    "TB": 1.01,
    "TEX": 1.00,
    "TOR": 1.00,
    "WSH": 1.01,
}


def independent_projection_eligible(projection: Mapping[str, object] | None) -> bool:
    """Audit-only digests must not fill independent_model_prob.

    League-avg SO stubs and the WNBA minutes scaffold stay shadow/audit until
    calibrated. Other projection artifacts remain eligible.
    """

    if not isinstance(projection, Mapping):
        return False
    digest = str(projection.get("feature_snapshot_hash") or "")
    return digest not in AUDIT_ONLY_PROJECTION_HASHES


def _default_stats_fetch_json(request_url: str) -> dict[str, object]:
    from urllib.request import Request, urlopen

    request = Request(
        request_url,
        headers={"User-Agent": "outlier-projections/1.0", "Accept": "application/json"},
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("stats payload must be an object")
    return payload


def _normalize_person_name(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


_SELECTION_NAME_RE = re.compile(
    r"^(.*?)\s+(?:OVER|UNDER)\b",
    re.IGNORECASE,
)
_TRAILING_MARKET_RE = re.compile(
    r"\s+(?:SO|STRIKEOUTS?|K|PTS|REB|AST|PRA|PR|PA|RA)$",
    re.IGNORECASE,
)


def _player_name_from_row(row: Mapping[str, object]) -> str:
    player = str(row.get("player") or "").strip()
    if player:
        return player
    selection = str(row.get("selection") or "")
    if " - " in selection:
        return selection.split(" - ", 1)[0].strip()
    match = _SELECTION_NAME_RE.match(selection)
    if not match:
        return ""
    return _TRAILING_MARKET_RE.sub("", match.group(1).strip()).strip()


def _float_stat(value: Any) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def compute_starter_so_features_from_logs(
    splits: Iterable[Mapping[str, object]],
    *,
    min_starts: int = DEFAULT_SO_MIN_STARTS,
    max_starts: int = DEFAULT_SO_MAX_STARTS,
    min_total_bf: int = DEFAULT_SO_MIN_TOTAL_BF,
) -> dict[str, object] | None:
    """Derive starter BF / K-rate from MLB Stats API pitching game logs.

    Uses only gamesStarted appearances. Newest splits first when dates exist.
    Returns None when the sample is too thin — callers must fail closed.
    """
    starts: list[tuple[str, float, float]] = []
    for split in splits:
        if not isinstance(split, Mapping):
            continue
        stat = split.get("stat")
        if not isinstance(stat, Mapping):
            continue
        if int(_float_stat(stat.get("gamesStarted")) or 0) < 1:
            continue
        bf = _float_stat(stat.get("battersFaced"))
        so = _float_stat(stat.get("strikeOuts"))
        if bf is None or so is None or bf <= 0 or so < 0 or so > bf:
            continue
        starts.append((str(split.get("date") or ""), bf, so))
    starts.sort(key=lambda item: item[0], reverse=True)
    kept = starts[: max(0, max_starts)]
    if len(kept) < min_starts:
        return None
    total_bf = sum(item[1] for item in kept)
    total_k = sum(item[2] for item in kept)
    if total_bf < min_total_bf:
        return None
    return {
        "projected_bf": total_bf / len(kept),
        "strikeout_rate": total_k / total_bf,
        "starts": len(kept),
        "total_bf": total_bf,
        "total_k": total_k,
        "feature_source": "mlb_stats_gamelog",
    }


def fetch_pitcher_pitching_game_logs(
    pitcher_id: int | str,
    *,
    season: int,
    fetch_json: Any | None = None,
) -> list[dict[str, object]]:
    """Fetch one pitcher's pitching gameLog splits for ``season``."""
    person_id = str(pitcher_id).strip()
    if not person_id.isdigit():
        return []
    url = (
        f"{MLB_STATS_API_BASE}/people/{person_id}/stats"
        f"?stats=gameLog&group=pitching&season={int(season)}"
    )
    loader = fetch_json or _default_stats_fetch_json
    payload = loader(url)
    stats = payload.get("stats")
    if not isinstance(stats, list) or not stats:
        return []
    splits = stats[0].get("splits") if isinstance(stats[0], Mapping) else None
    if not isinstance(splits, list):
        return []
    return [split for split in splits if isinstance(split, Mapping)]


def fetch_team_batter_k_rate(
    team_code: str,
    *,
    season: int,
    fetch_json: Any | None = None,
) -> float | None:
    """Season batter K rate (SO/PA) for one MLB team abbreviation."""
    team_id = MLB_TEAM_STATS_IDS.get(str(team_code or "").strip().upper())
    if team_id is None:
        return None
    url = (
        f"{MLB_STATS_API_BASE}/teams/{team_id}/stats"
        f"?stats=season&group=hitting&season={int(season)}"
    )
    loader = fetch_json or _default_stats_fetch_json
    payload = loader(url)
    stats = payload.get("stats")
    if not isinstance(stats, list) or not stats:
        return None
    splits = stats[0].get("splits") if isinstance(stats[0], Mapping) else None
    if not isinstance(splits, list) or not splits:
        return None
    stat = splits[0].get("stat") if isinstance(splits[0], Mapping) else None
    if not isinstance(stat, Mapping):
        return None
    strikeouts = _float_stat(stat.get("strikeOuts"))
    plate_appearances = _float_stat(stat.get("plateAppearances"))
    if (
        strikeouts is None
        or plate_appearances is None
        or plate_appearances <= 0
        or strikeouts < 0
        or strikeouts > plate_appearances
    ):
        return None
    return strikeouts / plate_appearances


def park_k_factor_for_venue_team(team_code: str | None) -> float | None:
    """Return curated home-park SO multiplier for a team abbreviation."""
    code = str(team_code or "").strip().upper()
    if not code:
        return None
    factor = HOME_PARK_K_FACTORS.get(code)
    if factor is None:
        return 1.0
    return factor


def enrich_probable_with_so_features(
    by_team: Mapping[str, Mapping[str, object]],
    *,
    season: int,
    fetch_json: Any | None = None,
    min_starts: int = DEFAULT_SO_MIN_STARTS,
    max_starts: int = DEFAULT_SO_MAX_STARTS,
    min_total_bf: int = DEFAULT_SO_MIN_TOTAL_BF,
) -> dict[str, dict[str, object]]:
    """Attach starter SO + opponent/park context onto a probable-pitcher lookup."""
    enriched: dict[str, dict[str, object]] = {}
    cache: dict[str, dict[str, object] | None] = {}
    opponent_cache: dict[str, float | None] = {}
    for team, info in by_team.items():
        row = dict(info) if isinstance(info, Mapping) else {}
        pitcher_id = str(row.get("pitcher_id") or "").strip()
        if row.get("confirmed") and pitcher_id:
            if pitcher_id not in cache:
                try:
                    logs = fetch_pitcher_pitching_game_logs(
                        pitcher_id, season=season, fetch_json=fetch_json
                    )
                    cache[pitcher_id] = compute_starter_so_features_from_logs(
                        logs,
                        min_starts=min_starts,
                        max_starts=max_starts,
                        min_total_bf=min_total_bf,
                    )
                except (HTTPError, URLError, TimeoutError, ValueError, OSError, json.JSONDecodeError) as exc:
                    logger.warning(
                        "SO gamelog features failed for pitcher_id=%s season=%s: %s",
                        pitcher_id,
                        season,
                        exc,
                    )
                    cache[pitcher_id] = None
            features = cache[pitcher_id]
            if features:
                row.update(features)

            opponent = str(row.get("opponent") or "").strip().upper()
            if opponent:
                if opponent not in opponent_cache:
                    try:
                        opponent_cache[opponent] = fetch_team_batter_k_rate(
                            opponent, season=season, fetch_json=fetch_json
                        )
                    except (
                        HTTPError,
                        URLError,
                        TimeoutError,
                        ValueError,
                        OSError,
                        json.JSONDecodeError,
                    ) as exc:
                        logger.warning(
                            "Opponent K%% fetch failed for team=%s season=%s: %s",
                            opponent,
                            season,
                            exc,
                        )
                        opponent_cache[opponent] = None
                if opponent_cache[opponent] is not None:
                    row["opponent_k_rate"] = opponent_cache[opponent]
                    row["opponent_k_source"] = "mlb_stats_team_hitting"

            home_away = str(row.get("home_away") or "").strip().upper()
            venue_team = str(team).strip().upper() if home_away == "HOME" else opponent
            park_factor = park_k_factor_for_venue_team(venue_team)
            if park_factor is not None:
                row["park_k_factor"] = park_factor
                row["park_k_source"] = "curated_home_park_so"
                row["park_team"] = venue_team
        enriched[str(team)] = row
    return enriched


def mlb_so_projection_record(
    row: Mapping[str, object], probable_by_team: Mapping[str, Mapping[str, object]] | None
) -> dict[str, object] | None:
    """Build a starter-SO projection from probable pitchers.

    Confirmed starters with pitcher-specific BF/K features emit an
    independent-eligible hash. Confirmed starters without features fall back to
    the league-average audit stub. Relievers/openers stay blank.
    The betting line is never used as a feature.
    """
    sport = str(row.get("sport") or row.get("league") or "").upper()
    market = str(row.get("market_type") or row.get("market") or row.get("proposition") or "").upper()
    if sport != "MLB":
        return None
    if market not in {"SO", "STRIKEOUTS", "PITCHER_STRIKEOUTS", "K"} and "STRIKEOUT" not in market:
        selection = str(row.get("selection") or "").upper()
        if "STRIKEOUT" not in selection:
            return None
    player = _normalize_person_name(_player_name_from_row(row))
    if not player or not probable_by_team:
        return None
    listed = None
    for info in probable_by_team.values():
        if not isinstance(info, Mapping):
            continue
        if _normalize_person_name(info.get("pitcher")) == player:
            listed = info
            break
    if listed is None or not listed.get("confirmed"):
        return None
    line = row.get("line")
    side = str(row.get("headline_side") or row.get("position") or "").upper()
    if "OVER" in str(row.get("selection") or "").upper():
        side = "OVER"
    elif "UNDER" in str(row.get("selection") or "").upper():
        side = "UNDER"
    if side not in {"OVER", "UNDER"} or line in (None, ""):
        return None
    try:
        line_value = float(str(line).replace("+", ""))
    except (TypeError, ValueError):
        return None

    projected_bf = _float_stat(listed.get("projected_bf"))
    strikeout_rate = _float_stat(listed.get("strikeout_rate"))
    feature_source = str(listed.get("feature_source") or "")
    if (
        feature_source == "mlb_stats_gamelog"
        and projected_bf is not None
        and strikeout_rate is not None
        and projected_bf > 0
        and 0.0 < strikeout_rate < 1.0
    ):
        adjusted_bf, adjusted_rate = apply_so_context_adjustments(
            projected_bf,
            strikeout_rate,
            opponent_k_rate=_float_stat(listed.get("opponent_k_rate")),
            park_k_factor=_float_stat(listed.get("park_k_factor")),
        )
        distribution = mlb_strikeout_distribution(adjusted_bf, adjusted_rate)
        feature_hash = GAMELOG_SO_HASH
    else:
        distribution = mlb_strikeout_distribution(STARTER_PROJECTED_BF, LEAGUE_STRIKEOUT_RATE)
        feature_hash = LEAGUE_AVG_SO_HASH
    record = distribution.to_record(line=line_value, side=side)
    return {
        "status": "eligible",
        "sport": "MLB",
        "row_id": row.get("outcome_id"),
        "event_id": row.get("event_id"),
        "market_id": row.get("market_id"),
        "line": line_value,
        "side": side,
        "feature_snapshot_hash": feature_hash,
        "distribution": record,
    }


def apply_so_context_adjustments(
    projected_bf: float,
    strikeout_rate: float,
    *,
    opponent_k_rate: float | None = None,
    park_k_factor: float | None = None,
) -> tuple[float, float]:
    """Blend optional opponent/park context into starter SO features.

    Missing context leaves the gamelog rate unchanged (fail open on enrichment,
    fail closed on thin gamelog samples upstream).
    """
    rate = strikeout_rate
    if opponent_k_rate is not None and 0.0 < opponent_k_rate < 1.0:
        rate = 0.7 * rate + 0.3 * opponent_k_rate
    if park_k_factor is not None and 0.5 <= park_k_factor <= 1.5:
        rate *= park_k_factor
    rate = min(0.45, max(0.08, rate))
    bf = max(1.0, projected_bf)
    return bf, rate


def compute_wnba_minutes_features(
    recent_minutes: Iterable[float],
    *,
    min_games: int = 3,
    max_games: int = 10,
    recent_points: Iterable[float] | None = None,
) -> dict[str, object] | None:
    """Derive a bounded minutes + points-per-minute projection from recent games.

    Fail closed on thin samples. Used by the WNBA audit scaffold; does not
    auto-promote into live Kelly until calibrated.
    """
    minutes_values = [
        float(value) for value in recent_minutes if _float_stat(value) is not None
    ]
    minutes_values = [value for value in minutes_values if 0.0 <= value <= 48.0]
    if len(minutes_values) < min_games:
        return None
    kept_minutes = minutes_values[: max(0, max_games)]
    mean_minutes = sum(kept_minutes) / len(kept_minutes)
    features: dict[str, object] = {
        "projected_minutes": mean_minutes,
        "games": len(kept_minutes),
        "feature_source": "wnba_minutes_recent",
    }
    if recent_points is not None:
        points_values = [
            float(value) for value in recent_points if _float_stat(value) is not None
        ]
        points_values = [value for value in points_values if value >= 0]
        kept_points = points_values[: len(kept_minutes)]
        if len(kept_points) == len(kept_minutes) and sum(kept_minutes) > 0:
            features["points_per_minute"] = sum(kept_points) / sum(kept_minutes)
            features["feature_source"] = "wnba_espn_gamelog"
    return features


def resolve_wnba_athlete_id(
    player_name: str,
    *,
    fetch_json: Any | None = None,
) -> str | None:
    """Resolve a WNBA player display name to an ESPN athlete id."""
    from urllib.parse import quote

    name = str(player_name or "").strip()
    if not name:
        return None
    url = f"{ESPN_SEARCH_URL}?region=us&lang=en&query={quote(name)}&limit=8&type=player"
    loader = fetch_json or _default_stats_fetch_json
    payload = loader(url)
    items = payload.get("items")
    if not isinstance(items, list):
        return None
    target = _normalize_person_name(name)
    for item in items:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("league") or "").casefold() != "wnba":
            continue
        if str(item.get("type") or "").casefold() != "player":
            continue
        display = _normalize_person_name(item.get("displayName"))
        if display == target or target in display or display in target:
            athlete_id = str(item.get("id") or "").strip()
            if athlete_id.isdigit():
                return athlete_id
    return None


def fetch_wnba_athlete_gamelog_stats(
    athlete_id: str,
    *,
    season: int,
    fetch_json: Any | None = None,
) -> tuple[list[float], list[float]]:
    """Return (minutes, points) lists newest-first from ESPN gamelog."""
    person_id = str(athlete_id).strip()
    if not person_id.isdigit():
        return [], []
    url = f"{ESPN_WNBA_GAMELOG_URL}/{person_id}/gamelog?season={int(season)}"
    loader = fetch_json or _default_stats_fetch_json
    payload = loader(url)
    names = payload.get("names")
    if not isinstance(names, list):
        return [], []
    try:
        minutes_idx = names.index("minutes")
        points_idx = names.index("points")
    except ValueError:
        return [], []
    minutes: list[float] = []
    points: list[float] = []
    season_types = payload.get("seasonTypes")
    if not isinstance(season_types, list):
        return [], []
    for season_type in season_types:
        if not isinstance(season_type, Mapping):
            continue
        categories = season_type.get("categories")
        if not isinstance(categories, list):
            continue
        for category in categories:
            if not isinstance(category, Mapping):
                continue
            events = category.get("events")
            if not isinstance(events, list):
                continue
            for event in events:
                if not isinstance(event, Mapping):
                    continue
                stats = event.get("stats")
                if not isinstance(stats, list):
                    continue
                if max(minutes_idx, points_idx) >= len(stats):
                    continue
                minute_value = _float_stat(stats[minutes_idx])
                point_value = _float_stat(stats[points_idx])
                if minute_value is None or point_value is None:
                    continue
                minutes.append(minute_value)
                points.append(point_value)
    return minutes, points


_WNBA_FEATURE_CACHE: dict[str, dict[str, object] | None] = {}


def get_wnba_points_features(
    player_name: str,
    *,
    season: int,
    fetch_json: Any | None = None,
    cache: dict[str, dict[str, object] | None] | None = None,
) -> dict[str, object] | None:
    """Fetch/cache WNBA minutes+PPM features for one player. Fail closed."""
    key = f"{season}:{_normalize_person_name(player_name)}"
    store = cache if cache is not None else _WNBA_FEATURE_CACHE
    if key in store:
        return store[key]
    try:
        athlete_id = resolve_wnba_athlete_id(player_name, fetch_json=fetch_json)
        if not athlete_id:
            store[key] = None
            return None
        minutes, points = fetch_wnba_athlete_gamelog_stats(
            athlete_id, season=season, fetch_json=fetch_json
        )
        features = compute_wnba_minutes_features(minutes, recent_points=points)
        store[key] = features
        return features
    except (HTTPError, URLError, TimeoutError, ValueError, OSError, json.JSONDecodeError) as exc:
        logger.warning("WNBA minutes features failed for %s: %s", player_name, exc)
        store[key] = None
        return None


def wnba_points_projection_record(
    row: Mapping[str, object],
    *,
    features: Mapping[str, object] | None,
) -> dict[str, object] | None:
    """Shadow-only WNBA points scaffold from minutes * usage rate.

    Returns None unless features are complete. Never writes league averages.
    Audit-only hash: does not fill independent_model_prob.
    """
    sport = str(row.get("sport") or row.get("league") or "").upper()
    market = str(row.get("market_type") or row.get("market") or row.get("proposition") or "").upper()
    if sport != "WNBA":
        return None
    if market not in {"PTS", "POINTS"} and "POINT" not in str(row.get("selection") or "").upper():
        return None
    if not isinstance(features, Mapping):
        return None
    minutes = _float_stat(features.get("projected_minutes"))
    usage_rate = _float_stat(features.get("points_per_minute"))
    if minutes is None or usage_rate is None or minutes <= 0 or usage_rate <= 0:
        return None
    line = row.get("line")
    side = str(row.get("headline_side") or row.get("position") or "").upper()
    if "OVER" in str(row.get("selection") or "").upper():
        side = "OVER"
    elif "UNDER" in str(row.get("selection") or "").upper():
        side = "UNDER"
    if side not in {"OVER", "UNDER"} or line in (None, ""):
        return None
    try:
        line_value = float(str(line).replace("+", ""))
    except (TypeError, ValueError):
        return None
    mean_points = minutes * usage_rate
    # Poisson-like discrete approximation via NB2 with light overdispersion.
    distribution = negative_binomial_distribution(mean_points, dispersion=8.0)
    record = distribution.to_record(line=line_value, side=side)
    return {
        "status": "eligible",
        "sport": "WNBA",
        "row_id": row.get("outcome_id"),
        "event_id": row.get("event_id"),
        "market_id": row.get("market_id"),
        "line": line_value,
        "side": side,
        "feature_snapshot_hash": WNBA_MINUTES_HASH,
        "distribution": record,
    }


def project_mlb_row(row: Mapping[str, object]) -> dict[str, object]:
    """Project one normalized MLB row when its feature snapshot is present."""

    features = row.get("projection_features") or row.get("features") or {}
    if not isinstance(features, Mapping):
        features = {}
    proposition = str(row.get("proposition") or row.get("market") or "").upper()
    try:
        if "STRIKEOUT" in proposition:
            distribution = mlb_strikeout_distribution(
                float(features["projected_bf"]),
                float(features["strikeout_rate"]),
                workload_dispersion=float(features.get("workload_dispersion", 20.0)),
            )
        elif "HITS_ALLOWED" in proposition or proposition in {"PITCHER_HITS", "HITS"}:
            distribution = mlb_hits_allowed_distribution(
                float(features["projected_bf"]), float(features["hit_rate"]), dispersion=float(features.get("dispersion", 8.0))
            )
        elif "TOTAL_BASE" in proposition:
            distribution = mlb_total_bases_distribution(
                int(features["plate_appearances"]),
                float(features["zero_rate"]),
                features["positive_outcome_probs"],
            )
        elif "FIRST_INNING" in proposition or "NRFI" in proposition or "YRFI" in proposition:
            distribution = mlb_first_inning_run_distribution(
                float(features["home_run_mean"]), float(features["away_run_mean"])
            )
        else:
            return {"status": "ineligible", "reason": "unsupported_market", "row_id": row.get("outcome_id")}
    except (KeyError, TypeError, ValueError) as exc:
        return {
            "status": "ineligible",
            "reason": "missing_or_invalid_features",
            "detail": str(exc),
            "row_id": row.get("outcome_id"),
        }

    record = {
        "status": "eligible",
        "sport": row.get("sport") or row.get("league") or "MLB",
        "row_id": row.get("outcome_id"),
        "event_id": row.get("event_id"),
        "market_id": row.get("market_id"),
        "market": row.get("market") or row.get("proposition"),
        "line": row.get("line"),
        "side": row.get("position"),
        "distribution": distribution.to_record(),
        "feature_snapshot_hash": row.get("feature_snapshot_hash"),
    }
    if row.get("line") is not None and row.get("position"):
        partition_side = str(row["position"])
        if "FIRST_INNING" in proposition or "NRFI" in proposition or "YRFI" in proposition:
            partition_side = {"YES": "OVER", "NO": "UNDER"}.get(
                partition_side.upper(), partition_side
            )
        record["distribution"] = distribution.to_record(
            line=float(str(row["line"])), side=partition_side
        )
    return record


def project_rows(rows: Iterable[Mapping[str, object]], sport: str) -> list[dict[str, object]]:
    if sport.upper() != "MLB":
        return [
            {
                "status": "shadow_only",
                "reason": "WNBA_model_phase_two",
                "sport": sport.upper(),
                "row_id": row.get("outcome_id"),
                "event_id": row.get("event_id"),
                "market_id": row.get("market_id"),
            }
            for row in rows
        ]
    return [project_mlb_row(row) for row in rows]


def _write_status(command: str, sport: str, output: Path | None) -> int:
    payload = {
        "command": command,
        "sport": sport.upper(),
        "status": "scaffold-ready",
        "generated_at": datetime.now().astimezone().isoformat(),
    }
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    else:
        print(json.dumps(payload, indent=2))
    return 0


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Independent deterministic projection layer")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("backfill", "train", "project", "validate"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--sport", required=True, choices=("MLB", "WNBA"))
        subparser.add_argument("--date", default=date.today().isoformat())
        subparser.add_argument("--output", type=Path)
        if command == "project":
            subparser.add_argument("--input", type=Path)
        if command in {"backfill", "train"}:
            subparser.add_argument("--from", dest="from_date")
            subparser.add_argument("--to", dest="to_date")
        if command == "validate":
            subparser.add_argument("--artifact", type=Path)
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "project" and args.input:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        rows = payload.get("records", payload) if isinstance(payload, Mapping) else payload
        if not isinstance(rows, list):
            raise ValueError("projection input must be a JSON list or an object containing records")
        result = {
            "sport": args.sport,
            "date": args.date,
            "generated_at": datetime.now().astimezone().isoformat(),
            "projections": project_rows(rows, args.sport),
        }
        rendered = json.dumps(result, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0
    return _write_status(args.command, args.sport, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
