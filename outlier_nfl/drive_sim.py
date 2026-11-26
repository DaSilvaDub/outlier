"""Run a seeded NFL regulation drive simulation from supplied matchup inputs."""
from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter
from pathlib import Path

TRIALS = 50_000
# Offensive TD with 0/1/2 conversion points, made FG, safety conceded, no score.
OUTCOMES = ((6, 0), (7, 0), (8, 0), (3, 0), (0, 2), (0, 0))


def simulate(
    home: list[float], away: list[float], drives: list[float], seed: int
) -> Counter[tuple[int, int]]:
    """Return joint regulation scores. Drive counts share a game pace draw."""
    for weights, size in ((home, len(OUTCOMES)), (away, len(OUTCOMES)), (drives, 7)):
        if (
            len(weights) != size
            or any(not math.isfinite(w) or w < 0 for w in weights)
            or not math.isclose(sum(weights), 1.0, rel_tol=0, abs_tol=1e-8)
        ):
            raise ValueError("Expected nonnegative probability vectors summing to one.")
    rng = random.Random(seed)
    pace = rng.choices(range(8, 15), drives, k=TRIALS)
    scores: Counter[tuple[int, int]] = Counter()
    for count in pace:
        home_score = away_score = 0
        for own, opponent in rng.choices(OUTCOMES, home, k=count):
            home_score += own
            away_score += opponent
        for own, opponent in rng.choices(OUTCOMES, away, k=count):
            away_score += own
            home_score += opponent
        scores[home_score, away_score] += 1
    return scores


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()
    inputs = json.loads(args.inputs.read_text(encoding="utf-8"))
    for key in ("event_id", "as_of", "source"):
        if not isinstance(inputs.get(key), str) or not inputs[key].strip():
            parser.error(f"Missing input provenance: {key}")
    scores = simulate(inputs["home"], inputs["away"], inputs["drives"], args.seed)
    home_mean = sum(h * n for (h, _), n in scores.items()) / TRIALS
    away_mean = sum(a * n for (_, a), n in scores.items()) / TRIALS
    print(json.dumps({
        "model": "regulation_drive_monte_carlo",
        "inputs": inputs,
        "seed": args.seed,
        "trials": TRIALS,
        "home_mean": home_mean,
        "away_mean": away_mean,
        "mean_home_spread": away_mean - home_mean,
        "mean_total": home_mean + away_mean,
        "home_lead_probability": sum(n for (h, a), n in scores.items() if h > a) / TRIALS,
        "away_lead_probability": sum(n for (h, a), n in scores.items() if h < a) / TRIALS,
        "tie_probability": sum(n for (h, a), n in scores.items() if h == a) / TRIALS,
        "joint_regulation_scores": [[h, a, n] for (h, a), n in sorted(scores.items())],
    }))


if __name__ == "__main__":
    main()
