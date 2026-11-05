# Audit output quarantine — 2026-11-05

Status: **QUARANTINED / NO BET** for withdrawn files; exact execution counts and
errors are recorded in `quarantine-output-manifest.json`. This is a conservative
publication-tree quarantine, not a certification of every source artifact.

The user supplied Deliverable 8 categories covering NFL validated picks/stakes,
first-TD/PRIZEPICKS export rows, stale alt floors, matchup rankings/scorecards and
stub reports; NBA early-look props/reports, dated master reports, May reports,
packs and stale audit statuses; NCAA extra packs, over boards, posted props,
mispriced totals/team points, dated simulation packs, T-1 snapshots and audit
narratives. To avoid relying on filenames as a safety classifier, the utility
withdraws a superset of current published files from the named sport folders.

Explicit roots:

- Desktop `today`, resolved to `C:\Users\dasil\OneDrive\Desktop\today` (the Desktop
  junction alias is deduplicated).
- `G:\My Drive\today` and the explicit C: Google Drive `today` mirror.
- `BETTING REPORTS\NBA`, `NFL`, `NCAA` under G: Google Drive, its explicit C:
  mirror, and OneDrive Desktop.

Only `Fresh_Data\NBA`, `NFL`, `NCAA`, the named sport CSVs in immediate
`extra_packs*` aliases, specific NFL/NBA/NCAA report aliases at the root, and
those three BETTING REPORTS sport subtrees are scanned. MLB/WNBA extra packs,
generic candidate files, user notes and `Bankroll_Master_Ledger.md` are retained.
Historical Older_Data and unrelated Drive directories are not traversed.
SQLite/database files and their WAL/SHM/journals remain research-only, unchanged.
No repository data, calibration, model source, or live ledger is moved.
Repository report originals and Older_Data copies remain research evidence,
subject to the publication hold; they are not physically moved by this utility.

Each publication root receives a same-filesystem `quarantine\2026-11-05` tree
preserving relative paths. Every planned file has a source SHA-256 and size;
the source must still match before moving, and the destination hash is checked.
Existing destinations are never overwritten. Missing scopes, errors, retained
databases and successful moves appear in the manifest. The receipt is persisted
after every action so an interruption leaves a reviewable record.

Symlinks and junction redirections inside scoped trees are rejected. OneDrive
cloud placeholders are permitted only with resolved containment checks at every
path; treating all cloud reparse attributes as directory redirections would
prevent quarantining the actual OneDrive publication folder.

Each scoped sport publication folder receives `QUARANTINE_HOLD.json` with
`staking_allowed: false`. These markers are notices; the runtime publication
hold and removal of unsafe files enforce the hold for consumers that would not
otherwise read notices. A future run must not republish quarantined evidence.

Unresolved: Deliverable 8 category labels do not uniquely identify every source
repository artifact, every September 27 variant, every historical audit
narrative or additional output location outside these explicit roots. No claim
is made that these unlocated sources have been removed globally. Existing
research artifacts must remain non-actionable until individually rebuilt and
verified. Refer to manifest errors and missing scopes before judging coverage.

Verification: six temporary-tree tests cover inventory-only behavior,
same-filesystem hash-preserving moves, idempotence, ledger/archive preservation,
source changes, destination escapes, duplicate aliases, missing roots, and
preservation of unrelated sport packs and user files. Ruff
passes. No live pipeline or reasoning provider was invoked.

Execution receipt: **668 export files quarantined**, **2 research databases
retained**, **18 publication hold markers written**, **0 move/hash errors**.
The eight inventory issues were absent sport subfolders at inventory time,
listed individually in the manifest. Existing Desktop outputs contributed 250
files; G: today 314; C: Drive mirror today 36; G: BETTING REPORTS 35; C: Drive
mirror BETTING REPORTS 33. Approximately 104 MB of files were moved in place,
not copied or deleted. The manifest in this directory matches the external
receipt byte-for-byte (SHA-256
`691a61f81baec0a243097b2f6a88959227796d7a44841e5fbe8a272efd16c97d`).
