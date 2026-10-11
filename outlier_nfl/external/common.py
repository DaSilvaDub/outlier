"""Shared download client for external NFL data adapters (nflverse release assets)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import time
from typing import Any

from outlier_nfl.utils import atomic_write_bytes
from outlier_nfl.tape_nflverse import NFLVERSE_RELEASES, fetch_bytes, parse_csv_bytes

__all__ = ["Client", "NFLVERSE_RELEASES", "num"]


class Client:
    """Fetches CSV / gzipped-CSV assets with a simple on-disk TTL cache.

    Cache files are keyed by a hash of the URL under ``cache_dir``; entries
    younger than ``ttl`` seconds are reused, older ones are re-downloaded.
    ``ttl=0`` disables reuse.
    """

    def __init__(self, cache_dir: str | os.PathLike[str] = "cache/external", ttl: int = 86400):
        self.cache_dir = Path(cache_dir).resolve()
        self.ttl = ttl
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def __repr__(self) -> str:
        return f"Client(cache_dir={self.cache_dir!s}, ttl={self.ttl})"

    def _cache_path(self, url: str) -> Path:
        return self.cache_dir / (hashlib.sha256(url.encode("utf-8")).hexdigest()[:32] + ".bin")

    def fetch_csv(self, url: str, timeout: float = 120.0) -> list[dict[str, str]]:
        path = self._cache_path(url)
        if self.ttl > 0 and path.exists() and time.time() - path.stat().st_mtime < self.ttl:
            return parse_csv_bytes(path.read_bytes())
        data = fetch_bytes(url, timeout)
        atomic_write_bytes(path, data)  # unique temp name (F27)
        return parse_csv_bytes(data)


def num(value: Any) -> float | None:
    """Float or None for blank / NA cells."""
    if value in (None, "", "NA"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
