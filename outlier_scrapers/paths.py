from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"

# The canonical checkout's calibration directory. calibration/*.sqlite3 is
# gitignored, so a PROJECT_ROOT-relative ledger would give every worktree its own
# empty copy; local runs from any worktree share this one instead.
CANONICAL_CALIBRATION_DIR = Path(r"C:\Users\dasil\Dev\GitHub\outlier\calibration")


def _resolve_calibration_path(env_var: str, relative: Path) -> Path:
    """Env override, else the canonical checkout when present, else this checkout."""
    override = os.environ.get(env_var, "").strip()
    if override:
        return Path(override)
    if CANONICAL_CALIBRATION_DIR.is_dir():
        return CANONICAL_CALIBRATION_DIR / relative
    return PROJECT_ROOT / "calibration" / relative


def resolve_feedback_db_path() -> Path:
    """The single authoritative feedback ledger (decisions, snapshots, settlements).

    Resolution order: ``OUTLIER_FEEDBACK_DB``; the canonical checkout's ledger when
    its directory exists (local Windows runs, any worktree); ``PROJECT_ROOT`` for
    CI and cloud sandboxes where the canonical checkout does not exist.
    """
    return _resolve_calibration_path("OUTLIER_FEEDBACK_DB", Path("feedback.sqlite3"))


def resolve_feedback_report_dir() -> Path:
    """Where feedback reports land; resolved like :func:`resolve_feedback_db_path`."""
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
