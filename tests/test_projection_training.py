import json
import random
from datetime import date, timedelta

import pytest

from outlier_scrapers import projections
from outlier_scrapers import projection_training as training


def _splits(count: int, *, batters_faced: int = 24, strikeouts: int = 6, start_day: int = 1):
    return [
        {
            "date": (date(2026, 5, 1) + timedelta(days=5 * index)).isoformat(),
            "stat": {
                "gamesStarted": 1,
                "battersFaced": batters_faced,
                "strikeOuts": strikeouts + index,
            },
        }
        for index in range(start_day - 1, start_day - 1 + count)
    ]


def _synthetic_samples(pitchers: int = 40, starts: int = 18, seed: int = 11):
    rng = random.Random(seed)
    samples = []
    for pitcher_id in range(1, pitchers + 1):
        true_rate = min(0.40, max(0.10, rng.gauss(0.24, 0.04)))
        splits = []
        for index in range(starts):
            batters_faced = max(8, int(round(rng.gauss(23, 3))))
            strikeouts = sum(1 for _ in range(batters_faced) if rng.random() < true_rate)
            splits.append(
                {
                    "date": (date(2026, 4, 1) + timedelta(days=4 * index)).isoformat(),
                    "stat": {
                        "gamesStarted": 1,
                        "battersFaced": batters_faced,
                        "strikeOuts": strikeouts,
                    },
                }
            )
        samples.extend(
            training.build_walk_forward_samples(splits, pitcher_id=pitcher_id, season=2026)
        )
    samples.sort(key=lambda row: (row["date"], row["pitcher_id"]))
    return samples


def _write_samples(path, samples):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for sample in samples:
            handle.write(json.dumps(sample, sort_keys=True) + "\n")
    return path


def test_walk_forward_samples_never_include_their_own_start():
    samples = training.build_walk_forward_samples(_splits(6), pitcher_id=660271, season=2026)
    # First three starts cannot be featurized (min_starts=3), and each remaining
    # sample's features must be built from strictly earlier starts only.
    assert [sample["date"] for sample in samples] == ["2026-05-16", "2026-05-21", "2026-05-26"]
    first = samples[0]
    assert first["starts"] == 3
    assert first["total_k"] == 6 + 7 + 8
    assert first["actual_so"] == 9
    assert all(sample["feature_schema_hash"] == training.FEATURE_SCHEMA_HASH for sample in samples)


def test_walk_forward_samples_skip_relief_appearances():
    splits = _splits(4)
    splits.append(
        {
            "date": "2026-06-01",
            "stat": {"gamesStarted": 0, "battersFaced": 6, "strikeOuts": 3},
        }
    )
    samples = training.build_walk_forward_samples(splits, pitcher_id=1, season=2026)
    assert "2026-06-01" not in {sample["date"] for sample in samples}


def test_backfill_writes_dataset_from_public_stats_endpoints(tmp_path):
    roster = {
        "roster": [
            {"person": {"id": 660271}, "position": {"code": "1", "abbreviation": "P"}},
            {"person": {"id": 592450}, "position": {"code": "8", "abbreviation": "CF"}},
        ]
    }
    logs = {"stats": [{"splits": _splits(6)}]}
    requested: list[str] = []

    def fake_fetch(url: str):
        requested.append(url)
        return roster if "/roster" in url else logs

    output = tmp_path / "samples.jsonl"
    status = training.backfill(
        "MLB",
        start="2026-05-01",
        end="2026-05-31",
        output=output,
        fetch_json=fake_fetch,
        team_codes=["DET"],
    )
    assert status["status"] == "ok"
    assert status["pitcher_count"] == 1  # the position player is not crawled
    assert status["sample_count"] == 3
    written = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert {row["pitcher_id"] for row in written} == {660271}
    assert any("gameLog" in url for url in requested)


def test_backfill_survives_a_dead_pitcher_endpoint(tmp_path):
    roster = {"roster": [{"person": {"id": 1}, "position": {"code": "1"}}]}

    def fake_fetch(url: str):
        if "/roster" in url:
            return roster
        raise TimeoutError("statsapi timeout")

    status = training.backfill(
        "MLB",
        start="2026-05-01",
        end="2026-05-31",
        output=tmp_path / "samples.jsonl",
        fetch_json=fake_fetch,
        team_codes=["DET"],
    )
    assert status["status"] == "ok"
    assert status["fetch_error_count"] == 1
    assert status["sample_count"] == 0


def test_backfill_rejects_an_inverted_window(tmp_path):
    with pytest.raises(training.TrainingError):
        training.backfill(
            "MLB",
            start="2026-05-31",
            end="2026-05-01",
            output=tmp_path / "samples.jsonl",
            fetch_json=lambda url: {},
        )


def test_train_fits_league_priors_and_stays_unpromoted(tmp_path):
    dataset = _write_samples(tmp_path / "samples.jsonl", _synthetic_samples())
    artifact = training.train(
        "MLB",
        as_of="2026-06-15",
        dataset=dataset,
        output=tmp_path / "model.json",
        min_samples=50,
        refine_sample_cap=25,
    )
    assert artifact["status"] == "trained"
    assert artifact["promoted"] is False
    assert artifact["model_version"]
    assert artifact["feature_schema_hash"] == training.FEATURE_SCHEMA_HASH
    # Priors are recovered from the samples, not copied from the shipped defaults.
    assert 0.15 < artifact["parameters"]["league_strikeout_rate"] < 0.32
    assert 15.0 < artifact["parameters"]["starter_projected_bf"] < 30.0
    assert artifact["metrics"]["train_count_nll"] <= artifact["metrics"]["baseline_count_nll"]
    # Nothing after the cutoff may be used for fitting.
    assert artifact["trained_through"] == "2026-06-15"
    assert artifact["n_train"] == sum(
        1 for row in _synthetic_samples() if row["date"] < "2026-06-15"
    )


def test_train_reports_insufficient_samples_instead_of_a_bogus_fit(tmp_path):
    dataset = _write_samples(
        tmp_path / "samples.jsonl",
        training.build_walk_forward_samples(_splits(6), pitcher_id=1, season=2026),
    )
    artifact = training.train(
        "MLB", as_of="2026-07-01", dataset=dataset, output=tmp_path / "model.json"
    )
    assert artifact["status"] == "insufficient_samples"
    assert artifact["promoted"] is False
    assert artifact["parameters"] == projections.default_so_model_params()


def test_train_without_a_dataset_says_how_to_build_one(tmp_path):
    with pytest.raises(training.TrainingError, match="backfill"):
        training.train("MLB", dataset=tmp_path / "missing.jsonl")


def test_validate_scores_only_samples_after_the_training_cutoff(tmp_path):
    samples = _synthetic_samples()
    dataset = _write_samples(tmp_path / "samples.jsonl", samples)
    model = tmp_path / "model.json"
    training.train(
        "MLB",
        as_of="2026-05-20",
        dataset=dataset,
        output=model,
        min_samples=50,
        refine_sample_cap=25,
    )
    report = training.validate(
        "MLB",
        artifact=model,
        dataset=dataset,
        output=tmp_path / "validation.json",
        min_samples=25,
    )
    holdout = sum(1 for row in samples if row["date"] >= "2026-05-20")
    assert report["metrics"]["n"] == holdout
    assert report["verdict"] in {"pass", "fail"}
    assert report["metrics"]["count_nll"] > 0
    assert 0.0 <= report["metrics"]["over_under_brier"] <= 1.0
    assert set(report["metrics"]["pit_coverage"]) == {"q10", "q25", "q50", "q75", "q90"}
    assert report["baseline_metrics"]["n"] == holdout
    assert json.loads((tmp_path / "validation.json").read_text(encoding="utf-8"))["verdict"]


def test_validate_reports_insufficient_data_rather_than_passing(tmp_path):
    samples = _synthetic_samples()
    dataset = _write_samples(tmp_path / "samples.jsonl", samples)
    model = tmp_path / "model.json"
    training.train(
        "MLB",
        as_of="2026-05-20",
        dataset=dataset,
        output=model,
        min_samples=50,
        refine_sample_cap=25,
    )
    report = training.validate(
        "MLB",
        artifact=model,
        dataset=dataset,
        output=tmp_path / "validation.json",
        min_samples=10**6,
    )
    assert report["verdict"] == "insufficient_data"


def test_validate_rejects_an_incompatible_artifact(tmp_path):
    dataset = _write_samples(tmp_path / "samples.jsonl", _synthetic_samples(pitchers=5))
    model = tmp_path / "model.json"
    model.write_text(
        json.dumps(
            {
                "schema_version": training.SCHEMA_VERSION,
                "status": "trained",
                "feature_schema_hash": "not-the-current-schema",
                "trained_through": "2026-05-20",
                "model_version": "abc",
                "parameters": projections.default_so_model_params(),
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(training.TrainingError, match="feature schema"):
        training.validate("MLB", artifact=model, dataset=dataset, output=tmp_path / "v.json")


def test_promotion_requires_a_passing_validation_report(tmp_path):
    dataset = _write_samples(tmp_path / "samples.jsonl", _synthetic_samples())
    model = tmp_path / "model.json"
    training.train(
        "MLB",
        as_of="2026-05-20",
        dataset=dataset,
        output=model,
        min_samples=50,
        refine_sample_cap=25,
    )
    report_path = tmp_path / "validation.json"
    with pytest.raises(training.TrainingError, match="validation report"):
        training.promote("MLB", artifact=model, validation=report_path, actor="tester")

    report = json.loads(
        json.dumps(
            training.validate(
                "MLB", artifact=model, dataset=dataset, output=report_path, min_samples=25
            )
        )
    )
    report["verdict"] = "fail"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(training.TrainingError, match="verdict"):
        training.promote("MLB", artifact=model, validation=report_path, actor="tester")

    report["verdict"] = "pass"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    result = training.promote("MLB", artifact=model, validation=report_path, actor="tester")
    assert result["status"] == "promoted"
    promoted = json.loads(model.read_text(encoding="utf-8"))
    assert promoted["promoted"] is True
    audit = promoted["promotion_audit"][-1]
    assert audit["actor"] == "tester"
    assert audit["model_version"] == promoted["model_version"]
    assert audit["promoted_at"]
