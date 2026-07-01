Research the game and market context in the supplied Outlier pack.

Use the pack's slate date and as-of timestamp as the time anchor. Look for news published in
the 24 hours leading up to game time; do not substitute the current calendar date when reviewing
an older pack.

Focus only on:

- MLB: confirmed starting pitchers, pitcher rest, bullpen usage, posted lineups, players in/out,
  and platoon implications.
- WNBA: injury status, availability, load management, rotations/minutes, back-to-backs/travel,
  and usage changes caused by absences.

The briefing and authoritative candidates.csv are supplied below. Treat each candidates.csv
market_id, selection, line, and price as immutable.

Output rules:

- Report sourced information only.
- Emit exactly one record per finding and affected market. If one finding affects multiple
  markets, repeat it once for each market.
- Every record must use this exact pipe-delimited form on one line:
  FINDING | market_id=<exact> | selection=<exact> | line=<exact> | price=<exact> |
  verdict=<CONFIRMS|CONTRADICTS|NEUTRAL> | claim=<text> | source_name=<text> |
  source_tier=<1|2|3> | source_timestamp=<timestamp>
- Copy market_id, selection, line, and price verbatim from candidates.csv. Never invent, update,
  normalize, or reformat them.
- Do not use the pipe character inside a field value. Do not add headings or prose outside the
  records.
- If there are no sourced findings, output exactly: NO_SOURCED_FINDINGS
