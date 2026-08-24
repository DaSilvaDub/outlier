# Handoff Summary - 2026-08-24

## 1. Last Commit SHA
- `58a4d6e4fa5cadbcfbfb5a6041e2a7a1c9f836b2` on branch `claude/outlier-prompts-optimize-fpyxcs`
- Pull Request: https://github.com/DaSilvaDub/outlier/pull/116 (open, CI green, mergeable)

## 2. What This Branch Does
Aligns the five AI Research Desk prompts (`prompts/A-E.md`) with the structured
envelope contract the runners actually enforce.

- `B.md` and `D.md` had been byte-identical, and both were near-copies of `A.md`
  — one Markdown-report prompt serving three passes with different providers and
  different tools, while the runners had moved to forced structured output.
- A and D are pack-only but were told to do 24h Tier-1 web research, which is
  only satisfiable by fabricating.
- Neither A nor D was ever shown `pack_date` or the three pack hashes, which the
  envelope requires and the gate checks before reading a verdict.
- `prompts/Master_Cards_Analysis.md` is new: the human paste lane's Markdown
  prompt, split out of `A.md`. `generate_prompts.py` fails closed if it is
  missing rather than falling back to the now-JSON `A.md`.

Three gate changes came out of review, each backing a rule the prompts state:
`pack_date` validation, E citations bound to the record they name
(`UpstreamPublication.records`), and `nonzero_stake_on_non_bet`.

## 3. Verification
- `pytest`: 1176 passed locally. 10 failures are environmental in this container
  (no `openai` / `anthropic` / `google-genai`, no pip network) and fail
  identically on a stashed clean tree. CI runs all of them green.
- CI on `58a4d6e`: `test` success, `typecheck` success. `mergeable_state: clean`.
- `ruff check` clean; `mypy outlier_scrapers` at or below the pre-existing baseline.
- No reasoning model, desk runner, or provider API was invoked at any point.
- STEP 0 `report-sync.ps1` was NOT run: Linux container, no PowerShell, no access
  to the Windows worktrees. Re-run it locally before trusting any branch,
  worktree, or commit-existence claim from this session.

## 4. Next Steps
- Merge PR #116 **off-slate**. Prompt text joins every `request_sha256`, so the
  first desk run after merge is a full re-run by design. The `C: None` fix to
  `upstream_publication_ids` also changes E's request hash on its own.
- On that first run, watch `violations.json`: the point of these edits is fewer
  `pack_mismatch`, `identity_tamper`, `stake_*` and `unsupported_injury_claim`.
- Follow-up PR (deliberately excluded here, full list in PR #116 comments):
  enforce in the gate what the prompts state — non-empty `evidence` on a `BET`,
  `kind: "pack"` for the pack-only passes, external-evidence
  `source`/`tier`/`timestamp` validation, non-empty `contradictions` on a D
  `BET`, fail-closed `actionable`, filtering upstream records through
  `violations.json`, and `pack_date` in `request_sha256`. Each widens the reject
  surface, so land them with a shadow-mode observation window.
- Also folded into that follow-up: `test_prompt_contracts` asserts the identity
  block by source search rather than captured provider requests, and does not
  cover `claude_synthesis`; and the envelope's `pass` field is not bound to the
  invoking runner at publish time.
