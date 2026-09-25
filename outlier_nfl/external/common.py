# Simple client stub for external data adapters

import os
from pathlib import Path


class Client:
    """A minimal client used by external adapters.

    The real implementation would handle HTTP requests, caching, rate‑limiting,
    and authentication. For the purposes of the current pipeline we only need a
    placeholder that stores ``cache_dir`` and ``ttl`` attributes so that the
    adapters can be instantiated without error.
    """

    def __init__(self, cache_dir: str | os.PathLike = "cache/external", ttl: int = 86400):
        self.cache_dir = Path(cache_dir).resolve()
        self.ttl = ttl
        # Ensure the cache directory exists – the adapters may write temporary files.
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def __repr__(self) -> str:
        return f"Client(cache_dir={self.cache_dir!s}, ttl={self.ttl})"
