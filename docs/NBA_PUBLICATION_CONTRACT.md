# NBA daily publication

`refresh_nba_pack.ps1` publishes only the requested Eastern slate. Prior-slate
fallback cannot authorize rows. Each row needs an aware future game timestamp,
a finite line, selected OVER/UNDER American price, and a sourced quote timestamp
(`quote_timestamp`, `odds_timestamp`, `odds_as_of`, or `quote_as_of`) no more than
12 hours old. Explicit stale, final, live, postponed, or cancelled inputs fail.
The 12-hour limit is an upper bound, not a guarantee a book still offers a price.

Availability must identify the player and exact game, not just roster membership.
The registry's player entry may contain `game_availability` observations:

```json
{
  "slate_date": "2026-11-23",
  "matchup_abbr": "AAA @ BBB",
  "reported_utc": "2026-11-23T21:30:00Z",
  "source": "official game report reference",
  "report_submitted": true,
  "status": "PROBABLE"
}
```

Observations require an aware report timestamp within six hours and matching
`game_id` on both records, or an exact matchup when IDs are unavailable. Probable
and questionable remain eligible; confirmed OUT/inactive/suspended/IR are removed.
Missing, old, conflicting, or unsubmitted observations stand down as unknown.
Legacy roster-only registries cannot authorize a game. No new provider fetch is
implied by this contract: the upstream supplier must provide scoped evidence.

Every CSV has a `.status.json` sidecar with slate date, exact SHA-256, row count,
actionable count, and status. Empty results explicitly say `awaiting_odds` or
`awaiting_availability` and replace older CSVs. The filter summary carries the
same counts and hash. Copies are checked at each destination, including rechecking
that games have not started. Inconsistent bundles stop publication.

Companion edges and grades must match the final pack's frozen player/game/market/
line/side/price contract. Raw top-prop narratives are replaced by the verified
status. Totals projections and matchup narratives are research references with
zero actionable totals until executable prices are supplied.

For offline validation, use `-SkipPipeline -SkipDrive -ScratchOut <temp CSV>
-PropSource <fixture CSV> -Registry <fixture registry>`. Scratch runs do not stage
the live Desktop tree. `-SkipPipeline` never launches an automatic source refresh.
Live publication remains subject to the existing publication hold.
