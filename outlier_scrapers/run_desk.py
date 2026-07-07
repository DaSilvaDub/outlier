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
from typing import Sequence

from outlier_scrapers import paths
from outlier_scrapers import runner_common as rc

from outlier_scrapers import reasoning
from outlier_scrapers import gemini_research
from outlier_scrapers import c_research
from outlier_scrapers import claude_reasoning
from outlier_scrapers import claude_synthesis

logger = logging.getLogger(__name__)

PHASES = ["A", "B", "C", "D", "E"]
PHASE_OUTPUTS = {
    "A": "chatgpt_a.md",
    "B": "gemini_b.md",
    "C": "chatgpt_c.md",
    "D": "claude_d.md",
    "E": "claude_e.md",
}
PHASE_RUNNERS = {
    "A": reasoning.run_reasoning,
    "B": gemini_research.run_gemini_b,
    "C": c_research.run_c_research,
    "D": claude_reasoning.run_claude_d,
    "E": claude_synthesis.run_claude_e,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_status(pack_dir: Path, status: dict) -> None:
    status_path = pack_dir / "reasoning_status.json"
    tmp = status_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(status, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, status_path)
    logger.info("Wrote %s", status_path)


def _extract_request_sha(p: Path) -> str | None:
    if not p.exists():
        return None
    try:
        txt = p.read_text(encoding="utf-8")
        if not txt.startswith("---\n"):
            return None
        end = txt.find("\n---\n", 4)
        if end == -1:
            return None
        for line in txt[4:end].splitlines():
            if "request_sha256:" in line:
                return line.split(":", 1)[1].strip().strip("'\"")
    except Exception:
        return None
    return None


def _phase_has_key(phase: str) -> bool:
    if phase in ("A",):
        return bool(os.getenv("OPENAI_API_KEY"))
    if phase in ("B", "C"):
        return bool(os.getenv("GEMINI_API_KEY"))
    if phase in ("D", "E"):
        return bool(os.getenv("ANTHROPIC_API_KEY"))
    return True


def run_phase(phase: str, pack_dir: Path, *, force: bool = False) -> tuple[int, str]:
    if phase not in PHASE_RUNNERS:
        return 1, "failed"
    if not _phase_has_key(phase):
        return 1, "skipped-no-key"
    try:
        out = pack_dir / PHASE_OUTPUTS[phase]
        prev_sha = _extract_request_sha(out)
        rc_code = PHASE_RUNNERS[phase](pack_dir, force=force, refresh_if_stale=not force)
        new_sha = _extract_request_sha(out)
        if rc_code == 0:
            if prev_sha and new_sha and prev_sha == new_sha:
                return 0, "cached"
            if force:
                return 0, "forced-refresh"
            return 0, "success"
        return rc_code, "failed"
    except Exception:
        logger.exception("phase %s error", phase)
        return 1, "failed"


def local_synthesize_inputs(pack_dir: Path) -> str:
    """Concat for fallback claude_e slot. C optional."""
    parts = []
    for fname in ("briefing.md", "chatgpt_a.md", "gemini_b.md", "claude_d.md"):
        p = pack_dir / fname
        if p.exists():
            parts.append(f"\n\n===== {fname.upper().replace('.MD','')} =====\n" + p.read_text(encoding="utf-8"))
    c = pack_dir / "chatgpt_c.md"
    if c.exists():
        parts.append("\n\n===== CHATGPT_C (optional) =====\n" + c.read_text(encoding="utf-8"))
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
    # Simple candidates section quoting exact ids/lines
    lines += ["## Candidates (quoted)", ""]
    for r in rows[:10]:
        mid = r.get("market_id", "")
        sel = r.get("selection", "")
        ln = r.get("line", "")
        pr = r.get("price", "")
        lines.append(f"- {mid}: {sel} @ {ln} ({pr})")
    outp = pack_dir / "manual_betting_report.md"
    outp.write_text("\n".join(lines), encoding="utf-8")
    return outp


def _inputs_for_e_present(pack_dir: Path) -> bool:
    needed = ["briefing.md", "chatgpt_a.md", "gemini_b.md", "claude_d.md"]
    return all((pack_dir / n).exists() for n in needed)


def _has_final_report(pack_dir: Path) -> tuple[bool, str, Path | None]:
    e = pack_dir / "claude_e.md"
    if e.exists():
        return True, "claude_e", e
    m = pack_dir / "manual_betting_report.md"
    if m.exists():
        return True, "local_synthesis", m
    return False, "", None


def _compute_overall(components: dict, has_final: bool) -> str:
    a_ok = components.get("A", {}).get("status") in ("success", "cached", "forced-refresh")
    b_ok = components.get("B", {}).get("status") in ("success", "cached", "forced-refresh")
    d_ok = components.get("D", {}).get("status") in ("success", "cached", "forced-refresh")
    if a_ok and b_ok and d_ok and has_final:
        return "FULL"
    if (a_ok or b_ok or d_ok) and has_final:
        return "PARTIAL"
    return "DATA_ONLY"


def orchestrate_desk(
    pack_dir: Path,
    *,
    steps: Sequence[str] | None = None,
    force: bool = False,
    allow_local_synth: bool = True,
) -> int:
    if not pack_dir.exists() or not ((pack_dir / "candidates.csv").exists() or (pack_dir / "briefing.md").exists()):
        logger.error("Pack directory %s is missing required briefing.md or candidates.csv. Run pack first.", pack_dir)
        status = {"date": pack_dir.name, "generated_at": _now_iso(), "overall": "DATA_ONLY",
                  "components": {}, "final_report": {}, "notes": ["missing pack inputs"]}
        write_status(pack_dir, status)
        return 1

    requested = [s for s in (steps or PHASES) if s in PHASES]

    status = {
        "date": pack_dir.name,
        "generated_at": _now_iso(),
        "overall": "running",
        "components": {},
        "final_report": {},
        "notes": [],
    }
    write_status(pack_dir, status)

    comp = {}

    # A, B, C (Prompt C style injury/lineup), D
    for ph in [p for p in requested if p in ("A", "B", "C", "D")]:
        logger.info("=== Phase %s ===", ph)
        out_file = pack_dir / PHASE_OUTPUTS[ph]
        prev = _extract_request_sha(out_file)
        ec, tok = run_phase(ph, pack_dir, force=force)
        cur = _extract_request_sha(out_file)
        comp[ph] = {
            "status": tok,
            "file": PHASE_OUTPUTS[ph],
            "request_sha256": cur or prev or "",
        }
        if tok == "failed":
            status["notes"].append(f"{ph} failed")
        status["components"] = comp
        write_status(pack_dir, status)

    # E (gated on A/B/D; C optional)
    if "E" in requested:
        logger.info("=== Phase E ===")
        if not _inputs_for_e_present(pack_dir):
            logger.warning("E gated: missing one or more of briefing/A/B/D outputs")
            comp["E"] = {"status": "failed", "file": "claude_e.md", "request_sha256": ""}
            status["notes"].append("E inputs incomplete")
        else:
            has_key = _phase_has_key("E")
            if has_key and not force:
                ec, tok = run_phase("E", pack_dir, force=False)
            else:
                if not has_key:
                    tok = "skipped-no-key"
                    ec = 1
                else:
                    ec, tok = run_phase("E", pack_dir, force=True)

            if ec == 0:
                comp["E"] = {"status": tok or "success", "file": "claude_e.md", "request_sha256": _extract_request_sha(pack_dir/"claude_e.md") or ""}
            else:
                comp["E"] = {"status": tok or "failed", "file": "claude_e.md", "request_sha256": ""}
                if allow_local_synth:
                    try:
                        content = local_synthesize_inputs(pack_dir)
                        fm = "---\nmodel: local-synthesis-fallback\n---\n\n"
                        rc.atomic_write(pack_dir, "claude_e.md", fm, content)
                        comp["E"]["status"] = "success"
                        comp["E"]["used_local_fallback"] = True
                    except Exception as ex:
                        status["notes"].append(f"local concat failed: {ex}")

        status["components"] = comp
        write_status(pack_dir, status)

    # Always guarantee a final betting report
    has_final, source, fpath = _has_final_report(pack_dir)
    if not has_final and allow_local_synth:
        try:
            fpath = produce_manual_betting_report(pack_dir)
            has_final = True
            source = "local_synthesis"
            if "E" in comp:
                comp["E"]["used_local_fallback"] = True
            status["notes"].append("produced manual_betting_report.md via local synthesis")
        except Exception as ex:
            status["notes"].append(f"manual report synthesis failed: {ex}")

    status["components"] = comp
    if has_final and fpath:
        status["final_report"] = {
            "source": source,
            "file": str(fpath.name),
        }

    overall = _compute_overall(comp, has_final)
    status["overall"] = overall
    status["generated_at"] = _now_iso()
    write_status(pack_dir, status)

    return 0 if overall in ("FULL", "PARTIAL") else 1


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="AI Research Desk orchestrator (A/B/C/D/E).")
    parser.add_argument("--date", default=datetime.now().astimezone().strftime("%Y-%m-%d"))
    parser.add_argument("--force", action="store_true", help="Force re-run of steps (bypass hash check).")
    parser.add_argument(
        "--steps",
        default="A,B,C,D,E",
        help="Comma-separated subset of A,B,C,D,E. Default: A,B,C,D,E",
    )
    parser.add_argument(
        "--no-local-fallback",
        dest="allow_local",
        action="store_false",
        default=True,
        help="Disable local synthesis fallback (manual_betting_report.md + claude_e placeholder).",
    )
    args = parser.parse_args(argv)

    steps = [s.strip().upper() for s in args.steps.split(",") if s.strip()]
    pack_dir = paths.PROJECT_ROOT / "packs" / args.date

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
