import pytest

from outlier_scrapers import parlay_legs


def _leg(event="e1", market="m1", outcome="o1") -> dict:
    return {"event_id": event, "market_id": market, "outcome_id": outcome, "noise": "x"}


def test_encode_and_decode_round_trip():
    encoded = parlay_legs.encode_legs([_leg(), _leg("e2", "m2", "o2")])

    assert parlay_legs.decode_legs(encoded) == [
        {"event_id": "e1", "market_id": "m1", "outcome_id": "o1"},
        {"event_id": "e2", "market_id": "m2", "outcome_id": "o2"},
    ]


def test_encoding_refuses_a_leg_with_incomplete_identity():
    """A partial leg set would settle a parlay against a subset of its legs."""

    assert parlay_legs.encode_legs([_leg(), {"event_id": "e2", "market_id": "m2"}]) == ""
    assert parlay_legs.encode_legs([]) == ""


@pytest.mark.parametrize(
    "payload",
    ["", "not json", "{}", "[]", '[{"event_id": "e1"}]', '["e1"]', "null"],
)
def test_malformed_leg_payloads_decode_to_nothing(payload):
    assert parlay_legs.decode_legs(payload) == []


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        (["W", "W"], "W"),
        (["W", "L"], "L"),
        (["L", "L"], "L"),
        (["PUSH", "PUSH"], "PUSH"),
        (["W", "PUSH"], "W"),
        (["L", "PUSH"], "L"),
        (["W", "W", "W"], "W"),
    ],
)
def test_leg_results_combine_with_pushes_dropping_out(results, expected):
    assert parlay_legs.combine_results(results) == expected


@pytest.mark.parametrize("results", [["W", ""], ["", ""], ["W", None], ["W", "PENDING"], []])
def test_an_unsettled_leg_leaves_the_parlay_pending(results):
    assert parlay_legs.combine_results(results) is None


def test_a_pushed_leg_pays_at_one_so_the_payout_shrinks():
    assert parlay_legs.surviving_decimal(["W", "PUSH"], [2.0, 1.5]) == pytest.approx(2.0)
    assert parlay_legs.surviving_decimal(["W", "W"], [2.0, 1.5]) == pytest.approx(3.0)
    assert parlay_legs.surviving_decimal(["PUSH", "PUSH"], [2.0, 1.5]) == pytest.approx(1.0)


def test_a_surviving_leg_without_a_price_has_no_payout():
    assert parlay_legs.surviving_decimal(["W", "W"], [2.0, None]) is None
    assert parlay_legs.surviving_decimal(["W"], [2.0, 1.5]) is None
    # A pushed leg drops out, so its missing price does not block the payout.
    assert parlay_legs.surviving_decimal(["W", "PUSH"], [2.0, None]) == pytest.approx(2.0)


def test_parlay_pnl_by_result():
    assert parlay_legs.parlay_pnl("W", 2.0, 3.0) == pytest.approx(4.0)
    assert parlay_legs.parlay_pnl("L", 2.0, 3.0) == pytest.approx(-2.0)
    assert parlay_legs.parlay_pnl("PUSH", 2.0, 3.0) == 0.0
    assert parlay_legs.parlay_pnl("W", 0.0, 3.0) == 0.0
    assert parlay_legs.parlay_pnl("W", 2.0, None) is None
