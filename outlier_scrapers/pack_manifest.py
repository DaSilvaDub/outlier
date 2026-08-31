"""Pack run manifest v2: per-artifact identity plus the legacy run envelope."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

logger = logging.getLogger(__name__)

MANIFEST_SCHEMA_VERSION = "2.0"

# Relative pack paths the daily job is willing to attest. Missing files are
# omitted rather than recorded as errors — the pack writer decides what exists.
KNOWN_ARTIFACTS: tuple[tuple[str, str], ...] = (
    ("candidates.csv", "pack"),
    ("briefing.md", "pack"),
    ("game_totals.csv", "pack"),
    ("team_totals.csv", "pack"),
    ("portfolio_risk.json", "pack"),
    ("decisions.csv", "pack"),
    ("projections.jsonl", "pack"),
    ("candidate_coverage.json", "pack"),
    ("reasoning_status.json", "desk"),
    ("manual_betting_report.md", "desk"),
    ("chatgpt_a.md", "desk"),
    ("gemini_b.md", "desk"),
    ("chatgpt_c.md", "desk"),
    ("claude_d.md", "desk"),
    ("claude_e.md", "desk"),
    ("verdicts/desk_snapshot.json", "desk"),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_record(
    path: Path,
    *,
    producer: str,
    status: str = "ok",
    pack_dir: Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    relative = path.name
    if pack_dir is not None:
        try:
            relative = path.relative_to(pack_dir).as_posix()
        except ValueError:
            relative = path.name
    stat = path.stat()
    return {
        "path": relative,
        "sha256": sha256_file(path),
        "bytes": stat.st_size,
        "producer": producer,
        "status": status,
        "generated_at": generated_at
        or datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    }


def collect_artifacts(
    pack_dir: Path,
    known: Iterable[tuple[str, str]] = KNOWN_ARTIFACTS,
    *,
    include_desk: bool = True,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for relative, producer in known:
        if producer == "desk" and not include_desk:
            continue
        path = pack_dir / relative
        if path.is_file():
            records.append(artifact_record(path, producer=producer, pack_dir=pack_dir))
    return records


def build_manifest_v2(
    pack_dir: Path,
    *,
    run_id: str,
    timestamp: str,
    leagues: list[str],
    profile: str,
    overall: str,
    pack_rows: int | None,
    extra: Mapping[str, Any] | None = None,
    include_desk: bool | None = None,
) -> dict[str, Any]:
    attest_desk = include_desk if include_desk is not None else pack_rows != 0
    payload: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "run_id": run_id,
        "timestamp": timestamp,
        "leagues": list(leagues),
        "profile": profile,
        "overall": overall,
        "pack_rows": pack_rows,
        "status_file": str(pack_dir / "reasoning_status.json")
        if attest_desk and (pack_dir / "reasoning_status.json").exists()
        else None,
        "artifacts": collect_artifacts(pack_dir, include_desk=attest_desk),
    }
    if extra:
        for key, value in extra.items():
            payload[key] = value
    return payload


def write_manifest(pack_dir: Path, data: Mapping[str, Any]) -> None:
    pack_dir.mkdir(parents=True, exist_ok=True)
    mpath = pack_dir / "manifest.json"
    tmp = mpath.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(dict(data), indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, mpath)
    logger.info("Wrote %s", mpath)
