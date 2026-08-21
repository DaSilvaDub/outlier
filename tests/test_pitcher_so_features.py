"""Pitcher-specific SO features from MLB Stats API game logs."""

from __future__ import annotations

import pytest

from outlier_scrapers.projections import (
    GAMELOG_SO_HASH,
    LEAGUE_AVG_SO_HASH,
    compute_starter_so_features_from_logs,
    independent_projection_eligible,
    mlb_so_projection_record,
)


def _start(*, so: int, bf: int, ip: str = "6.0", gs: int = 1, date: str = "2026-07-01") -> dict:
    return {
        "date": date,
        "stat": {
            "strikeOuts": so,
            "battersFaced": bf,
            "inningsPitched": ip,
            "gamesStarted": gs,
        },
    }


def test_compute_starter_so_features_uses_recent_starts_only():
    logs = [
        _start(so=6, bf=24, date="2026-07-20"),
        _start(so=5, bf=22, date="2026-07-14"),
        _start(so=7, bf=23, date="2026-07-08"),
        _start(so=1, bf=8, gs=0, date="2026-07-05"),  # relief — ignore
        _start(so=4, bf=20, date="2026-07-01"),
    ]
    features = compute_starter_so_features_from_logs(logs, min_starts=3, max_starts=3)
    assert features is not None
    assert features["starts"] == 3
    assert features["projected_bf"] == pytest.approx((24 + 22 + 23) / 3)
    assert features["strikeout_rate"] == pytest.approx((6 + 5 + 7) / (24 + 22 + 23))
    assert features["total_bf"] == 69
    assert features["total_k"] == 18


def test_compute_starter_so_features_fail_closed_on_thin_sample():
    logs = [_start(so=6, bf=24), _start(so=5, bf=22)]
    assert compute_starter_so_features_from_logs(logs, min_starts=3) is None


def test_mlb_so_projection_uses_gamelog_features_as_independent():
    row = {
        "sport": "MLB",
        "market_type": "SO",
        "selection": "Michael McGreevy - Strikeouts OVER 5.5",
        "player": "Michael McGreevy",
        "event_id": "g1",
        "market_id": "m1",
        "outcome_id": "o1",
        "line": 5.5,
        "headline_side": "OVER",
    }
    probable = {
        "STL": {
            "pitcher": "Michael McGreevy",
            "confirmed": True,
            "pitcher_id": 700241,
            "projected_bf": 22.5,
            "strikeout_rate": 0.27,
            "feature_starts": 5,
            "feature_source": "mlb_stats_gamelog",
        }
    }
    record = mlb_so_projection_record(row, probable)
    assert record is not None
    assert record["feature_snapshot_hash"] == GAMELOG_SO_HASH
    assert independent_projection_eligible(record) is True
    assert record["distribution"]["win_prob"] > 0


def test_mlb_so_projection_without_pitcher_features_stays_league_avg_audit():
    row = {
        "sport": "MLB",
        "market_type": "SO",
        "selection": "Michael McGreevy - Strikeouts OVER 5.5",
        "player": "Michael McGreevy",
        "event_id": "g1",
        "market_id": "m1",
        "outcome_id": "o1",
        "line": 5.5,
        "headline_side": "OVER",
    }
    probable = {"STL": {"pitcher": "Michael McGreevy", "confirmed": True, "pitcher_id": 700241}}
    record = mlb_so_projection_record(row, probable)
    assert record is not None
    assert record["feature_snapshot_hash"] == LEAGUE_AVG_SO_HASH
    assert independent_projection_eligible(record) is False


def test_enrich_probable_with_so_features_attaches_gamelog_rates():
    from outlier_scrapers.projections import enrich_probable_with_so_features

    by_team = {
        "STL": {
            "pitcher": "Michael McGreevy",
            "pitcher_id": 700241,
            "confirmed": True,
        },
        "CHC": {
            "pitcher": None,
            "pitcher_id": None,
            "confirmed": False,
        },
    }

    def _fake_fetch(_url: str) -> dict:
        return {
            "stats": [
                {
                    "splits": [
                        {
                            "date": "2026-07-20",
                            "stat": {"strikeOuts": 6, "battersFaced": 24, "gamesStarted": 1},
                        },
                        {
                            "date": "2026-07-14",
                            "stat": {"strikeOuts": 5, "battersFaced": 22, "gamesStarted": 1},
                        },
                        {
                            "date": "2026-07-08",
                            "stat": {"strikeOuts": 7, "battersFaced": 23, "gamesStarted": 1},
                        },
                    ]
                }
            ]
        }

    enriched = enrich_probable_with_so_features(by_team, season=2026, fetch_json=_fake_fetch)
    assert enriched["STL"]["feature_source"] == "mlb_stats_gamelog"
    assert enriched["STL"]["starts"] == 3
    assert enriched["STL"]["projected_bf"] == pytest.approx((24 + 22 + 23) / 3)
    assert "projected_bf" not in enriched["CHC"]


def test_compute_starter_so_features_fail_closed_on_low_bf():
    logs = [
        _start(so=1, bf=10, date="2026-07-20"),
        _start(so=1, bf=10, date="2026-07-14"),
        _start(so=1, bf=10, date="2026-07-08"),
    ]
    assert compute_starter_so_features_from_logs(logs, min_starts=3, min_total_bf=45) is None


def test_compute_starter_so_features_caps_at_max_starts():
    logs = [
        _start(so=6, bf=24, date=f"2026-07-{day:02d}")
        for day in range(1, 10)
    ]
    features = compute_starter_so_features_from_logs(logs, min_starts=3, max_starts=8)
    assert features is not None
    assert features["starts"] == 8


def test_enrich_probable_fail_closed_on_fetch_error():
    from outlier_scrapers.projections import enrich_probable_with_so_features

    by_team = {
        "STL": {
            "pitcher": "Michael McGreevy",
            "pitcher_id": 700241,
            "confirmed": True,
        }
    }

    def _boom(_url: str) -> dict:
        raise TimeoutError("statsapi timeout")

    enriched = enrich_probable_with_so_features(by_team, season=2026, fetch_json=_boom)
    assert "projected_bf" not in enriched["STL"]
    assert enriched["STL"]["pitcher_id"] == 700241


def test_mlb_so_projection_requires_feature_source_for_independent():
    row = {
        "sport": "MLB",
        "market_type": "SO",
        "selection": "Michael McGreevy - Strikeouts OVER 5.5",
        "player": "Michael McGreevy",
        "event_id": "g1",
        "market_id": "m1",
        "outcome_id": "o1",
        "line": 5.5,
        "headline_side": "OVER",
    }
    probable = {
        "STL": {
            "pitcher": "Michael McGreevy",
            "confirmed": True,
            "pitcher_id": 700241,
            "projected_bf": 22.5,
            "strikeout_rate": 0.27,
            # missing feature_source — must stay audit-only
        }
    }
    record = mlb_so_projection_record(row, probable)
    assert record is not None
    assert record["feature_snapshot_hash"] == LEAGUE_AVG_SO_HASH
    assert independent_projection_eligible(record) is False
