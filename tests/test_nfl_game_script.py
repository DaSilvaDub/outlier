"""Regression: quoted spreads keep their side, and a missing spread is not a pick'em."""

from __future__ import annotations

from outlier_nfl.matchup import build_matchup_script, render_matchup_markdown
from outlier_nfl.models import BookPrice, NflGameLine
from scripts.nfl_game_script import NflGameScriptGenerator


def _row(
    market: str,
    line: float,
    team: str | None,
    position: str,
    *,
    odds: int = -110,
    market_type: str = "GAMELINE",
    proposition: str | None = None,
) -> dict[str, object]:
    return {
        "market": market,
        "market_type": market_type,
        "proposition": proposition or market,
        "scope": "full_game",
        "team": team,
        "position": position,
        "line": line,
        "best_odds": odds,
        "implied_probability": 52.38,
        "books": [{"book": "DK", "odds": odds}],
    }


def test_chiefs_plus_3_5_is_read_from_the_kc_side_and_unquoted_is_none() -> None:
    """A Chiefs +3.5 quote is 3.5 from the KC side; no quote is None, not 5.5/54.5."""
    gen = NflGameScriptGenerator()
    home_dog = gen.extract_game_environment(
        [
            _row("SPREAD", 3.5, "KC", "HOME", odds=-105),
            _row("SPREAD", -3.5, "LAC", "AWAY", odds=-115),
        ],
        home_team="KC",
        away_team="LAC",
    )
    assert home_dog["primary_spread_line"] == 3.5
    assert home_dog["spread_side"] == "KC"
    assert home_dog["home_spread_odds"] == -105

    away_dog = gen.extract_game_environment(
        [
            _row("SPREAD", -3.5, "LAC", "HOME", odds=-115),
            _row("SPREAD", 3.5, "KC", "AWAY", odds=-105),
        ],
        home_team="LAC",
        away_team="KC",
    )
    assert away_dog["spread_side"] == "KC"
    assert away_dog["primary_spread_line"] == -3.5

    home_favorite = gen.extract_game_environment(
        [
            _row("SPREAD", -3.5, "KC", "HOME", odds=-108),
            _row("SPREAD", 3.5, "LAC", "AWAY", odds=-112),
        ],
        home_team="KC",
        away_team="LAC",
    )
    assert home_favorite["primary_spread_line"] == -3.5
    assert home_favorite["spread_side"] == "LAC"
    assert home_favorite["home_spread_odds"] == -108

    bare = gen.extract_game_environment(
        [_row("ML", 0.0, "KC", "HOME", odds=-150)],
        home_team="KC",
        away_team="LAC",
    )
    assert bare["primary_spread_line"] is None
    assert bare["primary_total_line"] is None

    quoted_total = gen.extract_game_environment(
        [
            _row("TOTAL", 47.5, None, "OVER", proposition="TOTAL"),
            _row("TOTAL", 47.5, None, "UNDER", proposition="TOTAL"),
        ],
        home_team="KC",
        away_team="LAC",
    )
    assert quoted_total["primary_spread_line"] is None
    assert quoted_total["primary_total_line"] == 47.5


def test_pickem_zero_stays_zero() -> None:
    gen = NflGameScriptGenerator()
    env = gen.extract_game_environment(
        [
            _row("SPREAD", 0.0, "KC", "HOME"),
            _row("SPREAD", 0.0, "LAC", "AWAY"),
        ],
        home_team="KC",
        away_team="LAC",
    )
    assert env["primary_spread_line"] == 0.0
    assert env["primary_spread_line"] != 5.5


def _spread(home: str, away: str, home_line: float, away_line: float) -> list[NflGameLine]:
    def one(team: str, line: float, position: str) -> NflGameLine:
        return NflGameLine(
            event_id="e1",
            event_starts_at=None,
            matchup=f"{away} @ {home}",
            home_team=home,
            away_team=away,
            market_type="GAMELINE",
            market="SPREAD",
            proposition="SPREAD",
            position=position,
            line=line,
            signed_line=str(line),
            selection=f"{team} {line}",
            team=team,
            books=(BookPrice(book="DK", odds=-110, odds_raw="-110", decimal=1.91),),
            best_odds=-110,
            implied_probability=52.38,
        )

    return [one(home, home_line, "HOME"), one(away, away_line, "AWAY")]


def test_unquoted_spread_is_placeholder_and_has_no_lean() -> None:
    total = NflGameLine(
        event_id="e1",
        event_starts_at=None,
        matchup="NE @ KC",
        home_team="KC",
        away_team="NE",
        market_type="GAMELINE",
        market="TOTAL",
        proposition="TOTAL",
        position="OVER",
        line=47.5,
        signed_line=None,
        selection="Over 47.5",
        team=None,
        books=(BookPrice(book="DK", odds=-110, odds_raw="-110", decimal=1.91),),
        best_odds=-110,
        implied_probability=52.38,
    )
    script = build_matchup_script("e1", "KC", "NE", [total], {})
    md = render_matchup_markdown(script, date="2026-11-20")

    assert script.total == 47.5
    assert script.total_source == "market"
    assert script.spread_source == "default"
    assert script.spread_lean == ""
    assert "Spread lean" not in md
    assert "+0.0" not in md
    assert "no quoted spread" in md


def test_quoted_pickem_keeps_neutral_lean() -> None:
    script = build_matchup_script("e1", "KC", "NE", _spread("KC", "NE", 0.0, 0.0), {})
    md = render_matchup_markdown(script, date="2026-11-20")

    assert script.home_spread == 0.0
    assert script.spread_source == "market"
    assert script.spread_lean == "NEUTRAL"
    assert "- **Spread lean:** NEUTRAL (KC +0.0)" in md
