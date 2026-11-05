"""Retire the audited Gemini early-look entrypoints, preserving immutable originals.

This is deliberately an exact allowlist, not a recursive skill-directory cleanup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

ENTRYPOINTS = (
    "run_slate_simulation_engine.py",
    "run_next_day_slate_simulations.py",
    "run_next_day_slate_20261031.py",
    "run_next_day_slate_20261101.py",
    "run_next_day_slate_20261102.py",
    "run_next_day_slate_20261104.py",
    "run_next_day_slate_20261105.py",
    "generate_next_day_slate_master_report.py",
    "generate_next_day_slate_master_report_20261101.py",
    "generate_next_day_slate_master_report_20261102.py",
    "generate_next_day_slate_master_report_20261104.py",
    "generate_next_day_slate_master_report_20261105.py",
    "export_next_day_deliverables_20261105.py",
)
TOMBSTONE = b'''"""RETIRED: audited early-look betting source. NO BET."""
raise SystemExit("RETIRED / NO BET: this early-look generator used unverified prices or fixed betting metrics. Publication is disabled.")
'''


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def retire(source: Path, backup: Path) -> dict:
    source = source.resolve(strict=True)
    backup = backup.resolve()
    if not source.is_dir() or source == backup or source in backup.parents or backup in source.parents:
        raise ValueError("Backup must be outside the active scripts tree")
    paths = [source / name for name in ENTRYPOINTS]
    for path in paths:
        if path.is_symlink() or not path.is_file() or path.resolve().parent != source:
            raise ValueError(f"Unsafe or missing allowlisted file: {path}")
    manifest_path = backup / "retirement_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["source"] != str(source) or manifest["entrypoints"] != list(ENTRYPOINTS):
            raise ValueError("Existing manifest belongs to a different retirement")
        for row in manifest["files"]:
            if digest((backup / row["name"]).read_bytes()) != row["original_sha256"]:
                raise ValueError(f"Backup hash mismatch: {row['name']}")
        for path, row in zip(paths, manifest["files"], strict=True):
            current = path.read_bytes()
            if current != TOMBSTONE and digest(current) != row["original_sha256"]:
                raise ValueError(f"Active source changed since backup: {path}")
    else:
        originals = [path.read_bytes() for path in paths]
        if any(payload == TOMBSTONE for payload in originals):
            raise ValueError("Already retired file has no provenance manifest")
        backup.mkdir(parents=True, exist_ok=True)
        rows = []
        for path, payload in zip(paths, originals, strict=True):
            saved = backup / path.name
            if saved.exists():
                if saved.read_bytes() != payload:
                    raise ValueError(f"Refusing to overwrite different backup: {saved}")
            else:
                with saved.open("xb") as stream:
                    stream.write(payload)
            if digest(saved.read_bytes()) != digest(payload):
                raise ValueError(f"Backup verification failed: {saved}")
            rows.append({"name": path.name, "original_path": str(path),
                         "backup_path": str(saved), "original_sha256": digest(payload),
                         "tombstone_sha256": digest(TOMBSTONE)})
        manifest = {"status": "RETIRED / NO BET", "source": str(source),
                    "entrypoints": list(ENTRYPOINTS), "files": rows}
        with manifest_path.open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2)
            stream.write("\n")
    # Backups and their manifest are verified before replacing any entrypoint.
    for path in paths:
        if path.read_bytes() != TOMBSTONE:
            temporary = path.with_suffix(".py.retiring")
            with temporary.open("xb") as stream:
                stream.write(TOMBSTONE)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scripts-root", type=Path, required=True)
    parser.add_argument("--backup-root", type=Path, required=True)
    args = parser.parse_args()
    manifest = retire(args.scripts_root, args.backup_root)
    print(f"RETIRED / NO BET: {len(manifest['files'])} entrypoints; originals: {args.backup_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
