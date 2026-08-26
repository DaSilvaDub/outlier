"""By-pack-type accuracy audit for a generated pack.

The daily feedback report grades every settled decision as one undifferentiated
population.  That answers "is the desk profitable" but not "which pack lane is
carrying it", so a pitcher-strikeout lane running cold can hide behind healthy
game totals for weeks.

This module grades a single pack (or a date range of packs) *by pack type* and,
just as importantly, reports the lanes a pack generated that never reached the
feedback ledger at all.  An unmeasured lane is reported as ``NO_COVERAGE``
rather than being silently omitted, because a lane with no rows in the ledger
and a lane that went 0-for-0 look identical in every existing report.

Grading itself is not re-implemented here.  Settlement remains
``outlier_scrapers.results``; this module only reads what that collector wrote.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import feedback

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKS_DIR = REPO_ROOT / "packs"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "calibration" / "reports" / "pack_accuracy"

# Every wagerable lane a pack can emit, mapped to the capture ``source`` the
# feedback ledger would record for it.  Lanes whose capture source is ``None``
# have no ledger representation today - they are the coverage gaps this audit
# exists to make visible.
LANE_FILES: dict[str, str | None] = {
    "opportunities.csv": "opportunities",
    "candidates.csv": "candidates",
    "game_totals.csv": "game_totals",
    "team_totals.csv": "team_totals",
    "ultimate_alt.csv": "ultimate_alt",
    "alt_player_props.csv": None,
    "alt_team_totals.csv": None,
    "mlb_alt_spreads.csv": None,
    "wnba_alt_spreads.csv": None,
    "mlb_alt_bankroll_props.csv": None,
    "wnba_alt_bankroll_props.csv": None,
    "alt_player_props_parlays.csv": None,
    "alt_team_total_parlays.csv": None,
    "mlb_alt_bankroll_parlays.csv": None,
    "ultimate_alt_parlays.csv": None,
}

# ``opportunities.csv`` supersedes ``candidates.csv`` when both exist, matching
# ``feedback._load_pack_rows``.  Counting both would double-count the same bets.
SUPERSEDED_LANES = {"opportunities.csv": "candidates.csv"}

STATUS_NO_COVERAGE = "NO_COVERAGE"
STATUS_NO_SETTLEMENTS = "NO_SETTLEMENTS"
STATUS_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
STATUS_ON_TRACK = "ON_TRACK"
STATUS_NEEDS_ADJUSTMENT = "NEEDS_ADJUSTMENT"
STATUS_AHEAD_OF_MODEL = "AHEAD_OF_MODEL"

DEFAULT_MIN_SAMPLES = 20
DEFAULT_TOLERANCE = 0.05

PACK_TYPE_FIELDS = [
    "pack_type",
    "status",
    "status_reason",
    *feedback.GROUP_METRIC_FIELDS,
    "expected_hit_rate",
    "calibration_gap",
    "graded",
    "unsettled",
]

LANE_COVERAGE_FIELDS = [
    "lane",
    "file",
    "pack_rows",
    "capture_source",
    "captured_rows",
    "settled_rows",
    "coverage_state",
    "note",
]


class PackAccuracyError(RuntimeError):
    """Raised when the requested pack or ledger cannot be audited."""


@dataclass
class LaneCoverage:
    lane: str
    file: str
    pack_rows: int
    capture_source: str
    captured_rows: int
    settled_rows: int
    coverage_state: str
    note: str


@dataclass
class PackAudit:
    pack_path: str
    pack_label: str
    pack_rows: int
    captured_rows: int
    settled_rows: int
    pack_types: list[dict[str, Any]] = field(default_factory=list)
    lanes: list[LaneCoverage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _row_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def inventory_pack_lanes(pack_dir: Path) -> dict[str, int]:
    """Count wagerable rows per lane CSV present in ``pack_dir``."""

    pack_dir = Path(pack_dir)
    counts: dict[str, int] = {}
    for filename in LANE_FILES:
        path = pack_dir / filename
        if path.exists():
            counts[filename] = _row_count(path)
    for primary, superseded in SUPERSEDED_LANES.items():
        if primary in counts and superseded in counts:
            counts.pop(superseded)
    return counts


def _pack_filter(pack_dir: Path) -> tuple[str, list[str]]:
    """Build a SQL predicate matching ledger rows for ``pack_dir``.

    Packs are captured with an absolute ``pack_path``, so a ledger copied
    between machines (or a repo moved on disk) would match nothing on an exact
    compare.  The dated folder name is stable across both, so it is accepted as
    a fallback rather than reporting a populated pack as uncaptured.
    """

    resolved = str(Path(pack_dir).resolve())
    label = Path(pack_dir).name
    return (
        "(pack_path = ? OR pack_path LIKE ? OR pack_path LIKE ?)",
        [resolved, f"%/{label}", f"%\\{label}"],
    )


def _captured_memberships(
    conn: sqlite3.Connection, pack_dir: Path
) -> list[dict[str, Any]]:
    predicate, params = _pack_filter(pack_dir)
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT snapshot_id, source, pack_path, pack_timestamp, selected, actionable
            FROM pack_snapshot_memberships
            WHERE {predicate}
            """,
            params,
        )
    ]


def _expected_hit_rate(rows: Sequence[dict[str, Any]]) -> float | None:
    """Mean model P(win | not push) over rows that actually resolved W/L."""

    values: list[float] = []
    for row in rows:
        if feedback._text(row.get("win_loss_push")).upper() not in {"W", "L"}:
            continue
        for column in (
            "final_blended_prob",
            "market_consensus_prob",
            "independent_model_prob",
        ):
            probability = feedback._scoring_probability(row, column)
            if probability is not None:
                values.append(probability)
                break
    return sum(values) / len(values) if values else None


def assess(
    metrics: dict[str, Any],
    *,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    tolerance: float = DEFAULT_TOLERANCE,
) -> tuple[str, str]:
    """Classify a pack type's graded metrics into an actionable status."""

    graded = int(metrics.get("graded") or 0)
    if graded == 0:
        return STATUS_NO_SETTLEMENTS, "no settled rows for this lane"
    if graded < min_samples:
        return (
            STATUS_INSUFFICIENT_DATA,
            f"{graded} settled rows is below the {min_samples}-row read threshold",
        )
    actual = metrics.get("hit_rate")
    expected = metrics.get("expected_hit_rate")
    if actual is None:
        return STATUS_INSUFFICIENT_DATA, "every graded row pushed; no win rate to read"
    if expected is None:
        roi = metrics.get("roi")
        if roi is None:
            return STATUS_INSUFFICIENT_DATA, "no model probability and no staked units"
        if roi < 0:
            return STATUS_NEEDS_ADJUSTMENT, f"no model probability to compare; ROI {roi:.1%}"
        return STATUS_ON_TRACK, f"no model probability to compare; ROI {roi:.1%}"
    gap = actual - expected
    detail = f"actual {actual:.1%} vs expected {expected:.1%} ({gap:+.1%})"
    if gap < -tolerance:
        return STATUS_NEEDS_ADJUSTMENT, f"model is overconfident: {detail}"
    if gap > tolerance:
        return STATUS_AHEAD_OF_MODEL, f"model is underconfident: {detail}"
    return STATUS_ON_TRACK, f"calibrated within {tolerance:.0%}: {detail}"


def summarize_pack_types(
    graded_rows: Sequence[dict[str, Any]],
    unsettled_by_type: dict[str, int],
    *,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    tolerance: float = DEFAULT_TOLERANCE,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in graded_rows:
        grouped[row.get("pack_type") or feedback.PACK_TYPE_UNCLASSIFIED].append(row)

    summaries: list[dict[str, Any]] = []
    for pack_type in sorted(set(grouped) | set(unsettled_by_type)):
        rows = grouped.get(pack_type, [])
        metrics = feedback._group_metrics(rows, "pack_type", pack_type)
        metrics["graded"] = len(rows)
        metrics["unsettled"] = unsettled_by_type.get(pack_type, 0)
        metrics["expected_hit_rate"] = _expected_hit_rate(rows)
        actual = metrics.get("hit_rate")
        expected = metrics.get("expected_hit_rate")
        metrics["calibration_gap"] = (
            actual - expected if actual is not None and expected is not None else None
        )
        status, reason = assess(metrics, min_samples=min_samples, tolerance=tolerance)
        metrics["status"] = status
        metrics["status_reason"] = reason
        summaries.append(metrics)
    return summaries


def audit_pack(
    pack_dir: Path,
    db_path: Path = feedback.DEFAULT_DB_PATH,
    *,
    connection: sqlite3.Connection | None = None,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    tolerance: float = DEFAULT_TOLERANCE,
) -> PackAudit:
    """Grade one pack by pack type and report its unmeasured lanes."""

    pack_dir = Path(pack_dir)
    if not pack_dir.exists():
        raise PackAccuracyError(f"Pack directory does not exist: {pack_dir}")

    lane_counts = inventory_pack_lanes(pack_dir)
    owns_connection = connection is None
    conn = connection if connection is not None else feedback.open_database(Path(db_path))
    try:
        memberships = _captured_memberships(conn, pack_dir)
        source_by_snapshot = {
            str(row["snapshot_id"]): feedback._text(row.get("source"))
            for row in memberships
        }
        joined = feedback._joined_rows(conn)
    finally:
        if owns_connection:
            conn.close()

    graded_rows: list[dict[str, Any]] = []
    for row in joined:
        snapshot_id = str(row.get("snapshot_id") or "")
        if snapshot_id not in source_by_snapshot:
            continue
        # The lane recorded for *this* pack wins over the global vintage lane:
        # a snapshot re-captured by a later pack must still be attributed to the
        # pack being audited.
        row["pack_source"] = source_by_snapshot[snapshot_id] or row.get("pack_source", "")
        row["pack_type"] = feedback.classify_pack_type(row)
        graded_rows.append(row)

    settled_snapshots = {str(row.get("snapshot_id")) for row in graded_rows}
    settled_by_source: dict[str, int] = defaultdict(int)
    for row in graded_rows:
        settled_by_source[feedback._text(row.get("pack_source"))] += 1

    captured_by_source: dict[str, int] = defaultdict(int)
    for row in memberships:
        captured_by_source[feedback._text(row.get("source"))] += 1

    unsettled_by_type: dict[str, int] = defaultdict(int)
    for row in memberships:
        if str(row["snapshot_id"]) in settled_snapshots:
            continue
        source = feedback._text(row.get("source"))
        mapped = feedback.PACK_SOURCE_TYPES.get(source.lower())
        unsettled_by_type[mapped or feedback.PACK_TYPE_UNCLASSIFIED] += 1

    pack_types = summarize_pack_types(
        graded_rows, dict(unsettled_by_type), min_samples=min_samples, tolerance=tolerance
    )

    lanes: list[LaneCoverage] = []
    warnings: list[str] = []
    for filename, pack_rows in sorted(lane_counts.items()):
        capture_source = LANE_FILES.get(filename)
        if capture_source is None:
            state = STATUS_NO_COVERAGE if pack_rows else "EMPTY"
            note = (
                "lane is generated but never captured into the feedback ledger; "
                "its accuracy is unmeasurable"
                if pack_rows
                else "lane produced no rows"
            )
            if pack_rows:
                warnings.append(
                    f"{filename}: {pack_rows} generated rows are never captured, "
                    "so this lane has no accuracy history"
                )
            lanes.append(
                LaneCoverage(
                    lane=Path(filename).stem,
                    file=filename,
                    pack_rows=pack_rows,
                    capture_source="",
                    captured_rows=0,
                    settled_rows=0,
                    coverage_state=state,
                    note=note,
                )
            )
            continue
        captured = captured_by_source.get(capture_source, 0)
        settled = settled_by_source.get(capture_source, 0)
        if pack_rows and not captured:
            state = STATUS_NO_COVERAGE
            note = "lane has rows but nothing was captured; run capture-pack"
            warnings.append(f"{filename}: {pack_rows} rows present but none captured")
        elif captured and not settled:
            state = STATUS_NO_SETTLEMENTS
            note = "captured but not settled yet; run the results collector"
        elif not pack_rows:
            state = "EMPTY"
            note = "lane produced no rows"
        else:
            state = "MEASURED"
            note = f"{settled}/{captured} captured rows settled"
        lanes.append(
            LaneCoverage(
                lane=Path(filename).stem,
                file=filename,
                pack_rows=pack_rows,
                capture_source=capture_source,
                captured_rows=captured,
                settled_rows=settled,
                coverage_state=state,
                note=note,
            )
        )

    if not memberships:
        warnings.append(
            "no ledger rows reference this pack; it was never captured "
            "(python -m outlier_scrapers.feedback capture-pack)"
        )

    return PackAudit(
        pack_path=str(pack_dir.resolve()),
        pack_label=pack_dir.name,
        pack_rows=sum(lane_counts.values()),
        captured_rows=len(memberships),
        settled_rows=len(graded_rows),
        pack_types=pack_types,
        lanes=lanes,
        warnings=warnings,
    )


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _pct(value: Any) -> str:
    return "-" if value is None else f"{float(value):.1%}"


def render_markdown(audit: PackAudit) -> str:
    lines = [
        f"# Pack accuracy - {audit.pack_label}",
        "",
        f"- Pack path: `{audit.pack_path}`",
        f"- Generated rows: {audit.pack_rows}",
        f"- Captured into ledger: {audit.captured_rows}",
        f"- Settled and graded: {audit.settled_rows}",
        "",
        "## By pack type",
        "",
        "| pack type | status | graded | unsettled | W-L-P | hit rate | expected | gap | units | pnl | roi |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in audit.pack_types:
        lines.append(
            "| {pack_type} | {status} | {graded} | {unsettled} | {wins}-{losses}-{pushes} "
            "| {hit} | {exp} | {gap} | {units} | {pnl} | {roi} |".format(
                pack_type=row.get("pack_type"),
                status=row.get("status"),
                graded=row.get("graded"),
                unsettled=row.get("unsettled"),
                wins=row.get("wins"),
                losses=row.get("losses"),
                pushes=row.get("pushes"),
                hit=_pct(row.get("hit_rate")),
                exp=_pct(row.get("expected_hit_rate")),
                gap=_pct(row.get("calibration_gap")),
                units=_fmt(row.get("units")),
                pnl=_fmt(row.get("profit")),
                roi=_pct(row.get("roi")),
            )
        )
    lines += [
        "",
        "### Status detail",
        "",
    ]
    for row in audit.pack_types:
        lines.append(f"- **{row.get('pack_type')}** - {row.get('status')}: {row.get('status_reason')}")
    lines += [
        "",
        "## Lane coverage",
        "",
        "| lane | pack rows | captured | settled | state | note |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for lane in audit.lanes:
        lines.append(
            f"| {lane.file} | {lane.pack_rows} | {lane.captured_rows} | "
            f"{lane.settled_rows} | {lane.coverage_state} | {lane.note} |"
        )
    if audit.warnings:
        lines += ["", "## Warnings", ""]
        lines.extend(f"- {warning}" for warning in audit.warnings)
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_audit(audit: PackAudit, output_dir: Path) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "pack_type_accuracy.csv", PACK_TYPE_FIELDS, audit.pack_types)
    _write_csv(
        output_dir / "lane_coverage.csv",
        LANE_COVERAGE_FIELDS,
        [asdict(lane) for lane in audit.lanes],
    )
    (output_dir / "summary.json").write_text(
        json.dumps(
            {
                "pack_path": audit.pack_path,
                "pack_label": audit.pack_label,
                "pack_rows": audit.pack_rows,
                "captured_rows": audit.captured_rows,
                "settled_rows": audit.settled_rows,
                "pack_types": audit.pack_types,
                "lanes": [asdict(lane) for lane in audit.lanes],
                "warnings": audit.warnings,
            },
            indent=2,
            sort_keys=True,
            default=str,
        ),
        encoding="utf-8",
    )
    report_path = output_dir / "report.md"
    report_path.write_text(render_markdown(audit), encoding="utf-8")
    return report_path


def resolve_pack_dir(pack: str | None, date: str | None, packs_dir: Path) -> Path:
    if pack and date:
        raise PackAccuracyError("Pass either --pack or --date, not both")
    if pack:
        return Path(pack)
    if date:
        return Path(packs_dir) / date
    raise PackAccuracyError("One of --pack or --date is required")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", help="Path to a pack directory (packs/<date>)")
    parser.add_argument("--date", help="Slate date, resolved under --packs-dir")
    parser.add_argument("--packs-dir", type=Path, default=DEFAULT_PACKS_DIR)
    parser.add_argument("--db", type=Path, default=feedback.DEFAULT_DB_PATH)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--min-samples", type=int, default=DEFAULT_MIN_SAMPLES)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    parser.add_argument(
        "--fail-on-gap",
        action="store_true",
        help="Exit non-zero when a lane is generated but never measured",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    pack_dir = resolve_pack_dir(args.pack, args.date, args.packs_dir)
    audit = audit_pack(
        pack_dir,
        args.db,
        min_samples=args.min_samples,
        tolerance=args.tolerance,
    )
    output_dir = args.output or (DEFAULT_OUTPUT_DIR / audit.pack_label)
    report_path = write_audit(audit, output_dir)
    print(render_markdown(audit))
    logger.info("Wrote pack accuracy report to %s", report_path)
    if args.fail_on_gap and any(
        lane.coverage_state == STATUS_NO_COVERAGE for lane in audit.lanes
    ):
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
