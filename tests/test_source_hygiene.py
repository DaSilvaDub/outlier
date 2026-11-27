"""Every tracked Python source must parse when read as plain UTF-8.

A UTF-8 BOM (U+FEFF) is invisible to CPython's import machinery, to ruff and to
mypy, but any tool that reads a module with ``encoding="utf-8"`` and hands the
text to :func:`ast.parse` fails on it with "invalid non-printable character
U+FEFF". ``outlier_nfl/injuries.py`` carried one, so the whole tree is checked.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _tracked_python_files() -> list[Path] | None:
    """Tracked ``*.py`` paths, or ``None`` when this is not a usable git checkout."""
    try:
        listing = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "-z", "--", "*.py"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return [REPO_ROOT / name for name in listing.split("\0") if name]


def test_tracked_sources_parse_as_plain_utf8():
    files = _tracked_python_files()
    if files is None:
        pytest.skip("not a git checkout; cannot enumerate tracked sources")
    assert files, "git ls-files reported no tracked Python sources"

    failures = []
    for path in files:
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            failures.append(f"{path.relative_to(REPO_ROOT)}: {exc}")
    assert not failures, "sources that do not parse as plain UTF-8: " + "; ".join(failures)
