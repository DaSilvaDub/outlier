"""Opponent K% / park factor fetchers + WNBA pack audit wiring."""

from __future__ import annotations

import pytest

from outlier_scrapers.pack import apply_shadow_projection
from outlier_scrapers.projections import (
    WNBA_MINUTES_HASH,
    enrich_probable_with_so_features,
    fetch_team_batter_k_rate,
    get_wnba_points_features,
    independent_projection_eligible,
    park_k_factor_for_venue_team,
    wnba_points_projection_record,
)


def test_fetch_team_batter_k_rate_from_statsapi_payload():
    def _fake(url: str) -> dict:
        assert "teams/138/stats" in url
        return {
            "stats": [
                {
                    "splits": [
                        {"stat": {"strikeOuts": 900, "plateAppearances": 4500}}
                    ]
                }
            ]
        }

    rate = fetch_team_batter_k_rate("STL", season=2026, fetch_json=_fake)
    assert rate == pytest.approx(0.2)


def test_park_k_factor_defaults_and_known_parks():
    assert park_k_factor_for_venue_team("COL") == pytest.approx(0.93)
    assert park_k_factor_for_venue_team("SD") == pytest.approx(1.05)
    assert park_k_factor_for_venue_team("ZZZ") == 1.0
    assert park_k_factor_for_venue_team("") is None


def test_enrich_attaches_opponent_and_park_context():
    by_team = {
        "STL": {
            "pitcher": "Michael McGreevy",
            "pitcher_id": 700241,
            "confirmed": True,
            "opponent": "CHC",
            "home_away": "HOME",
        },
        "CHC": {
            "pitcher": "Opponent Arm",
            "pitcher_id": 1,
            "confirmed": True,
            "opponent": "STL",
            "home_away": "AWAY",
        },
    }

    def _fake(url: str) -> dict:
        if "/people/" in url:
            return {
                "stats": [
                    {
                        "splits": [
                            {
                                "date": "2026-07-20",
                                "stat": {
                                    "strikeOuts": 6,
                                    "battersFaced": 24,
                                    "gamesStarted": 1,
                                },
                            },
                            {
                                "date": "2026-07-14",
                                "stat": {
                                    "strikeOuts": 5,
                                    "battersFaced": 22,
                                    "gamesStarted": 1,
                                },
                            },
                            {
                                "date": "2026-07-08",
                                "stat": {
                                    "strikeOuts": 7,
                                    "battersFaced": 23,
                                    "gamesStarted": 1,
                                },
                            },
                        ]
                    }
                ]
            }
        if "/teams/" in url and "/stats" in url:
            return {
                "stats": [
                    {
                        "splits": [
                            {"stat": {"strikeOuts": 1000, "plateAppearances": 4000}}
                        ]
                    }
                ]
            }
        raise AssertionError(url)

    enriched = enrich_probable_with_so_features(by_team, season=2026, fetch_json=_fake)
    assert enriched["STL"]["feature_source"] == "mlb_stats_gamelog"
    assert enriched["STL"]["opponent_k_rate"] == pytest.approx(0.25)
    assert enriched["STL"]["park_k_factor"] == pytest.approx(0.99)  # STL home
    assert enriched["CHC"]["park_team"] == "STL"  # away pitches at STL park


def test_wnba_features_from_espn_payloads():
    def _fake(url: str) -> dict:
        if "search" in url:
            return {
                "items": [
                    {
                        "id": "2998928",
                        "displayName": "Breanna Stewart",
                        "type": "player",
                        "league": "wnba",
                    }
                ]
            }
        if "gamelog" in url:
            return {
                "names": ["minutes", "points"],
                "seasonTypes": [
                    {
                        "categories": [
                            {
                                "events": [
                                    {"stats": ["34", "22"]},
                                    {"stats": ["32", "18"]},
                                    {"stats": ["30", "20"]},
                                    {"stats": ["28", "16"]},
                                ]
                            }
                        ]
                    }
                ],
            }
        raise AssertionError(url)

    cache: dict = {}
    features = get_wnba_points_features(
        "Breanna Stewart", season=2026, fetch_json=_fake, cache=cache
    )
    assert features is not None
    assert features["games"] == 4
    assert features["projected_minutes"] == pytest.approx((34 + 32 + 30 + 28) / 4)
    assert features["points_per_minute"] == pytest.approx((22 + 18 + 20 + 16) / (34 + 32 + 30 + 28))


def test_wnba_pack_shadow_is_audit_only():
    row = {
        "sport": "WNBA",
        "market_type": "PTS",
        "selection": "Breanna Stewart - Points OVER 20.5",
        "player": "Breanna Stewart",
        "event_id": "e1",
        "market_id": "m1",
        "outcome_id": "o1",
        "line": 20.5,
        "headline_side": "OVER",
        "independent_model_prob": "",
    }
    features = {
        "projected_minutes": 32.0,
        "points_per_minute": 0.7,
        "feature_source": "wnba_espn_gamelog",
        "games": 5,
    }
    generated = wnba_points_projection_record(row, features=features)
    assert generated is not None
    assert generated["feature_snapshot_hash"] == WNBA_MINUTES_HASH
    assert independent_projection_eligible(generated) is False
    flags = apply_shadow_projection(row, generated, "OVER")
    assert flags == []
    assert row["independent_model_prob"] == ""
    assert row["projection_feature_hash"] == WNBA_MINUTES_HASH
    assert row["projection_mean"] != ""
