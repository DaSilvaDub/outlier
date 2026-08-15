"""AI Research Desk orchestrator.

Entry point: python -m outlier_scrapers.run_desk

Orchestrates A/B/C/D/E passes. C is the automated Prompt C style narrow research pass
for injury and lineup context. Ties all findings to exact pack market_ids and quoted
lines without altering them.

Status uses components + final_report + manual_betting_report.md for the skill contract.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence, Any

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

logger = logging.getLogger(__name__)

PHASES = ("A", "B", "C", "D", "E")
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
SUCCESS_STATES = {"success", "cached", "forced-refresh"}
STATUS_NAME = "reasoning_status.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _request_hash(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return rc.extract_yaml_request_hash(path.read_text(encoding="utf-8")) or ""
    except OSError:
        return ""


def _restore_on_failure(path: Path, previous: bytes | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
        return
    rc.atomic_write(path.parent, path.name, "", previous.decode("utf-8"))


def run_phase(phase: str, pack_dir: Path, *, force: bool = False) -> dict[str, str]:
    if phase not in PHASE_RUNNERS:
        return {"status": "failed", "file": "", "request_sha256": ""}

    output = pack_dir / PHASE_OUTPUTS[phase]
    previous = output.read_bytes() if output.exists() else None
    previous_hash = _request_hash(output)

    if force and not os.getenv(PHASE_KEYS[phase]):
        return {
            "status": "skipped-no-key",
            "file": PHASE_OUTPUTS[phase],
            "request_sha256": previous_hash,
        }

    try:
        exit_code = PHASE_RUNNERS[phase](
            pack_dir, force=force, refresh_if_stale=not force
        )
    except Exception:
        logger.exception("Phase %s failed", phase)
        exit_code = 1

    if exit_code == 0 and not output.exists():
        exit_code = 1

    if exit_code != 0:
        _restore_on_failure(output, previous)
        status = "skipped-no-key" if not os.getenv(PHASE_KEYS[phase]) else "failed"
        return {
            "status": status,
            "file": PHASE_OUTPUTS[phase],
            "request_sha256": _request_hash(output) or previous_hash,
        }

    current_hash = _request_hash(output)
    if previous_hash and current_hash == previous_hash:
        status = "cached"
    elif force:
        status = "forced-refresh"
    else:
        status = "success"
    return {
        "status": status,
        "file": PHASE_OUTPUTS[phase],
        "request_sha256": current_hash,
    }


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
    """Minimal manual report per contract. Quotes pack lines exactly. C findings included if present."""
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


def _has_final_report(pack_dir: Path) -> tuple[bool, str, Path | None]:
    e = pack_dir / PHASE_OUTPUTS["E"]
    if e.exists():
        return True, "claude_e", e
    m = pack_dir / "manual_betting_report.md"
    if m.exists():
        return True, "local_synthesis", m
    return False, "", None


def orchestrate_desk(
    pack_dir: Path,
    *,
    steps: Sequence[str] | None = None,
    force: bool = False,
    allow_local_synth: bool = False,
) -> int:
    load_environment()
    selected = PHASES if steps is None else steps
    requested = tuple(step.upper() for step in selected)
    unknown = [step for step in requested if step not in PHASES]
    if unknown:
        logger.error("Unknown desk phase(s): %s", ",".join(unknown))
        return 1

    totals_bytes, totals_hash = rc.load_game_totals(pack_dir)
    team_totals_bytes, team_totals_hash = rc.load_team_totals(pack_dir)
    status: dict[str, Any] = {
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

    if not (pack_dir / "briefing.md").exists() or not (pack_dir / "candidates.csv").exists():
        status["overall"] = "DATA_ONLY"
        status["notes"].append("missing briefing.md or candidates.csv")
        _write_status(pack_dir, status)
        return 1

    components: dict[str, dict[str, Any]] = {}
    for phase in ("A", "B", "C", "D"):
        if phase in requested:
            components[phase] = run_phase(phase, pack_dir, force=force)

    if "E" in requested:
        required = ("briefing.md", "chatgpt_a.md", "gemini_b.md", "claude_d.md")
        if all((pack_dir / name).exists() for name in required):
            components["E"] = run_phase("E", pack_dir, force=force)
            if (
                allow_local_synth
                and components["E"].get("status") not in SUCCESS_STATES
            ):
                try:
                    content = local_synthesize_inputs(pack_dir)
                    fm = "---\nmodel: local-synthesis-fallback\n---\n\n"
                    rc.atomic_write(pack_dir, PHASE_OUTPUTS["E"], fm, content)
                    components["E"]["status"] = "success"
                    components["E"]["used_local_fallback"] = True
                except Exception as ex:
                    status["notes"].append(f"local concat failed: {ex}")
        else:
            components["E"] = {
                "status": "gated-missing-input",
                "file": PHASE_OUTPUTS["E"],
                "request_sha256": "",
            }
            status["notes"].append("E inputs incomplete")

    status["components"] = components

    has_final, source, fpath = _has_final_report(pack_dir)
    if not has_final and allow_local_synth:
        try:
            fpath = produce_manual_betting_report(pack_dir)
            has_final = True
            source = "local_synthesis"
            status["notes"].append("produced manual_betting_report.md via local synthesis")
        except Exception as ex:
            status["notes"].append(f"manual report synthesis failed: {ex}")

    e_usable = (
        components.get("E", {}).get("status") in SUCCESS_STATES
        if "E" in requested
        else (pack_dir / PHASE_OUTPUTS["E"]).exists()
    )
    required_usable = all(
        (
            components.get(phase, {}).get("status") in SUCCESS_STATES
            if phase in requested
            else (pack_dir / PHASE_OUTPUTS[phase]).exists()
        )
        for phase in ("A", "B", "D")
    )

    if has_final and fpath:
        status["final_report"] = {"source": source, "file": str(fpath.name)}

    try:
        from outlier_scrapers import desk_snapshot

        snapshot = desk_snapshot.maybe_advance_desk(pack_dir)
        if snapshot is not None:
            status["desk_snapshot"] = {
                "synthesis_source": snapshot.get("synthesis_source"),
                "publications": snapshot.get("publications"),
            }
            if snapshot.get("synthesis_source"):
                status.setdefault("final_report", {})
                status["final_report"]["source"] = snapshot["synthesis_source"]
    except Exception as ex:
        status["notes"].append(f"desk snapshot skipped: {ex}")

    if e_usable and required_usable:
        status["overall"] = "FULL"
        if not status["final_report"]:
            status["final_report"] = {"source": "claude_e", "file": PHASE_OUTPUTS["E"]}
        exit_code = 0
    elif e_usable or has_final:
        status["overall"] = "PARTIAL"
        if not status["final_report"] and fpath:
            status["final_report"] = {"source": source, "file": str(fpath.name)}
        exit_code = 0
    else:
        status["overall"] = "DATA_ONLY"
        exit_code = 1

    status["generated_at"] = _now_iso()
    _write_status(pack_dir, status)
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
    args = parser.parse_args(argv)

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
    )


if __name__ == "__main__":
    raise SystemExit(main())