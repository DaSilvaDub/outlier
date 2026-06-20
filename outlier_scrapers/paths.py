from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"


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
