# Issues

- [ ] Re-auth standalone Outlier session and run live MLB/WNBA discovery.
- [ ] After discovery, decide whether MLB weather/ballpark/pitcher splits are available through stable endpoints.
- [ ] After discovery, decide whether WNBA parsed insights should be added as V2.

## Pipeline data-quality follow-ups (from 2026-07-10 report review)

The desk now receives resolved team/opponent/full-name/matchup/market_label context and
the alt-line `priced_line`, so it no longer guesses teams/markets off the event_id hash.
Deferred, larger-scope items surfaced by the same review:

- [ ] Roster / probable-pitcher reconciliation: player→team is trusted from the Outlier feed
      with no cross-check, so a stale feed value would still pass. Needs an external roster
      source to detect trades/reassignments — out of scope until such an endpoint exists.
- [x] Sport↔market-type consistency + line-magnitude sanity guard: `classify_foreign_market`
      (registry.py) + `market_validation_flags` (pack.py) now emit non-fatal
      `cross_sport_market:<LEAGUE>` / `implausible_line` / `non_numeric_line` entries in
      `data_quality_flags`. Deterministic; never hard-drops or false-flags a valid market.
- [x] Per-market fetch-error visibility: line-movement status now carries `error_market_ids`
      and the pack freshness caveat names the missing markets (capped) instead of only a count.

