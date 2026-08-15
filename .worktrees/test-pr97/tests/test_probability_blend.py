import json

import pytest

from outlier_scrapers import probability_blend


def _rows(*, market_type: str, wins: int, losses: int) -> list[dict]:
    base = {
        "captured_at": "2026-07-19T16:00:00+00:00",
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


def test_fit_artifact_learns_each_dimension_and_resolves_context():
    rows = _rows(market_type="PLAYER_PROP", wins=8, losses=2)
    rows += _rows(market_type="GAMELINE", wins=2, losses=8)
    artifact = probability_blend.fit_weight_artifact(
        rows, min_samples=5, prior_strength=0,
        generated_at="2026-07-20T00:00:00+00:00",
    )
    assert artifact["status"] == "active"
    assert artifact["dimensions"]["market_type"]["PLAYER_PROP"]["market_weight"] == 1.0
    assert artifact["dimensions"]["market_type"]["GAMELINE"]["market_weight"] == 0.0
    player = probability_blend.resolve_market_weight(
        artifact, {**rows[0], "captured_at": "2026-07-21T00:00:00+00:00"}
    )
    game = probability_blend.resolve_market_weight(
        artifact, {**rows[-1], "captured_at": "2026-07-21T00:00:00+00:00"}
    )
    assert player["market_weight"] > game["market_weight"]
    assert set(artifact["dimensions"]) == set(probability_blend.DIMENSIONS)


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
