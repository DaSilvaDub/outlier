"""Unit tests for the alt team-total L10 board and parlay suggestions."""
from datetime import datetime, timezone

from outlier_scrapers.alt_team_totals import (
    build_alt_team_total_board,
    build_alt_team_total_parlays,
    extract_l10,
    format_alt_team_totals_md,
    is_alt_team_total_record,
)

NOW = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)
FUTURE_START = "2026-07-14T23:00:00+00:00"
PAST_START = "2026-07-14T01:00:00+00:00"


def _l10_stats(hits: int, total: int = 10, side: str = "homeSummaryStat") -> dict:
    results = [True] * hits + [False] * (total - hits)
    return {side: {"l10": hits / total, "l10Results": results}}


def _tt_record(
    line: float,
    position: str = "OVER",
    *,
    team: str = "Aces",
    stats: dict | None = None,
    books: list[dict] | None = None,
    market_id: str = "m1",
    outcome_id: str | None = None,
    event_id: str = "E1",
    event_starts_at: str | None = FUTURE_START,
    market_type: str = "TEAM_PROP",
    proposition: str = "POINTS",
    matchup: str = "Liberty @ Aces",
    **kwargs,
) -> dict:
    d = {
        "market_id": market_id,
        "outcome_id": outcome_id or f"{market_id}:{line}:{position}",
        "event_id": event_id,
        "event_starts_at": event_starts_at,
        "market_type": market_type,
        "proposition": proposition,
        "market": proposition,
        "scope": "full_game",
        "line": line,
        "position": position,
        "team": team,
        "matchup": matchup,
        "books": books if books is not None else [{"book": "dk", "odds": -400}],
        "stats": stats if stats is not None else _l10_stats(10),
    }
    d.update(kwargs)
    return d


def _games_norm(records: list[dict]) -> dict:
    return {"generated_at": "2026-07-14T10:00:00+00:00", "records": records}


def test_record_filter_league_propositions():
    assert is_alt_team_total_record(_tt_record(80.5), league="WNBA")
    assert is_alt_team_total_record(_tt_record(4.5, proposition="RUNS"), league="MLB")
    assert not is_alt_team_total_record(_tt_record(4.5, proposition="RUNS"), league="WNBA")
    assert not is_alt_team_total_record(
        _tt_record(160.5, market_type="GAMELINE", proposition="TOTAL"), league="WNBA"
    )
    assert not is_alt_team_total_record(
        _tt_record(40.5, scope="2H", period_label="2H"), league="WNBA"
    )
    # MLB team totals are total runs: accept the raw variants and the canonical
    # market alias, but never the GAMELINE game total or WNBA runs tokens.
    assert is_alt_team_total_record(_tt_record(4.5, proposition="TOTAL_RUNS"), league="MLB")
    assert is_alt_team_total_record(_tt_record(4.5, proposition="TOTAL"), league="MLB")
    r_alias = _tt_record(4.5, proposition="", market="R")
    assert is_alt_team_total_record(r_alias, league="MLB")
    assert not is_alt_team_total_record(
        _tt_record(8.5, market_type="GAMELINE", proposition="TOTAL"), league="MLB"
    )
    assert not is_alt_team_total_record(
        _tt_record(80.5, proposition="TOTAL_RUNS"), league="WNBA"
    )


def test_board_emits_mlb_total_runs_ladder():
    records = [
        _tt_record(2.5, proposition="TOTAL_RUNS", team="Yankees",
                   matchup="Yankees @ Red Sox",
                   stats=_l10_stats(10, side="awaySummaryStat")),
        _tt_record(3.5, proposition="TOTAL_RUNS", team="Yankees",
                   matchup="Yankees @ Red Sox",
                   stats=_l10_stats(9, side="awaySummaryStat")),
    ]
    rows = build_alt_team_total_board(_games_norm(records), league="MLB", now=NOW)
    assert [r["line"] for r in rows] == [3.5, 2.5]
    assert [r["l10_hits"] for r in rows] == [9, 10]
    assert rows[0]["is_best_line"] == "true"


def test_extract_l10_prefers_results_array():
    rec = _tt_record(
        80.5, stats={"homeSummaryStat": {"l10": 0.5, "l10Results": [True] * 9 + [False]}}
    )
    l10 = extract_l10(rec)
    assert l10 == {"hits": 9, "total": 10, "pct": 90.0, "source": "l10Results", "flag": None}


def test_extract_l10_pct_fallback_and_short_sample():
    pct_only = extract_l10(_tt_record(80.5, stats={"homeSummaryStat": {"l10": 0.9}}))
    assert pct_only is not None
    assert pct_only["pct"] == 90.0
    assert pct_only["source"] == "l10"
    short = extract_l10(
        _tt_record(80.5, stats={"homeSummaryStat": {"l10Results": [True] * 7}})
    )
    assert short is not None
    assert short["flag"] == "SHORT_SAMPLE"
    assert short["pct"] == 100.0
    assert extract_l10(_tt_record(80.5, stats={})) is None


def test_extract_l10_side_selection():
    both = {
        "homeSummaryStat": {"l10Results": [True] * 10},
        "awaySummaryStat": {"l10Results": [True] * 5 + [False] * 5},
    }
    home = extract_l10(_tt_record(80.5, team="Aces", stats=both))
    assert home is not None and home["pct"] == 100.0
    away = extract_l10(_tt_record(80.5, team="Liberty", stats=both))
    assert away is not None and away["pct"] == 50.0
    ambiguous = extract_l10(_tt_record(80.5, team="Sparks", stats=both))
    assert ambiguous == {"flag": "AMBIGUOUS_STATS_SIDE"}


def test_board_band_boundaries():
    records = [
        _tt_record(78.5, stats=_l10_stats(10)),   # 100% in
        _tt_record(80.5, stats=_l10_stats(9)),    # 90% in
        _tt_record(84.5, stats=_l10_stats(8)),    # 80% out
    ]
    rows = build_alt_team_total_board(_games_norm(records), league="WNBA", now=NOW)
    assert [r["line"] for r in rows] == [80.5, 78.5]
    assert all(90.0 <= r["l10_pct"] <= 100.0 for r in rows)


def test_board_best_line_is_highest_qualifying():
    records = [
        _tt_record(76.5, stats=_l10_stats(10)),
        _tt_record(78.5, stats=_l10_stats(10)),
        _tt_record(80.5, stats=_l10_stats(9)),
    ]
    rows = build_alt_team_total_board(_games_norm(records), league="WNBA", now=NOW)
    best = [r for r in rows if r["is_best_line"] == "true"]
    assert len(best) == 1
    assert best[0]["line"] == 80.5


def test_board_skips_started_unpriced_under_and_flags_integer_lines():
    records = [
        _tt_record(80.5, event_id="E9", event_starts_at=PAST_START),
        _tt_record(80.5, "UNDER", team="Liberty", market_id="m2"),
        _tt_record(80.0, market_id="m3", team="Sky", matchup="Sky @ Mercury",
                   stats=_l10_stats(10, side="awaySummaryStat")),
        _tt_record(78.5, market_id="m4", team="Wings", matchup="Wings @ Lynx",
                   stats=_l10_stats(10, side="awaySummaryStat"), books=[]),
    ]
    rows = build_alt_team_total_board(_games_norm(records), league="WNBA", now=NOW)
    by_team = {r["team"]: r for r in rows}
    assert "Aces" not in by_team          # started event dropped
    assert "Liberty" not in by_team       # UNDER rows never qualify
    assert "INTEGER_LINE_PUSH_RISK" in by_team["Sky"]["quality_flags"]
    assert "NO_PRICE" in by_team["Wings"]["quality_flags"]
    assert by_team["Wings"]["decimal_price"] == ""


def test_board_best_price_across_books():
    records = [
        _tt_record(80.5, books=[{"book": "dk", "odds": -450}, {"book": "fd", "odds": -380}]),
    ]
    rows = build_alt_team_total_board(_games_norm(records), league="WNBA", now=NOW)
    assert rows[0]["best_book"] == "fd"
    assert rows[0]["best_price"] == -380
    assert rows[0]["books_count"] == 2


def test_board_dedupes_duplicate_market_lines():
    records = [
        _tt_record(80.5, market_id="m1"),
        _tt_record(80.5, market_id="m1b"),  # same logical market, same line
    ]
    rows = build_alt_team_total_board(_games_norm(records), league="WNBA", now=NOW)
    assert len(rows) == 1


def test_board_empty_slate():
    assert build_alt_team_total_board(_games_norm([]), league="WNBA", now=NOW) == []
    assert build_alt_team_total_board(None, league="WNBA", now=NOW) == []


def _board_rows() -> list[dict]:
    records = [
        _tt_record(84.5, team="Aces", stats=_l10_stats(10),
                   books=[{"book": "dk", "odds": -450}]),
        _tt_record(79.5, team="Liberty", market_id="m2",
                   stats=_l10_stats(9, side="awaySummaryStat"),
                   books=[{"book": "fd", "odds": -380}]),
        _tt_record(70.5, team="Sky", market_id="m3", event_id="E2",
                   matchup="Sky @ Mercury",
                   stats=_l10_stats(10, side="awaySummaryStat"),
                   books=[{"book": "dk", "odds": -500}]),
    ]
    return build_alt_team_total_board(_games_norm(records), league="WNBA", now=NOW)


def test_parlays_two_leg_combinations_and_sgp_flag():
    rows = _board_rows()
    parlays = build_alt_team_total_parlays(rows)
    assert len(parlays) == 3  # C(3, 2)
    sgp = [p for p in parlays if p["is_sgp"] == "true"]
    assert len(sgp) == 1  # Aces + Liberty share E1
    assert "SGP_CORRELATED_LEGS" in sgp[0]["quality_flags"]
    assert sgp[0]["event_ids"] == "E1"


def test_parlay_math():
    rows = _board_rows()
    parlays = build_alt_team_total_parlays(rows)
    sgp = next(p for p in parlays if p["is_sgp"] == "true")
    # dec(-450) = 1.2222..., dec(-380) = 1.2631...
    expected = (100.0 / 450.0 + 1.0) * (100.0 / 380.0 + 1.0)
    assert abs(sgp["combined_decimal"] - round(expected, 4)) < 1e-9
    assert sgp["combined_american"] == -184  # 1.5439 decimal
    assert abs(sgp["naive_l10_prob"] - 90.0) < 1e-9  # 1.0 * 0.9
    assert parlays[0]["naive_l10_prob"] >= parlays[-1]["naive_l10_prob"]


def test_parlays_exclude_flagged_and_non_best_rows():
    records = [
        _tt_record(84.5, team="Aces", stats=_l10_stats(10)),
        _tt_record(82.5, team="Aces", stats=_l10_stats(10)),  # not best line
        _tt_record(70.0, team="Sky", market_id="m3", event_id="E2",
                   matchup="Sky @ Mercury",
                   stats=_l10_stats(10, side="awaySummaryStat")),  # integer line
        _tt_record(79.5, team="Wings", market_id="m4", event_id="E3",
                   matchup="Wings @ Lynx",
                   stats=_l10_stats(10, side="awaySummaryStat"), books=[]),  # no price
    ]
    rows = build_alt_team_total_board(_games_norm(records), league="WNBA", now=NOW)
    parlays = build_alt_team_total_parlays(rows)
    assert parlays == []  # only Aces is eligible; no pair possible


def test_board_target_date_scoping_and_no_cross_date_parlays():
    from outlier_scrapers.pack import _local_date

    other_start = "2026-07-16T23:00:00+00:00"
    records = [
        _tt_record(80.5, team="Aces"),
        _tt_record(75.5, team="Mercury", market_id="m2", event_id="E2",
                   matchup="Sky @ Mercury", event_starts_at=other_start),
    ]
    target = _local_date(FUTURE_START)
    rows = build_alt_team_total_board(
        _games_norm(records), league="WNBA", now=NOW, target_date=target
    )
    assert [r["team"] for r in rows] == ["Aces"]
    assert build_alt_team_total_parlays(rows) == []
    # Without a date bound both slates are emitted (CLI/pack always pass one).
    unbounded = build_alt_team_total_board(_games_norm(records), league="WNBA", now=NOW)
    assert {r["team"] for r in unbounded} == {"Aces", "Mercury"}


def test_board_drops_inactive_markets():
    records = [
        _tt_record(80.5, team="Aces", is_active=False),
        _tt_record(75.5, team="Mercury", market_id="m2", event_id="E2",
                   matchup="Sky @ Mercury", is_active=True),
        _tt_record(70.5, team="Wings", market_id="m3", event_id="E3",
                   matchup="Wings @ Lynx",
                   stats=_l10_stats(10, side="awaySummaryStat")),  # unknown stays
    ]
    rows = build_alt_team_total_board(_games_norm(records), league="WNBA", now=NOW)
    assert {r["team"] for r in rows} == {"Mercury", "Wings"}
    parlays = build_alt_team_total_parlays(rows)
    assert all("Aces" not in p["legs"] for p in parlays)


def test_parlays_exclude_short_sample_rows():
    records = [
        _tt_record(84.5, team="Aces", stats=_l10_stats(10)),
        _tt_record(75.5, team="Mercury", market_id="m2", event_id="E2",
                   matchup="Sky @ Mercury", stats=_l10_stats(10)),
        _tt_record(70.5, team="Wings", market_id="m3", event_id="E3",
                   matchup="Wings @ Lynx",
                   stats={"awaySummaryStat": {"l10Results": [True]}}),  # 1/1
    ]
    rows = build_alt_team_total_board(_games_norm(records), league="WNBA", now=NOW)
    wings = next(r for r in rows if r["team"] == "Wings")
    assert "SHORT_SAMPLE" in wings["quality_flags"]
    assert wings["l10_hits"] == 1 and wings["l10_total"] == 1
    parlays = build_alt_team_total_parlays(rows)
    assert len(parlays) == 1  # only Aces + Mercury
    assert all("Wings" not in p["legs"] for p in parlays)


def test_write_pack_scopes_alt_board_to_pack_date(tmp_path):
    import csv

    from outlier_scrapers.pack import _local_date, write_pack

    # write_pack filters against the real clock, so use far-future starts.
    pack_start = "2099-07-14T23:00:00+00:00"
    other_start = "2099-07-16T23:00:00+00:00"
    records = [
        _tt_record(80.5, team="Aces", event_starts_at=pack_start),
        _tt_record(75.5, team="Mercury", market_id="m2", event_id="E2",
                   matchup="Sky @ Mercury", event_starts_at=other_start),
    ]
    target = _local_date(pack_start)
    assert target is not None
    out_dir = tmp_path / "packs" / target
    write_pack([], out_dir, games_norm_by_league={"WNBA": _games_norm(records)})
    with open(out_dir / "alt_team_totals.csv", newline="", encoding="utf-8") as fh:
        board = list(csv.DictReader(fh))
    assert [r["team"] for r in board] == ["Aces"]
    with open(out_dir / "alt_team_total_parlays.csv", newline="", encoding="utf-8") as fh:
        assert list(csv.DictReader(fh)) == []


def test_markdown_report():
    rows = _board_rows()
    parlays = build_alt_team_total_parlays(rows)
    md = format_alt_team_totals_md(rows, parlays)
    assert "Aces OVER 84.5" in md
    assert "10/10 L10" in md
    assert "[SGP]" in md
    empty = format_alt_team_totals_md([], [])
    assert "No team-total alt lines" in empty
