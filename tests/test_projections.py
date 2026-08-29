import json
import math

import pytest

from outlier_scrapers.projections import (
    _projection_slate_date,
    build_mlb_so_projections,
    export_projections,
    mlb_first_inning_run_distribution,
    mlb_hits_allowed_distribution,
    mlb_so_projection_record,
    mlb_strikeout_distribution,
    mlb_total_bases_distribution,
    negative_binomial_distribution,
    parse_args,
    project_rows,
)


def assert_valid_distribution(distribution):
    assert all(probability >= 0 for probability in distribution.pmf.values())
    assert math.isclose(sum(distribution.pmf.values()) + distribution.tail_mass, 1.0, abs_tol=1e-9)
    assert distribution.mean >= 0
    assert distribution.variance >= 0


def test_negative_binomial_distribution_is_bounded_and_calibrated():
    distribution = negative_binomial_distribution(4.0, 8.0)
    assert_valid_distribution(distribution)
    assert distribution.mean == pytest.approx(4.0, abs=0.01)


def test_over_partition_includes_omitted_upper_tail_mass():
    distribution = negative_binomial_distribution(4.0, 8.0, maximum=4)
    partition = distribution.partition(3.5, "OVER")
    expected = distribution.tail_mass + distribution.pmf[4]
    assert partition["win_prob"] == pytest.approx(expected)
    assert sum(partition.values()) == pytest.approx(1.0)


def test_partition_rejects_line_beyond_truncated_support():
    distribution = negative_binomial_distribution(4.0, 8.0, maximum=4)
    with pytest.raises(ValueError, match="bounded PMF support"):
        distribution.partition(5.5, "OVER")


def test_strikeouts_mix_stochastic_workload():
    distribution = mlb_strikeout_distribution(18.0, 0.27)
    assert_valid_distribution(distribution)
    partition = distribution.partition(5.5, "OVER")
    assert partition["win_prob"] + partition["push_prob"] + partition["loss_prob"] == pytest.approx(1.0)


def test_hits_allowed_and_first_inning_models_emit_full_partitions():
    assert_valid_distribution(mlb_hits_allowed_distribution(20.0, 0.12))
    assert_valid_distribution(mlb_first_inning_run_distribution(0.42, 0.38))


def test_total_bases_has_zero_hurdle_and_compound_outcomes():
    distribution = mlb_total_bases_distribution(4, 0.65, {1: 0.65, 2: 0.25, 3: 0.05, 4: 0.05})
    assert_valid_distribution(distribution)
    assert distribution.pmf[0] == pytest.approx(0.65**4)
    assert distribution.pmf.get(1, 0.0) > 0


def test_projection_cli_contract():
    args = parse_args(["project", "--sport", "MLB", "--date", "2026-07-14"])
    assert args.command == "project"
    assert args.sport == "MLB"
    assert args.date == "2026-07-14"


def test_explicit_projection_date_does_not_relabel_a_stale_feed():
    with pytest.raises(ValueError, match="do not match projection target_date"):
        _projection_slate_date(
            {},
            [
                {
                    "sport_context": {
                        "event_starts_at": "2026-08-24T19:10:00-04:00"
                    }
                }
            ],
            "2026-08-25",
        )


def test_mlb_so_projection_only_emits_for_confirmed_starters():
    row = {
        "sport": "MLB",
        "market_type": "SO",
        "selection": "Jackson Jobe - Strikeouts OVER 4.5",
        "player": "Jackson Jobe",
        "event_id": "g1",
        "market_id": "m1",
        "outcome_id": "o1",
        "line": 4.5,
        "headline_side": "OVER",
    }
    confirmed = {"DET": {"pitcher": "Jackson Jobe", "confirmed": True}}
    record = mlb_so_projection_record(row, confirmed)
    assert record is not None
    assert record["distribution"]["win_prob"] > 0
    assert record["distribution"]["side"] == "OVER"
    assert mlb_so_projection_record(row, {"DET": {"pitcher": "Jackson Jobe", "confirmed": False}}) is None
    assert mlb_so_projection_record(row, {"DET": {"pitcher": "Tarik Skubal", "confirmed": True}}) is None
    reliever = {**row, "player": "Robert Stock", "selection": "Robert Stock - Strikeouts OVER 3.5"}
    assert mlb_so_projection_record(reliever, confirmed) is None


def test_project_rows_returns_auditable_probability_partition():
    projections = project_rows(
        [
            {
                "outcome_id": "k-1",
                "event_id": "game-1",
                "proposition": "PITCHER_STRIKEOUTS",
                "line": 5.5,
                "position": "OVER",
                "projection_features": {"projected_bf": 19, "strikeout_rate": 0.28},
            }
        ],
        "MLB",
    )
    assert projections[0]["status"] == "eligible"
    distribution = projections[0]["distribution"]
    assert distribution["win_prob"] + distribution["push_prob"] + distribution["loss_prob"] == pytest.approx(1.0)


def test_first_inning_yes_no_sides_map_to_over_under_partition():
    projection = project_rows(
        [
            {
                "outcome_id": "yrfi-1",
                "event_id": "game-1",
                "market_id": "first-inning",
                "proposition": "FIRST_INNING_RUN",
                "line": 0.5,
                "position": "YES",
                "projection_features": {"home_run_mean": 0.4, "away_run_mean": 0.35},
            }
        ],
        "MLB",
    )[0]
    assert projection["side"] == "YES"
    assert projection["distribution"]["side"] == "OVER"
    assert projection["distribution"]["push_prob"] == 0.0


def test_build_mlb_so_projections_only_keeps_eligible_rows():
    confirmed = {"DET": {"pitcher": "Jackson Jobe", "confirmed": True}}
    props_rows = [
        {
            "sport": "MLB",
            "market_type": "SO",
            "player": "Jackson Jobe",
            "event_id": "g1",
            "market_id": "m1",
            "outcome_id": "o1",
            "line": 4.5,
            "position": "OVER",
        },
        {
            "sport": "MLB",
            "market_type": "SO",
            "player": "Not A Starter",
            "event_id": "g2",
            "market_id": "m2",
            "outcome_id": "o2",
            "line": 3.5,
            "position": "UNDER",
        },
    ]
    records = build_mlb_so_projections(props_rows, confirmed)
    assert len(records) == 1
    assert records[0]["row_id"] == "o1"
    assert records[0]["status"] == "eligible"


def test_export_projections_skips_leagues_without_a_projection_model():
    status = export_projections("NHL")
    assert status == {
        "status": "skipped",
        "reason": "no independent projection model for NHL",
        "record_count": 0,
    }


def test_export_projections_missing_props_file_returns_error(tmp_path, monkeypatch):
    from outlier_scrapers.paths import LeaguePaths

    fake_paths = LeaguePaths(
        league="MLB",
        root=tmp_path,
        raw=tmp_path / "raw",
        normalized=tmp_path / "normalized",
        reports=tmp_path / "reports",
    )
    monkeypatch.setattr("outlier_scrapers.paths.league_paths", lambda league: fake_paths)

    status = export_projections("MLB")
    assert status["status"] == "error"
    assert status["record_count"] == 0


def test_export_projections_writes_normalized_projections_file(tmp_path, monkeypatch):
    from outlier_scrapers.paths import LeaguePaths

    fake_paths = LeaguePaths(
        league="MLB",
        root=tmp_path,
        raw=tmp_path / "raw",
        normalized=tmp_path / "normalized",
        reports=tmp_path / "reports",
    )
    fake_paths.normalized.mkdir(parents=True, exist_ok=True)
    fake_paths.props_normalized_latest().write_text(
        json.dumps(
            {
                "records": [
                    {
                        "sport": "MLB",
                        "market_type": "SO",
                        "player": "Jackson Jobe",
                        "event_id": "g1",
                        "market_id": "m1",
                        "outcome_id": "o1",
                        "line": 4.5,
                        "position": "OVER",
                    },
                    {
                        "sport": "MLB",
                        "market_type": "SO",
                        "player": "Not Confirmed",
                        "event_id": "g2",
                        "market_id": "m2",
                        "outcome_id": "o2",
                        "line": 3.5,
                        "position": "UNDER",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("outlier_scrapers.paths.league_paths", lambda league: fake_paths)
    monkeypatch.setattr(
        "outlier_scrapers.probable_pitchers.load_probable_pitcher_lookup",
        lambda league: {"DET": {"pitcher": "Jackson Jobe", "confirmed": True}},
    )

    status = export_projections("MLB", target_date="2026-08-25")
    assert status == {"status": "ok", "record_count": 1}
    assert fake_paths.projections_latest().exists()
    written = json.loads(fake_paths.projections_latest().read_text(encoding="utf-8"))
    assert written["sport"] == "MLB"
    assert written["date"] == "2026-08-25"
    assert len(written["projections"]) == 1
    assert written["projections"][0]["row_id"] == "o1"


def test_non_mlb_shadow_rows_preserve_input_cardinality_and_identity():
    rows = [
        {"outcome_id": "w1", "event_id": "g1", "market_id": "m1"},
        {"outcome_id": "w2", "event_id": "g1", "market_id": "m2"},
    ]
    projections = project_rows(rows, "WNBA")
    assert len(projections) == len(rows)
    assert [projection["row_id"] for projection in projections] == ["w1", "w2"]
    assert {projection["status"] for projection in projections} == {"shadow_only"}


def test_export_projections_writes_multimarket_wnba_artifact(tmp_path, monkeypatch):
    """WNBA gets its own <sport>_projections_latest.json, not an inline-only path."""

    from outlier_scrapers.paths import LeaguePaths
    from outlier_scrapers.projections import export_projections

    fake_paths = LeaguePaths(
        league="WNBA",
        root=tmp_path,
        raw=tmp_path / "raw",
        normalized=tmp_path / "normalized",
        reports=tmp_path / "reports",
    )
    fake_paths.normalized.mkdir(parents=True, exist_ok=True)
    fake_paths.props_normalized_latest().write_text(
        json.dumps(
                        {
                "records": [
                    {
                        "sport": "WNBA",
                        "market_type": "PTS",
                        "player": "Napheesa Collier",
                        "event_id": "g1",
                        "market_id": "m1",
                        "outcome_id": "o1",
                        "line": 19.5,
                        "position": "OVER",
                        "as_of": "2026-08-24T12:00:00-04:00",
                        "sport_context": {
                            "event_starts_at": "2026-08-25T00:30:00-04:00"
                        },
                    },
                    {
                        "sport": "WNBA",
                        "market_type": "REB",
                        "player": "Napheesa Collier",
                        "event_id": "g1",
                        "market_id": "m2",
                        "outcome_id": "o2",
                            "line": 8.5,
                            "position": "OVER",
                            "sport_context": {
                                "event_starts_at": "2026-08-25T00:30:00-04:00"
                            },
                        },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("outlier_scrapers.paths.league_paths", lambda league: fake_paths)
    monkeypatch.setattr(
        "outlier_scrapers.projections.get_wnba_player_features",
        lambda name, season, fetch_json=None, cache=None: {
            "projected_minutes": 32.0,
            "points_per_minute": 0.62,
            "rebounds_per_minute": 0.25,
            "assists_per_minute": 0.15,
        },
    )

    status = export_projections("WNBA")
    assert status == {"status": "ok", "record_count": 2}
    written = json.loads(fake_paths.projections_latest().read_text(encoding="utf-8"))
    assert written["sport"] == "WNBA"
    assert written["date"] == "2026-08-25"
    assert [record["row_id"] for record in written["projections"]] == ["o1", "o2"]
    assert written["projections"][0]["feature_snapshot_hash"] == "wnba-gamelog-stat-rates-v2"
    assert written["coverage"] == {
        "mode": "audit_only",
        "supported_opportunities": 2,
        "projected_opportunities": 2,
        "by_market": {
            "PTS": {"opportunities": 1, "projected": 1},
            "REB": {"opportunities": 1, "projected": 1},
            "AST": {"opportunities": 0, "projected": 0},
            "PR": {"opportunities": 0, "projected": 0},
            "PA": {"opportunities": 0, "projected": 0},
            "RA": {"opportunities": 0, "projected": 0},
            "PRA": {"opportunities": 0, "projected": 0},
        },
    }


def test_export_projections_requires_unambiguous_slate_date(tmp_path, monkeypatch):
    from outlier_scrapers.paths import LeaguePaths

    fake_paths = LeaguePaths(
        league="MLB",
        root=tmp_path,
        raw=tmp_path / "raw",
        normalized=tmp_path / "normalized",
        reports=tmp_path / "reports",
    )
    fake_paths.normalized.mkdir(parents=True, exist_ok=True)
    fake_paths.props_normalized_latest().write_text(
        json.dumps(
            {
                "records": [
                    {
                        "sport": "MLB",
                        "market_type": "SO",
                        "player": "Jackson Jobe",
                        "event_id": "g1",
                        "market_id": "m1",
                        "outcome_id": "o1",
                        "line": 4.5,
                        "position": "OVER",
                        "sport_context": {"event_starts_at": "2026-08-25T19:10:00-04:00"},
                    },
                    {
                        "sport": "MLB",
                        "market_type": "SO",
                        "player": "Tarik Skubal",
                        "event_id": "g2",
                        "market_id": "m2",
                        "outcome_id": "o2",
                        "line": 5.5,
                        "position": "OVER",
                        "sport_context": {"event_starts_at": "2026-08-26T19:10:00-04:00"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("outlier_scrapers.paths.league_paths", lambda league: fake_paths)
    monkeypatch.setattr(
        "outlier_scrapers.probable_pitchers.load_probable_pitcher_lookup",
        lambda league: {
            "DET": {"pitcher": "Jackson Jobe", "confirmed": True},
            "SEA": {"pitcher": "Tarik Skubal", "confirmed": True},
        },
    )

    status = export_projections("MLB")
    assert status["status"] == "error"
    assert "projection slate date is not safely derivable" in status["reason"]


def test_export_projections_filters_mixed_feed_to_explicit_target_date(tmp_path, monkeypatch):
    from outlier_scrapers.paths import LeaguePaths
    from outlier_scrapers.projections import export_projections

    fake_paths = LeaguePaths(
        league="MLB",
        root=tmp_path,
        raw=tmp_path / "raw",
        normalized=tmp_path / "normalized",
        reports=tmp_path / "reports",
    )
    fake_paths.normalized.mkdir(parents=True, exist_ok=True)
    fake_paths.props_normalized_latest().write_text(
        json.dumps(
            {
                "date": "2026-08-26",
                "records": [
                    {
                        "sport": "MLB",
                        "market_type": "SO",
                        "player": "Jackson Jobe",
                        "event_id": "g1",
                        "market_id": "m1",
                        "outcome_id": "o1",
                        "line": 4.5,
                        "position": "OVER",
                        "sport_context": {"event_starts_at": "2026-08-25T19:10:00-04:00"},
                    },
                    {
                        "sport": "MLB",
                        "market_type": "SO",
                        "player": "Tarik Skubal",
                        "event_id": "g2",
                        "market_id": "m2",
                        "outcome_id": "o2",
                        "line": 5.5,
                        "position": "OVER",
                        "sport_context": {"event_starts_at": "2026-08-26T19:10:00-04:00"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("outlier_scrapers.paths.league_paths", lambda league: fake_paths)
    monkeypatch.setattr(
        "outlier_scrapers.probable_pitchers.load_probable_pitcher_lookup",
        lambda league: {
            "DET": {"pitcher": "Jackson Jobe", "confirmed": True},
            "SEA": {"pitcher": "Tarik Skubal", "confirmed": True},
        },
    )

    status = export_projections("MLB", target_date="2026-08-26")
    assert status["status"] == "ok"
    written = json.loads(fake_paths.projections_latest().read_text(encoding="utf-8"))
    assert written["date"] == "2026-08-26"
    assert "o1" not in [record.get("row_id") for record in written["projections"]]


def test_wnba_projection_build_fetches_once_per_player_and_skips_unsupported_markets(
    monkeypatch,
):
    """Caching is per player, not per outcome, across supported market families."""

    import outlier_scrapers.projections as projections_module
    from outlier_scrapers.projections import build_wnba_projections

    resolved: list[str] = []
    gamelogs: list[str] = []

    def fake_resolve(player_name, *, fetch_json=None):
        resolved.append(player_name)
        return f"id-{player_name}"

    def fake_gamelog(athlete_id, *, season, fetch_json=None):
        gamelogs.append(str(athlete_id))
        return [30.0, 31.0, 29.0], {
            "points": [18.0, 20.0, 16.0],
            "rebounds": [8.0, 7.0, 9.0],
            "assists": [5.0, 6.0, 4.0],
        }

    monkeypatch.setattr(projections_module, "resolve_wnba_athlete_id", fake_resolve)
    monkeypatch.setattr(projections_module, "fetch_wnba_athlete_gamelog", fake_gamelog)

    rows = [
        {
            "sport": "WNBA",
            "market_type": "PTS",
            "player": "A Player" if index < 4 else "B Player",
            "outcome_id": f"o{index}",
            "line": 15.5,
            "position": "OVER" if index % 2 else "UNDER",
        }
        for index in range(6)
    ] + [
        {
            "sport": "WNBA",
            "market_type": "AST",
            "player": "C Player",
            "outcome_id": "o9",
            "line": 4.5,
            "position": "OVER",
        }
    ] + [
        {
            "sport": "WNBA",
            "market_type": "BLK",
            "player": "D Player",
            "outcome_id": "o10",
            "line": 1.5,
            "position": "OVER",
        }
    ]
    records = build_wnba_projections(rows, season=2026)

    assert len(records) == 7
    assert {record["sport"] for record in records} == {"WNBA"}
    assert {record["market"] for record in records} == {"PTS", "AST"}
    # Seven supported rows, three distinct players: one resolution and one
    # gamelog fetch each. The unsupported BLK row never reaches the model.
    assert len(resolved) == 3
    assert len(gamelogs) == 3
    assert "D Player" not in resolved


def test_wnba_combination_projection_sums_component_rates_but_stays_audit_only():
    from outlier_scrapers.projections import (
        independent_projection_eligible,
        wnba_projection_record,
    )

    features = {
        "projected_minutes": 30.0,
        "points_per_minute": 0.5,
        "rebounds_per_minute": 0.2,
        "assists_per_minute": 0.1,
    }
    base = {
        "sport": "WNBA",
        "player": "A Player",
        "event_id": "g1",
        "line": 20.5,
        "position": "OVER",
    }

    record = wnba_projection_record(
        {**base, "market": "PRA", "market_id": "m1", "outcome_id": "o1"},
        features=features,
    )

    assert record is not None
    assert record["market"] == "PRA"
    assert record["distribution"]["mean"] == pytest.approx(24.0)
    assert independent_projection_eligible(record) is False


def test_wnba_gamelog_parser_reads_points_rebounds_and_assists_aliases():
    from outlier_scrapers.projections import fetch_wnba_athlete_gamelog

    payload = {
        "names": ["minutes", "PTS", "totalRebounds", "AST"],
        "seasonTypes": [
            {
                "categories": [
                    {
                        "events": [
                            {"stats": ["31", "20", "9", "6"]},
                            {"stats": ["29", "18", "7", "5"]},
                        ]
                    }
                ]
            }
        ],
    }

    minutes, stats = fetch_wnba_athlete_gamelog(
        "123", season=2026, fetch_json=lambda url: payload
    )

    assert minutes == [31.0, 29.0]
    assert stats == {
        "points": [20.0, 18.0],
        "rebounds": [9.0, 7.0],
        "assists": [6.0, 5.0],
    }


