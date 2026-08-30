"""AI Research Desk orchestrator.

Entry point: python -m outlier_scrapers.run_desk

Orchestrates A/B/C/D/E passes. A/B/C/D are independent and run concurrently
(bounded thread pool); C is the automated Prompt C style narrow research pass
for injury and lineup context. E reconciles the validated A/D/B publications
(C optional) and runs only after they gate-check as CURRENT. Ties all findings
to exact pack market_ids and quoted lines without altering them.

`desk_snapshot.json` remains the sole authoritative final-report pointer;
status uses components + final_report + manual_betting_report.md for the
skill contract.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import dataclasses
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Sequence

from outlier_scrapers import (
    c_research,
    claude_reasoning,
    claude_synthesis,
    gemini_research,
    pack,
    paths,
    reasoning,
)
from outlier_scrapers import runner_common as rc
from outlier_scrapers.environment import load_environment
from outlier_scrapers.stage_result import (
    ArtifactState,
    FinalReportResolution,
    StageErrorCode,
    StageExecutionState,
    StageResult,
)

logger = logging.getLogger(__name__)

PHASES = ("A", "B", "C", "D", "E")
INDEPENDENT_PHASES = ("A", "B", "C", "D")
PHASE_OUTPUTS = {
    "A": "chatgpt_a.md",
    "B": "gemini_b.md",
    "C": "chatgpt_c.md",
    "D": "claude_d.md",
    "E": "claude_e.md",
}
PHASE_KEYS = {
    "A": "OPENAI_API_KEY",
    "B": "GEMINI_API_KEY",
    "C": "GEMINI_API_KEY",
    "D": "ANTHROPIC_API_KEY",
    "E": "ANTHROPIC_API_KEY",
}
PHASE_RUNNERS = {
    "A": reasoning.run_reasoning,
    "B": gemini_research.run_gemini_b,
    "C": c_research.run_c_research,
    "D": claude_reasoning.run_claude_d,
    "E": claude_synthesis.run_claude_e,
}
PHASE_MODULES: dict[str, ModuleType] = {
    "A": reasoning,
    "B": gemini_research,
    "C": c_research,
    "D": claude_reasoning,
    "E": claude_synthesis,
}
SUCCESS_STATES = {"success", "cached", "forced-refresh"}
STATUS_NAME = "reasoning_status.json"
REQUIRED_UPSTREAM_PHASES = ("A", "D", "B")
DEFAULT_MAX_WORKERS = 4


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stage_result(
    phase: str,
    *,
    execution_state: StageExecutionState,
    artifact_state: ArtifactState,
    request_sha256: str = "",
    publication_id: str | None = None,
    error_code: StageErrorCode | None = None,
    reason: str = "",
    cache_hit: bool = False,
    forced: bool = False,
    used_local_fallback: bool = False,
) -> StageResult:
    return StageResult(
        phase=phase,
        execution_state=execution_state,
        artifact_state=artifact_state,
        output_file=PHASE_OUTPUTS.get(phase, ""),
        request_sha256=request_sha256,
        publication_id=publication_id,
        error_code=error_code,
        reason=reason,
        cache_hit=cache_hit,
        forced=forced,
        used_local_fallback=used_local_fallback,
    )


def run_phase(phase: str, pack_dir: Path, *, force: bool = False) -> StageResult:
    """Run one desk phase, returning a typed :class:`StageResult`.

    Caching and success are decided from PR1's authoritative publication
    validation (``verdicts/<phase>/current.json`` + its manifest/hash
    integrity against the live pack identity), never from legacy Markdown
    file existence. A forced run always executes -- even when the resulting
    publication/request hash is unchanged -- and is reported with
    ``forced=True``/``cache_hit=False``, never collapsed into a cache hit.
    """
    if phase not in PHASE_RUNNERS:
        return _stage_result(
            phase,
            execution_state=StageExecutionState.FAILED,
            artifact_state=ArtifactState.INVALID,
            error_code=StageErrorCode.INTERNAL_ERROR,
            reason=f"unknown phase {phase!r}",
        )

    module = PHASE_MODULES[phase]
    identity = rc.load_pack_identity(pack_dir)

    try:
        expected_hash = module.expected_request_sha256(pack_dir)
    except rc.RunnerError as exc:
        return _stage_result(
            phase,
            execution_state=StageExecutionState.FAILED,
            artifact_state=ArtifactState.MISSING,
            error_code=StageErrorCode.MISSING_INPUT,
            reason=str(exc),
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Phase %s: failed computing expected request hash", phase)
        return _stage_result(
            phase,
            execution_state=StageExecutionState.FAILED,
            artifact_state=ArtifactState.INVALID,
            error_code=StageErrorCode.INTERNAL_ERROR,
            reason=str(exc),
        )

    validation = rc.validate_current_publication(
        pack_dir, phase, identity, expected_request_sha256=expected_hash
    )

    if not force and validation.current:
        return _stage_result(
            phase,
            execution_state=StageExecutionState.SUCCEEDED,
            artifact_state=ArtifactState.CURRENT,
            request_sha256=validation.request_sha256 or expected_hash,
            publication_id=validation.publication_id,
            cache_hit=True,
        )

    if not os.getenv(PHASE_KEYS[phase]):
        return _stage_result(
            phase,
            execution_state=StageExecutionState.SKIPPED,
            artifact_state=validation.state,
            request_sha256=expected_hash,
            publication_id=validation.publication_id if validation.current else None,
            error_code=StageErrorCode.NO_API_KEY,
            reason="missing API key",
        )

    try:
        exit_code = PHASE_RUNNERS[phase](
            pack_dir, force=force, refresh_if_stale=not force
        )
    except Exception:
        logger.exception("Phase %s failed", phase)
        exit_code = 1

    try:
        post_expected_hash = module.expected_request_sha256(pack_dir)
    except Exception:
        post_expected_hash = expected_hash

    post_validation = rc.validate_current_publication(
        pack_dir, phase, identity, expected_request_sha256=post_expected_hash
    )

    if post_validation.current:
        # The publication is authoritative and committed. A Markdown-render
        # failure after that commit (or an opaque nonzero legacy exit code
        # once the publication already validates) is a compatibility-artifact
        # concern, not an execution failure.
        markdown_ok = exit_code == 0 and (pack_dir / PHASE_OUTPUTS[phase]).exists()
        return _stage_result(
            phase,
            execution_state=StageExecutionState.SUCCEEDED,
            artifact_state=ArtifactState.CURRENT,
            request_sha256=post_validation.request_sha256 or post_expected_hash,
            publication_id=post_validation.publication_id,
            forced=force,
            error_code=None if markdown_ok else StageErrorCode.COMPATIBILITY_ARTIFACT_FAILED,
            reason="" if markdown_ok else "legacy markdown render failed after publication commit",
        )

    if (
        post_validation.state is ArtifactState.STALE
        and post_validation.reason.startswith("pack_identity_mismatch")
    ):
        error_code = StageErrorCode.IDENTITY_MISMATCH
    else:
        # Never parse log strings to guess a finer-grained code: an opaque
        # nonzero legacy runner return code (or a publication that still
        # fails to validate) maps deterministically to INTERNAL_ERROR.
        error_code = StageErrorCode.INTERNAL_ERROR

    return _stage_result(
        phase,
        execution_state=StageExecutionState.FAILED,
        artifact_state=post_validation.state,
        request_sha256=post_expected_hash,
        error_code=error_code,
        reason=post_validation.reason or f"phase runner exited {exit_code}",
    )


def run_independent_phases(
    phases: Sequence[str],
    pack_dir: Path,
    *,
    force: bool = False,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> dict[str, StageResult]:
    """Run the requested A/B/C/D phases concurrently, bounded by ``max_workers``.

    Phases are normalized/deduped and always returned in canonical A,B,C,D
    order (not completion order). E is not independent and is rejected here.
    One phase failing never cancels or blocks the others: every future is
    gathered, and an uncaught worker exception becomes a FAILED StageResult
    with error_code=INTERNAL_ERROR rather than propagating.
    """
    if max_workers < 1:
        raise ValueError("max_workers must be positive")

    requested = {step.upper() for step in phases}
    unknown = requested - set(INDEPENDENT_PHASES)
    if unknown:
        raise ValueError(
            "run_independent_phases only accepts A/B/C/D; got: "
            + ",".join(sorted(unknown))
        )
    normalized = [p for p in INDEPENDENT_PHASES if p in requested]
    if not normalized:
        return {}

    workers = max(1, min(max_workers, len(normalized)))
    results: dict[str, StageResult] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_phase = {
            executor.submit(run_phase, phase, pack_dir, force=force): phase
            for phase in normalized
        }
        for future in concurrent.futures.as_completed(future_to_phase):
            phase = future_to_phase[future]
            try:
                results[phase] = future.result()
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("Phase %s raised inside worker thread", phase)
                results[phase] = _stage_result(
                    phase,
                    execution_state=StageExecutionState.FAILED,
                    artifact_state=ArtifactState.INVALID,
                    error_code=StageErrorCode.INTERNAL_ERROR,
                    reason=f"{type(exc).__name__}: {exc}",
                )

    return {phase: results[phase] for phase in normalized}


def _game_totals_context(pack_dir: Path) -> str:
    totals_bytes, _ = rc.load_game_totals(pack_dir)
    team_totals_bytes, _ = rc.load_team_totals(pack_dir)
    parts: list[str] = []
    if totals_bytes:
        parts.append(
            "\n\n===== GAME_TOTALS.CSV (projection board) =====\n"
            + totals_bytes.decode("utf-8-sig")
        )
    if team_totals_bytes:
        parts.append(
            "\n\n===== TEAM_TOTALS.CSV (projection board) =====\n"
            + team_totals_bytes.decode("utf-8-sig")
        )
    return "".join(parts)


def local_synthesize_inputs(pack_dir: Path) -> str:
    """Concat for fallback claude_e slot. C optional."""
    parts = []
    for fname in ("briefing.md", "chatgpt_a.md", "gemini_b.md", "claude_d.md"):
        p = pack_dir / fname
        if p.exists():
            parts.append(
                f"\n\n===== {fname.upper().replace('.MD', '')} =====\n"
                + p.read_text(encoding="utf-8")
            )
    totals_ctx = _game_totals_context(pack_dir)
    if totals_ctx:
        parts.append(totals_ctx)
    c = pack_dir / "chatgpt_c.md"
    if c.exists():
        parts.append(
            "\n\n===== CHATGPT_C (optional) =====\n" + c.read_text(encoding="utf-8")
        )
    return (
        "---\nmodel: local-synthesis-fallback\n"
        f"timestamp: {_now_iso()}\n---\n\n"
        + "# Local concatenation of desk inputs (fallback)\n"
        + "".join(parts)
    )


def _read_text_safe(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8") if p.exists() else ""
    except Exception:
        return ""


def _load_candidates(pack_dir: Path) -> list[dict]:
    f = pack_dir / "candidates.csv"
    if not f.exists():
        return []
    with f.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def produce_manual_betting_report(pack_dir: Path) -> Path:
    """Render from validated envelopes when A/D/B are published; else today's pack quote."""
    from outlier_scrapers import verdict_report

    rendered = verdict_report.try_render_from_envelopes(pack_dir)
    if rendered is not None:
        text, _source = rendered
        rc.atomic_write(pack_dir, "manual_betting_report.md", "", text)
        return pack_dir / "manual_betting_report.md"

    rows = _load_candidates(pack_dir)
    # Only quote provably-pregame candidates (same house rule as the model
    # prompts) so this fallback report can't surface a locked/started event.
    kept, locked = pack.drop_locked_events(rows, now=datetime.now().astimezone())
    if locked:
        logger.warning(
            "manual report: dropped %d candidate(s) locked or without a start time",
            len(locked),
        )
    rows = kept
    briefing = _read_text_safe(pack_dir / "briefing.md")
    c_text = _read_text_safe(pack_dir / "chatgpt_c.md")
    a_text = _read_text_safe(pack_dir / "chatgpt_a.md")
    b_text = _read_text_safe(pack_dir / "gemini_b.md")
    d_text = _read_text_safe(pack_dir / "claude_d.md")
    date = pack_dir.name
    lines = [
        f"# Manual Betting Report — {date}",
        "",
        "Pack lines are authoritative; all quotes are verbatim.",
        "",
        "## Briefing",
        briefing[:2000],
        "",
    ]
    if c_text:
        lines += ["## C (injury/lineup research)", c_text[:3000], ""]
    for label, txt in [("A", a_text), ("B", b_text), ("D", d_text)]:
        if txt:
            lines += [f"## {label}", txt[:1500], ""]
    totals_bytes, _ = rc.load_game_totals(pack_dir)
    team_totals_bytes, _ = rc.load_team_totals(pack_dir)
    lines += ["## Game Totals (projection board)", ""]
    if totals_bytes:
        lines.append(totals_bytes.decode("utf-8-sig"))
    else:
        lines.append("(game_totals.csv not present in pack)")
    lines += ["", "## Team Totals (projection board)", ""]
    if team_totals_bytes:
        lines.append(team_totals_bytes.decode("utf-8-sig"))
    else:
        lines.append("(team_totals.csv not present in pack)")
    lines += ["", "## Candidates (quoted)", ""]
    for r in rows[:10]:
        mid = r.get("market_id", "")
        sel = r.get("selection", "")
        ln = r.get("line", "")
        pr = r.get("price", "")
        lines.append(f"- {mid}: {sel} @ {ln} ({pr})")
    out_name = "manual_betting_report.md"
    rc.atomic_write(pack_dir, out_name, "", "\n".join(lines))
    return pack_dir / out_name


def _write_status(pack_dir: Path, payload: dict) -> None:
    pack_dir.mkdir(parents=True, exist_ok=True)
    rc.atomic_write(pack_dir, STATUS_NAME, "", json.dumps(payload, indent=2, sort_keys=True))


def write_status(pack_dir: Path, status: dict) -> None:
    """Public alias used by older callers."""
    _write_status(pack_dir, status)


def validate_desk_request(steps: Sequence[str] | None) -> tuple[tuple[str, ...], str | None]:
    """Validate requested steps against PHASES; return (normalized, error)."""
    selected = PHASES if steps is None else steps
    requested = tuple(step.upper() for step in selected)
    unknown = [step for step in requested if step not in PHASES]
    if unknown:
        return requested, f"Unknown desk phase(s): {','.join(unknown)}"
    return requested, None


def build_initial_status(pack_dir: Path) -> dict[str, Any]:
    """Build the initial status payload (date, generated_at, game/team totals, ...)."""
    totals_bytes, totals_hash = rc.load_game_totals(pack_dir)
    team_totals_bytes, team_totals_hash = rc.load_team_totals(pack_dir)
    return {
        "date": pack_dir.name,
        "generated_at": _now_iso(),
        "overall": "running",
        "components": {},
        "final_report": {},
        "game_totals": {
            "file": rc.GAME_TOTALS_NAME,
            "present": totals_bytes is not None,
            "sha256": totals_hash,
        },
        "team_totals": {
            "file": rc.TEAM_TOTALS_NAME,
            "present": team_totals_bytes is not None,
            "sha256": team_totals_hash,
        },
        "notes": [],
    }


def run_synthesis_phase(
    pack_dir: Path,
    results: dict[str, StageResult],
    *,
    force: bool = False,
    allow_local_synth: bool = False,
) -> StageResult:
    """Run E sequentially, after every independent future has completed AND
    the upstream readiness gate (validated CURRENT A/D/B; C optional) passes.
    """
    from outlier_scrapers import desk_snapshot

    if not desk_snapshot.upstream_ready_for_e(pack_dir, results):
        return _stage_result(
            "E",
            execution_state=StageExecutionState.GATED,
            artifact_state=ArtifactState.MISSING,
            error_code=StageErrorCode.MISSING_INPUT,
            reason="E inputs incomplete: A/D/B do not all validate as CURRENT",
        )

    result = run_phase("E", pack_dir, force=force)
    if allow_local_synth and result.execution_state is not StageExecutionState.SUCCEEDED:
        try:
            content = local_synthesize_inputs(pack_dir)
            fm = "---\nmodel: local-synthesis-fallback\n---\n\n"
            rc.atomic_write(pack_dir, PHASE_OUTPUTS["E"], fm, content)
            result = dataclasses.replace(
                result,
                execution_state=StageExecutionState.SUCCEEDED,
                used_local_fallback=True,
            )
        except Exception as ex:
            logger.warning("local concat failed: %s", ex)
    return result


def advance_snapshot(
    pack_dir: Path, status: dict[str, Any], *, hold_locks: bool = True
) -> dict[str, Any] | None:
    """Wrap ``desk_snapshot.maybe_advance_desk`` and populate status fields."""
    from outlier_scrapers import desk_snapshot, verdict_policy, verdict_store

    snapshot: dict[str, Any] | None = None
    try:
        snapshot = desk_snapshot.maybe_advance_desk(pack_dir, hold_locks=hold_locks)
        if snapshot is not None:
            status["desk_snapshot"] = {
                "synthesis_source": snapshot.get("synthesis_source"),
                "publications": snapshot.get("publications"),
            }
            if snapshot.get("synthesis_source"):
                status.setdefault("final_report", {})
                status["final_report"]["source"] = snapshot["synthesis_source"]
        policy = verdict_policy.load_verdict_policy()
        status["verdicts"] = {
            "policy_mode": policy.mode,
            "rejected_count": verdict_store.rejected_count_from_snapshot(pack_dir),
            "synthesis_source": (snapshot or {}).get("synthesis_source"),
        }
    except Exception as ex:
        status["notes"].append(f"desk snapshot skipped: {ex}")
    return snapshot


def resolve_final_report(pack_dir: Path) -> FinalReportResolution:
    """Snapshot-first final-report resolution.

    Delegates the fingerprint/pinned-publication authority check to
    :func:`desk_snapshot.validate_snapshot_for_final_report` (reused, not
    reimplemented) and layers the ``file``/``compatibility_artifact`` fields
    on top. ``claude_e.md`` and ``manual_betting_report.md`` are treated as
    non-authoritative rendered compatibility artifacts of the snapshot's
    pinned publication -- never the source of truth themselves.
    """
    from outlier_scrapers import desk_snapshot, verdict_report

    result = desk_snapshot.validate_snapshot_for_final_report(pack_dir)
    if not result.authoritative:
        return result

    e_md = pack_dir / PHASE_OUTPUTS["E"]
    manual_md = pack_dir / "manual_betting_report.md"
    compatibility_artifact: Path | None = None
    if result.source == verdict_report.SYNTHESIS_CLAUDE_E and e_md.exists():
        compatibility_artifact = e_md
    elif manual_md.exists():
        compatibility_artifact = manual_md

    return dataclasses.replace(
        result,
        file=pack_dir / "verdicts" / desk_snapshot.SNAPSHOT_NAME,
        compatibility_artifact=compatibility_artifact,
    )


def compute_overall_state(
    pack_dir: Path,
    requested: Sequence[str],
    results: dict[str, StageResult],
    final_report: FinalReportResolution,
) -> tuple[str, int]:
    """FULL/PARTIAL/DATA_ONLY + exit code, from committed publications only."""
    identity = rc.load_pack_identity(pack_dir)

    def usable(phase: str) -> bool:
        if phase in results:
            return results[phase].execution_state is StageExecutionState.SUCCEEDED
        return rc.validate_current_publication(pack_dir, phase, identity).current

    e_usable = usable("E")
    required_usable = all(usable(phase) for phase in REQUIRED_UPSTREAM_PHASES)

    if e_usable and required_usable:
        return "FULL", 0
    if e_usable or final_report.authoritative:
        return "PARTIAL", 0
    return "DATA_ONLY", 1


def orchestrate_desk(
    pack_dir: Path,
    *,
    steps: Sequence[str] | None = None,
    force: bool = False,
    allow_local_synth: bool = False,
    hold_locks: bool = True,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> int:
    load_environment()
    requested, error = validate_desk_request(steps)
    if error:
        logger.error(error)
        return 1

    status = build_initial_status(pack_dir)

    if not (pack_dir / "briefing.md").exists() or not (pack_dir / "candidates.csv").exists():
        status["overall"] = "DATA_ONLY"
        status["notes"].append("missing briefing.md or candidates.csv")
        write_status(pack_dir, status)
        return 1

    independent_requested = [p for p in INDEPENDENT_PHASES if p in requested]
    results: dict[str, StageResult] = run_independent_phases(
        independent_requested, pack_dir, force=force, max_workers=max_workers
    )

    if "E" in requested:
        results["E"] = run_synthesis_phase(
            pack_dir, results, force=force, allow_local_synth=allow_local_synth
        )
        if results["E"].execution_state is StageExecutionState.GATED:
            status["notes"].append("E inputs incomplete")

    status["components"] = {
        phase: result.as_status_dict() for phase, result in results.items()
    }

    advance_snapshot(pack_dir, status, hold_locks=hold_locks)
    final_report = resolve_final_report(pack_dir)

    if final_report.authoritative:
        status["final_report"] = final_report.as_status_dict()
    elif allow_local_synth:
        try:
            fpath = produce_manual_betting_report(pack_dir)
            from outlier_scrapers import verdict_report

            report_text = fpath.read_text(encoding="utf-8") if fpath else ""
            source = verdict_report.read_synthesis_source(report_text) or "local_synthesis"
            status["notes"].append("produced manual_betting_report.md via local synthesis")
            status["final_report"] = {"source": source, "file": str(fpath.name)}
        except Exception as ex:
            status["notes"].append(f"manual report synthesis failed: {ex}")
            status["final_report"] = final_report.as_status_dict()
    else:
        status["final_report"] = final_report.as_status_dict()

    overall, exit_code = compute_overall_state(pack_dir, requested, results, final_report)
    status["overall"] = overall
    status["generated_at"] = _now_iso()
    write_status(pack_dir, status)
    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="AI Research Desk orchestrator (A/B/C/D/E).")
    parser.add_argument("--date", default=datetime.now().astimezone().strftime("%Y-%m-%d"))
    parser.add_argument("--force", action="store_true", help="Force re-run of steps (bypass hash check).")
    parser.add_argument(
        "--steps",
        default=",".join(PHASES),
        help="Comma-separated subset of A,B,C,D,E. Default: A,B,C,D,E",
    )
    parser.add_argument(
        "--no-local-fallback",
        dest="allow_local",
        action="store_false",
        default=True,
        help="Disable local synthesis fallback (manual_betting_report.md + claude_e placeholder).",
    )
    parser.add_argument(
        "--break-stale-lock",
        action="store_true",
        help="Break packs/<date>/verdicts/.writer_lock only if the stale-lock rule allows.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=DEFAULT_MAX_WORKERS,
        help="Max concurrent A/B/C/D workers (default: 4). Must be positive.",
    )
    args = parser.parse_args(argv)

    if args.max_workers < 1:
        parser.error("--max-workers must be positive")

    steps = [s.strip().upper() for s in args.steps.split(",") if s.strip()]
    pack_dir = paths.PROJECT_ROOT / "packs" / args.date

    if args.break_stale_lock:
        from outlier_scrapers import desk_snapshot

        result = desk_snapshot.break_stale_lock(pack_dir)
        print(json.dumps(result, indent=2, sort_keys=True))
        if result.get("broken") or result.get("reason") == "no_lock":
            return 0
        return 1

    if not (pack_dir / "candidates.csv").exists() and not (pack_dir / "briefing.md").exists():
        logger.warning("Pack for %s missing candidates.csv or briefing.md. Produce the pack first.", args.date)

    return orchestrate_desk(
        pack_dir,
        steps=steps,
        force=args.force,
        allow_local_synth=args.allow_local,
        max_workers=args.max_workers,
    )


if __name__ == "__main__":
    raise SystemExit(main())
