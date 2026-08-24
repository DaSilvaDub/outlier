"""Historical backfill, walk-forward training, and validation for the
independent MLB pitcher-strikeout projection model.

The projection layer ships hand-set priors (shrinkage strength, workload
dispersion, league rates). This module turns them into fitted parameters:

``backfill``
    Walks each pitcher's MLB Stats API game logs and emits one supervised
    sample per start — features built strictly from *earlier* starts, the
    label taken from the start itself, so a sample can never see its own
    outcome.
``train``
    Fits the free parameters on samples before ``--as-of`` with a
    chronological selection split, and writes a versioned, unpromoted
    artifact.
``validate``
    Scores an artifact on samples after its training cutoff (count NLL/MAE,
    CRPS, PIT coverage, and over/under Brier + log loss across the standard
    line ladder) against the shipped defaults.
``promote``
    Manual, audited flip of ``promoted`` — the only thing that lets a trained
    artifact change live inference.

Betting lines are never features. Only free public MLB Stats API endpoints are
used; no reasoning provider is ever contacted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .projections import (
    DEFAULT_SO_MAX_STARTS,
    DEFAULT_SO_MIN_STARTS,
    DEFAULT_SO_MIN_TOTAL_BF,
    MLB_STATS_API_BASE,
    MLB_TEAM_STATS_IDS,
    SO_FEATURE_SCHEMA,
    SO_FEATURE_SCHEMA_HASH,
    SO_MODEL_SCHEMA_VERSION,
    _default_stats_fetch_json,
    _float_stat,
    compute_starter_so_features_from_logs,
    default_so_model_params,
    mlb_strikeout_distribution,
    resolve_so_model_params,
    shrink_starter_so_features,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = SO_MODEL_SCHEMA_VERSION
# The feature contract lives with the features themselves in projections.py, so
# inference can refuse an artifact fitted on a different one.
FEATURE_SCHEMA = SO_FEATURE_SCHEMA
FEATURE_SCHEMA_HASH = SO_FEATURE_SCHEMA_HASH

DEFAULT_MIN_TRAIN_SAMPLES = 200
DEFAULT_MIN_VALIDATION_SAMPLES = 100
SELECTION_HOLDOUT_FRACTION = 0.2
# Cap on samples scored with the full mixture PMF during dispersion search.
DEFAULT_REFINE_SAMPLE_CAP = 1500
VALIDATION_LINE_LADDER: tuple[float, ...] = (3.5, 4.5, 5.5, 6.5, 7.5, 8.5)
PIT_LEVELS: tuple[float, ...] = (0.1, 0.25, 0.5, 0.75, 0.9)

RATE_PRIOR_BF_GRID: tuple[float, ...] = (15.0, 30.0, 45.0, 60.0, 90.0, 120.0, 180.0, 240.0, 360.0)
BF_PRIOR_STARTS_GRID: tuple[float, ...] = (0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0)
BASE_DISPERSION_GRID: tuple[float, ...] = (4.0, 6.0, 8.0, 12.0, 16.0, 24.0, 40.0)
THIN_START_STEP_GRID: tuple[float, ...] = (0.0, 2.5, 5.0, 7.5)


class TrainingError(RuntimeError):
    """Raised when a backfill/train/validate step cannot run honestly."""


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


def artifact_dir() -> Path:
    from . import paths

    return paths.PROJECT_ROOT / "calibration" / "projections"


def dataset_path(sport: str = "MLB") -> Path:
    return artifact_dir() / f"{sport.upper()}_so_samples.jsonl"


def model_path(sport: str = "MLB") -> Path:
    return artifact_dir() / f"{sport.upper()}_so_model.json"


def validation_report_path(sport: str = "MLB") -> Path:
    return artifact_dir() / f"{sport.upper()}_so_validation.json"


def _write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _read_json(path: Path, *, label: str) -> Any:
    """Read a JSON artifact, surfacing IO/parse failure as TrainingError.

    The CLI only catches TrainingError, so an unreadable file must not escape as
    a bare traceback.
    """

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TrainingError(f"unreadable {label} {path}: {exc}") from exc


def _parse_date(value: Any, *, field: str) -> date:
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise TrainingError(f"{field} must be YYYY-MM-DD (got {value!r})") from exc


# --------------------------------------------------------------------------
# Backfill
# --------------------------------------------------------------------------


def fetch_season_pitcher_ids(
    season: int,
    *,
    fetch_json: Any | None = None,
    team_codes: Sequence[str] | None = None,
) -> list[int]:
    """Full-season pitcher person IDs for every (or a subset of) MLB club."""

    loader = fetch_json or _default_stats_fetch_json
    codes = list(team_codes) if team_codes else sorted(MLB_TEAM_STATS_IDS)
    seen: dict[int, None] = {}
    for code in codes:
        team_id = MLB_TEAM_STATS_IDS.get(str(code).strip().upper())
        if team_id is None:
            continue
        url = (
            f"{MLB_STATS_API_BASE}/teams/{team_id}/roster"
            f"?rosterType=fullSeason&season={int(season)}"
        )
        try:
            payload = loader(url)
        except Exception as exc:  # bounded: one dead club must not kill a backfill
            logger.warning("Roster fetch failed for %s %s: %s", code, season, str(exc)[:200])
            continue
        roster = payload.get("roster")
        if not isinstance(roster, list):
            continue
        for entry in roster:
            if not isinstance(entry, Mapping):
                continue
            position = entry.get("position")
            code_value = (
                str(position.get("code") or "") if isinstance(position, Mapping) else ""
            )
            abbreviation = (
                str(position.get("abbreviation") or "") if isinstance(position, Mapping) else ""
            )
            if code_value != "1" and abbreviation.upper() not in {"P", "SP", "RP"}:
                continue
            person = entry.get("person")
            person_id = _float_stat(person.get("id")) if isinstance(person, Mapping) else None
            if person_id is None:
                continue
            seen.setdefault(int(person_id), None)
    return sorted(seen)


def build_walk_forward_samples(
    splits: Iterable[Mapping[str, object]],
    *,
    pitcher_id: int | str,
    season: int,
    min_starts: int = DEFAULT_SO_MIN_STARTS,
    max_starts: int = DEFAULT_SO_MAX_STARTS,
    min_total_bf: int = DEFAULT_SO_MIN_TOTAL_BF,
) -> list[dict[str, Any]]:
    """One supervised sample per start, featurized from earlier starts only.

    ``compute_starter_so_features_from_logs`` is the same function inference
    uses, so a trained parameter set is fitted on exactly the features the
    daily pack will feed it. Prior splits are passed by date order, which makes
    outcome leakage structurally impossible rather than merely unlikely.
    """

    ordered: list[tuple[str, dict[str, object], float, float]] = []
    for split in splits:
        if not isinstance(split, Mapping):
            continue
        stat = split.get("stat")
        if not isinstance(stat, Mapping):
            continue
        if int(_float_stat(stat.get("gamesStarted")) or 0) < 1:
            continue
        batters_faced = _float_stat(stat.get("battersFaced"))
        strikeouts = _float_stat(stat.get("strikeOuts"))
        game_date = str(split.get("date") or "").strip()
        if not game_date or batters_faced is None or strikeouts is None:
            continue
        if batters_faced <= 0 or strikeouts < 0 or strikeouts > batters_faced:
            continue
        ordered.append((game_date, dict(split), batters_faced, strikeouts))
    ordered.sort(key=lambda item: item[0])

    samples: list[dict[str, Any]] = []
    for index, (game_date, _split, actual_bf, actual_so) in enumerate(ordered):
        history = [item[1] for item in ordered[:index]]
        features = compute_starter_so_features_from_logs(
            history,
            min_starts=min_starts,
            max_starts=max_starts,
            min_total_bf=min_total_bf,
        )
        if features is None:
            continue
        projected_bf = _float_stat(features.get("projected_bf"))
        strikeout_rate = _float_stat(features.get("strikeout_rate"))
        prior_starts = _float_stat(features.get("starts"))
        prior_bf = _float_stat(features.get("total_bf"))
        prior_k = _float_stat(features.get("total_k"))
        if (
            projected_bf is None
            or strikeout_rate is None
            or prior_starts is None
            or prior_bf is None
            or prior_k is None
        ):
            continue
        samples.append(
            {
                "sport": "MLB",
                "market": "SO",
                "date": game_date,
                "season": int(season),
                "pitcher_id": int(str(pitcher_id)),
                "projected_bf": projected_bf,
                "strikeout_rate": strikeout_rate,
                "starts": int(prior_starts),
                "total_bf": prior_bf,
                "total_k": prior_k,
                "actual_bf": float(actual_bf),
                "actual_so": int(actual_so),
                "feature_schema_hash": FEATURE_SCHEMA_HASH,
            }
        )
    return samples


def load_samples(
    path: Path | None = None,
    *,
    sport: str = "MLB",
    start: date | None = None,
    end: date | None = None,
) -> list[dict[str, Any]]:
    """Read the backfilled dataset, chronologically ordered."""

    dataset = Path(path) if path is not None else dataset_path(sport)
    if not dataset.exists():
        raise TrainingError(
            f"no backfilled dataset at {dataset}; run "
            f"`python -m outlier_scrapers.projections backfill --sport {sport.upper()}` first"
        )
    samples: list[dict[str, Any]] = []
    with dataset.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError as exc:
                raise TrainingError(f"{dataset}:{line_number} is not valid JSON: {exc}") from exc
            if not isinstance(record, Mapping):
                continue
            if str(record.get("feature_schema_hash") or "") != FEATURE_SCHEMA_HASH:
                continue
            sample_date = str(record.get("date") or "")
            if not sample_date:
                continue
            if start is not None and sample_date < start.isoformat():
                continue
            if end is not None and sample_date > end.isoformat():
                continue
            samples.append(dict(record))
    samples.sort(key=lambda row: (str(row.get("date")), int(row.get("pitcher_id") or 0)))
    return samples


def backfill(
    sport: str = "MLB",
    *,
    start: str,
    end: str,
    output: Path | None = None,
    fetch_json: Any | None = None,
    team_codes: Sequence[str] | None = None,
    pitcher_limit: int | None = None,
) -> dict[str, Any]:
    """Build the supervised dataset for one sport over a date window."""

    if sport.strip().upper() != "MLB":
        return {
            "status": "skipped",
            "reason": f"no historical model for {sport.upper()}",
            "sample_count": 0,
        }
    start_date = _parse_date(start, field="--from")
    end_date = _parse_date(end, field="--to")
    if end_date < start_date:
        raise TrainingError("--to must not precede --from")

    loader = fetch_json or _default_stats_fetch_json
    samples: list[dict[str, Any]] = []
    pitcher_count = 0
    error_count = 0
    seasons = list(range(start_date.year, end_date.year + 1))
    for season in seasons:
        pitcher_ids = fetch_season_pitcher_ids(
            season, fetch_json=loader, team_codes=team_codes
        )
        if pitcher_limit is not None:
            pitcher_ids = pitcher_ids[: max(0, int(pitcher_limit))]
        for pitcher_id in pitcher_ids:
            pitcher_count += 1
            url = (
                f"{MLB_STATS_API_BASE}/people/{pitcher_id}/stats"
                f"?stats=gameLog&group=pitching&season={int(season)}"
            )
            try:
                payload = loader(url)
            except Exception as exc:  # one unavailable pitcher must not abort the run
                error_count += 1
                logger.warning(
                    "Game-log fetch failed for pitcher %s (%s): %s",
                    pitcher_id,
                    season,
                    str(exc)[:200],
                )
                continue
            stats = payload.get("stats")
            splits = (
                stats[0].get("splits")
                if isinstance(stats, list) and stats and isinstance(stats[0], Mapping)
                else None
            )
            if not isinstance(splits, list):
                continue
            for sample in build_walk_forward_samples(
                [split for split in splits if isinstance(split, Mapping)],
                pitcher_id=pitcher_id,
                season=season,
            ):
                if start_date.isoformat() <= sample["date"] <= end_date.isoformat():
                    samples.append(sample)

    samples.sort(key=lambda row: (row["date"], row["pitcher_id"]))
    destination = Path(output) if output is not None else dataset_path(sport)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        for sample in samples:
            handle.write(json.dumps(sample, sort_keys=True) + "\n")
    return {
        "status": "ok",
        "sport": "MLB",
        "output": str(destination),
        "sample_count": len(samples),
        "pitcher_count": pitcher_count,
        "fetch_error_count": error_count,
        "seasons": seasons,
        "from": start_date.isoformat(),
        "to": end_date.isoformat(),
        "feature_schema_hash": FEATURE_SCHEMA_HASH,
    }


# --------------------------------------------------------------------------
# Scoring primitives
# --------------------------------------------------------------------------


def predict(sample: Mapping[str, Any], params: Mapping[str, float]) -> dict[str, float]:
    """Model-side prediction for one sample, using the inference code path."""

    return shrink_starter_so_features(
        float(sample["projected_bf"]),
        float(sample["strikeout_rate"]),
        starts=int(sample.get("starts") or 0),
        total_bf=_float_stat(sample.get("total_bf")),
        total_k=_float_stat(sample.get("total_k")),
        params=params,
    )


def _binomial_nll(successes: float, trials: float, probability: float) -> float:
    probability = min(1 - 1e-9, max(1e-9, probability))
    failures = max(0.0, trials - successes)
    return -(successes * math.log(probability) + failures * math.log(1.0 - probability))


def _rate_objective(samples: Sequence[Mapping[str, Any]], params: Mapping[str, float]) -> float:
    total = 0.0
    trials = 0.0
    for sample in samples:
        predicted = predict(sample, params)
        total += _binomial_nll(
            float(sample["actual_so"]),
            float(sample["actual_bf"]),
            predicted["strikeout_rate"],
        )
        trials += float(sample["actual_bf"])
    return total / trials if trials > 0 else math.inf


def _bf_objective(samples: Sequence[Mapping[str, Any]], params: Mapping[str, float]) -> float:
    squared = 0.0
    for sample in samples:
        predicted = predict(sample, params)
        squared += (predicted["projected_bf"] - float(sample["actual_bf"])) ** 2
    return math.sqrt(squared / len(samples)) if samples else math.inf


def _count_distribution(sample: Mapping[str, Any], params: Mapping[str, float]) -> Any:
    predicted = predict(sample, params)
    return mlb_strikeout_distribution(
        predicted["projected_bf"],
        predicted["strikeout_rate"],
        workload_dispersion=predicted["workload_dispersion"],
    )


def _count_nll(samples: Sequence[Mapping[str, Any]], params: Mapping[str, float]) -> float:
    total = 0.0
    for sample in samples:
        distribution = _count_distribution(sample, params)
        actual = int(sample["actual_so"])
        probability = distribution.pmf.get(actual)
        if probability is None:
            maximum = max(distribution.pmf, default=0)
            probability = distribution.tail_mass if actual > maximum else 0.0
        total += -math.log(max(probability, 1e-12))
    return total / len(samples) if samples else math.inf


def _chronological_split(
    samples: Sequence[Mapping[str, Any]], fraction: float
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    if not samples:
        return [], []
    cut = max(1, int(round(len(samples) * (1.0 - fraction))))
    cut = min(cut, len(samples) - 1) if len(samples) > 1 else len(samples)
    return list(samples[:cut]), list(samples[cut:])


# --------------------------------------------------------------------------
# Train
# --------------------------------------------------------------------------


def fit_parameters(
    samples: Sequence[Mapping[str, Any]],
    *,
    refine_sample_cap: int = DEFAULT_REFINE_SAMPLE_CAP,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Fit league priors, shrinkage strengths, and workload dispersion.

    Staged rather than a joint grid: the rate prior is identified by the
    binomial likelihood of observed strikeouts, the workload prior by BF error,
    and only the two dispersion parameters need the (expensive) full mixture
    PMF. Every stage selects on a later, unseen chronological slice.
    """

    if not samples:
        raise TrainingError("cannot fit parameters on an empty sample set")

    fit_rows, selection_rows = _chronological_split(samples, SELECTION_HOLDOUT_FRACTION)
    if not selection_rows:
        selection_rows = list(fit_rows)

    params = default_so_model_params()

    total_k = sum(float(row["actual_so"]) for row in fit_rows)
    total_bf = sum(float(row["actual_bf"]) for row in fit_rows)
    if total_bf > 0:
        params["league_strikeout_rate"] = min(0.45, max(0.08, total_k / total_bf))
    params["starter_projected_bf"] = min(
        40.0, max(1.0, sum(float(row["actual_bf"]) for row in fit_rows) / len(fit_rows))
    )

    best_rate_prior = params["rate_prior_bf"]
    best_rate_score = math.inf
    for candidate in RATE_PRIOR_BF_GRID:
        trial = {**params, "rate_prior_bf": candidate}
        score = _rate_objective(selection_rows, trial)
        if score < best_rate_score:
            best_rate_score, best_rate_prior = score, candidate
    params["rate_prior_bf"] = best_rate_prior

    best_bf_prior = params["bf_prior_starts"]
    best_bf_score = math.inf
    for candidate in BF_PRIOR_STARTS_GRID:
        trial = {**params, "bf_prior_starts": candidate}
        score = _bf_objective(selection_rows, trial)
        if score < best_bf_score:
            best_bf_score, best_bf_prior = score, candidate
    params["bf_prior_starts"] = best_bf_prior

    refine_rows = list(selection_rows)
    if refine_sample_cap > 0 and len(refine_rows) > refine_sample_cap:
        stride = math.ceil(len(refine_rows) / refine_sample_cap)
        refine_rows = refine_rows[::stride]

    best_dispersion = (params["base_workload_dispersion"], params["thin_start_dispersion_step"])
    best_count_score = math.inf
    for base in BASE_DISPERSION_GRID:
        for step in THIN_START_STEP_GRID:
            trial = {
                **params,
                "base_workload_dispersion": base,
                "thin_start_dispersion_step": step,
            }
            score = _count_nll(refine_rows, trial)
            if score < best_count_score:
                best_count_score, best_dispersion = score, (base, step)
    params["base_workload_dispersion"], params["thin_start_dispersion_step"] = best_dispersion

    diagnostics = {
        "n_fit": len(fit_rows),
        "n_selection": len(selection_rows),
        "n_dispersion_refine": len(refine_rows),
        "selection_rate_nll_per_bf": round(best_rate_score, 8),
        "selection_bf_rmse": round(best_bf_score, 6),
        "selection_count_nll": round(best_count_score, 6),
    }
    return params, diagnostics


def _model_version(params: Mapping[str, float], trained_through: str, n_train: int) -> str:
    payload = json.dumps(
        {
            "params": {key: round(float(value), 8) for key, value in sorted(params.items())},
            "trained_through": trained_through,
            "n_train": n_train,
            "feature_schema_hash": FEATURE_SCHEMA_HASH,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def train(
    sport: str = "MLB",
    *,
    as_of: str | None = None,
    dataset: Path | None = None,
    output: Path | None = None,
    min_samples: int = DEFAULT_MIN_TRAIN_SAMPLES,
    refine_sample_cap: int = DEFAULT_REFINE_SAMPLE_CAP,
) -> dict[str, Any]:
    """Fit and persist an unpromoted projection-model artifact."""

    if sport.strip().upper() != "MLB":
        return {
            "status": "skipped",
            "reason": f"no trainable model for {sport.upper()}",
            "n_train": 0,
        }
    cutoff = _parse_date(as_of, field="--as-of") if as_of else date.today()
    samples = load_samples(dataset, sport=sport)
    training_rows = [row for row in samples if str(row["date"]) < cutoff.isoformat()]
    destination = Path(output) if output is not None else model_path(sport)

    if len(training_rows) < min_samples:
        artifact = {
            "schema_version": SCHEMA_VERSION,
            "sport": "MLB",
            "market": "SO",
            "status": "insufficient_samples",
            "promoted": False,
            "model_version": "",
            "feature_schema_hash": FEATURE_SCHEMA_HASH,
            "trained_through": cutoff.isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "n_train": len(training_rows),
            "min_samples": min_samples,
            "parameters": default_so_model_params(),
            "metrics": {},
        }
        _write_json(destination, artifact)
        return {**artifact, "output": str(destination)}

    params, diagnostics = fit_parameters(training_rows, refine_sample_cap=refine_sample_cap)
    baseline = default_so_model_params()
    scored_rows = training_rows
    if refine_sample_cap > 0 and len(scored_rows) > refine_sample_cap:
        stride = math.ceil(len(scored_rows) / refine_sample_cap)
        scored_rows = scored_rows[::stride]
    fitted_nll = _count_nll(scored_rows, params)
    baseline_nll = _count_nll(scored_rows, baseline)
    version = _model_version(params, cutoff.isoformat(), len(training_rows))
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "sport": "MLB",
        "market": "SO",
        "status": "trained",
        "promoted": False,
        "model_version": version,
        "feature_schema_hash": FEATURE_SCHEMA_HASH,
        "trained_through": cutoff.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_train": len(training_rows),
        "min_samples": min_samples,
        "parameters": {key: round(float(value), 8) for key, value in params.items()},
        "baseline_parameters": baseline,
        "metrics": {
            **diagnostics,
            "train_count_nll": round(fitted_nll, 6),
            "baseline_count_nll": round(baseline_nll, 6),
            "count_nll_improvement": round(baseline_nll - fitted_nll, 6),
        },
    }
    _write_json(destination, artifact)
    return {**artifact, "output": str(destination)}


# --------------------------------------------------------------------------
# Validate
# --------------------------------------------------------------------------


def _distribution_metrics(
    samples: Sequence[Mapping[str, Any]], params: Mapping[str, float]
) -> dict[str, Any]:
    count = len(samples)
    if not count:
        return {"n": 0}
    nll = 0.0
    absolute_error = 0.0
    squared_error = 0.0
    crps = 0.0
    coverage = {f"q{int(level * 100)}": 0 for level in PIT_LEVELS}
    ladder: dict[str, dict[str, float]] = {
        f"{line}": {"brier": 0.0, "log_loss": 0.0, "n": 0.0} for line in VALIDATION_LINE_LADDER
    }
    for sample in samples:
        distribution = _count_distribution(sample, params)
        actual = int(sample["actual_so"])
        probability = distribution.pmf.get(actual)
        if probability is None:
            maximum = max(distribution.pmf, default=0)
            probability = distribution.tail_mass if actual > maximum else 0.0
        nll += -math.log(max(probability, 1e-12))
        mean = distribution.mean
        absolute_error += abs(mean - actual)
        squared_error += (mean - actual) ** 2
        cumulative = 0.0
        support = max(max(distribution.pmf, default=0), actual)
        for outcome in range(0, support + 1):
            cumulative += distribution.pmf.get(outcome, 0.0)
            crps += (cumulative - (1.0 if actual <= outcome else 0.0)) ** 2
        for level in PIT_LEVELS:
            if actual <= distribution.quantile(level):
                coverage[f"q{int(level * 100)}"] += 1
        for line in VALIDATION_LINE_LADDER:
            partition = distribution.partition(line, "OVER")
            predicted = min(1 - 1e-9, max(1e-9, partition["win_prob"]))
            observed = 1.0 if actual > line else 0.0
            bucket = ladder[f"{line}"]
            bucket["brier"] += (predicted - observed) ** 2
            bucket["log_loss"] += -(
                observed * math.log(predicted) + (1.0 - observed) * math.log(1.0 - predicted)
            )
            bucket["n"] += 1.0
    brier_total = sum(bucket["brier"] for bucket in ladder.values())
    log_loss_total = sum(bucket["log_loss"] for bucket in ladder.values())
    ladder_n = sum(bucket["n"] for bucket in ladder.values()) or 1.0
    return {
        "n": count,
        "count_nll": round(nll / count, 6),
        "count_mae": round(absolute_error / count, 6),
        "count_rmse": round(math.sqrt(squared_error / count), 6),
        "crps": round(crps / count, 6),
        "pit_coverage": {
            key: round(value / count, 6) for key, value in sorted(coverage.items())
        },
        "over_under_brier": round(brier_total / ladder_n, 6),
        "over_under_log_loss": round(log_loss_total / ladder_n, 6),
        "line_ladder": {
            line: {
                "brier": round(bucket["brier"] / bucket["n"], 6),
                "log_loss": round(bucket["log_loss"] / bucket["n"], 6),
                "n": int(bucket["n"]),
            }
            for line, bucket in sorted(ladder.items())
            if bucket["n"]
        },
    }


def _calibration_gap(metrics: Mapping[str, Any]) -> float:
    coverage = metrics.get("pit_coverage")
    if not isinstance(coverage, Mapping) or not coverage:
        return math.inf
    deviations = []
    for level in PIT_LEVELS:
        observed = _float_stat(coverage.get(f"q{int(level * 100)}"))
        if observed is None:
            return math.inf
        deviations.append(abs(observed - level))
    return sum(deviations) / len(deviations)


def validate(
    sport: str = "MLB",
    *,
    artifact: Path | None = None,
    dataset: Path | None = None,
    output: Path | None = None,
    start: str | None = None,
    end: str | None = None,
    min_samples: int = DEFAULT_MIN_VALIDATION_SAMPLES,
    max_calibration_gap: float = 0.08,
) -> dict[str, Any]:
    """Score a trained artifact on samples it was never fitted on."""

    if sport.strip().upper() != "MLB":
        return {"status": "skipped", "reason": f"no model to validate for {sport.upper()}"}
    artifact_path = Path(artifact) if artifact is not None else model_path(sport)
    if not artifact_path.exists():
        raise TrainingError(f"no model artifact at {artifact_path}; run `train` first")
    payload = _read_json(artifact_path, label="model artifact")
    if not isinstance(payload, Mapping):
        raise TrainingError(f"model artifact {artifact_path} is not an object")
    if int(_float_stat(payload.get("schema_version")) or 0) != SCHEMA_VERSION:
        raise TrainingError(
            f"model artifact schema {payload.get('schema_version')!r} is incompatible "
            f"with schema {SCHEMA_VERSION}"
        )
    if str(payload.get("feature_schema_hash") or "") != FEATURE_SCHEMA_HASH:
        raise TrainingError(
            "model artifact was fitted on a different feature schema "
            f"({payload.get('feature_schema_hash')!r} != {FEATURE_SCHEMA_HASH})"
        )
    if str(payload.get("status") or "") != "trained":
        raise TrainingError(f"model artifact status is {payload.get('status')!r}, not 'trained'")

    trained_through = _parse_date(payload.get("trained_through"), field="trained_through")
    window_start = _parse_date(start, field="--from") if start else trained_through
    window_start = max(window_start, trained_through)
    window_end = _parse_date(end, field="--to") if end else None
    holdout = [
        row
        for row in load_samples(dataset, sport=sport, start=window_start, end=window_end)
        if str(row["date"]) >= trained_through.isoformat()
    ]

    params = resolve_so_model_params(payload.get("parameters"))
    baseline = default_so_model_params()
    fitted_metrics = _distribution_metrics(holdout, params)
    baseline_metrics = _distribution_metrics(holdout, baseline)
    gap = _calibration_gap(fitted_metrics)

    if len(holdout) < min_samples or not fitted_metrics.get("n"):
        verdict = "insufficient_data"
    elif (
        fitted_metrics["count_nll"] <= baseline_metrics["count_nll"]
        and fitted_metrics["over_under_brier"] <= baseline_metrics["over_under_brier"]
        and gap <= max_calibration_gap
    ):
        verdict = "pass"
    else:
        verdict = "fail"

    report = {
        "schema_version": SCHEMA_VERSION,
        "sport": "MLB",
        "market": "SO",
        "verdict": verdict,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifact": str(artifact_path),
        "model_version": str(payload.get("model_version") or ""),
        "trained_through": trained_through.isoformat(),
        "holdout_from": window_start.isoformat(),
        "holdout_to": window_end.isoformat() if window_end else "",
        "min_samples": min_samples,
        "max_calibration_gap": max_calibration_gap,
        "calibration_gap": round(gap, 6) if gap != math.inf else None,
        "metrics": fitted_metrics,
        "baseline_metrics": baseline_metrics,
    }
    _write_json(Path(output) if output is not None else validation_report_path(sport), report)
    return report


# --------------------------------------------------------------------------
# Promote
# --------------------------------------------------------------------------


def promote(
    sport: str = "MLB",
    *,
    artifact: Path | None = None,
    actor: str = "",
    require_validation: bool = True,
    validation: Path | None = None,
) -> dict[str, Any]:
    """Flip an artifact to promoted, recording who/when/version.

    Promotion is the only step that changes live inference, so it stays manual
    and refuses an artifact whose validation report does not pass.
    """

    if sport.strip().upper() != "MLB":
        raise TrainingError(f"no promotable model for {sport.upper()}")
    artifact_path = Path(artifact) if artifact is not None else model_path(sport)
    if not artifact_path.exists():
        raise TrainingError(f"no model artifact at {artifact_path}")
    payload = _read_json(artifact_path, label="model artifact")
    if not isinstance(payload, Mapping) or str(payload.get("status") or "") != "trained":
        raise TrainingError("only a trained artifact can be promoted")
    if require_validation:
        report_path = Path(validation) if validation is not None else validation_report_path(sport)
        if not report_path.exists():
            raise TrainingError(f"no validation report at {report_path}; run `validate` first")
        report = _read_json(report_path, label="validation report")
        if not isinstance(report, Mapping) or report.get("verdict") != "pass":
            raise TrainingError(
                f"validation verdict is {(report or {}).get('verdict')!r}, not 'pass'"
            )
        if str(report.get("model_version") or "") != str(payload.get("model_version") or ""):
            raise TrainingError("validation report does not match this artifact's model_version")
    promoted = dict(payload)
    promoted["promoted"] = True
    history = list(promoted.get("promotion_audit") or [])
    history.append(
        {
            "actor": actor or "unspecified",
            "promoted_at": datetime.now(timezone.utc).isoformat(),
            "model_version": str(payload.get("model_version") or ""),
            "trained_through": str(payload.get("trained_through") or ""),
        }
    )
    promoted["promotion_audit"] = history
    _write_json(artifact_path, promoted)
    return {
        "status": "promoted",
        "artifact": str(artifact_path),
        "model_version": promoted["model_version"],
        "actor": actor or "unspecified",
    }
