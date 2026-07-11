import json

import pytest

from outlier_scrapers import paths as P
from outlier_scrapers.pack import (
    CANDIDATES_HEADER,
    _summarize_lm_status,
    american_to_decimal,
    build_briefing,
    build_dossier,
    build_freshness_section,
    build_injuries,
    build_pack,
    build_row,
    index_ev_by_outcome,
    is_excluded_market,
    is_longshot_price,
    is_no_push_market,
    market_validation_flags,
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

    rows, target, games_norm = build_pack(["MLB", "WNBA"], None, 15, 10)
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
    write_pack(rows, out_dir, games_norm_by_league=games_norm)
    assert (out_dir / "candidates.csv").exists()
    assert (out_dir / "game_totals.csv").exists()
    assert (out_dir / "sections" / "game_totals.md").exists()
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


# 22. House rule: HR / HRR (H+R+RBI) / BB (walks) markets are excluded entirely,
#     matching both normalized short codes and raw proposition tokens.
def test_excluded_markets_dropped():
    for token in ("HR", "HRR", "BB", "HOME_RUNS", "HITS_RUNS_RBIS", "HITSRUNSRBIS", "WALKS"):
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
    assert is_excluded_market(None, "HRR") is True
    assert is_excluded_market("bb", None) is True
    assert is_excluded_market("HITS", "HITS") is False
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
    assert "HR / HRR" in text and "BB" in text
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

    rows, _target, _games_norm = build_pack(["MLB", "WNBA"], None, 15, 10)
    ids = {r["market_id"] for r in rows}
    assert "p1" not in ids  # started event dropped
    assert "gm1" in ids  # independently verified future game remains


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


def test_market_label_disambiguates_terse_code():
    # 'PT' reads as basketball points but is Pitches Thrown; market_label spells it out.
    card = _ctx_card(
        "ATL", "STL", "ATL @ STL", market="PT", market_raw="Pitches Thrown",
        market_label="Test Pitcher - Pitches Thrown",
    )
    row = make_row(card, [], sport="MLB")
    assert "Pitches Thrown" in row["market_label"]
    assert row["home_away"] == "AWAY"  # ATL is the away token in 'ATL @ STL'


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
