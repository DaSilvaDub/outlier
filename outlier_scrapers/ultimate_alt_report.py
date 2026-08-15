"""Deterministic, fail-closed reporting for the Ultimate Alt shadow lane."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from outlier_scrapers.ultimate_alt import (
    MIN_PARLAY_EV_PCT,
    ULTIMATE_ALT_HEADER,
    ULTIMATE_ALT_PARLAYS_HEADER,
    _number,
)

VALID_ALT_TYPES = ("SPREAD", "TOTAL", "PLAYER_PROP")
VALID_SHADOW_STATUSES = ("QUALIFIED", "REJECTED")
BASE_IDENTITY_FIELDS = (
    "event_id",
    "market_id",
    "outcome_id",
    "stable_wager_id",
    "book",
    "matchup",
    "selection",
    "line",
)
PLAYER_IDENTITY_FIELDS = ("player", "player_id")
RELEASE_GATE_EVIDENCE = (
    "at least 200 settled legs across at least 30 shadow days",
    "at least 40 settled legs in each of spreads, totals, and player props",
    "positive flat-stake ROI",
    "absolute calibration gap no greater than 5%",
    "closing-price data on at least 80% of settled legs",
    "non-negative average closing-price CLV",
)


class ReportInputError(ValueError):
    """Raised when a supplied report artifact cannot satisfy its schema contract."""


@dataclass(frozen=True)
class LegAudit:
    row: Mapping[str, str]
    reasons: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.reasons


@dataclass(frozen=True)
class ParlayAudit:
    row: Mapping[str, str]
    reasons: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.reasons


@dataclass(frozen=True)
class ReportAnalysis:
    counts: Mapping[str, Mapping[str, int]]
    qualified: tuple[LegAudit, ...]
    surviving: tuple[Mapping[str, str], ...]
    parlays: tuple[ParlayAudit, ...]
    best_parlay: Mapping[str, str] | None
    input_issues: tuple[str, ...]
    verdict: str


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate an Ultimate Alt shadow audit report.")
    parser.add_argument("--alt-csv", default="ultimate_alt.csv", help="Path to ultimate_alt.csv")
    parser.add_argument(
        "--parlay-csv",
        default="ultimate_alt_parlays.csv",
        help="Path to ultimate_alt_parlays.csv",
    )
    parser.add_argument("--output", required=True, help="Markdown report output path")
    parser.add_argument(
        "--pack-date",
        required=True,
        help="Pack date in YYYY-MM-DD format",
    )
    parser.add_argument(
        "--source-prompt",
        default="",
        help="Optional source prompt path recorded as provenance",
    )
    parser.add_argument("--agent", default="DETERMINISTIC", help="Frontmatter agent label")
    parser.add_argument("--workflow", default="generic", help="Frontmatter workflow label")
    return parser.parse_args(argv)


def _read_csv(path: Path, required_fields: Sequence[str]) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or ())
            missing = sorted(set(required_fields) - fields)
            if missing:
                raise ReportInputError(f"{path} is missing required columns: {', '.join(missing)}")
            return [dict(row) for row in reader]
    except FileNotFoundError as exc:
        raise ReportInputError(f"required input does not exist: {path}") from exc
    except csv.Error as exc:
        raise ReportInputError(f"could not parse {path}: {exc}") from exc


def _finite_number(value: Any) -> float | None:
    parsed = _number(value)
    return parsed if parsed is not None and math.isfinite(parsed) else None


def _probability(value: Any) -> float | None:
    parsed = _finite_number(value)
    if parsed is None:
        return None
    if parsed > 1.0:
        parsed /= 100.0
    return parsed if 0.0 <= parsed <= 1.0 else None


def _alt_type(row: Mapping[str, str]) -> str | None:
    value = str(row.get("alt_type") or "").strip().upper()
    return value if value in VALID_ALT_TYPES else None


def _status(row: Mapping[str, str]) -> str | None:
    value = str(row.get("shadow_status") or "").strip().upper()
    return value if value in VALID_SHADOW_STATUSES else None


def _audit_leg(row: Mapping[str, str]) -> LegAudit:
    reasons: list[str] = []
    alt_type = _alt_type(row)
    if alt_type is None:
        reasons.append("UNKNOWN_ALT_TYPE")

    rejection_reasons = str(row.get("rejection_reasons") or "").strip()
    if rejection_reasons:
        reasons.append(f"UPSTREAM_REJECTION:{rejection_reasons}")

    raw_units = str(row.get("portfolio_shadow_units") or "").strip()
    units = _finite_number(raw_units)
    if not raw_units:
        reasons.append("BLANK_PORTFOLIO_SHADOW_UNITS")
    elif units is None:
        reasons.append("INVALID_PORTFOLIO_SHADOW_UNITS")
    elif units <= 0.0:
        reasons.append("NONPOSITIVE_PORTFOLIO_SHADOW_UNITS")

    identity_fields: list[str] = list(BASE_IDENTITY_FIELDS)
    if alt_type == "PLAYER_PROP":
        identity_fields.extend(PLAYER_IDENTITY_FIELDS)
    missing_identity = [field for field in identity_fields if not str(row.get(field) or "").strip()]
    if missing_identity:
        reasons.append(f"MISSING_IDENTITY:{','.join(missing_identity)}")

    if _probability(row.get("conservative_prob")) is None:
        reasons.append("INVALID_CONSERVATIVE_PROBABILITY")
    if _probability(row.get("implied_prob")) is None:
        reasons.append("INVALID_IMPLIED_PROBABILITY")

    edge = _finite_number(row.get("edge_pct"))
    if edge is None:
        reasons.append("INVALID_EDGE")
    elif edge <= 0.0:
        reasons.append("NONPOSITIVE_EDGE")
    ev_pct = _finite_number(row.get("ev_pct"))
    if ev_pct is None:
        reasons.append("INVALID_EV")
    elif ev_pct <= 0.0:
        reasons.append("NONPOSITIVE_EV")

    return LegAudit(row=row, reasons=tuple(dict.fromkeys(reasons)))


def _split_tokens(value: Any, separator: str) -> list[str]:
    return [token.strip() for token in str(value or "").split(separator) if token.strip()]


def _integer(value: Any) -> int | None:
    parsed = _finite_number(value)
    if parsed is None or not parsed.is_integer():
        return None
    return int(parsed)


def _audit_parlay(
    row: Mapping[str, str],
    survivor_lookup: Mapping[tuple[str, str], Mapping[str, str]],
    survivor_counts: Counter[tuple[str, str]],
) -> ParlayAudit:
    reasons: list[str] = []
    flags = {
        token.strip().upper()
        for token in re.split(r"[;,]", str(row.get("quality_flags") or ""))
        if token.strip()
    }
    for required_flag in ("CROSS_EVENT", "SHADOW_ONLY"):
        if required_flag not in flags:
            reasons.append(f"MISSING_FLAG:{required_flag}")

    num_legs = _integer(row.get("num_legs"))
    if num_legs not in {2, 3}:
        reasons.append("INVALID_NUM_LEGS")

    event_ids = _split_tokens(row.get("event_ids"), ",")
    leg_names = _split_tokens(row.get("legs"), "|")
    if num_legs is not None and (len(event_ids) != num_legs or len(leg_names) != num_legs):
        reasons.append("LEG_COUNT_MISMATCH")
    if not event_ids or len(set(event_ids)) != len(event_ids):
        reasons.append("EVENTS_NOT_DISTINCT")

    alt_types = [token.upper() for token in _split_tokens(row.get("alt_types"), ",")]
    if len(set(alt_types)) < 2:
        reasons.append("INSUFFICIENT_ALT_TYPE_DIVERSITY")
    if any(token not in VALID_ALT_TYPES for token in alt_types):
        reasons.append("UNKNOWN_ALT_TYPE")

    decimal = _finite_number(row.get("combined_decimal"))
    if decimal is None or not 1.4 <= decimal <= 4.0:
        reasons.append("COMBINED_DECIMAL_OUT_OF_RANGE")
    if _probability(row.get("combined_conservative_prob")) is None:
        reasons.append("INVALID_COMBINED_CONSERVATIVE_PROBABILITY")
    ev_pct = _finite_number(row.get("ev_pct"))
    if ev_pct is None or ev_pct < MIN_PARLAY_EV_PCT:
        reasons.append("PARLAY_EV_BELOW_GATE")

    matched: list[Mapping[str, str]] = []
    if len(leg_names) == len(event_ids):
        for key in zip(leg_names, event_ids, strict=True):
            if survivor_counts[key] != 1:
                reasons.append(f"LEG_NOT_UNIQUE_SURVIVOR:{key[0]}@{key[1]}")
            elif key in survivor_lookup:
                matched.append(survivor_lookup[key])
    teams = [
        str(leg.get("team") or "").strip().casefold()
        for leg in matched
        if str(leg.get("team") or "").strip()
    ]
    players = [
        str(leg.get("player_id") or leg.get("player") or "").strip().casefold()
        for leg in matched
        if str(leg.get("player_id") or leg.get("player") or "").strip()
    ]
    if len(teams) != len(set(teams)):
        reasons.append("REPEATED_TEAM")
    if len(players) != len(set(players)):
        reasons.append("REPEATED_PLAYER")

    return ParlayAudit(row=row, reasons=tuple(dict.fromkeys(reasons)))


def analyze_report(
    alt_rows: Sequence[Mapping[str, str]],
    parlay_rows: Sequence[Mapping[str, str]],
) -> ReportAnalysis:
    counts = {alt_type: {"qualified": 0, "rejected": 0} for alt_type in VALID_ALT_TYPES}
    input_issues: list[str] = []
    qualified: list[LegAudit] = []
    for row_number, row in enumerate(alt_rows, 2):
        alt_type = _alt_type(row)
        status = _status(row)
        if alt_type is None:
            input_issues.append(f"alt row {row_number}: unknown alt_type")
            continue
        if status is None:
            input_issues.append(f"alt row {row_number}: invalid shadow_status")
            continue
        counts[alt_type][status.casefold()] += 1
        if status == "QUALIFIED":
            qualified.append(_audit_leg(row))

    surviving = [audit.row for audit in qualified if audit.ok]
    surviving.sort(
        key=lambda row: (
            _finite_number(row.get("edge_pct")) or float("-inf"),
            _finite_number(row.get("ev_pct")) or float("-inf"),
            str(row.get("stable_wager_id") or ""),
        ),
        reverse=True,
    )
    survivor_counts = Counter(
        (
            str(row.get("selection") or "").strip(),
            str(row.get("event_id") or "").strip(),
        )
        for row in surviving
    )
    survivor_lookup = {
        (
            str(row.get("selection") or "").strip(),
            str(row.get("event_id") or "").strip(),
        ): row
        for row in surviving
    }
    parlay_audits = [_audit_parlay(row, survivor_lookup, survivor_counts) for row in parlay_rows]
    valid_parlays = [audit.row for audit in parlay_audits if audit.ok]

    def parlay_rank(row: Mapping[str, str]) -> tuple[float, float, int]:
        rank = _integer(row.get("rank"))
        return (
            _finite_number(row.get("ev_pct")) or float("-inf"),
            _probability(row.get("combined_conservative_prob")) or float("-inf"),
            -(rank if rank is not None else 999_999),
        )

    best_parlay = max(valid_parlays, key=parlay_rank) if valid_parlays else None
    if not alt_rows or input_issues:
        verdict = "INSUFFICIENT DATA"
    elif not surviving or (parlay_rows and best_parlay is None):
        verdict = "REVISE"
    else:
        verdict = "CONTINUE"
    return ReportAnalysis(
        counts=counts,
        qualified=tuple(qualified),
        surviving=tuple(surviving),
        parlays=tuple(parlay_audits),
        best_parlay=best_parlay,
        input_issues=tuple(input_issues),
        verdict=verdict,
    )


def _markdown(value: Any) -> str:
    return str(value or "Unknown").replace("\r", " ").replace("\n", " ").replace("|", r"\|")


def _yaml_string(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def render_report(
    analysis: ReportAnalysis,
    *,
    pack_date: str,
    source_prompt: str = "",
    agent: str = "DETERMINISTIC",
    workflow: str = "generic",
    generated_at: datetime | None = None,
) -> str:
    generated = generated_at or datetime.now(timezone.utc)
    lines = [
        "---",
        f"workflow: {_yaml_string(workflow)}",
        f"agent: {_yaml_string(agent)}",
        f"pack_date: {_yaml_string(pack_date)}",
        f"generated_at: {_yaml_string(generated.astimezone(timezone.utc).isoformat())}",
    ]
    if source_prompt:
        lines.append(f"source_prompt: {_yaml_string(source_prompt)}")
    lines += ["---", "", "# Ultimate Alt Shadow Report", "", "## 1. Summary of Counts", ""]
    for alt_type in VALID_ALT_TYPES:
        bucket = analysis.counts[alt_type]
        lines.append(
            f"- **{alt_type}**: {bucket['qualified']} Qualified, {bucket['rejected']} Rejected"
        )

    lines += ["", "## 2. Audit of Qualified Legs", ""]
    if not analysis.qualified:
        lines.append("No qualified legs found in the supplied Ultimate Alt table.")
    for leg_audit in analysis.qualified:
        row = leg_audit.row
        lines.append(
            f"- **{_markdown(row.get('selection'))}** ({_markdown(row.get('matchup'))}) | "
            f"Cons Prob: {_markdown(row.get('conservative_prob'))} | "
            f"Imp Prob: {_markdown(row.get('implied_prob'))} | "
            f"EV: {_markdown(row.get('ev_pct'))}% | "
            f"Units: {_markdown(row.get('portfolio_shadow_units'))}"
        )
        status = "Passed supplied-data audit." if leg_audit.ok else "; ".join(leg_audit.reasons)
        lines.append(f"  - {'PASS' if leg_audit.ok else 'FAIL'}: {status}")

    lines += ["", "## 3. Ranked Surviving Legs", ""]
    if not analysis.surviving:
        lines.append("No surviving legs after the supplied-data audit.")
    for index, row in enumerate(analysis.surviving, 1):
        lines.append(
            f"{index}. **{_markdown(row.get('selection'))}** "
            f"(Edge: {_markdown(row.get('edge_pct'))}%; EV: {_markdown(row.get('ev_pct'))}%)"
        )

    lines += ["", "## 4. Strongest Valid Cross-Event Parlay", ""]
    if analysis.best_parlay is None:
        lines.append("No valid cross-event parlay found in the supplied parlay table.")
    else:
        row = analysis.best_parlay
        lines.append(
            f"**{_markdown(row.get('legs'))}** "
            f"(EV: {_markdown(row.get('ev_pct'))}%; decimal: "
            f"{_markdown(row.get('combined_decimal'))})"
        )
    invalid_parlays = [parlay_audit for parlay_audit in analysis.parlays if not parlay_audit.ok]
    if invalid_parlays:
        lines += ["", "Rejected supplied parlays:"]
        for parlay_audit in invalid_parlays:
            lines.append(
                f"- {_markdown(parlay_audit.row.get('legs'))}: {'; '.join(parlay_audit.reasons)}"
            )

    lines += ["", "## 5. Input Integrity", ""]
    if analysis.input_issues:
        lines.extend(f"- {issue}" for issue in analysis.input_issues)
    else:
        lines.append("No global input-contract violations detected.")

    lines += ["", "## 6. Verdict", "", f"SHADOW VERDICT: {analysis.verdict}"]
    evidence: Sequence[str]
    if analysis.verdict == "CONTINUE":
        lines.append(
            "Shadow evaluation may continue. This does not authorize activation or promotion."
        )
        evidence = RELEASE_GATE_EVIDENCE
    elif analysis.verdict == "REVISE":
        lines.append(
            "The supplied shadow artifacts require correction before evaluation continues."
        )
        evidence = (
            "at least one fully identified leg with positive edge, EV, and shadow units",
            "a valid supplied cross-event parlay when the parlay table is non-empty",
            *RELEASE_GATE_EVIDENCE,
        )
    else:
        lines.append("The supplied data cannot support a shadow-lane conclusion.")
        evidence = (
            "a non-empty Ultimate Alt table with only recognized alt types and statuses",
            "complete canonical CSV schemas and wager identities",
            *RELEASE_GATE_EVIDENCE,
        )
    lines.append("Evidence required before manual promotion:")
    lines.extend(f"- {item}" for item in evidence)
    return "\n".join(lines) + "\n"


def _validate_pack_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ReportInputError("--pack-date must use YYYY-MM-DD") from exc


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def run_report(
    *,
    alt_csv: Path,
    parlay_csv: Path,
    output: Path,
    pack_date: str,
    source_prompt: str = "",
    agent: str = "DETERMINISTIC",
    workflow: str = "generic",
) -> ReportAnalysis:
    normalized_date = _validate_pack_date(pack_date)
    alt_rows = _read_csv(alt_csv, ULTIMATE_ALT_HEADER)
    parlay_rows = _read_csv(parlay_csv, ULTIMATE_ALT_PARLAYS_HEADER)
    analysis = analyze_report(alt_rows, parlay_rows)
    report = render_report(
        analysis,
        pack_date=normalized_date,
        source_prompt=source_prompt,
        agent=agent,
        workflow=workflow,
    )
    _atomic_write(output, report)
    return analysis


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        analysis = run_report(
            alt_csv=Path(args.alt_csv),
            parlay_csv=Path(args.parlay_csv),
            output=Path(args.output),
            pack_date=args.pack_date,
            source_prompt=args.source_prompt,
            agent=args.agent,
            workflow=args.workflow,
        )
    except (OSError, ReportInputError) as exc:
        print(f"shadow report failed: {exc}", file=sys.stderr)
        return 2
    print(f"Shadow report written to {args.output} ({analysis.verdict})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
