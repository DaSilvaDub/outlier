# Structured AI Verdicts and Deterministic Validation

Revision 10 (2026-08-13). Ten revisions, eight external review rounds. Full detail on every finding
and its fix is in [Review resolutions](#review-resolutions); this is the one-line-per-round summary:

| Rev | Trigger | What it fixed |
| --- | --- | --- |
| 2 | Round 1 | Totals-identity ambiguity, B/E input gaps, shadow-mode contradiction, wrong provider for C |
| 3 | (owner ruling) | HRR/BB and total-bases market-policy conflicts resolved |
| 4 | Round 2 | C forced through the betting schema instead of its own findings-only contract |
| 5 | Round 3 | Publication claimed atomic but wasn't; `record_id` had no stable identity; prose invariant overstated; no-E fallback unspecified; stand-downs could fail the pass ratio |
| 6 | Round 4 | `request_sha256` conflated with response identity, breaking `--force`; no-E fallback not total/fail-closed; `C_verdict` reduction undefined |
| 7 | Round 5 | Five independent per-pass pointers ≠ one coherent desk result; `publication_id` didn't cover the whole directory; status/legacy files outside the atomic boundary; retention was age-only |
| 8 | Round 6 | Subset runs could advance the desk snapshot; `publications` couldn't literally be "copied from E" (E has no self-reference); history could log an uncommitted snapshot; legacy files could bypass the snapshot; retention prose vs. tests disagreed |
| 9 | Round 7 | `e_usable`/`required_usable` reuse reintroduced the stale-flat-file bug it replaced, and had no `fallback_no_e` state; no mutual exclusion across writers; `DERIVED_PACK_OUTPUTS` would crash on directory unlink and bypass retention; duplicate `record_id` disambiguation had no real tiebreak for byte-identical content |
| 10 | Round 8 | E accepted as fresh on pack hashes alone while still pinning superseded upstream publications; verdict lock didn't cover the pack CSVs its own readiness check reads; retention roots missed pass-level `current.json` targets and transitive citations; a crashed writer bricked publication permanently; `record_id` truncated SHA-256 to 48 bits while claiming uniqueness by construction |

Round 8's five fixes, in the detail the table above compresses:

- **E's freshness test gains a second condition: its pinned upstream publication IDs must equal the
  ones step 2 just selected.** Pack-fingerprint equality alone is insufficient precisely because an
  A-only rerun changes no pack CSV — old E stayed "fresh" while pinning old A, which would have
  published a snapshot naming the new A alongside an E that never saw it.
- **Lock ordering, not lock independence.** `_desk_publication_ready` hashes the live pack CSVs that
  `daily_job` rewrites, so the two locks protect overlapping state; every fingerprint-reading path now
  takes `.daily_job_lock` before `.writer_lock`, in a fixed global order.
- **Retention roots gain pass-level `current.json` targets and a transitive closure over citations**,
  so an unreconciled publication backing `latest_preview` is not deleted, and a retained E never
  outlives the upstream publications it cites.
- **The lock carries owner metadata and a defined stale-recovery rule** (same-host dead PID past a
  timeout is breakable; cross-host never is, given OneDrive-shared packs), replacing a design where a
  killed writer permanently bricked the pack's desk output.
- **`record_id` uses the full SHA-256 digest.** Collapsing identical records never addressed two
  *different* records colliding on a 48-bit prefix — "astronomically unlikely" is not the
  "unique by construction" this document claimed.

Round 7's four fixes, in the detail the table above compresses:

- **A purpose-built `_desk_publication_ready` predicate replaces the reused `e_usable`/
  `required_usable`.** That predicate falls back to bare flat-file existence for any phase outside
  the current invocation's `steps`, and has no state for `fallback_no_e` at all — round 6's fix
  reused it and reintroduced exactly the bug it existed to close, one layer down. The new predicate
  checks each pass's *current* publication against the pack's *live* fingerprint, not file existence
  against any point in history, and treats the deterministic fallback as always-available rather than
  as an artifact that has to already exist.
- **A single advisory writer lock**, reusing `daily_job._acquire_writer_lock`'s exact idiom, now
  guards every mutation to `packs/<date>/verdicts/` — per-pass publish, desk-level publish,
  projections, and retention alike — closing the concurrent-writer race commit-then-log ordering
  alone could not close.
- **The versioned store is structurally excluded from `pack.py`'s rebuild cleanup**, not added to
  `DERIVED_PACK_OUTPUTS` as a prior draft proposed — that loop calls bare `.unlink()`, which raises on
  a directory, and even for the single-file entries would have discarded the coherence contract on
  every routine rebuild, bypassing every retention rule in one blind pass.
- **Byte-identical duplicate records are collapsed, not disambiguated.** The previous "sort by
  content, rank within group" scheme had no field left to sort on once content is identical by
  definition, so it silently degraded back to array position — the exact instability content-hashing
  was introduced to remove. Collapsing removes the need for any tiebreak at all.

See [Review resolutions](#review-resolutions) for
the full history and [Resolved market policy](#resolved-market-policy) for the market-policy ruling,
now including player/team BB scoping.

## Summary

Insert a machine-checkable contract between every AI research-desk pass and the Markdown it
currently emits. Each pass first returns a schema-validated JSON envelope in a shape suited to what
that pass actually does; a deterministic validator re-derives every number and identity from the
pack and rejects any record that invents, alters, or exceeds pipeline truth; only records that pass
validation in `enforce` mode reach an authoritative report, and the report's numeric columns are
rendered locally from pack rows rather than from model prose.

There are three envelope kinds, not one. A, D, and B are betting passes and emit a **verdict
envelope** (`BET`/`PASS`/`STAND_DOWN`, with a stake). C is a research pass with no stake and no bet
authority and emits a **finding envelope** (`CONFIRMS`/`CONTRADICTS`/`NEUTRAL`, matching its live
`FINDING |` contract). E is a synthesis pass over the other four's already-validated output and
emits a **reconciliation envelope** that may only narrow what upstream passes already validated.
Forcing all three through one betting schema was revision 3's central defect, corrected here.

The models keep authority over explanation, contradiction, and stand-down judgement. They lose
authority over market identity, lines, prices, players, and stake size.

The implementation starts from synchronized `origin/master` in this worktree. It does not invoke
the AI Research Desk or any paid reasoning provider; all provider wiring is exercised with
injected fake clients, matching the existing runner tests.

## Current state

`prompts/A.md` §5, §11, §12, §14, §15 already state every rule this plan enforces — actionability,
high-variance exclusions, prohibited markets, the pregame gate, data-quality rejections,
`priced_line` reconciliation, `recommended_units_pre_news` as the stake ceiling, and a final audit
checklist. `outlier_scrapers/pack.py` already computes the numbers those rules refer to
(`model_prob`, `edge_pct`, `recommended_units_pre_news`, `max_units`, `actionable`, `board`,
`data_quality_flags`), and `ROLE_BLOCK` restates the constraints to every provider.

Almost nothing checks compliance. `reasoning.py` (A), `gemini_research.py` (B),
`claude_reasoning.py` (D), and `claude_synthesis.py` (E) each take the model's free text, wrap it in
YAML front matter, and write it to the pack. `run_desk.py` records only whether a file was produced
and whether its `request_sha256` changed.

The exception is `c_research.py` (C), which already implements the pattern this plan generalizes:
a strict record grammar, a market index built from `candidates.csv` plus both totals boards, and
`validate_output` rejecting unknown `market_id`s, altered `selection`/`line`/`price`, invalid
verdict or source tier, and out-of-window timestamps. C is the precedent for the *mechanism*
(identity index, echo-diff, timestamp window), not for the *schema*. C's live contract has no
`recommended_units` and a `CONFIRMS`/`CONTRADICTS`/`NEUTRAL` verdict, not `BET`/`PASS`/`STAND_DOWN` —
it is a research pass, not a betting pass, and stays one. This plan factors the shared identity and
tamper-checking machinery out of `c_research.validate_output` into `pack_index.py` /
`verdict_gate.py` so C, A, D, and B all use it, while giving C its own envelope kind rather than
bolting a stake field onto a pass that has never had one.

Two defects in that precedent are fixed here rather than propagated:

- `c_research._market_index` builds `{row["market_id"]: row}`. Where `candidates.csv` carries both
  sides of one market, the second row silently overwrites the first, and the tamper check then
  compares against the surviving side. The `(market_id, outcome_id)` key removes this class of bug.
- Totals rows are indexed under `totals_id` while candidate rows are indexed under `market_id`, two
  different column meanings in one dict. See [Canonical verdict identity](#canonical-verdict-identity).

## Architecture

Three new modules, all standard library only. The repo has no `pydantic`/`jsonschema` dependency and
`outlier_scrapers/schema.py` already establishes the hand-rolled "return a list of error strings"
convention; the new code follows it so `pyproject.toml` dependencies stay unchanged.

- `outlier_scrapers/verdicts.py` — the envelope schema, its version constant, the strict parser
  that turns raw model output into typed records, and the JSON Schema dict handed to providers that
  support native structured output.
- `outlier_scrapers/pack_index.py` — an immutable authoritative index built from the pack on disk:
  candidate rows keyed by canonical identity, the totals boards, the player roster, the injury
  ledger, per-event lock times, and the loaded `PortfolioPolicy`. This is the only thing the
  validator is allowed to consult.
- `outlier_scrapers/verdict_gate.py` — the deterministic validator. Pure function of
  `(envelope, index, now)` returning a per-verdict list of violation records. No I/O, no clock reads,
  no network, no model calls.

A fourth module, `outlier_scrapers/verdict_report.py`, renders the final Markdown from validated
verdicts joined against the index.

`runner_common.py` gains the shared two-stage mechanics so the five provider runners stay thin,
exactly as it already holds request-hashing, candidate validation, and atomic writes. A's duplicated
`extract_yaml_request_hash` and inline request-hash construction move there in the same pass.

## Canonical verdict identity

The pack has two identity conventions and the prompt has a third, so the plan must pick one before
any fixture exists.

- `candidates.csv` (`pack.CANDIDATES_HEADER`) carries `market_id` and `outcome_id` as separate
  provider values. `market_id` alone does not identify a side.
- `game_totals.csv` / `team_totals.csv` (`game_totals.GAME_TOTALS_HEADER`) carry the provider
  `market_id`, a synthetic `totals_id = f"{market_id}:{line}:{side}"`, and `outcome_id` set equal to
  `totals_id` (`game_totals.py:848,857`); the alternate-ladder path sets `totals_id = market_id`
  (`game_totals.py:972`).
- `prompts/A.md:78` instructs models to emit the `totals_id` **as** `market_id` for totals rows.

Resolution: **`outcome_id` is the canonical join key for every stream.** It is unique per side in
candidates and equals `totals_id` in totals, so one index serves both. `market_id` remains required
in the envelope and is validated as a corroborating field, with a totals-aware rule: a totals verdict
may carry either the CSV `market_id` or the `totals_id` in its `market_id` field during the
compatibility window, and a mismatch that is neither yields `market_id_mismatch`.

`prompts/A.md` §3 is amended in the same change to instruct models to emit `market_id` from the
`market_id` column and `outcome_id` from `totals_id`, so the compatibility branch can be removed
once every prompt is on the new contract. Amending the prompt is part of the work, not a follow-up:
leaving §3 as written guarantees the ambiguity survives in production output.

## Three envelope kinds

All three share an `evidence` item shape (see below) and the identity fields `market_id`,
`outcome_id`, `stream`, `selection`, `line`, `price`, `book`. They differ in what a record asserts.

Every record in every envelope kind also carries **`record_id`**, assigned by `verdict_gate.py`
after parsing, never trusted from the model. This exists because `outcome_id` alone is not a stable
citation target — C in particular may legitimately emit more than one finding for the same
`outcome_id` (e.g. a lineup-status finding and a weather finding on the same game total), and prior
to this revision E's `cites` and the `verdict_records` persistence key both assumed one record per
`(pass, outcome_id)`, which is false for C.

A prior draft of this derivation used `f"{pass}:{outcome_id}:{i}"` with `i` the record's array
position. That is order-dependent, and order is not stable: the repair loop (see
[Repair loop and failure policy](#repair-loop-and-failure-policy)) re-calls the model with a
correction for one violation, and nothing constrains the model to re-emit its other, unrelated
records in the same array order on the second attempt — the same logical finding could receive a
different `record_id` purely because the model listed it second instead of first. Since E's `cites`
and `verdict_records`' primary key both depend on `record_id` being a stable name for one specific
piece of content, a citation or a persisted row can silently point at a *different* record after a
repair round that never touched the record in question.

`record_id` is instead derived from the record's content:

1. Compute `content_hash = sha256(canonical_json(record excluding record_id)).hexdigest()` — the
   **full 64-character digest, not a truncation**. The record's identity-bearing fields (`market_id`,
   `outcome_id`, `stream`, `selection`, `line`, `price`, `book`, `verdict`, `claim`/`narrative` as
   applicable, `recommended_units` where present) are serialized with sorted keys. Two records are the
   same record, for citation purposes, exactly when their content is identical — this is a stronger
   and more useful notion of identity than "happened to be emitted at the same array position."

   A prior draft truncated to 12 hex characters (48 bits) and then claimed `record_id` was "unique by
   construction." Collapsing identical records, added in step 3, does not deliver that: it makes
   *identical* content share one ID, which is intended, but says nothing about two *different* records
   whose digests happen to agree in the first 48 bits. Such a pair would silently share a
   `record_id` — colliding in E's `cites` (a citation resolving to the wrong record) and in
   `verdict_records`' primary key (one row overwriting the other). At 48 bits the birthday bound is
   around 2^24 records before a collision is more likely than not, which is far outside any plausible
   slate — but "astronomically unlikely" and "unique by construction" are different guarantees, and
   this document asserts the latter, in a system where a mis-resolved citation silently reassigns a
   wager's provenance. The full digest costs 52 additional characters in an internal identifier that
   no human types, and buys an actual construction-level guarantee rather than a probabilistic one.
2. `record_id = f"{pass}:{outcome_id}:{content_hash}"`. Reordering the envelope's array, whether from
   a repair round or ordinary provider non-determinism on an otherwise-unchanged record, changes
   nothing about this value, because nothing about it depends on position.
3. **Exact duplicates are collapsed, not disambiguated.** A prior draft proposed breaking ties among
   byte-identical records by their rank in a list sorted on `(outcome_id, content_hash,
   json.dumps(record, sort_keys=True))` — but for two records with genuinely identical content, every
   one of those three sort key components is, by construction, also identical between them. There is
   no field left to sort on; Python's sort is stable, so the "rank" that scheme actually produces is
   silently the input array position again — precisely the instability step 2 exists to remove, not
   fixed but merely hidden behind a sort that looks content-derived and isn't. No sort key can
   distinguish what is, by the definition of "content hash," indistinguishable content.

   The correct handling is to not need distinct identities for indistinguishable content: when
   parsing an envelope, records within the same array that produce an identical `content_hash` for
   the same `outcome_id` are collapsed into a single record before `record_id` assignment — the
   model, functionally, made the same claim twice, and the pipeline treats it as one claim. This is
   recorded as a `warn`-severity `duplicate_record_collapsed` violation (pass, `outcome_id`,
   `content_hash`, and the collapsed count) so it is visible in `violations.json` without blocking
   the pass, and the surviving single record's `record_id` is unique by construction — there is
   exactly one record per `(outcome_id, content_hash)` after collapsing, so no disambiguating suffix
   is ever needed. `record_count` in `status_fragment.json` reflects the post-collapse count, since
   that is what actually exists to cite.

This removes any need to validate `record_id` uniqueness against the model — it is unique by
construction — and removes any incentive for a model to game it, since the model never sees or
emits the field at all.

### Verdict envelope — passes A, D, B

```
{
  "schema_version": "1.0",
  "pass": "A",
  "pack_date": "2026-08-12",
  "candidates_sha256": "...",
  "game_totals_sha256": "...",
  "team_totals_sha256": "...",
  "verdicts": [
    {
      "market_id": "...", "outcome_id": "...",
      "stream": "candidates" | "game_totals" | "team_totals",
      "selection": "...", "line": "...", "price": "...", "book": "...",
      "verdict": "BET" | "PASS" | "STAND_DOWN",
      "confidence": 0.72,
      "recommended_units": 1.0,
      "evidence": [ ... ],
      "contradictions": [ { "claim": "...", "severity": "minor" | "material" } ],
      "kill_triggers": [ { "condition": "...", "observable_before_lock": true } ],
      "rejection_reasons": ["..."]
    }
  ],
  "slate_notes": ["..."],
  "needs": ["..."]
}
```

(`record_id` is omitted from every example in this document because it is assigned after parsing, not
emitted by the model — see above.)

`confidence` is advisory and reported; it never scales a stake. `recommended_units` is validated and
clamped, never trusted. `rejection_reasons` is model-authored, machine-checked, and gives the model a
place to record its own stand-down rationale using the vocabulary in `A.md` §14E, so the audit
section of the report is model prose rather than a bare violation dump.

### Finding envelope — pass C

Matches C's live `FINDING | market_id=… | …` record grammar exactly, expressed as JSON instead of a
pipe-delimited line, with no stake field because C has never had one:

```
{
  "schema_version": "1.0",
  "pass": "C",
  "pack_date": "2026-08-12",
  "candidates_sha256": "...",
  "game_totals_sha256": "...",
  "team_totals_sha256": "...",
  "findings": [
    {
      "market_id": "...", "outcome_id": "...",
      "stream": "candidates" | "game_totals" | "team_totals",
      "selection": "...", "line": "...", "price": "...",
      "verdict": "CONFIRMS" | "CONTRADICTS" | "NEUTRAL",
      "claim": "...",
      "source_name": "...", "source_tier": 1, "source_timestamp": "...",
      "evidence": [ ... ]
    }
  ],
  "no_sourced_findings": false
}
```

A slate legitimately produces multiple findings against the same `outcome_id` — e.g. one lineup-status
finding and one weather finding on the same game total. `record_id` (derived, per above) is what makes
each of those independently citable; nothing in this shape assumes one finding per market.

`no_sourced_findings: true` with an empty `findings` list replaces the current bare
`NO_SOURCED_FINDINGS` sentinel string. The identity, echo-diff, and timestamp-window gates that
today live in `c_research.validate_output` move to `verdict_gate.py` unchanged in behavior and run
against this shape; C gets no `stake_above_row_cap` gate because it has no stake field to violate.

### Reconciliation envelope — pass E

E does not invent new candidates; it may only narrow what A, D, B, and optionally C already
validated. Its envelope references upstream verdicts by identity rather than restating them, and is
otherwise held to the same contract as the other two kinds — the previous revision's example
dropped the totals hashes and every echoed identity field, which was an unintentional omission, not
a documented exception, and would have let E's own tamper gate go unimplemented by anyone
implementing straight from the schema:

```
{
  "schema_version": "1.0",
  "pass": "E",
  "pack_date": "2026-08-12",
  "candidates_sha256": "...",
  "game_totals_sha256": "...",
  "team_totals_sha256": "...",
  "upstream_publication_ids": { "A": "...", "D": "...", "B": "...", "C": "..." },
  "reconciliations": [
    {
      "market_id": "...", "outcome_id": "...",
      "stream": "candidates" | "game_totals" | "team_totals",
      "selection": "...", "line": "...", "price": "...", "book": "...",
      "verdict": "BET" | "PASS" | "STAND_DOWN",
      "recommended_units": 1.0,
      "narrative": "...",
      "cites": [ { "pass": "A", "publication_id": "...", "record_id": "A:mkt_123:<64-hex-sha256>" } ],
      "rejection_reasons": ["..."]
    }
  ],
  "slate_notes": ["..."],
  "needs": ["..."]
}
```

`upstream_publication_ids` — not `upstream_request_hashes` as a prior revision had it — for the same
reason `record_id` moved off array position: `request_sha256` names a *request*, and under a forced
rerun (see [Atomic multi-file publication](#atomic-multi-file-publication)) two different responses
can share one `request_sha256`. E must be checkable against the exact upstream response it was shown,
not merely a response matching the same request, so E's context is built from each upstream pass's
current `publication_id` and that is what gets recorded and checked.

E's identity and tamper gates (`unknown_market`, `line_tampered`, `price_tampered`, etc.) run against
E's own echoed fields exactly as they do for A/D/B — E quoting a line that drifted from the pack is
exactly as much a fabrication as A quoting one. `pack_mismatch` is checked against all three of E's
own hashes, independent of whatever hashes the upstream passes recorded, because E's envelope is
itself a claim about the current pack, not merely a summary of prior claims.

`cites` references upstream records by `(publication_id, record_id)` together, not by `record_id`
alone and not by `(pass, outcome_id)` — an E record citing C must name both which of potentially
several C findings on that `outcome_id` it is drawing from, and which published C response that
finding came from, since a forced C rerun between when E was prompted and when E's envelope is
validated could otherwise leave `record_id` ambiguous across two different C publications. A `cites`
entry naming a `publication_id` that is not the pass's *current* published version at validation
time is `stale_citation`; a `record_id` that does not exist within that publication is
`unsourced_synthesis`. Every `outcome_id` in `reconciliations` must be covered by at least one
`cites` entry pointing at a validated **verdict**-envelope record — A, D, or B — with a matching
`outcome_id` (else `unsourced_synthesis`; a `cites` entry pointing only at a C finding does not by
itself source a BET, since C never proposes one — see
[Per-pass input contracts](#per-pass-input-contracts)). `recommended_units` may not exceed the minimum
of the cited upstream validated stakes for that `outcome_id` (else `stake_above_row_cap`). E has no
independent `evidence` list for pack-truth claims — its inputs are already-validated upstream
evidence, consumed via `cites`, not re-asserted.

### The shared `evidence` item

```
{ "claim": "...", "kind": "pack" | "external",
  "subject_type": "player" | "team" | "event" | "market" | "environment",
  "player_id": "...", "team": "...",
  "market_id": "...", "outcome_id": "...",
  "source": "...", "tier": 1, "timestamp": "..." }
```

- **`subject_type` is required, and `subject_type: "player"` requires `player_id`.** See the player
  gate below — this is what makes an invented name unrenderable rather than merely flagged.
- **`outcome_id` is the join key** on every identity reference, per
  [Canonical verdict identity](#canonical-verdict-identity).
- **`stream` on the parent record disambiguates the board** without probing three indexes, and makes
  a candidates-row verdict claiming to be a totals row an explicit violation.
- **`selection`, `line`, `price`, and `book` are echoed on every record kind.** The tamper gate works
  by requiring the model to restate what it thinks it is referencing, then comparing to the pack. A
  model that never quotes a line can never be caught changing one.

`evidence`, `contradictions`, and `kill_triggers` are the model's product and are never used to
compute a number.

## Two invariants, not one

A prior revision of this document claimed "an invented name has no path to output" as a single,
unqualified invariant. That was true for the structured player-evidence channel and false for
everything else the schema lets a model write, because §14.B's rationale prose, §14.C's `claim`
strings, E's `narrative`, and top-level `slate_notes`/`needs` are all rendered as free text with no
entity binding at all — a model could write "Smith is ruled out" as prose in a rationale field
without ever populating a `subject_type: "player"` evidence item, and that sentence reaches the
report untouched. This section replaces the single overstated claim with the two real ones, so the
guarantee actually shipped matches the guarantee described.

**Invariant A (holds, enforced, tested): no fact that determines the report's numbers or the
report's structured identity fields can be fabricated.** Every number in §14.B's table — market ID,
selection, line, price, book, edge, units — and every rendered player name in that table is read
from `index`, never from the envelope's free-text fields. This is what the player, tamper, and
identity gates enforce, and it is a real guarantee: there is no code path from `claim` or `narrative`
text to a table cell.

**Invariant B (does not hold, is not attempted, is now stated as a boundary): free-form rationale
prose is not fact-checked against the pack.** `claim`, `contradictions[].claim`, `narrative`,
`slate_notes`, and `needs` are rendered verbatim beneath the gate-checked table, labeled as the
model's commentary. A model can still write a sentence naming a player who isn't in the pack, or
attributing an injury the pack doesn't support, inside that commentary. Full claim-level fact
verification of arbitrary natural-language text is a different, much larger problem than validating
a typed envelope against an index, and is explicitly out of scope for this deterministic validator.
Revision 3's proposal to `warn`-flag capitalized name bigrams not in the roster was correctly
rejected in the second review as a bypassable heuristic masquerading as enforcement; removing it
rather than keeping a false sense of coverage is the right call.

One cheap, real mitigation is kept, at `warn` severity, documented here as exactly what it is — a
best-effort signal for a human reviewer during the `shadow`-mode telemetry period, not a claim of
completeness: `unbound_name_in_prose` fires when `claim` or `narrative` text contains a
capitalized-name pattern that matches neither `index.players` nor any `player_id`-bound evidence item
already present on the same record. It catches careless fabrication (a name invented from nothing)
without claiming to catch adversarial fabrication (a name chosen to evade the pattern), and it never
blocks a BET — only `unknown_player` and `unbound_player_claim`, both structured-channel violations,
do that.

## Per-pass input contracts

The envelope requires `candidates_sha256`, `market_id`, and `outcome_id`. Two passes cannot satisfy
that from what they are currently given, so their inputs change as part of this work.

| Pass | Provider | Envelope kind | Inputs today | Inputs required |
| --- | --- | --- | --- | --- |
| A | OpenAI Responses | verdict | candidates ledger + both totals boards | unchanged |
| B | Gemini, `google_search` grounded | verdict | `briefing.md` + both totals boards only (`gemini_research.py:85`) | **add** the filtered candidates ledger and `candidates_sha256`, via the same `rc.validate_candidates` call A and C already use |
| C | Gemini, `google_search` grounded (`c_research.py:46` delegates to B's call path) | finding | briefing + candidates ledger + both totals boards | unchanged inputs; existing `validate_output` is replaced by the shared gate emitting finding-envelope violations |
| D | Anthropic | verdict | candidates ledger + totals | unchanged |
| E | Anthropic | reconciliation | `briefing.md` + the **Markdown** of A, B, D, optional C (`claude_synthesis.py:32`) | **replace** the A/B/D Markdown inputs with their validated verdict envelopes, and C's with its validated finding envelope when present; keep `briefing.md` for narrative context only — no number or identity may be sourced from it |

B's change enlarges its prompt by the candidates ledger, which is the same payload A and C already
send, and changes its `request_sha256` (it gains `candidates_hash`), forcing one re-run. That cost is
unavoidable: a pass that never sees `outcome_id` cannot emit a verdict the gate can check, and B's
output currently feeds E.

E's change is the more consequential one and is the point of the whole design. Synthesizing from
prose lets an upstream fabrication survive into the final report even when the upstream pass was
itself validated, because the prose is not what was validated. Consuming envelopes means E's inputs
are already gate-clean, and E's own reconciliations are then checked against the same index. E may
only narrow: a reconciliation on an `outcome_id` no cited upstream pass proposed is
`unsourced_synthesis`, and a stake above the minimum of the upstream validated stakes for that
`outcome_id` is `stake_above_row_cap`. E's consumption of C is evidence, not identity: a validated
`CONFIRMS`/`CONTRADICTS` finding from C is admissible support for a reconciliation's narrative and
for the injury/lineup gate below, but C's `findings` are never a `cites` source for
`unsourced_synthesis` on their own, because C never asserts a stake or a BET — only A, D, and B do.

## Authoritative index

`pack_index.py` builds, from the pack directory:

- `rows` — `outcome_id -> row`, unioned across `candidates.csv` (after the same `drop_locked_events`
  filter `runner_common.validate_candidates` applies), `game_totals.csv`, and `team_totals.csv` via
  `rc.parse_game_totals` / `rc.parse_team_totals`, each tagged with its `stream`. Locked rows are
  kept in a separate `dropped` map so a locked-market recommendation gets a specific violation code
  rather than `unknown_market`. A duplicate non-empty `outcome_id` across streams is a pack-integrity
  error raised at index build, not a silent overwrite.

  **Empty `outcome_id` is expected, not exceptional, and is excluded from the index rather than
  indexed.** `game_totals._empty_row` (`game_totals.py:969-980`, the `INSUFFICIENT_DATA` /
  thin-ladder path) sets `totals_id: market_id` but never populates `outcome_id`, so it is written to
  the CSV as `""`. Multiple such rows are routine on a real slate — every totals market with an
  unbracketed ladder produces one. Indexing on `outcome_id` unconditionally, as revision 3 specified,
  means the second `""` row raises the "duplicate `outcome_id`" integrity error above and takes the
  whole index build down; that is a crash on ordinary pack contents, not a malformed-pack signal.
  `pack_index.py` therefore skips any row whose `outcome_id` is empty when building `rows`, and keeps
  those rows in a separate `unindexed_totals` list, keyed by `totals_id`, used only to render Section
  F's coverage notes. A verdict or finding referencing one of these rows by `outcome_id=""` is
  `unknown_market` like any other unmatched reference — there is nothing to bind to, by construction,
  since the row itself carries no market a model could legitimately quote a price against.
- `players` — `player_id -> {name, team, event_id}` from every non-empty `player_id`, used both to
  reject invented players and to render player names locally.
- `injuries` — per-event `injury_flags`, the only pack-internal support for an injury claim.
- `locks` — `_event_starts_at` per `event_id`, re-evaluated against the validation-time clock.
- `policy` — `portfolio.load_portfolio_policy()` for `max_wager_units`, `max_daily_units`,
  `max_event_units`, `max_player_units`, `max_team_units`, `max_market_type_units`,
  `max_correlated_cluster_units`, `max_book_units`, and `stake_increment`.

The index carries all three hashes used to build it: `candidates_sha256`, `game_totals_sha256`, and
`team_totals_sha256` (empty-file hashes per `runner_common.empty_game_totals_hash` /
`empty_team_totals_hash` when a board is absent). Pinning `candidates_sha256` alone, as revision 3
did, lets the totals boards change under an envelope whose candidates hash still matches — the gate
would then validate totals verdicts against a pack that no longer matches what the model saw. Every
envelope kind therefore carries and is checked against the full triple; a mismatch on any one of the
three is `pack_mismatch` — the envelope was produced against a different pack, in whole or in the
part it actually references.

## The deterministic gates

Every gate produces a violation record `{code, outcome_id, market_id, detail, severity}`. `severity`
is `reject` (the verdict cannot be a BET) or `warn` (recorded, reported, not fatal).

| Requested rejection | Code | Enforcement |
| --- | --- | --- |
| Unknown market IDs | `unknown_market`, `market_id_mismatch`, `stream_mismatch` | `outcome_id` absent from the index; or present with a `market_id` matching neither the CSV `market_id` nor the totals compatibility alias; or present in a different `stream` than claimed. |
| Changed lines or prices | `line_tampered`, `price_tampered`, `selection_tampered`, `book_tampered` | Numeric-normalized comparison of the echoed values against the indexed row. `priced_line` mismatches and `ev_line_fallback:priced_at=…` flags require the verdict to reference `priced_line`, else `priced_line_unreconciled` (A.md §5.6). |
| Players not in the pack | `unknown_player`, `unbound_player_claim`, `unbound_name_in_prose` (warn) | Any `player_id` absent from the roster is `unknown_player`. Any evidence item with `subject_type: "player"` and no `player_id` is `unbound_player_claim`. Both are `reject`. This binds the *structured* channel only — see [Two invariants, not one](#two-invariants-not-one) for what it does and does not cover in rendered prose. |
| Unsupported injury claims | `unsupported_injury_claim` | An evidence item asserting availability, injury, scratch, or lineup status must bind to a `player_id` **and** satisfy one of: (a) cite a row with non-empty `injury_flags` — the pack-only support available to A and D; (b) carry `source`, `tier`, and a `timestamp` inside the freshness window — the grounded support B and C produce directly; or (c) for E only, cite (`cites`) a specific B or C record that itself satisfies (a) or (b) — E is a *consumer* of upstream-validated evidence, not a third pack-only or grounded pass, so an E injury claim that only points at `injury_flags` while ignoring a validated B/C finding is not the intended failure mode this gate exists to catch. A bare assertion, or an E claim that cites nothing, fails. |
| Stakes above the pipeline cap | `stake_above_row_cap`, `stake_above_policy_cap`, `stake_off_increment`, `negative_stake` | Per row: `recommended_units <= min(recommended_units_pre_news, max_units, policy.max_wager_units)`, `>= 0`, and on the `stake_increment` grid. Across the envelope: the accepted BET set runs through `portfolio.allocate_portfolio_risk` and any group breach (daily, event, player, team, market type, correlated cluster, book) is reported with the binding constraint named. |
| Recommendations on locked markets | `locked_market` | `outcome_id` in the drop set from `drop_locked_events` at validation time, or `_event_starts_at` unparseable. Re-checking the clock catches a market that locked between pack build and desk run. |
| Recommendations with integrity flags | `integrity_flag` | Row carries any member of `pack.DISQUALIFYING_DQ_FLAGS` or a flag prefixed `pack.CROSS_SPORT_DQ_PREFIX`, or `actionable != "true"`, or `board == "A_FLAGGED"`. |
| High-variance and prohibited markets | `high_variance_market`, `prohibited_market`, `longshot_price`, `side_restricted` | `verdict_policy.prohibited_variance_markets` (3PM, hits allowed, turnovers — the corrected `A.md` §5.2 set); `verdict_policy.desk_prohibited_markets`, a scoped list (HR, walks allowed, HRR at any scope; BB scoped to `market_type == PLAYER_PROP` only) which is a *recommendation* ban, not a pack-entry ban — team BB is unaffected; `normalizer.ALLOWED_MLB_PLAYER_PROPS` / `ALLOWED_MLB_TEAM_PROPS` as a positive whitelist for MLB props, applied after the desk ban so a whitelisted-but-banned market still rejects; 2B OVER rejected; American price at or beyond `+pack.LONGSHOT_AMERICAN_PRICE` rejected. |

Envelope-level gates run first and short-circuit the rest: `envelope_unparseable`, `schema_invalid`,
`pack_mismatch` (any of the three pack hashes disagrees), and for pass E `unsourced_synthesis` and
`stale_citation` (a `cites` entry names a `publication_id` that is not the referenced pass's current
one).

A verdict whose `verdict` is `PASS` or `STAND_DOWN` is exempt from the stake and variance gates but
still subject to identity, tamper, and player gates — a stand-down that quotes a fabricated line is
still a fabrication, and `A.md` §14E requires rejected candidates to be quoted exactly.

## Provider integration

`runner_common.py` gains `request_structured(...)`, which each runner calls before its Markdown
stage, plus `parse_envelope(...)` and `write_envelope(...)`.

Provider assignment, taken from `run_desk.PHASE_RUNNERS` and `PHASE_KEYS` rather than assumed:

| Pass | Module | SDK | Structured-output mechanism |
| --- | --- | --- | --- |
| A | `reasoning.py` | `openai` 2.44.0 | `client.responses.create(text={"format": {"type": "json_schema", "name": "outlier_verdicts", "schema": …, "strict": True}})` — `text` is confirmed present on `Responses.create`. |
| B | `gemini_research.py` | `google-genai` 2.10.0 | `types.GenerateContentConfig(response_mime_type="application/json", response_schema=…)`; `response_json_schema` also exists on this version and is preferred if it accepts the emitted schema unchanged. |
| C | `c_research.py` (delegates to B's `call_gemini`) | `google-genai` 2.10.0 | same as B |
| D | `claude_reasoning.py` | `anthropic` 0.112.0 | forced tool: `tools=[{"name": "emit_verdicts", "input_schema": …}]`, `tool_choice={"type": "tool", "name": "emit_verdicts"}` — both confirmed present on `Messages.create`. |
| E | `claude_synthesis.py` | `anthropic` 0.112.0 | same as D; envelope read from the `tool_use` block's `input` rather than concatenated text blocks |

Two constraints shape this:

- **Grounded Gemini passes cannot be assumed to accept a response schema.** B and C both attach
  `types.Tool(google_search=…)`, and combining Search grounding with a constrained response schema
  has been restricted on Gemini. Passes B and C therefore treat native structured output as
  best-effort: the runner attempts it, and on any config rejection falls back to prompt-instructed
  JSON plus the strict parser. This is not a regression — it is exactly how C succeeds today with its
  `FINDING |` record grammar. Whether grounded structured output is accepted must be settled by a
  single recorded probe against the installed SDK before B/C wiring is written, not assumed from
  documentation.
- **Native structured output is an optimization, not the contract.** Every path runs the same
  defensive extractor: locate the outermost JSON object, parse, validate. A provider that regresses
  gets caught by the parser rather than silently degrading to prose.

### Recorded probe (step 7) — 2026-08-14

Offline construction probe against the installed `google-genai` **2.10.0**
(`outlier_scrapers/gemini_structured.py`). No `generate_content` call was made
and no `GEMINI_API_KEY` was used.

| Check | Result |
| --- | --- |
| `GenerateContentConfig` fields | `response_mime_type`, `response_schema`, `response_json_schema` present |
| Construct `google_search` + `response_json_schema` | **OK** (SDK accepted the object) |
| Construct `google_search` + `response_schema` | **OK** (SDK accepted the object) |
| Live grounded generate with a schema | **not invoked** |

Construction success is not live-API acceptance. B and C therefore keep the
plan's best-effort rule: attempt a grounded structured config, and on any
config/API rejection fall back to prompt-instructed JSON plus
`verdicts.parse_envelope`. C's step-6 fallback parser already covers the
unavailable case. `call_gemini(..., schema=)` implements that retry.

Cache invalidation: `verdicts.SCHEMA_VERSION` and the serialized schema hash join the `request_data`
dict in every runner's `compute_request_hash` call, so any schema change invalidates existing
`request_sha256` values and forces a re-run rather than reusing an envelope shaped to the old
contract. B additionally gains `candidates_hash`, and E's `input_hashes` switch from Markdown text
to envelope bytes.

## Modes, and what shadow mode may never do

`mode` comes from `config/verdict_policy.json`, mirroring `PortfolioPolicy.mode`.

**`enforce`** — verdicts carrying a `reject` violation are demoted to `STAND_DOWN` with
`rejection_reasons` replaced by the violation codes. Only surviving verdicts reach the authoritative
report, `decisions.csv`, or any stake.

**`shadow`** — nothing is demoted, and *nothing is authoritative*. Concretely:

- Shadow-mode envelopes and violations are written only to `verdicts/shadow/` inside the pack and to
  the `verdicts` block of `reasoning_status.json`.
- `verdict_report.py` refuses to render an authoritative report from a shadow-mode envelope; it
  raises rather than degrading, so there is no code path where an unvalidated BET reaches a report.
- No shadow verdict is written to `decisions.csv` or the verdict child table, and no shadow stake is
  ever emitted as executable.
- While in shadow, the existing Markdown pipeline continues to produce the report exactly as it does
  today. Shadow mode adds telemetry beside the current behavior; it does not partially replace it.

This is the resolution of an inconsistency in revision 1, which claimed both that only surviving
verdicts reach Markdown and that shadow mode ships without demotion. Those are compatible only if
shadow output is not the report — which is now stated as an invariant with a test.

Promotion from `shadow` to `enforce` is a separate, deliberate change gated on the grounded-Gemini
probe being recorded and a review of at least one full slate of violation telemetry.

**Implementation status (2026-08-25).** The mode semantics above are *not*
implemented. `policy.mode` was loaded and validated, then never read: there is no
demotion path, no `verdicts/shadow/` subtree, and `verdict_report.py` has no
shadow refusal. A `reject` violation today fails the whole pass via `RunnerError`,
which is neither the `enforce` behaviour described above (demote the record, keep
the pass) nor the `shadow` behaviour (telemetry beside the existing report).

The enforcement pass wired `mode` for its own new checks only —
`ENFORCEMENT_CODES` in `verdict_gate.py` are `warn` under `shadow` and `reject`
under `enforce`, via `enforcement_severity()`. That gives those checks a real
observation window without touching any gate that already bites: no existing
check was weakened, and `shadow` still fails a pass on a tampered line.

Still outstanding for full mode support: record-level demotion to `STAND_DOWN`
under `enforce`, the separate `verdicts/shadow/` publication tree, the
`verdict_report.py` refusal, and keeping shadow rows out of `decisions.csv`.

## Repair loop and failure policy

When validation returns violations, the runner retries up to `repair_attempts` (default 1) with the
original request plus a machine-generated repair block listing each violation code, the offending
`outcome_id`, and the authoritative pack value. This is the only feedback channel; the retry never
restates a number the model is expected to produce.

After the final attempt, in `enforce` mode, every record is classified along two independent axes:
what the model *intended* (its own `verdict` field before any gate ran), and *why* a violation fired,
if one did. The previous revision conflated these — it defined a "judgement class" that included
records the model voluntarily marked `STAND_DOWN`, and then said that class "never fails the pass"
in the same breath as saying it "counts toward `reject_fail_ratio`," while also saying exceeding
that ratio fails the pass. Those three statements cannot all be true — a slate where every candidate
was correctly stood down (a legitimate, cautious, zero-bet slate) would be 100% of the ratio's
numerator and would fail on a threshold meant to catch malfunction, not caution. This revision
separates the two axes so that voluntary caution never enters the ratio at all.

**`reject_fail_ratio` is computed only over the model's own attempted `BET`s** — records whose
`verdict` field, as emitted by the model before any gate ran, was `BET`. A record the model itself
marked `PASS` or `STAND_DOWN` is never in the numerator or the denominator of this ratio, at any
count, because it was never an attempt the gate needed to correct — it is the model doing exactly
what a cautious desk should do. The ratio's numerator is the subset of those attempted `BET`s that
ended up rejected by any `reject`-severity gate (stake-cap, integrity-flag, locked-market,
prohibited-market, or identity/tamper); the denominator is the count of attempted `BET`s. A slate
with zero attempted `BET`s has an undefined ratio and is trivially not a failure by this rule — there
is nothing to have malfunctioned on.

Independently of that ratio, violations are still split by *why*, and one class fails the pass
outright regardless of the ratio or of whether the record was an attempted `BET`:

- **Identity and tamper violations fail the pass outright, on any record, in any quantity.**
  `unknown_market`, `market_id_mismatch`, `stream_mismatch`, `line_tampered`, `price_tampered`,
  `selection_tampered`, `book_tampered`, `unknown_player`, `unbound_player_claim`, and
  `priced_line_unreconciled` are not "the model made a call the pack disagreed with" — they are "the
  model asserted something the pack does not contain," and that is true whether the record was a
  `BET`, a `PASS`, or a `STAND_DOWN`: `A.md` §14E requires even a rejected candidate to be quoted
  exactly, so a `STAND_DOWN` that fabricates a line is still a fabrication. A single one of these
  anywhere in the envelope fails the pass (exit 1) after repair is exhausted. This is what closes the
  gap where a model could emit one clean voluntary `PASS` alongside a pile of fabricated `BET`s, have
  the fabrications demoted, and exit 0 regardless of any ratio.
- **Every other `reject`-severity gate is judgement class** and contributes only to
  `reject_fail_ratio`'s numerator, and only when it fires on a record the model itself marked `BET`.
  The same violation firing on a model-marked `STAND_DOWN` record (e.g. a stand-down that happens to
  also reference a locked market) is recorded and reported but affects neither axis — the model
  already reached the conservative conclusion; the gate confirming an additional reason for it is not
  a new failure to count.
- The pass also fails outright if the envelope never parsed, or if `pack_mismatch` or
  `schema_invalid` fired.
- The pass fails if `reject_fail_ratio` (default 0.5) is exceeded on attempted `BET`s as defined
  above — a model whose *attempted-and-rejected* rate is that high is malfunctioning at proposing
  bets, which is a different and narrower claim than "this model stood a lot of things down."

Both axes are enforced in code, not by anything the model could influence: which class a violation
belongs to is fixed by which gate in [The deterministic gates](#the-deterministic-gates) produced it
(identity/tamper gates are hard-coded to the pass-failing class; every other `reject`-severity gate
is judgement class), and whether a record counts toward `reject_fail_ratio` at all is fixed by the
model's own `verdict` field as received, read before the gate runs and never altered by it.

## Atomic multi-file publication

Each pass now produces four artifacts (envelope, violations, Markdown, status contribution) where it
previously produced one. `run_desk.run_phase` currently snapshots and restores only the phase's
Markdown file (`run_desk.py:83`), so a mid-write failure would leave an envelope from run N beside
Markdown from run N−1 — the exact desynchronization this plan exists to prevent.

The previous revision described writing artifacts to a staging directory and then "moving each file
into place, with the manifest written last." That is not atomic: moving several final files and only
then writing the manifest still has a window, after the first file move and before the manifest
write, where a crash leaves the *old* manifest pointing at a directory that is a mix of new and old
files — the manifest's presence does not mean the files it names are the ones actually on disk,
because nothing stopped individual file moves from happening first.

The corrected design publishes an entire immutable version at once and swaps only a pointer. It uses
two distinct identifiers where the previous revision used one, because they answer different
questions and conflating them breaks `--force`:

- **`request_sha256`** answers "is this the same request." It is a hash of the inputs (prompt,
  role block, candidate/totals hashes, schema version) and is what `run_phase`'s existing hash-match
  check uses to decide whether to call the provider at all.
- **`publication_id`** answers "is this the same *published artifact set*." It is defined below as
  the hash of a manifest covering every file that will live in the versioned directory, not just the
  envelope and violations.

These must be different fields because `reasoning.py:111` and every other runner's `force=True` path
unconditionally deletes the existing output and re-calls the provider, independent of whether
`request_sha256` would still match (`run_desk.py:97-100` passes `force` straight through). Models
are not deterministic: a forced rerun with an unchanged `request_sha256` can and does return a
different envelope. Revision 5 keyed the versioned directory on `request_sha256` alone and treated
an existing directory as an immutable cache hit unconditionally — under that design, a forced rerun's
new response either collides with the existing directory (if publish tries to write into an
"immutable" path that already exists) or is silently discarded (if publish sees the directory exists
and treats that as "already done," skipping the write entirely). Keying the directory on
`publication_id` instead removes the collision: two different responses to the same request hash are
two different `publication_id`s and get two different directories, while an unforced rerun that
genuinely reproduces the same response (a real cache hit, not just a matching request) lands on the
same `publication_id` and is a safe no-op write.

- Each pass publish assembles its complete artifact set in memory first — envelope, violations,
  Markdown, and now (see below) status, all four together — before anything is named or written.
  Nothing under the eventual path is ever modified after the directory is fully written; writing to
  a `publication_id` that already exists is idempotent because, by construction of the ID (below),
  the directory's content is provably identical to what would be written again.
- The directory is built under a temporary name in the same parent
  (`...<publication_id>.tmp-<pid>`) and made visible with a single directory rename (`os.replace`,
  which is atomic on both POSIX and Windows for same-volume renames including directories) once
  every file inside it is written and fsynced. Before this rename, nothing in the versioned tree is
  visible to readers at all — there is no partial-write window because there is no in-place file to
  be partial.
- A single small pointer file, `packs/<date>/verdicts/<pass>/current.json`, holds
  `{"request_sha256": "...", "publication_id": "...", "published_at": "..."}` and is the only thing
  ever updated in place — via write-to-temp-then-`os.replace`, the one truly atomic primitive this
  design depends on. Publishing a new version is: write the versioned directory (rename-visible),
  then repoint `current.json`. A crash before the pointer update leaves `current.json` referencing
  the previous, still-fully-intact version; a crash after leaves it referencing the new,
  already fully-intact version. There is no state in which the pointer names a directory that is
  incomplete. Carrying both fields on the pointer is what lets a reader distinguish "this pack's
  request hasn't changed" from "this exact response was published" — `run_phase`'s existing
  `request_sha256`-based skip/cache logic reads only that field and is otherwise unaffected by this
  change; `publication_id` is new information for the artifact layer, not a replacement for the
  existing request-level cache check.
- Readers resolve a *pass-level* current version by reading `current.json` and then reading the
  directory named by its `publication_id`; they never read a `.tmp-*` directory or infer currency
  from file mtimes. Whether any reader is actually allowed to resolve a bare pass-level `current.json`
  directly, versus always going through the desk-level snapshot below, is addressed in the next
  section — the short answer is: only the publish path itself does.
- `_restore_on_failure` and `run_desk.run_phase`'s existing rollback are superseded by this scheme
  for the pass artifacts covered here: there is nothing to restore, because a failed publish attempt
  simply never repoints `current.json`, leaving the previous version live throughout. The old
  single-file snapshot/restore logic in `run_desk.py:76-80` is removed for these artifacts once this
  lands; it is retained only for artifact kinds this plan does not touch, if any remain.

This is the reviewer's exact recommendation from an earlier round — immutable versioned directories,
atomic pointer swap — and it is a simplification relative to the manifest-plus-move design it
replaced, not an addition: it has fewer moving parts because it removes the need to reason about
partial directory contents at all. Two gaps in this per-pass mechanism, found by a later review, are
fixed in the two subsections that follow: the artifact set the ID actually covers, and the fact that
five independent per-pass pointers do not add up to one coherent desk-level result.

### The per-pass immutable manifest, and what `publication_id` actually covers

A prior draft of this design defined `publication_id` as a hash of "the parsed envelope bytes plus
the violation-report bytes." That undercounts the directory's actual content in two ways that matter:
the directory also contains this pass's rendered Markdown fragment and (per the status fix below) a
status fragment, and neither was covered by the hash. Two concrete failures follow from that gap:

- **A renderer change can silently corrupt the idempotency guarantee.** If `verdict_report.py`'s
  per-pass Markdown rendering is fixed or changed, and the pass is republished with an unchanged
  envelope and violations, the old design computes the same `publication_id` as before — because the
  ID never looked at the Markdown — and then treats the write as a no-op per "writing to a
  `publication_id` that already exists is idempotent." That is false under a renderer change: the
  directory's Markdown *should* differ, and the design as drafted would either skip writing the
  corrected Markdown (stale content survives under an ID that claims completeness) or, if the publish
  path does write anyway, silently violate its own stated immutability invariant by mutating a
  directory whose name promises it never changes.
- **Two different requests that happen to produce byte-identical envelope+violations content would
  collide onto one `publication_id`** despite representing two distinct events worth distinguishing
  for audit purposes, since the ID carried no information tying it to which request produced it
  beyond what happened to be embedded in the envelope body itself.

The fix is to make `publication_id` the hash of a manifest that names every file that will exist in
the directory, by its own content hash, plus the metadata that actually distinguishes one publish
attempt from another:

```
manifest = {
  "pass": "A",
  "request_sha256": "...",
  "schema_version": "...",
  "files": {
    "verdicts.json": sha256(verdicts_json_bytes),
    "violations.json": sha256(violations_json_bytes),
    "report_fragment.md": sha256(report_fragment_bytes),
    "status_fragment.json": sha256(status_fragment_bytes)
  }
}
publication_id = sha256(canonical_json(manifest))
```

This is computed entirely in memory, before any file touches disk — `publication_id` names the
directory, so it cannot depend on anything already having been written under that name. The manifest
itself is then written into the directory as `manifest.json`, alongside the four files it describes,
purely as a self-check: a reader (or a test) can recompute every file's hash, recompute the manifest
hash, and confirm it equals the directory's own name. `manifest.json` is not part of what it itself
hashes — there is no self-reference — it is simply one more artifact in an already-fully-determined
set. With this definition, a renderer change necessarily changes `report_fragment.md`'s hash, which
necessarily changes the manifest, which necessarily changes `publication_id`: there is no code path
left where "content changed" and "ID unchanged" can both be true.

### The desk-level snapshot

Per-pass `current.json` pointers solve atomicity *within* one pass. They do not solve coherence
*across* passes, and a prior draft of this design implicitly assumed they did by having E pin
`upstream_publication_ids` at the moment it validated, while every reader — `run_desk`,
`verdict_report.py`, `daily_job.py` — was specified to "always resolve the current version" of each
pass independently. Those two things drift apart the moment any pass reruns after E has already
published: A's `current.json` now points at a new `publication_id`, but E's own envelope — and the
report already rendered from it — still references A's *old* one. A reader resolving "current A" and
"current E" independently gets a self-inconsistent pairing: E's report claims to reconcile a
specific A verdict that is no longer the one "current" actually names.

The fix is a fifth pointer, one level up, naming a *coherent set* rather than five independent
`current`s: `packs/<date>/verdicts/desk_snapshot.json`, published atomically (write-to-temp-then-
`os.replace`, same primitive as every other pointer in this design):

```
{
  "pack_fingerprint": {
    "candidates_sha256": "...", "game_totals_sha256": "...", "team_totals_sha256": "..."
  },
  "publications": { "A": "...", "D": "...", "B": "...", "C": "..." | null, "E": "..." | null },
  "synthesis_source": "claude_e" | "fallback_no_e",
  "published_at": "..."
}
```

**This publish is gated on a purpose-built readiness check, not on reusing `e_usable`/
`required_usable`, and not on "whichever phases were requested."** `run_desk.orchestrate_desk`'s
`steps` parameter accepts an arbitrary subset of `{A, B, C, D, E}` (`run_desk.py:269`), and two
prior drafts of this section got the gate wrong in two different ways:

- The first draft said the desk-level publish runs "after whichever phases were requested have
  finished" — which would let `orchestrate_desk(steps=["A"])`, a single-pass rerun, publish a new
  `desk_snapshot.json` covering a slate where D, B, and E were never touched by this run at all.
- The second draft's fix reused `run_desk.orchestrate_desk`'s existing `e_usable and
  required_usable` — but that predicate, for any phase *not* in the current invocation's `requested`
  set, falls back to bare flat-file existence (`(pack_dir / PHASE_OUTPUTS[phase]).exists()`,
  `run_desk.py:349-361`). That is deliberately stale-tolerant, because it exists to answer "does this
  pack have *a* B output, from whenever" for the old file-existence status concept — and it is exactly
  wrong for this purpose. After any prior full run left `gemini_b.md` and `claude_d.md` on disk,
  `orchestrate_desk(steps=["A"])` would still see `required_usable = True` from those old files and
  advance `desk_snapshot.json`, even though this invocation only touched A. Reusing the predicate
  reintroduced the exact bug it was meant to fix, one layer down. The same predicate also has no
  representation of `fallback_no_e` — `e_usable` means "E succeeded, or an E file already exists,"
  full stop; there is no branch for "E didn't run, but the deterministic fallback did."

`desk_snapshot.json` instead advances on a new predicate, purpose-built for this and not shared with
`orchestrate_desk`'s status reporting: **`_desk_publication_ready(pack_dir)`**, computed fresh on
every invocation regardless of `steps`:

1. Compute the pack's *current* fingerprint — `candidates_sha256`, `game_totals_sha256`,
   `team_totals_sha256` — by reading the live CSVs on disk right now, not by trusting any cached
   value.
2. For each of A, B, D: read that pass's `current.json`, then read the `manifest.json` /
   `verdicts.json` it names, and require both that the publication is validated (not a rejected-pass
   artifact) *and* that its own recorded `candidates_sha256`/`game_totals_sha256`/`team_totals_sha256`
   equal the fingerprint from step 1 exactly. A publication from a stale pack revision fails this
   check even if it is the newest thing ever published for that pass — freshness here means "matches
   the pack as it exists right now," not "is the most recent file on disk."
3. If all three pass, check E — but E's freshness test is **two** conditions, not one, and the pack
   fingerprint alone is the weaker of them:
   - E's own recorded pack hashes must equal the step-1 fingerprint, exactly as for A/B/D; **and**
   - E's `upstream_publication_ids` must equal, entry for entry, the publication IDs step 2 just
     selected for A, B, D (and C, when C is included in `publications` — an E that cited a C
     publication other than the selected one is as stale as one citing an old A).

   The second condition is the one that matters, and omitting it — as a prior draft did — makes the
   whole predicate self-contradictory. A pack-fingerprint-only check accepts an E whose upstream pins
   are stale, because rerunning A alone does not change any pack CSV: the fingerprint is identical
   before and after, so old E stays "fresh" by that test while still pinning old A. The snapshot
   would then publish `publications["A"]` = the *new* A (step 2's selection) alongside
   `synthesis_source: "claude_e"` and an E that never saw it — exactly the incoherent pairing this
   entire section exists to make impossible, reintroduced by the check meant to prevent it. Requiring
   upstream equality closes it: an A-only rerun leaves E's pins pointing at the superseded A, E fails
   this condition, and the fallback takes over.

   If E passes both, use it (`synthesis_source: "claude_e"`). If E fails either — never ran this
   cycle, stale pack hashes, or stale upstream pins — compute the no-E fallback
   ([The no-E fallback reconciliation](#the-no-e-fallback-reconciliation)) inline, right now, from
   A/B/D's current (already fingerprint-verified) publications. The fallback is not something that has
   to already exist as a stored artifact to "count" — it is deterministic code that always produces a
   result once A/B/D are ready, so its availability is not a gate at all; only A/B/D's freshness is.
4. `_desk_publication_ready` returns ready only if step 2 passed for all of A, B, D. C is included in
   `publications` when a fresh, validated C exists, and is `null` otherwise — it was never part of
   `required_usable` and remains optional here for the same reason (`run_desk.py:354-361` only checks
   A, B, D).

A run over any subset for which step 2 fails — including a single-pass rerun that leaves B or D
without a fresh, fingerprint-matching publication — updates only the reran pass's own `current.json`
and stops there; `desk_snapshot.json`, `desk_snapshot_history.jsonl`, and the legacy projections
(see below) are untouched.

A single-pass rerun for which step 2 *does* pass — rerunning A alone, immediately after a full cycle,
while B and D remain fingerprint-matching — is allowed to advance the snapshot, but step 3 governs
what it advances *to*. The existing E pinned the superseded A, so it fails step 3's upstream-equality
condition, and the snapshot advances with A's new publication, B/D's unchanged ones, `"E": null`, and
`synthesis_source: "fallback_no_e"`. That is the correct outcome, not a degradation: the desk's
reconciliation genuinely no longer covers the A that is now current, and publishing a deterministic,
conservative fallback is honest about that, where publishing the old E under a new A would not be. To
get `synthesis_source: "claude_e"` back, E has to actually run against the new A — which is exactly
what a full cycle does. The fix is about freshness against both the live pack *and* the selected
upstream set, not a blanket ban on subset runs ever publishing.

**Construction of `publications` is explicit, not "copied verbatim from E," because E's own pin
does not include E.** E's `upstream_publication_ids` field (see
[Reconciliation envelope — pass E](#reconciliation-envelope-pass-e)) is, by definition, E's record
of what it read from A/D/B/C — it cannot contain E's own ID, since E did not exist yet at the moment
it computed that map. Concretely:

- **When E ran and validated:** `publications` = E's own `upstream_publication_ids` (giving `A`,
  `D`, `B`, and `C` if E cited it) with `"E"` set to E's own `publication_id`, from its own
  already-completed per-pass publish. Two sources, not one — E's pin for the upstream four, and
  `run_desk`'s own knowledge of what it just published for E — combined by the code assembling this
  object, not "copied" from a single field that was never going to have all five values.
- **When the no-E fallback ran instead:** `publications` = the fallback module's own record of which
  `publication_id` it read from each of A/D/B (and C, if any reconciliation cited it) at the moment
  it computed the fallback, with `"E"` set to `null`.

There is exactly one source of truth for each of the five values in either case; nothing here is
independently re-resolved by whatever assembles `desk_snapshot.json`.

**This is the pointer every reader outside the publish path itself resolves.**
`verdict_report.py` rendering a report from already-published data, `daily_job.py`'s summary, and
`run_desk`'s own status assembly all read `desk_snapshot.json` first, then read each named pass's
directory at the `publication_id` the snapshot specifies — never a pass's bare `current.json`,
which is allowed to have moved on to a newer, not-yet-reconciled-by-E publication. Only the
orchestration path that is actively running a complete A–E cycle and is about to publish a *new*
`desk_snapshot.json` reads pass-level `current.json` pointers directly, because it is in the process
of establishing what the next coherent set will be.

A bounded history is kept, not just the single current snapshot — but it is written strictly after
the commit, not before, so a crash can leave history *incomplete*, never *false*.
`desk_snapshot.json` is repointed first, via its own atomic write-temp-then-`os.replace`; only once
that succeeds does the same code append a line — a full copy of the just-published payload, not just
a pointer to it — to `packs/<date>/verdicts/desk_snapshot_history.jsonl`. A prior draft had this
backwards (append, then repoint), which meant a crash between the two steps could leave a history
entry asserting a publish that `desk_snapshot.json` itself never actually committed to. With commit
first, the worst a crash can do is leave history one entry short of the truth — recoverable, in the
sense that a reader can always reconcile the two: `desk_snapshot.json`'s current content is
authoritative, and the history file's own last line is checked against it whenever the reader cares
about the distinction; a short gap is expected and harmless, a contradiction between the two never
happens because history is only ever written about something already committed.

### Concurrency

Commit-then-log ordering fixes the single-writer case: one process, one crash, at one point in that
process's own sequence of operations. It does not fix concurrent writers. Two `orchestrate_desk`
invocations racing each other — or one full run racing a standalone `--rebuild-projections` or
cleanup invocation — could each individually follow commit-then-log correctly and still interleave
their commits and appends: writer 1 commits `desk_snapshot.json`, writer 2 commits
`desk_snapshot.json` (now pointing past writer 1's), writer 1's history append runs after writer 2's,
and the history file's last line no longer matches the current pointer — not because either append
was false, but because nothing serialized the two writers against each other. The same race applies
to two per-pass publishes for the same pass, and to the retention pass running while a publish is
still in flight (pruning a versioned directory that a concurrent publish is about to reference).

The fix is an advisory writer lock per pack date, held for the duration of any mutation to
`packs/<date>/verdicts/`, reusing the exact idiom `daily_job._acquire_writer_lock` /
`_release_writer_lock` already establishes in this codebase (`daily_job.py:234-251`): `mkdir(exist_ok=False)`
on a sentinel directory as the atomic acquire (portable, no OS-specific file-locking API), `rmdir()`
as the release, wrapped so the lock is always released via `try`/`finally` even on an exception mid-mutation.

**The verdict lock alone is not sufficient, because `_desk_publication_ready` step 1 reads the live
pack CSVs.** A prior draft made `packs/<date>/verdicts/.writer_lock` deliberately independent of
`daily_job`'s `.daily_job_lock`, reasoning that "the two protect different resources." They do not:
step 1 hashes `candidates.csv`, `game_totals.csv`, and `team_totals.csv` to compute the fingerprint
every subsequent step compares against, and `daily_job` — holding only its own lock — rewrites
exactly those files. A pack rebuild landing between step 1's hash and step 3's E check produces a
snapshot whose recorded `pack_fingerprint` describes a pack revision that no longer exists on disk,
or worse, one assembled half from each. The two locks protect overlapping state, so they must be
ordered, not independent:

- Any desk write that reads or depends on the pack fingerprint — every per-pass publish,
  `_desk_publication_ready`, and the desk-level publish — acquires **`.daily_job_lock` first, then
  `.writer_lock`**, and releases in reverse. This ordering is fixed and global: no code path in this
  plan ever acquires them in the other order, which is what makes deadlock impossible rather than
  merely unlikely.
- Operations that touch only `packs/<date>/verdicts/` and never read pack CSVs — the legacy- and
  `latest_preview`-projection steps, and the retention pass — take `.writer_lock` alone. They read
  only already-published artifacts and the recorded fingerprints inside them, never the live CSVs, so
  they cannot observe a torn pack.
- `daily_job` itself is unchanged: it continues to take `.daily_job_lock` only, and knows nothing
  about `.writer_lock`. The ordering contract is satisfied entirely by the new code always taking the
  outer lock first, so no existing caller has to be modified to participate.

Every one of the following acquires the appropriate lock(s) per the ordering above before doing
anything and releases them when done, with no exceptions:

- a per-pass publish (versioned-directory write, `manifest.json` computation, and the pass-level
  `current.json` repoint) — this closes the same-pass race,
- `_desk_publication_ready` plus the desk-level publish it gates (`desk_snapshot.json` repoint and
  the subsequent `desk_snapshot_history.jsonl` append) — this is what makes commit-then-log actually
  hold under concurrency, not just under a single writer's own crash,
- the legacy-projection step and its standalone `--rebuild-projections` invocation — a second writer
  path that a prior draft left unlocked,
- the reachability-and-age retention pass — so cleanup can never observe a versioned directory
  mid-write, or delete one a concurrent publish is in the process of making reachable.

### Stale-lock recovery

A bare `mkdir`/`rmdir` lock has no owner and no expiry, so a writer killed between acquire and
release leaves a sentinel directory nothing will ever remove — every subsequent publish fails its
bounded retry and the pack's desk output is permanently stuck until a human intervenes. A prior draft
accepted this, and even encoded it as a test asserting later writers fail after the holder is killed.
That is not an acceptable steady state for a pipeline that runs unattended on a schedule: the failure
is silent from the operator's perspective (a desk run that "just fails") and its remedy —
`rmdir` a specific hidden directory — is not discoverable from the error.

The lock therefore carries owner metadata and a defined recovery path:

- Acquire writes `packs/<date>/verdicts/.writer_lock/owner.json` immediately after the `mkdir`
  succeeds, containing `{"pid": …, "hostname": …, "acquired_at": …, "operation": "desk_publish" | …}`.
  The `mkdir` remains the atomic acquire — `owner.json` is written *after*, and its absence is itself
  meaningful (see below).
- A contender that fails to acquire reads `owner.json` and applies exactly one recovery rule: the
  lock is **breakable** only if `hostname` matches the current host *and* the recorded `pid` is not
  running *and* `acquired_at` is older than a configured `stale_lock_after` (default 30 minutes,
  comfortably beyond the longest legitimate publish, which is bounded by provider timeouts already
  capped at 600s). Breaking is itself performed under a short retry — remove `owner.json`, then
  `rmdir` the sentinel, then attempt a normal acquire — so two contenders racing to break the same
  stale lock still produce exactly one winner via `mkdir(exist_ok=False)`.
- A lock whose `hostname` differs from the current host is **never** broken automatically, regardless
  of age: this codebase's packs live on OneDrive-synced paths shared across machines
  (`docs/ENT-SYNC-GLOBAL-PROMPT.md` documents multiple clones and worktrees), so a "dead" PID on
  another host cannot be verified and may be actively writing. These surface as an explicit operator
  error naming the holding host, PID, and age, plus the exact remediation command.
- A sentinel directory with **no** `owner.json` at all is treated as breakable after
  `stale_lock_after` regardless of host — it means a writer died in the sub-millisecond window
  between `mkdir` and the metadata write, so there is no owner to respect.
- The same rule set is exposed as a standalone `--break-stale-lock` command that performs the checks
  and reports precisely why a lock is or is not breakable, so an operator facing the cross-host case
  has a supported action rather than being told to delete a hidden directory by hand.

A contending writer that fails to acquire the lock does not block indefinitely: it retries with a
short bounded backoff (matching the existing retry idioms elsewhere in this codebase, e.g. the
provider-call retry loops in `reasoning.py`/`gemini_research.py`) and fails with a clear "another
desk write is in progress for this pack" error if the lock is still held after that window — the same
fail-fast-on-contention posture `_acquire_writer_lock`'s callers already use, rather than inventing a
new waiting policy for this one.

### Retention

Prior versioned directories are left on disk by default — they are cheap (JSON and Markdown) and are
the audit trail of every attempt, including every distinct forced-rerun response. Pruning is an
explicit, separate cleanup pass, not part of the publish path itself, and it leads with
**reachability, not age**, as the deciding question — age-only pruning can delete a directory that an
*old but still-current* `desk_snapshot.json` depends on, or that a retained `verdict_records` row
cites for audit — both real possibilities, since a desk snapshot or a persisted decision can
reference a `publication_id` well after it stopped being any individual pass's newest one. (The exact
role age still plays is stated precisely below — reachability is necessary but not, by itself,
sufficient for deletion.) Cleanup computes the retained set as the union of:

- every `publication_id` referenced by the current `desk_snapshot.json`,
- **every `publication_id` named by any pass-level `current.json`**, whether or not the desk snapshot
  has caught up to it. A prior draft omitted these, which would have let cleanup delete the target of
  `A/current.json` after an A-only rerun — a publication that is unreachable from the desk snapshot by
  design (that is exactly the state a subset rerun produces) but is still the pass's current
  publication and the source of its `latest_preview.md`/`latest_preview.json`. Deleting it would break
  the preview files and orphan the pointer, leaving `current.json` naming a directory that no longer
  exists.
- every `publication_id` referenced by any entry retained in `desk_snapshot_history.jsonl` (bounded
  by that file's own retention window, kept short — e.g. the last 20 desk publishes — since its only
  purpose is recent audit, not permanent archive),
- every `publication_id` referenced by any `verdict_records` row that `feedback.py`'s own existing
  retention policy has not yet pruned — this plan does not invent a new retention rule for that table,
  it only makes sure this cleanup pass consults it before deleting anything the table still cites,
- **and, transitively, every `publication_id` cited by anything already in the retained set.**
  Reachability here is a graph traversal, not a flat union of four lists: a retained E publication's
  `upstream_publication_ids` and its records' `cites` entries name A/B/D/C publications that may
  themselves appear in none of the roots above — an E retained via `desk_snapshot_history.jsonl`
  routinely pins upstream publications that the *current* snapshot has long since moved past. Deleting
  those while keeping the E that cites them produces exactly the dangling citation the `stale_citation`
  gate and this whole publication scheme exist to make impossible. Cleanup therefore seeds the
  retained set from the roots above and closes it under citation — repeatedly adding every
  publication named by any retained publication's `upstream_publication_ids` or `cites` until the set
  stops growing (a bounded traversal: citations only ever point from E to A/B/D/C, never in a cycle,
  so it terminates in one hop in practice and is written as a fixpoint loop only for safety).

**The deletion rule is a conjunction, stated once here and nowhere else: a versioned directory is
deleted only when it is both unreachable from that set *and* older than a configured age cutoff
(default: matching the pack's own retention window).** A prior draft's prose said only "deletes any
versioned directory not in that set" — reachability alone, no age check — which contradicts this
plan's own test coverage for the same mechanism (see [Testing](#testing), reachability-retention
tests), which requires a freshly-published, not-yet-referenced-by-anything directory to survive until
it ages out. That grace period is not incidental: a directory can be legitimately unreachable for a
few seconds by construction — it was just published under a new `publication_id` by a pass that
hasn't yet been folded into a new `desk_snapshot.json` (a single-pass rerun, or A/D/B mid-run before
E or the fallback has completed) — and deleting on reachability alone would race a normal, in-progress
desk cycle. Age-only pruning is wrong for the reasons above; reachability-only pruning is wrong for
this one. Only the conjunction is correct: unreachable *and* expired. This is what makes the no-E
fallback's rule 5 (labeling the report `fallback_no_e`) and E's own `cites` mechanism actually
durable — a citation that survives into a persisted decision or a published snapshot cannot be
silently pruned out from under it, and a mid-cycle publish is never pruned before it has had a chance
to become reachable.

## Deterministic Markdown

`verdict_report.py` renders the `A.md` §14 report structure. The source is E's validated
reconciliation set when present (E's whole purpose is to be the final word) — falling back, when E
did not run or produced nothing usable, to a **deterministic reconciliation** of A/D/B's validated
verdicts computed by this module itself, not to an unspecified "union." Section labels below are the
`A.md` §14 report sections (§14.A–§14.F), not the pipeline passes A–E; they are named identically by
coincidence and that ambiguity is called out here once rather than repeated at each bullet.

### The no-E fallback reconciliation

Combining three passes' opinions on one `outcome_id` needs a rule, not a merge, because A, D, and B
can validly disagree — one may `BET` where another `STAND_DOWN`s the same market on legitimate
research grounds, and their `recommended_units` need not match even when all three say `BET`. This
mirrors `run_desk.orchestrate_desk`'s existing `e_usable` / `required_usable` fallback logic
(`run_desk.py:349-361`) at the report level rather than only at the pass-count level that function
already checks.

The previous revision's two rules — "`STAND_DOWN` wins" and "otherwise `BET` only if every pass that
produced a verdict said `BET`" — are not total and not fail-closed. A record where A says `BET` and D
says `PASS` (no `STAND_DOWN` anywhere) satisfies neither rule: rule 1 doesn't fire because nothing was
a `STAND_DOWN`, and rule 2 doesn't fire because not every contributing pass said `BET`. And rule 2's
"every pass that produced a verdict" is vacuously true for a single contributor — if D and B never
evaluated a given `outcome_id` at all (out of scope for their evidence, or the row failed a
pass-specific gate) while A alone said `BET`, that one verdict satisfies "every pass that produced a
verdict said `BET`" trivially and would publish a wager backed by a single, uncorroborated opinion —
precisely what a three-pass desk exists to avoid.

The corrected rule is total (every combination of A/D/B outcomes maps to exactly one fallback
verdict) and requires an explicit quorum rather than treating silence as agreement. For every
`outcome_id`, in code, with no model call:

1. **Define `contributors`** as the subset of `{A, D, B}` that produced a validated verdict — `BET`,
   `PASS`, or `STAND_DOWN` — for this `outcome_id`. A pass that never evaluated the row at all
   (out of scope for its evidence, or the row failed a gate specific to that pass) is absent from
   `contributors`, not silently counted as agreement.
2. **Quorum: `contributors` must equal `{A, D, B}` exactly, or the fallback verdict is `STAND_DOWN`**
   with reason `insufficient_quorum`. This is what kills the single-uncorroborated-`BET` case: a
   `BET` from one pass, with the other two never having weighed in, is not a three-pass desk's
   verdict — it is one pass's verdict, and the fallback exists precisely because no pass was asked to
   reconcile. Requiring full participation before ever considering `BET` is the deliberately
   conservative choice appropriate to a fallback that only runs when the pass meant to make this
   judgement call (E) did not.
3. **With full quorum: any `PASS` or `STAND_DOWN` among the three wins, verdict is `STAND_DOWN`.**
   `PASS` and `STAND_DOWN` are both "this pass will not bet this," and this rule treats them
   identically for reconciliation purposes — the previous revision's rule 1 named only
   `STAND_DOWN` and left `PASS` unhandled, which is the gap fixed here. This matches the sizing
   philosophy already in `A.md` §11 — "materially negative evidence should reduce size" and "never
   increase... merely because news is favorable" — applied one level up, to the reconciliation
   itself: a pass declining to bet is not overruled by passes that reached a different conclusion.
4. **Only if all three say `BET`, the fallback verdict is `BET`**, with stake `min()` across the
   three passes' validated `recommended_units` for that `outcome_id` — never a sum, never an
   average, never the maximum. Every `(A, D, B)` combination is now covered by exactly one of steps
   2–4: quorum failure, any non-`BET` present, or unanimous `BET`.
5. **Rationale is the concatenation** of each contributing pass's evidence-derived rationale, each
   labeled by its source pass letter, so the report shows *why* each pass reached its conclusion
   rather than presenting a synthesized opinion no model actually held. A `STAND_DOWN` from
   `insufficient_quorum` states which pass(es) never evaluated the row, so the report doesn't read as
   a silent drop.
6. The rendered report — and `reasoning_status.json`'s `final_report` block — is marked
   `synthesis_source: "fallback_no_e"` rather than `"claude_e"`, so a reader can always tell whether E
   reconciled the slate or the deterministic fallback did. This status distinction already exists in
   `run_desk.orchestrate_desk`'s `final_report.source` field (`local_synthesis` vs `claude_e`,
   `run_desk.py:344,364,369`); this plan adds the third value to the same field rather than inventing
   a parallel one.

This is deterministic code, not a fourth opinion — it computes a conservative combination of
verdicts the gate already validated, which is consistent with the plan's own boundary that models
explain and the pipeline computes. It does not attempt research-quality reconciliation (weighing
which pass's evidence was stronger); that judgement is exactly what E exists to provide, and its
absence is why the fallback is deliberately conservative rather than adjudicative.

- §14.B's table columns — market ID, selection, line, price, book, probability source, pack edge,
  pre-news units — are read from the index row, never from the envelope. Player names come from
  `index.players`. Final units come from the clamped, policy-allocated value. The model's
  contribution to each row is the rationale prose beneath it, drawn from the rendering record's
  `evidence`/`contradictions` (verdict envelope) or `narrative` (reconciliation envelope).
- §14.C (Research Validation) is built from validated pass C `findings` directly — each finding's
  `claim`, `source_name`, `source_tier`, `source_timestamp` map onto the table's Claim/Source/Tier/
  Timestamp columns without another pass's evidence re-derivation — supplemented by any
  `kind: external` evidence items on the rendering record itself.
- §14.D is the `allocate_portfolio_risk` output, so correlation adjustment is computed rather than
  asserted.
- §14.E lists every non-BET record with pack-quoted identity and the union of model
  `rejection_reasons` and machine violation codes.
- §14.F merges `slate_notes`, `needs`, `unindexed_totals` coverage notes (per
  [Authoritative index](#authoritative-index)), and the existing freshness/coverage summary.

This is what makes the request's closing constraint structural rather than aspirational for the
report's numbers and structured identity fields — §14.B's table cells cannot be fabricated, because
they are never sourced from the envelope's free text. It is deliberately not a claim about the
prose beneath that table; see [Two invariants, not one](#two-invariants-not-one) for the boundary.

## Artifacts and status

Per pass, published via the versioned-directory scheme in
[Atomic multi-file publication](#atomic-multi-file-publication) — file name and internal record-array
key follow the pass's envelope kind (`verdicts` for A/D/B, `findings` for C, `reconciliations` for E).
`current.json` at `packs/<date>/verdicts/<pass>/current.json` is the pass-level pointer, and
`packs/<date>/verdicts/desk_snapshot.json` is the desk-level one every non-publish reader actually
uses (see [The desk-level snapshot](#the-desk-level-snapshot)). Each versioned directory
`packs/<date>/verdicts/<pass>/<publication_id>/` contains exactly the four files the manifest names,
plus the manifest itself:

- `verdicts.json` — the envelope as returned, with `model`, `schema_version`, `publication_id`,
  `request_sha256`, `candidates_sha256`, `game_totals_sha256`, and `team_totals_sha256` as top-level
  keys.
- `violations.json` — every violation record, including `warn` severity, with the binding pack value
  for each, and a `class` field distinguishing pass-failing (identity/tamper) from judgement-outcome
  violations per the failure policy above.
- `report_fragment.md` — this pass's Markdown contribution, where applicable.
- `status_fragment.json` — this pass's `envelope_present`, `envelope_kind`, `schema_version`,
  `record_count`, `bet_count` (0 for C), `rejected_count`, `violation_codes` (counts by code), `mode`,
  `repair_attempts`, and `structured_output_native` (whether the provider accepted the schema or the
  fallback parser was used). This is what a prior draft placed in `reasoning_status.json` directly,
  written by a second, independent code path from the rest of the pass's artifacts — meaning a crash
  between the two writes could leave status and content disagreeing about what was actually
  published. Folding it into the same atomically-published set as the envelope and violations closes
  that gap: status for a given `publication_id` is now part of the same all-or-nothing write as
  everything else that publication contains.
- `manifest.json` — the four files above, each by its own content hash, plus `pass`,
  `request_sha256`, and `schema_version`; `publication_id` is the hash of this manifest (see
  [The per-pass immutable manifest](#the-per-pass-immutable-manifest-and-what-publication_id-actually-covers)).

**Legacy flat files (`chatgpt_a.md` and siblings) and `reasoning_status.json` are non-authoritative
projections of the committed desk snapshot, not independently written outputs.** A prior draft kept
them on "today's write path" — written by the same code that used to write them before this plan
existed — alongside the new atomic artifacts. That reintroduces, at the boundary between the two
paths, exactly the desynchronization this whole feature exists to prevent: a crash between the atomic
publish succeeding and the legacy write completing leaves a legacy file that disagrees with the
authoritative versioned directory it was supposed to mirror, and nothing about the atomic guarantee
covers a file outside it. Instead:

**The legacy-named compatibility files derive from exactly one source: the current
`desk_snapshot.json`, never a pass-level `current.json`.** A prior draft allowed either — "after
`desk_snapshot.json` is published (or a pass-level `current.json` is repointed)" — which reopens the
same coherence gap [The desk-level snapshot](#the-desk-level-snapshot) exists to close: a single-pass
rerun of A alone (which, per that section, updates only A's `current.json` and does *not* advance
`desk_snapshot.json`) would still be allowed to regenerate `chatgpt_a.md` from A's brand-new,
not-yet-desk-reconciled output. Any consumer reading `chatgpt_a.md` at that point would see a verdict
set newer than, and inconsistent with, whatever `claude_e.md`/`reasoning_status.json` still say the
desk reconciled against — the exact mismatch this whole design exists to prevent, just relocated to
the legacy-file boundary. So:

- `chatgpt_a.md`, `gemini_b.md`, `chatgpt_c.md`, `claude_d.md`, `claude_e.md`,
  `manual_betting_report.md`, and `reasoning_status.json` are rebuilt **only** by reading
  `desk_snapshot.json` and then reading each pass's directory at exactly the `publication_id` that
  snapshot names — never a pass's own `current.json`. Because `desk_snapshot.json` only advances on a
  complete A/B/D-plus-(E-or-fallback) cycle, these files are guaranteed self-consistent with each
  other and with whatever the desk actually reconciled, by construction, not by convention.
- A pass that has published newer output not yet reflected in `desk_snapshot.json` (the single-pass
  rerun case) is visible, if a reader wants it, only through a separately-named diagnostic path —
  `packs/<date>/verdicts/<pass>/latest_preview.md` and `latest_preview.json`, rebuilt from that
  pass's own `current.json` on the same idempotent schedule as the legacy files below. These names
  are never read by anything this plan defines as authoritative (the report renderer, `daily_job.py`,
  `verdict_records` persistence); they exist purely so an operator debugging a single-pass rerun can
  see its output without waiting for a full desk cycle. `latest_preview.json` states this machine-
  readably (`"authoritative": false`); `latest_preview.md` states the same thing as its first line —
  a fixed banner, not prose an operator could miss — so nothing downstream, human or code, can confuse
  either file for the desk-authoritative one.
- This step is explicitly allowed to fail, crash, or simply not have run yet, without any loss of
  authoritative data — the versioned directories and `desk_snapshot.json` are unaffected by it, and
  the projection can always be regenerated by re-reading the current snapshot. Nothing downstream of
  the atomic artifacts depends on the projection existing; it exists only for consumers not yet
  updated to read the versioned path directly.
- The projection step is not required to run synchronously with publish. `run_desk` runs the
  legacy-file projection once, at the end of `orchestrate_desk`, only when `desk_snapshot.json` has
  just advanced; it runs the `latest_preview` projection after any pass-level publish, including a
  single-pass rerun. Either can also be invoked standalone (e.g. `--rebuild-projections`) to repair a
  stale or missing file without touching any authoritative data at all.

In shadow mode the versioned directories live under `packs/<date>/verdicts/shadow/<pass>/` with their
own `current.json`, and shadow's `desk_snapshot.json` equivalent lives under
`packs/<date>/verdicts/shadow/desk_snapshot.json` — keeping the invariant from
[Modes, and what shadow mode may never do](#modes-and-what-shadow-mode-may-never-do) that shadow
output is never reachable from the pointer an `enforce`-mode reader resolves, including the desk-level
pointer, not just the per-pass ones.

**`pack.DERIVED_PACK_OUTPUTS` is *not* extended to cover any of this, and the versioned store is
excluded from ordinary rebuild cleanup entirely.** A prior draft said the new files should be added
to `DERIVED_PACK_OUTPUTS` so a pack rebuild would clean them up like every other derived artifact.
That is wrong on both a mechanical and a design level:

- **Mechanically, it would crash.** `pack.py`'s rebuild loop is `for name in DERIVED_PACK_OUTPUTS:
  (out_dir / name).unlink(missing_ok=True)` (`pack.py:1747-1748`) — `Path.unlink()` raises
  `IsADirectoryError` on a directory. Every versioned publication is a directory
  (`packs/<date>/verdicts/<pass>/<publication_id>/`); the first rebuild after this plan landed would
  fail outright the moment it reached one of those entries.
- **Even for the single-file entries (`current.json`, `desk_snapshot.json`,
  `desk_snapshot_history.jsonl`), unlinking them on every rebuild is the wrong operation, not just an
  unsafe one.** A rebuild is a routine, frequent operation on this pack; deleting the desk-level
  pointer on every rebuild would discard the entire coherence contract this plan exists to establish,
  and do it by a code path that consults none of the retention rules in [Retention](#retention) —
  reachability, age, or the writer lock in [Concurrency](#concurrency) all get bypassed, because a
  blind `unlink()` loop has no concept of any of them.

Instead, `packs/<date>/verdicts/` is carved out as its own subtree, structurally excluded from
`DERIVED_PACK_OUTPUTS` and from `pack.py`'s rebuild loop by name (the loop is changed to skip any
entry that resolves under that subtree, or — more simply — the loop's iteration is scoped to
`DERIVED_PACK_OUTPUTS` filenames directly under `out_dir`, which `verdicts/` already isn't, so no
change to the loop itself is strictly required as long as `verdicts/` is never added to the constant).
The only two processes ever allowed to remove anything from that subtree are the locked reachability-
and-age retention pass in [Retention](#retention), and the locked per-pass/desk-level publish paths
themselves replacing what they own. If a pack's fingerprint genuinely changes — a rebuild that
produces a materially different `candidates.csv`/`game_totals.csv`/`team_totals.csv` — this needs no
special-case handling: `_desk_publication_ready` will simply stop finding fresh, fingerprint-matching
publications for A/B/D against the new fingerprint, existing publications become unreachable from any
new snapshot, and the ordinary retention pass reclaims them on its own schedule, honoring the same
age grace period as any other unreachable directory. A rebuild is not a special deletion event for
this subtree; it is just a fingerprint change that the existing mechanisms already handle correctly.

`run_desk.PHASE_OUTPUTS` and `pack.DERIVED_PACK_OUTPUTS` are extended only for genuinely file-based
derived outputs outside `verdicts/` that this plan adds (none, currently — the legacy flat files
`run_desk.PHASE_OUTPUTS` already names are reused, not newly introduced). `daily_job.py` surfaces the
aggregate rejected count in its summary, read from `desk_snapshot.json`'s named publications rather
than by independently resolving each pass.

## Verdict persistence

`feedback.DECISION_FIELDS` is one row per decision with fixed `A_verdict`, `B_verdict`, `C_verdict`,
`D_verdict`, `final_verdict`, and `units` columns (`feedback.py:86`) — and no `E_verdict`. Five
per-pass envelope records cannot be folded into that shape, and widening it with a singular `pass`
column would corrupt the grain.

Instead, add a child table `verdict_records` with primary key
`(decision_id, pass, publication_id, record_id)` and columns `outcome_id`, `stream`, `verdict`,
`confidence`, `recommended_units_model`, `recommended_units_validated`, `violation_codes`, `mode`,
`created_at`. Two changes from what an earlier revision specified, both for the same underlying
reason as the filesystem layer above:

- **`record_id` joins the key** because `(decision_id, pass, request_sha256)` alone permits only one
  row per pass per decision, which is incompatible with C legitimately emitting several findings
  against one `outcome_id` in a single request; without `record_id`, the second finding either fails
  a uniqueness constraint or silently overwrites the first.
- **`publication_id` replaces `request_sha256`** because, exactly as in
  [Atomic multi-file publication](#atomic-multi-file-publication), `request_sha256` names a request
  and a forced rerun can produce a second, different response under the same request hash;
  persisting by `request_sha256` would either collide two genuinely different responses into one key
  (if their `record_id`s happened to coincide) or silently conflate them under audit. `publication_id`
  is the content-hash of the actual response, so two different forced-rerun responses always persist
  as distinct rows, and `feedback.py`'s existing settlement/scoring queries gain a real, unambiguous
  handle on "which exact response was this."

The migration is purely additive and follows the existing `ALTER TABLE ... ADD COLUMN` /
`CREATE TABLE IF NOT EXISTS` conventions in `feedback.py`; no existing column changes meaning.

**Legacy `A_verdict`..`D_verdict` projection.** `decisions.decision_id` is a `TEXT PRIMARY KEY`
(`feedback.py:593`) holding one scalar per pass per decision — one BET/PASS/STAND_DOWN verdict, since
A, D, and B each produce at most one validated verdict per `outcome_id` (a pass emitting two verdicts
for the same `outcome_id` is itself a violation, not a supported case). Those four legacy columns
therefore keep being populated exactly as today, sourced from the child table for the passes they
cover — there is nothing to reduce, because there is nothing multi-valued.

**`C_verdict` is different and was left unspecified in the previous revision.** C's finding envelope
permits, by design, several findings per `outcome_id` — the same design change that motivated
`record_id` in the first place — so a decision's `outcome_id` can have zero, one, or many validated C
findings, each independently `CONFIRMS`/`CONTRADICTS`/`NEUTRAL`, and the legacy column is a single
scalar. The reduction is a fixed, content-based precedence, not an emission-order pick: across every
validated C finding sharing the decision's `outcome_id`, `C_verdict` takes the most consequential
value present — `CONTRADICTS` if any finding contradicts, else `CONFIRMS` if any finding confirms,
else `NEUTRAL` if only neutral findings exist, else left empty exactly as it is today when C produced
nothing usable for that market. This mirrors the same "a problem found by one source is not overruled
by sources that didn't look hard enough to find it" precedence already used in the
[no-E fallback reconciliation](#the-no-e-fallback-reconciliation), applied to C's three-value enum
instead of a bet/no-bet decision. The full per-finding detail — every C finding, individually, with
its own citation, source, and tier — remains available in `verdict_records`; `C_verdict` is
deliberately a lossy summary column for legacy readers, not the source of truth.

Whether to add `E_verdict` is a separate question deliberately left out of this change.

Scoring model verdicts against settled outcomes is the loop this table makes possible and is out of
scope here.

## Configuration

A new `config/verdict_policy.json`, loaded by the same defensive pattern as
`portfolio.load_portfolio_policy` (missing file yields documented defaults, unknown keys raise):

```
mode                        shadow | enforce      default shadow
repair_attempts             int                   default 1
reject_fail_ratio           float                 default 0.5
prohibited_variance_markets list[str]             3PM, THREES, THREE_POINTERS_MADE, HA,
                                                  HITS_ALLOWED, TO, TURNOVERS
desk_prohibited_markets     list[{market, scope}] see below — BB is player-scoped, not a bare token
enforce_mlb_whitelist       bool                  default true
external_evidence_max_age_h float                 default 24.0
```

## Resolved market policy

Both conflicts were ruled on by the repo owner on 2026-08-12; the player/team BB scope question
surfaced by the second review was ruled on the same day. Every validator fixture encodes these
answers, so they are settled before step 1 rather than defaulted.

**HRR and BB — `prompts/A.md` §5.3 wins for the player props. Team BB stays recommendable.**

The whitelist and the desk ban are answering different questions, and the fix is to stop treating
them as the same question. `normalizer.ALLOWED_MLB_PLAYER_PROPS` /
`normalizer.ALLOWED_MLB_TEAM_PROPS` govern what may *enter* a pack; `A.md` §5.3 governs what may be
*recommended*. HRR and BB keep entering packs in both forms — the normalizer is unchanged, so nothing
upstream moves.

`§5.3`'s "BB / walks" reads naturally as the batting-walks player prop, and `BB` is also, separately,
an explicit member of `ALLOWED_MLB_TEAM_PROPS` alongside team H/SO/R/TOTAL — a market with its own
whitelist entry that §5.3 was not written with in view. `desk_prohibited_markets` therefore scopes by
`market_type`, not by the bare token: a BET verdict on HRR (any `market_type`) or on `BB` where
`market_type == "PLAYER_PROP"` is `prohibited_market`; a BET on `BB` where
`market_type == "TEAM_PROP"` is unaffected and recommendable like any other team total. Both banned
forms still appear as ordinary candidates and only fail at the recommendation gate, landing in the
stand-down section.

Concretely, `desk_prohibited_markets` is a list of `{market, scope}` pairs rather than bare strings:
`{"market": "HRR", "scope": "any"}`, `{"market": "BB", "scope": "PLAYER_PROP"}`. `verdict_gate.py`
reads a row's `market_type` field (present on `CANDIDATES_HEADER`) to resolve `scope`; totals rows,
which carry no player/team prop distinction in this sense, only match `scope: "any"` entries.

The corollary needs writing down where every agent reads it: **whitelisted ≠ recommendable, and the
scope matters.** The house-rule text in `CLAUDE.md`, `AGENTS.md`, `GROK.md`, `GEMINI.md`, and their
user-home copies gains that distinction, phrased narrowly enough not to read as a contradiction of
the existing "HRR and BB are allowed at generation" line: generation allows both; the desk may not
BET the HRR prop or the *player* BB prop; team BB remains a normal recommendable team total. Those
files are kept in lockstep by `scripts/verify-sync.ps1`, which rewrites the invariant block from
`docs/ENT-SYNC-GLOBAL-PROMPT.md`; the edit therefore goes into `ENT-SYNC-GLOBAL-PROMPT.md` and is
propagated by a sync run, not typed into each file by hand.

**Total bases — allowed. `pack.ROLE_BLOCK` is corrected.**

`ROLE_BLOCK`'s variance taxonomy currently reads "High variance: 3PM, hits allowed, total bases,
turnovers." Total bases is removed, leaving 3PM, hits allowed, and turnovers — which then matches
`A.md` §5.2 exactly. `A.md` §5.2 becomes the single authoritative never-recommend set, and TB is
recommendable with no special handling. TB is deliberately not moved into the moderate-variance list
alongside strikeouts/assists/points; it is simply unclassified, so nothing reduces its confidence or
sizing.

The resulting config values, which replace the placeholders in the previous revision:

```
prohibited_variance_markets  ["3PM", "THREES", "THREE_POINTERS_MADE",
                              "HA", "HITS_ALLOWED", "TO", "TURNOVERS"]
desk_prohibited_markets      [
  {"market": "HR", "scope": "any"},
  {"market": "HOME_RUNS", "scope": "any"},
  {"market": "WALKS_ALLOWED", "scope": "any"},
  {"market": "HRR", "scope": "any"},
  {"market": "BB", "scope": "PLAYER_PROP"}
]
```

`enforce` mode is no longer gated on a policy decision. It remains gated on the grounded-Gemini probe
and one slate of violation telemetry.

## Testing

TDD throughout, `pytest`, fully offline, no provider network path. New files mirror the existing
naming: `tests/test_verdicts.py`, `tests/test_pack_index.py`, `tests/test_verdict_gate.py`,
`tests/test_verdict_report.py`, with additions to `tests/test_runner_common.py`,
`tests/test_reasoning.py`, `tests/test_claude_reasoning.py`, `tests/test_claude_synthesis.py`,
`tests/test_gemini_research.py`, `tests/test_c_research.py`, `tests/test_run_desk.py`, and
`tests/test_feedback.py`.

Minimum coverage:

- One red-path test per violation code, each built from a fixture pack whose row is valid except for
  the single property under test, asserting the exact code fires and no other.
- A green-path test proving a fully compliant envelope produces zero violations and a report whose
  numeric cells equal the pack row byte-for-byte.
- Identity tests: a totals verdict emitting `totals_id` as `market_id` (compatibility branch passes);
  the same with a wrong `market_id` (`market_id_mismatch`); a `stream` that disagrees with the index;
  a duplicate `outcome_id` across streams raising at index build; a candidates fixture carrying both
  sides of one `market_id`, proving the old `c_research._market_index` collision is gone.
- Adversarial fixtures: a line one tick off; a stake of `1.25` against a `0.5` increment; a stake at
  exactly `max_units` (passes) and one increment above (fails); an injury claim citing a row with
  empty `injury_flags`; a player claim with no `player_id` (`unbound_player_claim`, rejected — this
  is the *structured* evidence channel, per [Invariant A](#two-invariants-not-one), and is where an
  invented player identity is actually blocked); a `player_id` absent from the roster
  (`unknown_player`, rejected); a BET on a row that locked between pack build and validation; a 2B
  OVER; `+150` and `+149` prices. (An invented player name in free-form *prose* is covered separately
  under Prose-invariant tests below, and is explicitly not expected to be blocked — see
  [Invariant B](#two-invariants-not-one).)
- Market-policy tests pinning the 2026-08-12 ruling: a BET on an HRR row and on a *player* BB row
  (`market_type == PLAYER_PROP`) rejects as `prohibited_market`, while a BET on a *team* BB row
  (`market_type == TEAM_PROP`) passes with no violation — proving the ban is scoped by
  `market_type`, not by the bare token `BB`. All three rows still appear as candidates, proving the
  pack-entry path is untouched. A BET on a TB row passes with no variance violation and no confidence
  penalty; 3PM, hits allowed, and turnovers still reject. A guard test asserts `pack.ROLE_BLOCK` no
  longer names total bases as high variance, so a future edit that reintroduces it fails loudly
  instead of silently re-diverging from `A.md` §5.2.
- Envelope-level tests: malformed JSON, JSON wrapped in prose, a stale `candidates_sha256` with a
  fresh totals hash and vice versa (both must trip `pack_mismatch`), an envelope of attempted `BET`s
  where more than half are rejected (fails `reject_fail_ratio`), a single identity/tamper violation
  alongside otherwise-clean attempted `BET`s well under the ratio threshold (still fails the pass —
  proves the two failure classes are independent, not just the ratio check), and — the case the third
  review's finding exists to prevent — an envelope where every record is a model-chosen `STAND_DOWN`
  or `PASS` with zero attempted `BET`s (passes cleanly, regardless of count, proving voluntary caution
  can never trip the ratio).
- `pack_index` totals fixture: two `INSUFFICIENT_DATA` rows on one slate (both `outcome_id=""`) build
  a valid index instead of raising, and a verdict referencing `outcome_id=""` gets `unknown_market`.
- Pass C tests: a finding envelope with no `recommended_units` field validates cleanly (proves the
  schema has no stake to violate); the existing `test_c_research.py` red-path fixtures (unknown
  `market_id`, altered `selection`/`line`/`price`, invalid tier, out-of-window timestamp) are ported
  to the finding-envelope shape and still fire the same violation codes through the shared gate.
- Pass E tests: a reconciliation on an `outcome_id` no upstream pass proposed
  (`unsourced_synthesis`); a `cites` entry naming a `record_id` that does not exist in the referenced
  pass's *current* validated output (`unsourced_synthesis`); a `cites` entry naming a correct
  `record_id` but a stale `publication_id` — e.g. from before a forced rerun of that pass —
  (`stale_citation`); a `cites` entry pointing only at a C finding with
  no A/D/B verdict backing the same `outcome_id` (still `unsourced_synthesis`, proving C alone cannot
  source a BET); C emitting two findings on one `outcome_id` and E citing the second one specifically
  by `record_id` (proves multi-finding citation actually resolves); an E stake above the upstream
  validated stake; an E injury claim that `cites` a validated B/C finding passes; an E injury claim
  citing nothing fails even though a matching `injury_flags` row exists on the underlying candidate
  (proves E is evidence-consuming, not pack-only); an E record with a tampered `line` fails exactly
  like an A tamper (proves E's own identity/tamper gates run, not just its synthesis-specific gates).
- No-E fallback tests, one per branch of the quorum rule: A `BET`, D `BET`, B never evaluated the
  row — quorum fails, fallback is `STAND_DOWN` with reason `insufficient_quorum` (the case the third
  review's finding exists to prevent — a lone uncorroborated `BET` must never publish); A `BET`, D
  `BET`, B `BET` — full quorum, fallback is `BET` at `min()` of the three stakes; A `BET`, D `PASS`,
  B `BET` — full quorum but not unanimous, fallback is `STAND_DOWN` (proves `PASS` is handled, not
  just `STAND_DOWN`); A `BET`, D `STAND_DOWN`, B `BET` — same, via the other non-`BET` value. The
  rendered report and `reasoning_status.json` carry `synthesis_source: "fallback_no_e"` in every
  fallback path and `"claude_e"` when E ran successfully.
- Prose-invariant tests: a `claim` string naming a real roster player with no bound `player_id`
  evidence item still renders in §14.C (proves Invariant B is real, not accidentally also enforced);
  the same string trips `unbound_name_in_prose` at `warn` only, never blocking the record's `BET`.
- Aggregate cap tests driving `allocate_portfolio_risk` past each group ceiling and asserting the
  binding constraint is named.
- Mode tests: identical inputs produce identical violation lists in both modes; shadow writes only to
  the shadow path; `verdict_report` raises on a shadow envelope; no shadow row reaches
  `verdict_records`.
- Runner tests with fake clients: schema propagation into `request_sha256`; the repair retry firing
  exactly once; the Gemini fallback path when the config rejects a response schema; publish atomicity
  via the versioned-directory scheme — a simulated crash after the versioned directory is written but
  before `current.json` is repointed leaves the previous version fully live and readable, and a
  simulated crash mid-write of the versioned directory (before its rename) leaves no trace visible to
  any reader at all.
- Migration tests: `verdict_records` created on a legacy database with the four-column primary key
  `(decision_id, pass, publication_id, record_id)`; legacy `A_verdict`..`D_verdict` values unchanged
  for existing rows; two `record_id`s from the same `(decision_id, pass, publication_id)` both persist
  without a uniqueness conflict; two different forced-rerun `publication_id`s for the same
  `request_sha256` both persist as distinct rows rather than colliding.
- `C_verdict` reduction tests: two validated C findings on one `outcome_id`, one `CONFIRMS` and one
  `NEUTRAL`, reduce to `CONFIRMS`; one `CONTRADICTS` among several `CONFIRMS` reduces to
  `CONTRADICTS`; zero validated C findings for an `outcome_id` leaves `C_verdict` empty, matching
  today's behavior when C produced nothing usable.
- `record_id` stability tests: the same logical finding assigned different array positions across two
  parsed envelopes (simulating a repair-round reorder) yields the identical `record_id`; two records
  differing by even one field (e.g. `claim` text) never collapse and each gets its own `record_id`,
  proving collapsing is content-exact, not fuzzy; every emitted `record_id`'s hash component is
  asserted to be the full 64 hex characters, guarding against a future "shorten it for readability"
  change silently reintroducing the truncation collision risk the eighth review identified.
- Duplicate-collapsing tests: two byte-identical C findings on one `outcome_id` collapse to a single
  record with a `warn`-severity `duplicate_record_collapsed` violation naming the pair; `record_count`
  in `status_fragment.json` reflects the post-collapse count (one, not two); three byte-identical
  records collapse to one record with the violation noting a collapsed count of three; re-parsing the
  same envelope with its array reversed still collapses to the identical single `record_id`, since
  collapsing depends only on content, not position.
- Publication-identity tests: two `force=True` runner calls returning genuinely different envelope
  content under the same `request_sha256` produce two different `publication_id`s and two distinct
  versioned directories, neither overwriting the other; an unforced rerun that reproduces byte-
  identical output lands on the same `publication_id` and writes nothing new; a `cites` entry naming
  a `publication_id` that is no longer the referenced pass's current one fails `stale_citation`;
  republishing with an unchanged envelope and violations but a *different* `report_fragment.md`
  (simulating a renderer fix) yields a different `publication_id`, proving the manifest hash actually
  covers the Markdown and not just the envelope; a directory's `manifest.json` is recomputed by a test
  helper from the other four files and asserted equal to the directory's own name, for both a normal
  publish and a hand-corrupted fixture (asserted *unequal* in the corrupted case, proving the
  self-check would actually catch tampering).
- Desk-snapshot tests: A, D, B, and E each publish independently, then `desk_snapshot.json` is
  written — its `publications["E"]` is asserted to equal E's own `publication_id` from its own
  per-pass publish (not present in, and not derivable from, E's `upstream_publication_ids` field,
  which never contains E's own ID), and `publications["A"/"D"/"B"/"C"]` are asserted to equal E's
  `upstream_publication_ids` exactly; A is then republished with new content (`current.json` for A
  now points elsewhere) and a reader resolving `desk_snapshot.json` still gets the *original* A
  `publication_id`, proving a later rerun cannot retroactively make an already-published desk
  snapshot inconsistent; the no-E-fallback path produces a `desk_snapshot.json` with `"E": null` and
  `synthesis_source: "fallback_no_e"`, with `publications` for A/D/B matching exactly what the
  fallback module read at computation time.
- `_desk_publication_ready` tests (the case the seventh review's finding exists to prevent, and where
  round 6's fix — reusing `e_usable`/`required_usable` — itself failed): a full A–E cycle publishes,
  leaving `gemini_b.md`/`claude_d.md` on disk from that run; `orchestrate_desk(steps=["A"])` is then
  invoked; assert `desk_snapshot.json` is **unchanged**, proving the new predicate does not fall back
  to stale flat-file existence for B/D the way `e_usable`/`required_usable` does. A separate fixture
  changes `game_totals.csv` (a fresh pack fingerprint) without republishing B or D, then reruns A
  alone: still no `desk_snapshot.json` change, because B/D's *existing* current publications now fail
  the fingerprint-match check in step 2, even though those files still exist on disk and are still
  each pass's own newest publication. A third fixture republishes B and D against the new fingerprint,
  then reruns A alone: `desk_snapshot.json` *does* advance, with A's new publication and B/D's
  now-fingerprint-fresh ones — proving the fix is about freshness, not a blanket subset-run ban. A
  fourth fixture has A/B/D fresh but E genuinely never ran this cycle: `_desk_publication_ready`
  computes and uses the no-E fallback inline, with no dependency on any pre-existing E artifact.
- E upstream-equality tests (the case the eighth review's finding exists to prevent): a full A–E cycle
  publishes, then A alone is rerun **with no pack change at all**, so every pack hash — including E's
  own recorded ones — is byte-identical before and after. Assert that the resulting snapshot carries
  `"E": null` and `synthesis_source: "fallback_no_e"`, *not* `"claude_e"`: E's pack fingerprint still
  matches (which is why a fingerprint-only check wrongly accepted it), but its
  `upstream_publication_ids["A"]` names the superseded A publication, so the upstream-equality
  condition correctly rejects it. A companion fixture reruns the full cycle so E actually sees the new
  A, and asserts `synthesis_source` returns to `"claude_e"` with `upstream_publication_ids["A"]`
  equal to the snapshot's `publications["A"]`. A third asserts the same rejection when only C is
  rerun and E's pinned C differs from the selected one, proving C participates in the equality check
  whenever it is included in `publications`.
- Desk-commit-ordering tests: a simulated crash immediately after `desk_snapshot.json` is repointed
  but before the corresponding `desk_snapshot_history.jsonl` line is appended leaves
  `desk_snapshot.json` correctly reflecting the new publish (proving the commit already happened) and
  leaves history one entry short rather than containing a false entry; a simulated crash *before*
  `desk_snapshot.json` is repointed (mid-computation of the new payload) leaves both `desk_snapshot.json`
  and history referencing only the previous, still-fully-valid publish — never a partial or
  about-to-be-published one.
- Reachability-retention tests, one per root: a `publication_id` referenced only by the *current*
  `desk_snapshot.json` survives a cleanup pass; one referenced only by an older entry still inside
  `desk_snapshot_history.jsonl`'s retention window survives; one referenced only by a retained
  `verdict_records` row survives; **one referenced only by a pass-level `current.json` — the A-only-
  rerun case, unreachable from the desk snapshot by design — survives, and its
  `latest_preview.md`/`latest_preview.json` remain readable afterward** (the case the eighth review's
  finding exists to prevent); one referenced by nothing in any of those roots survives if newer than
  the age cutoff and is deleted only once neither reachable nor within the age window — proving
  retention is the intersection of "unreachable" and "old enough," not either alone, so a very old but
  still-cited directory is never deleted purely for being old, and a brand-new directory from an
  in-progress desk cycle is never deleted purely for being momentarily unreachable.
- Transitive-retention tests: an E publication retained *only* via `desk_snapshot_history.jsonl`
  (long since superseded as current) pins A/B/D publications that appear in no other root; assert all
  of them survive cleanup, and that resolving every `cites` entry in that retained E still succeeds
  afterward — proving the retained set is closed under citation rather than a flat union of roots. A
  companion fixture drops that E out of the history window entirely and asserts its
  now-uncited upstream publications become eligible for deletion on the normal age schedule, proving
  the closure shrinks correctly rather than retaining everything forever.
- Legacy-projection tests: `chatgpt_a.md` and `reasoning_status.json` are correctly rebuilt from
  `desk_snapshot.json` after the projection step is deliberately skipped (simulating a crash between
  atomic publish and projection), proving no data is lost when the non-authoritative path fails; the
  projection step run twice in a row is idempotent and produces byte-identical legacy files both
  times; a single-pass rerun of A that updates A's `current.json` without advancing
  `desk_snapshot.json` leaves `chatgpt_a.md` unchanged, and the rerun's actual output is visible only
  in `latest_preview.md`/`latest_preview.json` under that pass's own directory —
  `latest_preview.json` carrying `"authoritative": false` and `latest_preview.md` carrying the
  equivalent fixed banner as its first line.
- Concurrency tests: two threads/processes racing a per-pass publish for the same pass, with the
  second forced to contend for `.writer_lock`, produce exactly one final `current.json` (whichever
  acquired first) and no corrupted or partially-written versioned directory from the loser; a desk-
  level publish holding the lock blocks a concurrent retention pass from starting until it releases,
  proving cleanup can never observe a directory mid-write.
- Lock-ordering tests: every code path that reads the pack fingerprint is asserted (by instrumenting
  the two acquire calls and recording their order) to take `.daily_job_lock` before `.writer_lock`,
  and never the reverse — the property that makes deadlock structurally impossible rather than
  merely improbable; a `daily_job` run holding `.daily_job_lock` is shown to block
  `_desk_publication_ready` from starting, proving a pack rebuild cannot land between step 1's hash
  and step 3's check; the projection and retention paths are asserted to acquire `.writer_lock`
  *only*, never `.daily_job_lock`, confirming they stay off the ordered path entirely.
- Stale-lock recovery tests: a sentinel left by a killed same-host writer, with `owner.json` naming a
  PID that is no longer running and an `acquired_at` older than `stale_lock_after`, is successfully
  broken and reacquired by the next writer — the case a prior draft wrongly asserted should
  permanently fail; the same sentinel with a *live* PID is not broken, regardless of age; a sentinel
  whose `owner.json` names a different `hostname` is never broken automatically at any age, and the
  raised error names the holding host, PID, and age; a sentinel directory with no `owner.json` at all
  is breakable once older than `stale_lock_after`; two contenders racing to break the same stale lock
  produce exactly one winner, with the loser observing a normal held lock rather than a half-broken
  one; `--break-stale-lock` reports the correct breakable/not-breakable verdict and reason for each of
  the above without mutating anything in the not-breakable cases.
- Rebuild-exclusion tests: running `pack.py`'s rebuild cleanup with `verdicts/` populated (versioned
  directories, both `current.json` levels, `desk_snapshot.json`, history) does not raise
  `IsADirectoryError` and leaves every file under `verdicts/` byte-for-byte untouched; a rebuild that
  changes `game_totals.csv`'s content (and therefore the pack fingerprint) leaves existing
  publications on disk unmodified — they simply stop satisfying `_desk_publication_ready` until fresh
  publications land, and are reclaimed only by the ordinary retention pass on its own schedule, not by
  the rebuild itself; `pack.DERIVED_PACK_OUTPUTS` is asserted to contain no entry that resolves under
  `packs/<date>/verdicts/`.

## Build order

0. Land the market-policy corrections, since every later fixture encodes them: remove total bases
   from `pack.ROLE_BLOCK`'s high-variance list, and add the "whitelisted ≠ recommendable, scope
   matters" distinction — including the player/team BB split — to `docs/ENT-SYNC-GLOBAL-PROMPT.md`,
   then propagate to `AGENTS.md`, `CLAUDE.md`, `GROK.md`, `GEMINI.md`, and the user-home copies with
   a sync run rather than by hand. `ROLE_BLOCK` is part of every runner's `request_sha256`, so this
   invalidates existing desk caches by design — expect the next desk run to be a full re-run, and land
   this off-slate rather than mid-run.
1. `verdicts.py` — the three envelope schemas (verdict, finding, reconciliation), shared version
   constant, strict parser per kind, and the JSON Schema emitter for each. Tests first.
2. `pack_index.py` — canonical-identity index over an existing pack fixture, including the
   empty-`outcome_id` totals handling and the full three-hash pack fingerprint; no validator
   dependency.
3. `prompts/A.md` §3 totals-identity amendment, plus the same wording in B/C/D/E; C's prompt also
   gains the JSON finding-envelope shape in place of the `FINDING |` line format.
4. `verdict_gate.py` — gates in the table order above, one test-driven commit per code group,
   including the pass-failing-vs-judgement violation classification and the scoped
   `desk_prohibited_markets` check.
5. `runner_common.py` — `request_structured`, `parse_envelope`, `write_envelope`, repair-block
   construction, request-hash extension (all three pack hashes), `publication_id` derivation as the
   full per-pass manifest hash (envelope, violations, Markdown fragment, and status fragment — not
   envelope-and-violations alone; see
   [The per-pass immutable manifest](#the-per-pass-immutable-manifest-and-what-publication_id-actually-covers)),
   the versioned-directory publish path and pass-level `current.json` pointer swap (replacing
   `run_desk.run_phase`'s single-file snapshot/restore for these artifacts), and the migration of A's
   duplicated request-hash mechanics. `run_desk.orchestrate_desk`'s new final step —
   `desk_snapshot.json` publish, the legacy-projection step, and reachability-based retention — is
   deliberately sequenced later, as step 12, since it depends on every pass's publish path and the
   no-E fallback module (step 11) existing first.
6. Pass C first — replace `c_research.validate_output`/`_market_index` with the shared gate against
   the finding envelope. C is the pass with a working, tested contract today, so it is the cheapest
   place to prove the shared gate reproduces existing behavior before any pass depends on it for
   something new. `tests/test_c_research.py`'s existing fixtures are the acceptance bar.
7. A one-off recorded probe of grounded Gemini structured output against `google-genai` 2.10.0,
   written up in this document, before B's wiring (C's fallback-parser path from step 6 already
   covers the case where the probe says native structured output is unavailable).
8. Pass A end to end with a fake OpenAI client (verdict envelope), including artifacts and status.
9. Pass B (with its new candidates input and verdict envelope), then D.
10. Pass E — reconciliation envelope consuming A/D/B's validated verdicts and C's validated findings;
    `unsourced_synthesis` and the upstream-stake-ceiling gate are exercised here for the first time,
    against `record_id`-keyed `cites`.
11. `verdict_report.py`, including the no-E deterministic fallback reconciliation
    ([Deterministic Markdown](#deterministic-markdown)), and the rewrite of
    `run_desk.produce_manual_betting_report` to render from the appropriate validated envelope set
    when present, falling back to today's behavior when absent. This precedes step 12 because the
    desk-level publish depends on the fallback module already existing and recording which
    `publication_id`s it read.
12. The locking layer, landed first since every remaining step in this list depends on it: the
    `.writer_lock` primitive (reusing `daily_job._acquire_writer_lock`'s idiom), the `owner.json`
    metadata and stale-recovery rules including `--break-stale-lock`, and the fixed
    `.daily_job_lock`-before-`.writer_lock` acquisition order for every path that reads the pack
    fingerprint — all per [Concurrency](#concurrency) and
    [Stale-lock recovery](#stale-lock-recovery). Then `_desk_publication_ready` — the purpose-built
    check requiring both live-fingerprint freshness *and*, for E, upstream-publication equality with
    step 2's selections; never a reuse of `e_usable`/`required_usable` — and the desk-level publish it
    gates: `desk_snapshot.json` (commit first, then append `desk_snapshot_history.jsonl`, never the
    reverse) built from E's `upstream_publication_ids` plus E's own `publication_id`, or the no-E
    fallback's own resolved set plus `"E": null`; the legacy-projection step (`chatgpt_a.md`/
    `reasoning_status.json` rebuilt only from the current desk snapshot, never a pass-level pointer)
    and its `latest_preview` counterpart (rebuilt from a pass's own `current.json`, explicitly marked
    non-authoritative); and the retention pass, whose roots include every pass-level `current.json`
    target and whose retained set is closed transitively over citations — all lock-held, all per
    [The desk-level snapshot](#the-desk-level-snapshot) and [Retention](#retention).
13. `pack.py` rebuild-path exclusion: confirm `packs/<date>/verdicts/` is never named in
    `DERIVED_PACK_OUTPUTS` and add the guard test asserting that (see
    [Artifacts and status](#artifacts-and-status)) before this lands, since it is what prevents the
    very first post-merge rebuild from crashing on a directory `unlink()`.
14. `config/verdict_policy.json`, `run_desk` status block, `daily_job`
    summary, `verdict_records` table and migration (keyed by `record_id` and `publication_id`, and
    including the `C_verdict` precedence reduction).
15. Docs: `docs/feedback-loop.md` cross-reference, `AGENTS.md` invariant entry,
    `.agent-log/HANDOFF.md`.

Steps 1, 2, 4, and 5 land with no behavior change to existing desk output, so the gate can be merged
and reviewed before any pass depends on it.

## Non-goals

- No change to how any number is computed. `pack.py`, `sizing.py`, `probability_blend.py`, and
  `portfolio.py` keep sole authority over probabilities, edges, and caps.
- No new provider, model, or paid call. Existing model selection is unchanged.
- No scoring of model verdicts against settled results.
- No `E_verdict` column in `decisions.csv`.
- No change to the exported manual prompt packs or the analyze-prompt skills beyond the prompt
  amendments in step 3 (totals identity, and C's JSON finding-envelope shape).

## Review resolutions

Revision 1 was reviewed externally. Each finding, verified against the code in this worktree and the
installed SDKs:

| Finding | Status | Where addressed |
| --- | --- | --- |
| B and E lack the inputs the envelope requires | **Accepted.** `gemini_research.py:85` confirms B receives only briefing + totals; `claude_synthesis.py:32` confirms E consumes Markdown. | [Per-pass input contracts](#per-pass-input-contracts) |
| Totals identity inconsistent with the proposed index | **Accepted.** `game_totals.py:848,857` sets `outcome_id = totals_id`; `A.md:78` tells models to emit `totals_id` as `market_id`. | [Canonical verdict identity](#canonical-verdict-identity) |
| Shadow mode contradicts the fail-closed summary | **Accepted.** Revision 1 asserted both. | [Modes, and what shadow mode may never do](#modes-and-what-shadow-mode-may-never-do) |
| Prompt C assigned to the wrong provider | **Accepted — my error.** `c_research.py:46` delegates to `gemini_research.call_gemini`, and `run_desk.PHASE_KEYS["C"]` is `GEMINI_API_KEY`. C is Gemini, not OpenAI. | [Provider integration](#provider-integration) |
| Unresolved market policy must not ship as defaults | **Accepted, then resolved.** Revision 2 removed the defaults and blocked `enforce`; the owner ruled on 2026-08-12 and revision 3 encodes the ruling rather than a default. | [Resolved market policy](#resolved-market-policy) |
| Unknown-player gate bypassable through prose | **Accepted for the structured evidence channel.** Player claims must bind to `player_id`, and rendered table names come from the index. This round's fix overstated the reach of that guarantee to prose generally; corrected in round 3 below. | [The deterministic gates](#the-deterministic-gates), [Two invariants, not one](#two-invariants-not-one) |
| Per-pass verdict storage needs separate cardinality | **Accepted.** `feedback.DECISION_FIELDS` (`feedback.py:86`) has fixed A–D verdict columns; a singular `pass` column would corrupt the grain. | [Verdict persistence](#verdict-persistence) |
| Multi-file publication is not atomic | **Accepted.** `run_desk.py:83` restores only the phase Markdown. | [Atomic multi-file publication](#atomic-multi-file-publication) |

One correction to the review that produced revision 2. It recommended "the currently documented
`response_format` form" for Gemini. `types.GenerateContentConfig` in the installed `google-genai`
2.10.0 exposes `response_mime_type`, `response_schema`, and `response_json_schema`; there is no
`response_format` field. Revision 1's field names were correct. The review's underlying concern was
still valid and was adopted: the *grounded* path is the unproven one, so B and C treat native
structured output as best-effort behind the strict parser, gated on a recorded probe. Confirmed the
same way: `openai` 2.44.0 `Responses.create` accepts `text`, and `anthropic` 0.112.0 `Messages.create`
accepts `tools` and `tool_choice`.

### Round 2 (revision 3 → revision 4)

A second review, after the owner's market-policy ruling landed in revision 3, found revision 3 still
not implementation-ready. Each finding, re-verified against this worktree's code:

| Finding | Status | Where addressed |
| --- | --- | --- |
| C is still forced through the betting envelope, deleting its only working structured output | **Accepted — this was the central defect.** Revision 3 named C as "the precedent" and then specified one `BET`/`PASS`/`STAND_DOWN` schema for every pass, silently replacing C's live `CONFIRMS`/`CONTRADICTS`/`NEUTRAL`, no-stake contract. | [Three envelope kinds](#three-envelope-kinds), build order step 6 |
| `outcome_id`-only index crashes on real totals rows | **Accepted, and reproduced.** Read `game_totals.py:969-980`: `_empty_row` sets `totals_id` but never `outcome_id`, which is left `""`. Confirmed this is the routine `INSUFFICIENT_DATA` path, not an edge case — every thin-ladder totals market hits it. | [Authoritative index](#authoritative-index) |
| Pack fingerprint pinned only `candidates_sha256`; totals could drift under a matching hash | **Accepted.** The index and every envelope now carry and check all three pack hashes. | [Authoritative index](#authoritative-index) |
| E misclassified as pack-only for injuries, making B/C's research decorative | **Accepted.** E's injury gate now has a third path: cite a validated B/C finding. | [The deterministic gates](#the-deterministic-gates) (injury row) |
| Pass success too weak — one clean verdict plus a pile of demoted fabrications could still exit 0 | **Accepted.** Violations are now split into a pass-failing class (identity/tamper — always fails, any ratio) and a judgement-outcome class (subject to `reject_fail_ratio`). | [Repair loop and failure policy](#repair-loop-and-failure-policy) |
| Team BB scope unstated; §5.3's "BB" plausibly means the player prop only | **Not part of the original finding — raised as an open question by the reviewer, with a recommendation.** Put to the repo owner directly; ruled 2026-08-12: player BB banned, team BB recommendable. | [Resolved market policy](#resolved-market-policy) |

The reviewer's "do not go straight to step 1" instruction is reflected in the build order: step 0
(market policy) and step 1 (the now-three-kind schema) both changed as a direct result of this round,
and pass C — the working precedent — moved ahead of pass A in sequencing (step 6, before the
Gemini-grounding probe in step 7) specifically because it is the cheapest place to prove the shared
gate reproduces a contract that already has passing tests, before any new pass depends on the gate
for something it cannot yet fall back and check against.

### Round 3 (revision 4 → revision 5)

A third review, run against the exact on-disk revision 4 text (confirmed by SHA-256 before this
round's edits began: `f958a97d7b7b9843b5177cbabf8efec56b15f54c8cdc6f8f1c29d8c05c180831`), found five
more blockers. Each was re-verified against the cited lines and against the actual code before being
accepted:

| Finding | Status | Where addressed |
| --- | --- | --- |
| Publication described as atomic but wasn't — final files were moved before the manifest, leaving a window where the old manifest points at partially overwritten files | **Accepted.** The staging-plus-manifest design is replaced with immutable versioned directories and a single atomically-swapped `current.json` pointer — the reviewer's exact recommendation, and simpler than what it replaces. | [Atomic multi-file publication](#atomic-multi-file-publication) |
| C findings had no stable identity — multiple findings per `outcome_id` collide in E's `cites` and in `verdict_records`'s primary key | **Accepted, and reproduced.** Nothing in the finding-envelope shape ever prevented C from emitting two findings on one market; the schema simply never gave either a citable identity. | [Three envelope kinds](#three-envelope-kinds) (derived `record_id`), [Verdict persistence](#verdict-persistence) |
| Invented names and unsupported claims still have a path to the rendered report through free-form prose, contradicting the plan's own "no path to output" claim | **Accepted — the invariant as stated was false.** `claim`, `contradictions`, E `narrative`, `slate_notes`, and `needs` were never entity-bound; only structured player evidence was. Rather than attempt full NLP fact-checking (which the round-2 review already correctly identified as a bypassable heuristic when proposed as `warn`-only name matching), the plan now states two separate, accurate invariants and keeps the cheap heuristic explicitly labeled as a non-authoritative signal. | [Two invariants, not one](#two-invariants-not-one) |
| The no-E fallback ("union of A/D/B") was unspecified — no rule for conflicting verdicts or stakes across passes | **Accepted.** Replaced with a deterministic, code-computed reconciliation: any validated stand-down wins, otherwise unanimous `BET` required, stake is the minimum across contributing passes, and the report is labeled `fallback_no_e` so it's never mistaken for E's own synthesis. | [Deterministic Markdown § The no-E fallback reconciliation](#the-no-e-fallback-reconciliation) |
| Voluntary `PASS`/`STAND_DOWN` verdicts counted toward `reject_fail_ratio`, so a correctly cautious all-stand-down slate could fail the pass | **Accepted, and the defect was worse than described**: re-reading the cited text found it self-contradictory, not just under-specified — it said judgement outcomes "never fail the pass" and "count toward `reject_fail_ratio`" in the same sentence, while a separate sentence said exceeding that ratio does fail the pass. Fixed by computing the ratio only over the model's own attempted `BET`s; voluntary `PASS`/`STAND_DOWN` never enters either side of the ratio, at any count. | [Repair loop and failure policy](#repair-loop-and-failure-policy) |
| E's schema contradicts the shared contract — its example omits both totals hashes and every echoed identity field the plan says every envelope carries | **Accepted.** This was an unintentional omission in the example, not a documented exception; fixed by adding the missing fields and stating explicitly that E's identity/tamper gates run exactly as A/D/B's do. | [Three envelope kinds § Reconciliation envelope](#reconciliation-envelope-pass-e) |

No corrections to this round's findings were needed — all five held on verification against the
code, and the `record_id` fix subsumes the E-schema fix's `cites` shape, so both were implemented
together rather than as two separate schema changes.

### Round 4 (revision 5 → revision 6)

A fourth review, run against the exact on-disk revision 5 text (confirmed by SHA-256 before this
round's edits began: `e125b33b4544095b1736fb9ea9100e3f93d84f6fc5424d7843384e05ba728662`), found four
more blockers, all in the mechanisms round 3 introduced to fix the previous round's findings — the
fixes themselves needed a second pass, not the surrounding design:

| Finding | Status | Where addressed |
| --- | --- | --- |
| Publication identity conflicts with forced refreshes — an existing directory keyed on `request_sha256` was treated as an immutable cache hit, but `force=True` unconditionally re-calls the provider regardless of hash match | **Accepted, and reproduced.** Read `reasoning.py:108-112` and `run_desk.py:97-100`: `force` deletes the existing output and re-calls the provider independent of `request_sha256`. A changed forced response would have collided with or been silently discarded by round 3's design. | [Atomic multi-file publication](#atomic-multi-file-publication) (`request_sha256` vs `publication_id`) |
| `record_id` remains order-dependent — deriving it from array position means repair-round reordering changes the identity of records nothing about the repair actually touched | **Accepted.** Re-reading round 3's own derivation confirmed it: `f"{pass}:{outcome_id}:{i}"` with `i` an array index is exactly as position-dependent as the citation instability round 2 first flagged, just relocated from "no ID at all" to "an ID that isn't stable." | [Three envelope kinds](#three-envelope-kinds) (content-hash `record_id` with content-sorted duplicate disambiguation) |
| The no-E fallback is not total or sufficiently fail-closed — a `BET`/`PASS` mix satisfies neither rule, and a single surviving `BET` from a pass that never had corroborating input can publish | **Accepted, and worse on inspection than the summary suggested**: re-deriving the truth table for all `(A, D, B)` outcome combinations showed the two-rule design left an actual gap (not just an edge case) uncovered, and "every pass that produced a verdict said `BET`" is vacuously satisfied by exactly one contributing pass. | [The no-E fallback reconciliation](#the-no-e-fallback-reconciliation) (explicit quorum requirement, total four-branch rule) |
| Legacy `C_verdict` projection is undefined for C's multi-finding-per-outcome shape | **Accepted.** Confirmed `decisions.decision_id` is a scalar-per-decision primary key (`feedback.py:593`), so a single column cannot hold several findings' worth of verdicts without a defined reduction. Fixed with a precedence rule (`CONTRADICTS` > `CONFIRMS` > `NEUTRAL`) rather than retiring the column, since existing readers depend on it and the child table already holds the full detail. | [Verdict persistence](#verdict-persistence) |

Two structural consequences followed from the first finding once it was accepted, not called out as
separate findings by the review but necessary to keep the plan internally consistent: `record_id`'s
citation shape had to move from `(pass, record_id)` to `(pass, publication_id, record_id)` for the
same reason the filesystem layer needed both fields, which produced a new gate (`stale_citation`);
and the `verdict_records` primary key, artifact file names, and `reasoning_status.json` fields that
referenced `request_sha256` as if it uniquely named a response were updated to `publication_id`
wherever that was the actual claim being made. Both changes are downstream of the same root cause the
review identified, not independent fixes.

### Round 5 (revision 6 → revision 7)

A fifth review, run against the exact on-disk revision 6 text (confirmed by SHA-256 before this
round's edits began: `44bf78460168d3e56f7ee9d4689799b73ba9991fa39c1f579ad1d5ea342ee4d4`), found the
per-pass publication fix from round 4 was directionally correct but stopped one layer too early —
every finding here is about what happens *around* a correctly-atomic per-pass publish, not about the
per-pass mechanism itself:

| Finding | Status | Where addressed |
| --- | --- | --- |
| No coherent desk-level snapshot — E pins upstream publication IDs, but readers independently resolve each pass's own `current.json`, so a later A/B/C/D rerun can make an already-published E stale or mix generations | **Accepted, and this was the sharpest finding of the round.** Round 4's fix made each pass's own publish atomic but never asked whether five independently-resolved atoms add up to one coherent whole; they don't, the moment any pass reruns after E has already validated against it. | [The desk-level snapshot](#the-desk-level-snapshot) |
| `publication_id` does not identify the immutable directory's complete content — it hashes only envelope and violations, while the directory also contains the Markdown fragment and (now) status | **Accepted, and reproduced the concrete failure**: a renderer fix, republished with unchanged envelope/violations, would compute the same `publication_id` as before and then treat the differing Markdown as a no-op write under "idempotent because identical" — which is false in that case. | [The per-pass immutable manifest](#the-per-pass-immutable-manifest-and-what-publication_id-actually-covers) |
| Status and compatibility files remain outside the atomic transaction — `reasoning_status.json` is updated separately, and legacy flat files retain today's non-atomic write path | **Accepted.** Status is now `status_fragment.json`, one of the four files covered by the same manifest hash as everything else in the publish. Legacy files are redefined as an explicitly non-authoritative, safely-re-runnable projection *of* the atomic snapshot, rather than a second independent write path that can drift from it. | [Artifacts and status](#artifacts-and-status) |
| Cleanup can destroy cited evidence — age-only pruning may delete a non-current upstream publication still referenced by the current E or by a persisted decision | **Accepted.** Retention is redefined as the union of everything reachable from the current desk snapshot, the bounded snapshot history, and anything a still-retained `verdict_records` row cites — deleted only if outside that reachable set *and* past the age cutoff, not either condition alone. | [Retention](#retention) |
| One test still contradicts the stated prose boundary — an adversarial fixture asserts an invented prose name "cannot reach the rendered report," directly contradicting Invariant B | **Accepted — this was a real leftover, not a restatement.** Tracing it down confirmed it predated the round-3 invariant split and was never updated when Invariant B was written; a separate, correct "Prose-invariant tests" bullet already existed alongside the stale one. Removed the contradiction rather than leaving two bullets asserting opposite things. | [Testing](#testing) |

No corrections to this round's findings were needed. Every retention, manifest, and snapshot change
in this revision follows directly from the four P1/P2 findings; nothing here is a design choice made
independent of what the review actually asked for.

### Round 6 (revision 7 → revision 8)

A sixth review, run against the exact on-disk revision 7 text (confirmed by SHA-256 before this
round's edits began: `504b3b0d19d2b69bd0a3be8a0af504370446071424362dd5d19debbb5092d0e5`), found five
gaps in the desk-snapshot mechanism the previous round introduced. Every finding here is about the
mechanism's edge behavior — partial runs, construction correctness, crash ordering, source-of-truth
discipline, and one prose/test mismatch — not about whether the mechanism was the right idea:

| Finding | Status | Where addressed |
| --- | --- | --- |
| Partial runs can publish an authoritative snapshot — the plan said publication follows "whichever phases were requested," while `orchestrate_desk` accepts an arbitrary phase subset | **Accepted, and reproduced.** Confirmed `run_desk.py:269`'s `steps` parameter accepts any subset of `{A, B, C, D, E}`; a single-pass rerun would have been eligible to publish a desk-level snapshot covering passes it never touched. Fixed by gating the desk-level publish on the same completeness condition `orchestrate_desk` already computes for its own `FULL` status (`e_usable and required_usable`, `run_desk.py:366-370`), reused rather than redefined. | [The desk-level snapshot](#the-desk-level-snapshot) |
| The snapshot cannot be copied verbatim from E's pin — E's `upstream_publication_ids` contains only A/D/B/C, never E's own ID | **Accepted — a real logical gap, not an edge case.** E cannot reference its own `publication_id` inside a field it computed before that ID existed. Fixed by defining construction explicitly as two sources, not one: E's `upstream_publication_ids` for the four upstream passes, plus E's own already-published `publication_id` added by the code assembling the snapshot; the no-E path is the fallback module's own record plus `"E": null`. | [The desk-level snapshot](#the-desk-level-snapshot) |
| History can record a snapshot that never committed — appending to `desk_snapshot_history.jsonl` before repointing `desk_snapshot.json` lets a crash between the two leave a false "published" entry | **Accepted.** Reordered to commit-then-log: `desk_snapshot.json` is repointed first (the atomic primitive already used throughout this design), and only after that succeeds does the same code append to history. A crash can now leave history short by one entry; it can never contain an entry for something that was not actually committed. | [The desk-level snapshot](#the-desk-level-snapshot) |
| Legacy projections have two conflicting sources — declared projections of the committed desk snapshot, but also rebuildable after a pass-level pointer moves, so an A-only rerun could make `chatgpt_a.md` disagree with the authoritative snapshot | **Accepted, and this was the one that mattered most**: it silently reopened the exact coherence gap the desk-level snapshot exists to close, just at the legacy-file boundary instead of the pointer-resolution boundary. Fixed by restricting the legacy-named compatibility files to derive only from `desk_snapshot.json`; a pass's fresher, not-yet-reconciled output is now visible only through separately-named, explicitly-marked-non-authoritative `latest_preview.md`/`latest_preview.json` files. | [Artifacts and status](#artifacts-and-status) |
| Retention prose contradicts its own tests — the implementation text said "delete every unreachable directory" with no age qualifier, while the tests required an age grace period on top of reachability | **Accepted — the prose and the tests genuinely disagreed, not just under-specified.** Stated the rule once, precisely, as a conjunction: delete only when both unreachable and past the age cutoff, with the grace period justified by the concrete race it prevents (a just-published, not-yet-referenced directory from an in-progress desk cycle). | [Retention](#retention) |

One thing found while fixing the fourth item, not itself a review finding: the double-hyphen anchor
bug from round 4's fix (`#reconciliation-envelope--pass-e` instead of the single-hyphen form GitHub's
slugger actually produces for an em-dash heading) had been silently reintroduced in this round's own
draft edits before the anchor checker caught it. Re-verified with the same anchor-resolution script
used in every prior round before finalizing; all links resolve.

### Round 7 (revision 8 → revision 9)

A seventh review, run against the exact on-disk revision 8 text (confirmed by SHA-256 before this
round's edits began: `425ab4e9b093f112ed0185f4913374dc4ae65d75e261893d3b1a7b610f01475f`), found that
round 6's fixes for the desk-snapshot mechanism did not survive contact with the actual implementation
hooks they named:

| Finding | Status | Where addressed |
| --- | --- | --- |
| The completeness gate still permits subset publication — reusing `e_usable`/`required_usable` inherits their fallback to bare flat-file existence for any phase outside the current invocation's `steps` | **Accepted, and reproduced exactly.** Read `run_desk.py:349-361`: for a phase not in `requested`, both variables fall back to `(pack_dir / PHASE_OUTPUTS[phase]).exists()`. After a prior full run left `gemini_b.md`/`claude_d.md` on disk, `orchestrate_desk(steps=["A"])` would see `required_usable = True` from those old files and advance `desk_snapshot.json`, having touched only A. Round 6's fix reused the exact predicate whose stale-tolerance was the round-5-and-6 bug in the first place, one call site removed. | [The desk-level snapshot](#the-desk-level-snapshot) |
| That predicate cannot represent the no-E fallback — `e_usable` means "E succeeded, or an E file already exists," with no `fallback_no_e` state | **Accepted.** `e_usable`/`required_usable` were never designed to model this; they answer a different, older question ("does this pack have outputs from *some* run"). Replaced with `_desk_publication_ready`, purpose-built to check current-publication freshness against the live pack fingerprint and to treat the deterministic fallback as always-computable rather than as a stored artifact. | [The desk-level snapshot](#the-desk-level-snapshot) |
| Desk publication lacks mutual exclusion — snapshot-swap-then-history-append is safe for one writer only; concurrent runs and the standalone projection command are additional writer paths | **Accepted.** Commit-then-log (round 6's fix) prevents a false history entry from a single writer's own crash; it does nothing about two writers interleaving. Added one advisory writer lock, reusing `daily_job._acquire_writer_lock`'s existing `mkdir(exist_ok=False)` idiom (`daily_job.py:234-251`) rather than inventing a new locking primitive, held across every per-pass publish, the desk-level publish, both projection paths, and retention. | [Concurrency](#concurrency) |
| Pack rebuild cleanup conflicts with version retention — the plan added versioned directories to `DERIVED_PACK_OUTPUTS`, but the rebuild loop calls bare `.unlink()` on every entry | **Accepted, and reproduced.** `pack.py:1747-1748` is `for name in DERIVED_PACK_OUTPUTS: (out_dir / name).unlink(missing_ok=True)` — `Path.unlink()` raises `IsADirectoryError` on a directory, so the first rebuild after this plan landed would crash outright; even the single-file entries, if added, would have let a routine rebuild discard the coherence contract through a code path that consults none of the retention rules. Excluded `packs/<date>/verdicts/` from `DERIVED_PACK_OUTPUTS` entirely; a pack-fingerprint change is handled by the existing reachability mechanism (old publications simply stop matching), not by a rebuild-time deletion. | [Artifacts and status](#artifacts-and-status) |
| Exact-duplicate `record_id` disambiguation has no intrinsic ordering — byte-identical records have identical values for every proposed sort key | **Accepted — a real logic error, not an edge case.** The proposed sort key `(outcome_id, content_hash, json.dumps(record, sort_keys=True))` is, by construction, identical across byte-identical records; Python's stable sort then silently preserves input array order among ties, reintroducing the exact position-dependence content-hashing was meant to remove. Replaced disambiguation with collapsing: byte-identical records merge into one before `record_id` assignment, recorded as a `warn`-severity `duplicate_record_collapsed` note, removing the need for any tiebreak. | [Three envelope kinds](#three-envelope-kinds) |

No corrections to this round's findings were needed; all five held on verification against the code.
This round's fixes also prompted one unforced change: the revision-history preamble at the top of this
document, which had grown to a full paragraph per round across seven rounds, is now a table — the
prose form was becoming a genuine readability cost for anyone opening this file fresh, independent of
anything a reviewer flagged.

### Round 8 (revision 9 → revision 10)

An eighth review, run against the exact on-disk revision 9 text (confirmed by SHA-256 before this
round's edits began: `67c81b7d462fed951504f0cd676c1ab486a06e8996bf7e7ced22347e8fbd9a1f`), found five
boundary gaps. Two of them (#1 and #5) are outright logic errors in revision 9's own text rather than
under-specification — the kind that are only visible once the surrounding mechanism is concrete enough
to reason about end to end:

| Finding | Status | Where addressed |
| --- | --- | --- |
| E freshness ignores the publications it reconciled — E accepted on pack-hash freshness alone, without requiring its pinned A/B/D/C IDs to match step 2's selections | **Accepted — and revision 9 was self-contradictory here, not merely incomplete.** Its own worked example claimed an A-only rerun would advance the snapshot with the new A, while its E check would have accepted the old E (whose pack hashes are unchanged, since rerunning A touches no CSV) and published `synthesis_source: "claude_e"` alongside an A that E never saw. Added upstream-publication equality as a second, independent freshness condition, and corrected the worked example to show the fallback correctly taking over. | [The desk-level snapshot](#the-desk-level-snapshot) |
| The verdict lock does not protect the live pack inputs — readiness hashes pack CSVs that `daily_job` mutates under a different lock | **Accepted.** Revision 9 explicitly argued the two locks "protect different resources," which is false: `_desk_publication_ready` step 1 hashes exactly the CSVs `daily_job` rewrites. Replaced lock independence with a fixed global acquisition order (`.daily_job_lock` before `.writer_lock`) for every fingerprint-reading path, with the projection and retention paths staying off that path entirely. | [Concurrency](#concurrency) |
| Retention roots omit pass-current and transitive citation dependencies | **Accepted, both halves.** Cleanup could delete the target of `A/current.json` after a subset rerun (unreachable from the desk snapshot *by design* in that state) breaking `latest_preview`, and could retain an E from the history window while deleting the upstream publications that E's own `cites` entries name. Added pass-level `current.json` targets as a fourth root and made the retained set closed under citation rather than a flat union. | [Retention](#retention) |
| A crashed writer permanently bricks publication — the lock had bounded retry but no owner metadata or stale recovery | **Accepted, and revision 9 encoded the broken behavior as an intended test assertion**, which is how it survived a round. Added `owner.json` (PID, hostname, timestamp, operation), a same-host-dead-PID-past-timeout break rule, an explicit never-auto-break rule for cross-host holders (these packs live on OneDrive-synced paths shared across machines, per `docs/ENT-SYNC-GLOBAL-PROMPT.md`), and a `--break-stale-lock` operator command. | [Stale-lock recovery](#stale-lock-recovery) |
| `record_id` is not unique by construction — SHA-256 truncated to 12 hex characters | **Accepted.** Collapsing identical records, added last round, addresses identical content sharing an ID; it says nothing about two *different* records agreeing in the first 48 bits, which would collide in `cites` resolution and in `verdict_records`' primary key. Switched to the full digest — the claim in this document is "unique by construction," and a 48-bit prefix delivers a probabilistic guarantee instead. | [Three envelope kinds](#three-envelope-kinds) |

Two of this round's fixes required correcting assertions elsewhere in the plan that had encoded the
buggy behavior as expected: the `_desk_publication_ready` worked example (finding #1) and the
stale-lock concurrency test (finding #4). Both are now corrected in place rather than left as
contradictions, and the corresponding tests assert the fixed behavior.
