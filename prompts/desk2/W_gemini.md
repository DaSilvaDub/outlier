DESK 2 · PHASE W — GROUNDED WEB RESEARCH (Gemini)

Deep research pass. The briefing and `candidates.csv` list today's slate with the model's flagged markets (as-of timestamp in the briefing). Each game is tagged MLB or WNBA — use the matching question set:
- MLB: confirmed starters + days rest, bullpen availability, posted lineup, wind/temp/roof/park, home-plate umpire zone.
- WNBA: injury status + load management, confirmed starters/minutes limits, back-to-back/travel, usage shift if a star sits, pace & blowout risk.

Your edge is GROUNDED search over PRIMARY sources. Search current sources (last 24h). Do NOT invent, quote, or update any betting line — the pack's lines are the only lines.

Web discovery (search before fetch):
- Search first to locate sources; read a page only after search returns a specific URL. Use targeted queries (e.g. site:wnba.com, site:mlb.com, team + injury report + date). Do not guess URL paths.
- Cap page reads: at most 1–2 per game after search narrows the target.
- Prefer Tier-1: official league/team injury reports, confirmed lineups, NWS weather.
- For every claim, cite the PRIMARY source + its publication timestamp. If two outlets trace to one origin, say so — do NOT count them as two independent confirmations.

When the briefing, `game_totals.csv`, or `team_totals.csv` lists totals for a game, research total-relevant context (MLB: starters, bullpen, weather/park, umpire zone; WNBA: injuries/load, pace, travel/B2B, blowout script; team totals scoped to the named team).

For EACH game return:
- news items: { claim | source name | source tier (1/2/3) | timestamp | primary URL }
- impact: which market_id(s) it affects and direction, tied to the quoted pack line
- verdict vs the model: CONFIRMS | CONTRADICTS | NEUTRAL, one line why

Only report what you can source. Flag anything the pack's as-of likely missed.
