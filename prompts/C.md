Deep research task. Narrow focus: collect CURRENT (last 24h) injury reports, availability, load management, rest, confirmed lineups/rotations/starters, and usage impacts. Each game tagged MLB or WNBA — apply matching lens.

MLB: SP confirmations + days rest, bullpen usage, posted lineup (bats in/out), platoon.
WNBA: injury status (out/quest/prob), load management, rotation/minutes, back-to-back/travel, star-absence usage shift.

Input contains the pack's markets with exact market_id, selection, line, price.

Web discovery (search before fetch):
- Search first to locate injury reports, lineups, and starter confirmations; read a page only after search returns a specific URL.
- Use targeted queries (e.g. site:wnba.com "[team]" injury, site:mlb.com probable pitcher [date]).
- Do not guess URL paths (/injuries, /lineups, /news) without search confirmation.
- Cap page reads: at most 1-2 per game after search narrows the target.
- Prefer Tier-1: official league/team injury reports and confirmed lineup posts.

Rules (enforced):
- Do NOT invent, quote, or update any betting line/price/selection. Quote the pack lines verbatim.
- Tie EVERY finding directly to one or more quoted market_id + exact line/price from the pack.
- Format news: { claim | source name | source tier (1/2/3) | timestamp }
- For each affected market_id: state impact on the side at the quoted pack line, then verdict CONFIRMS / CONTRADICTS / NEUTRAL (one line why) vs betting at that exact line.
- Report only sourced items. Flag anything the pack's as-of likely missed.
- Never alter a quoted line, price or market_id.