"""Staged, immutable run bundles (re-implemented from the NFL design, plan M13).

Artifacts are staged privately, sealed with a sha256 manifest, renamed to
``runs/<run_id>/`` in one step, copied to published names atomically, and the
run pointer is written last.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

MANIFEST_NAME = "manifest.json"


def replace_with_retry(src: Path, dst: Path, retries: int = 5, delay: float = 0.2) -> None:
    """Atomic replace, retrying transient Windows/cloud-sync file locks."""
    for attempt in range(1, retries + 1):
        try:
            src.replace(dst)
            return
        except (PermissionError, FileExistsError):
            if attempt >= retries:
                raise
            time.sleep(delay * (1.5 ** (attempt - 1)))


def atomic_write_bytes(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{dest.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        tmp.write_bytes(data)
        replace_with_retry(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RunWriter:
    def __init__(self, nba_dir: Path | str, run_id: str) -> None:
        self.run_id = run_id
        self.runs_root = Path(nba_dir) / "runs"
        self.run_dir = self.runs_root / run_id
        self.stage_dir = self.runs_root / f".staging-{run_id}"
        if self.run_dir.exists():
            raise FileExistsError(f"run bundle already exists: {self.run_dir}")
        self.stage_dir.mkdir(parents=True, exist_ok=False)
        self._publish: dict[str, list[Path]] = {}
        self.committed = False

    def staged_path(self, name: str) -> Path:
        path = self.stage_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def publish(self, name: str, destinations: Iterable[Path]) -> None:
        self._publish.setdefault(name, []).extend(Path(d) for d in destinations)

    def withhold_publication(self) -> None:
        """Drop every staged publish destination: the run stays bundle-only."""
        self._publish.clear()

    def stage_json(self, name: str, payload: Any, publish: Iterable[Path] = ()) -> None:
        text = json.dumps(payload, indent=2, default=str, ensure_ascii=False)
        self.staged_path(name).write_text(text, encoding="utf-8")
        self.publish(name, publish)

    def stage_text(self, name: str, text: str, publish: Iterable[Path] = ()) -> None:
        self.staged_path(name).write_text(text, encoding="utf-8")
        self.publish(name, publish)

    def commit(
        self, manifest: Mapping[str, Any], *, pointers: Iterable[Path] = ()
    ) -> dict[str, Any]:
        if self.committed:
            raise RuntimeError(f"run {self.run_id} already committed")
        artifacts = []
        for path in sorted(p for p in self.stage_dir.rglob("*") if p.is_file()):
            name = path.relative_to(self.stage_dir).as_posix()
            data = path.read_bytes()
            artifacts.append(
                {
                    "name": name,
                    "sha256": sha256_hex(data),
                    "bytes": len(data),
                    "published_to": [str(d) for d in self._publish.get(name, [])],
                }
            )
        body = {**dict(manifest), "run_id": self.run_id, "artifacts": artifacts}
        manifest_bytes = json.dumps(body, indent=2, default=str, ensure_ascii=False).encode("utf-8")
        (self.stage_dir / MANIFEST_NAME).write_bytes(manifest_bytes)
        replace_with_retry(self.stage_dir, self.run_dir)
        self.committed = True

        for name, dests in self._publish.items():
            if not dests:
                continue
            data = (self.run_dir / name).read_bytes()
            for dest in dests:
                atomic_write_bytes(dest, data)

        pointer = {
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "manifest_sha256": sha256_hex(manifest_bytes),
        }
        pointer_bytes = json.dumps(pointer, indent=2).encode("utf-8")
        for dest in pointers:
            atomic_write_bytes(Path(dest), pointer_bytes)
        return pointer
