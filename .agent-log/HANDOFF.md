# Handoff Summary - 2026-08-24 (desk prompt optimization)

## 1. Branch / PR
- Branch: `claude/outlier-prompts-optimize-fpyxcs`
- Work: realigned the five AI Research Desk prompts (A/B/C/D/E) with the
  structured-envelope contract they actually run under.

## 2. What was wrong
- `prompts/B.md` and `prompts/D.md` were **byte-identical**, and both were a near
  copy of `A.md`. One generic Markdown-report prompt was serving three passes
  with different providers, different tools, and different jobs.
- A/B/D/E still asked for a Markdown report with tables (§14) while the runners
  force a JSON verdict/reconciliation envelope. E had the envelope contract
  bolted onto the end of a body that contradicted it.
- A and D are pack-only (no web tools) but were instructed to do 24-hour Tier-1
  external research — an instruction only satisfiable by fabrication.
- **A and D were never sent `pack_date` or the three pack hashes**, yet the
  envelope requires them and `verdict_gate` fails the whole pass on a mismatch.
  B, C and E received them; A and D could not emit a valid envelope.
- Gate rules absent from every prompt: the 0.5-unit stake grid and 3.0-unit cap,
  the `priced_line`-as-`line` rule, `board=A_FLAGGED`, the 2B UNDER-only side
  restriction, `WALKS_ALLOWED`, BB being prohibited only at `PLAYER_PROP` scope,
  and player-claim `player_id` binding.
- `B.md`/`D.md` still listed total bases as never-recommend after it was removed
  from `ROLE_BLOCK`.

## 3. Files touched
- `prompts/A.md` — Pass A, pack-only verdict envelope (OpenAI strict schema).
- `prompts/D.md` — Pass D, pack-only red-team verdict envelope; `contradictions`
  now mandatory on every backed bet.
- `prompts/B.md` — Pass B, web-grounded verdict envelope; research discipline,
  source tiers, external-evidence and injury-binding rules; JSON-only (B can fall
  back to prompt-instructed JSON).
- `prompts/C.md` — tightened; adds the `source_timestamp` window
  (`pack_date − 2d` .. `pack_date + 1d`) and player binding.
- `prompts/E.md` — reconciliation only; the contradictory report body is gone.
- `prompts/Master_Cards_Analysis.md` — **new**: the human Markdown paste lane,
  split out of `A.md`, with the stale market rules corrected.
- `outlier_scrapers/runner_common.py` — `build_pack_identity_block()`.
- `outlier_scrapers/{reasoning,claude_reasoning}.py` — send that block (A and D
  could not previously satisfy the envelope header).
- `outlier_scrapers/{gemini_research,c_research}.py` — same block, so all five
  passes see one labelled `PACK IDENTITY` header.
- `.agents/.../generate_prompts.py` — Master Cards loads its own template.
- `tests/test_prompt_contracts.py` — **new**, 21 cases pinning prompt/schema/
  runner alignment.
- `AI-research-desk-runbook.md` §3 — rewritten to describe the real contracts.

## 4. Verification
- `pytest`: 1151 passed. 10 failures are pre-existing and environmental — the
  container has no `openai` / `anthropic` / `google-genai` and no pip network;
  the identical 10 fail on a stashed (clean) tree.
- `ruff check`: clean. `mypy outlier_scrapers`: same 4 pre-existing errors as on
  a clean tree.
- STEP 0 `report-sync.ps1` was **not** run: this is a Linux remote container with
  no PowerShell and no access to the Windows worktrees. Re-run it locally before
  trusting any branch/worktree claim.
- No reasoning model, desk runner, or provider API was invoked.

## 5. Next steps
- Run `report-sync.ps1` locally and confirm `REPORT STATUS: OK`.
- First live desk run after merge will be a full re-run: prompt text is part of
  every `request_sha256`, so all caches are invalidated by design. Land it
  off-slate.
- Watch `violations.json` on that first run — the point of these edits is fewer
  `pack_mismatch`, `identity_tamper`, `stake_*` and `unsupported_injury_claim`
  codes.
- Desk 2 (`prompts/desk2/*.md`) was not touched; it is a manual paste workflow
  with no gate, so it has no envelope contract to drift from.
