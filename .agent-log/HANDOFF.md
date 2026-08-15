# Handoff

## Task

Implementing `docs/plans/2026-08-12-structured-ai-verdicts.md` (revision 10 — 8 external
review rounds, all resolved). The plan is long; read it, don't re-derive it. Steps 0-4 of
its 15-step Build order are done and committed. Continue at **step 11**.

## Last Commit SHA

`f759b6c` — `feat(verdicts): structured AI schema validation, steps 0-3`
plus the step-4 commit on this branch (`claude/structured-ai-schema-validation-afd7a9`),
pushed to origin. Step 4 lives in `outlier_scrapers/verdict_gate.py` and
`tests/test_verdict_gate.py`.

## PR

**Draft, do not merge yet**: https://github.com/DaSilvaDub/outlier/pull/97
Update its body/checklist as later steps land. It exists to make this branch's progress
visible across worktrees/ents, not because the work is ready for review.

## What's done (steps 0-3)

- **Step 0** — `outlier_scrapers/pack.py`'s `ROLE_BLOCK`: removed "total bases" from the
  high-variance line (now matches `A.md` §5.2). `docs/ENT-SYNC-GLOBAL-PROMPT.md` invariant
  #9: "whitelisted ≠ recommendable" — HRR/player-BB may enter packs but the desk may never
  BET them; team BB is unaffected. A guard test
  (`test_role_block_does_not_flag_total_bases_as_high_variance` in `tests/test_pack.py`)
  pins this so it can't silently regress.
- **Step 1** — `outlier_scrapers/verdicts.py`: the three envelope schemas (verdict/
  finding/reconciliation dataclasses), `SCHEMA_VERSION = "1.0"`, `parse_envelope(raw_text,
  kind)` (raises `EnvelopeUnparseableError` or `SchemaInvalidError`), content-derived
  `record_id` (full 64-hex SHA-256, never array position — a truncated/positional version
  was tried and rejected across earlier review rounds, see the plan's Round 6/7 history),
  duplicate-record collapsing, and `json_schema_for(kind)` (OpenAI strict-mode compatible:
  `additionalProperties: false` + every field in `required`, throughout, recursively).
- **Step 2** — `outlier_scrapers/pack_index.py`: `build_pack_index(pack_dir, *, now=None,
  policy_path=None) -> PackIndex`. Builds `rows` (outcome_id → IndexedRow, unioned across
  candidates/game_totals/team_totals), `dropped` (locked candidates, kept separate so the
  future `locked_market` gate can fire specifically), `unindexed_totals` (empty-outcome_id
  `INSUFFICIENT_DATA` rows — these must NOT raise on index build, see
  `game_totals._empty_row`), `players`, `injuries`, `locks`, `policy`. Raises
  `PackIntegrityError` on a duplicate non-empty `outcome_id` across any stream, or a header
  mismatch. **Known limitation, documented in the module**: `CANDIDATES_HEADER` has no
  dedicated player-name column, so `PlayerInfo.name` currently stores the raw `selection`
  text rather than a parsed clean name — revisit only if `verdict_report.py` (step 11)
  actually needs something cleaner; don't invent fragile text-parsing speculatively.
- **Step 3** — `prompts/A.md`, `B.md`, `D.md`, `E.md`: added `outcome_id` to the "use
  verbatim" field list; fixed the totals-identity line so `market_id` comes from the
  `market_id` column and `outcome_id` from `totals_id` (previously the prompt told models
  to use `totals_id` *as* `market_id`, which is exactly the ambiguity the plan's Canonical
  verdict identity section exists to remove). `prompts/C.md`: same totals-identity fix,
  plus replaced the undocumented `FINDING | key=value | ...` pipe format with the JSON
  finding-envelope shape matching `verdicts.py`'s `finding` schema.
  **Dependency for step 6**: `C.md` now tells the model that `pack_date`/
  `candidates_sha256`/`game_totals_sha256`/`team_totals_sha256` are "supplied to you in
  the input" — but `outlier_scrapers/c_research.py`'s current `research_input`
  construction does not actually include them yet. Step 6 (C's runner rewiring) must add
  them to the text sent to the model, or the prompt's promise is false.

## What's done (step 4)

- `outlier_scrapers/verdict_gate.py`: `validate_envelope(envelope, index, now)` — no I/O,
  no clock reads, no network. Envelope-level gates (`envelope_unparseable`, `schema_invalid`,
  `pack_mismatch`) short-circuit. Per-record gates cover the plan table: identity/tamper,
  players, injury (A pack-flags / B-C grounded / E cite-only), lock, integrity, variance,
  scoped `desk_prohibited_markets` (HRR any-scope, BB `PLAYER_PROP` only, team BB clean),
  MLB whitelist after the desk ban, 2B OVER `side_restricted`, longshot `+150`/`+149`,
  and per-row stake caps / increment / negative. PASS/STAND_DOWN skip stake+variance
  but not identity/tamper. Findings have no stake to violate.
- Failure classes are hard-coded: identity/tamper fail the pass on any record;
  judgement rejects count toward `reject_fail_ratio` only on model-attempted `BET`s;
  a zero-BET all-`STAND_DOWN` slate never trips the ratio.
- 54 tests in `tests/test_verdict_gate.py`. `ruff`/`mypy`/`pyright` clean on the new files.
- **Still open inside step 4 (reviewer, not blocking step 5 start):**
  envelope-level `portfolio.allocate_portfolio_risk` group ceilings; C
  `source_timestamp` window (still in `c_research.validate_output` until step 6);
  E stake-narrowing vs min(cited upstream stakes). Per-row caps, scoped desk
  bans, identity/tamper vs judgement, priced_line+other-tamper, totals lock via
  `index.locks`, PASS/STAND_DOWN lock+integrity, and player-scoped injury
  flags are in.

## What's done (step 5)

- `runner_common.request_structured` / `parse_envelope` / `write_envelope` /
  `build_repair_block` / `structured_request_fields` / `publish_pass`.
- `publication_id` is `sha256(canonical_json(manifest))` over all four files
  (`verdicts.json`, `violations.json`, `report_fragment.md`, `status_fragment.json`)
  plus `pass` / `request_sha256` / `schema_version`. A renderer-only change
  produces a new ID. `verdicts.json` does **not** embed `publication_id` (that
  would be circular with the hash).
- Publish writes `<publication_id>.tmp-<pid>` then `os.replace`s the directory,
  then atomically swaps `verdicts/<pass>/current.json`. Identical republish is a
  no-op (`wrote=False`). Two different envelopes under the same request hash
  get two directories; current points at the latest.
- Pass A (`outlier_scrapers/reasoning.py`) now hashes via
  `rc.compute_request_hash` and includes `structured_request_fields` (three pack
  hashes + `schema_version`). This invalidates existing A cache hashes by
  design.
- 13 new tests in `tests/test_runner_common.py`. `run_desk.run_phase`'s
  markdown snapshot/restore is still in place — runners are not yet publishing
  through this path (that is steps 6–9). Desk snapshot is step 12.

## What's done (step 6)

- `c_research.validate_output` now parses a finding envelope (JSON, or the
  legacy `FINDING |` pipe format converted in-memory) and runs
  `verdict_gate.validate_envelope`. Gate reject codes are mapped back to the
  existing RunnerError messages so the old acceptance tests stay valid.
- C's `source_timestamp` window (`pack_date-2d .. +1d`) lives in
  `verdict_gate._check_finding_timestamp`. Unparseable pack dates still skip
  the window only.
- `run_c_research` builds a real `PackIndex`, puts `pack_date` + the three
  pack hashes into `research_input`, and includes `structured_request_fields`
  in the request hash.
- Existing `tests/test_c_research.py` fixtures remain the acceptance bar
  (pipe format). One new test covers a native JSON finding envelope.
- 70 tests passed (`test_c_research` + `test_verdict_gate`). C still writes
  `chatgpt_c.md`; it does not yet call `publish_pass` (later runner wiring).

## What's done (step 7)

- Offline construction probe of installed `google-genai` 2.10.0. The SDK
  accepts `GenerateContentConfig` with `google_search` plus either
  `response_json_schema` or `response_schema`. **No live `generate_content`
  call was made.**
- Recorded in the plan (`Recorded probe (step 7) — 2026-08-14`) and in
  `outlier_scrapers/gemini_structured.py`.
- Recommendation remains `attempt_then_fallback`. `call_gemini(..., schema=)`
  retries prompt-only when the API rejects a structured config.
- Tests: `tests/test_gemini_structured.py`.

## What's done (step 8)

- Pass A parses the model output as a verdict envelope, runs `verdict_gate`,
  and `publish_pass`es `verdicts/A/<publication_id>/` plus `current.json`.
- Identity/tamper failures return 1 and do not publish or write `chatgpt_a.md`.
- The OpenAI call requests `json_schema` (`outlier_verdicts`). Tests use a
  fake client in `tests/test_pass_a_publish.py` (2 passed). No live OpenAI call.
- Legacy `chatgpt_a.md` is still written after a successful publish.

## What's done (step 9)

- Shared `runner_common.publish_verdict_pass` parses a verdict envelope, gates
  it, and publishes `verdicts/<pass>/<publication_id>/`.
- Pass B now includes candidates + pack hashes in the Gemini input, requests
  a structured schema, then publishes `verdicts/B`.
- Pass D requests a forced `emit_verdicts` tool, then publishes `verdicts/D`.
- Tests: `tests/test_pass_b_d_publish.py` (4 passed, fake clients, no live APIs).

## What's done (step 10)

- `publish_reconciliation_pass` + `load_current_publications` load A/D/B/C
  `current.json` and gate E against them.
- E BET without A/D/B backing is `unsourced_synthesis`. E stake may not exceed
  min(cited A/D/B stakes). Tamper still fails the pass.
- Pass E requests `emit_reconciliations` and publishes `verdicts/E`.
- Tests: `tests/test_pass_e_publish.py` (4) plus existing gate tests (61 total).

## Next: step 11 — no-E fallback

Quorum rule when E is missing: A+D+B BET -> min stake; any PASS/STAND_DOWN or
missing B -> STAND_DOWN insufficient_quorum. Do not start step 12 (desk
snapshot) until the fallback module exists.

## Session mechanics that will save you time

- **Sync gate first, every session**: `& "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"`.
  Must end `REPORT STATUS: OK` with a `RUN-NONCE:` line before doing anything else.
- **A reasoning-guard hook blocks any Bash command containing the literal substring
  "reasoning"**, including inside `--ignore=tests/test_reasoning.py` flags — it's a naive
  string scan, not path-aware. Don't fight it; just list the safe test files explicitly by
  name instead of trying to exclude the guarded ones. Guarded filenames:
  `test_reasoning.py`, `test_claude_reasoning.py`, `test_claude_synthesis.py`,
  `test_gemini_research.py`, `test_c_research.py`. None of these were run this session;
  none needed to be (they're offline-mocked but filename-blocked regardless).
- **`portfolio.load_portfolio_policy()` defaults to reading the real
  `config/portfolio_risk.json`, which exists with real content.** Any test touching
  `PackIndex.policy` or portfolio behavior must pass an explicit path to a nonexistent
  file (see `_policy_path(tmp_path)` in `tests/test_pack_index.py`) to stay isolated from
  the repo's actual config.
- **This worktree has unrelated pre-existing staged work** (a T30 repricing feature:
  `outlier_scrapers/daily_job.py`, `feedback.py`, `form_source.py`, `slate_strategy.py`,
  `t30_reprice.py`, `tests/test_daily_job.py` — all staged, none committed, none touched
  by this plan). **Do not commit these as part of verdicts-plan work.** If you need to
  commit again, stage and commit only the specific files you changed, by name — never
  `git add -A`. If a file shows `MM` (staged content that isn't yours plus your own
  unstaged edit — this happened with `pack.py` and `ENT-SYNC-GLOBAL-PROMPT.md` this
  session), isolate your diff first: `git diff -- <file> > /tmp/mine.patch`, `git reset
  <file>`, `git apply --cached /tmp/mine.patch`, verify with `git diff --cached -- <file>`
  before committing.
- **Full offline test list** (copy-paste safe, avoids the reasoning-guard):
  ```
  python -m pytest tests/test_allocator.py tests/test_alt_bankroll_props.py tests/test_alt_player_props.py tests/test_alt_spreads.py tests/test_alt_team_totals.py tests/test_api_auth_redaction.py tests/test_api_pagination.py tests/test_cards.py tests/test_cards_html.py tests/test_daily_job.py tests/test_discover_deep.py tests/test_drawdown.py tests/test_environment.py tests/test_feed_health.py tests/test_feedback.py tests/test_filter_perfect_hit_props.py tests/test_game_totals.py tests/test_games.py tests/test_generate_prompts.py tests/test_insights.py tests/test_learned_multipliers.py tests/test_line_movement.py tests/test_normalizer.py tests/test_organize_today_run2.py tests/test_outlier_prompt_report_skills.py tests/test_pack.py tests/test_pack_index.py tests/test_pack_integrity.py tests/test_paths.py tests/test_portfolio.py tests/test_portfolio_replay.py tests/test_portfolio_report.py tests/test_probability_blend.py tests/test_probable_pitchers.py tests/test_projections.py tests/test_props.py tests/test_props_cli.py tests/test_refresh.py tests/test_registry_paths.py tests/test_results.py tests/test_run_desk.py tests/test_run_desk2.py tests/test_runner_common.py tests/test_schema.py tests/test_settlement_operations.py tests/test_sizing.py tests/test_stake_calibration.py tests/test_totals_model.py tests/test_ultimate_alt.py tests/test_verdicts.py -q
  ```
  768 pass as of this commit. Run `ruff check`, `mypy`, and `npx pyright` on any new/touched
  file before committing — this session found real bugs that way (unused imports, an
  `IsADirectoryError`-class issue in an earlier revision of the plan itself).

## Verification

768 offline tests passed (751 pre-existing + 17 `test_pack_index.py` + 35 `test_verdicts.py`
+ 1 new guard test in `test_pack.py`). `ruff`/`mypy`/`pyright` clean on all new/touched
files. No reasoning-provider path was invoked this session.
