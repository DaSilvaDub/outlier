from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .paths import LeaguePaths, league_paths


SCHEMA_VERSION = "1.0"
MAX_SOURCE_AGE_HOURS = 6.0
MAX_FUTURE_SECONDS = 5 * 60
MIN_SAFE_COVERAGE_PCT = 90.0

STATUS_FIELDS = (
    "props_status",
    "games_status",
    "insights_status",
    "injuries_status",
    "line_movement_status",
    "game_line_movement_status",
    "cards_status",
)
SERIALIZED_KEYS = (
    *STATUS_FIELDS,
    "coverage_pct",
    "oldest_source_age",
    "latest_source_age",
    "failed_ids",
    "schema_version",
)
STATUS_VALUES = {"ok", "partial", "stale", "missing", "error"}


class _RefreshTaskResult(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def league(self) -> str: ...

    @property
    def ok(self) -> bool: ...

    @property
    def error(self) -> str: ...

    @property
    def skipped(self) -> bool: ...


STREAM_VALUES = {"props", "games", "all"}
ID_TYPES = {"market_id", "outcome_id", "event_id", "player_id", "global"}

_STATUS_TO_FEED = {
    "props_status": "props",
    "games_status": "games",
    "insights_status": "insights",
    "injuries_status": "injuries",
    "line_movement_status": "line_movement",
    "game_line_movement_status": "game_line_movement",
    "cards_status": "cards",
}
_TASK_TO_LANE = {
    "props": ("props", "props"),
    "games": ("games", "games"),
    "insights": ("insights", "props"),
    "line_movement": ("line_movement", "props"),
    "game_line_movement": ("game_line_movement", "games"),
    "cards": ("cards", "props"),
    "game_cards": ("cards", "games"),
}
_STATUS_RANK = {"ok": 0, "partial": 1, "stale": 2, "missing": 3, "error": 4}


@dataclass(frozen=True)
class _Inspection:
    status_payload: dict[str, Any] | None
    artifact_payload: dict[str, Any] | None
    state: str
    failures: list[dict[str, str]]
    ages: list[float]


@dataclass(frozen=True)
class _Component:
    status: str
    coverage: float
    failures: list[dict[str, str]]


def _failure(
    feed: Any,
    stream: Any,
    id_type: str,
    identifier: Any,
    reason: Any,
) -> dict[str, str]:
    value = str(identifier or "").strip()
    if id_type == "global" or not value:
        id_type = "global"
        value = "*"
    message = str(reason or "source failure").strip() or "source failure"
    return {
        "feed": str(feed).strip(),
        "stream": _normalize_stream(stream),
        "id_type": id_type,
        "id": value,
        "reason": message[:300],
    }


def _normalize_stream(stream: Any) -> str:
    token = str(stream or "").strip().lower().replace("-", "_")
    if token in {"props", "prop", "player", "players", "player_prop", "player_props"}:
        return "props"
    if token in {"games", "game", "gameline", "gamelines", "game_line", "game_lines"}:
        return "games"
    if token in {"all", "both", "global", "*"}:
        return "all"
    return token


def _dedupe_failures(failures: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    unique: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for item in failures:
        if not isinstance(item, Mapping):
            continue
        normalized = _failure(
            item.get("feed"),
            item.get("stream"),
            str(item.get("id_type") or "global"),
            item.get("id"),
            item.get("reason"),
        )
        key = (
            normalized["feed"],
            normalized["stream"],
            normalized["id_type"],
            normalized["id"],
        )
        existing = unique.get(key)
        if existing is None or normalized["reason"] < existing["reason"]:
            unique[key] = normalized
    return [unique[key] for key in sorted(unique)]


def _combine_status(*values: str) -> str:
    return max(values or ("ok",), key=lambda value: _STATUS_RANK.get(value, 4))


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("missing timestamp")
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


def _read_object(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, "missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"malformed: {exc}"
    if not isinstance(payload, dict):
        return None, "malformed: root must be an object"
    return payload, None


def _inspect_pair(
    status_path: Path,
    artifact_path: Path,
    *,
    feed: str,
    stream: str,
    now: datetime,
) -> _Inspection:
    failures: list[dict[str, str]] = []
    states = ["ok"]
    ages: list[float] = []
    status_payload, status_error = _read_object(status_path)
    artifact_payload, artifact_error = _read_object(artifact_path)

    for label, path, payload, error in (
        ("status", status_path, status_payload, status_error),
        ("artifact", artifact_path, artifact_payload, artifact_error),
    ):
        if error:
            state = "missing" if error == "missing" else "error"
            states.append(state)
            failures.append(
                _failure(feed, stream, "global", "*", f"{label} file {error}: {path.name}")
            )
            continue

        assert payload is not None
        try:
            generated_at = _parse_timestamp(payload.get("generated_at"))
        except (TypeError, ValueError, OverflowError) as exc:
            states.append("error")
            failures.append(
                _failure(feed, stream, "global", "*", f"{label} generated_at invalid: {exc}")
            )
            continue

        age_seconds = (now - generated_at.astimezone(now.tzinfo)).total_seconds()
        if age_seconds < -MAX_FUTURE_SECONDS:
            states.append("error")
            failures.append(
                _failure(
                    feed, stream, "global", "*", f"{label} generated_at exceeds future tolerance"
                )
            )
            continue

        age_hours = max(0.0, age_seconds / 3600.0)
        # The serialized age range describes concrete normalized/card sources,
        # not the surrounding status envelopes. Status timestamps are still
        # validated and can make the component stale or erroneous.
        if label == "artifact":
            ages.append(age_hours)
        if age_hours > MAX_SOURCE_AGE_HOURS:
            states.append("stale")
            failures.append(
                _failure(
                    feed,
                    stream,
                    "global",
                    "*",
                    f"{label} source age {age_hours:.2f}h exceeds {MAX_SOURCE_AGE_HOURS:.0f}h",
                )
            )

    if status_payload is not None and not isinstance(status_payload.get("status"), str):
        states.append("error")
        failures.append(_failure(feed, stream, "global", "*", "status value missing or malformed"))

    return _Inspection(
        status_payload=status_payload,
        artifact_payload=artifact_payload,
        state=_combine_status(*states),
        failures=_dedupe_failures(failures),
        ages=ages,
    )


def _overlay_task_result(
    inspection: _Inspection,
    result: _RefreshTaskResult,
    *,
    feed: str,
    stream: str,
) -> _Inspection:
    """Prefer a structured producer result over inferring success from files."""

    if result.skipped:
        return inspection
    if result.ok:
        failures = [
            item for item in inspection.failures if not item["reason"].startswith("status file ")
        ]
        status_payload = inspection.status_payload
        state = inspection.state
        if status_payload is None:
            status_payload = {"status": "ok"}
            if any("source age" in item["reason"] for item in failures):
                state = "stale"
            elif any(item["reason"].startswith("artifact file ") for item in failures):
                state = inspection.state
            else:
                state = "ok"
        return replace(
            inspection,
            status_payload=status_payload,
            state=state,
            failures=_dedupe_failures(failures),
        )
    status_payload = dict(inspection.status_payload or {})
    status_payload["status"] = "error"
    status_payload["error"] = result.error or "refresh task failed"
    return replace(
        inspection,
        status_payload=status_payload,
        state="error",
        failures=_dedupe_failures(inspection.failures),
    )


def _inspections_with_task_results(
    inspections: dict[str, _Inspection],
    task_results: Sequence[_RefreshTaskResult] | None,
    league: str,
) -> dict[str, _Inspection]:
    if not task_results:
        return inspections
    updated = dict(inspections)
    token = league.strip().upper()
    for result in task_results:
        if result.league.strip().upper() != token:
            continue
        lane = _TASK_TO_LANE.get(result.name)
        if lane is None:
            continue
        feed, stream = lane
        key = result.name if result.name in updated else feed
        if key not in updated:
            continue
        updated[key] = _overlay_task_result(updated[key], result, feed=feed, stream=stream)
    return updated


def _producer_state(status_payload: Mapping[str, Any] | None) -> str:
    if status_payload is None:
        return "missing"
    token = str(status_payload.get("status") or "").strip().lower()
    if token in STATUS_VALUES:
        return token
    if token in {"stale_props", "stale_inputs"}:
        return "stale"
    if token in {"missing_inputs", "not_found"}:
        return "missing"
    if token in {"auth_required", "failed", "failure"}:
        return "error"
    return "error"


def _producer_failure(
    payload: Mapping[str, Any] | None,
    *,
    feed: str,
    stream: str,
    state: str,
) -> dict[str, str]:
    raw_status = str((payload or {}).get("status") or "missing")
    reason = (payload or {}).get("error") or f"producer status {raw_status}"
    return _failure(feed, stream, "global", "*", reason)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _coverage_from_pairs(
    payload: Mapping[str, Any] | None,
    pairs: Iterable[tuple[str, str]],
    *,
    fallback: float = 100.0,
) -> float:
    if payload is None:
        return 0.0
    requested = 0.0
    succeeded = 0.0
    found = False
    for requested_key, succeeded_key in pairs:
        req = _number(payload.get(requested_key))
        succ = _number(payload.get(succeeded_key))
        if req is None or succ is None:
            continue
        found = True
        requested += req
        succeeded += min(succ, req)
    if not found:
        return fallback
    if requested == 0:
        return 100.0
    return max(0.0, min(100.0, succeeded * 100.0 / requested))


def _coverage_requested_errors(
    payload: Mapping[str, Any] | None,
    requested_key: str,
    error_key: str,
) -> float:
    if payload is None:
        return 0.0
    requested = _number(payload.get(requested_key))
    errors = _number(payload.get(error_key))
    if requested is None:
        return 100.0
    if requested == 0:
        return 100.0
    error_count = errors or 0.0
    return max(0.0, min(100.0, (requested - min(requested, error_count)) * 100.0 / requested))


def _coverage_cards(payload: Mapping[str, Any] | None) -> float:
    if payload is None:
        return 0.0
    count_pairs = (
        ("cards_requested_count", "cards_succeeded_count"),
        ("requested_count", "succeeded_count"),
        ("cards_requested", "cards_succeeded"),
    )
    for container in (payload, payload.get("coverage")):
        if not isinstance(container, Mapping):
            continue
        for requested_key, succeeded_key in count_pairs:
            requested = _number(container.get(requested_key))
            succeeded = _number(container.get(succeeded_key))
            if requested is None or succeeded is None:
                continue
            if requested == 0:
                return 100.0
            return max(0.0, min(100.0, min(succeeded, requested) * 100.0 / requested))
    # Current card producers expose descriptive upstream/card totals, not a
    # generation-attempt denominator. Treat their successful status as complete
    # rather than dividing filtered card output by upstream market count.
    return 100.0


def _error_rows(payload: Mapping[str, Any] | None, key: str) -> list[Mapping[str, Any]]:
    value = (payload or {}).get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _failures_from_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    feed: str,
    stream: str,
    default_reason: str,
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    for row in rows:
        id_type = "global"
        identifier: Any = "*"
        for candidate in ("market_id", "outcome_id", "event_id", "player_id"):
            if str(row.get(candidate) or "").strip():
                id_type = candidate
                identifier = row[candidate]
                break
        failures.append(
            _failure(
                feed,
                stream,
                id_type,
                identifier,
                row.get("reason") or row.get("error") or default_reason,
            )
        )
    return _dedupe_failures(failures)


def _component(
    inspection: _Inspection,
    *,
    feed: str,
    stream: str,
    coverage: float,
    producer_state: str | None = None,
    partial_failures: Iterable[Mapping[str, Any]] = (),
) -> _Component:
    state = producer_state or _producer_state(inspection.status_payload)
    failures = [*inspection.failures]
    scoped = _dedupe_failures(partial_failures)
    if state == "partial":
        if scoped:
            failures.extend(scoped)
        else:
            failures.append(
                _producer_failure(inspection.status_payload, feed=feed, stream=stream, state=state)
            )
    elif state != "ok":
        failures.append(
            _producer_failure(inspection.status_payload, feed=feed, stream=stream, state=state)
        )
    return _Component(
        status=_combine_status(inspection.state, state),
        coverage=max(0.0, min(100.0, float(coverage))),
        failures=_dedupe_failures(failures),
    )


def _props_component(inspection: _Inspection) -> _Component:
    payload = inspection.status_payload
    failures = _failures_from_rows(
        _error_rows(payload, "schedule_event_fetch_errors"),
        feed="props",
        stream="props",
        default_reason="schedule event fetch failed",
    )
    state = _producer_state(payload)
    error_count = _number((payload or {}).get("schedule_event_fetch_error_count")) or 0.0
    if state == "ok" and (error_count > 0 or failures):
        state = "partial"
    coverage = _coverage_requested_errors(
        payload,
        "schedule_event_fetch_requested_count",
        "schedule_event_fetch_error_count",
    )
    return _component(
        inspection,
        feed="props",
        stream="props",
        coverage=coverage,
        producer_state=state,
        partial_failures=failures,
    )


def _games_step_component(
    inspection: _Inspection,
    *,
    feed: str,
    stream: str,
    steps: set[str],
    count_pairs: Iterable[tuple[str, str]],
    core: bool = False,
) -> _Component:
    payload = inspection.status_payload
    rows = _error_rows(payload, "fetch_errors")
    relevant_rows = [row for row in rows if str(row.get("step") or "").strip().lower() in steps]
    recognized_steps = {"matchup", "markets", "insights", "injuries"}
    unknown_rows = [
        row for row in rows if str(row.get("step") or "").strip().lower() not in recognized_steps
    ]
    failures = _failures_from_rows(
        relevant_rows,
        feed=feed,
        stream=stream,
        default_reason=f"{feed} fetch failed",
    )
    if unknown_rows:
        unknown_steps = sorted(
            {str(row.get("step") or "missing").strip() or "missing" for row in unknown_rows}
        )
        failures.append(
            _failure(
                feed,
                stream,
                "global",
                "*",
                f"unclassified games fetch step(s): {', '.join(unknown_steps)}",
            )
        )
    coverage = _coverage_from_pairs(payload, count_pairs)
    raw_state = _producer_state(payload)
    has_step_contract = bool(rows) or any(
        _number((payload or {}).get(key)) is not None for pair in count_pairs for key in pair
    )

    if raw_state in {"missing", "stale", "error"}:
        state = raw_state
    elif relevant_rows or unknown_rows or coverage < 100.0:
        state = "partial"
    elif raw_state == "partial" and not rows and not has_step_contract:
        state = "partial"
    else:
        state = "ok"

    return _component(
        inspection,
        feed=feed,
        stream=stream,
        coverage=coverage,
        producer_state=state,
        partial_failures=failures,
    )


def _insights_standalone_component(inspection: _Inspection) -> _Component:
    payload = inspection.status_payload
    rows = _error_rows(payload, "fetch_errors") or _error_rows(payload, "errors")
    failures = _failures_from_rows(
        rows,
        feed="insights",
        stream="props",
        default_reason="standalone insights fetch failed",
    )
    state = _producer_state(payload)
    if state == "ok" and failures:
        state = "partial"
    return _component(
        inspection,
        feed="insights",
        stream="props",
        coverage=100.0 if payload is not None else 0.0,
        producer_state=state,
        partial_failures=failures,
    )


def _line_movement_component(
    inspection: _Inspection,
    *,
    feed: str,
    stream: str,
) -> _Component:
    payload = inspection.status_payload
    raw_error_ids = (payload or {}).get("error_market_ids")
    error_ids = raw_error_ids if isinstance(raw_error_ids, list) else []
    first_pass_errors = _error_rows(payload, "first_pass_errors")
    reason_by_id = {
        str(row.get("market_id")): row.get("error") or "market fetch failed"
        for row in first_pass_errors
        if str(row.get("market_id") or "").strip()
    }
    # New statuses expose final residual IDs explicitly, excluding recovered
    # retries. Older statuses can still scope their partial failures through the
    # error rows when that final-ID field is absent altogether.
    if not isinstance(raw_error_ids, list):
        error_ids = [
            row.get("market_id")
            for row in first_pass_errors
            if str(row.get("market_id") or "").strip()
        ]
    failures = [
        _failure(
            feed,
            stream,
            "market_id",
            market_id,
            reason_by_id.get(str(market_id), "market fetch failed"),
        )
        for market_id in error_ids
        if str(market_id or "").strip()
    ]
    state = _producer_state(payload)
    error_count = _number((payload or {}).get("fetch_error_count")) or 0.0
    if state == "ok" and (error_count > 0 or failures):
        state = "partial"
    coverage = _coverage_from_pairs(payload, (("markets_requested", "markets_fetched"),))
    return _component(
        inspection,
        feed=feed,
        stream=stream,
        coverage=coverage,
        producer_state=state,
        partial_failures=failures,
    )


def _cards_lane(
    inspection: _Inspection,
    *,
    stream: str,
) -> _Component:
    payload = inspection.status_payload
    state = _producer_state(payload)
    failures: list[dict[str, str]] = []
    missing_feeds = (payload or {}).get("missing_feeds")
    if isinstance(missing_feeds, list) and missing_feeds:
        state = "partial" if state == "ok" else state
        failures.extend(
            _failure("cards", stream, "global", "*", f"missing card input: {item}")
            for item in missing_feeds
        )
    return _component(
        inspection,
        feed="cards",
        stream=stream,
        coverage=_coverage_cards(payload),
        producer_state=state,
        partial_failures=failures,
    )


def _combined_component(*components: _Component) -> _Component:
    if not components:
        return _Component("missing", 0.0, [])
    return _Component(
        status=_combine_status(*(component.status for component in components)),
        coverage=sum(component.coverage for component in components) / len(components),
        failures=_dedupe_failures(
            failure for component in components for failure in component.failures
        ),
    )


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _paths_for(league: str) -> LeaguePaths:
    return league_paths(league)


def league_has_confirmed_empty_slate(league: str, *, now: datetime) -> bool:
    """Whether the games producer's own current-run status confirms zero
    scheduled events for this league today.

    games.py deliberately preserves the last real games_normalized_latest.json
    snapshot rather than overwrite it with an empty one on a zero-event day
    (see ``_should_preserve_previous_latest``), so that *artifact*'s age keeps
    growing for as long as the league has nothing scheduled -- indefinitely,
    for an out-of-season league. Reading that age as staleness cascades into
    the games/insights/injuries components and their coverage math, failing
    the safety gate every single day for a producer that is working exactly
    as designed.

    The producer's own status file, written fresh on every run regardless of
    whether it found games, is the trustworthy signal instead. It is trusted
    here only when it is itself fresh: a stale or missing status proves the
    check hasn't run recently, not that today is empty.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    paths = _paths_for(league)
    payload, error = _read_object(paths.reports / "games_status_latest.json")
    if error or payload is None:
        return False
    if payload.get("status") != "ok":
        return False
    if _number(payload.get("target_event_count")) != 0:
        return False
    try:
        generated_at = _parse_timestamp(payload.get("generated_at"))
    except (TypeError, ValueError, OverflowError):
        return False
    age_seconds = (now - generated_at.astimezone(now.tzinfo)).total_seconds()
    future_tolerance_seconds = MAX_FUTURE_SECONDS
    if age_seconds < -future_tolerance_seconds:
        return False
    age_hours = max(0.0, age_seconds / 3600.0)
    return age_hours <= MAX_SOURCE_AGE_HOURS


def build_feed_health(
    league: str,
    now: datetime | None = None,
    write: bool = True,
    task_results: Sequence[_RefreshTaskResult] | None = None,
) -> dict[str, Any]:
    """Build the v1 unified feed-health snapshot for one league.

    Both the producer status and the concrete normalized/card artifact are
    required for each lane. Producer completeness and source age are evaluated
    separately so a freshly written status cannot make an old artifact healthy.
    """

    reference_now = now or datetime.now(timezone.utc)
    if reference_now.tzinfo is None or reference_now.utcoffset() is None:
        reference_now = reference_now.replace(tzinfo=timezone.utc)
    paths = _paths_for(league)
    token = paths.league.lower()

    props_inspection = _inspect_pair(
        paths.reports / "props_export_status_latest.json",
        paths.props_normalized_latest(),
        feed="props",
        stream="props",
        now=reference_now,
    )
    games_inspection = _inspect_pair(
        paths.reports / "games_status_latest.json",
        paths.games_normalized_latest(),
        feed="games",
        stream="all",
        now=reference_now,
    )
    insights_inspection = _inspect_pair(
        paths.reports / "insights_status_latest.json",
        paths.normalized / f"{token}_insights_latest.json",
        feed="insights",
        stream="props",
        now=reference_now,
    )
    line_movement_inspection = _inspect_pair(
        paths.reports / "line_movement_status_latest.json",
        paths.line_movement_latest(),
        feed="line_movement",
        stream="props",
        now=reference_now,
    )
    game_line_movement_inspection = _inspect_pair(
        paths.reports / "games_line_movement_status_latest.json",
        paths.games_line_movement_latest(),
        feed="game_line_movement",
        stream="games",
        now=reference_now,
    )
    player_cards_inspection = _inspect_pair(
        paths.reports / "cards_status_latest.json",
        paths.cards_latest(),
        feed="cards",
        stream="props",
        now=reference_now,
    )
    game_cards_inspection = _inspect_pair(
        paths.reports / "games_cards_status_latest.json",
        paths.games_cards_latest(),
        feed="cards",
        stream="games",
        now=reference_now,
    )
    overlaid = _inspections_with_task_results(
        {
            "props": props_inspection,
            "games": games_inspection,
            "insights": insights_inspection,
            "line_movement": line_movement_inspection,
            "game_line_movement": game_line_movement_inspection,
            "cards": player_cards_inspection,
            "game_cards": game_cards_inspection,
        },
        task_results,
        league,
    )
    props_inspection = overlaid["props"]
    games_inspection = overlaid["games"]
    insights_inspection = overlaid["insights"]
    line_movement_inspection = overlaid["line_movement"]
    game_line_movement_inspection = overlaid["game_line_movement"]
    player_cards_inspection = overlaid["cards"]
    game_cards_inspection = overlaid["game_cards"]

    props = _props_component(props_inspection)
    games = _games_step_component(
        games_inspection,
        feed="games",
        stream="games",
        steps={"matchup", "markets"},
        count_pairs=(
            ("matchup_fetch_requested_count", "matchup_fetch_succeeded_count"),
            ("markets_fetch_requested_count", "markets_fetch_succeeded_count"),
        ),
        core=True,
    )
    event_insights = _games_step_component(
        games_inspection,
        feed="insights",
        stream="games",
        steps={"insights"},
        count_pairs=(("insights_fetch_requested_count", "insights_fetch_succeeded_count"),),
    )
    insights = _combined_component(
        _insights_standalone_component(insights_inspection), event_insights
    )
    injuries = _games_step_component(
        games_inspection,
        feed="injuries",
        stream="all",
        steps={"injuries"},
        count_pairs=(("injury_fetch_requested_count", "injury_fetch_succeeded_count"),),
    )
    line_movement = _line_movement_component(
        line_movement_inspection, feed="line_movement", stream="props"
    )
    game_line_movement = _line_movement_component(
        game_line_movement_inspection, feed="game_line_movement", stream="games"
    )
    cards = _combined_component(
        _cards_lane(player_cards_inspection, stream="props"),
        _cards_lane(game_cards_inspection, stream="games"),
    )

    components = (props, games, insights, injuries, line_movement, game_line_movement, cards)
    ages = [
        age
        for inspection in (
            props_inspection,
            games_inspection,
            insights_inspection,
            line_movement_inspection,
            game_line_movement_inspection,
            player_cards_inspection,
            game_cards_inspection,
        )
        for age in inspection.ages
    ]
    payload: dict[str, Any] = {
        "props_status": props.status,
        "games_status": games.status,
        "insights_status": insights.status,
        "injuries_status": injuries.status,
        "line_movement_status": line_movement.status,
        "game_line_movement_status": game_line_movement.status,
        "cards_status": cards.status,
        "coverage_pct": round(sum(component.coverage for component in components) / 7.0, 6),
        "oldest_source_age": round(max(ages), 6) if ages else None,
        "latest_source_age": round(min(ages), 6) if ages else None,
        "failed_ids": _dedupe_failures(
            failure for component in components for failure in component.failures
        ),
        "schema_version": SCHEMA_VERSION,
    }

    if write:
        _atomic_write_json(paths.reports / "feed_health_latest.json", payload)
    return payload


def validate_feed_health(payload: Any) -> tuple[bool, list[str]]:
    """Validate the v1 contract and apply its fail-closed safety gate."""

    reasons: list[str] = []
    if not isinstance(payload, Mapping):
        return False, ["payload must be an object"]

    keys = set(payload)
    expected = set(SERIALIZED_KEYS)
    missing = sorted(expected - keys)
    extra = sorted(keys - expected)
    if missing:
        reasons.append(f"missing keys: {', '.join(missing)}")
    if extra:
        reasons.append(f"unexpected keys: {', '.join(extra)}")
    if payload.get("schema_version") != SCHEMA_VERSION:
        reasons.append(f"schema_version must be {SCHEMA_VERSION}")

    for field in STATUS_FIELDS:
        status = payload.get(field)
        if status not in STATUS_VALUES:
            reasons.append(f"{field} has invalid status {status!r}")

    coverage = _number(payload.get("coverage_pct"))
    if coverage is None or coverage > 100.0:
        reasons.append("coverage_pct must be a finite number from 0 to 100")

    oldest = payload.get("oldest_source_age")
    latest = payload.get("latest_source_age")
    if (oldest is None) != (latest is None):
        reasons.append("source ages must both be numbers or both be null")
    elif oldest is None and latest is None:
        reasons.append("source ages are unavailable")
    if oldest is not None and latest is not None:
        oldest_number = _number(oldest)
        latest_number = _number(latest)
        if oldest_number is None or latest_number is None:
            reasons.append("source ages must be finite non-negative numbers")
        elif oldest_number < latest_number:
            reasons.append("oldest_source_age cannot be less than latest_source_age")
        elif oldest_number > MAX_SOURCE_AGE_HOURS:
            reasons.append(
                f"oldest_source_age {oldest_number:.2f} exceeds {MAX_SOURCE_AGE_HOURS:.0f}h"
            )

    raw_failures = payload.get("failed_ids")
    valid_failures: list[dict[str, Any]] = []
    if not isinstance(raw_failures, list):
        reasons.append("failed_ids must be a list")
    else:
        for index, failure in enumerate(raw_failures):
            if not isinstance(failure, Mapping):
                reasons.append(f"failed_ids[{index}] must be an object")
                continue
            required_failure_keys = {"feed", "stream", "id_type", "id", "reason"}
            if set(failure) != required_failure_keys:
                reasons.append(f"failed_ids[{index}] must contain the exact v1 failure keys")
                continue
            if not isinstance(failure.get("feed"), str) or not failure.get("feed", "").strip():
                reasons.append(f"failed_ids[{index}].feed must be a non-empty string")
            if failure.get("stream") not in STREAM_VALUES:
                reasons.append(f"failed_ids[{index}].stream is invalid")
            if failure.get("id_type") not in ID_TYPES:
                reasons.append(f"failed_ids[{index}].id_type is invalid")
            if not isinstance(failure.get("id"), str) or not failure.get("id", "").strip():
                reasons.append(f"failed_ids[{index}].id must be a non-empty string")
            if not isinstance(failure.get("reason"), str) or not failure.get("reason", "").strip():
                reasons.append(f"failed_ids[{index}].reason must be a non-empty string")
            if failure.get("id_type") == "global" and failure.get("id") != "*":
                reasons.append(f"failed_ids[{index}] global IDs must use '*'")
            valid_failures.append(dict(failure))
        if raw_failures != _dedupe_failures(valid_failures):
            reasons.append("failed_ids must be deduplicated and sorted")

    # Safety policy is part of validation because callers use this function as
    # the pre-pack gate.
    for field in STATUS_FIELDS:
        status = payload.get(field)
        if status in {"missing", "stale", "error"}:
            reasons.append(f"{field} is {status}")
        elif status == "partial" and isinstance(raw_failures, list):
            feed = _STATUS_TO_FEED[field]
            feed_failures = [failure for failure in valid_failures if failure.get("feed") == feed]
            if not feed_failures:
                reasons.append(f"{field} is partial without scoped IDs")
            elif any(
                failure.get("id_type") == "global" or failure.get("id") == "*"
                for failure in feed_failures
            ):
                reasons.append(f"{field} is partial with an unscoped failure")

    if coverage is not None and coverage < MIN_SAFE_COVERAGE_PCT:
        reasons.append(f"coverage_pct {coverage:.2f} is below {MIN_SAFE_COVERAGE_PCT:.0f}")
    return not reasons, reasons


def _row_values(row: Mapping[str, Any], id_type: str) -> set[str]:
    values: set[str] = set()
    for container in (row, row.get("source_ref"), row.get("ref"), row.get("card")):
        if not isinstance(container, Mapping):
            continue
        value = container.get(id_type)
        if isinstance(value, (list, tuple, set)):
            values.update(str(item).strip() for item in value if str(item).strip())
        elif str(value or "").strip():
            values.add(str(value).strip())
    return values


def matching_failures(
    payload: Any, stream: str, row: Mapping[str, Any] | None
) -> list[dict[str, str]]:
    """Return deterministic failures applicable to a candidate row.

    ``props`` and ``games`` are the serialized stream names. Common player/game
    aliases are accepted at the API boundary for compatibility.
    """

    if not isinstance(payload, Mapping):
        return []
    failures = payload.get("failed_ids")
    if not isinstance(failures, list):
        return []
    target_stream = _normalize_stream(stream)
    if target_stream not in {"props", "games", "all"}:
        return []
    candidate = row if isinstance(row, Mapping) else {}
    matched: list[dict[str, str]] = []
    for failure in failures:
        if not isinstance(failure, Mapping):
            continue
        failure_stream = _normalize_stream(failure.get("stream"))
        if target_stream != "all" and failure_stream not in {"all", target_stream}:
            continue
        if target_stream == "all" and failure_stream not in STREAM_VALUES:
            continue
        id_type = str(failure.get("id_type") or "")
        identifier = str(failure.get("id") or "").strip()
        if id_type == "global" and identifier == "*":
            matched.append(dict(failure))
        elif id_type in ID_TYPES and identifier in _row_values(candidate, id_type):
            matched.append(dict(failure))
    return _dedupe_failures(matched)


def is_feed_health_safe(
    payload: Any,
    stream: str | None = None,
    row: Mapping[str, Any] | None = None,
) -> bool:
    """Convenience safety helper for callers that also need row applicability."""

    safe, _ = validate_feed_health(payload)
    if not safe:
        return False
    if stream is None:
        return True
    return not matching_failures(payload, stream, row)
