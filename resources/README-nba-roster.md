# NBA roster runtime state

Run `python scripts/refresh_nba_roster_registry.py --date YYYY-MM-DD` from any
checkout. This creates `resources/nba_roster_registry.json` and an immutable
raw-response bundle under `nba_roster_snapshots/`. No Gemini file is required.
The code and tests are tracked; current injuries are runtime state, not a
permanently frozen registry checked into Git.

The default NBA refresh wrapper fetches all 30 ESPN rosters and the league
injury feed before filtering. A failed refresh stops the wrapper. The filter
independently checks the six-hour maximum age, provider season/timestamps,
raw response hashes, and a deterministic identity join. It cannot silently
use the former copied Gemini schema. All 32 required feed receipts must exist.
The wrapper retains each filter receipt and the normalized registry version;
temporary pack staging cleanup does not remove this evidence. Current snapshots
support today/next-day filtering, not retrospective or far-future availability.

Only explicit OUT/INACTIVE/SUSPENDED/IR statuses are excluded. A nonempty
injury list alone is not OUT. Unknown names/IDs, ambiguous identities and stale
or incomplete evidence fail closed. Probable/questionable labels are retained
as availability evidence, not approval to stake.

Snapshots reflect a single current ESPN provider observation. They do not
independently establish official NBA team injury reports, future availability,
betting edge or model promotion. Existing publication holds remain in force.
