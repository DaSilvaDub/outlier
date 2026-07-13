## Handoff — `fix/pack-structural-integrity` (PR #31)

**Last Commit SHA:** `5cb6282` (code commit on `fix/pack-structural-integrity`)

**PR Link:** https://github.com/DaSilvaDub/outlier/pull/31

**Files Touched:**
- `outlier_scrapers/props.py` — bounded repair of schedule gaps for prop-referenced events.
- `outlier_scrapers/cards.py` — preserved market type; headline-scoped line mismatch and ladder-aware spread mirror validation.
- `outlier_scrapers/pack.py` — coverage report, actionability, non-actionable EV audit section, team-total dedup/labels, and sourced proxy probability/edge/Kelly.
- `tests/test_props.py`, `tests/test_cards.py`, `tests/test_pack.py`, `tests/test_run_desk.py` — regression coverage and signature adaptation.

**Verification:** 105 focused tests passed (73 pack; 32 cards/props); Ruff and MyPy passed. Offline game-card/pack regeneration passed. `test_run_desk.py` collection hung before executing the mocked test and was terminated; no provider calls ran.

**Next Steps:**
- Review PR #31 and run CI.
- On the next authenticated props refresh, verify the MLB single-event enrichment restores `event_starts_at` and produces MLB candidates. Current stale July 13 source data still reports MLB as zero emitted with 394 unverifiable-start drops, now explicitly surfaced rather than silently omitted.

---

**Last Commit SHA:** `37e665d` (on branch `feat/desk2-manual-research-desk`)

**PR Link:** https://github.com/DaSilvaDub/outlier/pull/26

**What/Why:** Added "Desk 2", a second, opt-in AI research desk alongside the
existing A-E desk. A-E splits work by *function* (reason vs research) and
calls provider APIs; Desk 2 splits work by *model strength* (ChatGPT, Gemini,
Grok, Claude) and is manual/offline only — it generates paste-ready docs and
stitches saved replies, never calling a reasoning provider directly. Grok
fills a new live-X/late-breaking lane (replacing the old automated Phase C
web-research slot); its findings are treated as Tier-3-by-default and can
only lower confidence or flag a lead, never solely support a BET.

Phase map: Q=ChatGPT (quant/EV pack-only reasoning), W=Gemini (grounded web),
X=Grok (live-X sentiment), R=Claude (skeptical pack-only + contradiction
detection), S=Claude (head-of-desk synthesis).

**Files Touched:**
- `outlier_scrapers/run_desk2.py` — new module: `build_paste_doc()`,
  `generate_paste_docs()`, `produce_manual_report()`, `orchestrate_desk2()`.
  Imports no provider SDK and has no `PHASE_RUNNERS`/`PHASE_KEYS`, so it
  cannot call a reasoning model even by accident (house rule satisfied
  structurally). Reuses `pack.ROLE_BLOCK`, `runner_common` data-block
  assembly, and `pack.drop_locked_events` (pregame-only).
- `prompts/desk2/{Q_chatgpt,W_gemini,X_grok,R_claude,S_claude}.md` — the five
  role prompts.
- `outlier_scrapers/pack.py` — generalized the two desk-1-lettered ROLE_BLOCK
  labels ("REASONING PASSES (A, D)" / "RESEARCH PASSES (B, C)") to
  desk-agnostic wording ("pack-only" / "web-enabled") since the block is now
  shared by both desks. Semantics unchanged.
- `tests/test_run_desk2.py` — 15 new tests (phase map, offline generation
  with no API keys, Grok Tier-3 guardrail, pregame lock-drop, manual-report
  stitching, GENERATED/FULL/PARTIAL/DATA_ONLY status).
- `tests/test_pack.py` — updated `test_end_to_end` for the generalized
  ROLE_BLOCK wording.

**Usage:**
```
python -m outlier_scrapers.run_desk2 --date <YYYY-MM-DD> --stage generate
# paste each doc into its model, save replies as chatgpt_q.md / gemini_w.md /
# grok_x.md / claude_r.md
python -m outlier_scrapers.run_desk2 --date <YYYY-MM-DD> --stage assemble
```

**Tests:** `pytest tests/test_run_desk2.py` — 15 passed. Adjacent offline
suites (`test_run_desk`, `test_pack`, `test_runner_common`, `test_reasoning`,
`test_claude_reasoning`) — 114 passed, no regressions. `ruff check` and
`mypy outlier_scrapers/run_desk2.py` clean. Live-provider reasoning tests
intentionally not run (house rule: reasoning off unless explicitly asked).
CI on PR #26: typecheck + Codacy both green, `mergeStateStatus: CLEAN`.

**Gotcha for the next agent:** this session hit the local-branch-reset hazard
twice — the repo's concurrent sync tooling reset the checked-out feature
branch back to `origin/master` mid-session (via `git reset`, visible in
`git reflog show <branch>`), even after the branch had commits pushed to
origin. No work was lost either time (origin always had the real commits;
recovered locally via `git reset --hard origin/<branch>`), but **run
`git branch --show-current` and diff it against `origin/<branch>` right
before every commit**, not just at session start.

**Next Steps:**
- Review and merge PR #26.

---

Branch: feat/add-type-checking
Last Commit SHA: 1e4e5f9 (on feat/add-type-checking)
PR Link: https://github.com/DaSilvaDub/outlier/pull/24

Next Steps:
- Static type checking with MyPy and Pyright is fully implemented and configured in pyproject.toml.
- Added a GitHub Action workflow `.github/workflows/typecheck.yml` to automatically run mypy and pyright checks on pull requests.
- Type errors in login, pack, game_totals, run_desk, reasoning, insights, and games have been completely fixed (including a critical NameError bug in login.py).
- Legacy files that still need gradual type migration are cleanly excluded from MyPy and Pyright checks.
- Pull Request #24 is open. Wait for code review and merge.

---

**Last Commit SHA:** `8e526de` (on branch `fix/spread-sign-rendering`)

**PR Link:** https://github.com/DaSilvaDub/outlier/pull/25

**What/Why:** The 2026-07-12 five-model betting reports (Claude/Copilot/Gemini/Grok/ChatGPT)
disagreed on the sign of positive spread/run-line values — e.g. the same ATL @ STL
run-line row rendered as `-1.5` in some reports and `+1.5` in others — because
`pack.py` shipped positive spread lines as a bare unsigned number, leaving each
downstream model to guess the sign.

**Files Touched:**
- `outlier_scrapers/pack.py` — `_fmt_signed_line()` explicit `+`/`-` rendering for
  `proposition == "SPREAD"` markets (selection text, `line`, and the alt-line EV
  fallback's `priced_line`/`ev_line_fallback:priced_at=` annotation); new LEDGER
  CONTEXT bullet clarifying `model_prob` semantics on signed-margin rows.
- `outlier_scrapers/cards.py` — `_spread_sign_conflict()` flags a card
  `spread_sign_conflict` when a two-sided SPREAD market's HOME/AWAY lines aren't
  mirror-image (data corruption signal).
- `tests/test_pack.py`, `tests/test_cards.py` — 8 new tests incl. an end-to-end
  `assemble_game_card` integration test.

**Tests:** `pytest tests/` — 315 passed. Reviewed by python-reviewer subagent
(one HIGH finding — alt-line-fallback path left unsigned — fixed before push).

**Next Steps:**
- Review and merge PR #25.

---

## Handoff — `feat/schema-compatibility-gates` (PR #23)

**Files Touched:**
- `outlier_scrapers/schema.py`
- `outlier_scrapers/normalizer.py`
- `outlier_scrapers/line_movement.py`
- `outlier_scrapers/pack.py`
- `tests/test_schema.py`

**Next Steps:**
- Monitor PR 23 CI / review and merge it.

---

## Handoff — `fix/ruff-dev-dependency` (PR #22)

**Last Commit SHA:** `9c5b1d6`

**PR Link:** https://github.com/DaSilvaDub/outlier/pull/22

**Files Touched:**
- `pyproject.toml` — add `ruff` (pinned `>=0.5.0`) to the `dev` optional-dependencies group
- `requirements.txt` — add `ruff>=0.5.0`
- `outlier_scrapers/login.py` — import `PROJECT_ROOT` from `.paths`
- `outlier_scrapers/runner_common.py` — move `logging`/`datetime` imports to the top of the file instead of mid-file

**Next Steps:**
- Verify `pip install -e .[dev]` installs both `pytest` and `ruff`.
- Review and merge PR #22.

---

## Handoff — Antigravity / Claude Code (branch `feat/split-team-totals`, PR #19)

**Date:** 2026-07-12

Resolved merge conflicts between `master` (which introduced the logical market grouping and period_identity changes via PR #17) and the `feat/split-team-totals` PR (PR #19).

1. Resolved conflicts in `outlier_scrapers/game_totals.py` by incorporating `period_identity` and updating `is_game_total_record` / `is_team_total_record` to use `is_full_game_total(rec)`.
2. Resolved conflicts in `tests/test_game_totals.py` by merging tests from both branches.
3. Addressed automated PR review feedback (Codacy/Gemini): removed duplication in `is_game_total_record`/`is_team_total_record`, added `runner_common.load_all_totals()` to dedupe totals-loading across reasoning runners, fixed a hardcoded error message, and added missing `daily_job.py` test coverage for the team-totals-only actionable path.
4. Re-resolved repeated HANDOFF.md-only conflicts against master as other agents' PRs (#22, #23) landed handoff updates concurrently — no further source conflicts.

**Next steps:**
- PR #19 merged into master.
