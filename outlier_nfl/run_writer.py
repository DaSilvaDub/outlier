"""Staged, immutable run bundles for the NFL pipeline (F27).

Every artifact of a run is first written into a private staging directory.
:meth:`RunWriter.commit` then

1. writes ``manifest.json`` (run context, sources, sha256 and size of every
   artifact, and where each one is published) into the staging directory;
2. renames the staging directory to ``runs/<run_id>/`` in one step, so a bundle
   is either complete or absent;
3. copies artifacts to their published names (dated / latest), each atomically;
4. writes the run pointer last, so a pointer never names a run whose published
   files are not all in place.

A run that fails before ``commit`` publishes nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from outlier_nfl.utils import _replace_with_retry, safe_write_json

MANIFEST_NAME = "manifest.json"


def _atomic_write_bytes(dest: Path, data: bytes) -> None:
    """Write ``data`` to ``dest`` through a unique temp sibling and an atomic replace."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{dest.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        tmp.write_bytes(data)
        _replace_with_retry(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RunWriter:
    """Stage a run's artifacts, then commit them as one bundle plus published copies."""

    def __init__(self, nfl_dir: Path | str, run_id: str) -> None:
        self.run_id = run_id
        self.runs_root = Path(nfl_dir) / "runs"
        self.run_dir = self.runs_root / run_id
        self.stage_dir = self.runs_root / f".staging-{run_id}"
        if self.run_dir.exists():
            raise FileExistsError(f"run bundle already exists: {self.run_dir}")
        self.stage_dir.mkdir(parents=True, exist_ok=False)
        self._publish: dict[str, list[Path]] = {}
        self.committed = False

    # -- staging -----------------------------------------------------------
    def staged_path(self, name: str) -> Path:
        """Path inside the staging directory for ``name`` (parents created)."""
        path = self.stage_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def publish(self, name: str, destinations: Iterable[Path]) -> None:
        """Copy staged artifact ``name`` to each destination at commit time."""
        self._publish.setdefault(name, []).extend(Path(d) for d in destinations)

    def withhold_publication(self) -> None:
        """Drop every publish destination staged so far: the run stays bundle-only."""
        self._publish.clear()

    def stage_json(self, name: str, payload: Any, publish: Iterable[Path] = ()) -> None:
        safe_write_json(self.staged_path(name), payload)
        self.publish(name, publish)

    def stage_text(self, name: str, text: str, publish: Iterable[Path] = ()) -> None:
        self.staged_path(name).write_text(text, encoding="utf-8")
        self.publish(name, publish)

    def stage_tree(self, subdir: str, destination: Path) -> None:
        """Publish every file staged under ``subdir`` into ``destination`` (same names)."""
        root = self.stage_dir / subdir
        if not root.is_dir():
            return
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            rel = path.relative_to(root)
            self.publish(f"{subdir}/{rel.as_posix()}", [destination / rel])

    def final_path(self, name: str) -> Path:
        """Where ``name`` lives once committed: its first published copy, else the bundle."""
        dests = self._publish.get(name)
        return dests[0] if dests else self.run_dir / name

    def resolve(self, staged: str | Path) -> str:
        """Map a path inside the staging directory to its final location."""
        path = Path(staged)
        try:
            rel = path.resolve().relative_to(self.stage_dir.resolve())
        except ValueError:
            return str(staged)
        return str(self.final_path(rel.as_posix()))

    # -- commit ------------------------------------------------------------
    def commit(
        self,
        manifest: Mapping[str, Any],
        *,
        pointers: Iterable[Path] = (),
    ) -> dict[str, Any]:
        """Seal the bundle, publish copies, then write ``pointers`` last."""
        if self.committed:
            raise RuntimeError(f"run {self.run_id} already committed")
        artifacts = []
        for path in sorted(p for p in self.stage_dir.rglob("*") if p.is_file()):
            name = path.relative_to(self.stage_dir).as_posix()
            data = path.read_bytes()
            artifacts.append({
                "name": name,
                "sha256": _sha256(data),
                "bytes": len(data),
                "published_to": [str(d) for d in self._publish.get(name, [])],
            })
        body = {**dict(manifest), "run_id": self.run_id, "artifacts": artifacts}
        manifest_bytes = json.dumps(body, indent=2, default=str, ensure_ascii=False).encode("utf-8")
        (self.stage_dir / MANIFEST_NAME).write_bytes(manifest_bytes)
        # Same transient-lock retry as the published copies: on Windows an indexer or
        # antivirus scan briefly holding a staged file makes the folder rename fail.
        _replace_with_retry(self.stage_dir, self.run_dir)
        self.committed = True

        for name, dests in self._publish.items():
            data = (self.run_dir / name).read_bytes()
            for dest in dests:
                _atomic_write_bytes(dest, data)

        manifest_sha = _sha256(manifest_bytes)
        pointer = {
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "manifest_sha256": manifest_sha,
        }
        pointer_bytes = json.dumps(pointer, indent=2).encode("utf-8")
        for dest in pointers:
            _atomic_write_bytes(Path(dest), pointer_bytes)
        return pointer
