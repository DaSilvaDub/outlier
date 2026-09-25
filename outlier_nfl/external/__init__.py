"""
External adapters for Outlier NFL pipeline.
Provides a helper to load metrics from free data sources.
"""

from .common import Client
from .ngs import fetch as fetch_ngs
from .pbp import fetch as fetch_pbp
from .schedule import fetch as fetch_schedule


def load_external_metrics(season: int, through_week: int = 22):
    """Fetch and aggregate external metrics for a given NFL season.

    Parameters
    ----------
    season: int
        The NFL season year (e.g., 2026).
    through_week: int, optional
        Include data through this week (default includes all weeks).
    """
    client = Client(cache_dir="cache/external", ttl=86400)
    metrics = []
    # NextGen Stats for passing, rushing, receiving
    for kind in ("passing", "rushing", "receiving"):
        result = fetch_ngs(client, season, kind, through_week)
        if isinstance(result, dict):
            metrics.extend(result.get("records", []))
    # Play‑by‑play team EPA aggregates
    result = fetch_pbp(client, season, through_week)
    if isinstance(result, dict):
        metrics.extend(result.get("records", []))
    # Schedule information (games, lines, etc.)
    result = fetch_schedule(client, season, through_week)
    if isinstance(result, dict):
        metrics.extend(result.get("records", []))
    return metrics
