"""Shared scaffolding for the AI research-desk reasoning/research runners.

Holds the provider-agnostic mechanics — request-hash, candidates validation,
atomic front-matter write — so the per-prompt runner modules stay thin. Keeps
``reasoning.py`` (Prompt A) untouched; new runners (B, D, E) build on this.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from pathlib import Path

from outlier_scrapers import pack


class RunnerError(Exception):
    """Raised for any recoverable runner failure (-> exit code 1)."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


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


def validate_candidates(pack_dir: Path) -> tuple[bytes, str]:
    """Validate candidates.csv exists, has the canonical header and >=1 row."""
    candidates_file = pack_dir / "candidates.csv"
    if not candidates_file.exists():
        raise RunnerError(f"Candidates file {candidates_file} does not exist.")
    raw_bytes = candidates_file.read_bytes()
    with open(candidates_file, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != pack.CANDIDATES_HEADER:
            raise RunnerError("candidates.csv header does not match pack.CANDIDATES_HEADER")
        if len(list(reader)) == 0:
            raise RunnerError("candidates.csv has no data rows.")
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
