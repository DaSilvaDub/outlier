"""Offline alternate-floor calibration research; never approves bets.

Only sealed live pregame snapshots with an explicit ranking formula and verified
final outcome attestations enter fitting. Book/rerun duplicates contribute one
latest pregame target. Games receive equal weight, and ISO kickoff weeks stay
whole across the chronological training/validation split. All fitted artifacts
remain SHADOW_ONLY, including when the held-out measurements look promising.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from outlier_nfl.alt_floors import RETAIL_BOOKS, TARGET_BOOK_ALIASES, compact_book
from outlier_nfl.boxscore import grade_side
from outlier_nfl.floor_probability import odds_probability
from outlier_nfl.run_context import EASTERN, try_parse_utc
from outlier_nfl.utils import safe_write_json

RANK_VERSION = "nfl_alt_floor_rank_v1"
METHOD_VERSION = "nfl_floor_monotone_sigmoid_v1"
MARKETS = ("PASS_YDS", "RUSH_YDS", "REC_YDS")
IDENTITY_FIELDS = ("event_id", "player_id", "team", "opponent", "market", "scope", "position")


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _identity(row: dict[str, Any]) -> tuple[Any, ...] | None:
    """No name/fuzzy fallback: every member of the wager identity is explicit."""
    if any(not isinstance(row.get(k), str) or not row[k].strip() for k in IDENTITY_FIELDS):
        return None
    line = _number(row.get("line"))
    if (
        row["market"] not in MARKETS
        or row["scope"] != "full_game"
        or row["position"] != "OVER"
        or row["team"] == row["opponent"]
        or line is None
        or line < 0
        or line % 1 != 0.5
    ):
        return None
    return tuple(row[k] for k in IDENTITY_FIELDS) + (line,)


def _problem(problems: list[dict[str, Any]], location: str, reason: str) -> None:
    problems.append({"location": location, "reason": reason})


def _live_context(context: Any) -> bool:
    return (
        isinstance(context, dict)
        and context.get("mode") == "live"
        and context.get("replay") is False
        and context.get("live_inputs_postdate_as_of") is False
        and try_parse_utc(context.get("as_of_utc")) is not None
        and try_parse_utc(context.get("run_started_utc")) is not None
    )


def collect_snapshots(runs_dir: Path) -> dict[str, Any]:
    """Inspect ``runs/*/manifest.json`` without changing any archive file."""
    problems: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    inventory_rows = 0
    manifests = sorted(runs_dir.glob("*/manifest.json"))
    for manifest_path in manifests:
        location = str(manifest_path)
        try:
            manifest_bytes = manifest_path.read_bytes()
            manifest = json.loads(manifest_bytes)
            if not isinstance(manifest, dict):
                raise ValueError("manifest must be an object")
        except (OSError, ValueError) as exc:
            _problem(problems, location, f"invalid_manifest: {exc}")
            continue
        context = manifest.get("context")
        run_id = manifest.get("run_id")
        created = try_parse_utc(manifest.get("created_utc"))
        sealed = try_parse_utc(manifest.get("sealed_utc"))
        if (
            not _live_context(context)
            or not isinstance(run_id, str)
            or run_id != manifest_path.parent.name
            or manifest.get("status") != "OK"
            or created is None
            or sealed is None
        ):
            _problem(problems, location, "run_not_admitted_live_pregame")
            continue
        assert isinstance(context, dict)
        captured = try_parse_utc(context["as_of_utc"])
        started = try_parse_utc(context["run_started_utc"])
        assert captured is not None and started is not None
        if sealed < max(created, started, captured):
            _problem(problems, location, "seal_before_capture_or_start")
            continue
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, list):
            _problem(problems, location, "missing_artifact_index")
            continue
        indexed: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for artifact in artifacts:
            if isinstance(artifact, dict) and isinstance(artifact.get("name"), str):
                indexed[artifact["name"]].append(artifact)
        for name, entries in indexed.items():
            if not (Path(name).name.startswith("nfl_alt_floors_") and name.endswith(".json")):
                continue
            if Path(name).name == "nfl_alt_floors_latest.json":
                continue  # Published alias of the dated export, not another capture.
            inventory_location = f"{run_id}/{name}"
            path = (manifest_path.parent / name).resolve()
            if not path.is_relative_to(manifest_path.parent.resolve()) or len(entries) != 1:
                _problem(problems, inventory_location, "ambiguous_or_escaped_artifact")
                continue
            artifact = entries[0]
            try:
                data = path.read_bytes()
                if _hash(data) != artifact.get("sha256") or len(data) != artifact.get("bytes"):
                    raise ValueError("artifact_hash_or_size_mismatch")
                payload = json.loads(data)
                if not isinstance(payload, dict):
                    raise ValueError("floor payload must be an object")
            except (OSError, ValueError) as exc:
                _problem(problems, inventory_location, str(exc))
                continue
            records = payload.get("calibration_records")
            if not isinstance(records, list):
                # Legacy artifacts are counted, never assigned a guessed formula.
                legacy = payload.get("records", payload.get("master_pool", payload.get("master", [])))
                inventory_rows += len(legacy) if isinstance(legacy, list) else 0
                _problem(problems, inventory_location, "legacy_missing_calibration_records")
                continue
            inventory_rows += len(records)
            if (
                payload.get("run_id") != run_id
                or try_parse_utc(payload.get("captured_at")) != captured
                or payload.get("run_context") != context
            ):
                _problem(problems, inventory_location, "export_provenance_mismatch")
                continue
            for index, row in enumerate(records):
                row_location = f"{inventory_location}#{index}"
                if not isinstance(row, dict):
                    _problem(problems, row_location, "invalid_record")
                    continue
                identity = _identity(row)
                score = _number(row.get("ranking_score"))
                kickoff = try_parse_utc(row.get("event_starts_at"))
                if identity is None:
                    reason = "unsupported_or_missing_exact_identity"
                elif (
                    row.get("quote_eligible") is not True
                    or odds_probability(row.get("target_odds")) is None
                    or compact_book(row.get("target_book")) not in RETAIL_BOOKS | TARGET_BOOK_ALIASES
                ):
                    reason = "quote_ineligible_for_training"
                elif score is None or not 0 <= score <= 1 or row.get("ranking_score_version") != RANK_VERSION:
                    reason = "missing_or_unknown_ranking_formula"
                elif (
                    row.get("run_id") != run_id
                    or try_parse_utc(row.get("captured_at")) != captured
                ):
                    reason = "record_provenance_mismatch"
                elif kickoff is None or sealed >= kickoff:
                    reason = "capture_or_bundle_not_before_kickoff"
                else:
                    candidates.append({
                        **row,
                        "ranking_score": score,
                        "line": identity[-1],
                        "captured_at": captured.isoformat(),
                        "event_starts_at": kickoff.isoformat(),
                        "artifact_sha256": _hash(data),
                        "manifest_sha256": _hash(manifest_bytes),
                        "sealed_utc": sealed.isoformat(),
                    })
                    continue
                _problem(problems, row_location, reason)
    rows = _deduplicate(candidates, problems)
    return {
        "snapshot_policy": "latest_admitted_pregame_per_exact_target",
        "manifests_inspected": len(manifests),
        "inventory_rows": inventory_rows,
        "admitted_rows_before_deduplication": len(candidates),
        "records": rows,
        "problems": problems,
    }


def _deduplicate(rows: list[dict[str, Any]], problems: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    event_identity: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for row in rows:
        key = _identity(row)
        assert key is not None
        groups[key].append(row)
        event_identity[row["event_id"]].add((row["event_starts_at"], "|".join(sorted((row["team"], row["opponent"])))))
    conflicted_events = {event for event, identities in event_identity.items() if len(identities) > 1}
    selected = []
    for key, group in groups.items():
        if key[0] in conflicted_events:
            _problem(problems, str(key), "conflicting_event_identity")
            continue
        latest = max(row["captured_at"] for row in group)
        same_time = [row for row in group if row["captured_at"] == latest]
        if len({row["ranking_score"] for row in same_time}) != 1:
            _problem(problems, str(key), "conflicting_score_at_latest_capture")
            continue
        # Deterministic selection; retail-book multiplicity has no training weight.
        selected.append(sorted(same_time, key=lambda row: (row["run_id"], row["artifact_sha256"]))[-1])
    return sorted(selected, key=lambda row: (row["event_starts_at"], str(_identity(row))))


def settle_snapshots(
    rows: list[dict[str, Any]], outcomes_path: Path | None, *, as_of: datetime
) -> dict[str, Any]:
    """Join explicit verified finals; missing stats and unresolved finals stay absent."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    problems: list[dict[str, Any]] = []
    if outcomes_path is None:
        return {"records": [], "problems": [{"reason": "outcomes_not_supplied"}], "outcomes_sha256": None}
    try:
        data = outcomes_path.read_bytes()
        payload = json.loads(data)
        outcomes = payload.get("records") if isinstance(payload, dict) else None
        if not isinstance(outcomes, list):
            raise ValueError("outcomes must contain records list")
    except (OSError, ValueError) as exc:
        return {"records": [], "problems": [{"reason": f"invalid_outcomes: {exc}"}], "outcomes_sha256": None}
    indexed: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    invalid_keys: set[tuple[Any, ...]] = set()
    for index, outcome in enumerate(outcomes):
        if not isinstance(outcome, dict):
            _problem(problems, str(index), "invalid_outcome")
            continue
        key = _identity(outcome)
        actual = _number(outcome.get("actual"))
        finalized = try_parse_utc(outcome.get("finalized_at"))
        kickoff = try_parse_utc(outcome.get("event_starts_at"))
        if key is None:
            _problem(problems, str(index), "missing_outcome_exact_identity")
            continue
        if (
            outcome.get("status") != "verified_final"
            or actual is None or actual % 1 != 0
            or finalized is None or kickoff is None or not kickoff < finalized <= as_of
            or not isinstance(outcome.get("source_id"), str) or not outcome["source_id"].strip()
            or not _sha(outcome.get("source_sha256"))
        ):
            invalid_keys.add(key)
            _problem(problems, str(index), "outcome_not_verified_final_before_fit")
            continue
        indexed[key].append({**outcome, "actual": actual, "finalized_at": finalized.isoformat(), "event_starts_at": kickoff.isoformat()})
    settled = []
    for row in rows:
        key = _identity(row)
        matches = indexed.get(key, []) if key is not None else []
        if key in invalid_keys or not matches:
            _problem(problems, str(key), "missing_or_invalid_verified_outcome")
            continue
        if len({(o["actual"], o["event_starts_at"]) for o in matches}) != 1:
            _problem(problems, str(key), "conflicting_verified_outcomes")
            continue
        outcome = max(matches, key=lambda o: o["finalized_at"])
        if row["event_starts_at"] != outcome["event_starts_at"]:
            _problem(problems, str(key), "outcome_kickoff_identity_mismatch")
            continue
        result = grade_side(outcome["actual"], float(row["line"]), row["position"])
        settled.append({**row, "actual": outcome["actual"], "result": result, "y": int(result == "W"), "finalized_at": outcome["finalized_at"], "outcome_source_id": outcome["source_id"], "outcome_source_sha256": outcome["source_sha256"]})
    return {"records": settled, "problems": problems, "outcomes_sha256": _hash(data)}


def _sigmoid(value: float) -> float:
    return 1 / (1 + math.exp(-max(-35.0, min(35.0, value))))


def _weights(rows: list[dict[str, Any]]) -> list[float]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[row["event_id"]] += 1
    return [1 / counts[row["event_id"]] for row in rows]


def fit_sigmoid(rows: list[dict[str, Any]], *, regularization: float = 0.1) -> dict[str, float]:
    """Penalized logistic Newton fit, constrained to a nonnegative score slope."""
    if not rows or regularization <= 0:
        raise ValueError("training rows and positive regularization are required")
    weights = _weights(rows)
    prevalence = (sum(w * r["y"] for w, r in zip(weights, rows)) + 0.5) / (sum(weights) + 1)
    intercept, slope = math.log(prevalence / (1 - prevalence)), 0.0

    def objective(a: float, b: float) -> float:
        loss = regularization * b * b / 2 + 0.001 * a * a / 2
        for row, weight in zip(rows, weights):
            z = a + b * (row["ranking_score"] - 0.5)
            loss += weight * (max(z, 0) + math.log1p(math.exp(-abs(z))) - row["y"] * z)
        return loss

    for _ in range(100):
        ga, gb, haa, hab, hbb = 0.001 * intercept, regularization * slope, 0.001, 0.0, regularization
        for row, weight in zip(rows, weights):
            x = row["ranking_score"] - 0.5
            p = _sigmoid(intercept + slope * x)
            residual, variance = weight * (p - row["y"]), weight * p * (1 - p)
            ga += residual
            gb += residual * x
            haa += variance
            hab += variance * x
            hbb += variance * x * x
        determinant = haa * hbb - hab * hab
        da, db = (hbb * ga - hab * gb) / determinant, (haa * gb - hab * ga) / determinant
        old = objective(intercept, slope)
        step = 1.0
        a, b = intercept, slope
        for _ in range(30):
            a, b = intercept - step * da, max(0.0, slope - step * db)
            if objective(a, b) <= old:
                break
            step /= 2
        if abs(a - intercept) + abs(b - slope) < 1e-9:
            break
        intercept, slope = a, b
    return {"intercept": intercept, "slope": slope, "score_center": 0.5, "regularization": regularization}


def _probability(coefficients: dict[str, Any], score: float) -> float:
    return _sigmoid(coefficients["intercept"] + coefficients["slope"] * (score - coefficients["score_center"]))


def _metrics(rows: list[dict[str, Any]], probabilities: list[float]) -> dict[str, Any]:
    if not rows:
        return {"rows": 0, "independent_events": 0, "brier": None, "logloss": None}
    weights = _weights(rows)
    total = sum(weights)
    brier = sum(w * (p - r["y"]) ** 2 for r, p, w in zip(rows, probabilities, weights)) / total
    logloss = -sum(w * (r["y"] * math.log(max(1e-12, p)) + (1 - r["y"]) * math.log(max(1e-12, 1 - p))) for r, p, w in zip(rows, probabilities, weights)) / total
    return {"rows": len(rows), "independent_events": len({r["event_id"] for r in rows}), "brier": brier, "logloss": logloss}


def _reliability(rows: list[dict[str, Any]], probabilities: list[float]) -> list[dict[str, Any]]:
    bins: list[dict[str, Any]] = []
    for index in range(5):
        selected = [(r, p) for r, p in zip(rows, probabilities) if min(4, int(p * 5)) == index]
        events: dict[str, list[tuple[float, int]]] = defaultdict(list)
        for row, p in selected:
            events[row["event_id"]].append((p, row["y"]))
        n = len(events)
        if not n:
            bins.append({"lower": index / 5, "upper": (index + 1) / 5, "rows": 0, "independent_events": 0})
            continue
        mean_p = sum(sum(p for p, _ in values) / len(values) for values in events.values()) / n
        rate = sum(sum(y for _, y in values) / len(values) for values in events.values()) / n
        radius = math.sqrt(math.log(40) / (2 * n))
        bins.append({"lower": index / 5, "upper": (index + 1) / 5, "rows": len(selected), "independent_events": n, "mean_probability": mean_p, "event_weighted_win_rate": rate, "win_rate_interval_95_simultaneous": [max(0, rate - radius), min(1, rate + radius)], "interval_method": "Hoeffding_union_bound_5_bins_independent_events"})
    return bins


def _baseline(row: dict[str, Any]) -> float | None:
    p = _number(row.get("baseline_probability"))
    identity = row.get("baseline_identity")
    if p is None or not 0 < p < 1 or not isinstance(identity, dict):
        return None
    captured = try_parse_utc(row.get("baseline_captured_at"))
    snapshot = try_parse_utc(row.get("captured_at"))
    if (
        _identity(identity) != _identity(row)
        or not _sha(row.get("baseline_source_sha256"))
        or not isinstance(row.get("baseline_source_id"), str)
        or not row["baseline_source_id"].strip()
        or captured is None or snapshot is None or captured > snapshot
    ):
        return None
    return p


def build_calibration(
    rows: list[dict[str, Any]], *, as_of: datetime,
    min_training_events: int = 30, min_holdout_events: int = 10,
) -> dict[str, Any]:
    """Fit on earlier whole weeks; holdout outcomes never affect coefficients."""
    if as_of.tzinfo is None or min_training_events < 2 or min_holdout_events < 2:
        raise ValueError("aware fit cutoff and at least two events per partition required")
    artifact: dict[str, Any] = {
        "schema_version": 1, "method_version": METHOD_VERSION,
        "ranking_score_version": RANK_VERSION, "status": "DATA_INSUFFICIENT",
        "publication_status": "SHADOW_ONLY", "approved_for_betting": False,
        "fit_as_of": as_of.astimezone(UTC).isoformat(),
        "split_policy": "chronological_whole_Eastern_ISO_kickoff_weeks_last_25pct_holdout",
        "event_weighting": "one_total_weight_per_event_per_family",
        "minimum_training_events": min_training_events, "minimum_holdout_events": min_holdout_events,
        "families": {},
    }
    valid = []
    for row in rows:
        final = try_parse_utc(row.get("finalized_at"))
        score = _number(row.get("ranking_score"))
        kickoff = try_parse_utc(row.get("event_starts_at"))
        captured = try_parse_utc(row.get("captured_at"))
        if (
            _identity(row) is not None
            and row.get("ranking_score_version") == RANK_VERSION
            and score is not None and 0 <= score <= 1
            and row.get("y") in (0, 1)
            and final is not None and final <= as_of
            and kickoff is not None and captured is not None and captured < kickoff < final
        ):
            valid.append({**row, "ranking_score": score, "captured_at": captured.isoformat(), "finalized_at": final.isoformat(), "event_starts_at": kickoff.isoformat()})
    for market in MARKETS:
        family = [r for r in valid if r["market"] == market]
        weeks: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in family:
            kickoff = try_parse_utc(row.get("event_starts_at"))
            if kickoff is not None:
                # SNF after UTC midnight still belongs to Sunday's local slate.
                iso = kickoff.astimezone(EASTERN).isocalendar()
                weeks[f"{iso.year}-W{iso.week:02d}"].append(row)
        ordered = sorted(weeks)
        split = max(1, len(ordered) - max(1, math.ceil(len(ordered) * 0.25)))
        training = [row for week in ordered[:split] for row in weeks[week]]
        holdout = [row for week in ordered[split:] for row in weeks[week]]
        if holdout:
            # An earlier game whose outcome was not known when holdout forecasts
            # were captured cannot be used to fit the pre-holdout model.
            boundary = min(r["captured_at"] for r in holdout)
            training = [r for r in training if r["finalized_at"] < boundary]
        train_events, test_events = {r["event_id"] for r in training}, {r["event_id"] for r in holdout}
        entry: dict[str, Any] = {
            "status": "DATA_INSUFFICIENT", "coefficients": None,
            "training_rows": len(training), "training_events": len(train_events),
            "holdout_rows": len(holdout), "holdout_events": len(test_events),
            "training_event_ids": sorted(train_events), "holdout_event_ids": sorted(test_events),
            "training_weeks": ordered[:split], "holdout_weeks": ordered[split:],
        }
        artifact["families"][market] = entry
        if train_events & test_events or len(train_events) < min_training_events or len(test_events) < min_holdout_events or len({r["y"] for r in training}) != 2:
            entry["reason"] = "insufficient_independent_events_or_outcome_classes"
            continue
        coefficients = fit_sigmoid(training)
        p = [_probability(coefficients, r["ranking_score"]) for r in holdout]
        weights = _weights(training)
        prevalence = sum(r["y"] * w for r, w in zip(training, weights)) / sum(weights)
        paired = [(r, predicted, _baseline(r)) for r, predicted in zip(holdout, p) if _baseline(r) is not None]
        paired_rows = [r for r, _, _ in paired]
        entry.update({
            "status": "SHADOW_FITTED", "coefficients": coefficients,
            "score_support": [min(r["ranking_score"] for r in training), max(r["ranking_score"] for r in training)],
            "holdout_metrics": _metrics(holdout, p),
            "training_prevalence_baseline_metrics": _metrics(holdout, [prevalence] * len(holdout)),
            "ranking_score_diagnostic_metrics": _metrics(holdout, [r["ranking_score"] for r in holdout]),
            "exact_pair_baseline_metrics": _metrics(paired_rows, [float(b) for _, _, b in paired if b is not None]),
            "model_on_baseline_subset_metrics": _metrics(paired_rows, [predicted for _, predicted, _ in paired]),
            "reliability_bins": _reliability(holdout, p),
            "acceptance": "NOT_APPROVED_requires_independent_review_of_support_reliability_and_price_baselines",
        })
    if any(f["status"] == "SHADOW_FITTED" for f in artifact["families"].values()):
        artifact["status"] = "SHADOW_FITTED"
    return artifact


def predict_shadow(artifact: Any, market: str, score: Any) -> dict[str, Any]:
    """Measurement only. Withhold out-of-support and unapproved model results."""
    result: dict[str, Any] = {"method_status": "UNAVAILABLE", "shadow_probability": None, "approved_for_betting": False}
    if not isinstance(artifact, dict):
        return result
    value = _number(score)
    families = artifact.get("families")
    if not isinstance(families, dict):
        return result
    family = families.get(market)
    if not isinstance(family, dict):
        return result
    if (
        artifact.get("method_version") != METHOD_VERSION
        or artifact.get("ranking_score_version") != RANK_VERSION
        or artifact.get("publication_status") != "SHADOW_ONLY"
        or family.get("status") != "SHADOW_FITTED"
        or value is None
    ):
        return result
    support = family.get("score_support")
    coefficients = family.get("coefficients")
    if not isinstance(support, list) or len(support) != 2 or not isinstance(coefficients, dict):
        return result
    lower, upper = (_number(value) for value in support)
    if lower is None or upper is None or not 0 <= lower <= upper <= 1:
        return result
    intercept, slope, center = (_number(coefficients.get(key)) for key in ("intercept", "slope", "score_center"))
    if intercept is None or slope is None or center is None or slope < 0 or center != 0.5:
        return result
    if not lower <= value <= upper:
        return {**result, "method_status": "OUT_OF_TRAINING_SUPPORT"}
    checked = {"intercept": intercept, "slope": slope, "score_center": center}
    return {**result, "method_status": "SHADOW_ONLY", "shadow_probability": _probability(checked, value)}


def run_offline_build(runs_dir: Path, output_dir: Path, outcomes_path: Path | None, *, as_of: datetime) -> dict[str, Any]:
    """Write research outputs only to an explicit directory outside the input runs."""
    root, destination = runs_dir.resolve(), output_dir.resolve()
    if destination == root or destination.is_relative_to(root):
        raise ValueError("output directory must be outside immutable input runs")
    snapshots = collect_snapshots(root)
    settlement = settle_snapshots(snapshots["records"], outcomes_path, as_of=as_of)
    artifact = build_calibration(settlement["records"], as_of=as_of)
    artifact["outcomes_sha256"] = settlement["outcomes_sha256"]
    artifact["snapshot_policy"] = snapshots["snapshot_policy"]
    artifact["input_record_sha256"] = _hash(json.dumps(settlement["records"], sort_keys=True, allow_nan=False).encode())
    destination.mkdir(parents=True, exist_ok=True)
    safe_write_json(destination / "floor_calibration_inventory.json", snapshots)
    safe_write_json(destination / "floor_calibration_settled.json", settlement)
    safe_write_json(destination / "floor_calibration_shadow.json", artifact)
    report = (
        "# NFL alternate-floor calibration research\n\n"
        f"Status: **{artifact['status']}**. Publication: **SHADOW_ONLY**.\n\n"
        "No betting probability, EV, approval, or stake is emitted. Outcomes are explicit "
        "verified-final input attestations; this offline tool cannot verify their provider independently.\n\n"
        f"Manifests inspected: {snapshots['manifests_inspected']}. Inventory rows: {snapshots['inventory_rows']}. "
        f"Admitted unique targets: {len(snapshots['records'])}. Settled targets: {len(settlement['records'])}.\n\n"
        "| Family | Status | Training events | Holdout events |\n|---|---|---:|---:|\n"
    )
    for market, family in artifact["families"].items():
        report += f"| {market} | {family['status']} | {family['training_events']} | {family['holdout_events']} |\n"
    report += "\nSnapshots and refused rows are in the inventory JSON; settlement gaps are in the settled JSON. "
    report += "Coefficients use earlier weeks only, with equal total game weight. Held-out reliability uncertainty uses independent event units.\n"
    (destination / "floor_calibration_report.md").write_text(report, encoding="utf-8")
    return artifact
