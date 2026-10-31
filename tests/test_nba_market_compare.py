"""SBRO closing-line parsing, joining to ESPN games, and the model-vs-market report.

All fixtures are synthetic rows in the page's layout; no archive content is reproduced here.
"""

from __future__ import annotations

import gzip
from datetime import date, datetime, timedelta, UTC
from pathlib import Path

import numpy as np
import pytest

from outlier_nba.ingest import sbro
from outlier_nba.ingest.espn import GameRow
from outlier_nba.ratings.possessions import build_dataset
from outlier_nba.track import market_compare as mc
from tests.nba_synth import make_league, make_season

HEADER = (
    "<tr>"
    + "".join(
        f"<td>{h}</td>" for h in "Date Rot VH Team 1st 2nd 3rd 4th Final Open Close ML 2H".split()
    )
    + "</tr>"
)


def _row(day, rot, vh, team, final, open_, close, ml, half="50"):
    cells = [day, rot, vh, team, "20", "20", "20", "20", final, open_, close, ml, half]
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def _page(*rows: str) -> str:
    return "<table><tbody>" + HEADER + "".join(rows) + "</tbody></table>"


def _pair(day, away, home, sa, sh, a_open, a_close, h_open, h_close, ml_a, ml_h):
    return _row(day, "501", "V", away, sa, a_open, a_close, ml_a) + _row(
        day, "502", "H", home, sh, h_open, h_close, ml_h
    )


def test_home_favourite_row_holds_the_spread_and_visitor_row_the_total() -> None:
    page = _page(
        _pair(
            "1016",
            "Philadelphia",
            "Boston",
            "87",
            "105",
            "208.5",
            "211.5",
            "5",
            "4.5",
            "170",
            "-200",
        )
    )
    (g,), skipped = sbro.parse_season(page, "2018-19")
    assert skipped == {}
    assert (g["away"], g["home"], g["day"]) == ("PHI", "BOS", date(2018, 10, 16))
    assert g["spread_home"] == -4.5 and g["total_close"] == 211.5 and g["total_open"] == 208.5
    assert g["spread_open_home"] == -5.0 and (g["ml_home"], g["ml_away"]) == (-200, 170)


def test_visitor_favourite_gives_the_home_team_a_positive_handicap() -> None:
    page = _page(
        _pair("0105", "Boston", "Charlotte", "110", "100", "7", "6.5", "222", "224", "-260", "210")
    )
    (g,), _ = sbro.parse_season(page, "2018-19")
    assert g["day"] == date(2019, 1, 5)  # January belongs to the season's end year
    assert g["spread_home"] == 6.5 and g["total_close"] == 224.0


def test_pickem_spread_reads_pk_as_zero() -> None:
    page = _page(
        _pair("1120", "Utah", "Miami", "99", "101", "212", "213", "pk", "pk", "-110", "-110")
    )
    (g,), _ = sbro.parse_season(page, "2018-19")
    assert g["spread_home"] == 0.0 and g["total_close"] == 213.0


def test_unparseable_games_are_skipped_and_counted_not_repaired() -> None:
    page = _page(
        _pair(
            "1016",
            "Philadelphia",
            "Boston",
            "87",
            "105",
            "208.5",
            "211.5",
            "5",
            "4.5",
            "170",
            "-200",
        ),
        _pair(
            "1017", "Atlanta", "Dallas", "90", "95", "5", "4", "4", "3", "100", "-110"
        ),  # no total
        _pair("1018", "Nowhere", "Dallas", "90", "95", "210", "211", "4", "3", "100", "-110"),
        _pair("1019", "Atlanta", "Dallas", "90", "95", "210", "211", "4", "3", "NL", "-110"),
        _row("1020", "1", "N", "Atlanta", "90", "210", "211", "100")
        + _row("1020", "2", "N", "Dallas", "95", "4", "3", "-110"),
    )
    games, skipped = sbro.parse_season(page, "2018-19")
    assert [g["neutral"] for g in games] == [False, True]  # the N pair is a neutral game now
    assert skipped["total_not_identifiable"] == 1 and skipped["unknown_team"] == 1
    assert skipped["missing_moneyline_or_score"] == 1


def test_a_stray_row_that_pairs_with_nothing_is_skipped() -> None:
    page = _page(_row("1016", "1", "V", "Boston", "99", "210", "211", "-110"))
    games, skipped = sbro.parse_season(page, "2018-19")
    assert games == [] and skipped == {}  # a lone trailing row is not even a candidate pair
    page = _page(
        _row("1016", "1", "H", "Boston", "99", "4", "3", "-110"),
        _row("1016", "2", "H", "Dallas", "98", "4", "3", "-110"),
        _row("1016", "3", "V", "Atlanta", "90", "210", "211", "100"),
    )
    games, skipped = sbro.parse_season(page, "2018-19")
    assert games == [] and skipped["not_a_pair"] >= 1


def test_moneyline_devig_sums_to_one_and_orders_correctly() -> None:
    p = sbro.market_probability(-200, 170)
    assert 0.6 < p < 0.7
    assert sbro.market_probability(-200, 170, "multiplicative") == pytest.approx(
        (200 / 300) / (200 / 300 + 100 / 270), abs=1e-9
    )
    assert sbro.market_probability(0, 170) is None


def _espn_game(game_id, day: datetime, home, away, hp, ap) -> GameRow:
    return GameRow(
        game_id, "2018-19", 2, day, home, away, hp, ap, 4, 0, False, 1, "",
        90, 20, 40, 10, 12, 90, 20, 40, 10, 12,
    )  # fmt: skip


def test_join_requires_matching_scores_and_tolerates_a_one_day_date_shift() -> None:
    page = _page(
        _pair(
            "1016",
            "Philadelphia",
            "Boston",
            "87",
            "105",
            "208.5",
            "211.5",
            "5",
            "4.5",
            "170",
            "-200",
        ),
        _pair("1017", "Atlanta", "Dallas", "90", "95", "210", "211", "4", "3", "120", "-140"),
        _pair("1018", "Utah", "Miami", "99", "101", "210", "211", "4", "3", "120", "-140"),
    )
    parsed, _ = sbro.parse_season(page, "2018-19")
    games = [
        _espn_game(
            "g1", datetime(2018, 10, 17, 1, 0, tzinfo=UTC), "BOS", "PHI", 105, 87
        ),  # 21:00 ET on 10-16
        _espn_game(
            "g2", datetime(2018, 10, 17, 23, 0, tzinfo=UTC), "DAL", "ATL", 99, 90
        ),  # score differs
    ]
    lines, stats = sbro.match_games(parsed, games)
    assert set(lines) == {"g1"}
    assert stats["matched"] == 1 and stats["score_mismatch"] == 1 and stats["no_espn_game"] == 1
    assert lines["g1"].spread_home == -4.5 and 0.6 < lines["g1"].p_home_ml < 0.7


def test_fetch_is_one_request_per_uncached_season_and_refuses_other_hosts(tmp_path: Path) -> None:
    calls = []

    def fetch(url: str) -> str:
        calls.append(url)
        return "<html></html>"

    th = sbro.Throttle(burst=1, gap=0, pause=0, sleep=lambda s: None)
    kw = {"cache": tmp_path, "fetch": fetch, "throttle": th, "min_games": 0}
    assert sbro.fetch_seasons(["2018-19", "2019-20"], **kw) == 2
    assert sbro.fetch_seasons(["2018-19"], **kw) == 0
    assert len(calls) == 2 and all(u.startswith(sbro.BASE) for u in calls)
    assert sbro.read_cached("2018-19", tmp_path) == "<html></html>"
    assert sbro.read_cached("2020-21", tmp_path) is None
    with pytest.raises(sbro.EspnError, match="refusing"):
        sbro.http_fetch_text("https://example.com/x")


def test_corrupt_cached_page_is_a_miss(tmp_path: Path) -> None:
    sbro.fetch_seasons(["2018-19"], cache=tmp_path, fetch=lambda u: "x" * 1000,
                       throttle=sbro.Throttle(burst=1, gap=0, pause=0, sleep=lambda s: None),
                       min_games=0)  # fmt: skip
    path = tmp_path / "2018-19.html.gz"
    path.write_bytes(path.read_bytes()[:12])
    assert sbro.read_cached("2018-19", tmp_path) is None


def test_load_market_reports_per_season_joins(tmp_path: Path) -> None:
    page = _page(
        _pair(
            "1016",
            "Philadelphia",
            "Boston",
            "87",
            "105",
            "208.5",
            "211.5",
            "5",
            "4.5",
            "170",
            "-200",
        )
    )
    with gzip.open(tmp_path / "2018-19.html.gz", "wt", encoding="utf-8") as fh:
        fh.write(page)
    games = [_espn_game("g1", datetime(2018, 10, 17, 1, 0, tzinfo=UTC), "BOS", "PHI", 105, 87)]
    lines, report = sbro.load_market(["2018-19", "2019-20"], games, tmp_path)
    assert set(lines) == {"g1"}
    assert report["parser_version"] == sbro.PARSER_VERSION
    assert report["seasons"]["2018-19"]["matched"] == 1
    assert report["seasons"]["2019-20"] == {"cached": False}


# --------------------------------------------------------------------- comparison


def _synthetic(seed=3, edge=0.0):
    """Two seasons; market = noisy truth. ``edge``>0 makes the model see the truth."""
    league = make_league(seed=seed, spread=3.5)
    games = make_season(
        league, "2017-18", datetime(2017, 10, 17, tzinfo=UTC), games_per_team=40, seed=seed
    )
    games += [
        GameRow(**{**g.__dict__, "season": "2018-19", "game_id": "b" + g.game_id,
                   "start_utc": g.start_utc + timedelta(days=400)})
        for g in games
    ]  # fmt: skip
    ds = build_dataset(games)
    rng = np.random.default_rng(seed)
    truth = np.clip(0.5 + 0.1 * rng.normal(size=len(ds)), 0.1, 0.9)
    won = (ds.hp > ds.ap).astype(float)
    market = np.clip(truth + rng.normal(0, 0.05, len(ds)), 0.05, 0.95)
    model = np.where(rng.uniform(size=len(ds)) < edge, won * 0.7 + 0.15, market)
    lines = {
        g.game_id: sbro.MarketLine(
            g.game_id, g.season, date(2018, 1, 1) + timedelta(days=i % 120), g.home, g.away,
            float(market[i]), -110, -110, -3.0, 220.0, 219.0, -3.5,
        )
        for i, g in enumerate(ds.games)
    }  # fmt: skip
    return ds, lines, market, model


def test_report_is_explicitly_unavailable_without_overlap() -> None:
    ds, _lines, market, model = _synthetic()
    out = mc.market_report(
        ds, {}, {"sim": model}, np.zeros(len(ds)), np.zeros(len(ds)), ["2018-19"]
    )
    assert out == {"available": False, "n": 0, "note": "no scored game has a closing line"}


def test_a_model_that_copies_the_market_shows_no_gain_and_no_blend_weight() -> None:
    ds, lines, market, model = _synthetic(edge=0.0)
    out = mc.market_report(
        ds, lines, {"sim": model, "elo": model, "ridge": model, "pool": model},
        np.full(len(ds), 3.0), np.full(len(ds), 221.0), ["2017-18", "2018-19"],
    )  # fmt: skip
    assert out["available"] and out["n"] == len(ds)
    gain = out["gain_over_market"]["sim"]
    assert abs(gain["mean"]) < 1e-9
    assert out["blend_with_market"]["weights"]["2017-18"] is None  # nothing earlier to fit on
    assert out["margin"]["market_mae"] > 0 and out["total"]["n"] == len(ds)


def test_blend_weight_for_a_season_uses_only_earlier_seasons() -> None:
    ds, lines, market, model = _synthetic(edge=0.5, seed=4)
    kwargs = dict(sim_margin=np.zeros(len(ds)), sim_total=np.zeros(len(ds)))
    base = mc.market_report(
        ds, lines, {"sim": model}, kwargs["sim_margin"], kwargs["sim_total"], ["2017-18", "2018-19"]
    )
    # sabotage the outcomes of the later season only: the later season's weight must not move
    flipped = build_dataset([GameRow(**{**g.__dict__, "home_pts": g.away_pts + 1, "away_pts": g.home_pts})
                             if g.season == "2018-19" else g for g in ds.games])  # fmt: skip
    again = mc.market_report(
        flipped,
        lines,
        {"sim": model},
        kwargs["sim_margin"],
        kwargs["sim_total"],
        ["2017-18", "2018-19"],
    )
    assert (
        base["blend_with_market"]["weights"]["2018-19"]
        == again["blend_with_market"]["weights"]["2018-19"]
    )


def test_total_and_margin_errors_compare_model_with_the_market_line() -> None:
    ds, lines, market, model = _synthetic()
    actual_total = ds.hp + ds.ap
    out = mc.market_report(
        ds, lines, {"sim": model}, (ds.hp - ds.ap).astype(float), actual_total.astype(float),
        ["2017-18", "2018-19"],
    )  # fmt: skip
    assert out["total"]["model_mae"] == pytest.approx(0.0)  # a perfect model
    assert out["total"]["market_mae"] > 1.0 and out["total"]["market_bias"] != 0.0
    assert out["margin"]["model_mae"] == pytest.approx(0.0)


# ------------------------------------------------- page validation, neutral sites, coverage


def _many_pages(n: int) -> str:
    rows = [
        _pair(
            f"{10 + (i // 28) % 3:02d}{1 + i % 28:02d}",
            "Atlanta",
            "Boston",
            "90",
            "100",
            "210",
            "211",
            "4",
            "3",
            "120",
            "-140",
        )
        for i in range(n)
    ]
    return _page(*rows)


def test_a_placeholder_page_is_rejected_and_never_cached(tmp_path: Path) -> None:
    th = sbro.Throttle(burst=1, gap=0, pause=0, sleep=lambda s: None)
    for html in ("<html>Just a moment...</html>", _page(), _many_pages(5)):
        with pytest.raises(sbro.SbroError, match="parsed to"):
            sbro.fetch_seasons(["2018-19"], cache=tmp_path, fetch=lambda u, h=html: h, throttle=th)
    assert not list(tmp_path.glob("*.html.gz"))
    assert (
        sbro.fetch_seasons(
            ["2018-19"],
            cache=tmp_path,
            fetch=lambda u: _many_pages(sbro.MIN_PAGE_GAMES),
            throttle=th,
        )
        == 1
    )


def test_neutral_site_games_are_oriented_from_espn_and_verified_by_score() -> None:
    # two N rows, no home team: Atlanta (favourite, holds the spread) vs Dallas, 99-90
    page = _page(
        _row("1016", "1", "N", "Dallas", "90", "211", "212", "150"),
        _row("1016", "2", "N", "Atlanta", "99", "4", "3.5", "-170"),
    )
    parsed, _ = sbro.parse_season(page, "2018-19")
    assert parsed[0]["neutral"] and parsed[0]["favourite"] == "b"
    tip = datetime(2018, 10, 17, 1, 0, tzinfo=UTC)
    atl_home = [_espn_game("n1", tip, "ATL", "DAL", 99, 90)]
    dal_home = [_espn_game("n2", tip, "DAL", "ATL", 90, 99)]
    lines, stats = sbro.match_games(parsed, atl_home)
    assert stats["matched"] == 1 and stats["matched_neutral"] == 1
    a = lines["n1"]
    assert a.neutral and a.spread_home == -3.5 and (a.ml_home, a.ml_away) == (-170, 150)
    b = sbro.match_games(parsed, dal_home)[0]["n2"]  # ESPN lists Dallas as home this time
    assert b.neutral and b.spread_home == 3.5 and (b.ml_home, b.ml_away) == (150, -170)
    assert a.p_home_ml > 0.5 > b.p_home_ml
    wrong = [_espn_game("n3", tip, "ATL", "DAL", 99, 91)]  # one point off: not the same game
    assert sbro.match_games(parsed, wrong)[1]["score_mismatch"] == 1


def test_coverage_problems_name_every_gap() -> None:
    ok = {"cached": True, "matched": 900}
    report = {
        "seasons": {
            "2018-19": ok,
            "2019-20": {"cached": False},
            "2020-21": {"cached": True, "matched": 12},
        }
    }
    problems = sbro.coverage_problems(report, 400)
    assert any("2019-20" in p and "not cached" in p for p in problems)
    assert any("2020-21" in p and "only 12" in p for p in problems)
    assert sbro.coverage_problems({"seasons": {"2018-19": ok}}, 400) == []
    empty = sbro.coverage_problems({"seasons": {"2018-19": {"cached": False}}}, 400)
    assert empty[0].startswith("no closing-line games matched")


def test_load_market_records_a_reproducibility_manifest_without_content(tmp_path: Path) -> None:
    page = _page(
        _pair(
            "1016",
            "Philadelphia",
            "Boston",
            "87",
            "105",
            "208.5",
            "211.5",
            "5",
            "4.5",
            "170",
            "-200",
        ),
        _row("1017", "1", "N", "Utah", "99", "210", "211", "120")
        + _row("1017", "2", "N", "Miami", "101", "3", "2", "-140"),
    )
    with gzip.open(tmp_path / "2018-19.html.gz", "wt", encoding="utf-8") as fh:
        fh.write(page)
    games = [
        _espn_game("g1", datetime(2018, 10, 17, 1, 0, tzinfo=UTC), "BOS", "PHI", 105, 87),
        _espn_game("g2", datetime(2018, 10, 18, 1, 0, tzinfo=UTC), "MIA", "UTA", 101, 99),
    ]
    lines, report = sbro.load_market(["2018-19"], games, tmp_path)
    info = report["seasons"]["2018-19"]
    assert len(info["html_sha256"]) == 64 and info["parsed"] == 2 and info["parsed_neutral"] == 1
    assert info["matched"] == 2 and info["matched_neutral"] == 1
    assert "Boston" not in repr(report) and lines["g2"].neutral and not lines["g1"].neutral


# ----------------------------------------------------- provider-independent source


def test_closing_lines_can_come_from_a_csv(tmp_path: Path) -> None:
    from outlier_nba.market import closing

    path = tmp_path / "close.csv"
    path.write_text(
        "game_id,season,date,home,away,ml_home,ml_away,spread_home,total_close,total_open,"
        "spread_open_home,neutral\n"
        "g1,2018-19,2018-10-16,BOS,PHI,-200,170,-4.5,211.5,208.5,-5,\n"
        "g2,2018-19,2018-10-17,MIA,UTA,-140,120,,,,,true\n"
        "g3,2018-19,2018-10-18,MIA,UTA,0,120,,,,,\n",
        encoding="utf-8",
    )
    lines = closing.lines_from_csv(path)
    assert set(lines) == {"g1", "g2"}  # g3 has an unusable price
    assert lines["g1"].spread_home == -4.5 and 0.6 < lines["g1"].p_home_ml < 0.7
    assert lines["g2"].spread_home is None and lines["g2"].neutral


def test_the_report_runs_on_any_provider_and_splits_neutral_from_ordinary() -> None:
    ds, lines, market, model = _synthetic(edge=0.0)
    flagged = {
        gid: sbro.MarketLine(**{**ln.__dict__, "neutral": i % 10 == 0})
        for i, (gid, ln) in enumerate(lines.items())
    }
    out = mc.market_report(
        ds, flagged, {"sim": model}, np.full(len(ds), 3.0), np.full(len(ds), 221.0),
        ["2017-18", "2018-19"],
    )  # fmt: skip
    venues = out["by_venue"]
    assert set(venues) == {"ordinary", "neutral"}
    assert venues["neutral"]["n"] + venues["ordinary"]["n"] == out["n"]
    assert venues["neutral"]["n"] == pytest.approx(out["n"] / 10, abs=2)


def test_dates_roll_over_once_so_the_bubble_months_land_in_2020() -> None:
    """2019-20 ran from October 2019 to October 2020; a month rule would misdate August-October."""
    page = _page(
        _pair("1022", "Utah", "Miami", "99", "101", "210", "211", "4", "3", "120", "-140"),
        _pair("0115", "Utah", "Miami", "99", "101", "210", "211", "4", "3", "120", "-140"),
        _pair("0307", "Utah", "Miami", "99", "101", "210", "211", "4", "3", "120", "-140"),
        _pair("0731", "Utah", "Miami", "99", "101", "210", "211", "4", "3", "120", "-140"),
        _pair("1008", "Utah", "Miami", "99", "101", "210", "211", "4", "3", "120", "-140"),
    )
    days = [g["day"] for g in sbro.parse_season(page, "2019-20")[0]]
    assert days == [
        date(2019, 10, 22), date(2020, 1, 15), date(2020, 3, 7), date(2020, 7, 31), date(2020, 10, 8)
    ]  # fmt: skip
