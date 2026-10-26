"""Publish the committed calibration weights as the first locked versions.

    python -m scripts.restore_committed_calibration [--ref <commit>] [--calibration-dir DIR]

Reads ``calibration/blend_weights.json`` and ``calibration/stake_calibration.json``
from git at ``--ref`` (default ``HEAD``) with ``git show``, checks each blob is the
audited committed one (blend ``3e313d7``, stake ``c6a5b6c``), and saves each as a
write-once version behind a pointer (see ``outlier_scrapers.calibration_store``).
The recorded ``source`` is ``git:<full blob hash>``. The live ``*.json`` files and
any other version are never written. Running it again with the same blobs changes
nothing. A pointer that already names a different version is left alone and the
script exits 1.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess  # nosec B404 - runs only git with fixed arguments, never shell input
import sys

from outlier_scrapers import calibration_store, paths

COMMITTED_BLOBS = {
    "blend_weights.json": "3e313d7",
    "stake_calibration.json": "c6a5b6c",
}
RUN_ID = "restore-committed-weights"


class RestoreError(RuntimeError):
    pass


def _git(repo: Path, *args: str) -> bytes:
    # Fixed git subcommands; ``args`` are the literal rev-parse/show arguments built above.
    done = subprocess.run(  # nosec B603 B607
        ["git", "-C", str(repo), *args], capture_output=True, check=False
    )
    if done.returncode != 0:
        raise RestoreError(f"git {' '.join(args)} failed: {done.stderr.decode(errors='replace').strip()}")
    return done.stdout


def restore(repo: Path, ref: str, calibration_dir: Path) -> list[str]:
    """Publish the committed versions; returns one status line per file."""
    lines: list[str] = []
    for name, expected in COMMITTED_BLOBS.items():
        spec = f"{ref}:calibration/{name}"
        blob = _git(repo, "rev-parse", spec).decode().strip()
        if not blob.startswith(expected):
            raise RestoreError(f"{spec} is blob {blob[:7]}, expected the committed {expected}")
        artifact = json.loads(_git(repo, "show", spec).decode("utf-8"))
        live = Path(calibration_dir) / name
        digest = calibration_store.sha256_bytes(calibration_store._canonical_bytes(artifact))
        pointer = calibration_store.read_pointer(live)
        if pointer is not None:
            if pointer["sha256"] == digest:
                lines.append(f"{name}: already published ({digest[:12]}), unchanged")
                continue
            raise RestoreError(
                f"{name}: pointer already names {str(pointer['sha256'])[:12]}, not the committed "
                f"version {digest[:12]}; not repointing"
            )
        version = calibration_store.publish_version(
            artifact, live, run_id=RUN_ID, source=f"git:{blob}"
        )
        lines.append(f"{name}: published {version.name} from git blob {blob[:7]}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--calibration-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    target = args.calibration_dir or calibration_store.calibration_dir()
    try:
        for line in restore(paths.PROJECT_ROOT, args.ref, target):
            print(line)
    except (RestoreError, calibration_store.CalibrationWriteRefused,
            calibration_store.CalibrationVersionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
