"""Resolve one exported Outlier prompt and a collision-safe shared report path."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

PROMPT_DATE_RE = re.compile(r"_pack_(\d{4}-\d{2}-\d{2})(?:\.[^.]+)?$", re.IGNORECASE)
PATH_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9]+")

AGENT_LABELS = {
    "chatgpt": "CHAT",
    "codex": "CHAT",
    "claude": "CLAUDE",
    "gemini": "GEMINI",
    "grok": "GROK",
    "copilot": "COPILOT",
}


@dataclass(frozen=True)
class Resolution:
    prompt_path: str
    report_path: str
    agent: str
    pack_date: str
    prompt_kind: str


def desktop_dir() -> Path:
    """Return the redirected Windows Desktop used by this workstation."""
    one_drive_desktop = Path.home() / "OneDrive" / "Desktop"
    if one_drive_desktop.is_dir():
        return one_drive_desktop
    return Path.home() / "Desktop"


def default_prompt_roots(repo_root: Path) -> list[Path]:
    return [
        desktop_dir() / "today" / "prompts",
        Path(r"G:\My Drive\today\prompts"),
        repo_root / "packs",
    ]


def pack_date_for(path: Path) -> str:
    match = PROMPT_DATE_RE.search(path.name)
    if match:
        return match.group(1)
    for parent in path.parents:
        if PATH_DATE_RE.fullmatch(parent.name):
            date.fromisoformat(parent.name)
            return parent.name
    raise ValueError(f"Could not determine pack date from prompt path: {path}")


def prompt_kind_for(path: Path, pack_date: str) -> str:
    stem = path.stem
    stem = re.sub(r"^\d+_", "", stem)
    stem = re.sub(rf"_pack_{re.escape(pack_date)}$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"^paste_", "", stem, flags=re.IGNORECASE)
    token = SAFE_TOKEN_RE.sub("_", stem).strip("_")
    if not token:
        raise ValueError(f"Could not determine prompt kind from: {path.name}")
    return token


def discover_prompts(repo_root: Path) -> list[Path]:
    found: dict[str, Path] = {}
    for root in default_prompt_roots(repo_root):
        if not root.is_dir():
            continue
        patterns = ("*_pack_*.txt", "paste_*.md")
        for pattern in patterns:
            for path in root.rglob(pattern):
                try:
                    pack_date_for(path)
                except ValueError:
                    continue
                found.setdefault(path.name.casefold(), path.resolve())
    return sorted(found.values(), key=lambda item: (pack_date_for(item), item.name.casefold()))


def choose_prompt(explicit: str | None, repo_root: Path) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"Prompt file does not exist: {path}")
        if path.suffix.casefold() not in {".txt", ".md"}:
            raise ValueError(f"Prompt must be a .txt or .md file: {path}")
        if path.suffix.casefold() == ".txt" and not PROMPT_DATE_RE.search(path.name):
            raise ValueError(f"Exported .txt prompt must end with _pack_YYYY-MM-DD: {path}")
        if path.suffix.casefold() == ".md" and not path.name.casefold().startswith("paste_"):
            raise ValueError(f"Pack prompt Markdown must be named paste_*.md: {path}")
        pack_date_for(path)
        return path

    prompts = discover_prompts(repo_root)
    if not prompts:
        raise ValueError("No generated Outlier prompt files were found in the supported locations.")
    latest_date = max(pack_date_for(path) for path in prompts)
    latest = [path for path in prompts if pack_date_for(path) == latest_date]
    if len(latest) != 1:
        choices = "\n".join(f"  - {path}" for path in latest)
        raise ValueError(
            f"Latest pack date {latest_date} has {len(latest)} prompt files; pass --prompt with one exact path:\n{choices}"
        )
    return latest[0]


def collision_safe_path(report_dir: Path, filename: str) -> Path:
    base = report_dir / filename
    if not base.exists():
        return base
    for version in range(2, 10_000):
        candidate = base.with_name(f"{base.stem}_v{version}{base.suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError(f"Could not find a free report filename for {base}")


def resolve(prompt: Path, agent: str, report_dir: Path) -> Resolution:
    pack_date = pack_date_for(prompt)
    prompt_kind = prompt_kind_for(prompt, pack_date)
    agent_label = AGENT_LABELS[agent]
    filename = f"{agent_label}_{prompt_kind}_report_{pack_date}.md"
    report_path = collision_safe_path(report_dir.resolve(), filename)
    return Resolution(
        prompt_path=str(prompt.resolve()),
        report_path=str(report_path),
        agent=agent_label,
        pack_date=pack_date,
        prompt_kind=prompt_kind,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", help="Exact exported .txt or paste_*.md prompt path")
    parser.add_argument("--agent", required=True, choices=sorted(AGENT_LABELS))
    parser.add_argument(
        "--report-dir",
        default=str(desktop_dir() / "BETTING REPORTS"),
        help="Shared report directory (default: redirected Desktop/BETTING REPORTS)",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[4]
    try:
        prompt = choose_prompt(args.prompt, repo_root)
        resolution = resolve(prompt, args.agent, Path(args.report_dir))
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(asdict(resolution), indent=2))
    else:
        for key, value in asdict(resolution).items():
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
