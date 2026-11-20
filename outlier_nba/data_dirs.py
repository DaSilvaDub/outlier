"""Where NBA shadow data lives outside the repository: one place, one override.

The NBA shadow ledger, games store and ESPN cache sit in a local data directory (default
``~/outlier_nba_data``), not in the checkout (which is behind a cloud-sync filter) and not in
``%TEMP%`` (which is wiped). Run bundles (``team_shadow.csv`` plus a sealed ``manifest.json``)
belong beside them: they are the only record of each model row's market id and side, so a wiped
bundle makes its ledger rows permanently unverifiable.

Overrides: ``OUTLIER_NBA_DATA_DIR`` moves the whole data directory; ``OUTLIER_NBA_BUNDLES_DIR``
moves only the bundles. A location inside the repository checkout or the system temp directory
is refused, so a misconfiguration fails loudly instead of quietly losing bundles.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from outlier_scrapers import paths

DATA_DIR_ENV = "OUTLIER_NBA_DATA_DIR"
BUNDLES_DIR_ENV = "OUTLIER_NBA_BUNDLES_DIR"


class NonDurableLocationError(ValueError):
    """The bundle location is inside the repo checkout or the temp directory."""


def _forbidden_roots() -> tuple[Path, ...]:
    return (Path(paths.PROJECT_ROOT).resolve(), Path(tempfile.gettempdir()).resolve())


def data_dir() -> Path:
    override = os.environ.get(DATA_DIR_ENV)
    return Path(override) if override else Path.home() / "outlier_nba_data"


def _checked(path: Path) -> Path:
    resolved = path.resolve()
    for root in _forbidden_roots():
        if resolved == root or root in resolved.parents:
            raise NonDurableLocationError(
                f"{path} is inside {root}; bundles must live outside the repo checkout and temp"
            )
    return path


def bundles_dir() -> Path:
    """Root of all run bundles (durable, outside the repo and temp)."""
    override = os.environ.get(BUNDLES_DIR_ENV)
    return _checked(Path(override) if override else data_dir() / "bundles")


def team_bundle_root() -> Path:
    """``--nba-dir`` for ``run`` (bundles land in ``<root>/runs/<run_id>``)."""
    return bundles_dir() / "team"


def capture_bundle_root() -> Path:
    """``--nba-dir`` for ``capture``; kept apart from the model runs."""
    return bundles_dir() / "capture"
