---
name: synthesize-outlier-pack
description: >-
  Synthesizes an MLB, WNBA, or combined daily betting report locally from the
  standalone Outlier briefing pack when external reasoning models fail, are
  unavailable, or are intentionally bypassed. Use for pack-only fallback
  analysis and for returning the final report directly in chat.
---

# Outlier Pack Manual Synthesis

## Overview
This skill is an instruction-only workflow that acts as the primary local fallback when external reasoning models (`claude_synthesis`, `gemini_research`, or `claude_reasoning`) are unavailable due to API rate limits or credit exhaustion, or when intentionally bypassed by the user. 
**Do not attempt to call the external runners yourself**; instead, you will use this skill to synthesize the report entirely within your local LLM context based on the already generated pack files.

## Dependencies & Guardrails
- **Project Scope:** Operate strictly inside the `outlier` repo. Do not access `nba-props-pipeline`.
- **Privacy:** Never log tokens, cookies, raw auth headers, full Outlier payloads, or book-odds values in discovery reports.
- **Data Integrity:** Preserve unknown teams/markets through `_raw` fields.
- **Workflow Boundary:** Use `synthesize-outlier-pack` for local, pack-only fallback synthesis. Use `analyze-wnba-outlier` when the task requires a WNBA refresh, live injury/line validation, or rebuilding the WNBA pack.

## Workflow

### 1. Locate the Pack
Determine the correct date of the pack. Default to using an existing `packs/YYYY-MM-DD/` pack (e.g. today's date). Do not automatically run a full data refresh merely because an external reasoning model failed.
If the user requests analysis of an existing/latest pack, locate and read it without refreshing.

### 2. (Optional) Execute the Pipeline
Run `python -m outlier_scrapers.daily_job --leagues <requested leagues>` ONLY when the user explicitly requests fresh data or no usable pack exists AND the user approves a refresh.
Accept only the currently supported league set: `MLB`, `WNBA`, or `MLB,WNBA`.
If `daily_job.py` fails with an authentication error, stop and notify the user immediately so they can trigger the auth workflow. Do not attempt to guess or bypass auth.
Never invoke the external reasoning runners as part of the local fallback.

### 3. Read Required Files
You must load the full content of both of these files:
- `packs/YYYY-MM-DD/briefing.md`: Contains freshness logic, slate context, and the critical `ROLE_BLOCK`.
- `packs/YYYY-MM-DD/candidates.csv`: Contains the shortlisted props with exact sizing and odds.
- *(Optional)* `dossiers/*.md`: Review these if deeper game or injury context is needed.

*Error Handling:* If `candidates.csv` or `briefing.md` is missing, report the error clearly to the user and **do not fabricate data**.

### 4. Local LLM Synthesis
Treat the `ROLE_BLOCK` text found in `briefing.md` as your primary system prompt for analysis. 

**Selection Rules:**
- **Trust the Pack:** Prefer the data in the pack over any internal knowledge.
- **Line Matching:** For every candidate you analyze, you must quote the exact `market_id`, `selection`, `line`, and `price` directly from the CSV. Never paraphrase the line/price.
- **Stale Lines:** Downgrade props on stale lines (use the freshness section in the briefing).
- **Injury/Usage:** Weight injury and usage changes higher than older data. If a card has no supporting freshness or injury context in the briefing, downgrade it.
- **Artifact Detection:** Explicitly filter out >90% EV edges. Cross-reference these with the slate context; if unexplainable by breaking news, treat them as stale lines/artifacts and move them to the stand-down section.
- **Pack-only rule:** Missing context goes under `NEEDS`; do not fill gaps from memory or silently change lines or prices.

### 5. Generate the Report
Return the complete synthesized report directly in chat by default. 
Write to a file (`packs/YYYY-MM-DD/manual_betting_report.md`) ONLY when the user asks for a saved artifact. If a file is written, return the same substantive result in chat or provide a concise summary plus the file path, according to the user's request.

For a mixed pack, group recommendations under `## MLB` and `## WNBA`. For a single-league pack, use one league-specific title without an unnecessary extra grouping level.

The final output must be structured exactly like this:
```markdown
# Daily Betting Report - [Date]

## [League] Standouts
(High-confidence EV edge and strong signal props. Required fields per play: `sport`, exact `market_id`, `selection` @ exact `line` (`price`) from candidates.csv, `edge%`, recommended `units`, and a short pack-supported rationale.)

## Leans
(Props that have a positive edge but lack sufficient confidence or freshness context.)

## Passes
(Props considered but ultimately rejected.)

## Injury Context
(Relevant injury updates and their direct impact on the slate.)

## Stale-Line or Data Quality Notes
(List any bets killed due to stale lines or >90% unrealistic EV edges, referencing the exact `market_id`. Acknowledge any missing games or partial freshness data here. NEEDS go here.)
```

### 6. Final Verification
Run tests to ensure no project states were corrupted during script execution:
```bash
pytest
```
Report completion to the user only after tests pass.

## References
- `AGENTS.md`: For core project guardrails.
- `AI-research-desk-runbook.md`: For deep context on the pipeline architecture.
- `packs/YYYY-MM-DD/briefing.md`: For the `ROLE_BLOCK` instructions.
