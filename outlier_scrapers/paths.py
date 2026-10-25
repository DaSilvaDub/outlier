from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"

FEEDBACK_DB_NAME = "feedback.sqlite3"
MIGRATE_LEDGER_COMMAND = "python -m outlier_scrapers.feedback migrate-ledger"


class LedgerMigrationRequired(RuntimeError):
    """The default ledger is missing locally while a pre-migration ledger exists.

    A RuntimeError on purpose: pipeline steps catch OSError/ValueError/sqlite3.Error
    as soft failures, and this must stop the run instead of being logged and skipped.
    """


def local_calibration_dir() -> Path | None:
    """``%LOCALAPPDATA%\\outlier\\calibration``, or None where LOCALAPPDATA is unset.

    The canonical checkout sits behind a cloud-sync filter that truncates large
    SQLite writes (the ledger was corrupted four times). AppData\\Local is per-user,
    so every worktree still shares one ledger, and it is never synced. Non-Windows
    hosts (CI, cloud sandboxes) have no LOCALAPPDATA and keep the old fallback.
    """
    base = os.environ.get("LOCALAPPDATA", "").strip()
    if not base:
        return None
    return Path(base) / "outlier" / "calibration"


def main_checkout_root() -> Path:
    """The main checkout this one belongs to, found from git, never hardcoded.

    In a linked worktree ``.git`` is a file naming ``<main>/.git/worktrees/<name>``;
    the main checkout is three levels up from that. In a normal checkout, or when
    nothing can be read, it is ``PROJECT_ROOT`` itself. Used only to *find* the
    pre-migration ledger; no code writes into it.
    """
    dot_git = PROJECT_ROOT / ".git"
    if dot_git.is_file():
        try:
            text = dot_git.read_text(encoding="utf-8").strip()
        except OSError:
            return PROJECT_ROOT
        if text.startswith("gitdir:"):
            gitdir = Path(text.split(":", 1)[1].strip())
            if not gitdir.is_absolute():
                gitdir = (PROJECT_ROOT / gitdir).resolve()
            if gitdir.parent.name == "worktrees":
                return gitdir.parent.parent.parent
    return PROJECT_ROOT


def _repo_calibration_dir() -> Path:
    """The calibration dir: ``OUTLIER_CALIBRATION_DIR`` or this checkout's own.

    There is no hardcoded path to another checkout. A run from any worktree
    reads and writes its own ``calibration/`` (or the directory it was told to).
    """
    override = os.environ.get("OUTLIER_CALIBRATION_DIR", "").strip()
    if override:
        return Path(override)
    return PROJECT_ROOT / "calibration"


def _resolve_calibration_path(env_var: str, relative: Path) -> Path:
    """Env override, else this checkout's calibration dir."""
    override = os.environ.get(env_var, "").strip()
    if override:
        return Path(override)
    return _repo_calibration_dir() / relative


def legacy_feedback_db_path() -> Path:
    """Where the ledger lived before it moved to local data (in-repo calibration/).

    That is the main checkout's calibration dir (found through git, so a worktree
    still sees the one real pre-migration ledger), or ``OUTLIER_CALIBRATION_DIR``.
    """
    override = os.environ.get("OUTLIER_CALIBRATION_DIR", "").strip()
    if override:
        return Path(override) / FEEDBACK_DB_NAME
    return main_checkout_root() / "calibration" / FEEDBACK_DB_NAME


def resolve_feedback_db_path() -> Path:
    """The single authoritative feedback ledger (decisions, snapshots, settlements).

    Resolution order: ``OUTLIER_FEEDBACK_DB``; :func:`local_calibration_dir` on
    Windows (shared by every worktree, outside the cloud-synced checkout); the
    canonical checkout's ledger when its directory exists; ``PROJECT_ROOT`` for CI
    and cloud sandboxes. SQLite's -wal/-shm sidecars live next to whichever wins.
    """
    override = os.environ.get("OUTLIER_FEEDBACK_DB", "").strip()
    if override:
        return Path(override)
    local_dir = local_calibration_dir()
    if local_dir is not None:
        return local_dir / FEEDBACK_DB_NAME
    return legacy_feedback_db_path()


def archived_ledgers(directory: Path) -> list[Path]:
    """Originals the migration renamed aside, oldest first (names sort by date)."""
    return sorted(directory.glob("feedback.migrated-*.sqlite3"))


def ledger_file_present(db_path: Path) -> bool:
    """True when ``db_path`` holds data; a bare ``sqlite3.connect`` leaves 0 bytes."""
    return db_path.is_file() and db_path.stat().st_size > 0


def require_migrated_ledger(db_path: Path) -> None:
    """Refuse to create an empty ledger at the local default while an old one exists.

    Only the local-data default is guarded: an explicit path (``--db``,
    ``OUTLIER_FEEDBACK_DB``, a test's tmp_path) is the caller's choice. A ledger
    archived by the migration also blocks, so a lost local copy fails loudly
    instead of silently restarting the ledger from zero. A 0-byte default counts
    as missing: a raw ``sqlite3.connect`` elsewhere must not disarm the guard.
    """
    if os.environ.get("OUTLIER_FEEDBACK_DB", "").strip():
        return
    local_dir = local_calibration_dir()
    if local_dir is None:
        return
    default = local_dir / FEEDBACK_DB_NAME
    if os.path.normcase(os.path.abspath(db_path)) != os.path.normcase(os.path.abspath(default)):
        return
    if ledger_file_present(default):
        return
    legacy = legacy_feedback_db_path()
    if legacy.exists():
        raise LedgerMigrationRequired(
            f"The feedback ledger now lives at {default}, which does not exist yet, but "
            f"the old ledger is still at {legacy}. Stop any running pipeline, then run: "
            f"{MIGRATE_LEDGER_COMMAND}"
        )
    archived = archived_ledgers(legacy.parent)
    if archived:
        staging = default.with_name(default.name + ".migrating")
        if staging.exists():
            raise LedgerMigrationRequired(
                f"A ledger migration was interrupted: the verified copy is at {staging}. "
                f"Run {MIGRATE_LEDGER_COMMAND} again to finish it."
            )
        raise LedgerMigrationRequired(
            f"The feedback ledger at {default} is missing, but it was migrated earlier "
            f"(archived copy: {archived[-1]}). Restore the local ledger instead of "
            "starting an empty one; set OUTLIER_FEEDBACK_DB to override deliberately."
        )


def resolve_feedback_report_dir() -> Path:
    """Where feedback reports land: the in-repo calibration dir, not local data.

    Reports are small, regenerated on every run and meant to be browsed in the
    checkout, so they stay put; only the large, transactional ledger moved.
    """
    return _resolve_calibration_path("OUTLIER_FEEDBACK_REPORT_DIR", Path("reports") / "latest")


FEEDBACK_DB_PATH = resolve_feedback_db_path()
FEEDBACK_REPORT_DIR = resolve_feedback_report_dir()


@dataclass(frozen=True)
class LeaguePaths:
    league: str
    root: Path
    raw: Path
    normalized: Path
    reports: Path

    def ensure(self) -> "LeaguePaths":
        for path in (self.raw, self.normalized, self.reports):
            path.mkdir(parents=True, exist_ok=True)
        return self

    def timestamped(self, directory: Path, stem: str, suffix: str = ".json") -> Path:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        return directory / f"{self.league.lower()}_{stem}_{timestamp}{suffix}"

    @property
    def cards(self) -> Path:
        return self.root / "cards"

    def _latest(self, directory: Path, stem: str, suffix: str = ".json") -> Path:
        return directory / f"{self.league.lower()}_{stem}_latest{suffix}"

    def games_normalized_latest(self) -> Path:
        return self._latest(self.normalized, "games")

    def props_normalized_latest(self) -> Path:
        return self._latest(self.normalized, "props")

    def games_enrichment_latest(self) -> Path:
        return self._latest(self.normalized, "games_enrichment")

    def games_line_movement_latest(self) -> Path:
        return self._latest(self.normalized, "games_line_movement")

    def probable_pitchers_latest(self) -> Path:
        return self._latest(self.normalized, "probable_pitchers")

    def projections_latest(self) -> Path:
        return self._latest(self.normalized, "projections")

    def line_movement_latest(self) -> Path:
        return self._latest(self.normalized, "line_movement")

    def cards_latest(self) -> Path:
        return self._latest(self.cards, "cards")

    def games_cards_latest(self) -> Path:
        return self._latest(self.cards, "games_cards")


def league_paths(league: str) -> LeaguePaths:
    token = league.strip().upper()
    root = DATA_DIR / token
    return LeaguePaths(
        league=token,
        root=root,
        raw=root / "raw",
        normalized=root / "normalized",
        reports=root / "reports",
    )


def session_dir() -> Path:
    return CONFIG_DIR / ".outlier_session"


def storage_state_file() -> Path:
    return session_dir() / "storage_state.json"


def legacy_session_file() -> Path:
    return CONFIG_DIR / "outlier_session.json"


def bearer_token_file() -> Path:
    return session_dir() / "api_bearer_token.txt"


def session_metadata_file() -> Path:
    return session_dir() / "session_metadata.json"


def api_request_headers_file() -> Path:
    return session_dir() / "api_request_headers.json"


def otp_status_file() -> Path:
    return session_dir() / "otp_status.json"


def otp_code_file() -> Path:
    return session_dir() / "otp_code.txt"


def storage_state_candidates() -> list[Path]:
    return [storage_state_file(), legacy_session_file()]
