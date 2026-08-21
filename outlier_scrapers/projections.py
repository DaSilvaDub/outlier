"""Deterministic, provider-independent projection primitives.

The first implementation slice deliberately contains no network adapters.  It
provides the distribution and probability contracts that adapters and sport
feature builders can consume without pulling training-only dependencies into
the inference path.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping


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


def independent_projection_eligible(projection: Mapping[str, object] | None) -> bool:
    """League-average SO stubs are audit-only and must not fill independent_model_prob."""

    if not isinstance(projection, Mapping):
        return False
    digest = str(projection.get("feature_snapshot_hash") or "")
    return digest != LEAGUE_AVG_SO_HASH


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


def mlb_so_projection_record(
    row: Mapping[str, object], probable_by_team: Mapping[str, Mapping[str, object]] | None
) -> dict[str, object] | None:
    """Build an independent starter-SO projection from probable pitchers.

    Confirmed starters only. Relievers/openers and unconfirmed names stay blank
    so a league-average guess cannot masquerade as an independent edge.
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
    distribution = mlb_strikeout_distribution(STARTER_PROJECTED_BF, LEAGUE_STRIKEOUT_RATE)
    record = distribution.to_record(line=line_value, side=side)
    return {
        "status": "eligible",
        "sport": "MLB",
        "row_id": row.get("outcome_id"),
        "event_id": row.get("event_id"),
        "market_id": row.get("market_id"),
        "line": line_value,
        "side": side,
        "feature_snapshot_hash": LEAGUE_AVG_SO_HASH,
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
