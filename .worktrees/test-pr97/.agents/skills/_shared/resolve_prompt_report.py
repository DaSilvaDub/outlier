"""Resolve generic or sequential Outlier prompts and shared report paths."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

PACK_DATE_RE = re.compile(r"_pack_(\d{4}-\d{2}-\d{2})(?:\.[^.]+)?$", re.IGNORECASE)
PATH_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
GENERIC_RE = re.compile(r"^\d+_Master_(.+)_pack_(\d{4}-\d{2}-\d{2})\.txt$", re.IGNORECASE)
SEQUENTIAL_TXT_RE = re.compile(
    r"^\d+_Phase([QRWXS])_.+_pack_(\d{4}-\d{2}-\d{2})\.txt$", re.IGNORECASE
)
SEQUENTIAL_MD_RE = re.compile(r"^paste_([QRWXS])_.+\.md$", re.IGNORECASE)
SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9]+")

AGENT_LABELS = {
    "chatgpt": "CHAT",
    "codex": "CHAT",
    "claude": "CLAUDE",
    "gemini": "GEMINI",
    "grok": "GROK",
    "copilot": "COPILOT",
}
SEQUENTIAL_PHASES = ("Q", "R", "W", "X", "S")
PHASE_AGENTS = {
    "Q": frozenset({"chatgpt", "codex"}),
    "R": frozenset({"claude"}),
    "W": frozenset({"gemini"}),
    "X": frozenset({"grok"}),
    "S": frozenset({"claude"}),
}


@dataclass(frozen=True)
class PromptInfo:
    path: Path
    workflow: str
    pack_date: str
    prompt_kind: str
    phase: str | None = None


@dataclass(frozen=True)
class Resolution:
    workflow: str
    prompt_path: str
    report_path: str
    report_dir: str
    agent: str
    pack_date: str
    prompt_kind: str
    phase: str | None
    prior_report_paths: tuple[str, ...]


def desktop_dir() -> Path:
    one_drive_desktop = Path.home() / "OneDrive" / "Desktop"
    return one_drive_desktop if one_drive_desktop.is_dir() else Path.home() / "Desktop"


def pack_date_from_parent(path: Path) -> str:
    for parent in path.parents:
        if PATH_DATE_RE.fullmatch(parent.name):
            return parent.name
    raise ValueError(f"Could not determine pack date from prompt path: {path}")


def classify_prompt(path: Path) -> PromptInfo:
    generic = GENERIC_RE.fullmatch(path.name)
    if generic:
        kind = SAFE_TOKEN_RE.sub("_", generic.group(1)).strip("_")
        return PromptInfo(path, "generic", generic.group(2), f"Master_{kind}")

    sequential_txt = SEQUENTIAL_TXT_RE.fullmatch(path.name)
    if sequential_txt:
        phase = sequential_txt.group(1).upper()
        return PromptInfo(path, "sequential", sequential_txt.group(2), f"Phase{phase}", phase)

    sequential_md = SEQUENTIAL_MD_RE.fullmatch(path.name)
    if sequential_md:
        phase = sequential_md.group(1).upper()
        return PromptInfo(path, "sequential", pack_date_from_parent(path), f"Phase{phase}", phase)

    raise ValueError(f"File is not a supported generated Outlier prompt: {path}")


def prompt_roots(repo_root: Path, workflow: str) -> list[Path]:
    desk = "Desk1_Automated" if workflow == "generic" else "Desk2_Manual"
    roots = [
        desktop_dir() / "today" / "prompts" / desk,
        Path(r"G:\My Drive\today\prompts") / desk,
    ]
    if workflow == "sequential":
        roots.append(repo_root / "packs")
    return roots


def discover_prompts(repo_root: Path, workflow: str) -> list[PromptInfo]:
    found: dict[str, PromptInfo] = {}
    patterns = ("*_pack_*.txt",) if workflow == "generic" else ("*_pack_*.txt", "paste_*.md")
    for root in prompt_roots(repo_root, workflow):
        if not root.is_dir():
            continue
        for pattern in patterns:
            for path in root.rglob(pattern):
                try:
                    info = classify_prompt(path.resolve())
                except ValueError:
                    continue
                if info.workflow == workflow:
                    found.setdefault(path.name.casefold(), info)
    return sorted(found.values(), key=lambda item: (item.pack_date, item.path.name.casefold()))


def workflow_report_dir(report_root: Path, workflow: str, pack_date: str) -> Path:
    return report_root / workflow.upper() / pack_date


def phase_report_glob(phase: str, pack_date: str) -> str:
    order = SEQUENTIAL_PHASES.index(phase) + 1
    return f"{order:02d}_{phase}_*_report_{pack_date}*.md"


def latest_phase_report(report_dir: Path, phase: str, pack_date: str) -> Path | None:
    matches = list(report_dir.glob(phase_report_glob(phase, pack_date)))
    if not matches:
        return None
    return max(matches, key=lambda path: (path.stat().st_mtime_ns, path.name))


def prior_reports(report_dir: Path, phase: str, pack_date: str) -> tuple[Path, ...]:
    required = SEQUENTIAL_PHASES[: SEQUENTIAL_PHASES.index(phase)]
    found: list[Path] = []
    missing: list[str] = []
    for predecessor in required:
        report = latest_phase_report(report_dir, predecessor, pack_date)
        if report is None:
            missing.append(predecessor)
        else:
            found.append(report.resolve())
    if missing:
        raise ValueError(
            f"Phase {phase} is out of order; missing predecessor reports: {', '.join(missing)}"
        )
    return tuple(found)


def choose_prompt(
    explicit: str | None, repo_root: Path, workflow: str, report_root: Path
) -> PromptInfo:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"Prompt file does not exist: {path}")
        info = classify_prompt(path)
        if info.workflow != workflow:
            raise ValueError(f"Prompt belongs to {info.workflow}, not {workflow}: {path}")
        return info

    prompts = discover_prompts(repo_root, workflow)
    if not prompts:
        raise ValueError(f"No generated {workflow} Outlier prompts were found.")
    latest_date = max(info.pack_date for info in prompts)
    latest = [info for info in prompts if info.pack_date == latest_date]
    if workflow == "generic":
        if len(latest) != 1:
            choices = "\n".join(f"  - {info.path}" for info in latest)
            raise ValueError(
                f"Latest date {latest_date} has {len(latest)} generic prompts; pass --prompt:\n{choices}"
            )
        return latest[0]

    by_phase: dict[str | None, PromptInfo] = {}
    for phase in SEQUENTIAL_PHASES:
        candidates = [info for info in latest if info.phase == phase]
        exported = [info for info in candidates if info.path.suffix.casefold() == ".txt"]
        if exported:
            by_phase[phase] = exported[0]
        elif candidates:
            by_phase[phase] = candidates[0]
    report_dir = workflow_report_dir(report_root, workflow, latest_date)
    for phase in SEQUENTIAL_PHASES:
        info = by_phase.get(phase)
        if info is None:
            raise ValueError(f"Sequential prompt set is missing Phase {phase} for {latest_date}.")
        if latest_phase_report(report_dir, phase, latest_date) is None:
            prior_reports(report_dir, phase, latest_date)
            return info
    raise ValueError(f"All sequential phases are already complete for {latest_date}.")


def collision_safe_path(report_dir: Path, filename: str) -> Path:
    base = report_dir / filename
    if not base.exists():
        return base
    for version in range(2, 10_000):
        candidate = base.with_name(f"{base.stem}_v{version}{base.suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError(f"Could not find a free report filename for {base}")


def resolve(info: PromptInfo, agent: str, report_root: Path) -> Resolution:
    if info.workflow == "sequential":
        assert info.phase is not None
        if agent not in PHASE_AGENTS[info.phase]:
            expected = ", ".join(sorted(PHASE_AGENTS[info.phase]))
            raise ValueError(f"Phase {info.phase} must be run by {expected}, not {agent}.")

    report_dir = workflow_report_dir(report_root.resolve(), info.workflow, info.pack_date)
    predecessors: tuple[Path, ...] = ()
    if info.phase is not None:
        predecessors = prior_reports(report_dir, info.phase, info.pack_date)
        order = SEQUENTIAL_PHASES.index(info.phase) + 1
        filename = f"{order:02d}_{info.phase}_{AGENT_LABELS[agent]}_report_{info.pack_date}.md"
    else:
        filename = f"{AGENT_LABELS[agent]}_{info.prompt_kind}_report_{info.pack_date}.md"

    report_path = collision_safe_path(report_dir, filename)
    return Resolution(
        workflow=info.workflow,
        prompt_path=str(info.path.resolve()),
        report_path=str(report_path),
        report_dir=str(report_dir),
        agent=AGENT_LABELS[agent],
        pack_date=info.pack_date,
        prompt_kind=info.prompt_kind,
        phase=info.phase,
        prior_report_paths=tuple(str(path) for path in predecessors),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", required=True, choices=("generic", "sequential"))
    parser.add_argument("--prompt", help="Exact generated prompt path")
    parser.add_argument("--agent", required=True, choices=sorted(AGENT_LABELS))
    parser.add_argument(
        "--report-root",
        default=str(desktop_dir() / "BETTING REPORTS"),
        help="Shared report root (default: redirected Desktop/BETTING REPORTS)",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[3]
    report_root = Path(args.report_root)
    try:
        info = choose_prompt(args.prompt, repo_root, args.workflow, report_root)
        resolution = resolve(info, args.agent, report_root)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    payload = asdict(resolution)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for key, value in payload.items():
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
