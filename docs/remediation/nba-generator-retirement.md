# NBA early-look generator retirement — 2026-11-05

Status: **RETIRED / NO BET**. This retirement does not promote a replacement model.

The exact 13 entrypoints listed in `nba-generator-retirement-manifest.json` were
backed up and SHA-256 verified before their active sources were replaced. Seven
simulation entrypoints emitted fixed player odds, EV and probability strings;
five next-day narrative generators embedded betting percentages/stakes; one
exporter copied those deliverables to Desktop/Drive Fresh_Data.

Active directory:
`C:\Users\dasil\.gemini\config\skills\nba-master-betting-pipeline\scripts`

Preserved originals and original-path/hash manifest:
`C:\Users\dasil\outlier-quarantine\2026-11-05\nba-generators`

Each active entrypoint is now a dependency-free tombstone that raises
`SystemExit` on invocation or import with an explicit `RETIRED / NO BET` message.
It creates no reports, simulations, candidate rows or published files. Original
files remain evidence only; they must not be run for staking or publication.

`scripts/retire_nba_early_look.py` implements an exact filename allowlist, refuses
missing/symlink files and nested backup roots, verifies all originals before
replacing anything, refuses changed backups/sources, and supports idempotent
re-execution. It does not traverse or modify other skill files, SKILL.md,
calibration, ledgers or data directories.

Offline regression checks in `tests/test_retire_nba_early_look.py` exercise all 13
tombstones through execution and import-style loading, assert nonzero exits and
no output files, scan for numeric-percent literals, verify original hashes and
idempotence, reject corrupt backups, and preserve unrelated sources.

Verification: five offline tests passed; Ruff passed. Re-running retirement
verified every preserved original hash and left the tombstones unchanged. A
numeric-percent literal scan of the 13 active sources returned no matches. A
direct invocation of the actual `run_next_day_slate_20261105.py` tombstone exited
1 with `RETIRED / NO BET`. Pytest exited 0 but emitted an unrelated cleanup
permission warning for the pre-existing temporary `pytest-current` link.

The full Deliverable 8 output quarantine list is separate from this generator
retirement. Existing published reports are not made safe merely by retiring
their generators. A future betting replacement requires actual source quotes,
legal market lines and validated probabilities; varying synthetic values does
not satisfy that requirement.
