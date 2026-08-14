# Handoff

## Task

Implementing `docs/plans/2026-08-12-structured-ai-verdicts.md` (revision 10 — 8 external
review rounds, all resolved). The plan is long; read it, don't re-derive it. Steps 0-3 of
its 15-step Build order are done and committed. Continue at **step 4**.

## Last Commit SHA

`f759b6c` — `feat(verdicts): structured AI schema validation, steps 0-3`, on branch
`claude/structured-ai-schema-validation-afd7a9`, pushed to origin.

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

## Next: step 4 — `outlier_scrapers/verdict_gate.py`

Build order text: *"gates in the table order above, one test-driven commit per code group,
including the pass-failing-vs-judgement violation classification and the scoped
`desk_prohibited_markets` check."*

Read the plan's **"The deterministic gates"** section (the full violation table) and
**"Repair loop and failure policy"** (the pass-failing vs. judgement-outcome split — this
one has a documented history of being gotten wrong twice across review rounds, read it
carefully before implementing). Also re-read **"Resolved market policy"** for the exact
`desk_prohibited_markets` scoping (`{market, scope}` pairs, not bare strings — BB is
`PLAYER_PROP`-scoped, HRR is any-scope).

`verdict_gate.py` is a pure function of `(envelope, index, now)` — no I/O, no clock reads
beyond the `now` parameter, no network. It consumes `verdicts.py`'s parsed envelope types
and `pack_index.py`'s `PackIndex`, and produces `Violation` records (`{code, outcome_id,
market_id, detail, severity}` — `severity` is `reject` or `warn`).

TDD as usual: `tests/test_verdict_gate.py`, one red-path test per violation code from the
gates table (each built from a fixture pack whose row is valid except for the single
property under test), plus a green-path test and the adversarial fixtures listed in the
plan's Testing section (line one tick off, stake at exactly `max_units` vs. one increment
above, 2B OVER, `+150` vs `+149`, etc.).

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
