"""Market environment for NFL matchup scripts.

The pipeline renders each game with ``outlier_nfl.matchup.render_matchup_markdown``.
This module only loads a normalized slate and reads the quoted spread and total.
A missing quote is None — never a filled-in 5.5 or 54.5.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# Ensure repository root is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Ensure Windows stdout handles UTF-8 gracefully
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logger = logging.getLogger("nfl_game_script")


def american_to_prob(odds: int | float) -> float:
    """Calculate implied win probability percentage from American odds."""
    if odds > 0:
        return 100.0 / (odds + 100.0) * 100.0
    elif odds < 0:
        return abs(odds) / (abs(odds) + 100.0) * 100.0
    return 50.0


def devig_two_way(odds_a: int | float, odds_b: int | float) -> tuple[float, float]:
    """Devig a two-way market using proportional distribution."""
    p_a = american_to_prob(odds_a)
    p_b = american_to_prob(odds_b)
    total_p = p_a + p_b
    if total_p == 0:
        return 50.0, 50.0
    return (p_a / total_p) * 100.0, (p_b / total_p) * 100.0


def _get(item: Any, key: str, default: Any = None) -> Any:
    """Helper to access attribute or dictionary key interchangeably."""
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _as_quote(item: Any) -> SimpleNamespace:
    return SimpleNamespace(
        line=_get(item, "line"),
        position=_get(item, "position"),
        team=_get(item, "team"),
        best_odds=_get(item, "best_odds"),
        implied_probability=_get(item, "implied_probability"),
    )


def _best_row(
    rows: list[Any],
    *,
    team: str | None = None,
    line: float | None = None,
    position: str | None = None,
) -> Any | None:
    matched: list[Any] = []
    for row in rows:
        if team is not None and _get(row, "team") != team:
            continue
        if position is not None and _get(row, "position") != position:
            continue
        if line is not None:
            raw = _get(row, "line")
            if raw is None or abs(float(raw) - float(line)) > 1e-9:
                continue
        matched.append(row)
    if not matched:
        return None
    return max(matched, key=lambda row: len(_get(row, "books") or []))


class NflGameScriptGenerator:
    """Analyzes normalized NFL pipeline data to generate game scripts."""

    def __init__(self, data_dir: Path | str = "data/NFL/normalized") -> None:
        self.data_dir = Path(data_dir).resolve()

    def load_data(self, date: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        games_file = self.data_dir / f"nfl_games_{date}.json"
        props_file = self.data_dir / f"nfl_calibrated_props_{date}.json"
        if not props_file.exists():
            props_file = self.data_dir / f"nfl_props_{date}.json"

        if not games_file.exists():
            games_file = self.data_dir / "nfl_games_latest.json"
        if not props_file.exists():
            props_file = self.data_dir / "nfl_calibrated_props_latest.json"
        if not props_file.exists():
            props_file = self.data_dir / "nfl_props_latest.json"

        if not games_file.exists() or not props_file.exists():
            raise FileNotFoundError(
                f"Missing normalized data files for date {date} in {self.data_dir}"
            )

        with open(games_file, "r", encoding="utf-8") as f:
            games_data = json.load(f).get("records", [])
        with open(props_file, "r", encoding="utf-8") as f:
            props_data = json.load(f).get("records", [])

        # Auto-calibrate on the fly if loaded props do not carry calibration metadata
        if props_data and ("confidence_tier" not in props_data[0] or not props_data[0].get("calibration_tags")):
            try:
                from outlier_nfl.calibration import apply_game_script_calibration
                from outlier_nfl.consensus import select_consensus_player_props
                from outlier_nfl.models import BookPrice, NflGameLine, NflPlayerProp

                def _dict_to_game_line(d: dict[str, Any]) -> NflGameLine:
                    books = [BookPrice(**b) if isinstance(b, dict) else b for b in d.get("books", [])]
                    d_copy = {k: v for k, v in d.items() if k in NflGameLine.__dataclass_fields__}
                    d_copy["books"] = books
                    return NflGameLine(**d_copy)

                def _dict_to_prop(d: dict[str, Any]) -> NflPlayerProp:
                    books = [BookPrice(**b) if isinstance(b, dict) else b for b in d.get("books", [])]
                    d_copy = {k: v for k, v in d.items() if k in NflPlayerProp.__dataclass_fields__}
                    d_copy["books"] = books
                    return NflPlayerProp(**d_copy)

                g_objs = [_dict_to_game_line(g) for g in games_data]
                p_objs = [_dict_to_prop(p) for p in props_data]
                consensus = select_consensus_player_props(p_objs)
                calibrated = apply_game_script_calibration(g_objs, consensus)
                props_data = [p.to_dict() for p in calibrated]
            except Exception as exc:
                logger.warning("Could not auto-calibrate props: %s", exc)

        return games_data, props_data

    def extract_game_environment(
        self, games: list[Any], home_team: str | None = None, away_team: str | None = None
    ) -> dict[str, Any]:
        """Extract consensus and best market lines for the game."""
        if not games:
            return {}

        first_game = games[0]
        resolved_home = home_team or _get(first_game, "home_team") or "BUF"
        resolved_away = away_team or _get(first_game, "away_team") or "DET"

        # Moneylines
        mls = [
            g for g in games
            if _get(g, "market") in ("ML", "MONEYLINE") and _get(g, "scope") == "full_game"
        ]
        home_ml = next((m for m in mls if _get(m, "team") == resolved_home or _get(m, "position") == "HOME"), None)
        away_ml = next((m for m in mls if _get(m, "team") == resolved_away or _get(m, "position") == "AWAY"), None)

        home_ml_best = _get(home_ml, "best_odds") if home_ml else None
        away_ml_best = _get(away_ml, "best_odds") if away_ml else None

        devig_home, devig_away = (
            devig_two_way(home_ml_best, away_ml_best)
            if home_ml_best and away_ml_best
            else (None, None)
        )

        from outlier_nfl.calibration import primary_spread, primary_total

        # Primary lines are the two-way quote closest to a coin flip. No quote
        # stays None — a home dog and a pick'em are real lines, not 5.5 / 54.5.
        spread_rows = [
            g for g in games
            if _get(g, "market") == "SPREAD" and _get(g, "scope") == "full_game"
        ]
        away_signed = primary_spread(
            [_as_quote(g) for g in spread_rows], resolved_away, resolved_home
        )
        if away_signed is None:
            primary_spread_line = None
            spread_side = None
            home_spread_row = None
            away_spread_row = None
        else:
            home_signed = -float(away_signed)
            if home_signed == 0:
                home_signed = 0.0
            primary_spread_line = home_signed
            if home_signed > 0:
                spread_side = resolved_home
            elif home_signed < 0:
                spread_side = resolved_away
            else:
                spread_side = None
            home_spread_row = _best_row(spread_rows, team=resolved_home, line=home_signed)
            away_spread_row = _best_row(
                spread_rows, team=resolved_away, line=float(away_signed)
            )

        total_rows = [
            g for g in games
            if _get(g, "market") == "TOTAL"
            and _get(g, "market_type") == "GAMELINE"
            and _get(g, "scope") == "full_game"
        ]
        primary_total_line = primary_total([_as_quote(g) for g in total_rows])
        if primary_total_line is None:
            over_row = None
            under_row = None
        else:
            over_row = _best_row(total_rows, line=primary_total_line, position="OVER")
            under_row = _best_row(total_rows, line=primary_total_line, position="UNDER")

        # Team Totals
        tt_rows = [
            g for g in games
            if _get(g, "market_type") == "TEAM_PROP"
            and _get(g, "scope") == "full_game"
            and _get(g, "proposition") in ("POINTS", "TOTAL_POINTS", "TEAM_TOTAL", "TOTAL")
        ]
        home_tt_rows = [r for r in tt_rows if _get(r, "team") == resolved_home]
        away_tt_rows = [r for r in tt_rows if _get(r, "team") == resolved_away]

        home_tt_line = None
        home_tt_over_odds = None
        if home_tt_rows:
            best_home_tt = max(home_tt_rows, key=lambda x: len(_get(x, "books") or []))
            home_tt_line = _get(best_home_tt, "line")
            home_tt_over_odds = _get(best_home_tt, "best_odds")

        away_tt_line = None
        away_tt_over_odds = None
        if away_tt_rows:
            best_away_tt = max(away_tt_rows, key=lambda x: len(_get(x, "books") or []))
            away_tt_line = _get(best_away_tt, "line")
            away_tt_over_odds = _get(best_away_tt, "best_odds")

        return {
            "matchup": f"{resolved_away} @ {resolved_home}",
            "home_team": resolved_home,
            "away_team": resolved_away,
            "home_ml_best": home_ml_best,
            "away_ml_best": away_ml_best,
            "devig_home_win_pct": round(devig_home, 1) if devig_home else None,
            "devig_away_win_pct": round(devig_away, 1) if devig_away else None,
            "primary_spread_line": primary_spread_line,
            "spread_side": spread_side,
            "home_spread_odds": _get(home_spread_row, "best_odds") if home_spread_row else None,
            "home_spread_books": len(_get(home_spread_row, "books") or []) if home_spread_row else 0,
            "away_spread_odds": _get(away_spread_row, "best_odds") if away_spread_row else None,
            "away_spread_books": len(_get(away_spread_row, "books") or []) if away_spread_row else 0,
            "primary_total_line": primary_total_line,
            "total_over_odds": _get(over_row, "best_odds") if over_row else None,
            "total_over_books": len(_get(over_row, "books") or []) if over_row else 0,
            "total_under_odds": _get(under_row, "best_odds") if under_row else None,
            "total_under_books": len(_get(under_row, "books") or []) if under_row else 0,
            "home_team_total": home_tt_line or 30.5,
            "home_team_total_odds": home_tt_over_odds,
            "away_team_total": away_tt_line or 24.5,
            "away_team_total_odds": away_tt_over_odds,
        }
