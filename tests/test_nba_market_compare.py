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
    assert len(games) == 1
    assert skipped["total_not_identifiable"] == 1 and skipped["unknown_team"] == 1
    assert skipped["missing_moneyline_or_score"] == 1
    assert skipped["not_a_visitor_home_pair"] >= 1


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
    assert sbro.fetch_seasons(["2018-19", "2019-20"], cache=tmp_path, fetch=fetch, throttle=th) == 2
    assert sbro.fetch_seasons(["2018-19"], cache=tmp_path, fetch=fetch, throttle=th) == 0
    assert len(calls) == 2 and all(u.startswith(sbro.BASE) for u in calls)
    assert sbro.read_cached("2018-19", tmp_path) == "<html></html>"
    assert sbro.read_cached("2020-21", tmp_path) is None
    with pytest.raises(sbro.EspnError, match="refusing"):
        sbro.http_fetch_text("https://example.com/x")


def test_corrupt_cached_page_is_a_miss(tmp_path: Path) -> None:
    sbro.fetch_seasons(["2018-19"], cache=tmp_path, fetch=lambda u: "x" * 1000,
                       throttle=sbro.Throttle(burst=1, gap=0, pause=0, sleep=lambda s: None))  # fmt: skip
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
    assert report["2018-19"]["matched"] == 1 and report["2019-20"] == {"cached": False}


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
