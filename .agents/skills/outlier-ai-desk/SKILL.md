---
name: outlier-ai-desk
description: >-
  Orchestrates the full AI Research Desk for a daily Outlier pack (A/B/D/E runners).
  Ensures a pack exists, reads front-matter to detect staleness and uses --force when needed,
  invokes the provider runners (OpenAI A, Gemini B, Claude D/E) only when keys are available,
  produces FULL/PARTIAL/DATA_ONLY status where FULL requires a complete final report (E or fallback),
  always guarantees a final betting report (via claude_e.md or local pack synthesis fallback),
  writes reasoning_status.json, gates E on all required inputs, and runs pytest before declaring done.
---

# Outlier AI Research Desk Orchestrator

## Purpose
Complete the automated + hybrid reasoning layer on top of the scrape/pack pipeline.
`daily_job` produces the data pack. This skill drives the multi-model "desk" (Prompts A/B/D/E), handles paid API steps idempotently, reports structured status, and **always** yields a usable betting report even if one or more external models are unavailable.

Use this instead of manually running separate `gemini_research`, `claude_reasoning`, `claude_synthesis`, and `reasoning` commands. It is the canonical way to finish a slate after packing.

## Guardrails (non-negotiable)
- Operate only inside this `outlier` repo. Never touch `nba-props-pipeline`.
- Never log, print, or persist API keys, full Outlier payloads, cookies, or raw book odds.
- Every recommendation in a report **must** quote exact `market_id`, `selection`, `line`, and `price` from the pack's `candidates.csv`.
- Respect pack-only vs web-allowed rules from `briefing.md` ROLE_BLOCK.
- Sizing is **never** invented by models or this skill — use `recommended_units_pre_news` (or downgrade only).
- When any required external runner cannot run (no key, rate limit, refusal), fall back to local synthesis from the pack (see `synthesize-outlier-pack`).
- Always run `pytest -q` (or targeted) at the end and only report success after green.
- Preserve existing outputs on hash match (idempotent). The agent uses `--force` when it proves a hash mismatch (or on explicit user request). Never blindly call without a decision.

## Prerequisites
- A pack for the target date exists under `packs/YYYY-MM-DD/` (with at minimum `briefing.md` + `candidates.csv`).
- For full desk: the relevant keys must be in env or `.env`:
  - `OPENAI_API_KEY` (Prompt A)
  - `GEMINI_API_KEY` (Prompt B)
  - `ANTHROPIC_API_KEY` (Prompts D and E)
- The individual runners (`outlier_scrapers.reasoning`, `.gemini_research`, `.claude_reasoning`, `.claude_synthesis`) and `runner_common` are already implemented and tested.

## Workflow

### 1. Locate or Produce the Pack
Default date = today (local).

```powershell
# Option A: Use latest existing pack (preferred for cost)
# Option B: Force fresh data (user must approve)
python -m outlier_scrapers.daily_job --leagues MLB,WNBA
# or with reasoning A only:
python -m outlier_scrapers.daily_job --leagues MLB,WNBA --run-reasoning
```

If no usable pack, stop and tell the user. Do not fabricate.

Confirm the pack dir:
`packs/YYYY-MM-DD/{briefing.md, candidates.csv, dossiers/..., (optional prior runner outputs)}`

### 2. Determine What to Run (Dependency Order)
Required for full E synthesis:
- A: `chatgpt_a.md` (pack-only, OpenAI)
- B: `gemini_b.md` (web-grounded, Gemini on briefing)
- D: `claude_d.md` (pack-only, Claude)

Optional:
- C: `chatgpt_c.md` (per-game manual paste — skip or include if present)

E (synthesis) is the final paid step that produces `claude_e.md`. E **requires** briefing.md + chatgpt_a.md + gemini_b.md + claude_d.md to all be present (see `claude_synthesis.gather_inputs()`). It cannot run on a partial set.

Strategy inside this skill (the agent implements the decision logic):
- For each of A/B/D:
  - If the output file exists, read its YAML front-matter `request_sha256` (using the same logic as `runner_common.extract_yaml_request_hash`).
  - Re-compute the current request hash from the live pack inputs (briefing/candidates + ROLE_BLOCK + prompt + model).
  - If hashes match → treat as fresh cached, skip the API call.
  - If hashes **mismatch** (or file missing) → the output is stale. Invoke the runner **with `--force`** (the only way to override via CLI; the internal `refresh_if_stale=True` is not exposed on the command line).
- Only invoke a runner if its required API key is present.
- Collect per-runner outcome: success / skipped-no-key / skipped-cached / forced-refresh / failed.
- Compute overall status (the agent must inspect front-matter hashes and file presence):
  - FULL: A, B, and D are all present + fresh (or successfully produced on this run) **AND** a final report exists (claude_e.md produced successfully, or local synthesis fallback produced a complete report).
  - PARTIAL: At least one of A/B/D is usable **and** a usable final report was produced (either claude_e.md succeeded or local synthesis fallback succeeded).
  - DATA_ONLY: No reasoning outputs and no usable synthesized report; only the raw pack data is available.
  - Note: A valid final report (successful E **or** local synthesis fallback) is required for both FULL and PARTIAL. E is never optional for claiming a complete desk.

### 3. Invoke the Runners (idempotent + safe)
Use the CLI entry points. **The agent (not the CLI) decides whether a refresh is needed** by comparing front-matter hashes against the current pack. The CLIs only expose `--force` (they default to no-op if the output file already exists).

```powershell
# Example: agent read front-matter, detected mismatch or missing file → use --force
python -m outlier_scrapers.reasoning --date YYYY-MM-DD --force
python -m outlier_scrapers.gemini_research --date YYYY-MM-DD --force
python -m outlier_scrapers.claude_reasoning --date YYYY-MM-DD --force

# Fresh / cached case (hashes matched) — no --force
python -m outlier_scrapers.reasoning --date YYYY-MM-DD

# Prompt E — ONLY after all of briefing + A + B + D are present and valid
python -m outlier_scrapers.claude_synthesis --date YYYY-MM-DD
```

The skill implementation (or you driving it) should:
- Run A/B/D in parallel where safe (they are independent after pack).
- **Only invoke E after A + B + D + briefing.md are all confirmed present and their content is valid.** `claude_synthesis.gather_inputs()` (and therefore the CLI) **requires** all three reasoning outputs plus the briefing; partial subsets will cause E to fail. Do not attempt E until the required inputs validate.
- When the agent itself has detected a hash mismatch, pass `--force` on the CLI for that runner. Do not rely on the CLI's internal no-op logic.
- Pass `--force` on explicit user request even without a detected mismatch.
- Capture exit codes and any `RunnerError` messages.

If a runner returns non-zero or produces no usable output file:
- Log the reason.
- Continue with remaining steps.
- Mark that component as failed for status.

### 4. Write Structured Status
After attempting the desk steps, atomically write (or update):

`packs/YYYY-MM-DD/reasoning_status.json`

Minimal schema (allow-listed keys only):
```json
{
  "date": "2026-06-28",
  "generated_at": "2026-06-28T...",
  "overall": "FULL" | "PARTIAL" | "DATA_ONLY",
  "components": {
    "A": { "status": "success" | "cached" | "skipped-no-key" | "forced-refresh" | "failed", "file": "chatgpt_a.md", "request_sha256": "..." },
    "B": { ... },
    "D": { ... },
    "E": { "status": "...", "file": "claude_e.md" }
  },
  "final_report": { "source": "claude_e" | "local_synthesis", "file": "claude_e.md" | "manual_betting_report.md" },
  "notes": ["any allow-listed observations"]
}
```

Use atomic write pattern (same volume as the pack dir) matching `runner_common.atomic_write` style.

Exit semantics (for any wrapper script or daily automation):
- FULL or PARTIAL → exit 0 (a usable final report exists, either claude_e.md or a generated manual_betting_report.md / equivalent)
- DATA_ONLY → exit 1 (no usable report; user must review the raw pack)

### 5. Guarantee a Final Betting Report
Always produce a human-usable report:

**Preferred path (when E succeeded):**
- Read `packs/YYYY-MM-DD/claude_e.md`
- Optionally copy or link relevant sections into a dated `betting_report.md` (or `final_guide.md`).

**Fallback path (any missing paid output, rate limits, no keys, or explicit bypass):**
- Invoke the local pack-only synthesis rules exactly as documented in `synthesize-outlier-pack`.
- Read `briefing.md` + `candidates.csv` (and dossiers as needed).
- Produce the canonical report structure directly:
  ```markdown
  # Daily Betting Report - YYYY-MM-DD

  ## MLB Standouts
  ...

  ## WNBA Standouts
  ...

  ## Leans
  ...

  ## Passes
  ...

  ## Injury Context
  ...

  ## Stale-Line or Data Quality Notes
  ...
  ```
- Write the result to `packs/YYYY-MM-DD/manual_betting_report.md` (or `betting_report.md`) when the user wants an artifact; otherwise return the full text in the interaction.

Never mix invented data. Every line quotes pack fields. Honor the freshness caveats present in the briefing.

### 6. Post-Desk Validation & Cleanup
- Re-inspect the pack dir for required files.
- If `candidates.csv` or `briefing.md` mutated unexpectedly, surface an error.
- Run the project test suite:
  ```powershell
  pytest -q --tb=no
  ```
  **Known Windows behavior**: pytest may exit non-zero due to temp file cleanup `PermissionError` (`.pytest_cache` / `pytest-of-*`) even when all tests pass. This is a documented quirk (see handoff notes). Check the summary line for "passed" count — treat as success if the logic tests are green and only teardown warnings appear. Use `pytest --cache-clear -q --tb=no` if stale cache causes odd failures (e.g. references to renamed tests).
- Report only after the counts look clean for the runner/pack/daily/auth modules.
- Optionally invoke `scripts/build_dashboard.py` or open `dashboard.html` to give the user a visual summary of feed status vs the reasoning layer.

### 7. User-Facing Output
Return:
- The overall status (FULL / PARTIAL / DATA_ONLY)
- Path to the final report artifact
- Summary table of which components ran vs cached vs fell back
- Any high-severity stale-line / partial-feed warnings pulled from the briefing
- The full final report content (or a tight excerpt + file path)

## Usage Examples

```powershell
# After daily_job produced the pack, finish the desk
# (this skill drives the steps above)

# Force a full re-run of reasoning for a specific date
# (user explicitly asked)

# Local-only day (no paid keys or budget exhausted)
# → skill automatically falls back to synthesize-outlier-pack logic
```

## Integration with Existing Tools
- `daily_job --run-reasoning` still only guarantees Prompt A. Call this skill afterwards (or extend daily_job in a future change) to get B/D/E + final report.
- `synthesize-outlier-pack` is the fallback engine when this orchestrator cannot obtain full desk outputs.
- Individual runners remain directly callable for debugging or one-off re-runs.
- `reasoning_status.json` is the single source of truth for "what did the desk actually produce today?"

## References
- [AI-research-desk-runbook.md](../../../AI-research-desk-runbook.md) — full rules, ROLE_BLOCK, sizing, kill criteria, source tiers.
- [README.md](../../../README.md) — CLI surface and daily_job.
- [AGENTS.md](../../../AGENTS.md) — project guardrails.
- `outlier_scrapers/runner_common.py`, `pack.py`, `reasoning.py`, `gemini_research.py`, `claude_reasoning.py`, `claude_synthesis.py`
- `.agents/skills/synthesize-outlier-pack/SKILL.md` — local synthesis contract.

## Success Criteria for a Run
- All tests green.
- A report (claude_e or manual) was produced that only references pack lines.
- Status file written with correct overall classification.
- No secrets or raw payloads leaked.
- User can act on the output immediately (standouts have exact market_id + line + price + units from pack).