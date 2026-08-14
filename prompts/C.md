Deep research task. Narrow focus: collect CURRENT (last 24h) injury reports, availability, load management, rest, confirmed lineups/rotations/starters, and usage impacts. Each game tagged MLB or WNBA — apply matching lens.

MLB: SP confirmations + days rest, bullpen usage, posted lineup (bats in/out), platoon.
WNBA: injury status (out/quest/prob), load management, rotation/minutes, back-to-back/travel, star-absence usage shift.

Input contains the pack's markets with exact market_id, outcome_id, selection, line, price.
For game/team totals, `market_id` is the exact `market_id` column from `game_totals.csv` or
`team_totals.csv`; `outcome_id` is the exact `totals_id` from that same row. Quote that row's
selection, line, and price exactly. Never substitute one for the other.

Web discovery (search before fetch):
- Search first to locate injury reports, lineups, and starter confirmations; read a page only after search returns a specific URL.
- Use targeted queries (e.g. site:wnba.com "[team]" injury, site:mlb.com probable pitcher [date]).
- Do not guess URL paths (/injuries, /lineups, /news) without search confirmation.
- Cap page reads: at most 1-2 per game after search narrows the target.
- Prefer Tier-1: official league/team injury reports and confirmed lineup posts.

Rules (enforced):
- Do NOT invent, quote, or update any betting line/price/selection. Quote the pack lines verbatim.
- Tie EVERY finding directly to one or more quoted market_id + outcome_id + exact line/price from the pack.
- Never alter a quoted line, price, market_id, or outcome_id.
- Report only sourced items. Flag anything the pack's as-of likely missed.

# OUTPUT FORMAT — JSON ONLY

Respond with a single JSON object matching this exact shape. No prose before or after the JSON, and
no other text of any kind — the response is parsed as JSON directly.

```
{
  "schema_version": "1.0",
  "pass": "C",
  "pack_date": "<the pack_date value supplied to you in the input>",
  "candidates_sha256": "<the candidates_sha256 value supplied to you in the input>",
  "game_totals_sha256": "<the game_totals_sha256 value supplied to you in the input>",
  "team_totals_sha256": "<the team_totals_sha256 value supplied to you in the input>",
  "findings": [
    {
      "market_id": "<exact market_id from the pack>",
      "outcome_id": "<exact outcome_id from the pack>",
      "stream": "candidates" | "game_totals" | "team_totals",
      "selection": "<exact pack selection>",
      "line": "<exact pack line>",
      "price": "<exact pack price>",
      "verdict": "CONFIRMS" | "CONTRADICTS" | "NEUTRAL",
      "claim": "<one sentence: the finding, and why it CONFIRMS/CONTRADICTS/NEUTRAL vs betting this exact line>",
      "source_name": "<publication or official source name>",
      "source_tier": 1,
      "source_timestamp": "<ISO 8601 timestamp of the source>",
      "evidence": []
    }
  ],
  "no_sourced_findings": false
}
```

Every `market_id` and `outcome_id` you emit must already exist in the supplied pack data — never
invent one. `source_tier`: `1` = official league/team source, `2` = credentialed beat reporter,
`3` = other outlet. `evidence` may always be left as an empty array; it is not required for this
pass. Echo `schema_version`, `pass`, `pack_date`, and the three `*_sha256` values exactly as
supplied — never alter, guess, or omit them.

If no current, sourced finding materially affects any pack market, return `"findings": []` and
`"no_sourced_findings": true` — do not fabricate a finding to avoid an empty array.
