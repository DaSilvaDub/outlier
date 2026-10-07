"""
External adapters for Outlier NFL pipeline.
Provides a helper to load metrics from free data sources (nflverse release assets).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
import logging
from typing import Any

from outlier_nfl.run_context import SourceRecord, before_week_from_schedule

from .common import Client
from .ngs import fetch as fetch_ngs
from .pbp import fetch as fetch_pbp
from .schedule import fetch as fetch_schedule

logger = logging.getLogger(__name__)


@dataclass
class ExternalLoad:
    """External records admitted for one run plus per-source availability."""

    records: list[dict[str, Any]] = field(default_factory=list)
    sources: list[SourceRecord] = field(default_factory=list)
    before_week: int | None = None


def load_external_metrics(
    season: int,
    *,
    slate_date: str,
    as_of_utc: datetime,
    client: Client | None = None,
) -> ExternalLoad:
    """Fetch external metrics for ``season`` as they were knowable at ``as_of_utc``.

    The nflverse schedule is fetched first and sets the cutoff
    (:func:`before_week_from_schedule`); NGS and PBP keep only weeks strictly
    before it. Without a verified cutoff (schedule unavailable or empty) the
    week-bound sources are reported UNAVAILABLE instead of loading every week.

    Each adapter is isolated: one failing source is logged and recorded, and the
    others still load.
    """
    client = client or Client(cache_dir="cache/external", ttl=86400)
    out = ExternalLoad()
    try:
        schedule_records = fetch_schedule(client, season, as_of_utc).get("records", [])
    except Exception as exc:  # one source down must not drop the rest
        logger.warning("External source schedule unavailable: %s", exc)
        out.sources.append(SourceRecord("external:schedule", "UNAVAILABLE", f"fetch failed: {exc}"))
        schedule_records = None
    if schedule_records is not None:
        out.records.extend(schedule_records)
        out.sources.append(SourceRecord(
            "external:schedule", "AVAILABLE", rows_in=len(schedule_records),
            rows_admitted=len(schedule_records)))
        out.before_week = before_week_from_schedule(schedule_records, slate_date, as_of_utc)

    calls: list[tuple[str, Callable[[int], dict[str, Any]]]] = [
        (f"external:ngs:{kind}", partial(fetch_ngs, client, season, kind))
        for kind in ("passing", "rushing", "receiving")
    ]
    calls.append(("external:pbp", partial(fetch_pbp, client, season)))
    for name, call in calls:
        if out.before_week is None:
            out.sources.append(SourceRecord(name, "UNAVAILABLE", "missing verified cutoff"))
            continue
        try:
            result = call(out.before_week)
        except Exception as exc:  # one source down must not drop the rest
            logger.warning("External source %s unavailable: %s", name, exc)
            out.sources.append(SourceRecord(name, "UNAVAILABLE", f"fetch failed: {exc}"))
            continue
        records = result.get("records", []) if isinstance(result, dict) else []
        out.records.extend(records)
        weeks = [int(r["week"]) for r in records if isinstance(r.get("week"), int)]
        out.sources.append(SourceRecord(
            name, "AVAILABLE", rows_admitted=len(records),
            max_week_admitted=max(weeks) if weeks else None))
    return out
