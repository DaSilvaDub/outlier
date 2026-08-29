import json

import pytest

from outlier_scrapers import probability_blend


def _rows(
    *, market_type: str, wins: int, losses: int, captured_at: str = "2026-07-19T16:00:00+00:00"
) -> list[dict]:
    base = {
        "captured_at": captured_at,
        "event_starts_at": "2026-07-19T20:00:00+00:00",
        "sport": "WNBA",
        "market_type": market_type,
        "price": -110,
        "data_quality_tier": "HIGH",
        "market_consensus_prob": 0.8,
        "independent_model_prob": 0.2,
        "push_prob": 0.0,
    }
    return [
        {**base, "win_loss_push": result}
        for result in (["W"] * wins + ["L"] * losses)
    ]


def test_fit_market_weight_minimizes_brier_loss():
    pairs = [(0.6, 0.4, 1.0)] * 54 + [(0.6, 0.4, 0.0)] * 46
    fitted = probability_blend.fit_market_weight(pairs)
    assert fitted["market_weight"] == pytest.approx(0.7)
    assert fitted["model_weight"] == pytest.approx(0.3)


def test_fit_artifact_floors_segments_without_oos_evidence():
    """Without a chronological spread to hold out, no segment can prove OOS
    improvement, so every learned weight is floored rather than trusted at
    its raw in-sample fit (Phase 1: stop overriding the market on noise)."""
    rows = _rows(market_type="PLAYER_PROP", wins=8, losses=2)
    rows += _rows(market_type="GAMELINE", wins=2, losses=8)
    artifact = probability_blend.fit_weight_artifact(
        rows, min_samples=5, prior_strength=0,
        generated_at="2026-07-20T00:00:00+00:00",
    )
    assert artifact["status"] == "active"
    assert artifact["global_holdout"] is None
    assert artifact["dimensions"]["market_type"]["PLAYER_PROP"]["market_weight"] == 1.0
    gameline = artifact["dimensions"]["market_type"]["GAMELINE"]
    assert gameline["market_weight"] == probability_blend.DEFAULT_MARKET_WEIGHT_FLOOR
    assert gameline["raw_market_weight"] == 0.0
    assert gameline["holdout"] is None
    player = probability_blend.resolve_market_weight(
        artifact, {**rows[0], "captured_at": "2026-07-21T00:00:00+00:00"}
    )
    game = probability_blend.resolve_market_weight(
        artifact, {**rows[-1], "captured_at": "2026-07-21T00:00:00+00:00"}
    )
    assert player["market_weight"] > game["market_weight"]
    assert set(artifact["dimensions"]) == set(probability_blend.DIMENSIONS)


def test_fit_artifact_unlocks_below_floor_with_oos_improvement():
    """A segment with enough chronologically spread history that the model
    actually beats the market out-of-sample is allowed below the floor."""
    days = [f"2026-0{7 if d < 22 else 8}-{d:02d}T16:00:00+00:00" for d in range(1, 31)]
    rows: list[dict] = []
    for day in days:
        # independent model (0.2) is consistently right; market (0.8) is wrong.
        for _ in range(3):
            row_batch = _rows(market_type="PLAYER_PROP", wins=0, losses=1, captured_at=day)
            for row in row_batch:
                row["event_starts_at"] = day.replace("T16:00:00", "T20:00:00")
            rows += row_batch
    artifact = probability_blend.fit_weight_artifact(
        rows, min_samples=5, prior_strength=0, holdout_fraction=0.3,
        generated_at="2026-09-01T00:00:00+00:00",
    )
    assert artifact["status"] == "active"
    assert artifact["global_holdout"] is not None
    assert artifact["global_holdout"]["improved"] is True
    assert artifact["global"]["market_weight"] < probability_blend.DEFAULT_MARKET_WEIGHT_FLOOR


def test_fit_artifact_quarantines_corrupt_column_alignment_rows():
    """Rows carrying the 2026-07 column-misalignment corruption (market_type
    or time_before_game landing as a stringified percentile) must never train
    the blend."""
    clean = _rows(market_type="PLAYER_PROP", wins=8, losses=2)
    corrupt = _rows(market_type="50.0", wins=100, losses=0)
    for row in corrupt:
        row["time_before_game"] = "25.0"
    artifact = probability_blend.fit_weight_artifact(
        clean + corrupt, min_samples=5, prior_strength=0,
        generated_at="2026-07-20T00:00:00+00:00",
    )
    assert artifact["eligible_samples"] == len(clean)
    assert "50.0" not in artifact["dimensions"]["market_type"]


def test_insufficient_or_post_start_history_stays_market_only():
    rows = _rows(market_type="PLAYER_PROP", wins=1, losses=1)
    for row in rows:
        row["event_starts_at"] = "2026-07-19T15:00:00+00:00"
    artifact = probability_blend.fit_weight_artifact(rows, min_samples=1)
    assert artifact["status"] == "insufficient_history"
    assert artifact["eligible_samples"] == 0


def test_zero_prior_is_honored_and_future_artifact_fails_closed():
    artifact = {
        "schema_version": 1,
        "status": "active",
        "generated_at": "2026-07-20T00:00:00+00:00",
        "model_version": "blend-test",
        "prior_strength": 0,
        "global": {"market_weight": 1.0, "n": 100},
        "dimensions": {"market_type": {"PLAYER_PROP": {"market_weight": 0.0, "n": 30}}},
    }
    context = {
        "captured_at": "2026-07-21T00:00:00+00:00",
        "sport": "MLB", "market_type": "PLAYER_PROP", "price": -110,
        "data_quality_tier": "HIGH",
    }
    assert probability_blend.resolve_market_weight(artifact, context)["market_weight"] == 0.0
    future = probability_blend.resolve_market_weight(
        artifact, {**context, "captured_at": "2026-07-19T00:00:00+00:00"}
    )
    assert future["market_weight"] == 1.0
    assert future["source"] == "market_only_artifact_cutoff"


def test_weight_artifact_round_trip(tmp_path):
    artifact = probability_blend.fit_weight_artifact(
        _rows(market_type="PLAYER_PROP", wins=8, losses=2), min_samples=5,
        generated_at="2026-07-20T00:00:00+00:00",
    )
    output = probability_blend.write_weight_artifact(artifact, tmp_path / "weights.json")
    assert probability_blend.load_weight_artifact(output) == json.loads(output.read_text())


@pytest.mark.parametrize(
    ("hours", "expected"),
    [(-0.1, "POST_START"), (0.5, "0_TO_1H"), (4, "1_TO_6H"),
     (12, "6_TO_24H"), (36, "24H_PLUS")],
)
def test_time_before_game_buckets(hours, expected):
    assert probability_blend.time_before_game_bucket(hours) == expected
