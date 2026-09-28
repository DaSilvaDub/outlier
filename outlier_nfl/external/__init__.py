"""
External adapters for Outlier NFL pipeline.
Provides a helper to load metrics from free data sources (nflverse release assets).
"""

from functools import partial
import logging
from typing import Any, Callable

from .common import Client
from .ngs import fetch as fetch_ngs
from .pbp import fetch as fetch_pbp
from .schedule import fetch as fetch_schedule

logger = logging.getLogger(__name__)


def load_external_metrics(
    season: int, through_week: int = 22, client: Client | None = None
) -> list[dict[str, Any]]:
    """Fetch and aggregate external metrics for a given NFL season.

    Parameters
    ----------
    season: int
        The NFL season year (e.g., 2026).
    through_week: int, optional
        Include data through this week (default includes all weeks).
    client: Client, optional
        Download client; defaults to a 24h disk cache under ``cache/external``.

    Each adapter is isolated: one failing source is logged and skipped so the
    others still load.
    """
    client = client or Client(cache_dir="cache/external", ttl=86400)
    calls: list[tuple[str, Callable[[], dict[str, Any]]]] = [
        (f"ngs:{kind}", partial(fetch_ngs, client, season, kind, through_week))
        for kind in ("passing", "rushing", "receiving")
    ]
    calls.append(("pbp", partial(fetch_pbp, client, season, through_week)))
    calls.append(("schedule", partial(fetch_schedule, client, season, through_week)))
    metrics: list[dict[str, Any]] = []
    for name, call in calls:
        try:
            result = call()
        except Exception as exc:  # one source down must not drop the rest
            logger.warning("External source %s unavailable: %s", name, exc)
            continue
        if isinstance(result, dict):
            metrics.extend(result.get("records", []))
    return metrics
