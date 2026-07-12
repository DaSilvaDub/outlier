"""Shared scaffolding for the AI research-desk reasoning/research runners.

Holds the provider-agnostic mechanics — request-hash, candidates validation,
game_totals context injection, atomic front-matter write — so per-prompt runners stay thin.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from pathlib import Path

from outlier_scrapers import pack

GAME_TOTALS_NAME = "game_totals.csv"


class RunnerError(Exception):
    """Raised for any recoverable runner failure (-> exit code 1)."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def empty_game_totals_hash() -> str:
    """Stable hash when game_totals.csv is absent."""
    return sha256_bytes(b"")


def load_game_totals(pack_dir: Path) -> tuple[bytes | None, str]:
    """Return (raw bytes or None, sha256). Missing file hashes as empty."""
    path = pack_dir / GAME_TOTALS_NAME
    if not path.exists():
        return None, empty_game_totals_hash()
    raw = path.read_bytes()
    return raw, sha256_bytes(raw)


def has_actionable_game_totals(totals_bytes: bytes | None) -> bool:
    """Return whether the optional totals board contains an actionable row."""
    if not totals_bytes:
        return False
    import io

    try:
        rows = csv.DictReader(io.StringIO(totals_bytes.decode("utf-8-sig")))
        return any(str(row.get("actionable") or "").strip().lower() == "true" for row in rows)
    except (UnicodeDecodeError, csv.Error) as exc:
        raise RunnerError("game_totals.csv is malformed") from exc


def append_totals_block(base: str, totals_bytes: bytes | None) -> str:
    """Append labeled game_totals.csv context when the pack artifact exists."""
    if not totals_bytes:
        return base
    return (
        base
        + "\n\n===== GAME_TOTALS.CSV (projection board) =====\n"
        + totals_bytes.decode("utf-8-sig")
    )


def build_reasoning_data_block(candidates_bytes: bytes, totals_bytes: bytes | None) -> str:
    """Merge candidates.csv and optional game_totals.csv for reasoning passes."""
    base = "candidates.csv:\n" + candidates_bytes.decode("utf-8-sig")
    return append_totals_block(base, totals_bytes)


def compute_request_hash(request_data: dict) -> str:
    """Canonical, order-independent hash of the full request definition."""
    canonical = json.dumps(request_data, sort_keys=True).encode("utf-8")
    return sha256_bytes(canonical)


def extract_yaml_request_hash(content: str) -> str | None:
    """Trivial reader for the ``request_sha256`` key in YAML front matter."""
    if not content.startswith("---\n"):
        return None
    end_idx = content.find("\n---\n", 4)
    if end_idx == -1:
        return None
    for line in content[4:end_idx].splitlines():
        if line.startswith("request_sha256:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    return None


import logging
from datetime import datetime

logger = logging.getLogger(__name__)

def validate_candidates(pack_dir: Path, *, allow_empty: bool = False) -> tuple[bytes, str]:
    """Validate candidates.csv exists and has the canonical header.

    At least one unlocked candidate is required unless an actionable totals
    board explicitly enables the header-only state.
    Also re-applies the lock filter in case events started since pack build."""
    candidates_file = pack_dir / "candidates.csv"
    if not candidates_file.exists():
        raise RunnerError(f"Candidates file {candidates_file} does not exist.")
    
    with open(candidates_file, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != pack.CANDIDATES_HEADER:
            raise RunnerError("candidates.csv header does not match pack.CANDIDATES_HEADER")

    with open(candidates_file, "r", encoding="utf-8") as f:
        dict_reader = csv.DictReader(f)
        rows = list(dict_reader)

    kept, locked = pack.drop_locked_events(rows, now=datetime.now().astimezone())
    if locked:
        logger.warning(
            "Reasoning-time lock filter dropped %d event(s) locked since pack build",
            len(locked),
        )
    
    if not kept and not allow_empty:
        raise RunnerError("candidates.csv has no data rows after dropping locked events.")

    import io
    out_io = io.StringIO()
    writer = csv.DictWriter(out_io, fieldnames=pack.CANDIDATES_HEADER)
    writer.writeheader()
    writer.writerows(kept)
    
    raw_bytes = out_io.getvalue().encode("utf-8-sig")
    return raw_bytes, sha256_bytes(raw_bytes)


def read_required_text(path: Path, label: str) -> str:
    if not path.exists():
        raise RunnerError(f"{label} {path} missing.")
    return path.read_text(encoding="utf-8")


def atomic_write(pack_dir: Path, out_name: str, front_matter: str, body: str) -> None:
    """Write front_matter + body to pack_dir/out_name atomically (tmp + replace)."""
    stem = out_name.rsplit(".", 1)[0]
    fd, tmp_path = tempfile.mkstemp(dir=str(pack_dir), prefix=f"{stem}_tmp_", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(front_matter)
            f.write(body)
        os.replace(tmp_path, pack_dir / out_name)
    except Exception as e:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise RunnerError(f"Failed to write output: {type(e).__name__}") from e
