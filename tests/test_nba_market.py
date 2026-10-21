import math

import pytest

from outlier_nba.market.consensus import (
    DFS_BOOKS,
    BookQuote,
    consensus_two_way,
    median,
)
from outlier_nba.market.devig import (
    DevigError,
    american_to_decimal,
    american_to_prob,
    devig,
    hold,
    multiplicative,
    power,
    shin,
)
from outlier_nba.market.ladders import (
    Rung,
    ladder_from_consensus,
    monotonicity_violations,
    push_win_loss,
)


# ---- odds conversion -------------------------------------------------------
def test_american_conversions():
    assert american_to_prob(-110) == pytest.approx(110 / 210)
    assert american_to_prob(+150) == pytest.approx(0.4)
    assert american_to_prob("+100") == pytest.approx(0.5)
    assert american_to_prob("−120") == pytest.approx(120 / 220)  # unicode minus
    assert american_to_decimal(-110) == pytest.approx(1 + 100 / 110)
    assert american_to_decimal(+150) == pytest.approx(2.5)


@pytest.mark.parametrize("bad", [None, "", "abc", 0, "0", float("nan"), 50, -50, 99, -99])
def test_american_rejects_unparseable_and_impossible_prices(bad):
    assert american_to_prob(bad) is None
    assert american_to_decimal(bad) is None


# ---- devig methods ---------------------------------------------------------
@pytest.mark.parametrize("method", ["multiplicative", "shin", "power"])
def test_symmetric_market_devigs_to_even(method):
    p = devig([american_to_prob(-110), american_to_prob(-110)], method)
    assert p == pytest.approx([0.5, 0.5])


@pytest.mark.parametrize("method", ["multiplicative", "shin", "power"])
def test_devig_output_sums_to_one_and_preserves_order(method):
    q = [american_to_prob(-250), american_to_prob(+200)]
    p = devig(q, method)
    assert sum(p) == pytest.approx(1.0)
    assert p[0] > p[1]
    assert all(0 < x < 1 for x in p)
    assert p[0] < q[0]  # the favourite's vig-inclusive quote overstates it


def test_multiplicative_known_value():
    # -200 / +170: q = 0.6667 + 0.3704 = 1.0370
    q = [200 / 300, 100 / 270]
    assert multiplicative(q) == pytest.approx([q[0] / sum(q), q[1] / sum(q)])


def test_methods_disagree_on_favourite_as_documented():
    q = [american_to_prob(-600), american_to_prob(+420)]
    m, s, pw = multiplicative(q)[0], shin(q)[0], power(q)[0]
    # Longshot-loaded margin: multiplicative gives the favourite the least, power the most.
    assert m < s < pw


def test_power_solves_exponent_exactly():
    q = [0.55, 0.52]
    p = power(q)
    k = math.log(p[0]) / math.log(q[0])
    assert p[1] == pytest.approx(q[1] ** k)


def test_shin_two_way_matches_equal_margin_closed_form():
    # For two outcomes Shin collapses to the equal-margin (additive) estimator.
    q = [0.58, 0.48]
    expected = [x - (sum(q) - 1) / 2 for x in q]
    assert shin(q) == pytest.approx(expected, abs=1e-9)


def test_shin_falls_back_to_multiplicative_on_arbitrage():
    q = [0.45, 0.50]  # sums to 0.95: no informed-money interpretation exists
    assert shin(q) == pytest.approx(multiplicative(q))


def test_fair_book_is_returned_unchanged():
    assert shin([0.6, 0.4]) == pytest.approx([0.6, 0.4])
    assert power([0.6, 0.4]) == pytest.approx([0.6, 0.4])


def test_hold():
    assert hold([american_to_prob(-110)] * 2) == pytest.approx(1 - 1 / (2 * 110 / 210))
    assert hold([0.4, 0.5]) < 0


@pytest.mark.parametrize("bad", [[0.5], [0.0, 1.0], [0.5, float("nan")], [-0.1, 0.5], [1.2, 0.3]])
def test_devig_rejects_malformed_quotes(bad):
    with pytest.raises(DevigError):
        devig(bad, "multiplicative")


def test_unknown_method_is_rejected():
    with pytest.raises(DevigError):
        devig([0.5, 0.5], "additive-ish")


# ---- consensus -------------------------------------------------------------
def _quote(book, o, u):
    return BookQuote(book, o, u)


def test_median_even_and_odd():
    assert median([0.1, 0.9, 0.5]) == 0.5
    assert median([0.2, 0.4]) == pytest.approx(0.3)


def test_consensus_median_of_per_book_devig_not_of_raw_odds():
    quotes = [
        _quote("DraftKings", -115, -105),
        _quote("FanDuel", -110, -110),
        _quote("Caesars", -105, -115),
        _quote("BetMGM", -150, +120),  # outlier book
    ]
    c = consensus_two_way(quotes, method="multiplicative")
    per_book = sorted(
        multiplicative([american_to_prob(a), american_to_prob(b)])[0]
        for a, b in [(-115, -105), (-110, -110), (-105, -115), (-150, 120)]
    )
    expected = (per_book[1] + per_book[2]) / 2
    assert c.n_books == 4 and not c.thin_market
    # median-then-renormalize: p_a + p_b == 1
    assert c.p_a + c.p_b == pytest.approx(1.0)
    assert c.p_a == pytest.approx(expected, abs=0.01)
    assert c.flags == []


def test_thin_market_when_fewer_than_three_books():
    c = consensus_two_way([_quote("A", -110, -110), _quote("B", -120, 100)])
    assert c.thin_market and c.n_books == 2 and "THIN_MARKET" in c.flags


def test_dfs_pickem_books_are_excluded_from_the_consensus():
    quotes = [
        _quote("DraftKings", -110, -110),
        _quote("FanDuel", -110, -110),
        _quote("Caesars", -110, -110),
        _quote("PrizePicks", 100, 100),  # flat payout, not a priced market
        _quote("Underdog", -137, -105),
        _quote("Sleeper", 100, 100),
    ]
    c = consensus_two_way(quotes)
    assert c.n_books == 3 and not c.thin_market
    assert {"prizepicks", "underdog", "sleeper"} <= DFS_BOOKS
    assert set(c.excluded_books) == {"PrizePicks", "Underdog", "Sleeper"}


def test_placeholder_prices_are_dropped():
    quotes = [
        _quote("A", -110, -110),
        _quote("B", -110, -110),
        _quote("C", -110, -110),
        _quote("D", -100000, +100000),  # stub price: devigs to ~0.999
        _quote("E", None, -110),  # missing side
        _quote("F", "junk", -110),
    ]
    c = consensus_two_way(quotes)
    assert c.n_books == 3
    assert set(c.dropped_books) == {"D", "E", "F"}


def test_no_usable_books_gives_no_probability():
    c = consensus_two_way([_quote("A", None, None)])
    assert c.p_a is None and c.p_b is None and c.n_books == 0
    assert "NO_USABLE_BOOKS" in c.flags


def test_consensus_method_is_recorded_and_default_is_shin():
    quotes = [_quote(b, -130, +110) for b in "ABC"]
    assert consensus_two_way(quotes).method == "shin"
    assert consensus_two_way(quotes, method="power").method == "power"


def test_consensus_best_prices_are_per_side_max_decimal():
    quotes = [_quote("A", -110, -110), _quote("B", -105, -120), _quote("C", -120, +100)]
    c = consensus_two_way(quotes)
    assert c.best_a == (-105, "B")
    assert c.best_b == (100, "C")


# ---- ladders / integer-line push -------------------------------------------
def test_integer_line_push_from_adjacent_half_lines():
    ladder = {23.5: 0.55, 24.5: 0.47, 25.5: 0.39}
    wlp = push_win_loss(24.0, ladder)
    # over 24 wins if > 24 (i.e. >= 24.5): p(o24.5); loses if < 24 (<= 23): 1 - p(o23.5)
    assert wlp is not None
    win, push, loss = wlp
    assert win == pytest.approx(0.47)
    assert push == pytest.approx(0.55 - 0.47)
    assert loss == pytest.approx(1 - 0.55)
    assert win + push + loss == pytest.approx(1.0)


def test_half_line_has_no_push_and_integer_without_neighbours_is_none():
    assert push_win_loss(24.5, {24.5: 0.47}) == (0.47, 0.0, pytest.approx(0.53))
    assert push_win_loss(24.0, {23.5: 0.55}) is None
    assert push_win_loss(24.0, {}) is None


def test_non_monotone_ladder_is_flagged_and_push_refused():
    ladder = {23.5: 0.40, 24.5: 0.47}  # P(over) must fall as the line rises
    assert monotonicity_violations(ladder) == [(23.5, 24.5)]
    assert push_win_loss(24.0, ladder) is None


def test_ladder_from_consensus_uses_only_two_sided_rungs():
    rungs = [
        Rung(23.5, [_quote(b, -150, +125) for b in "ABC"]),
        Rung(24.5, [_quote(b, +110, -130) for b in "ABC"]),
        Rung(25.5, [_quote(b, +200, None) for b in "ABC"]),
    ]
    ladder, flags = ladder_from_consensus(rungs, method="multiplicative")
    assert set(ladder) == {23.5, 24.5}
    assert ladder[23.5] > ladder[24.5]
    assert flags[25.5] == ["ONE_SIDED_NO_DEVIG"]
