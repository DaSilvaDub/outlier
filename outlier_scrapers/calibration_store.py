"""Write-once, versioned calibration artifacts behind a pointer.

Why this exists: ``calibration/blend_weights.json`` and ``stake_calibration.json``
were rewritten in place by whichever process ran last (a daily refit, a test, a
branch checkout in the main checkout), and the previous contents were gone. Here a
refit never touches the live file:

* each fit is saved once as ``<dir>/versions/<name>/<name>.<sha12>.json``
  (exclusive create; the same bytes may be saved again, different bytes under the
  same name are refused);
* ``<dir>/<name>.pointer.json`` names the version readers should load, with its
  full sha256; every pointer change is also appended to ``pointer_history.jsonl``;
* a pointer whose version file is missing or does not match its hash is an error,
  never a silent fall back to older data;
* with no pointer yet, readers load the legacy ``<dir>/<name>.json`` unchanged, so
  nothing moves until a first version is published.

Writes through a symlink or Windows junction are refused, and so is any write
under a directory listed in ``PROTECTED_DIRS_ENV`` (``os.pathsep``-separated). The
test suite lists this checkout's real ``calibration/`` and the main checkout's, so
a test can never reach either: the write is refused before it happens.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from outlier_scrapers import paths

CALIBRATION_DIR_ENV = "OUTLIER_CALIBRATION_DIR"
PROTECTED_DIRS_ENV = "OUTLIER_PROTECTED_CALIBRATION_DIRS"
POINTER_HISTORY_NAME = "pointer_history.jsonl"
POINTER_SCHEMA_VERSION = 1


class CalibrationWriteRefused(RuntimeError):
    """A calibration write was refused before anything was written."""


class CalibrationVersionError(RuntimeError):
    """A pointer names a version that is missing, altered, or unknown."""


def calibration_dir() -> Path:
    """The calibration directory for this process.

    ``OUTLIER_CALIBRATION_DIR`` when set, else this checkout's ``calibration/``.
    There is no hardcoded path to any other checkout: a worktree reads and writes
    its own copy, so no run can reach into the main checkout's files.
    """
    override = os.environ.get(CALIBRATION_DIR_ENV, "").strip()
    if override:
        return Path(override)
    return paths.PROJECT_ROOT / "calibration"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    # Windows junctions are not symlinks to os.path.islink, but is_junction exists on 3.12+.
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def refuse_links(path: Path) -> None:
    """Refuse a write when the target or any existing parent is a link."""
    candidate = Path(path)
    for part in (candidate, *candidate.parents):
        if part.exists() or part.is_symlink():
            if _is_link(part):
                raise CalibrationWriteRefused(
                    f"Refusing to write {path}: {part} is a symlink or junction."
                )


def _protected_dirs() -> list[Path]:
    raw = os.environ.get(PROTECTED_DIRS_ENV, "")
    return [Path(p).resolve() for p in raw.split(os.pathsep) if p.strip()]


def refuse_forbidden_write(path: Path) -> None:
    """Raise before writing when ``path`` is under a protected directory (tests)."""
    target = Path(path).resolve()
    for protected in _protected_dirs():
        try:
            target.relative_to(protected)
        except ValueError:
            continue
        raise CalibrationWriteRefused(
            f"Refusing to write {path}: it is inside the protected calibration "
            f"directory {protected} ({PROTECTED_DIRS_ENV}). Point the writer at a "
            f"temporary directory (see {CALIBRATION_DIR_ENV})."
        )


def _guard_write(path: Path) -> None:
    refuse_forbidden_write(path)
    refuse_links(path)


def _canonical_bytes(artifact: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(artifact), indent=2, sort_keys=True) + "\n").encode("utf-8")


def pointer_path(live_path: Path) -> Path:
    live_path = Path(live_path)
    return live_path.with_name(f"{live_path.stem}.pointer.json")


def versions_dir(live_path: Path) -> Path:
    live_path = Path(live_path)
    return live_path.parent / "versions" / live_path.stem


def read_pointer(live_path: Path) -> dict[str, Any] | None:
    pointer = pointer_path(live_path)
    if not pointer.exists():
        return None
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationVersionError(f"Unreadable calibration pointer {pointer}: {exc}") from exc
    if not isinstance(payload, dict) or "version_file" not in payload or "sha256" not in payload:
        raise CalibrationVersionError(f"Malformed calibration pointer {pointer}")
    return payload


def resolve_artifact_path(live_path: Path) -> Path:
    """The file a reader should load for ``live_path``.

    The pointed-to version when a pointer exists (verified against its sha256),
    else ``live_path`` itself (legacy, nothing published yet).
    """
    live_path = Path(live_path)
    pointer = read_pointer(live_path)
    if pointer is None:
        return live_path
    version = live_path.parent / str(pointer["version_file"])
    if not version.is_file():
        raise CalibrationVersionError(
            f"Calibration pointer {pointer_path(live_path)} names {version}, which is missing."
        )
    actual = sha256_file(version)
    if actual != pointer["sha256"]:
        raise CalibrationVersionError(
            f"Calibration version {version} does not match its pointer hash "
            f"(pointer {pointer['sha256'][:12]}, file {actual[:12]})."
        )
    return version


def current_sha256(live_path: Path) -> str | None:
    """Content hash of the artifact a reader would load, or None when there is none."""
    resolved = resolve_artifact_path(Path(live_path))
    return sha256_file(resolved) if resolved.is_file() else None


def known_version_hashes(live_path: Path) -> set[str]:
    """sha256 of every saved version of ``live_path`` (file names embed the first 12)."""
    directory = versions_dir(live_path)
    if not directory.is_dir():
        return set()
    return {sha256_file(p) for p in directory.glob("*.json")}


def _append_history(live_path: Path, entry: Mapping[str, Any]) -> None:
    history = live_path.parent / POINTER_HISTORY_NAME
    _guard_write(history)
    with open(history, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(entry), sort_keys=True) + "\n")


def publish_version(
    artifact: Mapping[str, Any],
    live_path: Path,
    *,
    replace_file=os.replace,
    run_id: str | None = None,
    source: str | None = None,
) -> Path:
    """Save ``artifact`` as a new write-once version and point readers at it.

    Returns the version file. ``live_path`` (the legacy file) is never written.
    ``replace_file`` is the atomic-replace used for the pointer, injectable so a
    caller can retry transient Windows errors.
    """
    live_path = Path(live_path)
    data = _canonical_bytes(artifact)
    digest = sha256_bytes(data)
    directory = versions_dir(live_path)
    version = directory / f"{live_path.stem}.{digest[:12]}.json"
    _guard_write(version)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        with open(version, "xb") as handle:
            handle.write(data)
    except FileExistsError:
        if sha256_file(version) != digest:
            raise CalibrationWriteRefused(
                f"{version} already exists with different content; versions are write-once."
            ) from None
    relative = version.relative_to(live_path.parent).as_posix()
    pointer = pointer_path(live_path)
    _guard_write(pointer)
    body = {
        "schema_version": POINTER_SCHEMA_VERSION,
        "version_file": relative,
        "sha256": digest,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "source": source,
    }
    temporary = pointer.with_name(pointer.name + ".tmp")
    temporary.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    replace_file(temporary, pointer)
    _append_history(live_path, body)
    return version
