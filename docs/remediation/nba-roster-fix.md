# NBA roster refresh and exact-date export repair - 2026-11-05

**Installed. Publication remains held / NO BET.**

The manually copied Gemini registry is no longer a trusted input. Tracked
refresh, validation and filtering code now fetches ESPN's team directory,
30 team rosters and the league injury feed. Runtime snapshots are generated
under `resources/`, with immutable raw responses, source URLs, SHA-256 hashes,
fetch times, provider timestamps, season identity, and a normalized version.
No manual resources-directory copy is needed in a fresh checkout: the default
refresh wrapper creates it before filtering. Explicit `-RegistryPath` is also
supported; an invalid or stale supplied registry fails rather than falling back.

The loader rebuilds the roster from all 32 raw feeds and compares the result
with the snapshot. Missing/unverified legacy state, wrong seasons, incomplete
teams, duplicate player identities, stale/future evidence and unknown candidate
identities fail closed. The maximum age is six hours; normal wrapper execution
refreshes every time. Current observations are restricted to today/next-day
slates, never historical or far-future dates. Explicit candidate ESPN ID and
team fields must match. Injury presence alone no longer means OUT: only
OUT/INACTIVE/SUSPENDED/IR statuses are removed. Questionable is not upgraded to
ACTIVE and is not approval to stake.

The default wrapper stores the registry and raw versions durably, and retains
per-run filter receipts under `resources/nba_filter_receipts/`. Temporary pack
cleanup does not delete these receipts. Runtime snapshots remain intentionally
untracked; code, documentation and deterministic tests are in the PR.

The exporter now selects only explicit exact Eastern slate dates. Offset-aware
game timestamps must agree in Eastern time; legacy naive display timestamps
are not assigned an invented timezone. It searches every configured producer
location, including the actual `output/MATCHUP/PROP EDGES` path. It rejects
stale/empty/wrong-day inputs, its own output as a source, and cross-date fallback.
Producer and quote timestamps are checked when supplied by JSON companions.
It never starts live producers implicitly and does not create/replace output
on source validation failure. Missing quote timestamps remain unverified.

## Live diagnostic evidence

At **2026-11-05 08:12:25 UTC**, ESPN returned **30 teams / 521 players**:
430 ACTIVE, 81 OUT, 10 QUESTIONABLE. All injury identities joined. This is
current ESPN provider evidence, not an independently verified official NBA
injury report. A five-player diagnostic removed the two OUT names and retained
two ACTIVE names and one QUESTIONABLE name. The canonical filter also passed
that check using its default installed registry path.

See `nba-roster-live-verification.json`, `nba-roster-diagnostic/`, and
`nba-roster-deployment.json`. The old canonical registry and scripts were
hash-preserved under the backup directory recorded in the deployment receipt.

The current real prop producer sources are stale/unavailable. The repaired
exporter exited **2** and created **no output** rather than producing a zero-row
pack or relabeling another slate. See `nba-current-source-export-check.json`.
No upstream betting pipeline, paid reasoning, data/calibration rewrite, or
publication hold override was run.

## Verification

- 18 offline roster/filter cases passed (including source tampering, returned
  players, newly OUT players, DTD, unknown identity, season/date/TTL failures,
  failed refresh preserving prior state and immutable snapshot retention).
- 14 PowerShell orchestration cases passed, including automatic registry
  refresh with no resources copy and refresh failure withholding publication.
- 29 exporter cases passed, including ET rollover and stale quote metadata.
- Ruff and PowerShell syntax checks passed. Review blockers were corrected
  and re-reviewed. Pytest exited zero with the existing Temp/pytest-current
  permission warning during atexit cleanup.

To refresh availability explicitly, run
`python scripts/refresh_nba_roster_registry.py --date YYYY-MM-DD`.
This is a data-only provider fetch; it does not resume publication. Keep the
existing hold until market sources and the broader audit are revalidated.
