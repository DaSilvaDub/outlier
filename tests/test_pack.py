import json

import pytest

from outlier_scrapers import paths as P
from outlier_scrapers.pack import (
    CANDIDATES_HEADER,
    american_to_decimal,
    build_briefing,
    build_injuries,
    build_pack,
    build_row,
    index_ev_by_outcome,
    is_no_push_market,
    rank_rows,
    select_date,
    write_pack,
)

SOURCE_TS = {"cards": "CT", "line_movement": "LMT", "props": "PT"}


def make_row(card, ev_records, sport="MLB", event_starts=None, injuries=None):
    return build_row(
        card,
        ev_records,
        index_ev_by_outcome(ev_records),
        sport,
        "ODDS_TS",
        "NORM_TS",
        SOURCE_TS,
        event_starts or {},
        injuries or {},
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
    assert row["decimal_price"] == 2.1
    assert row["price"] == 110  # same row as the chosen book, not card best_odds
    assert row["book"] == "FD"
    assert isinstance(row["edge_pct"], float)
    assert row["recommended_units_pre_news"] == 1.0
    assert row["sizing_flags"] == ""


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


# 14. Quota ranking never starves board B.
def test_quota_ranking():
    rows = [{"_board": "board_a", "_rank_value": i, "market_id": f"a{i}"} for i in range(20)]
    rows += [{"_board": "board_b", "_rank_value": i, "market_id": f"b{i}"} for i in range(5)]
    out = rank_rows(rows, top_ev_n=15, top_signal_n=10)
    assert sum(1 for r in out if r["_board"] == "board_a") == 15
    assert sum(1 for r in out if r["_board"] == "board_b") == 5
    # deterministic: highest rank first
    assert out[0]["market_id"] == "a19"


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
            "events": {"EG": {"home_team_id": "T1", "away_team_id": "T2"}},
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
        json.dumps({"generated_at": "PN", "records": []})
    )
    (root / "normalized" / f"{low}_games_latest.json").write_text(
        json.dumps({"generated_at": "GN", "context": game_cards["context"]})
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

    rows, target = build_pack(["MLB", "WNBA"], None, 15, 10)
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

    out_dir = tmp_path / "packs" / target
    write_pack(rows, out_dir)
    assert (out_dir / "candidates.csv").exists()
    briefing = (out_dir / "briefing.md").read_text()
    assert "REASONING PASSES (A, D):" in briefing
    assert "Slate index" in briefing
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
