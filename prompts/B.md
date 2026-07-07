Deep research task. Here is today's slate with my model's flagged markets (as-of timestamp in header). Each game is tagged MLB or WNBA — use the matching question set:
- MLB: confirmed starters + days rest, bullpen availability, posted lineup, wind/temp/roof/park, home-plate umpire zone.
- WNBA: injury status + load management, confirmed starters/minutes limits, back-to-back/travel, usage shift if a star sits, pace & blowout risk.
Search current sources (last 24h). Do NOT invent, quote, or update any betting line — the pack's lines are the only lines.

Web discovery (search before fetch):
- Search first to locate sources; read a page only after search returns a specific URL.
- Use targeted queries (e.g. site:wnba.com, site:mlb.com, team name + injury report + date).
- Do not guess URL paths (/injuries, /lineups, /news) without search confirmation.
- Cap page reads: at most 1-2 per game after search narrows the target.
- Prefer Tier-1: official league/team injury reports, confirmed lineups, NWS weather.

For EACH game return:
- news items: each as { claim | source name | source tier (1/2/3 per the pack) | timestamp }
- impact: which market_id(s) it affects and direction, tied to the quoted pack line
- verdict vs my model: CONFIRMS / CONTRADICTS / NEUTRAL, one line why
Only report what you can source. Flag anything my as-of data likely missed.