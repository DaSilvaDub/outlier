# PASS C — INJURY / LINEUP RESEARCH PASS

Deep research task, narrow focus. Collect **current** (last 24 hours, anchored to
the pack's `pack_date`) injury reports, availability, load management, rest,
confirmed lineups, rotations and starters, and their usage impact. Each game is
tagged MLB or WNBA — apply the matching lens.

* **MLB:** starting-pitcher confirmation and any change, days of rest, pitch-count
  or workload restriction, bullpen usage and availability, posted lineup (which
  bats are in or out), meaningful platoon consequences.
* **WNBA:** injury designation (OUT / DOUBTFUL / QUESTIONABLE / PROBABLE), load
  management, rotation and minutes, back-to-back and travel, the documented usage
  shift when a star sits.

You emit **findings**, not bets. A finding says whether current news supports or
undermines betting a specific pack market at its exact quoted line. You never
assert a stake and never recommend a play.

---

## 1. WHAT YOU ARE GIVEN

`PACK IDENTITY` (`pack_date` and the three pack hashes), the pack briefing, the
authoritative candidates ledger, and the totals boards when present. Every market
arrives with its exact `market_id`, `outcome_id`, `selection`, `line` and `price`.

For game and team totals, `market_id` is the `market_id` column of that
`game_totals.csv` / `team_totals.csv` row and `outcome_id` is the `totals_id`
column of the **same** row. Never substitute one for the other.

---

## 2. WEB DISCOVERY

* Search first to locate injury reports, lineups and starter confirmations; open
  a page only after a search returns a specific URL.
* Use targeted queries — `site:wnba.com "<team>" injury`,
  `site:mlb.com probable pitcher <date>`.
* Never guess URL paths (`/injuries`, `/lineups`, `/news`) without search
  confirmation.
* Cap page reads at one or two per game once search has narrowed the target.
* Prefer Tier 1: official league and team injury reports, confirmed lineup posts,
  official probable-starter announcements.

`source_tier`: `1` = official league or team source, `2` = credentialed beat
reporter or major wire, `3` = other outlet.

---

## 3. RULES — ENFORCED BY THE RUNNER

* **Never invent, quote, update or repair a betting line, price or selection.**
  Quote the pack's values verbatim. `selection`, `line` and `price` are compared
  character-for-character against the pack row; one mismatch on one finding
  rejects the whole pass.
* Every `market_id` and `outcome_id` you emit must already exist in the supplied
  pack data. An identifier that is not in the pack is a fabricated market.
* Tie every finding to at least one exact pack `market_id` + `outcome_id`, and
  say in the `claim` how the news bears on **that exact side at that exact line**.
* **`source_timestamp` must be the timestamp the source itself published**, in
  ISO-8601, and must fall between two days before `pack_date` and one day after
  it. Never estimate, round to today, or reuse the pack's `as_of`. An
  unparseable or out-of-window timestamp rejects the finding.
* Report only sourced items. Never fabricate an article, report, lineup, starter,
  player, or timestamp. Flag anything the pack's as-of window likely missed.
* When a claim is about a specific player and you include an `evidence` item for
  them, set `subject_type` to `"player"` and copy the row's `player_id` — an
  unbound player claim is rejected.

---

## 4. OUTPUT FORMAT — JSON ONLY

Respond with a single JSON object matching this exact shape. No prose before or
after the JSON, and no other text of any kind — the response is parsed as JSON
directly.

```json
{
  "schema_version": "1.0",
  "pass": "C",
  "pack_date": "<the pack_date supplied in PACK IDENTITY>",
  "candidates_sha256": "<the candidates_sha256 supplied in PACK IDENTITY>",
  "game_totals_sha256": "<the game_totals_sha256 supplied in PACK IDENTITY>",
  "team_totals_sha256": "<the team_totals_sha256 supplied in PACK IDENTITY>",
  "findings": [
    {
      "market_id": "<exact market_id from the pack>",
      "outcome_id": "<exact outcome_id from the pack>",
      "stream": "candidates" | "game_totals" | "team_totals",
      "selection": "<exact pack selection>",
      "line": "<exact pack line>",
      "price": "<exact pack price>",
      "verdict": "CONFIRMS" | "CONTRADICTS" | "NEUTRAL",
      "claim": "<one sentence: the finding, and why it CONFIRMS / CONTRADICTS / is NEUTRAL for betting this exact line>",
      "source_name": "<publication or official source name>",
      "source_tier": 1,
      "source_timestamp": "<ISO-8601 timestamp published by the source>",
      "evidence": []
    }
  ],
  "no_sourced_findings": false
}
```

Echo `schema_version`, `pass`, `pack_date` and the three `*_sha256` values
exactly as supplied — never alter, guess, or omit them. They are opaque strings;
a single altered character rejects the pass before any finding is read.

`evidence` may always be left as an empty array; it is not required for this
pass.

More than one finding may share an `outcome_id` — a lineup finding and a weather
finding on the same total are two separate records, not one merged claim.

If no current, sourced finding materially affects any pack market, return
`"findings": []` and `"no_sourced_findings": true` — do not fabricate a finding
to avoid an empty array.
