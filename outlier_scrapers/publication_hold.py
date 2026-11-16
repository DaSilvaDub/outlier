"""Publication hold gate: the Python twin of ``Assert-PublicationHold`` (scripts/publication_guards.ps1).

A hold file is JSON with a boolean ``active`` key. Any active hold blocks external
(Drive) distribution; it never blocks local pack generation or Desktop staging.
Malformed or unreadable hold files fail closed by raising ``PublicationHoldError``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

REPO_HOLD_RELPATH = Path("docs") / "remediation" / "publication_hold.json"
QUARANTINE_HOLD_PATH = Path(r"C:\Users\dasil\outlier-quarantine\2026-11-05\publication_hold.json")
# The hold file is operational state kept untracked in the canonical checkout, so a run
# from a worktree or clone must still consult it there.
CANONICAL_HOLD_PATH = Path(r"C:\Users\dasil\Dev\GitHub\outlier") / REPO_HOLD_RELPATH
DEFAULT_EXTRA_PATHS = (CANONICAL_HOLD_PATH, QUARANTINE_HOLD_PATH)


class PublicationHoldError(RuntimeError):
    """A hold file exists but cannot be interpreted; callers must treat it as held."""


def hold_paths(repo_root: Path, extra_paths: Iterable[Path] = DEFAULT_EXTRA_PATHS) -> list[Path]:
    """Hold files to consult, repo file first, duplicates removed."""
    paths = [Path(repo_root) / REPO_HOLD_RELPATH]
    for extra in map(Path, extra_paths):
        if extra not in paths:
            paths.append(extra)
    return paths


def _read_active_flag(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PublicationHoldError(f"Unreadable publication hold: {path}: {exc}") from exc
    active = payload.get("active") if isinstance(payload, dict) else None
    if not isinstance(active, bool):
        raise PublicationHoldError(f"Invalid publication hold (active must be bool): {path}")
    return active


def publication_hold_active(
    repo_root: Path, extra_paths: Iterable[Path] = DEFAULT_EXTRA_PATHS
) -> bool:
    """True when any existing hold file says ``"active": true``."""
    return any(
        _read_active_flag(path) for path in hold_paths(repo_root, extra_paths) if path.is_file()
    )
