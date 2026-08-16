from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from outlier_scrapers import feed_health
from outlier_scrapers.paths import LeaguePaths


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def _paths(root: Path) -> LeaguePaths:
    league_root = root / "MLB"
    return LeaguePaths(
        league="MLB",
        root=league_root,
        raw=league_root / "raw",
        normalized=league_root / "normalized",
        reports=league_root / "reports",
    )


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _stamp(value: datetime = NOW) -> str:
    return value.isoformat()


def _seed_healthy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LeaguePaths:
    paths = _paths(tmp_path)
    monkeypatch.setattr(feed_health, "league_paths", lambda _league: paths)

    _write(
        paths.reports / "props_export_status_latest.json",
        {
            "league": "MLB",
            "status": "ok",
            "generated_at": _stamp(),
            "record_count": 0,
            "schedule_event_fetch_requested_count": 0,
            "schedule_event_fetch_error_count": 0,
            "schedule_event_fetch_errors": [],
        },
    )
    _write(paths.props_normalized_latest(), {"generated_at": _stamp(), "records": []})

    _write(
        paths.reports / "games_status_latest.json",
        {
            "league": "MLB",
            "status": "ok",
            "generated_at": _stamp(),
            "record_count": 0,
            "target_event_count": 0,
            "matchup_fetch_requested_count": 0,
            "matchup_fetch_succeeded_count": 0,
            "markets_fetch_requested_count": 0,
            "markets_fetch_succeeded_count": 0,
            "insights_fetch_requested_count": 0,
            "insights_fetch_succeeded_count": 0,
            "injury_fetch_requested_count": 0,
            "injury_fetch_succeeded_count": 0,
            "fetch_error_count": 0,
            "fetch_errors": [],
        },
    )
    _write(paths.games_normalized_latest(), {"generated_at": _stamp(), "records": []})

    _write(
        paths.reports / "insights_status_latest.json",
        {
            "league": "MLB",
            "status": "ok",
            "generated_at": _stamp(),
            "record_count": 0,
        },
    )
    _write(
        paths.normalized / "mlb_insights_latest.json",
        {"generated_at": _stamp(), "records": []},
    )

    for prefix in ("", "games_"):
        _write(
            paths.reports / f"{prefix}line_movement_status_latest.json",
            {
                "league": "MLB",
                "status": "ok",
                "generated_at": _stamp(),
                "markets_requested": 0,
                "markets_fetched": 0,
                "fetch_error_count": 0,
                "error_market_ids": [],
            },
        )
    _write(paths.line_movement_latest(), {"generated_at": _stamp(), "records": []})
    _write(paths.games_line_movement_latest(), {"generated_at": _stamp(), "records": []})

    _write(
        paths.reports / "cards_status_latest.json",
        {
            "league": "MLB",
            "status": "ok",
            "generated_at": _stamp(),
            "missing_feeds": [],
            "coverage": {"props_markets": 0, "cards_total": 0},
        },
    )
    _write(paths.cards_latest(), {"generated_at": _stamp(), "board_a": [], "board_b": []})
    _write(
        paths.reports / "games_cards_status_latest.json",
        {
            "league": "MLB",
            "status": "ok",
            "generated_at": _stamp(),
            "missing_feeds": [],
            "coverage": {"games_markets": 0, "cards_total": 0},
        },
    )
    _write(
        paths.games_cards_latest(),
        {"generated_at": _stamp(), "board_a": [], "board_b": []},
    )
    return paths


def _build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, write: bool = False) -> dict:
    _seed_healthy(tmp_path, monkeypatch)
    return feed_health.build_feed_health("MLB", now=NOW, write=write)


def test_healthy_empty_slate_is_100_percent_and_writes_exact_v1_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _seed_healthy(tmp_path, monkeypatch)

    payload = feed_health.build_feed_health("mlb", now=NOW, write=True)

    assert tuple(payload) == feed_health.SERIALIZED_KEYS
    assert all(payload[field] == "ok" for field in feed_health.STATUS_FIELDS)
    assert payload["coverage_pct"] == 100.0
    assert payload["oldest_source_age"] == 0.0
    assert payload["latest_source_age"] == 0.0
    assert payload["failed_ids"] == []
    assert payload["schema_version"] == "1.0"
    assert feed_health.validate_feed_health(payload) == (True, [])
    written = json.loads((paths.reports / "feed_health_latest.json").read_text(encoding="utf-8"))
    assert written == payload
    assert list(paths.reports.glob(".feed_health_latest.json.*.tmp")) == []


@pytest.mark.parametrize(
    ("mutation", "expected_status"),
    [
        ("stale", "stale"),
        ("future", "error"),
        ("malformed", "error"),
        ("missing", "missing"),
    ],
)
def test_stale_future_malformed_and_missing_sources_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    expected_status: str,
) -> None:
    paths = _seed_healthy(tmp_path, monkeypatch)
    artifact = paths.props_normalized_latest()
    if mutation == "stale":
        _write(artifact, {"generated_at": _stamp(NOW - timedelta(hours=6, seconds=1))})
    elif mutation == "future":
        _write(artifact, {"generated_at": _stamp(NOW + timedelta(minutes=5, seconds=1))})
    elif mutation == "malformed":
        artifact.write_text("{not-json", encoding="utf-8")
    else:
        artifact.unlink()

    payload = feed_health.build_feed_health("MLB", now=NOW, write=False)

    assert payload["props_status"] == expected_status
    safe, reasons = feed_health.validate_feed_health(payload)
    assert not safe
    assert any(f"props_status is {expected_status}" in reason for reason in reasons)
    assert any(
        failure["feed"] == "props"
        and failure["id_type"] == "global"
        and failure["id"] == "*"
        for failure in payload["failed_ids"]
    )


def test_six_hour_and_five_minute_boundaries_are_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _seed_healthy(tmp_path, monkeypatch)
    _write(paths.props_normalized_latest(), {"generated_at": _stamp(NOW - timedelta(hours=6))})
    status = json.loads(
        (paths.reports / "props_export_status_latest.json").read_text(encoding="utf-8")
    )
    status["generated_at"] = _stamp(NOW + timedelta(minutes=5))
    _write(paths.reports / "props_export_status_latest.json", status)

    payload = feed_health.build_feed_health("MLB", now=NOW, write=False)

    assert payload["props_status"] == "ok"
    assert payload["oldest_source_age"] == 6.0
    assert feed_health.validate_feed_health(payload) == (True, [])


def test_partial_with_scoped_market_ids_is_safe_and_row_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _seed_healthy(tmp_path, monkeypatch)
    status_path = paths.reports / "line_movement_status_latest.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status.update(
        {
            "status": "partial",
            "markets_requested": 10,
            "markets_fetched": 9,
            "fetch_error_count": 2,
            "error_market_ids": ["market-b", "market-a", "market-b"],
            "first_pass_errors": [
                {"market_id": "market-a", "error": "404"},
                {"market_id": "market-b", "error": "timeout"},
            ],
        }
    )
    _write(status_path, status)

    payload = feed_health.build_feed_health("MLB", now=NOW, write=False)

    assert payload["line_movement_status"] == "partial"
    assert payload["coverage_pct"] == pytest.approx((600.0 + 90.0) / 7.0)
    line_failures = [f for f in payload["failed_ids"] if f["feed"] == "line_movement"]
    assert [(f["id"], f["reason"]) for f in line_failures] == [
        ("market-a", "404"),
        ("market-b", "timeout"),
    ]
    assert feed_health.validate_feed_health(payload) == (True, [])
    assert feed_health.matching_failures(payload, "props", {"market_id": "market-a"}) == [
        line_failures[0]
    ]
    assert feed_health.matching_failures(payload, "props", {"market_id": "other"}) == []
    assert feed_health.matching_failures(payload, "games", {"market_id": "market-a"}) == []


def test_partial_without_scoped_ids_is_unsafe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _seed_healthy(tmp_path, monkeypatch)
    status_path = paths.reports / "line_movement_status_latest.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status.update(
        {
            "status": "partial",
            "markets_requested": 10,
            "markets_fetched": 9,
            "fetch_error_count": 1,
            "error_market_ids": [],
        }
    )
    _write(status_path, status)

    payload = feed_health.build_feed_health("MLB", now=NOW, write=False)
    safe, reasons = feed_health.validate_feed_health(payload)

    assert payload["line_movement_status"] == "partial"
    assert not safe
    assert "line_movement_status is partial with an unscoped failure" in reasons
    assert feed_health.matching_failures(payload, "props", {"market_id": "anything"})


def test_games_insights_injuries_and_cards_aggregate_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _seed_healthy(tmp_path, monkeypatch)
    games_status_path = paths.reports / "games_status_latest.json"
    games_status = json.loads(games_status_path.read_text(encoding="utf-8"))
    games_status.update(
        {
            "status": "partial",
            "matchup_fetch_requested_count": 2,
            "matchup_fetch_succeeded_count": 1,
            "markets_fetch_requested_count": 4,
            "markets_fetch_succeeded_count": 4,
            "insights_fetch_requested_count": 2,
            "insights_fetch_succeeded_count": 1,
            "injury_fetch_requested_count": 4,
            "injury_fetch_succeeded_count": 3,
            "fetch_error_count": 3,
            "fetch_errors": [
                {"step": "matchup", "event_id": "event-a", "error": "matchup failed"},
                {"step": "insights", "event_id": "event-b", "error": "insights failed"},
                {"step": "injuries", "event_id": "event-b", "error": "injuries failed"},
            ],
        }
    )
    _write(games_status_path, games_status)

    player_cards = json.loads(
        (paths.reports / "cards_status_latest.json").read_text(encoding="utf-8")
    )
    player_cards["coverage"] = {"props_markets": 10, "cards_total": 8}
    player_cards["cards_requested_count"] = 10
    player_cards["cards_succeeded_count"] = 8
    _write(paths.reports / "cards_status_latest.json", player_cards)
    game_cards = json.loads(
        (paths.reports / "games_cards_status_latest.json").read_text(encoding="utf-8")
    )
    game_cards["coverage"] = {"games_markets": 10, "cards_total": 9}
    game_cards["cards_requested_count"] = 10
    game_cards["cards_succeeded_count"] = 9
    _write(paths.reports / "games_cards_status_latest.json", game_cards)

    payload = feed_health.build_feed_health("MLB", now=NOW, write=False)

    assert payload["games_status"] == "partial"
    assert payload["insights_status"] == "partial"
    assert payload["injuries_status"] == "partial"
    assert payload["cards_status"] == "ok"
    expected = (100 + (5 / 6 * 100) + 75 + 75 + 100 + 100 + 85) / 7
    assert payload["coverage_pct"] == pytest.approx(expected)
    safe, reasons = feed_health.validate_feed_health(payload)
    assert not safe
    assert any("below 90" in reason for reason in reasons)

    games_event_a = feed_health.matching_failures(payload, "games", {"event_id": "event-a"})
    assert [(item["feed"], item["id"]) for item in games_event_a] == [("games", "event-a")]
    games_event_b = feed_health.matching_failures(payload, "games", {"event_id": "event-b"})
    assert [(item["feed"], item["id"]) for item in games_event_b] == [
        ("injuries", "event-b"),
        ("insights", "event-b"),
    ]
    props_event_b = feed_health.matching_failures(payload, "props", {"event_id": "event-b"})
    assert [(item["feed"], item["id"]) for item in props_event_b] == [
        ("injuries", "event-b")
    ]


def test_card_coverage_ignores_upstream_market_to_filtered_card_ratio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _seed_healthy(tmp_path, monkeypatch)
    player_path = paths.reports / "cards_status_latest.json"
    player = json.loads(player_path.read_text(encoding="utf-8"))
    player["coverage"] = {"props_markets": 100, "cards_total": 10}
    _write(player_path, player)
    games_path = paths.reports / "games_cards_status_latest.json"
    games = json.loads(games_path.read_text(encoding="utf-8"))
    games["coverage"] = {"games_markets": 100, "cards_total": 20}
    _write(games_path, games)

    payload = feed_health.build_feed_health("MLB", now=NOW, write=False)

    assert payload["coverage_pct"] == 100.0
    assert feed_health.validate_feed_health(payload) == (True, [])


def test_serialized_age_range_uses_artifacts_not_status_envelopes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _seed_healthy(tmp_path, monkeypatch)
    status_path = paths.reports / "props_export_status_latest.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["generated_at"] = _stamp(NOW - timedelta(hours=5))
    _write(status_path, status)

    payload = feed_health.build_feed_health("MLB", now=NOW, write=False)

    assert payload["oldest_source_age"] == 0.0
    assert payload["latest_source_age"] == 0.0


def test_backward_tolerates_missing_optional_counters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _seed_healthy(tmp_path, monkeypatch)
    props_path = paths.reports / "props_export_status_latest.json"
    props = json.loads(props_path.read_text(encoding="utf-8"))
    props.pop("schedule_event_fetch_requested_count")
    _write(props_path, props)
    games_path = paths.reports / "games_status_latest.json"
    games = json.loads(games_path.read_text(encoding="utf-8"))
    for key in list(games):
        if key.endswith("_requested_count") or key.endswith("_succeeded_count"):
            games.pop(key)
    _write(games_path, games)

    payload = feed_health.build_feed_health("MLB", now=NOW, write=False)

    assert payload["coverage_pct"] == 100.0
    assert feed_health.validate_feed_health(payload) == (True, [])


def test_games_producer_error_with_complete_counters_stays_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _seed_healthy(tmp_path, monkeypatch)
    status_path = paths.reports / "games_status_latest.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["status"] = "error"
    status["error"] = "producer aborted after writing counters"
    _write(status_path, status)

    payload = feed_health.build_feed_health("MLB", now=NOW, write=False)

    assert payload["games_status"] == "error"
    assert payload["insights_status"] == "error"
    assert payload["injuries_status"] == "error"
    assert not feed_health.validate_feed_health(payload)[0]


def test_games_producer_error_with_scoped_rows_keeps_derived_lanes_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _seed_healthy(tmp_path, monkeypatch)
    status_path = paths.reports / "games_status_latest.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status.update(
        {
            "status": "error",
            "error": "producer aborted after partial enrichments",
            "insights_fetch_requested_count": 2,
            "insights_fetch_succeeded_count": 1,
            "injury_fetch_requested_count": 2,
            "injury_fetch_succeeded_count": 1,
            "fetch_errors": [
                {"step": "insights", "event_id": "event-insight", "error": "insight failed"},
                {"step": "injuries", "event_id": "event-injury", "error": "injury failed"},
            ],
        }
    )
    _write(status_path, status)

    payload = feed_health.build_feed_health("MLB", now=NOW, write=False)

    assert payload["insights_status"] == "error"
    assert payload["injuries_status"] == "error"
    assert not feed_health.validate_feed_health(payload)[0]


def test_mixed_scoped_and_unknown_games_errors_keep_unscoped_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _seed_healthy(tmp_path, monkeypatch)
    status_path = paths.reports / "games_status_latest.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status.update(
        {
            "status": "partial",
            "matchup_fetch_requested_count": 1,
            "matchup_fetch_succeeded_count": 0,
            "fetch_error_count": 2,
            "fetch_errors": [
                {"step": "matchup", "event_id": "event-a", "error": "matchup failed"},
                {
                    "step": "mystery",
                    "event_id": "event-b",
                    "error": "unknown producer step failed",
                },
            ],
        }
    )
    _write(status_path, status)

    payload = feed_health.build_feed_health("MLB", now=NOW, write=False)
    safe, reasons = feed_health.validate_feed_health(payload)

    assert not safe
    assert any(failure["id_type"] == "global" for failure in payload["failed_ids"])
    assert any("unscoped failure" in reason for reason in reasons)


def test_line_movement_legacy_error_rows_remain_scoped_without_final_id_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _seed_healthy(tmp_path, monkeypatch)
    status_path = paths.reports / "line_movement_status_latest.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status.update(
        {
            "status": "partial",
            "markets_requested": 10,
            "markets_fetched": 9,
            "fetch_error_count": 1,
            "first_pass_errors": [{"market_id": "legacy-market", "error": "timeout"}],
        }
    )
    status.pop("error_market_ids")
    _write(status_path, status)

    payload = feed_health.build_feed_health("MLB", now=NOW, write=False)

    assert feed_health.validate_feed_health(payload) == (True, [])
    assert feed_health.matching_failures(
        payload, "props", {"market_id": "legacy-market"}
    ) == [
        {
            "feed": "line_movement",
            "stream": "props",
            "id_type": "market_id",
            "id": "legacy-market",
            "reason": "timeout",
        }
    ]


def _valid_payload(*, coverage: float = 100.0) -> dict:
    return {
        "props_status": "ok",
        "games_status": "ok",
        "insights_status": "ok",
        "injuries_status": "ok",
        "line_movement_status": "ok",
        "game_line_movement_status": "ok",
        "cards_status": "ok",
        "coverage_pct": coverage,
        "oldest_source_age": 1.0,
        "latest_source_age": 0.5,
        "failed_ids": [],
        "schema_version": "1.0",
    }


def test_coverage_90_percent_boundary() -> None:
    assert feed_health.validate_feed_health(_valid_payload(coverage=90.0)) == (True, [])
    safe, reasons = feed_health.validate_feed_health(_valid_payload(coverage=89.999))
    assert not safe
    assert any("below 90" in reason for reason in reasons)


@pytest.mark.parametrize(
    ("oldest", "latest", "expected"),
    [
        (6.000001, 0.1, "exceeds 6h"),
        (None, None, "unavailable"),
    ],
)
def test_validate_rejects_stale_or_unavailable_artifact_ages(
    oldest: float | None, latest: float | None, expected: str
) -> None:
    payload = _valid_payload()
    payload["oldest_source_age"] = oldest
    payload["latest_source_age"] = latest

    safe, reasons = feed_health.validate_feed_health(payload)

    assert not safe
    assert any(expected in reason for reason in reasons)


@pytest.mark.parametrize("status", ["missing", "stale", "error"])
def test_hard_statuses_are_unsafe(status: str) -> None:
    payload = _valid_payload()
    payload["props_status"] = status
    safe, reasons = feed_health.validate_feed_health(payload)
    assert not safe
    assert f"props_status is {status}" in reasons


def test_validate_rejects_non_exact_or_unsorted_contract() -> None:
    payload = _valid_payload()
    payload["extra"] = True
    payload["failed_ids"] = [
        {
            "feed": "props",
            "stream": "props",
            "id_type": "market_id",
            "id": "z",
            "reason": "failed",
        },
        {
            "feed": "props",
            "stream": "props",
            "id_type": "market_id",
            "id": "a",
            "reason": "failed",
        },
    ]

    safe, reasons = feed_health.validate_feed_health(payload)

    assert not safe
    assert any("unexpected keys" in reason for reason in reasons)
    assert "failed_ids must be deduplicated and sorted" in reasons


def test_validate_deduplicates_by_structured_identity_not_reason() -> None:
    payload = _valid_payload()
    payload["failed_ids"] = [
        {
            "feed": "props",
            "stream": "props",
            "id_type": "event_id",
            "id": "event-a",
            "reason": "first failure",
        },
        {
            "feed": "props",
            "stream": "props",
            "id_type": "event_id",
            "id": "event-a",
            "reason": "second failure",
        },
    ]

    safe, reasons = feed_health.validate_feed_health(payload)

    assert not safe
    assert "failed_ids must be deduplicated and sorted" in reasons


def test_matching_failures_supports_all_ids_streams_aliases_and_empty_payload() -> None:
    failures = [
        {
            "feed": "cards",
            "stream": "games",
            "id_type": "global",
            "id": "*",
            "reason": "games cards failed",
        },
        {
            "feed": "games",
            "stream": "all",
            "id_type": "event_id",
            "id": "event-1",
            "reason": "event failed",
        },
        {
            "feed": "line_movement",
            "stream": "props",
            "id_type": "market_id",
            "id": "market-1",
            "reason": "market failed",
        },
        {
            "feed": "line_movement",
            "stream": "games",
            "id_type": "outcome_id",
            "id": "outcome-1",
            "reason": "outcome failed",
        },
        {
            "feed": "props",
            "stream": "props",
            "id_type": "player_id",
            "id": "player-1",
            "reason": "player failed",
        },
    ]
    payload = {"failed_ids": sorted(failures, key=lambda item: tuple(item.values()))}
    row = {
        "market_id": "market-1",
        "event_id": "event-1",
        "source_ref": {"outcome_id": "outcome-1", "player_id": "player-1"},
    }

    props = feed_health.matching_failures(payload, "player", row)
    assert {item["id_type"] for item in props} == {"market_id", "event_id", "player_id"}
    games = feed_health.matching_failures(payload, "game", row)
    assert {item["id_type"] for item in games} == {"global", "event_id", "outcome_id"}
    all_streams = feed_health.matching_failures(payload, "all", row)
    assert {item["id_type"] for item in all_streams} == {
        "global",
        "event_id",
        "market_id",
        "outcome_id",
        "player_id",
    }
    assert feed_health.matching_failures({}, "props", row) == []
    assert feed_health.matching_failures(None, "games", row) == []
    assert feed_health.matching_failures(payload, "unknown", row) == []


def test_is_feed_health_safe_combines_global_gate_and_row_matching() -> None:
    payload = _valid_payload()
    payload["line_movement_status"] = "partial"
    payload["failed_ids"] = [
        {
            "feed": "line_movement",
            "stream": "props",
            "id_type": "market_id",
            "id": "bad-market",
            "reason": "404",
        }
    ]

    assert feed_health.is_feed_health_safe(payload)
    assert feed_health.is_feed_health_safe(payload, "props", {"market_id": "good-market"})
    assert not feed_health.is_feed_health_safe(payload, "props", {"market_id": "bad-market"})
