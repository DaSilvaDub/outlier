---
name: outlier-baseball
description: >-
  Produces a daily MLB betting report directly from the pipeline's briefing pack when external reasoning models fail or are bypassed. Uses local LLM synthesis with slate context to filter data artifacts.
---

# Outlier Baseball Manual Synthesis

## Overview
This skill is an instruction-only workflow that acts as the primary local fallback when external reasoning models (`claude_synthesis`, `gemini_research`, or `claude_reasoning`) are unavailable due to API rate limits or credit exhaustion. **Do not attempt to call the external runners yourself**; instead, you will use this skill to synthesize the report entirely within your local LLM context based on the generated pack files.

## Dependencies & Guardrails
- **Project Scope:** Operate strictly inside the `outlier` repo. Do not access `nba-props-pipeline`.
- **Privacy:** Never log tokens, cookies, raw auth headers, full Outlier payloads, or book-odds values in discovery reports.
- **Data Integrity:** Preserve unknown teams/markets through `_raw` fields.

## Workflow

### 1. Execute the Pipeline
Run the daily job to fetch fresh data and build the pack. We use `daily_job.py` because it safely orchestrates auth, full refresh, and packing in one step:
```bash
python -m outlier_scrapers.daily_job --leagues MLB
```
*Warning: This triggers a full refresh which may be slow.*

### 2. Auth & Error Fallback
If `daily_job.py` fails with an authentication error, stop and notify the user immediately so they can trigger the auth workflow. Do not attempt to guess or bypass auth.

### 3. Locate the Pack
Determine the correct date of the pack. Use the date that `daily_job.py` reports in the console output, or fall back to the current local date in `YYYY-MM-DD` format. The pack will be written to `packs/YYYY-MM-DD/`.

### 4. Read Required Files
You must load the full content of both of these files:
- `packs/YYYY-MM-DD/briefing.md`: Contains freshness logic, slate context, and the critical `ROLE_BLOCK`.
- `packs/YYYY-MM-DD/candidates.csv`: Contains the shortlisted props with exact sizing and odds.
- *(Optional)* `dossiers/*.md`: Review these if deeper game or injury context is needed.

*Error Handling:* If `candidates.csv` or `briefing.md` is missing after `daily_job.py` succeeds, report the error clearly to the user and **do not fabricate data**.

### 5. Local LLM Synthesis
Treat the `ROLE_BLOCK` text found in `briefing.md` as your primary system prompt for analysis. 

**Selection Rules:**
- **Trust the Pack:** Prefer the data in the pack over any internal knowledge.
- **Line Matching:** For every candidate you analyze, you must quote the exact `market_id`, `selection`, `line`, and `price` directly from the CSV. Never paraphrase the line/price.
- **Stale Lines:** Downgrade props on stale lines (use the freshness section in the briefing).
- **Injury/Usage:** Weight injury and usage changes higher than older data. If a card has no supporting freshness or injury context in the briefing, downgrade it.
- **Artifact Detection:** Explicitly filter out >90% EV edges. Cross-reference these with the slate context; if unexplainable by breaking news, treat them as stale lines/artifacts and move them to the stand-down section.

### 6. Generate the Report
Write the synthesized report to a markdown file located alongside the pack:
`packs/YYYY-MM-DD/mlb_betting_report.md`

The final output must be structured exactly like this:
```markdown
# MLB Betting Report - [Date]

## Best Plays
(High-confidence EV edge and strong signal props. Required fields per play: `market_id`, `selection @ exact line (price)`, `edge%`, `units`, and `one-line slate-context rationale`.)

## Leans
(Props that have a positive edge but lack sufficient confidence or freshness context.)

## Passes
(Props considered but ultimately rejected.)

## Injury Context
(Relevant injury updates and their direct impact on the slate.)

## Stale-Line or Data Quality Notes
(List any bets killed due to stale lines or >90% unrealistic EV edges, referencing the exact `market_id`. Acknowledge any missing games or partial freshness data here.)
```

### 7. Final Verification
Run tests to ensure no project states were corrupted during script execution:
```bash
pytest
```
Report completion to the user only after tests pass.

## References
- `AGENTS.md`: For core project guardrails.
- `AI-research-desk-runbook.md`: For deep context on the pipeline architecture.
- `packs/YYYY-MM-DD/briefing.md`: For the `ROLE_BLOCK` instructions.
