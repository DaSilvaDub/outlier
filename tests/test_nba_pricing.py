"""Team-market pricing from simulated draws, the blend, and the promotion stub."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from outlier_nba.pricing import blend, team_markets as tm
from outlier_nba.sim.engine import SimDraws
from outlier_nba.track import promotion


def _draws(home, away, *, tile: int = 100) -> SimDraws:
    """Repeat a short pattern ``tile`` times so exact fractions survive the draw minimum."""
    h = np.tile(np.asarray(home, dtype=np.int64), tile)[None, :]
    a = np.tile(np.asarray(away, dtype=np.int64), tile)[None, :]
    n = h.shape[1]
    return SimDraws(home=h, away=a, ot=np.zeros_like(h), poss=np.full((1, n), 99.0))


def _big() -> SimDraws:
    rng = np.random.default_rng(0)
    home = rng.integers(95, 130, 2000)
    away = rng.integers(95, 130, 2000)
    away = np.where(away == home, away + 1, away)
    return _draws(home, away)


def test_moneyline_probabilities_sum_to_one() -> None:
    p = tm.price_moneyline(_big())
    assert p["HOME"].p_win + p["AWAY"].p_win == pytest.approx(1.0)
    assert p["HOME"].p_push == 0.0


def test_spread_push_is_priced_not_lumped_into_a_side() -> None:
    d = _draws([103, 100, 97, 110], [100, 100 + 5, 100, 100])  # margins +3, -5, -3, +10
    out = tm.price_spread(d, home_line=-3.0)  # home must win by more than 3
    h, a = out["HOME"], out["AWAY"]
    assert (h.p_win, h.p_push, h.p_loss) == (0.25, 0.25, 0.5)  # +10 covers; +3 pushes
    assert (a.p_win, a.p_push, a.p_loss) == (0.5, 0.25, 0.25)
    assert h.p_win + h.p_push + h.p_loss == pytest.approx(1.0)


def test_half_point_spread_has_no_push() -> None:
    d = _draws([103, 100, 97, 110], [100, 105, 100, 100])
    out = tm.price_spread(d, home_line=-2.5)
    assert out["HOME"].p_push == 0.0 and out["HOME"].p_win == 0.5


def test_total_over_under_and_integer_push() -> None:
    d = _draws([110, 100, 120, 105], [100, 100, 100, 95])  # totals 210, 200, 220, 200
    out = tm.price_total(d, 200.0)
    assert out["OVER"].p_win == 0.5 and out["UNDER"].p_win == 0.0 and out["OVER"].p_push == 0.5
    assert tm.price_total(d, 200.5)["UNDER"].p_win == 0.5


def test_team_total_uses_that_teams_points() -> None:
    d = _draws([110, 100, 120, 105], [90, 100, 90, 95])
    home = tm.price_team_total(d, "HOME", 104.5)
    away = tm.price_team_total(d, "AWAY", 104.5)
    assert home["OVER"].p_win == 0.75 and away["OVER"].p_win == 0.0


def test_spread_total_and_team_total_cannot_contradict() -> None:
    """Same draws feed all three: a home team total over X plus away under Y implies a total."""
    d = _big()
    total = tm.price_total(d, 225.5)["OVER"].p_win
    ht = tm.price_team_total(d, "HOME", 0.5)["OVER"].p_win
    assert ht == 1.0  # every draw has the home team scoring
    assert 0.0 < total < 1.0
    # home margin > 0 is exactly the moneyline, and -0.5 spread is the same bet
    assert tm.price_spread(d, -0.5)["HOME"].p_win == tm.price_moneyline(d)["HOME"].p_win


def test_ladder_probabilities_are_monotone_in_the_line() -> None:
    d = _big()
    lines = [215.5, 220.5, 225.5, 230.5, 235.5]
    ladder = tm.price_ladder(d, "TOTAL", lines)
    overs = [ladder[x]["OVER"].p_win for x in lines]
    assert overs == sorted(overs, reverse=True)


def test_ev_is_push_aware() -> None:
    p = tm.Priced(p_win=0.45, p_push=0.10, p_loss=0.45, se=0.01, n_draws=1000)
    # +100 (decimal 2.0): win 0.45 - lose 0.45 = 0; a refunded push changes nothing
    assert p.ev(2.0) == pytest.approx(0.0)
    assert p.ev(2.2) == pytest.approx(0.45 * 1.2 - 0.45)
    assert p.fair_decimal == pytest.approx(1 + 0.45 / 0.45)


def test_nan_or_empty_draws_fail_closed() -> None:
    bad = SimDraws(
        home=np.array([[100.0, np.nan]]), away=np.array([[90.0, 91.0]]),
        ot=np.zeros((1, 2)), poss=np.ones((1, 2)),
    )  # fmt: skip
    with pytest.raises(tm.PricingError):
        tm.price_moneyline(bad)
    with pytest.raises(tm.PricingError):
        tm.price_moneyline(_draws([], [], tile=1))


def test_too_few_draws_are_refused() -> None:
    with pytest.raises(tm.PricingError, match="draws"):
        tm.price_moneyline(_draws([100] * 50, [90] * 50, tile=1))


# ------------------------------------------------------------------------------ blend


def test_zero_weight_returns_the_market_probability() -> None:
    f = blend.final_probability(p_model=0.62, p_market=0.55, weight=0.0)
    assert f.p_final == 0.55 and f.flags == ()


def test_missing_market_probability_fails_closed() -> None:
    f = blend.final_probability(p_model=0.62, p_market=None, weight=0.0)
    assert f.p_final is None and "MISSING_MARKET_PROB" in f.flags


def test_positive_weight_needs_the_model_too() -> None:
    assert blend.final_probability(None, 0.55, 0.2).p_final is None
    f = blend.final_probability(0.65, 0.55, 0.5)
    assert 0.55 < f.p_final < 0.65


def test_weight_outside_unit_interval_is_rejected() -> None:
    with pytest.raises(ValueError):
        blend.final_probability(0.6, 0.55, 1.5)


@pytest.mark.parametrize("bad", [float("nan"), 0.0, 1.0, -0.2])
def test_degenerate_probabilities_fail_closed(bad) -> None:
    assert blend.final_probability(0.6, bad, 0.0).p_final is None


# ------------------------------------------------------------------------- promotion


def test_initialised_config_starts_in_shadow_with_zero_weight(tmp_path: Path) -> None:
    path = tmp_path / "nba_promotion.json"
    promotion.initialize(path)
    for fam in promotion.TEAM_FAMILIES:
        assert promotion.status(fam, path) == "shadow"
        assert promotion.model_weight(fam, path) == 0.0


def test_hand_edited_config_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "nba_promotion.json"
    promotion.initialize(path)
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg["families"]["team.ML"]["status"] = "promoted"
    cfg["families"]["team.ML"]["model_weight"] = 0.9
    path.write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(promotion.PromotionTamperedError):
        promotion.model_weight("team.ML", path)


def test_missing_config_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(promotion.PromotionTamperedError):
        promotion.model_weight("team.ML", tmp_path / "absent.json")


def _passing(**kw) -> promotion.Evidence:
    base = {"n_settled": 2000, "distinct_dates": 80, "calibration_gap": 0.01,
            "oos_logloss_beats_market": True, "median_clv": 0.002, "fitted_weight": 0.4}  # fmt: skip
    return promotion.Evidence(**{**base, **kw})


def test_evaluator_promotes_only_when_every_key_passes(tmp_path: Path) -> None:
    path = tmp_path / "p.json"
    promotion.initialize(path)
    promotion.evaluate({"team.ML": _passing()}, path)
    assert promotion.status("team.ML", path) == "promoted"
    assert 0.0 < promotion.model_weight("team.ML", path) <= promotion.MAX_MODEL_WEIGHT


@pytest.mark.parametrize(
    "kw",
    [
        {"n_settled": 100},
        {"distinct_dates": 5},
        {"calibration_gap": 0.08},
        {"oos_logloss_beats_market": False},
        {"oos_logloss_beats_market": None},  # no market comparison possible: not promotable
        {"median_clv": -0.01},
        {"median_clv": None},
    ],
)
def test_any_failed_or_unknown_key_blocks_promotion(tmp_path: Path, kw) -> None:
    path = tmp_path / "p.json"
    promotion.initialize(path)
    promotion.evaluate({"team.ML": _passing(**kw)}, path)
    assert promotion.status("team.ML", path) == "shadow"
    assert promotion.model_weight("team.ML", path) == 0.0


def test_a_failed_reevaluation_demotes(tmp_path: Path) -> None:
    path = tmp_path / "p.json"
    promotion.initialize(path)
    promotion.evaluate({"team.ML": _passing()}, path)
    promotion.evaluate({"team.ML": _passing(calibration_gap=0.2)}, path)
    assert promotion.status("team.ML", path) == "shadow"


def test_unknown_family_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "p.json"
    promotion.initialize(path)
    with pytest.raises(KeyError):
        promotion.status("team.NOPE", path)


def test_committed_config_is_valid_and_all_shadow() -> None:
    path = Path(__file__).resolve().parent.parent / "config" / "nba_promotion.json"
    for fam in promotion.TEAM_FAMILIES:
        assert promotion.status(fam, path) == "shadow"
        assert promotion.model_weight(fam, path) == 0.0
