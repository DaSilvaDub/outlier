"""Unit tests for the totals ladder + L10 probability model backfill.

Covers the fix for blank edge_pct on OVER game/team total opportunities:
market-consensus devig from the two-sided ladder, an independent recent-games
(L10) probability, and a sample-weighted blend written back onto candidate /
opportunity rows.
"""
from __future__ import annotations

import pytest

from outlier_scrapers.sizing import compute_sizing
from outlier_scrapers.totals_model import (
    BASE_INDEPENDENT_WEIGHT,
    SOURCE_DEVIG,
    backfill_totals_probabilities,
    blend_over_probability,
    build_totals_prob_index,
)


def _rec(
    market_id: str,
    line: float,
    position: str,
    books: list[dict],
    *,
    market_type: str = "GAMELINE",
    proposition: str = "TOTAL",
    event_id: str = "E1",
    stats: dict | None = None,
    **kwargs,
) -> dict:
    d = {
        "market_id": market_id,
        "event_id": event_id,
        "event_starts_at": "2099-12-31T00:00:00Z",
        "market_type": market_type,
        "proposition": proposition,
        "market": proposition,
        "scope": "full_game",
        "line": line,
        "position": position,
        "matchup": "AAA @ BBB",
        "books": books,
    }
    if stats is not None:
        d["stats"] = stats
    d.update(kwargs)
    return d


def _books(odds: int) -> list[dict]:
    return [{"book": "DraftKings", "odds": odds}, {"book": "FanDuel", "odds": odds}]


def _l10_stats(home_hits: int, away_hits: int | None = None) -> dict:
    def blob(hits: int) -> dict:
        results = [True] * hits + [False] * (10 - hits)
        return {"l10": hits / 10.0, "l10Results": results}

    stats = {"homeSummaryStat": blob(home_hits)}
    if away_hits is not None:
        stats["awaySummaryStat"] = blob(away_hits)
    return stats


def _game_norm(records: list[dict]) -> dict:
    return {"generated_at": "2026-07-07T12:00:00Z", "records": records}


# ---------------------------------------------------------------------------
# index construction
# ---------------------------------------------------------------------------

def test_index_builds_per_line_p_over():
    norm = _game_norm(
        [
            _rec("m1", 8.5, "OVER", _books(-110)),
            _rec("m1", 8.5, "UNDER", _books(-110)),
        ]
    )
    index = build_totals_prob_index(norm, league="MLB")
    entry = index["m1"][8.5]
    assert entry["p_over"] == pytest.approx(0.5, abs=0.01)
    assert entry["book_count"] == 2


def test_index_merges_split_market_ids_into_one_ladder():
    norm = _game_norm(
        [
            _rec("m-low", 8.0, "OVER", _books(-140)),
            _rec("m-low", 8.0, "UNDER", _books(120)),
            _rec("m-high", 9.0, "OVER", _books(110)),
            _rec("m-high", 9.0, "UNDER", _books(-130)),
        ]
    )
    index = build_totals_prob_index(norm, league="MLB")
    # Both raw market_ids resolve to the same merged ladder.
    assert set(index["m-low"]) == {8.0, 9.0}
    assert index["m-low"][9.0]["p_over"] == index["m-high"][9.0]["p_over"]


def test_index_game_kind_l10_combines_home_and_away():
    norm = _game_norm(
        [
            _rec("m1", 8.5, "OVER", _books(-110), stats=_l10_stats(8, 6)),
            _rec("m1", 8.5, "UNDER", _books(-110)),
        ]
    )
    entry = build_totals_prob_index(norm, league="MLB")["m1"][8.5]
    l10 = entry["l10_over"]
    assert l10["hits"] == 14
    assert l10["total"] == 20
    assert l10["pct"] == pytest.approx(0.7)


def test_index_team_kind_uses_single_side_blob():
    norm = _game_norm(
        [
            _rec(
                "t1", 4.5, "OVER", _books(-110),
                market_type="TEAM_PROP", proposition="RUNS", team="LAD",
                stats=_l10_stats(4),
            ),
            _rec(
                "t1", 4.5, "UNDER", _books(-110),
                market_type="TEAM_PROP", proposition="RUNS", team="LAD",
            ),
        ]
    )
    entry = build_totals_prob_index(norm, league="MLB")["t1"][4.5]
    assert entry["l10_over"]["pct"] == pytest.approx(0.4)
    assert entry["l10_over"]["total"] == 10


def test_index_team_kind_respects_league_propositions():
    # RUNS is a team-total proposition for MLB but not for WNBA.
    rec_over = _rec(
        "t1", 4.5, "OVER", _books(-110),
        market_type="TEAM_PROP", proposition="RUNS", team="LAD",
    )
    rec_under = _rec(
        "t1", 4.5, "UNDER", _books(-110),
        market_type="TEAM_PROP", proposition="RUNS", team="LAD",
    )
    norm = _game_norm([rec_over, rec_under])
    assert "t1" in build_totals_prob_index(norm, league="MLB")
    assert "t1" not in build_totals_prob_index(norm, league="WNBA")


def test_index_excludes_period_scoped_markets():
    norm = _game_norm(
        [
            _rec("h1", 4.5, "OVER", _books(-110), period_label="1H", periods=[1]),
            _rec("h1", 4.5, "UNDER", _books(-110), period_label="1H", periods=[1]),
        ]
    )
    assert "h1" not in build_totals_prob_index(norm, league="WNBA")


def test_index_derives_push_prob_for_integer_lines():
    norm = _game_norm(
        [
            _rec("m3", 7.5, "OVER", _books(-140)),
            _rec("m3", 7.5, "UNDER", _books(120)),
            _rec("m3", 8.0, "OVER", _books(-110)),
            _rec("m3", 8.0, "UNDER", _books(-110)),
            _rec("m3", 8.5, "OVER", _books(110)),
            _rec("m3", 8.5, "UNDER", _books(-130)),
        ]
    )
    index = build_totals_prob_index(norm, league="MLB")
    assert index["m3"][8.0]["push_prob"] is not None
    assert index["m3"][8.0]["push_prob"] > 0
    assert index["m3"][8.5]["push_prob"] == 0.0


# ---------------------------------------------------------------------------
# blending
# ---------------------------------------------------------------------------

def test_blend_full_sample_uses_base_weight():
    blended, used = blend_over_probability(0.5, {"hits": 10, "total": 10, "pct": 1.0})
    assert used is True
    assert blended == pytest.approx(
        (1 - BASE_INDEPENDENT_WEIGHT) * 0.5 + BASE_INDEPENDENT_WEIGHT * 1.0
    )


def test_blend_short_sample_shrinks_weight():
    blended, _used = blend_over_probability(0.5, {"hits": 5, "total": 5, "pct": 1.0})
    w = BASE_INDEPENDENT_WEIGHT * 0.5
    assert blended == pytest.approx((1 - w) * 0.5 + w * 1.0)


def test_blend_without_l10_returns_market_prob():
    blended, used = blend_over_probability(0.55, None)
    assert used is False
    assert blended == pytest.approx(0.55)


# ---------------------------------------------------------------------------
# row backfill
# ---------------------------------------------------------------------------

def _opportunity_row(**overrides) -> dict:
    row = {
        "sport": "MLB",
        "market_id": "m1",
        "market_type": "GAMELINE",
        "market_label": "Total",
        "selection": "AAA @ BBB Total OVER 8.5",
        "line": 8.5,
        "price": -110,
        "decimal_price": 1.9090909090909092,
        "model_prob": "",
        "model_prob_source": "",
        "market_consensus_prob": "",
        "independent_model_prob": "",
        "final_blended_prob": "",
        "implied_prob": "",
        "edge_pct": "",
        "kelly_025_units": "",
        "max_units": "",
        "push_prob": 0.0,
        "sizing_flags": "",
    }
    row.update(overrides)
    return row


def _norm_by_league(records: list[dict]) -> dict:
    return {"MLB": _game_norm(records)}


def _two_sided(market_id: str = "m1", line: float = 8.5, *, stats: dict | None = None) -> list[dict]:
    return [
        _rec(market_id, line, "OVER", _books(-120), stats=stats),
        _rec(market_id, line, "UNDER", _books(100)),
    ]


def test_backfill_fills_over_total_row_with_blend():
    rows = [_opportunity_row()]
    out = backfill_totals_probabilities(rows, _norm_by_league(_two_sided(stats=_l10_stats(8, 6))))
    row = out[0]
    assert row["model_prob"] != ""
    assert row["model_prob_source"] == SOURCE_DEVIG
    assert row["market_consensus_prob"] != ""
    assert row["independent_model_prob"] == ""
    assert row["recency_hit_prob"] == pytest.approx(0.7)
    assert row["final_blended_prob"] == row["model_prob"]
    assert row["model_prob"] == row["market_consensus_prob"]
    assert row["edge_pct"] != ""
    assert row["implied_prob"] != ""
    sizing = compute_sizing(
        decimal_price=row["decimal_price"],
        model_prob=float(row["model_prob"]),
        push_prob=0.0,
    )
    assert float(row["edge_pct"]) == pytest.approx(sizing.edge_pct)
    assert "totals_ladder_model" in row["sizing_flags"]


def test_backfill_without_l10_uses_devig_source():
    rows = [_opportunity_row()]
    out = backfill_totals_probabilities(rows, _norm_by_league(_two_sided()))
    assert out[0]["model_prob_source"] == SOURCE_DEVIG
    assert out[0]["independent_model_prob"] == ""


def test_backfill_under_side_uses_complement():
    over = _opportunity_row()
    under = _opportunity_row(selection="AAA @ BBB Total UNDER 8.5", price=100, decimal_price=2.0)
    out = backfill_totals_probabilities([over, under], _norm_by_league(_two_sided()))
    p_over = float(out[0]["model_prob"])
    p_under = float(out[1]["model_prob"])
    assert p_over + p_under == pytest.approx(1.0)


def test_backfill_preserves_existing_model_prob():
    row = _opportunity_row(model_prob=0.61, model_prob_source="outlier_devig", edge_pct=0.04)
    out = backfill_totals_probabilities([row], _norm_by_league(_two_sided()))
    assert out[0]["model_prob"] == 0.61
    assert out[0]["model_prob_source"] == "outlier_devig"
    assert out[0]["edge_pct"] == 0.04


def test_backfill_ignores_unindexed_and_non_total_rows():
    three_way = _opportunity_row(
        market_id="tw1", market_label="Total Three Way",
        selection="AAA @ BBB Total Three Way UNDER 9",
    )
    player = _opportunity_row(
        market_id="p1", market_type="PLAYER_PROP",
        selection="Some Player - Hits OVER 1.5",
    )
    out = backfill_totals_probabilities([three_way, player], _norm_by_league(_two_sided()))
    assert out[0]["model_prob"] == ""
    assert out[1]["model_prob"] == ""


def test_backfill_row_without_side_token_is_skipped():
    row = _opportunity_row(selection="AAA @ BBB Total 8.5")
    out = backfill_totals_probabilities([row], _norm_by_league(_two_sided()))
    assert out[0]["model_prob"] == ""


def test_backfill_integer_line_uses_derived_push_prob():
    records = [
        _rec("m3", 7.5, "OVER", _books(-140)),
        _rec("m3", 7.5, "UNDER", _books(120)),
        _rec("m3", 8.0, "OVER", _books(-110)),
        _rec("m3", 8.0, "UNDER", _books(-110)),
        _rec("m3", 8.5, "OVER", _books(110)),
        _rec("m3", 8.5, "UNDER", _books(-130)),
    ]
    row = _opportunity_row(
        market_id="m3", line=8.0, push_prob="",
        selection="AAA @ BBB Total OVER 8.0",
    )
    out = backfill_totals_probabilities([row], _norm_by_league(records))
    filled = out[0]
    assert filled["edge_pct"] != ""
    assert filled["push_prob"] != ""
    assert float(filled["push_prob"]) > 0


def test_backfill_integer_line_without_brackets_sets_flag_only():
    records = [
        _rec("m3", 8.0, "OVER", _books(-110)),
        _rec("m3", 8.0, "UNDER", _books(-110)),
    ]
    row = _opportunity_row(
        market_id="m3", line=8.0, push_prob="",
        selection="AAA @ BBB Total OVER 8.0",
    )
    out = backfill_totals_probabilities([row], _norm_by_league(records))
    filled = out[0]
    assert filled["model_prob"] != ""
    assert filled["edge_pct"] == ""
    assert "push_capable_no_prob" in filled["sizing_flags"]


def test_backfill_single_book_flagged():
    records = [
        _rec("m1", 8.5, "OVER", [{"book": "DraftKings", "odds": -120}]),
        _rec("m1", 8.5, "UNDER", [{"book": "DraftKings", "odds": 100}]),
    ]
    out = backfill_totals_probabilities([_opportunity_row()], _norm_by_league(records))
    assert "totals_single_book" in out[0]["sizing_flags"]


def test_backfill_missing_decimal_price_fills_probs_only():
    row = _opportunity_row(price="", decimal_price="")
    out = backfill_totals_probabilities([row], _norm_by_league(_two_sided()))
    assert out[0]["model_prob"] != ""
    assert out[0]["edge_pct"] == ""


def test_backfill_does_not_mutate_inputs():
    row = _opportunity_row()
    rows = [row]
    backfill_totals_probabilities(rows, _norm_by_league(_two_sided()))
    assert row["model_prob"] == ""
    assert row["edge_pct"] == ""


# ---------------------------------------------------------------------------
# write_pack integration
# ---------------------------------------------------------------------------

def test_write_pack_backfills_over_total_opportunities(tmp_path):
    import csv

    from outlier_scrapers.pack import CANDIDATES_HEADER, write_pack

    row = {k: "" for k in CANDIDATES_HEADER}
    row.update(_opportunity_row())
    write_pack(
        [row],
        tmp_path / "2026-07-19",
        games_norm_by_league=_norm_by_league(_two_sided(stats=_l10_stats(8, 6))),
    )
    with open(tmp_path / "2026-07-19" / "opportunities.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    out = rows[0]
    assert out["edge_pct"] != ""
    assert out["model_prob_source"] == SOURCE_DEVIG
    assert out["independent_model_prob"] == ""
    assert out["recency_hit_prob"] != ""


def test_backfill_flags_totals_model_divergence():
    # Market consensus is 50/50, but L10 hit rate is 8/10 (0.80) -> divergence >= 0.15
    norm = _norm_by_league(_two_sided(stats=_l10_stats(10, 6)))
    row = _opportunity_row(selection="OVER 8.5")
    out = backfill_totals_probabilities([row], norm)
    assert "totals_model_divergence" in out[0]["sizing_flags"]

