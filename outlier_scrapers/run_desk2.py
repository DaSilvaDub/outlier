"""Desk 2 — model-strength-specialized, manual/offline research desk.

A second option alongside the A-E desk (``run_desk``). Where the A-E desk
splits work by *function* (reason vs research) and calls provider APIs, Desk 2
splits work by *model strength* and is MANUAL-ONLY:

    Q = ChatGPT  (quantitative / EV pack-only reasoning)
    W = Gemini   (grounded web research, primary sources)
    X = Grok     (live-X sentiment / late-breaking; Tier-3 by default)
    R = Claude   (skeptical pack-only reasoning + contradiction detection)
    S = Claude   (head-of-desk synthesis of Q/W/X/R)

Two stages:
  * ``generate`` writes a paste-ready doc per phase (shared ROLE_BLOCK +
    the phase prompt + the pack Data block). You paste each into its model
    and save the reply as ``chatgpt_q.md`` / ``gemini_w.md`` / ``grok_x.md`` /
    ``claude_r.md`` / ``claude_s.md``.
  * ``assemble`` stitches the saved replies into ``manual_desk2_report.md``.

By construction this module imports no provider SDK, needs no API key, and
makes no network call — it only reads pack files and writes docs. That keeps
it inside the house rule "never run reasoning models unless explicitly asked".

Entry point: ``python -m outlier_scrapers.run_desk2``
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from outlier_scrapers import pack, paths
from outlier_scrapers import runner_common as rc

logger = logging.getLogger(__name__)

DESK2_PHASES: tuple[str, ...] = ("Q", "W", "X", "R", "S")

PHASE_MODEL: dict[str, str] = {
    "Q": "chatgpt",
    "W": "gemini",
    "X": "grok",
    "R": "claude",
    "S": "claude",
}

# The model's reply — you save each model's answer here after pasting.
REPLY_OUTPUTS: dict[str, str] = {
    "Q": "chatgpt_q.md",
    "W": "gemini_w.md",
    "X": "grok_x.md",
    "R": "claude_r.md",
    "S": "claude_s.md",
}

# The generated paste-ready doc you copy into each model.
PASTE_OUTPUTS: dict[str, str] = {
    "Q": "paste_q_chatgpt.md",
    "W": "paste_w_gemini.md",
    "X": "paste_x_grok.md",
    "R": "paste_r_claude.md",
    "S": "paste_s_claude.md",
}

PROMPT_FILES: dict[str, str] = {
    "Q": "Q_chatgpt.md",
    "W": "W_gemini.md",
    "X": "X_grok.md",
    "R": "R_claude.md",
    "S": "S_claude.md",
}

# Pack-only reasoning phases: Data block = candidates + totals, no web.
PACK_ONLY: frozenset[str] = frozenset({"Q", "R"})
# Research phases: also get the briefing so findings tie to the slate.
RESEARCH: frozenset[str] = frozenset({"W", "X"})
# Synthesis phase: also gets the saved Q/W/X/R replies.
SYNTH: frozenset[str] = frozenset({"S"})
# The four inputs a full synthesis needs.
INPUT_PHASES: tuple[str, ...] = ("Q", "W", "X", "R")

STATUS_NAME = "desk2_status.json"
MANUAL_REPORT_NAME = "manual_desk2_report.md"
_REPLY_CAP = 4000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prompt_dir() -> Path:
    return paths.PROJECT_ROOT / "prompts" / "desk2"


def _load_prompt(phase: str) -> str:
    return rc.read_required_text(_prompt_dir() / PROMPT_FILES[phase], f"prompt {phase}")


def _load_pack_data(pack_dir: Path) -> tuple[bytes, bytes | None, bytes | None]:
    """Validated candidates (locked events dropped) + optional totals streams."""
    totals_bytes, _, team_totals_bytes, _ = rc.load_all_totals(pack_dir)
    allow_empty = rc.has_actionable_any_totals(totals_bytes, team_totals_bytes)
    candidates_bytes, _ = rc.validate_candidates(pack_dir, allow_empty=allow_empty)
    return candidates_bytes, totals_bytes, team_totals_bytes


def _reply_sections(pack_dir: Path) -> list[str]:
    """Labeled Q/W/X/R replies for the synthesis paste doc / manual report."""
    parts: list[str] = []
    for phase in INPUT_PHASES:
        p = pack_dir / REPLY_OUTPUTS[phase]
        if p.exists():
            body = p.read_text(encoding="utf-8")[:_REPLY_CAP]
            parts += ["", f"===== {phase} ({PHASE_MODEL[phase]}) =====", body]
    return parts


def build_paste_doc(phase: str, pack_dir: Path) -> str:
    """Assemble one paste-ready doc: ROLE_BLOCK + phase prompt + Data block."""
    phase = phase.upper()
    if phase not in DESK2_PHASES:
        raise rc.RunnerError(f"unknown Desk 2 phase: {phase!r}")

    prompt = _load_prompt(phase)
    candidates_bytes, totals_bytes, team_totals_bytes = _load_pack_data(pack_dir)
    data_block = rc.build_reasoning_data_block(
        candidates_bytes, totals_bytes, team_totals_bytes
    )

    parts: list[str] = ["\n".join(pack.ROLE_BLOCK), "", prompt]
    if phase in RESEARCH or phase in SYNTH:
        briefing = rc.read_required_text(pack_dir / "briefing.md", "briefing.md")
        parts += ["", "===== BRIEFING =====", briefing]
    parts += ["", "Data:", data_block]
    if phase in SYNTH:
        parts += _reply_sections(pack_dir)
    return "\n".join(parts)


def generate_paste_docs(
    pack_dir: Path, steps: Sequence[str] | None = None
) -> dict[str, str]:
    """Write a paste-ready doc for each requested phase; return {phase: file}."""
    selected = DESK2_PHASES if steps is None else tuple(s.upper() for s in steps)
    written: dict[str, str] = {}
    for phase in selected:
        if phase not in DESK2_PHASES:
            raise rc.RunnerError(f"unknown Desk 2 phase: {phase!r}")
        doc = build_paste_doc(phase, pack_dir)
        out_name = PASTE_OUTPUTS[phase]
        rc.atomic_write(pack_dir, out_name, "", doc)
        written[phase] = out_name
    return written


def _load_candidates(pack_dir: Path) -> list[dict[str, str]]:
    f = pack_dir / "candidates.csv"
    if not f.exists():
        return []
    with f.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _read_text_safe(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8") if p.exists() else ""
    except OSError:
        return ""


def produce_manual_report(pack_dir: Path) -> Path:
    """Stitch saved Q/W/X/R replies + quoted pack lines into one report."""
    rows = _load_candidates(pack_dir)
    kept, locked = pack.drop_locked_events(rows, now=datetime.now().astimezone())
    if locked:
        logger.warning(
            "desk2 manual report: dropped %d candidate(s) locked or without a start time",
            len(locked),
        )
    date = pack_dir.name
    lines: list[str] = [
        f"# Desk 2 Manual Report — {date}",
        "",
        "Model-specialized desk (Q=ChatGPT, W=Gemini, X=Grok, R=Claude). "
        "Pack lines are authoritative; all quotes are verbatim. X items are "
        "Tier-3 (sentiment) — informational unless corroborated by W or the pack.",
        "",
        "## Briefing",
        _read_text_safe(pack_dir / "briefing.md")[:2000],
        "",
    ]
    for phase in INPUT_PHASES:
        txt = _read_text_safe(pack_dir / REPLY_OUTPUTS[phase])
        if txt:
            lines += [f"## {phase} — {PHASE_MODEL[phase]}", txt[:_REPLY_CAP], ""]

    totals_bytes, _ = rc.load_game_totals(pack_dir)
    team_totals_bytes, _ = rc.load_team_totals(pack_dir)
    lines += ["## Game Totals (projection board)", ""]
    lines.append(
        totals_bytes.decode("utf-8-sig")
        if totals_bytes
        else "(game_totals.csv not present in pack)"
    )
    lines += ["", "## Team Totals (projection board)", ""]
    lines.append(
        team_totals_bytes.decode("utf-8-sig")
        if team_totals_bytes
        else "(team_totals.csv not present in pack)"
    )

    lines += ["", "## Candidates (quoted)", ""]
    for r in kept[:10]:
        lines.append(
            f"- {r.get('market_id', '')}: {r.get('selection', '')} "
            f"@ {r.get('line', '')} ({r.get('price', '')})"
        )
    lines += [
        "",
        "## AGREE/DISAGREE (fill during synthesis)",
        "market_id × Q/W/X/R — mark BET/LEAN/PASS/FADE per pass; X is a lead only.",
        "",
    ]
    rc.atomic_write(pack_dir, MANUAL_REPORT_NAME, "", "\n".join(lines))
    return pack_dir / MANUAL_REPORT_NAME


def _write_status(pack_dir: Path, payload: dict) -> None:
    pack_dir.mkdir(parents=True, exist_ok=True)
    rc.atomic_write(
        pack_dir, STATUS_NAME, "", json.dumps(payload, indent=2, sort_keys=True)
    )


def _base_status(pack_dir: Path, stage: str) -> dict:
    return {
        "date": pack_dir.name,
        "generated_at": _now_iso(),
        "desk": "desk2",
        "stage": stage,
        "overall": "running",
        "notes": [],
    }


def orchestrate_desk2(
    pack_dir: Path,
    *,
    steps: Sequence[str] | None = None,
    stage: str = "generate",
) -> int:
    """Run one Desk 2 stage. ``generate`` writes paste docs; ``assemble``
    stitches saved replies. Never calls a provider."""
    stage = stage.lower()
    status = _base_status(pack_dir, stage)
    has_inputs = (pack_dir / "briefing.md").exists() and (
        pack_dir / "candidates.csv"
    ).exists()

    if stage == "generate":
        if not has_inputs:
            status["overall"] = "DATA_ONLY"
            status["notes"].append("missing briefing.md or candidates.csv")
            _write_status(pack_dir, status)
            return 1
        written = generate_paste_docs(pack_dir, steps)
        status["paste_docs"] = written
        status["overall"] = "GENERATED"
        status["generated_at"] = _now_iso()
        _write_status(pack_dir, status)
        return 0

    if stage == "assemble":
        present = [p for p in INPUT_PHASES if (pack_dir / REPLY_OUTPUTS[p]).exists()]
        status["replies_present"] = present
        if not present:
            status["overall"] = "DATA_ONLY"
            status["notes"].append("no Q/W/X/R replies to assemble")
            _write_status(pack_dir, status)
            return 1
        report = produce_manual_report(pack_dir)
        status["overall"] = "FULL" if len(present) == len(INPUT_PHASES) else "PARTIAL"
        status["final_report"] = {
            "source": "local_desk2_synthesis",
            "file": report.name,
        }
        status["generated_at"] = _now_iso()
        _write_status(pack_dir, status)
        return 0

    logger.error("Unknown Desk 2 stage: %s", stage)
    status["overall"] = "ERROR"
    status["notes"].append(f"unknown stage {stage!r}")
    _write_status(pack_dir, status)
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    parser = argparse.ArgumentParser(
        description="Desk 2 — model-specialized manual research desk (Q/W/X/R/S)."
    )
    parser.add_argument(
        "--date", default=datetime.now().astimezone().strftime("%Y-%m-%d")
    )
    parser.add_argument(
        "--stage",
        choices=("generate", "assemble"),
        default="generate",
        help="generate paste-ready docs, or assemble saved replies.",
    )
    parser.add_argument(
        "--steps",
        default=",".join(DESK2_PHASES),
        help="Comma-separated subset of Q,W,X,R,S. Default: all.",
    )
    args = parser.parse_args(argv)

    steps = [s.strip().upper() for s in args.steps.split(",") if s.strip()]
    pack_dir = paths.PROJECT_ROOT / "packs" / args.date
    if not (pack_dir / "candidates.csv").exists():
        logger.warning(
            "Pack for %s missing candidates.csv. Produce the pack first.", args.date
        )
    return orchestrate_desk2(pack_dir, steps=steps, stage=args.stage)


if __name__ == "__main__":
    raise SystemExit(main())
