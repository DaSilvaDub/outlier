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
- [ ] Sport↔market-type consistency + line-magnitude sanity guard: an out-of-sport
      proposition or an implausible line currently normalizes to `market_raw` and flows
      through unflagged. Add a validation flag (not a hard drop).
- [ ] Per-market fetch-error visibility: failed line-movement markets vanish beyond an
      aggregate `fetch_error_count`; surface which markets are missing in the pack.

