import csv
import json
from unittest import mock
from pathlib import Path

import pytest

from outlier_scrapers import paths as P
from outlier_scrapers.pack import (
    CANDIDATES_HEADER,
    _opportunity_key,
    _restore_published_pack,
    _summarize_lm_status,
    _swap_staged_pack,
    american_to_decimal,
    build_briefing,
    build_dossier,
    build_freshness_section,
    build_injuries,
    build_pack,
    build_pack_with_coverage,
    build_row,
    index_projections,
    index_ev_by_outcome,
    is_excluded_market,
    is_longshot_price,
    is_no_push_market,
    load_projection_records,
    market_validation_flags,
    rank_rows,
    select_date,
    write_pack,
)
from outlier_scrapers.sizing import compute_historical_edge

SOURCE_TS = {"cards": "CT", "line_movement": "LMT", "props": "PT"}


def test_opportunity_key_normalizes_lines_and_preserves_zero_identity():
    numeric = {
        "sport": "WNBA",
        "event_id": "e1",
        "market_id": "m1",
        "outcome_id": 0,
        "selection": "OVER 10",
        "line": 10.0,
    }
    serialized = {**numeric, "outcome_id": "0", "line": "10"}

    assert _opportunity_key(numeric) == _opportunity_key(serialized)
    assert _opportunity_key(numeric)[3] == "0"


def make_row(
    card,
    ev_records,
    sport="MLB",
    event_starts=None,
    injuries=None,
    projections=None,
    blend_artifact=None,
    odds_ts="ODDS_TS",
):
    return build_row(
        card,
        ev_records,
        index_ev_by_outcome(ev_records),
        sport,
        odds_ts,
        "NORM_TS",
        SOURCE_TS,
        event_starts or {},
        injuries or {},
        projections or {},
        blend_artifact,
    )


def ev_card(side="OVER", outcome_id="o1", line=None, fallback=False, devig=2.0, **extra):
    sv = {"outcome_id": outcome_id, "line": line, "best_odds": -110}
    sv["ev"] = {
        "is_alt_line_fallback": fallback,
        "devig_decimal": devig,
        "best_ev_pct": 0.05,
        "kelly_pct": 0.02,
    }
    card = {
        "headline_side": side,
        "card_id": "m1",
        "market_id": "m1",
        "sides": {side: sv},
        "board": "A",
    }
    card.update(extra)
    return card


# 1. Header is canonical and includes the dedicated flags column.
def test_header_canonical_with_flags():
    assert CANDIDATES_HEADER[0] == "sport"
    assert CANDIDATES_HEADER[-1] == "source_timestamps"
    assert "sizing_flags" in CANDIDATES_HEADER
    assert "edge_pct" in CANDIDATES_HEADER
    # flags column sits right after the sizing block
    assert (
        CANDIDATES_HEADER[CANDIDATES_HEADER.index("recommended_units_pre_news") + 1]
        == "sizing_flags"
    )
    # data_quality_flags sits right after sizing_flags
    assert (
        CANDIDATES_HEADER[CANDIDATES_HEADER.index("sizing_flags") + 1]
        == "data_quality_flags"
    )
    # human-readable context columns are surfaced to the desk
    for col in (
        "matchup", "team", "team_name", "opponent", "opp_name",
        "home_away", "market_label", "priced_line",
    ):
        assert col in CANDIDATES_HEADER
    for col in (
        "independent_push_prob",
        "independent_edge_pct",
        "projection_distribution",
        "projection_mean",
        "projection_variance",
        "projection_quantiles",
        "projection_model_version",
        "projection_feature_hash",
        "projection_quality_flags",
        "blend_market_weight",
        "blend_model_weight",
        "blend_weight_source",
        "blend_model_version",
        "blend_segment",
        "data_quality_tier",
        "odds_range",
        "time_before_game",
        "hours_before_game",
    ):
        assert col in CANDIDATES_HEADER


# 2. EV happy path: book_decimal_odds present, no-push -> fully sized.
def test_ev_row_sized():
    card = ev_card(market_type="MONEYLINE", market="MONEYLINE")
    ev = [
        {
            "market_id": "m1",
            "outcome_id": "o1",
            "book": "FD",
            "book_odds": 110,
            "book_decimal_odds": 2.1,
            "calculated_ev_pct": 0.05,
        }
    ]
    row = make_row(card, ev)
    assert row["model_prob"] == 0.5
    assert row["outcome_id"] == "o1"
    assert row["market_consensus_prob"] == 0.5
    assert row["independent_model_prob"] == ""
    assert row["final_blended_prob"] == 0.5
    assert row["decimal_price"] == 2.1
    assert row["price"] == 110  # same row as the chosen book, not card best_odds
    assert row["book"] == "FD"
    assert isinstance(row["edge_pct"], float)
    assert row["recommended_units_pre_news"] == 1.0
    assert row["sizing_flags"] == ""


def test_shadow_projection_populates_reserved_fields_without_changing_consensus_or_sizing():
    card = ev_card(
        line=5.5,
        market_type="PLAYER_PROP",
        market="K",
        event_id="game-1",
    )
    ev = [
        {
            "market_id": "m1",
            "outcome_id": "o1",
            "book": "FD",
            "book_odds": 110,
            "book_decimal_odds": 2.1,
            "calculated_ev_pct": 0.05,
        }
    ]
    baseline = make_row(card, ev)
    projection = {
        "status": "eligible",
        "sport": "MLB",
        "row_id": "o1",
        "event_id": "game-1",
        "market_id": "m1",
        "line": 5.5,
        "side": "OVER",
        "feature_snapshot_hash": "features-123",
        "distribution": {
            "line": 5.5,
            "side": "OVER",
            "win_prob": 0.62,
            "push_prob": 0.0,
            "mean": 6.1,
            "variance": 4.2,
            "quantiles": {"0.5": 6, "0.9": 9},
            "model_version": "projection-v1",
        },
    }
    row = make_row(card, ev, projections={"o1": projection})

    assert row["independent_model_prob"] == 0.62
    assert row["independent_push_prob"] == 0.0
    assert row["independent_edge_pct"] == pytest.approx((0.62 - 1 / 2.1) * 100)
    assert row["projection_model_version"] == "projection-v1"
    assert row["projection_feature_hash"] == "features-123"
    assert row["projection_quality_flags"] == ""
    for field in (
        "model_prob",
        "market_consensus_prob",
        "final_blended_prob",
        "edge_pct",
        "kelly_025_units",
        "recommended_units_pre_news",
        "actionable",
    ):
        assert row[field] == baseline[field]


def test_active_learned_blend_updates_final_probability_and_sizing():
    card = ev_card(
        line=5.5, market_type="PLAYER_PROP", market="K", event_id="game-1"
    )
    ev = [{
        "market_id": "m1",
        "outcome_id": "o1",
        "book": "FD",
        "book_odds": 110,
        "book_decimal_odds": 2.1,
    }]
    projection = {
        "status": "eligible",
        "sport": "MLB",
        "row_id": "o1",
        "event_id": "game-1",
        "market_id": "m1",
        "line": 5.5,
        "side": "OVER",
        "distribution": {
            "line": 5.5,
            "side": "OVER",
            "win_prob": 0.62,
            "push_prob": 0.0,
        },
    }
    artifact = {
        "schema_version": 1,
        "status": "active",
        "generated_at": "2026-07-19T00:00:00+00:00",
        "model_version": "blend-test",
        "prior_strength": 30,
        "global": {"market_weight": 0.7, "n": 100},
        "dimensions": {},
    }

    row = make_row(
        card,
        ev,
        event_starts={"game-1": "2026-07-20T02:00:00+00:00"},
        projections={"o1": projection},
        blend_artifact=artifact,
        odds_ts="2026-07-20T00:00:00+00:00",
    )

    expected = 0.7 * 0.5 + 0.3 * 0.62
    assert row["final_blended_prob"] == pytest.approx(expected)
    assert row["model_prob"] == pytest.approx(expected)
    assert row["blend_market_weight"] == pytest.approx(0.7)
    assert row["blend_model_weight"] == pytest.approx(0.3)
    assert row["blend_weight_source"] == "learned:global"
    assert row["model_prob_source"] == "learned_blend:blend-test"
    assert row["edge_pct"] != pytest.approx((0.5 - 1 / 2.1) * 100)


def test_shadow_projection_mismatch_fails_closed_without_touching_consensus():
    card = ev_card(line=5.5, market_type="PLAYER_PROP", market="K", event_id="game-1")
    ev = [{
        "market_id": "m1",
        "outcome_id": "o1",
        "book": "FD",
        "book_odds": 110,
        "book_decimal_odds": 2.1,
    }]
    projection = {
        "status": "eligible",
        "row_id": "o1",
        "event_id": "game-1",
        "market_id": "m1",
        "line": 6.5,
        "side": "OVER",
        "distribution": {"win_prob": 0.62, "push_prob": 0.0, "line": 6.5, "side": "OVER"},
    }
    row = make_row(card, ev, projections={"o1": projection})
    assert row["independent_model_prob"] == ""
    assert row["projection_quality_flags"] == "projection_line_mismatch"
    assert row["market_consensus_prob"] == row["final_blended_prob"] == row["model_prob"]


def test_duplicate_projection_outcome_ids_are_rejected_as_ambiguous():
    payload = {
        "projections": [
            {"status": "eligible", "row_id": "o1", "event_id": "game-1"},
            {"status": "eligible", "row_id": "o1", "event_id": "game-2"},
        ]
    }
    assert index_projections(payload) == {}


def test_local_ev_probability_source_is_labeled_separately():
    card = ev_card(market_type="MONEYLINE", market="MONEYLINE")
    card["sides"]["OVER"]["ev"]["ev_source"] = "LOCAL"
    ev = [{
        "market_id": "m1",
        "outcome_id": "o1",
        "book": "FD",
        "book_odds": 110,
        "book_decimal_odds": 2.1,
    }]
    row = make_row(card, ev)
    assert row["model_prob_source"] == "local_devig"


# 3. EV price/book/decimal all come from the SAME (highest-decimal) record.
def test_two_book_same_row():
    card = ev_card(market_type="MONEYLINE", market="MONEYLINE")
    ev = [
        {
            "market_id": "m1",
            "outcome_id": "o1",
            "book": "DK",
            "book_odds": 100,
            "book_decimal_odds": 2.0,
            "calculated_ev_pct": 0.05,
        },
        {
            "market_id": "m1",
            "outcome_id": "o1",
            "book": "FD",
            "book_odds": 110,
            "book_decimal_odds": 2.1,
            "calculated_ev_pct": 0.08,
        },
    ]
    row = make_row(card, ev)
    assert (row["book"], row["price"], row["decimal_price"]) == ("FD", 110, 2.1)


# 4. Exact-line fallback (outcome_id mismatch, current_line match) still eligible.
def test_exact_line_eligibility():
    card = ev_card(line=5.5, market_type="MONEYLINE", market="MONEYLINE")
    ev = [
        {
            "market_id": "m1",
            "outcome_id": "oX",
            "side": "OVER",
            "current_line": 5.5,
            "book": "FD",
            "book_odds": 105,
            "book_decimal_odds": 2.05,
        }
    ]
    row = make_row(card, ev)
    assert row["decimal_price"] == 2.05
    assert isinstance(row["edge_pct"], float)


# 5. Alt-line fallback -> ineligible, flagged, edge_pct stays numeric-empty.
def test_alt_line_fallback_flag():
    row = make_row(ev_card(fallback=True), [])
    assert row["sizing_flags"] == "ev_line_fallback"
    assert row["recommended_units_pre_news"] == ""
    assert row["edge_pct"] == ""  # NOT polluted with the flag string


# 6. EV summary present but no book_decimal_odds -> no_book_decimal.
def test_no_book_decimal_flag():
    ev = [{"market_id": "m1", "outcome_id": "o1", "book": "FD", "book_odds": 110}]
    row = make_row(ev_card(market_type="MONEYLINE"), ev)
    assert row["sizing_flags"] == "no_book_decimal"
    assert row["recommended_units_pre_news"] == ""
    assert row["edge_pct"] == ""


# 7. Whole-number push-capable line -> sizing-ineligible.
def test_whole_number_push_ineligible():
    card = ev_card(line=8.0, market_type="TOTAL", market="TOTAL")
    ev = [
        {
            "market_id": "m1",
            "outcome_id": "o1",
            "book": "FD",
            "book_odds": 100,
            "book_decimal_odds": 2.0,
        }
    ]
    row = make_row(card, ev)
    assert row["sizing_flags"] == "push_capable_no_prob"
    assert row["recommended_units_pre_news"] == ""


# 8. No-EV/signal row: display odds anchored, implied_prob empty.
def test_no_ev_row():
    card = {
        "headline_side": "UNDER",
        "card_id": "m2",
        "sides": {"UNDER": {"outcome_id": "o2", "best_odds": -110}},
        "board": "B",
    }
    row = make_row(card, [])
    assert row["price"] == -110
    assert row["decimal_price"] == pytest.approx(1.909, abs=1e-3)
    assert row["implied_prob"] == ""
    assert row["model_prob"] == ""


def test_signal_row_populates_proxy_probability_edge_and_kelly_but_is_not_actionable():
    card = {
        "headline_side": "OVER",
        "card_id": "m2",
        "market_type": "PLAYER_PROP",
        "market": "AST",
        "board": "B",
        "sides": {
            "OVER": {
                "outcome_id": "o2",
                "line": 7.5,
                "best_odds": -144,
                "signal": {
                    "hit_component": 70.0,
                    "insight_component": 60.0,
                    "movement_corroboration": 1.0,
                    "orf_component": 55.0,
                    "insight_conflict": False,
                },
                "proxy_market_edge": {
                    "book": "Prophetx",
                    "odds": -144,
                    "fair_prob_pct": 56.933,
                    "source": "proxy_market",
                },
            }
        },
    }
    row = make_row(card, [])
    assert row["model_prob"] == pytest.approx(0.56933)
    assert row["model_prob_source"] == "proxy_market_devig"
    assert row["market_consensus_prob"] == pytest.approx(0.56933)
    assert row["final_blended_prob"] == pytest.approx(0.56933)
    assert row["board"] == "B"
    assert row["movement_component"] == pytest.approx(75.0)
    assert set(row["signal_flags"].split(";")) == {
        "hit_rate_support", "insight_support", "orf_support", "movement_support"
    }
    assert isinstance(row["edge_pct"], float)
    assert isinstance(row["kelly_025_units"], float)
    assert row["actionable"] == "false"
    assert row["recommended_units_pre_news"] == ""
    assert "proxy_market_probability" in row["sizing_flags"]


# 8b. Stale-line edge gate: RLM + thin_liquidity on an EV-sized row is a
#     phantom-edge risk (see 2026-07-11 Bonner O10.5: pack recommended 3.0u with
#     both flags already set). Withhold the unit recommendation and flag it, but
#     keep edge_pct visible (Round-2 Fix 1).
def test_stale_line_edge_gate_withholds_units():
    card = ev_card(
        market_type="MONEYLINE",
        market="MONEYLINE",
        flags=["reverse_line_movement", "thin_liquidity"],
    )
    ev = [
        {
            "market_id": "m1",
            "outcome_id": "o1",
            "book": "FD",
            "book_odds": 110,
            "book_decimal_odds": 2.1,
            "calculated_ev_pct": 0.05,
        }
    ]
    row = make_row(card, ev)
    assert row["recommended_units_pre_news"] == ""  # stake withheld
    assert "edge_suspect_stale_line" in row["data_quality_flags"]
    assert isinstance(row["edge_pct"], float)  # edge still visible, just not staked
    assert row["actionable"] == "false"
    assert row["_board"] == "flagged"


# 8c. The gate needs BOTH flags; a single flag (only RLM) does not trip it.
def test_stale_line_gate_requires_both_flags():
    card = ev_card(
        market_type="MONEYLINE", market="MONEYLINE", flags=["reverse_line_movement"]
    )
    ev = [
        {
            "market_id": "m1",
            "outcome_id": "o1",
            "book": "FD",
            "book_odds": 110,
            "book_decimal_odds": 2.1,
            "calculated_ev_pct": 0.05,
        }
    ]
    row = make_row(card, ev)
    assert row["recommended_units_pre_news"] == 1.0
    assert "edge_suspect_stale_line" not in row["data_quality_flags"]


# 8c2. A NaN/inf line must never crash build_row (found while adding the
#      non_numeric_line withhold-gate test below: _fmt_line did int(f) on NaN
#      unconditionally, which raises ValueError and would take down the whole
#      slate's pack generation over one bad upstream row).
def test_nan_line_does_not_crash_build_row():
    nan = float("nan")
    card = ev_card(line=nan, market_type="MONEYLINE", market="MONEYLINE")
    row = make_row(card, [])
    assert row is not None
    assert "non_numeric_line" in row["data_quality_flags"]


# 8d. spread_sign_conflict alone withholds the stake (2026-07-13 LAS @ ATL:
#     HOME -1.5 / AWAY +7.5 mismatched magnitudes shipped units=1.5 to the
#     desk despite the flag telling readers to stand the market down).
def test_spread_sign_conflict_withholds_units():
    card = ev_card(
        market_type="GAMELINE",
        market="SPREAD",
        proposition="SPREAD",
        flags=["spread_sign_conflict"],
    )
    ev = [
        {
            "market_id": "m1",
            "outcome_id": "o1",
            "book": "FD",
            "book_odds": 115,
            "book_decimal_odds": 2.15,
            "calculated_ev_pct": 0.05,
        }
    ]
    row = make_row(card, ev)
    assert row["recommended_units_pre_news"] == ""  # stake withheld
    assert "spread_sign_conflict" in row["data_quality_flags"]
    assert isinstance(row["edge_pct"], float)  # edge still visible, just not staked
    assert row["actionable"] == "false"
    assert row["_board"] == "flagged"


# 8e. Without the flag, an otherwise-identical SPREAD row sizes normally.
def test_spread_row_without_conflict_sizes_normally():
    card = ev_card(
        market_type="GAMELINE", market="SPREAD", proposition="SPREAD", flags=[]
    )
    ev = [
        {
            "market_id": "m1",
            "outcome_id": "o1",
            "book": "FD",
            "book_odds": 115,
            "book_decimal_odds": 2.15,
            "calculated_ev_pct": 0.05,
        }
    ]
    row = make_row(card, ev)
    assert row["recommended_units_pre_news"] != ""
    assert "spread_sign_conflict" not in row["data_quality_flags"]


def test_card_vs_movement_line_mismatch_is_disqualifying_even_for_legacy_cards():
    card = ev_card(
        line=7.5,
        market_type="GAMELINE",
        market="SPREAD",
        proposition="SPREAD",
    )
    card["sides"]["OVER"]["movement"] = {"open_line": 7.5, "current_line": 8.5}
    ev = [{
        "market_id": "m1",
        "outcome_id": "o1",
        "book": "FD",
        "book_odds": 115,
        "book_decimal_odds": 2.15,
        "calculated_ev_pct": 0.05,
    }]
    row = make_row(card, ev)
    assert "movement_line_mismatch" in row["data_quality_flags"]
    assert row["recommended_units_pre_news"] == ""
    assert row["actionable"] == "false"
    assert row["_board"] == "flagged"


# 8f. Same gate for the other ROLE_BLOCK "stand it down" flags: a corrupt
#     (NaN) line still sized fully before this gate existed, since
#     compute_sizing only consumes price/model_prob, never the line itself.
def test_non_numeric_line_withholds_units():
    nan = float("nan")
    card = ev_card(
        line=nan, market_type="MONEYLINE", market="MONEYLINE", proposition="MONEYLINE"
    )
    ev = [
        {
            "market_id": "m1",
            "outcome_id": "o1",
            "book": "FD",
            "book_odds": 115,
            "book_decimal_odds": 2.15,
            "calculated_ev_pct": 0.05,
        }
    ]
    row = make_row(card, ev)
    assert row["recommended_units_pre_news"] == ""
    assert "non_numeric_line" in row["data_quality_flags"]


# 8g. implausible_line (player-prop line past the sanity ceiling).
def test_implausible_line_withholds_units():
    card = ev_card(
        line=350.5,
        market_type="PLAYER_PROP",
        market="HITS",
        proposition="HITS",
        market_raw="Hits",
        player_id="p1",
    )
    ev = [
        {
            "market_id": "m1",
            "outcome_id": "o1",
            "book": "FD",
            "book_odds": 115,
            "book_decimal_odds": 2.15,
            "calculated_ev_pct": 0.05,
        }
    ]
    row = make_row(card, ev)
    assert row["recommended_units_pre_news"] == ""
    assert "implausible_line" in row["data_quality_flags"]


# 8h. cross_sport_market:<LEAGUE> (dynamic-suffix flag, matched by prefix).
def test_cross_sport_market_withholds_units():
    card = ev_card(
        line=6.5,
        market_type="PLAYER_PROP",
        market="REB",
        proposition="REBOUNDS",
        market_raw="Rebounds",
    )
    ev = [
        {
            "market_id": "m1",
            "outcome_id": "o1",
            "book": "FD",
            "book_odds": 115,
            "book_decimal_odds": 2.15,
            "calculated_ev_pct": 0.05,
        }
    ]
    row = make_row(card, ev, sport="MLB")
    assert row["recommended_units_pre_news"] == ""
    assert "cross_sport_market:WNBA" in row["data_quality_flags"]


# 9. american_to_decimal pure helper.
def test_american_to_decimal():
    assert american_to_decimal(150) == 2.50
    assert american_to_decimal("-200") == 1.50
    assert american_to_decimal(100) == 2.00
    assert american_to_decimal(None) is None


def test_is_no_push_market():
    assert is_no_push_market("MONEYLINE", None) is True
    assert is_no_push_market("TOTAL", 8.5) is True
    assert is_no_push_market("TOTAL", 8.0) is False
    assert is_no_push_market("SPREAD", -1.5) is True


# 10. headline_side None -> skipped.
def test_headline_side_none():
    assert make_row({"headline_side": None, "sides": {}}, []) is None


# 11. Identity recovery: game card with empty identity recovers event_id/market_type
#     from the outcome_id-matched ev_record.
def test_identity_recovery_from_ev_records():
    card = {
        "headline_side": "OVER",
        "card_id": "gm1",
        "event_id": None,
        "market_type": None,
        "board": "A",
        "sides": {
            "OVER": {
                "outcome_id": "g1",
                "line": 8.5,
                "ev": {
                    "is_alt_line_fallback": False,
                    "devig_decimal": 2.0,
                    "best_ev_pct": 0.04,
                    "kelly_pct": 0.01,
                },
            }
        },
    }
    ev = [
        {
            "market_id": "gm1",
            "outcome_id": "g1",
            "event_id": "E9",
            "market": "TOTAL",
            "market_type": "GAMELINE",
            "book": "FD",
            "book_odds": 100,
            "book_decimal_odds": 2.0,
        }
    ]
    row = make_row(card, ev)
    assert row["event_id"] == "E9"
    assert row["market_type"] == "GAMELINE"
    assert row["research_leverage"] == "HIGH"  # MLB TOTAL
    assert isinstance(row["edge_pct"], float)


# 12. source_timestamps holds the contributing artifacts.
def test_source_timestamps():
    row = make_row(ev_card(market_type="MONEYLINE"), [])
    ts = json.loads(row["source_timestamps"])
    assert ts == {"cards": "CT", "line_movement": "LMT", "props": "PT"}


# 13. Injuries join: games context events -> teams.
def test_build_injuries():
    games = {
        "context": {
            "events": {"E1": {"home_team_id": "T1", "away_team_id": "T2"}},
            "teams": {"T1": {"injuries": [{"player": "A. Star"}]}, "T2": {"injuries": []}},
        }
    }
    inj = build_injuries(games)
    assert inj["E1"] == "A. Star"


def test_build_injuries_formats_real_injury_schema():
    """Live injuries carry firstName/lastName and a nested injury.status, not a
    'player' key. build_injuries must render a legible string, not a dict dump."""
    games = {
        "context": {
            "events": {"E1": {"home_team_id": "T1", "away_team_id": "T2"}},
            "teams": {
                "T1": {
                    "injuries": [
                        {
                            "playerId": "p1",
                            "firstName": "Aaron",
                            "lastName": "Judge",
                            "injury": {"status": "OUT", "injury": "Toe"},
                            "teamId": "T1",
                        }
                    ]
                },
                "T2": {"injuries": []},
            },
        }
    }
    inj = build_injuries(games)
    assert inj["E1"] == "Aaron Judge (OUT; Toe)"


def test_build_injuries_includes_return_date_and_analysis():
    """P0 richer flags: body, return date, and truncated analysis when present."""
    long_analysis = "A" * 200
    games = {
        "context": {
            "events": {"E1": {"home_team_id": "T1", "away_team_id": "T2"}},
            "teams": {
                "T1": {
                    "injuries": [
                        {
                            "playerId": "p1",
                            "firstName": "Stephen",
                            "lastName": "Kolek",
                            "injury": {
                                "status": "60-Day IL",
                                "injury": "Right Forearm Strain",
                                "returnDate": "2026-09-04T00:00:00-0700",
                                "analysis": long_analysis,
                            },
                            "teamId": "T1",
                        }
                    ]
                },
                "T2": {
                    "injuries": [
                        {
                            "firstName": "Leonie",
                            "lastName": "Fiebich",
                            "injury": {
                                "status": "Out",
                                "injury": "Left Foot",
                                "returnDate": "2026-08-03",
                            },
                        }
                    ]
                },
            },
        }
    }
    inj = build_injuries(games)
    flag = inj["E1"]
    assert "Stephen Kolek (60-Day IL; Right Forearm Strain; ret 2026-09-04)" in flag
    assert "Leonie Fiebich (Out; Left Foot; ret 2026-08-03)" in flag
    assert " | " in flag
    first_player_flag = flag.split(" | ")[0]
    assert ": " in first_player_flag
    rendered_analysis = first_player_flag.split(": ", 1)[1]
    assert len(rendered_analysis) == 160
    assert rendered_analysis.endswith("...")
    assert "A" * 200 not in flag
    assert "playerId" not in flag


def test_build_injuries_rejects_unnormalized_return_dates_and_complex_types():
    """Unnormalized return dates (e.g. 'TBD') return empty date, avoiding 'ret TBD'."""
    games = {
        "context": {
            "events": {"E1": {"home_team_id": "T1", "away_team_id": "T2"}},
            "teams": {
                "T1": {
                    "injuries": [
                        {
                            "firstName": "Player",
                            "lastName": "One",
                            "injury": {
                                "status": "Questionable",
                                "returnDate": "TBD",
                                "injury": {"nested": "dict"},  # Non-scalar body
                                "analysis": ["list", "of", "items"],  # Non-scalar analysis
                            },
                        }
                    ]
                }
            },
        }
    }
    inj = build_injuries(games)
    assert inj["E1"] == "Player One (Questionable)"
    assert "ret" not in inj["E1"]
    assert "TBD" not in inj["E1"]
    assert "nested" not in inj["E1"]


# 14. Quota ranking never starves board B.
def test_quota_ranking():
    rows = [{"_board": "board_a", "_rank_value": i, "market_id": f"a{i}"} for i in range(20)]
    rows += [{"_board": "board_b", "_rank_value": i, "market_id": f"b{i}"} for i in range(5)]
    out = rank_rows(rows, top_ev_n=15, top_signal_n=10)
    assert sum(1 for r in out if r["_board"] == "board_a") == 15
    assert sum(1 for r in out if r["_board"] == "board_b") == 5
    # deterministic: highest rank first
    assert out[0]["market_id"] == "a19"


def test_flagged_audits_share_existing_ev_quota():
    rows = [
        {"_board": "board_a", "_rank_value": i, "market_id": f"a{i}"}
        for i in range(20)
    ]
    rows += [
        {"_board": "flagged", "_rank_value": i + 0.5, "market_id": f"f{i}"}
        for i in range(20)
    ]
    rows += [
        {"_board": "board_b", "_rank_value": i, "market_id": f"b{i}"}
        for i in range(10)
    ]
    out = rank_rows(rows, top_ev_n=15, top_signal_n=10)
    assert len(out) == 25
    assert sum(row["_board"] in {"board_a", "flagged"} for row in out) == 15


# 15. Date selection: pick requested, fall back to latest, keep undated.
def test_select_date():
    rows = [
        {"_event_starts_at": "2026-06-24T18:00:00-04:00", "market_id": "x"},
        {"_event_starts_at": "2026-06-25T18:00:00-04:00", "market_id": "y"},
        {"_event_starts_at": None, "market_id": "z"},
    ]
    kept, target = select_date(rows, "2026-06-24")
    ids = {r["market_id"] for r in kept}
    assert target == "2026-06-24"
    assert ids == {"x", "z"}  # matching date + undated kept, other date dropped

    kept2, target2 = select_date(rows, "2030-01-01")  # absent -> fallback to latest
    assert target2 == "2026-06-25"

    kept3, target3 = select_date([{"_event_starts_at": None, "market_id": "z"}], "2026-06-24")
    assert len(kept3) == 1  # no dates -> keep all


# 16. End-to-end: two streams + multi-sport + dossier uniqueness + briefing.
def _league_fixture(root, lg):
    low = lg.lower()
    (root / "cards").mkdir(parents=True, exist_ok=True)
    (root / "normalized").mkdir(parents=True, exist_ok=True)
    player_cards = {
        "generated_at": "PC",
        "board_a": [],
        "board_b": [
            {
                "card_id": "p1",
                "event_id": "EP",
                "market": "PTS",
                "matchup": "A @ B",
                "board": "B",
                "rank_value": 1.0,
                "headline_side": "OVER",
                "sides": {"OVER": {"outcome_id": "po", "line": 5.5, "best_odds": -110, "ev": None}},
            }
        ],
    }
    game_cards = {
        "generated_at": "GC",
        "board_a": [
            {
                "card_id": "gm1",
                "board": "A",
                "rank_value": 9.0,
                "headline_side": "OVER",
                "sides": {
                    "OVER": {
                        "outcome_id": "go",
                        "line": 8.5,
                        "best_odds": None,
                        "ev": {
                            "is_alt_line_fallback": False,
                            "devig_decimal": 2.0,
                            "best_ev_pct": 0.05,
                            "kelly_pct": 0.02,
                        },
                    }
                },
            }
        ],
        "board_b": [],
        "context": {
            "events": {
                "EG": {
                    "home_team_id": "T1",
                    "away_team_id": "T2",
                    "starts_at": "2099-07-07T23:10:00+00:00",
                }
            },
            "teams": {"T1": {"injuries": [{"player": "Hurt Guy"}]}},
        },
    }
    games_lm = {
        "generated_at": "GLM",
        "ev_records": [
            {
                "market_id": "gm1",
                "outcome_id": "go",
                "event_id": "EG",
                "market": "TOTAL",
                "market_type": "GAMELINE",
                "book": "FD",
                "book_odds": 110,
                "book_decimal_odds": 2.1,
                "calculated_ev_pct": 0.05,
            }
        ],
    }
    (root / "cards" / f"{low}_cards_latest.json").write_text(json.dumps(player_cards))
    (root / "cards" / f"{low}_games_cards_latest.json").write_text(json.dumps(game_cards))
    (root / "normalized" / f"{low}_line_movement_latest.json").write_text(
        json.dumps({"generated_at": "LM", "ev_records": []})
    )
    (root / "normalized" / f"{low}_games_line_movement_latest.json").write_text(
        json.dumps(games_lm)
    )
    (root / "normalized" / f"{low}_props_latest.json").write_text(
        json.dumps(
            {
                "generated_at": "PN",
                "records": [
                    {
                        "event_id": "EP",
                        "sport_context": {"event_starts_at": "2099-07-07T23:10:00+00:00"},
                    }
                ],
            }
        )
    )
    (root / "normalized" / f"{low}_games_latest.json").write_text(
        json.dumps({"generated_at": "GN", "context": game_cards["context"]})
    )
    (root / "normalized" / f"{low}_projections_latest.json").write_text(
        json.dumps(
            {
                "generated_at": "PROJ",
                "projections": [
                    {
                        "status": "eligible",
                        "sport": lg.upper(),
                        "row_id": "go",
                        "event_id": "EG",
                        "market_id": "gm1",
                        "line": 8.5,
                        "side": "OVER",
                        "distribution": {
                            "line": 8.5,
                            "side": "OVER",
                            "win_prob": 0.58,
                            "push_prob": 0.0,
                            "mean": 8.9,
                            "variance": 6.0,
                            "quantiles": {"0.5": 9},
                            "model_version": "projection-v1",
                        },
                    }
                ],
            }
        )
    )


def test_end_to_end(tmp_path, monkeypatch):
    def fake_lp(lg):
        root = tmp_path / "data" / lg.upper()
        return P.LeaguePaths(
            league=lg.upper(),
            root=root,
            raw=root / "raw",
            normalized=root / "normalized",
            reports=root / "reports",
        )

    for lg in ("MLB", "WNBA"):
        _league_fixture(tmp_path / "data" / lg, lg)
    monkeypatch.setattr("outlier_scrapers.pack.paths.league_paths", fake_lp)

    rows, target, games_norm, coverage = build_pack_with_coverage(
        ["MLB", "WNBA"], None, 15, 10
    )
    projection_records = load_projection_records(["MLB", "WNBA"])
    sports = {r["sport"] for r in rows}
    assert sports == {"MLB", "WNBA"}
    # both streams represented: a player (board_b) and a game (board_a) row exist
    assert any(r["_board"] == "board_a" for r in rows)
    assert any(r["_board"] == "board_b" for r in rows)
    # game row recovered identity + got sized
    game_rows = [r for r in rows if r["market_id"] == "gm1"]
    assert game_rows and game_rows[0]["event_id"] == "EG"
    assert game_rows[0]["injury_flags"] == "Hurt Guy"
    assert isinstance(game_rows[0]["edge_pct"], float)
    assert game_rows[0]["independent_model_prob"] == 0.58
    assert game_rows[0]["market_consensus_prob"] == game_rows[0]["final_blended_prob"]
    assert json.loads(game_rows[0]["source_timestamps"])["projections"] == "PROJ"

    out_dir = tmp_path / "packs" / target
    write_pack(
        rows,
        out_dir,
        games_norm_by_league=games_norm,
        coverage=coverage,
        projection_records=projection_records,
    )
    assert (out_dir / "candidates.csv").exists()
    assert (out_dir / "opportunities.csv").exists()
    projection_lines = (out_dir / "projections.jsonl").read_text().splitlines()
    assert len(projection_lines) == 2
    assert json.loads(projection_lines[0])["row_id"] == "go"
    assert (out_dir / "candidate_coverage.json").exists()
    assert (out_dir / "game_totals.csv").exists()
    assert (out_dir / "team_totals.csv").exists()
    assert (out_dir / "sections" / "game_totals.md").exists()
    assert (out_dir / "sections" / "team_totals.md").exists()
    assert (out_dir / "alt_team_totals.csv").exists()
    assert (out_dir / "alt_team_total_parlays.csv").exists()
    assert (out_dir / "sections" / "alt_team_totals.md").exists()
    briefing = (out_dir / "briefing.md").read_text()
    assert "REASONING PASSES (pack-only):" in briefing
    # Pass labels are desk-agnostic (no A/B/C/D letters) so the shared ROLE_BLOCK
    # reads cleanly in both the A-E desk and Desk 2 (Q/W/X/R/S).
    assert "(A, D)" not in briefing
    assert "(B, C)" not in briefing
    assert "Slate index" in briefing
    assert "### Game totals" in briefing
    assert "### Team totals" in briefing
    assert "### Candidate Coverage" in briefing
    assert coverage["MLB"]["emitted"] > 0
    assert coverage["WNBA"]["emitted"] > 0
    # dossiers unique per (sport,event)
    dossiers = list((out_dir / "dossiers").glob("*.md"))
    assert len(dossiers) == len({d.name for d in dossiers})
    assert len(dossiers) >= 2


# 17. Briefing carries the verbatim role block + slate line.
def test_briefing_role_block():
    rows = [
        {
            "_board": "board_a",
            "sport": "MLB",
            "market_id": "m",
            "selection": "OVER",
            "line": 1.5,
            "price": -110,
            "edge_pct": 0.05,
            "recommended_units_pre_news": 1.0,
            "event_id": "E",
            "_event_starts_at": None,
        }
    ]
    text = build_briefing(rows, "2026-06-24")
    assert "Use this pack ONLY" in text
    assert "first lock: n/a" in text


def test_briefing_deduplicates_totals_restatements_and_separates_flagged_ev():
    signal = {
        "_board": "board_b",
        "sport": "WNBA",
        "market_id": "tm1",
        "selection": "LAS Team Total OVER 85.5",
        "line": 85.5,
        "event_id": "E1",
    }
    flagged = {
        "_board": "flagged",
        "sport": "WNBA",
        "market_id": "spread1",
        "selection": "LAS @ ATL Spread AWAY +7.5",
        "line": "+7.5",
        "price": 115,
        "data_quality_flags": "spread_sign_conflict;movement_line_mismatch",
    }
    team_totals = [{
        "sport": "WNBA",
        "market_id": "tm1",
        "selection": "LAS Team Total OVER 85.5",
        "line": 85.5,
        "price": -110,
        "edge_pct": 0.01,
        "actionable": "false",
        "quality_flags": "SINGLE_BOOK",
    }]
    text = build_briefing([signal, flagged], "2026-07-13", team_totals_rows=team_totals)
    assert text.count("tm1") == 1
    assert "### Non-actionable flagged cards" in text
    top_ev = text.split("### Top EV cards", 1)[1].split("### Top signal cards", 1)[0]
    assert "spread1" not in top_ev


# 18. Selection is human-readable (name + label + side + line), not just the side token.
def test_selection_human_readable():
    card = ev_card(side="OVER", line=5.5, player="A. Judge", market="HITS", market_type="MONEYLINE")
    ev = [
        {
            "market_id": "m1",
            "outcome_id": "o1",
            "book": "FD",
            "book_odds": 110,
            "book_decimal_odds": 2.1,
            "market_label": "Hits O/U",
        }
    ]
    sel = make_row(card, ev)["selection"]
    assert "A. Judge" in sel and "OVER" in sel and "5.5" in sel
    assert sel != "OVER"


def test_team_total_identity_is_not_rendered_as_generic_pts():
    card = {
        "headline_side": "OVER",
        "card_id": "tm1",
        "proposition": "POINTS",
        "market": "PTS",
        "team": "LAS",
        "matchup": "LAS @ ATL",
        "board": "B",
        "sides": {"OVER": {"outcome_id": "to", "line": 85.5, "best_odds": -110}},
    }
    row = make_row(card, [], sport="WNBA")
    assert row["market_type"] == "TEAM_PROP"
    assert row["selection"] == "LAS Team Total OVER 85.5"


def test_mlb_runs_team_total_identity_uses_shared_sport_contract():
    card = {
        "headline_side": "OVER",
        "card_id": "tm-runs",
        "proposition": "RUNS",
        "market": "R",
        "team": "LAD",
        "matchup": "LAD @ SF",
        "board": "B",
        "sides": {"OVER": {"outcome_id": "to-runs", "line": 4.5, "best_odds": -110}},
    }

    row = make_row(card, [], sport="MLB")

    assert row["market_type"] == "TEAM_PROP"
    assert row["selection"] == "LAD Team Total OVER 4.5"


def test_mlb_team_total_uses_market_alias_when_proposition_is_missing():
    card = {
        "headline_side": "OVER",
        "card_id": "tm-runs-alias",
        "market": "R",
        "market_label": "RUNS",
        "team": "LAD",
        "matchup": "LAD @ SF",
        "board": "B",
        "sides": {
            "OVER": {
                "outcome_id": "to-runs-alias",
                "line": 4.5,
                "best_odds": -110,
            }
        },
    }

    row = make_row(card, [], sport="MLB")

    assert row["market_type"] == "TEAM_PROP"
    assert row["selection"] == "LAD Team Total OVER 4.5"


def test_untyped_mlb_total_with_team_stays_game_total():
    card = {
        "headline_side": "OVER",
        "card_id": "game-total",
        "proposition": "TOTAL",
        "market": "TOTAL",
        "team": "LAD",
        "matchup": "LAD @ SF",
        "board": "B",
        "sides": {"OVER": {"outcome_id": "game-over", "line": 8.5, "best_odds": -110}},
    }

    row = make_row(card, [], sport="MLB")

    assert row["market_type"] == "GAMELINE"
    assert "Team Total" not in row["selection"]


# 19. Public money / money% read the real card keys (percentage / money).
def test_public_money_fallback_keys():
    card = ev_card(market_type="MONEYLINE")
    card["sides"]["OVER"]["public_money"] = {"position": "OVER", "percentage": 20, "money": 99}
    row = make_row(card, [])
    assert row["public_money_pct"] == 20
    assert row["money_pct"] == 99

    # Valid 0% must be preserved, not treated as missing.
    card0 = ev_card(market_type="MONEYLINE")
    card0["sides"]["OVER"]["public_money"] = {"percentage": 0, "money": 0}
    row0 = make_row(card0, [])
    assert row0["public_money_pct"] == 0
    assert row0["money_pct"] == 0


def test_candidates_header_public_money_component_grouped():
    assert "public_money_component" in CANDIDATES_HEADER
    assert "public_money_divergence_pct" in CANDIDATES_HEADER
    # Component sits with other Board B components after orf_component.
    assert (
        CANDIDATES_HEADER[CANDIDATES_HEADER.index("orf_component") + 1]
        == "public_money_component"
    )
    # Raw divergence sits after money_pct.
    assert (
        CANDIDATES_HEADER[CANDIDATES_HEADER.index("money_pct") + 1]
        == "public_money_divergence_pct"
    )


def test_public_money_signal_flags_not_data_quality_or_actionable():
    """PM flags go on signal_flags only; never contaminate data_quality_flags."""
    card = ev_card(market_type="MONEYLINE")
    card["board"] = "A"
    card["flags"] = []  # no card-level quality flags
    card["sides"]["OVER"]["public_money"] = {"percentage": 20, "money": 55}
    card["sides"]["OVER"]["signal"] = {
        "public_money_component": 85.0,
        "public_money_divergence_pct": 35.0,
        "hit_pct": 60.0,
        "orf_component": 50.0,
        "insight_component": 50.0,
        "movement_corroboration": 0.0,
    }
    # Provide EV so Board A can be actionable if units/edge allow.
    ev = [
        {
            "market_id": card["card_id"],
            "side": "OVER",
            "outcome_id": "o1",
            "current_line": card["sides"]["OVER"].get("line"),
            "calculated_ev_pct": 5.0,
            "calculated_ev_method": "AVERAGE",
            "ev_source": "NATIVE",
            "devig_decimal": 2.0,
            "record_id": "ev1",
            "book_decimal_odds": 2.0,
            "kelly_pct": 2.0,
        }
    ]
    row = make_row(card, ev)
    assert "sharp_money_support" in str(row.get("signal_flags") or "")
    assert "sharp_money_support" not in str(row.get("data_quality_flags") or "")
    assert row["public_money_component"] == 85.0
    assert row["public_money_divergence_pct"] == 35.0
    # Control: same row shape without PM divergence should not invent DQ flags from PM.
    card2 = ev_card(market_type="MONEYLINE")
    card2["board"] = "A"
    card2["flags"] = []
    card2["sides"]["OVER"]["signal"] = {
        "public_money_component": "",
        "public_money_divergence_pct": "",
        "hit_pct": 60.0,
        "orf_component": 50.0,
        "insight_component": 50.0,
        "movement_corroboration": 0.0,
    }
    row2 = make_row(card2, ev)
    assert "sharp_money_support" not in str(row2.get("signal_flags") or "")
    assert "public_money_heavy" not in str(row2.get("signal_flags") or "")


# 20. Freshness/coverage banner flags a stale/partial stream and an OK stream.
def test_freshness_section_flags_stale_and_ok(tmp_path, monkeypatch):
    reports = tmp_path / "data" / "MLB" / "reports"
    reports.mkdir(parents=True)
    from datetime import datetime

    report = {
        "status": "ok",
        "markets_fetched": 10,
        "markets_requested": 10,
        "props_age_hours": 0.5,
        "generated_at": datetime.now().astimezone().isoformat(),
    }
    from outlier_scrapers.pack import _summarize_lm_status

    ok, line = _summarize_lm_status(report, "MLB props line-movement")
    assert ok is True
    assert "OK" in line
    assert "CAVEAT" not in line

    (reports / "games_line_movement_status_latest.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "markets_fetched": 8,
                "markets_requested": 8,
                "fetch_error_count": 0,
                "props_is_stale": False,
                "generated_at": datetime.now().astimezone().isoformat(),
            }
        )
    )
    (reports / "line_movement_status_latest.json").write_text(
        json.dumps(
            {
                "status": "partial",
                "markets_fetched": 42,
                "markets_requested": 890,
                "fetch_error_count": 3,
                "props_is_stale": True,
                "props_age_hours": 67.0,
                "generated_at": datetime.now().astimezone().isoformat(),
            }
        )
    )

    def fake_lp(lg):
        root = tmp_path / "data" / lg.upper()
        return P.LeaguePaths(
            league=lg.upper(),
            root=root,
            raw=root / "raw",
            normalized=root / "normalized",
            reports=reports,
        )

    monkeypatch.setattr("outlier_scrapers.pack.paths.league_paths", fake_lp)
    section = build_freshness_section(["MLB"])
    text = "\n".join(section)
    assert "### Freshness / Coverage" in text
    assert "MLB games line-movement: OK" in text
    assert "MLB props line-movement: CAVEAT" in text
    assert "stale" in text and "context-only" in text
    # And it embeds into the briefing.
    assert "Freshness / Coverage" in build_briefing([], "2026-06-24", section)


def test_summarize_lm_status_stale_generated_at():
    from datetime import datetime, timedelta
    from outlier_scrapers.pack import _summarize_lm_status

    # 7 hours ago
    stale_dt = datetime.now().astimezone() - timedelta(hours=7)

    report = {
        "status": "ok",
        "markets_fetched": 10,
        "markets_requested": 10,
        "props_age_hours": 0.5,
        "generated_at": stale_dt.isoformat(),
    }

    ok, line = _summarize_lm_status(report, "MLB games line-movement")
    assert ok is False
    assert "CAVEAT" in line
    assert "stale (>6h old)" in line


def test_summarize_lm_status_missing_generated_at():
    from outlier_scrapers.pack import _summarize_lm_status

    report = {
        "status": "ok",
        "markets_fetched": 10,
        "markets_requested": 10,
        "props_age_hours": 0.5,
    }

    ok, line = _summarize_lm_status(report, "MLB games line-movement")
    assert ok is False
    assert "CAVEAT" in line
    assert "missing timestamp" in line


# 22. House rule: pack hard-bans HR and Walks Allowed tokens.
#     HRR/BB are whitelist-admitted at generation and are not pack-excluded.
def test_excluded_markets_dropped():
    for token in ("HR", "HOME_RUNS", "WALKS_ALLOWED"):
        card = ev_card(market=token, market_type=token)
        assert make_row(card, []) is None, f"{token} should be excluded from the pack"


def test_excluded_market_matches_market_type_fallback():
    # Exclusion applies via market_type when the market token is missing, any case.
    card = ev_card(market=None, market_type="hr")
    assert make_row(card, []) is None


def test_non_excluded_markets_kept():
    # HITS is fine; BBA (walks allowed, pitcher) is a different market and stays.
    assert make_row(ev_card(market="HITS", market_type="HITS"), []) is not None
    assert make_row(ev_card(market="BBA", market_type="BBA"), []) is not None


def test_is_excluded_market():
    assert is_excluded_market("HR", None) is True
    assert is_excluded_market(None, "WALKS_ALLOWED") is True
    assert is_excluded_market("HITS", "HITS") is False
    assert is_excluded_market(None, "HRR") is False
    assert is_excluded_market("bb", None) is False
    assert is_excluded_market(None, None) is False


# 24. House rule: plus-money longshots (+150 or longer) are hard-filtered.
def test_longshot_price_dropped_ev_path():
    card = ev_card(market_type="MONEYLINE", market="MONEYLINE")
    ev = [
        {
            "market_id": "m1",
            "outcome_id": "o1",
            "book": "FD",
            "book_odds": 181,
            "book_decimal_odds": 2.81,
            "calculated_ev_pct": 0.05,
        }
    ]
    assert make_row(card, ev) is None


def test_longshot_price_dropped_display_path():
    card = {
        "headline_side": "OVER",
        "card_id": "m3",
        "sides": {"OVER": {"outcome_id": "o3", "best_odds": "+181"}},
        "board": "B",
    }
    assert make_row(card, []) is None


def test_non_longshot_prices_kept():
    # -110 favourite and modest plus-money both stay; missing price stays.
    assert make_row(ev_card(market_type="MONEYLINE"), []) is not None  # best_odds -110
    card = {
        "headline_side": "OVER",
        "card_id": "m4",
        "sides": {"OVER": {"outcome_id": "o4", "best_odds": "+130"}},
        "board": "B",
    }
    assert make_row(card, []) is not None


def test_is_longshot_price():
    assert is_longshot_price("+181") is True
    assert is_longshot_price(150) is True  # boundary: +150 or longer is out
    assert is_longshot_price(149) is False
    assert is_longshot_price("-105") is False
    assert is_longshot_price(None) is False
    assert is_longshot_price("") is False


# 23. Briefing carries the house rules (market exclusions + longshot avoidance).
def test_briefing_house_rules():
    text = build_briefing([], "2026-06-24")
    assert "HOUSE RULES" in text
    assert "strict whitelist" in text
    assert "2B" in text and "UNDER-only" in text
    assert "HR markets are excluded" in text
    assert "+150" in text and "longshot" in text.lower()


# 23b. Briefing tells the desk that same-event legs are correlated, so stacked
#      same-game bets are not sized as independent (Round-2 Fix 6).
def test_briefing_correlation_guidance():
    text = build_briefing([], "2026-06-24")
    low = text.lower()
    assert "correlat" in low  # correlation guidance present
    assert "event_id" in text or "matchup" in low  # keyed to an existing column


# 24. In-play guard: rows whose event already started are dropped from packs.
def test_drop_locked_events():
    from datetime import datetime, timezone

    from outlier_scrapers.pack import drop_locked_events

    now = datetime(2026, 7, 7, 3, 29, tzinfo=timezone.utc)  # 79 min after first lock
    rows = [
        {"_event_starts_at": "2026-07-07T02:10:00+00:00", "market_id": "live"},
        {"_event_starts_at": "2026-07-07T23:10:00Z", "market_id": "pregame"},
        {"_event_starts_at": None, "market_id": "undated"},
        {"_event_starts_at": "not-a-timestamp", "market_id": "junk"},
        {"_event_starts_at": "2026-07-07T23:10:00", "market_id": "naive"},
    ]
    kept, dropped = drop_locked_events(rows, now=now)
    assert {r["market_id"] for r in kept} == {"pregame"}
    assert {r["market_id"] for r in dropped} == {"live", "undated", "junk", "naive"}


def test_drop_locked_events_boundary_is_locked():
    from datetime import datetime, timezone

    from outlier_scrapers.pack import drop_locked_events

    now = datetime(2026, 7, 7, 2, 10, tzinfo=timezone.utc)
    rows = [{"_event_starts_at": "2026-07-07T02:10:00+00:00", "market_id": "at_lock"}]
    kept, dropped = drop_locked_events(rows, now=now)
    assert kept == [] and len(dropped) == 1  # exactly at first lock counts as live


# 25. build_pack applies the in-play guard end-to-end.
def test_build_pack_drops_started_events(tmp_path, monkeypatch):
    def fake_lp(lg):
        root = tmp_path / "data" / lg.upper()
        return P.LeaguePaths(
            league=lg.upper(),
            root=root,
            raw=root / "raw",
            normalized=root / "normalized",
            reports=root / "reports",
        )

    for lg in ("MLB", "WNBA"):
        _league_fixture(tmp_path / "data" / lg, lg)
    # Stamp the player-prop event EP with a start time far in the past: those
    # rows now carry live/in-play lines and must never reach the pack.
    for lg in ("MLB", "WNBA"):
        props_path = tmp_path / "data" / lg / "normalized" / f"{lg.lower()}_props_latest.json"
        props_path.write_text(
            json.dumps(
                {
                    "generated_at": "PN",
                    "records": [
                        {
                            "event_id": "EP",
                            "market_id": "p1",
                            "sport_context": {"event_starts_at": "2020-01-01T00:00:00+00:00"},
                        }
                    ],
                }
            )
        )
    monkeypatch.setattr("outlier_scrapers.pack.paths.league_paths", fake_lp)

    rows, _target, _games_norm, coverage = build_pack_with_coverage(["MLB", "WNBA"], None, 15, 10)
    ids = {r["market_id"] for r in rows}
    assert "p1" not in ids  # started event dropped
    assert "gm1" in ids  # independently verified future game remains
    assert coverage["MLB"]["date_filtered"] == 1
    assert coverage["WNBA"]["date_filtered"] == 1


def test_build_pack_coverage_explains_zero_rows_from_missing_event_starts(tmp_path, monkeypatch):
    def fake_lp(lg):
        root = tmp_path / "data" / lg.upper()
        return P.LeaguePaths(
            league=lg.upper(),
            root=root,
            raw=root / "raw",
            normalized=root / "normalized",
            reports=root / "reports",
        )

    root = tmp_path / "data" / "MLB"
    _league_fixture(root, "MLB")
    (root / "cards" / "mlb_games_cards_latest.json").write_text(
        json.dumps({"generated_at": "GC", "board_a": [], "board_b": []})
    )
    (root / "normalized" / "mlb_props_latest.json").write_text(
        json.dumps({"generated_at": "PN", "records": [{"event_id": "EP", "sport_context": {}}]})
    )
    monkeypatch.setattr("outlier_scrapers.pack.paths.league_paths", fake_lp)

    rows, _target, _games_norm, coverage = build_pack_with_coverage(
        ["MLB"], "2026-07-13", 15, 10
    )

    assert rows == []
    assert coverage["MLB"] == {
        "cards": 1,
        "rows_built": 1,
        "date_filtered": 0,
        "started_dropped": 0,
        "unverified_start_dropped": 1,
        "emitted": 0,
    }


def test_combined_pack_counts_undated_mlb_as_unverified_not_wrong_date(tmp_path, monkeypatch):
    def fake_lp(lg):
        root = tmp_path / "data" / lg.upper()
        return P.LeaguePaths(
            league=lg.upper(),
            root=root,
            raw=root / "raw",
            normalized=root / "normalized",
            reports=root / "reports",
        )

    for league in ("MLB", "WNBA"):
        _league_fixture(tmp_path / "data" / league, league)
    mlb = tmp_path / "data" / "MLB"
    (mlb / "cards" / "mlb_games_cards_latest.json").write_text(
        json.dumps({"generated_at": "GC", "board_a": [], "board_b": []})
    )
    (mlb / "normalized" / "mlb_props_latest.json").write_text(
        json.dumps({"generated_at": "PN", "records": [{"event_id": "EP", "sport_context": {}}]})
    )
    monkeypatch.setattr("outlier_scrapers.pack.paths.league_paths", fake_lp)

    rows, _target, _games_norm, coverage = build_pack_with_coverage(
        ["MLB", "WNBA"], None, 15, 10
    )

    assert {row["sport"] for row in rows} == {"WNBA"}
    assert coverage["MLB"]["date_filtered"] == 0
    assert coverage["MLB"]["unverified_start_dropped"] == 1


def test_build_pack_keeps_legacy_three_value_return(monkeypatch):
    monkeypatch.setattr(
        "outlier_scrapers.pack.build_pack_with_coverage",
        lambda *_args: ([{"market_id": "m"}], "2026-07-13", {"MLB": {}}, {"MLB": {}}),
    )
    rows, target, games_norm = build_pack(["MLB"], "2026-07-13", 15, 10)
    assert rows == [{"market_id": "m"}]
    assert target == "2026-07-13"
    assert games_norm == {"MLB": {}}


# 26. Briefing states the pregame-only / live-line house rule.
def test_briefing_pregame_house_rule():
    text = build_briefing([], "2026-07-06")
    lowered = text.lower()
    assert "pregame" in lowered
    assert "live" in lowered or "in-play" in lowered
    assert "first lock" in lowered


def test_write_pack_invalidates_stale_derived_outputs(tmp_path):
    out_dir = tmp_path / "packs" / "2026-07-06"
    dossiers = out_dir / "dossiers"
    dossiers.mkdir(parents=True)
    (out_dir / "reasoning_status.json").write_text('{"overall":"FULL"}')
    (out_dir / "manual_betting_report.md").write_text("stale live recommendations")
    (out_dir / "chatgpt_a.md").write_text("stale phase")
    (dossiers / "stale-event.md").write_text("stale dossier")
    (out_dir / "keep-me.txt").write_text("unrelated")

    write_pack([], out_dir)

    assert not (out_dir / "reasoning_status.json").exists()
    assert not (out_dir / "manual_betting_report.md").exists()
    assert not (out_dir / "chatgpt_a.md").exists()
    assert not (dossiers / "stale-event.md").exists()
    assert (out_dir / "keep-me.txt").read_text() == "unrelated"
    with (out_dir / "decisions.csv").open(newline="", encoding="utf-8") as handle:
        assert next(csv.reader(handle)) == [
            "decision_id", "snapshot_id", "pipeline_verdict", "A_verdict",
            "B_verdict", "C_verdict", "D_verdict", "final_verdict", "units",
            "kill_reason", "news_override", "policy_fingerprint", 
            "portfolio_mode", "pre_cap_units", "portfolio_units", "cap_reasons",
        ]


# 21. All-clean streams produce no UNRELIABLE guidance line.
def test_freshness_section_all_ok(tmp_path, monkeypatch):
    from datetime import datetime

    reports = tmp_path / "reports"
    reports.mkdir(parents=True)
    clean = {
        "status": "ok",
        "markets_fetched": 8,
        "markets_requested": 8,
        "fetch_error_count": 0,
        "generated_at": datetime.now().astimezone().isoformat(),
    }
    (reports / "games_line_movement_status_latest.json").write_text(json.dumps(clean))
    (reports / "line_movement_status_latest.json").write_text(json.dumps(clean))

    def fake_lp(lg):
        return P.LeaguePaths(
            league=lg.upper(), root=tmp_path, raw=tmp_path, normalized=tmp_path, reports=reports
        )

    monkeypatch.setattr("outlier_scrapers.pack.paths.league_paths", fake_lp)
    text = "\n".join(build_freshness_section(["WNBA"]))
    assert "CAVEAT" not in text and "UNRELIABLE" not in text


# --- Ledger context surfacing (report data-quality fixes) --------------------

def _ctx_card(sport_team, opp, matchup, **extra):
    """A minimal board-A player card carrying normalizer-resolved context."""
    card = {
        "headline_side": "OVER",
        "card_id": "c1",
        "market_id": "c1",
        "board": "A",
        "player": "Test Player",
        "market_type": "PLAYER_PROP",
        "team": sport_team,
        "opponent": opp,
        "matchup": matchup,
        "event_id": "ev1",
        "flags": [],
        "sides": {
            "OVER": {
                "outcome_id": "o1",
                "line": 6.5,
                "best_odds": 110,
                "ev": {"is_alt_line_fallback": False},
            }
        },
    }
    card.update(extra)
    return card


def test_context_columns_populated_with_full_names():
    # WNBA CHI @ LAS with the player on LAS: the desk must see 'Los Angeles Sparks',
    # not guess 'Las Vegas' from the LAS code.
    card = _ctx_card(
        "LAS", "CHI", "CHI @ LAS", market="REB", market_raw="Rebounds",
        market_label="Test Player - Rebounds",
    )
    row = make_row(card, [], sport="WNBA")
    assert row["team"] == "LAS"
    assert row["team_name"] == "Los Angeles Sparks"
    assert row["opp_name"] == "Chicago Sky"
    assert row["home_away"] == "HOME"  # LAS is the home token in 'CHI @ LAS'
    assert row["matchup"] == "CHI @ LAS"
    assert "Rebounds" in row["market_label"]


def test_portland_fire_context_resolves_full_names():
    # Expansion team must resolve like any other: PDX -> Portland Fire, AWAY side
    # of 'PDX @ MIN' (regression for the 2026-07-18 Carleton blank-team row).
    card = _ctx_card(
        "PDX", "MIN", "PDX @ MIN", market="PTS", market_raw="Points",
        market_label="Bridget Carleton - Points",
    )
    row = make_row(card, [], sport="WNBA")
    assert row["team"] == "PDX"
    assert row["team_name"] == "Portland Fire"
    assert row["opponent"] == "MIN"
    assert row["opp_name"] == "Minnesota Lynx"
    assert row["home_away"] == "AWAY"


def test_unresolved_team_blanks_opponent_and_flags():
    # When the player's team cannot be resolved, a populated opponent column is
    # worse than an empty one: 2026-07-18 the desk read opponent=MIN as the only
    # team context on a PDX player's row. Blank the pair and flag it.
    card = _ctx_card(
        None, "MIN", "PDX @ MIN", market="PTS", market_raw="Points",
        market_label="Bridget Carleton - Points",
    )
    row = make_row(card, [], sport="WNBA")
    assert not row["team"]
    assert not row["team_name"]
    assert not row["opponent"]
    assert not row["opp_name"]
    assert not row["home_away"]
    assert "team_enrichment_failed" in row["data_quality_flags"].split(";")


def test_player_prop_without_any_team_context_is_flagged():
    # A player row where neither side resolved is still an enrichment failure.
    card = _ctx_card(None, None, None)
    row = make_row(card, [], sport="WNBA")
    assert "team_enrichment_failed" in row["data_quality_flags"].split(";")


def test_resolved_team_context_is_not_flagged():
    card = _ctx_card("LAS", "CHI", "CHI @ LAS")
    row = make_row(card, [], sport="WNBA")
    assert "team_enrichment_failed" not in row["data_quality_flags"]


def test_gameline_without_team_context_is_not_flagged():
    # Game totals carry no team on purpose; the guard must not fire there.
    card = ev_card(market_type="GAMELINE", market="TOTAL", proposition="TOTAL",
                   matchup="PDX @ MIN", line=160.5)
    ev = [{
        "market_id": "m1", "outcome_id": "o1", "book": "FD",
        "book_odds": 110, "book_decimal_odds": 2.1, "calculated_ev_pct": 0.05,
    }]
    row = make_row(card, ev)
    assert row is not None
    assert "team_enrichment_failed" not in row["data_quality_flags"]


def test_market_label_disambiguates_terse_code():
    # 'PT' reads as basketball points but is Pitches Thrown; market_label spells it out.
    card = _ctx_card(
        "ATL", "STL", "ATL @ STL", market="PT", market_raw="Pitches Thrown",
        market_label="Test Pitcher - Pitches Thrown",
    )
    row = make_row(card, [], sport="MLB")
    assert "Pitches Thrown" in row["market_label"]
    assert row["home_away"] == "AWAY"  # ATL is the away token in 'ATL @ STL'


# --- Signed spread/run-line rendering (fix: models disagreed on -1.5 vs +1.5) --

def _spread_card(side, line, **extra):
    card = {
        "headline_side": side,
        "card_id": "rl1",
        "market_id": "rl1",
        "board": "A",
        "market_type": "GAMELINE",
        "market": "SPREAD",
        "proposition": "SPREAD",
        "market_label": "Run Line",
        "team": "ATL",
        "matchup": "ATL @ STL",
        "event_id": "evRL",
        "flags": [],
        "sides": {
            side: {
                "outcome_id": f"o{side}",
                "line": line,
                "best_odds": -170,
                "ev": {
                    "is_alt_line_fallback": False,
                    "devig_decimal": 1.6,
                    "best_ev_pct": 0.046,
                    "kelly_pct": 0.02,
                },
            }
        },
    }
    card.update(extra)
    return card


def test_positive_spread_line_renders_with_explicit_sign():
    # This is the exact ATL @ STL card from the 2026-07-12 report divergence:
    # AWAY side, line=1.5, priced at -170 (the AWAY side is favored to cover the
    # generous +1.5 cushion). Without an explicit '+' this reads as an ambiguous
    # bare magnitude, which is why five reports rendered it as -1.5 and +1.5.
    row = make_row(_spread_card("AWAY", 1.5), [])
    assert row["line"] == "+1.5"
    assert row["selection"] == "ATL @ STL Run Line AWAY +1.5"


def test_negative_spread_line_keeps_explicit_sign():
    row = make_row(_spread_card("HOME", -1.5), [])
    assert row["line"] == "-1.5"
    assert row["selection"] == "ATL @ STL Run Line HOME -1.5"


def test_non_spread_line_is_not_signed():
    # A positive TOTAL/prop line must never gain a '+' — only spread/run-line/
    # puck-line markets carry a signed margin.
    card = _ctx_card("ATL", "STL", "ATL @ STL", market="TOTAL", market_raw="Total")
    row = make_row(card, [], sport="MLB")
    assert row["line"] == 6.5  # unchanged float, no sign added
    assert "+" not in row["selection"]


def test_alt_line_fallback_surfaces_priced_line():
    # Shown line 9.0 but EV/price derived at 8.5 -> priced_line + annotated flag.
    card = {
        "headline_side": "OVER",
        "card_id": "g1",
        "market_id": "g1",
        "board": "A",
        "market_type": "GAMELINE",
        "market": "TOTAL",
        "matchup": "ATH @ CWS",
        "event_id": "evG",
        "flags": ["ev_line_fallback"],
        "sides": {
            "OVER": {
                "outcome_id": "oMain",
                "line": 9.0,
                "best_odds": -102,
                "ev": {
                    "is_alt_line_fallback": True,
                    "best_record_id": "recAlt",
                    "best_ev_pct": 0.07,
                    "ev_source": "OUTLIER",
                },
            }
        },
    }
    ev = [{
        "market_id": "g1", "outcome_id": "oAlt", "side": "OVER",
        "current_line": 8.5, "record_id": "recAlt", "book": "FD", "book_odds": -110,
    }]
    row = make_row(card, ev)
    assert row["line"] == 9.0  # display line unchanged
    assert str(row["priced_line"]) == "8.5"
    assert "ev_line_fallback:priced_at=8.5" in row["data_quality_flags"]


def test_alt_line_fallback_priced_line_is_signed_for_spread():
    # Same alt-line-fallback path as above, but on a SPREAD market: the
    # priced_line/ev_line_fallback annotation must carry an explicit sign too,
    # or a positive fallback line reintroduces the exact ambiguity this fix closes.
    card = _spread_card(
        "AWAY",
        1.5,
        flags=["ev_line_fallback"],
    )
    card["sides"]["AWAY"]["ev"]["is_alt_line_fallback"] = True
    card["sides"]["AWAY"]["ev"]["best_record_id"] = "recAlt"
    ev = [{
        "market_id": "rl1", "outcome_id": "oAWAY", "side": "AWAY",
        "current_line": 2.5, "record_id": "recAlt", "book": "FD", "book_odds": -170,
    }]
    row = make_row(card, ev)
    assert row["line"] == "+1.5"
    assert row["priced_line"] == "+2.5"
    assert "ev_line_fallback:priced_at=+2.5" in row["data_quality_flags"]


def test_dossier_and_briefing_show_matchup_not_bare_hash():
    card = _ctx_card("LAS", "CHI", "CHI @ LAS", market="REB", market_raw="Rebounds")
    row = make_row(card, [], sport="WNBA")
    row["_board"] = "board_a"
    row["_event_starts_at"] = "2026-07-10T22:00:00Z"

    dossier = build_dossier([row], "WNBA")
    assert "Los Angeles Sparks" in dossier and "Chicago Sky" in dossier

    briefing = build_briefing([row], "2026-07-10")
    # Slate index carries the human matchup alongside the event id.
    assert "Los Angeles Sparks" in briefing
    assert "event ev1" in briefing


# --- Data-quality validation flags (ISSUES.md follow-ups #2, #3) -------------

def _dq_card(proposition, line, market_raw=None, market_type="PLAYER_PROP",
             player_id="p1", **extra):
    card = {
        "headline_side": "OVER",
        "card_id": "d1",
        "market_id": "d1",
        "board": "A",
        "player": "Test Player",
        "player_id": player_id,
        "market_type": market_type,
        "market": None,
        "proposition": proposition,
        "market_raw": market_raw or proposition.title(),
        "team": "ATL",
        "opponent": "STL",
        "matchup": "ATL @ STL",
        "event_id": "ev1",
        "flags": [],
        "sides": {
            "OVER": {"outcome_id": "o1", "line": line, "best_odds": -110,
                     "ev": {"is_alt_line_fallback": False}}
        },
    }
    card.update(extra)
    return card


def test_cross_sport_market_flagged_not_dropped():
    # A basketball REBOUNDS proposition on an MLB event is a data artifact.
    row = make_row(_dq_card("REBOUNDS", 6.5, market_raw="Rebounds"), [])
    assert row is not None  # flagged, never hard-dropped
    assert "cross_sport_market:WNBA" in row["data_quality_flags"]


def test_valid_market_not_falsely_flagged():
    # A real, high-but-plausible MLB line (134.5 pitches thrown) must NOT flag.
    row = make_row(_dq_card("PITCHES_THROWN", 134.5, market_raw="Pitches Thrown"), [])
    assert row["data_quality_flags"] == ""


def test_implausible_player_prop_line_flagged():
    row = make_row(_dq_card("HITS", 999, market_raw="Hits"), [])
    assert "implausible_line" in row["data_quality_flags"]


def test_market_validation_flags_helper_is_deterministic():
    card = {"proposition": "REBOUNDS", "market_raw": "Rebounds"}
    flags = market_validation_flags("MLB", card, {}, None, "PLAYER_PROP", "p1", 6.5)
    assert flags == ["cross_sport_market:WNBA"]
    # Game/team totals with big lines are not player props -> no implausible flag.
    assert market_validation_flags("WNBA", {"proposition": "TOTAL"}, {}, "TOTAL",
                                   "GAMELINE", None, 168.5) == []


def test_nan_line_is_flagged_non_numeric():
    # A NaN line parses without error but compares False everywhere; it must not
    # slip past the ceiling check unflagged.
    nan = float("nan")
    card = {"proposition": "HITS", "market_raw": "Hits"}
    assert market_validation_flags("MLB", card, {}, None, "PLAYER_PROP", "p1", nan) == [
        "non_numeric_line"
    ]
    assert market_validation_flags("MLB", card, {}, None, "PLAYER_PROP", "p1", "NaN") == [
        "non_numeric_line"
    ]


def test_lm_status_names_missing_markets():
    import datetime
    report = {
        "status": "partial",
        "generated_at": datetime.datetime.now().astimezone().isoformat(),
        "markets_fetched": 90,
        "markets_requested": 100,
        "fetch_error_count": 3,
        "error_market_ids": ["aaa", "bbb", "ccc"],
    }
    ok, msg = _summarize_lm_status(report, "MLB props LM")
    assert ok is False
    assert "missing markets: aaa, bbb, ccc" in msg


def test_lm_status_caps_and_counts_extra_missing_markets():
    import datetime
    ids = [f"m{i}" for i in range(12)]
    report = {
        "status": "partial",
        "generated_at": datetime.datetime.now().astimezone().isoformat(),
        "markets_fetched": 88,
        "markets_requested": 100,
        "fetch_error_count": 12,
        "error_market_ids": ids,
    }
    _, msg = _summarize_lm_status(report, "MLB props LM")
    assert "(+4 more)" in msg  # 12 total, first 8 shown
# 26. WNBA home-away unresolved flag.
def test_home_away_unresolved_flag():
    card = ev_card(market_type="MONEYLINE")
    # Well-formed matchup where the team EXACTLY matches one of the sides
    card["matchup"] = "LVA @ NYL"
    card["team"] = "LVA"
    row = make_row(card, [])
    assert row["home_away"] == "AWAY"
    assert "HOME_AWAY_UNRESOLVED" not in row["data_quality_flags"]

    # Malformed matchup / unresolvable team alias
    card["team"] = "LV"  # LV instead of LVA
    row2 = make_row(card, [])
    assert row2["home_away"] == ""
    assert "HOME_AWAY_UNRESOLVED" in row2["data_quality_flags"]


def test_write_pack_validation_atomic(tmp_path):
    # Bug 4 Regression Test: write_pack must not delete existing valid artifacts if validation fails.
    out_dir = tmp_path / "packs" / "2026-07-20"
    out_dir.mkdir(parents=True)
    dossiers_dir = out_dir / "dossiers"
    dossiers_dir.mkdir()
    
    # Seed with existing artifacts
    (out_dir / "candidates.csv").write_text("dummy", encoding="utf-8")
    (dossiers_dir / "test_dossier.md").write_text("dummy", encoding="utf-8")
    
    # Invalid candidate row that will trigger ValidationError
    invalid_rows = [{"sport": "MLB"}]
    
    from outlier_scrapers.schema import ValidationError
    with pytest.raises(ValidationError, match="Critical schema compatibility violation"):
        write_pack(rows=invalid_rows, out_dir=out_dir)
        
    # Verify the artifacts are STILL THERE because validation failed BEFORE unlinking
    assert (out_dir / "candidates.csv").exists()
    assert (dossiers_dir / "test_dossier.md").exists()

# historical_edge_pct: descriptive edge from the raw recency hit rate.
def test_historical_edge_pct_column_position():
    # Sits right after edge_pct so the two are adjacent when eyeballing the CSV.
    assert (
        CANDIDATES_HEADER[CANDIDATES_HEADER.index("edge_pct") + 1]
        == "historical_edge_pct"
    )
    # Must not displace the pinned last column.
    assert CANDIDATES_HEADER[-1] == "source_timestamps"


def test_historical_edge_pct_blank_when_hit_data_missing():
    # Regression: signal_score() defaults hit_component to 50.0 when Outlier
    # has no recency data. That sentinel must NOT leak into historical_edge_pct.
    card = ev_card()
    card["sides"]["OVER"]["signal"] = {"hit_component": 50.0, "hit_pct": None}
    row = make_row(card, [])
    assert row is not None
    assert row["historical_edge_pct"] == ""


def test_historical_edge_pct_populated_from_raw_hit_pct():
    card = ev_card()
    card["sides"]["OVER"]["signal"] = {"hit_component": 62.0, "hit_pct": 62.0}
    row = make_row(card, [])
    assert row is not None
    dec = float(row["decimal_price"])
    push = float(row["push_prob"]) if row["push_prob"] not in ("", None) else 0.0
    expected = compute_historical_edge(0.62, dec, push)
    assert expected is not None
    assert row["historical_edge_pct"] != ""
    assert float(row["historical_edge_pct"]) == pytest.approx(expected, abs=1e-4)


def test_swap_staged_pack_retries_on_permission_error(tmp_path):
    staging_dir = tmp_path / "staging"
    out_dir = tmp_path / "out"
    staging_dir.mkdir()
    out_dir.mkdir()
    
    with mock.patch("outlier_scrapers.pack.os.replace") as mock_replace, \
         mock.patch("outlier_scrapers.pack.time.sleep") as mock_sleep:
        # First call for backup succeeds, second call for swap fails once then succeeds
        mock_replace.side_effect = [None, PermissionError("locked"), None]
        backup_dir = _swap_staged_pack(staging_dir, out_dir)
        
        assert backup_dir is not None
        assert mock_replace.call_count == 3
        mock_sleep.assert_called_once()

def test_swap_staged_pack_exhausts_retries(tmp_path):
    staging_dir = tmp_path / "staging"
    out_dir = tmp_path / "out"
    staging_dir.mkdir()
    out_dir.mkdir()
    
    with mock.patch("outlier_scrapers.pack.os.replace") as mock_replace, \
         mock.patch("outlier_scrapers.pack.time.sleep") as mock_sleep:
        # First call for backup fails consistently
        mock_replace.side_effect = PermissionError("locked")
        
        with pytest.raises(PermissionError):
            _swap_staged_pack(staging_dir, out_dir)
            
        assert mock_replace.call_count == 10
        assert mock_sleep.call_count == 10

def test_swap_staged_pack_immediate_rollback_on_other_error(tmp_path):
    staging_dir = tmp_path / "staging"
    out_dir = tmp_path / "out"
    staging_dir.mkdir()
    out_dir.mkdir()
    
    with mock.patch("outlier_scrapers.pack.os.replace") as mock_replace:
        def replace_side_effect(src, dst):
            if str(src) == str(staging_dir):
                raise ValueError("other error")
            Path(src).rename(dst)
            
        mock_replace.side_effect = replace_side_effect
        
        with pytest.raises(ValueError):
            _swap_staged_pack(staging_dir, out_dir)
            
        assert mock_replace.call_count == 3

def test_restore_published_pack_with_transient_lock(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    
    with mock.patch("outlier_scrapers.pack.shutil.rmtree") as mock_rmtree, \
         mock.patch("outlier_scrapers.pack.os.replace") as mock_replace, \
         mock.patch("outlier_scrapers.pack.time.sleep") as mock_sleep:
         
        # simulate transient lock on rmtree then success
        mock_rmtree.side_effect = [PermissionError("lock"), None]
        # simulate transient lock on replace then success
        mock_replace.side_effect = [PermissionError("lock"), None]
        
        _restore_published_pack(out_dir, backup_dir)
        
        assert mock_rmtree.call_count == 2
        assert mock_replace.call_count == 2
        assert mock_sleep.call_count == 2

def test_second_enforce_pack_write_refused_without_reserved_exposure(tmp_path):
    out_dir = tmp_path / "2026-07-25"
    out_dir.mkdir()
    sidecar_path = out_dir / "portfolio_risk.json"
    sidecar_path.write_text('{"mode": "enforce"}', encoding="utf-8")
    
    # Mock config to be enforce
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    policy_path = config_dir / "portfolio_risk.json"
    policy_path.write_text("""{
    "schema_version": "1.0",
    "policy_version": "1.0",
    "mode": "enforce",
    "streams_in_scope": ["candidates", "game_totals", "team_totals"],
    "stake_increment": 0.5,
    "max_wager_units": 1.0,
    "max_daily_units": 20.0,
    "max_event_units": 4.0,
    "max_player_units": 3.0,
    "max_team_units": 5.0,
    "max_market_type_units": 6.0,
    "max_correlated_cluster_units": 5.0,
    "max_book_units": 8.0,
    "non_authoritative_book_policy": "flag_and_report_only",
    "shadow_multipliers_neutral": true
}""", encoding="utf-8")
    
    import outlier_scrapers.paths as P
    original_project_root = P.PROJECT_ROOT
    P.PROJECT_ROOT = tmp_path
    
    try:
        with pytest.raises(ValueError, match="Enforce pack already exists"):
            write_pack([], out_dir)
    finally:
        P.PROJECT_ROOT = original_project_root

def test_enforce_refused_when_shadow_window_less_than_14_days(tmp_path):
    out_dir = tmp_path / "2026-07-25"
    out_dir.mkdir()
    
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    policy_path = config_dir / "portfolio_risk.json"
    policy_path.write_text("""{
    "schema_version": "1.0",
    "policy_version": "1.0",
    "mode": "enforce",
    "streams_in_scope": ["candidates", "game_totals", "team_totals"],
    "stake_increment": 0.5,
    "max_wager_units": 1.0,
    "max_daily_units": 20.0,
    "max_event_units": 4.0,
    "max_player_units": 3.0,
    "max_team_units": 5.0,
    "max_market_type_units": 6.0,
    "max_correlated_cluster_units": 5.0,
    "max_book_units": 8.0,
    "non_authoritative_book_policy": "flag_and_report_only",
    "shadow_multipliers_neutral": true
}""", encoding="utf-8")
    
    import outlier_scrapers.paths as P
    original_project_root = P.PROJECT_ROOT
    P.PROJECT_ROOT = tmp_path
    
    try:
        with pytest.raises(ValueError, match="feedback.sqlite3 not found"):
            write_pack([], out_dir)
            
        # Create an empty db
        db_dir = tmp_path / "calibration"
        db_dir.mkdir()
        db_path = db_dir / "feedback.sqlite3"
        import sqlite3
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE market_snapshots (captured_at TEXT)")
            
        with pytest.raises(ValueError, match="only 0 days of shadow history found"):
            write_pack([], out_dir)
            
        # Insert 13 days
        with sqlite3.connect(db_path) as conn:
            for i in range(1, 14):
                conn.execute(f"INSERT INTO market_snapshots VALUES ('2026-07-{i:02d}T12:00:00Z')")
                
        with pytest.raises(ValueError, match="only 13 days of shadow history found"):
            write_pack([], out_dir)
            
        # Insert 14th day
        with sqlite3.connect(db_path) as conn:
            conn.execute("INSERT INTO market_snapshots VALUES ('2026-07-14T12:00:00Z')")
            
        # Should not raise ValueError about 14 days
        try:
            write_pack([], out_dir)
        except Exception as e:
            if "shadow history" in str(e):
                pytest.fail(f"Unexpected error: {e}")
    finally:
        P.PROJECT_ROOT = original_project_root
