import json
from pathlib import Path

from outlier_scrapers import cards
from outlier_scrapers.cards import (
    build_cards_payload,
    movement_corroboration,
    proxy_market_edge,
    recency_hit_pct,
    snapshot_skew,
    two_way_fair,
)


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def test_two_way_fair_removes_vig_symmetric():
    fair = two_way_fair(-110, -110)
    assert fair is not None
    assert round(fair["OVER"], 1) == 50.0
    assert round(fair["UNDER"], 1) == 50.0
    assert fair["overround"] > 100.0  # both -110 carry vig


def test_two_way_fair_none_when_price_missing():
    assert two_way_fair(-110, None) is None


def test_proxy_market_edge_positive_and_labelled():
    # fair 50%, but a +120 book implies only 45.45% -> +4.55% market edge.
    edge = proxy_market_edge("OVER", [{"book": "X", "odds": 120}], 50.0)
    assert edge is not None
    assert edge["edge_pct"] > 0
    assert edge["source"] == "proxy_market"
    assert edge["book"] == "X"


def test_proxy_market_edge_none_without_fair():
    assert proxy_market_edge("OVER", [{"book": "X", "odds": 120}], None) is None


def test_recency_hit_pct_weighted_blend():
    val = recency_hit_pct({"l5_pct": 100, "l10_pct": 90, "l20_pct": 50, "season_pct": 50})
    assert val == 82.0  # .4*100 + .3*90 + .2*50 + .1*50


def test_movement_corroboration_direction():
    toward = movement_corroboration("OVER", {"line_delta_from_open": -1.0, "odds_delta_from_open": -10})
    against = movement_corroboration("OVER", {"line_delta_from_open": 1.0, "odds_delta_from_open": 20})
    assert toward == 1.0
    assert against == -1.0
    assert movement_corroboration("OVER", None) == 0.0


def test_snapshot_skew_flags_large_gap():
    ok = snapshot_skew(
        {
            "props": "2026-06-21T12:00:00-04:00",
            "line_movement": "2026-06-21T12:30:00-04:00",
            "insights": "2026-06-21T12:10:00-04:00",
        }
    )
    assert ok["is_skewed"] is False
    bad = snapshot_skew(
        {
            "props": "2026-06-21T02:00:00-04:00",
            "line_movement": "2026-06-21T16:00:00-04:00",
            "insights": "2026-06-21T15:00:00-04:00",
        }
    )
    assert bad["is_skewed"] is True
    assert bad["skew_hours"] == 14.0


# --------------------------------------------------------------------------- #
# Main-line selection
# --------------------------------------------------------------------------- #


def test_pick_main_line_prefers_movement_tracked_line():
    rows = [
        {"line": 7.5, "best_odds": -210, "books": [{"book": "A"}]},
        {"line": 15.5, "best_odds": 100, "books": [{"book": "A"}, {"book": "B"}]},
        {"line": 19.5, "best_odds": 1600, "books": [{"book": "A"}, {"book": "B"}, {"book": "C"}]},
    ]
    chosen = cards._pick_main_side_row(rows, {"current_line": 15.5}, [])
    assert chosen["line"] == 15.5


def test_pick_main_line_falls_back_to_pickem():
    rows = [
        {"line": 7.5, "best_odds": -800, "books": [{"book": "A"}]},   # implied ~88.9
        {"line": 9.5, "best_odds": 105, "books": [{"book": "A"}]},    # implied ~48.8 (closest to 50)
        {"line": 14.5, "best_odds": 600, "books": [{"book": "A"}, {"book": "B"}]},
    ]
    chosen = cards._pick_main_side_row(rows, None, [])
    assert chosen["line"] == 9.5


# --------------------------------------------------------------------------- #
# End-to-end build (synthetic latest files)
# --------------------------------------------------------------------------- #


def _prop(market_id, side, line, best_odds, outcome_id, **stats):
    return {
        "league": "WNBA",
        "event_id": "e1",
        "market_id": market_id,
        "player": stats.get("player", "Test Player"),
        "player_id": stats.get("player_id", "pl1"),
        "team": "NYL",
        "opponent": "LAS",
        "matchup": "NYL @ LAS",
        "market": stats.get("market", "REB"),
        "market_raw": "Rebounds",
        "side": side,
        "line": line,
        "books": [{"book": "DraftKings", "odds": best_odds}],
        "best_odds": best_odds,
        "l5_pct": stats.get("l5_pct", 60),
        "l10_pct": stats.get("l10_pct", 60),
        "l20_pct": stats.get("l20_pct", 60),
        "h2h_pct": stats.get("h2h_pct", 50),
        "season_pct": stats.get("season_pct", 60),
        "sport_context": {"outcome_id": outcome_id, "orf_score": 0.6, "scope": "full_game"},
    }


def _movement(market_id, side, current_line):
    return {
        "league": "WNBA",
        "market_id": market_id,
        "side": side,
        "current_line": current_line,
        "open_line": current_line,
        "open_odds": -105,
        "current_odds": -110,
        "line_delta_from_open": 0.0,
        "odds_delta_from_open": -5,
        "movement_count": 2,
        "movement_types": ["odds"],
    }


def _ev(market_id, side, ev_pct, outcome_id):
    return {
        "league": "WNBA",
        "market_id": market_id,
        "outcome_id": outcome_id,
        "side": side,
        "calculated_ev_pct": ev_pct,
        "calculated_ev_method": "AVERAGE",
        "kelly_pct": 1.6,
        "vig_pct": 5.0,
        "width_pct": 30.0,
        "devig_odds": -120,
        "current_line": 8.5,
        "current_odds": -106,
        "book": "DraftKings",
        "book_odds": -106,
        "max_bet": 250,
        "book_state": "PREMATCH",
    }


def _insight(market_id, outcome_id, side, line):
    return {
        "league": "WNBA",
        "insight_id": "i1",
        "market_id": market_id,
        "market_outcome_id": outcome_id,
        "player_id": "pl1",
        "side": side,
        "line": line,
        "hit_rate_pct": 80.0,
        "last_n_record": "8/10",
        "relevancy": 90,
        "text": "Test Player has cleared this line in 8 of 10.",
    }


def _write_latest(data_dir: Path, league: str, stem: str, records, extra=None):
    payload = {
        "generated_at": "2026-06-21T12:00:00-04:00",
        "league": league,
        "records": records,
    }
    if extra:
        payload.update(extra)
    out = data_dir / league / "normalized"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{league.lower()}_{stem}_latest.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_build_cards_routes_boards_and_aligns_main_line(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    data_dir = tmp_path / "data"

    # Market mEV: has Outlier EV -> Board A. Two sides at the same line 8.5.
    # Market mSIG: no EV -> Board B. Alt OVER lines 7.5 & 15.5; movement tracks 15.5.
    props = [
        _prop("mEV", "OVER", 8.5, -105, "oEVo"),
        _prop("mEV", "UNDER", 8.5, -105, "oEVu"),
        _prop("mSIG", "OVER", 7.5, -300, "oSIG75", market="PTS", l5_pct=40),
        _prop("mSIG", "OVER", 15.5, 100, "oSIG155", market="PTS", l5_pct=90),
    ]
    movement = [_movement("mEV", "UNDER", 8.5), _movement("mSIG", "OVER", 15.5)]
    ev_records = [_ev("mEV", "UNDER", 1.55, "oEVu")]
    insights = [_insight("mSIG", "oSIG155", "OVER", 15.5)]

    _write_latest(data_dir, "WNBA", "props", props)
    _write_latest(
        data_dir, "WNBA", "line_movement", movement, extra={"ev_records": ev_records}
    )
    _write_latest(data_dir, "WNBA", "insights", insights)

    payload = build_cards_payload("WNBA")

    assert payload["coverage"]["cards_total"] == 2
    assert payload["coverage"]["board_a_cards"] == 1
    assert payload["coverage"]["board_b_cards"] == 1

    a = payload["board_a"][0]
    assert a["card_id"] == "mEV"
    assert a["headline_side"] == "UNDER"
    assert a["rank_metric"] == "calculated_ev_pct"
    assert a["rank_value"] == 1.55
    assert a["sides"]["UNDER"]["ev"]["best_ev_pct"] == 1.55
    # both sides at same line 8.5 -> proxy edge computed
    assert a["sides"]["UNDER"]["proxy_market_edge"] is not None

    b = payload["board_b"][0]
    assert b["card_id"] == "mSIG"
    assert b["headline_side"] == "OVER"
    assert b["rank_metric"] == "signal_composite"
    # main line is the movement-tracked 15.5, not the 7.5 alt
    assert b["sides"]["OVER"]["line"] == 15.5
    assert {a_["line"] for a_ in b["sides"]["OVER"]["alt_lines"]} == {7.5}
    # insight joins to the main line via outcome_id
    assert len(b["sides"]["OVER"]["insights"]) == 1


def test_board_a_card_never_lacks_ev(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    data_dir = tmp_path / "data"
    props = [_prop("m1", "OVER", 2.5, 120, "o1")]
    _write_latest(data_dir, "WNBA", "props", props)
    _write_latest(data_dir, "WNBA", "line_movement", [], extra={"ev_records": []})
    _write_latest(data_dir, "WNBA", "insights", [])

    payload = build_cards_payload("WNBA")
    assert payload["coverage"]["board_a_cards"] == 0
    assert payload["board_b"][0]["headline_side"] == "OVER"


def test_non_average_ev_is_excluded_from_board_a(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    data_dir = tmp_path / "data"
    props = [_prop("m1", "OVER", 8.5, -105, "o1"), _prop("m1", "UNDER", 8.5, -105, "o2")]
    ev = _ev("m1", "OVER", 9.9, "o1")
    ev["calculated_ev_method"] = "WORST"  # not AVERAGE -> ignored
    _write_latest(data_dir, "WNBA", "props", props)
    _write_latest(data_dir, "WNBA", "line_movement", [], extra={"ev_records": [ev]})
    _write_latest(data_dir, "WNBA", "insights", [])

    payload = build_cards_payload("WNBA")
    assert payload["coverage"]["board_a_cards"] == 0


def test_ev_side_anchors_to_ev_line_over_movement(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    data_dir = tmp_path / "data"
    # EV exists only for the 8.5 UNDER, but movement tracks 9.5. The Board A
    # headline must anchor to the EV line (8.5), not the movement line (9.5),
    # so the card never shows one line's price with another line's EV.
    props = [
        _prop("m1", "UNDER", 8.5, -110, "oEV"),
        _prop("m1", "UNDER", 9.5, 100, "o95"),
    ]
    movement = [_movement("m1", "UNDER", 9.5)]
    ev_records = [_ev("m1", "UNDER", 2.0, "oEV")]  # _ev current_line == 8.5

    _write_latest(data_dir, "WNBA", "props", props)
    _write_latest(data_dir, "WNBA", "line_movement", movement, extra={"ev_records": ev_records})
    _write_latest(data_dir, "WNBA", "insights", [])

    payload = build_cards_payload("WNBA")
    assert payload["coverage"]["board_a_cards"] == 1
    side = payload["board_a"][0]["sides"]["UNDER"]
    assert side["line"] == 8.5  # anchored to EV line, not movement's 9.5
    assert side["outcome_id"] == side["ev"]["outcome_id"]
    assert side["ev"]["is_alt_line_fallback"] is False
    assert "ev_line_fallback" not in payload["board_a"][0]["flags"]
    assert {a["line"] for a in side["alt_lines"]} == {9.5}
