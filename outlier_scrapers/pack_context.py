"""Contextual enrichment for the pack writer: event starts, injuries, freshness.

These functions build contextual enrichment rather than candidate policy — no
row-selection or sizing logic lives here.
"""

from __future__ import annotations

from typing import Any, Sequence

from outlier_scrapers import feed_health

# Cap free-text analysis so multi-player event strings stay pack-readable.
_INJURY_ANALYSIS_MAX_CHARS = 160

FEED_STATUS_FIELDS = (
    "props_status",
    "games_status",
    "insights_status",
    "injuries_status",
    "line_movement_status",
    "game_line_movement_status",
    "cards_status",
)


def build_event_starts(props_payload: dict | None, games_payload: dict | None) -> dict[str, str]:
    starts: dict[str, str] = {}
    if props_payload:
        for rec in props_payload.get("records", []):
            eid = rec.get("event_id")
            sa = (rec.get("sport_context") or {}).get("event_starts_at")
            if eid and sa and eid not in starts:
                starts[str(eid)] = sa
    if games_payload:
        events = (games_payload.get("context") or {}).get("events") or {}
        for eid, info in events.items():
            if isinstance(info, dict):
                sa = info.get("starts_at") or info.get("event_starts_at")
                if sa and str(eid) not in starts:
                    starts[str(eid)] = sa
    return starts


def _injury_return_date(injury: dict[str, Any]) -> str:
    """Normalize API returnDate / return_date to YYYY-MM-DD when possible.

    Returns empty string if missing or unparseable to avoid unnormalized text
    (e.g., "TBD", "2026/09/04") in ``ret YYYY-MM-DD`` flags.
    """
    raw = injury.get("returnDate") or injury.get("return_date") or ""
    text = str(raw).strip()
    if not text:
        return ""
    # Common shapes: "2026-09-04", "2026-09-04T00:00:00-0700"
    if (
        len(text) >= 10
        and text[4] == "-"
        and text[7] == "-"
        and text[:4].isdigit()
        and text[5:7].isdigit()
        and text[8:10].isdigit()
    ):
        return text[:10]
    return ""


def _format_injury(item: dict[str, Any]) -> str:
    """Render one injury from the live schema for pack ``injury_flags``.

    Preferred shape::

        First Last (Status; Body; ret YYYY-MM-DD): analysis…

    Nested fields (when present): ``injury.status``, body from
    ``injury.injury``, ``injury.returnDate``, ``injury.analysis``.
    Missing pieces are omitted. Falls back to legacy ``player`` /
    ``description`` and never dumps the raw dict.
    """
    name = " ".join(part for part in (item.get("firstName"), item.get("lastName")) if part).strip()
    if not name:
        name = str(item.get("player") or item.get("description") or "").strip()

    raw_injury = item.get("injury")
    injury: dict[str, Any] = raw_injury if isinstance(raw_injury, dict) else {}
    raw_status = injury.get("status")
    status = str(raw_status).strip() if isinstance(raw_status, (str, int, float)) else ""
    # API body/diagnosis lives under nested key "injury" (e.g. "Right Forearm Strain").
    raw_body = injury.get("injury")
    body = str(raw_body).strip() if isinstance(raw_body, (str, int, float)) else ""
    ret = _injury_return_date(injury)
    raw_analysis = injury.get("analysis")
    analysis = str(raw_analysis).strip() if isinstance(raw_analysis, (str, int, float)) else ""

    paren_bits: list[str] = []
    if status:
        paren_bits.append(status)
    if body:
        paren_bits.append(body)
    if ret:
        paren_bits.append(f"ret {ret}")

    if name and paren_bits:
        core = f"{name} ({'; '.join(paren_bits)})"
    else:
        core = name

    if core and analysis:
        if len(analysis) > _INJURY_ANALYSIS_MAX_CHARS:
            analysis = analysis[: _INJURY_ANALYSIS_MAX_CHARS - 3].rstrip() + "..."
        core = f"{core}: {analysis}"
    return core


def build_injuries(games_payload: dict | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not games_payload:
        return out
    ctx = games_payload.get("context") or {}
    teams = ctx.get("teams") or {}
    events = ctx.get("events") or {}
    for eid, info in events.items():
        if not isinstance(info, dict):
            continue
        team_ids = [info.get("home_team_id"), info.get("away_team_id")]
        code_by_tid = {
            str(info.get("home_team_id") or ""): info.get("home"),
            str(info.get("away_team_id") or ""): info.get("away"),
        }
        flags: list[str] = []
        for tid in team_ids:
            inj = (teams.get(str(tid)) or {}).get("injuries") if tid else None
            team_code = str(code_by_tid.get(str(tid or "")) or "").strip().upper()
            for item in inj or []:
                if isinstance(item, dict):
                    formatted = _format_injury(item)
                else:
                    formatted = str(item)
                if formatted and team_code:
                    formatted = f"{team_code}: {formatted}"
                if formatted:
                    flags.append(formatted)
        if flags:
            out[str(eid)] = " | ".join(flags)
    return out


def build_freshness_section(
    leagues: Sequence[str],
    feed_health_by_league: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    lines = ["### Freshness / Coverage"]
    for raw in leagues:
        lg = raw.strip().upper()
        if not lg:
            continue
        health = (feed_health_by_league or {}).get(lg)
        if health is None:
            health = feed_health.build_feed_health(lg, write=True)
        safe, reasons = feed_health.validate_feed_health(health)
        statuses = ", ".join(
            f"{field.removesuffix('_status')}={health.get(field, 'missing')}"
            for field in FEED_STATUS_FIELDS
        )
        latest_age = health.get("latest_source_age")
        oldest_age = health.get("oldest_source_age")
        age_text = (
            f"{latest_age:.2f}h..{oldest_age:.2f}h"
            if isinstance(latest_age, (int, float)) and isinstance(oldest_age, (int, float))
            else "unknown"
        )
        verdict = "OK" if safe else "UNSAFE"
        lines.append(
            f"- {lg}: {verdict}; coverage={float(health.get('coverage_pct') or 0.0):.2f}%; "
            f"source_age={age_text}; {statuses}"
        )
        failed = health.get("failed_ids") or []
        if failed:
            shown = [
                f"{item.get('feed')}:{item.get('stream')}:{item.get('id_type')}:{item.get('id')}"
                for item in failed[:8]
                if isinstance(item, dict)
            ]
            more = f" (+{len(failed) - len(shown)} more)" if len(failed) > len(shown) else ""
            lines.append(f"  - failed_ids: {', '.join(shown)}{more}")
        if reasons:
            lines.append(f"  - gate: {'; '.join(reasons)}")
    return lines


def build_candidate_coverage_section(coverage: dict[str, dict[str, int]]) -> list[str]:
    """Explain exactly why a requested league did or did not reach the pack."""
    lines = ["### Candidate Coverage"]
    for league, stats in coverage.items():
        emitted = stats.get("emitted", 0)
        state = "ZERO CANDIDATES" if emitted == 0 else f"{emitted} emitted"
        lines.append(
            f"- {league}: {state}; cards={stats.get('cards', 0)}, "
            f"rows_built={stats.get('rows_built', 0)}, "
            f"date_filtered={stats.get('date_filtered', 0)}, "
            f"started_dropped={stats.get('started_dropped', 0)}, "
            f"unverified_start_dropped={stats.get('unverified_start_dropped', 0)}"
        )
    return lines


def build_feed_health_by_league(leagues: Sequence[str]) -> dict[str, dict[str, Any]]:
    health_by_league: dict[str, dict[str, Any]] = {}
    for raw_league in leagues:
        league = raw_league.strip().upper()
        if not league:
            continue
        health = feed_health.build_feed_health(league, write=True)
        safe, reasons = feed_health.validate_feed_health(health)
        if not safe:
            raise RuntimeError(f"{league} feed health unsafe: {'; '.join(reasons)}")
        health_by_league[league] = health
    return health_by_league
