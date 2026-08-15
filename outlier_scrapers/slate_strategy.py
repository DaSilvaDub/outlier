import argparse
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from outlier_scrapers.form_source import (
    FinalEvent,
    UnsupportedSport,
    _token,
    canon_team,
    iter_recent_finals,
)
from outlier_scrapers.paths import league_paths

logger = logging.getLogger(__name__)


@dataclass
class MatchupContext:
    event_id: str
    away: str
    home: str
    recent_games: list[FinalEvent]
    away_players: set[str] = field(default_factory=set)
    home_players: set[str] = field(default_factory=set)


@dataclass
class Direction:
    scope: str
    player: str
    player_key: str
    team: str
    market_family: str
    side: str
    confidence: str
    reasons: list[str] | None = None
    injury_linked: bool = False
    event_id: str = ""


@dataclass
class EventStrategyOut:
    event_id: str
    matchup: str
    away: str
    home: str
    team_form: dict[str, Any]
    player_form: list[dict[str, Any]]
    injuries: list[dict[str, Any]]
    trends: list[dict[str, Any]]
    directions: list[Direction]
    avoids: list[dict[str, Any]]


def _injury_name(item: dict[str, Any]) -> str:
    name = " ".join(
        part for part in (item.get("firstName"), item.get("lastName")) if part
    ).strip()
    return name or str(item.get("player") or item.get("description") or "").strip()


def _injury_status(item: dict[str, Any]) -> str:
    raw = item.get("injury")
    nested = raw if isinstance(raw, dict) else {}
    status = nested.get("status")
    if status in (None, ""):
        status = item.get("status")
    return str(status or "").strip().upper()


def _is_out_status(status: str) -> bool:
    token = status.strip().upper()
    return token == "OUT" or token.startswith("OUT ") or token.startswith("OUTFOR")


def _sides_from_matchup(matchup: str) -> tuple[str, str]:
    text = str(matchup or "").strip()
    if " @ " not in text:
        return "", ""
    away, home = text.split(" @ ", 1)
    return away.strip(), home.strip()


def _slate_events_from_games(payload: dict[str, Any]) -> list[dict[str, Any]]:
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    events_ctx = context.get("events") if isinstance(context.get("events"), dict) else {}
    teams_ctx = context.get("teams") if isinstance(context.get("teams"), dict) else {}
    records = payload.get("records") if isinstance(payload.get("records"), list) else []

    matchup_by_event: dict[str, tuple[str, str]] = {}
    for row in records:
        if not isinstance(row, dict):
            continue
        event_id = str(row.get("event_id") or "").strip()
        if not event_id or event_id in matchup_by_event:
            continue
        away, home = _sides_from_matchup(str(row.get("matchup") or ""))
        if away and home:
            matchup_by_event[event_id] = (away, home)

    event_ids = [str(eid) for eid in events_ctx] if events_ctx else list(matchup_by_event)
    slate: list[dict[str, Any]] = []
    for event_id in event_ids:
        info = events_ctx.get(event_id) if isinstance(events_ctx.get(event_id), dict) else {}
        away, home = matchup_by_event.get(event_id, ("", ""))
        injuries: list[dict[str, Any]] = []
        for team_id in (info.get("away_team_id"), info.get("home_team_id")):
            team_info = teams_ctx.get(str(team_id)) if team_id else None
            if not isinstance(team_info, dict):
                continue
            for item in team_info.get("injuries") or []:
                if isinstance(item, dict):
                    injuries.append(item)
        if away and home:
            slate.append(
                {"event_id": event_id, "away": away, "home": home, "injuries": injuries}
            )
    return slate


def _players_for_side(recent_games: list[FinalEvent], team: str) -> set[str]:
    keys: set[str] = set()
    for game in recent_games:
        if not _team_is(game, team):
            continue
        sport = game.sport or "WNBA"
        wanted = canon_team(sport, team)
        for player_key, player_team in game.player_teams.items():
            if wanted and canon_team(sport, player_team) == wanted:
                keys.add(player_key)
    return keys


def _team_is(game: FinalEvent, team: str) -> str:
    """Return 'away', 'home', or '' after alias normalization."""
    sport = game.sport or "WNBA"
    wanted = canon_team(sport, team)
    if wanted and wanted == canon_team(sport, game.away):
        return "away"
    if wanted and wanted == canon_team(sport, game.home):
        return "home"
    if team in (game.away, game.home):
        return "away" if game.away == team else "home"
    return ""


def window_team_form(recent_games: list[FinalEvent], team: str, limit: int = 5) -> dict[str, Any]:
    team_games = []
    for g in reversed(recent_games):
        if _team_is(g, team):
            team_games.append(g)
            if len(team_games) == limit:
                break
    
    if not team_games:
        return {"gp": 0}
        
    pts = []
    opp_pts = []
    wins = 0
    
    for g in team_games:
        side = _team_is(g, team)
        if side == "away":
            pts.append(g.away_score)
            opp_pts.append(g.home_score)
            if g.away_score > g.home_score:
                wins += 1
        else:
            pts.append(g.home_score)
            opp_pts.append(g.away_score)
            if g.home_score > g.away_score:
                wins += 1
                
    mean_pts = sum(pts) / len(pts)
    mean_opp = sum(opp_pts) / len(opp_pts)
    
    return {
        "gp": len(team_games),
        "wins": wins,
        "ppg": mean_pts,
        "opp_ppg": mean_opp
    }


def window_player_form(recent_games: list[FinalEvent], player_key: str, limit: int = 5) -> dict[str, Any]:
    player_games = []
    for g in reversed(recent_games):
        if player_key in g.players:
            player_games.append(g.players[player_key])
            if len(player_games) == limit:
                break
                
    if not player_games:
        return {"gp": 0}
        
    pts = [p.get("PTS", 0) for p in player_games]
    ast = [p.get("AST", 0) for p in player_games]
    
    mean_pts = sum(pts) / len(pts)
    var_pts = sum((x - mean_pts)**2 for x in pts) / len(pts) if len(pts) > 1 else 0
    std_pts = math.sqrt(var_pts)
    
    mean_ast = sum(ast) / len(ast)
    var_ast = sum((x - mean_ast)**2 for x in ast) / len(ast) if len(ast) > 1 else 0
    std_ast = math.sqrt(var_ast)
    
    return {
        "gp": len(player_games),
        "mean_pts": mean_pts,
        "std_pts": std_pts,
        "mean_ast": mean_ast,
        "std_ast": std_ast,
        "raw_pts": pts,
    }


def build_event_strategy(context: MatchupContext, injuries: list[dict[str, Any]] | None = None) -> EventStrategyOut:
    if injuries is None:
        injuries = []
        
    away_form = window_team_form(context.recent_games, context.away, 5)
    home_form = window_team_form(context.recent_games, context.home, 5)
    
    out = EventStrategyOut(
        event_id=context.event_id,
        matchup=f"{context.away} @ {context.home}",
        away=context.away,
        home=context.home,
        team_form={"away": away_form, "home": home_form},
        player_form=[],
        injuries=[
            {
                "player": _injury_name(i),
                "team": str(i.get("team") or i.get("teamId") or ""),
                "status": _injury_status(i),
            }
            for i in injuries
        ],
        trends=[],
        directions=[],
        avoids=[]
    )
    
    # 1. defense_collapse & defense_clamp
    for team, form, opp_team in [(context.away, away_form, context.home), (context.home, home_form, context.away)]:
        if form.get("gp", 0) > 0:
            if form.get("opp_ppg", 0) >= 95:
                out.trends.append({"id": "defense_collapse", "severity": "HIGH", "team": team, "text": f"{team} allowed {form['opp_ppg']:.1f} L5"})
                out.directions.append(
                    Direction(
                        "TEAM",
                        "",
                        "",
                        opp_team,
                        "PTS",
                        "OVER",
                        "HIGH",
                        reasons=[f"{team} defense collapse"],
                        event_id=context.event_id,
                    )
                )
            
            if form.get("opp_ppg", 100) <= 82:
                out.trends.append({"id": "defense_clamp", "severity": "MED", "team": team, "text": f"{team} clamp {form['opp_ppg']:.1f} L5"})
                # Semantic Fix 4: Suppress PTS OVER for the team PLAYING AGAINST the clamping defense.
                out.directions.append(
                    Direction(
                        "TEAM",
                        "",
                        "",
                        opp_team,
                        "PTS",
                        "UNDER",
                        "MED",
                        reasons=[f"Playing against {team} clamp defense"],
                        event_id=context.event_id,
                    )
                )
    
    # Analyze only players who are relevant to this matchup (Fix Leakage 2)
    all_matchup_players = context.away_players | context.home_players
        
    for p_key in all_matchup_players:
        p_form = window_player_form(context.recent_games, p_key, 5)
        if p_form["gp"] > 0:
            out.player_form.append({"player_key": p_key, "form": p_form})
            # ceiling_game
            ceiling_flag = False
            pts_list = p_form["raw_pts"]
            if len(pts_list) > 2:
                for i, pts in enumerate(pts_list):
                    # Fix Crash 1: Filter by index, not value
                    other_pts = pts_list[:i] + pts_list[i+1:]
                    if not other_pts:
                        continue
                    mean_other = sum(other_pts) / len(other_pts)
                    var_other = sum((x - mean_other)**2 for x in other_pts) / len(other_pts)
                    std_other = math.sqrt(var_other)
                    if pts > mean_other + 2 * std_other and pts > mean_other + 10:
                        ceiling_flag = True
                        break
            
            if ceiling_flag:
                out.trends.append({"id": "ceiling_game", "severity": "MED", "team": "", "text": f"{p_key} had a ceiling game"})
                out.directions.append(
                    Direction(
                        "PLAYER",
                        p_key,
                        p_key,
                        "",
                        "PTS",
                        "OVER",
                        "HIGH",
                        reasons=["Ceiling game observed"],
                        event_id=context.event_id,
                    )
                )

            # stable_playmaker
            if p_form["mean_ast"] >= 5.0 and p_form["std_ast"] <= 1.2 and p_form["gp"] >= 4:
                out.directions.append(
                    Direction(
                        "PLAYER",
                        p_key,
                        p_key,
                        "",
                        "AST",
                        "OVER",
                        "HIGH",
                        reasons=["Stable playmaker"],
                        event_id=context.event_id,
                    )
                )

    out_keys = {
        _token(row["player"])
        for row in out.injuries
        if _is_out_status(str(row.get("status") or ""))
    }
    if out_keys:
        out.directions = [
            d
            for d in out.directions
            if not (d.side == "OVER" and d.player_key and d.player_key in out_keys)
        ]
    return out


def export_slate_strategy_for_league(league: str) -> dict[str, Any]:
    token = league.strip().upper()
    if token == "MLB":
        return {"status": "skipped", "reason": "mlb_not_in_v1", "events": []}

    paths = league_paths(league)
    games_file = paths.games_normalized_latest()
    if not games_file.exists():
        return {"status": "empty", "events": []}
    
    try:
        games_payload = json.loads(games_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.error("Failed to parse %s", games_file)
        return {"status": "error", "reason": "invalid_json", "events": []}

    has_contract = isinstance(games_payload.get("records"), list) or (
        isinstance(games_payload.get("context"), dict)
        and isinstance((games_payload.get("context") or {}).get("events"), dict)
    )
    if not has_contract:
        return {"status": "error", "reason": "unrecognized_games_contract", "events": []}

    matchups = _slate_events_from_games(games_payload)
    if not matchups:
        return {"status": "empty", "events": []}
    
    teams = {side for row in matchups for side in (row["away"], row["home"])}
    try:
        recent_games = iter_recent_finals(token, teams, 10)
    except UnsupportedSport as exc:
        return {"status": "skipped", "reason": str(exc), "events": []}
    except Exception as exc:
        logger.error("Failed to fetch recent finals: %s", exc)
        raise
    
    out_events: list[dict[str, Any]] = []
    status = "ok"
    for m in matchups:
        away = m["away"]
        home = m["home"]
        event_id = m["event_id"]
        ctx = MatchupContext(
            event_id=event_id,
            away=away,
            home=home,
            recent_games=recent_games,
            away_players=_players_for_side(recent_games, away),
            home_players=_players_for_side(recent_games, home),
        )
        strategy = build_event_strategy(ctx, injuries=m["injuries"])
        
        out_events.append({
            "event_id": strategy.event_id,
            "matchup": strategy.matchup,
            "away": strategy.away,
            "home": strategy.home,
            "team_form": strategy.team_form,
            "player_form": strategy.player_form,
            "injuries": strategy.injuries,
            "trends": strategy.trends,
            "directions": [d.__dict__ for d in strategy.directions],
            "avoids": strategy.avoids
        })
    
    doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "league": league,
        "source": "espn_boxscores+outlier_injuries",
        "status": status,
        "events": out_events
    }
    
    json_out = paths.ensure().reports / "slate_strategy_latest.json"
    
    # Safe JSON dump writing (Rule 4)
    with open(json_out, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=2)
    
    # Markdown Rendering (Fix 8)
    md_lines = [f"# Slate strategy — {league} {doc['date']}", ""]
    for event_row in out_events:
        md_lines.append(f"## {event_row['matchup']}")
        md_lines.append("### Last 5 / Last 10")
        
        # Ensure the table header is properly rendered
        md_lines.append("| Team | W | L5 PPG | L5 Opp | L5 REB | L10 PPG | L10 Opp |")
        md_lines.append("|---|---|---|---|---|---|---|")
        
        # Add Rows for Away & Home
        away_form = event_row["team_form"].get("away", {})
        home_form = event_row["team_form"].get("home", {})
        
        if away_form:
            md_lines.append(f"| {event_row['away']} | {away_form.get('wins', 0)} | {away_form.get('ppg', 0):.1f} | {away_form.get('opp_ppg', 0):.1f} | - | - | - |")
        if home_form:
            md_lines.append(f"| {event_row['home']} | {home_form.get('wins', 0)} | {home_form.get('ppg', 0):.1f} | {home_form.get('opp_ppg', 0):.1f} | - | - | - |")
        
        md_lines.append("")
        md_lines.append("### Trends")
        if not event_row["trends"]:
            md_lines.append("- No standout trends")
        for t in event_row["trends"]:
            md_lines.append(f"- {t['severity']} — {t['id']} ({t['text']})")
            
        md_lines.append("")
        md_lines.append("### Directions")
        if not event_row["directions"]:
            md_lines.append("- None")
        for d in event_row["directions"]:
            reasons = d.get('reasons') or []
            md_lines.append(f"- {d['confidence']} {d['side']} {d.get('player') or d.get('team')} {d['market_family']} — {', '.join(reasons)}")
            
        md_lines.append("")
        md_lines.append("### Avoids")
        if not event_row["avoids"]:
            md_lines.append("- None")
        for a in event_row["avoids"]:
            md_lines.append(f"- {a['team']} {a['market_family']} — {a['reason']}")
            
        md_lines.append("")
        
    md_out = paths.reports / "slate_strategy_latest.md"
    md_out.write_text("\n".join(md_lines), encoding="utf-8")
    
    return doc

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", required=True)
    args = parser.parse_args()
    export_slate_strategy_for_league(args.league)
