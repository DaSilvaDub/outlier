"""Markdown rendering for the pack writer: dossiers and the briefing.

Rendering should not control candidate semantics — every function here reads
already-decided row fields (board, actionable, flags, ...) and formats them.
"""

from __future__ import annotations

import re
from typing import Any

from outlier_scrapers import slate_quality
from outlier_scrapers.pack_market import LONGSHOT_AMERICAN_PRICE

MLB_QUESTIONS = [
    "- **Starters:** both confirmed SPs, days rest, recent form, pitch-count limit / opener.",
    "- **Bullpen:** who threw the last 1-2 days, closer availability, gassed pen.",
    "- **Lineup:** posted lineup card, key bats in/out, platoon edge, regulars resting.",
    "- **Weather/park:** wind speed + direction, temp, rain risk, roof, park, altitude.",
    "- **Umpire:** home-plate ump strike-zone tendency.",
    "- *Markets:* full game, **F5**, run line, total, **NRFI/YRFI**, strikeout props, H+R+RBI.",
]
WNBA_QUESTIONS = [
    "- **Availability:** injury report status (out/quest/prob), load management, rest.",
    "- **Lineup/rotation:** confirmed starters, rotation changes, minutes restrictions.",
    "- **Schedule/fatigue:** back-to-back, travel/time-zone, schedule density.",
    "- **Usage shift:** if a star sits, who absorbs usage -> which prop **overs** light up.",
    "- **Game script:** pace matchup, blowout risk, foul-trouble tendencies.",
    "- *Markets:* spread, total, points/reb/ast, **PRA**, 3PM, alt lines.",
]


def _matchup_display(row: dict[str, Any]) -> str:
    """A human matchup line for a row, preferring full names over codes."""
    away_name = home_name = None
    matchup = str(row.get("matchup") or "").strip()
    ha = row.get("home_away")
    if ha == "HOME":
        home_name, away_name = row.get("team_name"), row.get("opp_name")
    elif ha == "AWAY":
        away_name, home_name = row.get("team_name"), row.get("opp_name")
    if away_name and home_name:
        return f"{away_name} @ {home_name} ({matchup})" if matchup else f"{away_name} @ {home_name}"
    return matchup or "unknown matchup"


_TOTALS_NAME_RE = re.compile(r"^(.*?)\s+(?:Total O/U|Team Total)\s+")


def _totals_event_display(row: dict[str, Any]) -> str:
    """Best-effort matchup/team label for a totals row (no matchup field on
    GAME_TOTALS_HEADER — the name is embedded in `selection`)."""
    match = _TOTALS_NAME_RE.match(str(row.get("selection") or ""))
    if match:
        return match.group(1)
    return str(row.get("team") or "unknown matchup")


def build_dossier(rows: list[dict[str, Any]], sport: str) -> str:
    matchup = _matchup_display(rows[0]) if rows else "unknown matchup"
    lines = [f"## {sport} game dossier — {matchup}", ""]
    lines += MLB_QUESTIONS if sport.upper() == "MLB" else WNBA_QUESTIONS
    lines += ["", "### Markets in play"]
    for r in rows:
        team = r.get("team_name") or r.get("team") or ""
        ctx = f" [{team}]" if team else ""
        lines.append(
            f"- {r.get('market_type')}: {r.get('selection')}{ctx} @ {r.get('line')} ({r.get('price')})"
        )
    lines += slate_quality.dossier_injury_section(rows)
    return "\n".join(lines)


ROLE_BLOCK = [
    "LEDGER CONTEXT (all passes):",
    "- Each candidate carries authoritative context: team / team_name, opponent / opp_name,"
    " home_away, matchup, and market_label. Use these verbatim — do NOT infer a player's team,"
    " the opponent, home/away, or what a market means from the event_id hash or a terse code"
    " (e.g. LAS is Los Angeles Sparks not Las Vegas; PT is Pitches Thrown).",
    "- Render ONLY fields present in the candidate rows. NEVER introduce a player, injury status, or pitcher not in the pack.",
    "- If a row has priced_line set (or a data_quality_flags entry like"
    " ev_line_fallback:priced_at=…), the EV/price were derived at priced_line, not the shown"
    " line — reconcile to priced_line before quoting an edge and note the mismatch.",
    "- data_quality_flags may also carry cross_sport_market:<LEAGUE>, implausible_line,"
    " non_numeric_line, spread_sign_conflict, movement_line_mismatch,"
    " ev_probability_mismatch,"
    " edge_suspect_stale_line, edge_suspect_thin_liquidity, SOURCE_INTEGRITY_FLAG,"
    " SIDE_RESOLUTION_CONFLICT, or UNINDEXED_SLATE_GAME — treat any such row as a"
    " data artifact with actionable=false and verdict PASS / STAND-DOWN.",
    "- model_prob_source distinguishes Outlier EV devig from proxy_market_devig. The proxy"
    " source fills probability/edge/Kelly for auditability but is market-implied context,"
    " NOT an independent predictive model confirmation. Reasoning models MUST NOT double-count"
    " proxy market devigs as independent corroboration of an EV play.",
    "- Spread / run line / puck line rows already carry an explicit sign (e.g. '+1.5' or"
    " '-1.5' in the line and selection) — never re-derive or flip it from model_prob or"
    " the favorite/underdog assumption. model_prob on these rows is the probability that"
    " the STATED signed side covers, not the probability of winning the game; a heavily"
    " favored team can correctly show a positive (cushion) line if that is the side priced.",
    "- Variance taxonomy to anchor evaluation:",
    "   * High variance: 3PM, hits allowed, turnovers.",
    "   * Moderate variance: strikeouts, assists, points.",
    "- Rank remaining player props by edge_pct / EV, never by raw model_prob."
    " Negative-edge high-prob 3s are juice, not plays.",
    "- usage_up_under: an UNDER player prop on a card whose own team has a"
    " confirmed Out/OFS is not Board A. Season-mean unders with usage up are a fade.",
    "- line_moved_with_side / +CLV: if the live number moved further toward the"
    " pack side (e.g. CHI -1.5 → -2.5), that is not a stale-line kill. Play the"
    " pack number if it is still bookable; size down only if forced onto the new number.",
    "- opponent_star_out on a gameline is the primary cover signal. own_star_out"
    " is secondary and must not veto a still-plus EV side.",
    "- CORRELATION: rows sharing the same event_id (same matchup) are same-game"
    " legs. Do NOT size stacked same-event bets as independent — their outcomes"
    " are correlated (e.g. two props in one game, or a team side plus that game's"
    " total). Discount total stake across correlated legs rather than summing"
    " each leg's recommended_units_pre_news at face value.",
    "",
    "HOUSE RULES (all passes):",
    "- MLB player props (strict whitelist): pitcher strikeouts (SO) only."
    " Team props: R and TOTAL (team run totals) only."
    " Game lines (ML/spread/total) are preserved in the feed."
    " MLB alt player parlays are SO OVER across different games."
    " MLB alt totals parlays are OVER game/team run totals across different games.",
    "- HR markets are excluded from this desk entirely."
    " If one appears in the pack, treat it as a data error and stand it down.",
    f"- Plus-money longshots priced +{LONGSHOT_AMERICAN_PRICE} or longer (e.g. a Hits Over at +181)"
    " are filtered from this pack. If one appears, treat it as a data error and stand it down.",
    "- Lines are PREGAME-only: candidates whose event already started (first lock in the past)"
    " are filtered from this pack. If a card's as_of/source timestamps fall at or after its"
    " event's first lock, its lines are LIVE/in-play — treat the whole event as a data error"
    " and stand it down.",
    "",
    "REASONING PASSES (pack-only):",
    "- Use this pack ONLY. Do not use memory or the web.",
    "- Never invent or recall odds/lines. Every verdict quotes the exact market_id + line/price from the pack.",
    "- If you need info not in the pack, list it under NEEDS — do not guess.",
    "",
    "RESEARCH PASSES (web-enabled):",
    "- You MAY use current web sources (last 24h).",
    "- Do NOT invent, quote, or update any betting line/price. The pack's lines are the only lines.",
    "- Tie every finding back to a quoted market_id + line/price from the pack.",
    "- Every news item must carry: claim, source name, SOURCE TIER (see §2e), and timestamp.",
    "",
    "WEB DISCOVERY (research passes + manual injury/lineup validation):",
    "- Search first to locate sources; fetch a page only after search returns a specific URL.",
    "- Use targeted queries (e.g. site:wnba.com, site:mlb.com, team name + injury report + date).",
    "- Do not guess URL paths (/injuries, /lineups, /news) without search confirmation.",
    "- Cap page fetches: at most 1-2 per game after search narrows the target.",
    "- Prefer Tier-1: official league/team injury reports, confirmed lineups, NWS weather.",
]


def _format_game_totals_md(
    totals_rows: list[dict[str, Any]], title: str = "# Game totals projection board"
) -> str:
    lines = [title, ""]
    if not totals_rows:
        lines.append("_No eligible totals markets._")
        return "\n".join(lines)
    for row in totals_rows:
        flags = row.get("quality_flags") or ""
        lines.append(
            f"- [{row.get('sport')}] {row.get('market_id')}: {row.get('selection')} "
            f"@ {row.get('line')} ({row.get('price')}) edge={row.get('edge_pct')} "
            f"actionable={row.get('actionable')} flags={flags}"
        )
    return "\n".join(lines)


def build_briefing(
    rows: list[dict[str, Any]],
    target_date: str,
    freshness_lines: list[str] | None = None,
    totals_rows: list[dict[str, Any]] | None = None,
    team_totals_rows: list[dict[str, Any]] | None = None,
    coverage_lines: list[str] | None = None,
    ultimate_alt_rows: list[dict[str, Any]] | None = None,
) -> str:
    derived_market_ids = {
        (str(r.get("sport") or ""), str(r.get("market_id")))
        for r in (totals_rows or []) + (team_totals_rows or [])
        if r.get("market_id")
    }
    lines = [f"SLATE: {target_date}", ""]
    if freshness_lines:
        lines += freshness_lines + [""]
    if coverage_lines:
        lines += coverage_lines + [""]
    lines += ROLE_BLOCK + ["", "### Top EV cards"]
    for r in rows:
        if (
            r.get("_board") == "board_a"
            and (str(r.get("sport") or ""), str(r.get("market_id"))) not in derived_market_ids
        ):
            lines.append(
                f"- [{r.get('sport')}] {r.get('market_id')}: {r.get('selection')} @ {r.get('line')} "
                f"({r.get('price')}) edge={r.get('edge_pct')} units={r.get('recommended_units_pre_news')} "
                f"| {_matchup_display(r)}"
            )
    playable_props = sorted(
        [
            r
            for r in rows
            if r.get("_board") == "board_a"
            and slate_quality.is_player_prop(r)
            and (str(r.get("sport") or ""), str(r.get("market_id"))) not in derived_market_ids
        ],
        key=slate_quality.playable_prop_sort_key,
    )
    lines += ["", "### Playable props by edge"]
    if playable_props:
        for r in playable_props:
            lines.append(
                f"- [{r.get('sport')}] {r.get('market_id')}: {r.get('selection')} "
                f"@ {r.get('line')} ({r.get('price')}) edge={r.get('edge_pct')} "
                f"prob={r.get('model_prob')} units={r.get('recommended_units_pre_news')}"
            )
    else:
        lines.append("- none")
    lines += ["", "### Top signal cards"]
    for r in rows:
        if (
            r.get("_board") == "board_b"
            and (str(r.get("sport") or ""), str(r.get("market_id"))) not in derived_market_ids
        ):
            lines.append(
                f"- [{r.get('sport')}] {r.get('market_id')}: {r.get('selection')} @ {r.get('line')} "
                f"| {_matchup_display(r)}"
            )
    flagged = [
        r
        for r in rows
        if r.get("_board") == "flagged"
        and (str(r.get("sport") or ""), str(r.get("market_id"))) not in derived_market_ids
    ]
    if flagged:
        lines += ["", "### Non-actionable flagged cards"]
        for r in flagged:
            lines.append(
                f"- [{r.get('sport')}] {r.get('market_id')}: {r.get('selection')} "
                f"@ {r.get('line')} ({r.get('price')}) actionable=false "
                f"flags={r.get('data_quality_flags')}"
            )
    lines += ["", "### Slate index"]
    seen: set[str] = set()
    for r in rows:
        eid = r.get("event_id")
        if eid and eid not in seen:
            seen.add(eid)
            lines.append(
                f"- {r.get('sport')} {_matchup_display(r)} | event {eid} "
                f"| first lock: {r.get('_event_starts_at') or 'n/a'}"
            )
    # Games that produced only Game/Team Totals markets (no player/team-prop
    # candidates) never appear in `rows`, so without this they were silently
    # missing from the Slate index while still being quoted later in the
    # Game/Team totals tables — an unverifiable-looking event reference.
    for r in (totals_rows or []) + (team_totals_rows or []):
        eid = r.get("event_id")
        if eid and eid not in seen:
            seen.add(eid)
            lines.append(
                f"- {r.get('sport')} {_totals_event_display(r)} | event {eid} "
                f"| first lock: n/a (totals-only event)"
            )
    # Events that only produced Ultimate Alt Shadow legs (spreads/totals/props
    # priced solely by that lane) never appear in `rows`, `totals_rows`, or
    # `team_totals_rows` either, so they were silently absent from the Slate
    # index while still being quoted in the Ultimate Alt section below — the
    # exact "event not in the supplied Slate index" gap flagged reviewing the
    # 2026-08-08 GROK Ultimate Alt Shadow report (NYM @ PIT).
    for r in ultimate_alt_rows or []:
        eid = r.get("event_id")
        if eid and eid not in seen:
            seen.add(eid)
            lines.append(
                f"- {r.get('sport')} {_matchup_display(r)} | event {eid} "
                f"| first lock: {r.get('event_starts_at') or 'n/a'} (alt-shadow-only event)"
            )
    if totals_rows is not None:
        lines.append("")
        lines.append(_format_game_totals_md(totals_rows, title="### Game totals"))
    if team_totals_rows is not None:
        lines.append("")
        lines.append(_format_game_totals_md(team_totals_rows, title="### Team totals"))
    return "\n".join(lines)
