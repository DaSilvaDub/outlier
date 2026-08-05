---
name: export-manual-outlier-packs
description: >-
  Runs the daily Outlier pipeline locally without automated reasoning models and exports paste-ready prompt documents for Claude, Grok, Copilot, Gemini, and ChatGPT. Use when the user requests fresh data plus manual model prompt files rather than the paid automated desk.
---

# Export Manual Outlier Packs

## Overview
Use this skill when the user wants to run the daily betting pipeline but bypasses the automated reasoning models in favor of manually uploading the pack data to external web LLMs (Claude, Grok, Copilot, Gemini, and ChatGPT).

## Workflow

### 1. Run Pipeline Locally
Execute the pipeline in data-only mode to generate the briefing and candidates data:
`python -m outlier_scrapers.daily_job --analysis-profile local`

> [!NOTE]
> This pipeline typically takes around **20 minutes** to complete, largely due to the MLB line-movement scraper. Set a long timer (e.g., 300-600 seconds) and do not kill the process if it appears stuck on line-movement.

### 2. Generate Prompt Documents
Run the provided helper script to automatically clean the target directories and generate the prompt files for all models:
`python .agents/skills/export-manual-outlier-packs/scripts/generate_prompts.py`

By default this writes to both `C:\Users\dasil\OneDrive\Desktop\today\prompts` and `G:\My Drive\today\prompts` (pass `--out-dir` one or more times to override). 

The script structures the output into two pipelines:
- `Desk1_Automated/`: four specialized master prompts (HitRate prompts are intentionally not generated — dropped pending a redesign):
  - `1_Master_Cards_pack_YYYY-MM-DD.txt`: Filtered for 2.0+ Unit Recommended Candidates.
  - `2_Master_Totals_pack_YYYY-MM-DD.txt`: Game Totals and Team Totals ONLY (Alternate Team Totals excluded).
  - `3_Master_Alt_Total_MLB_pack_YYYY-MM-DD.txt` and `3_Master_Alt_Total_WNBA_pack_YYYY-MM-DD.txt`: strict league-specific alternate game/team bankroll lines (L5=100%, L10>=90%, full-game, Hard Rock, odds -1000 through -200) when qualifying rows exist.
  - `4_Master_Alt_Player_Prop_pack_YYYY-MM-DD.txt`: strict full-game Hard Rock player alternate lines (L5=100%, L10>=90%, odds -1000 through -200); omitted when no rows qualify.

  "Alt Total" and "Alt Player Prop" are both bankroll-style plays (low variance, high probability) — the market type differs (game/team total vs. player prop), not the underlying strategy, and their filter thresholds are aligned.
- `Desk2_Manual/`: The strict sequential phase prompts (`1_PhaseQ...`, `2_PhaseR...`, etc.).

Each run archives old pack files into an `archive/` subfolder.

### 3. Run or Hand Off for Analysis

If the user also explicitly asks in the current turn to analyze generated prompts, route by folder:

- `Desk1_Automated/`: follow `.agents/skills/analyze-outlier-generic-prompts/SKILL.md`. Its shared CLI runner can send all latest master prompts to Codex, Claude, Gemini, and Grok, then save reports under `BETTING REPORTS/GENERIC/YYYY-MM-DD`.
- `Desk2_Manual/`: follow `.agents/skills/analyze-outlier-sequential-prompts/SKILL.md`. Its shared CLI runner executes Q → R → W → X → S with the fixed provider assignments and saves phase reports under `BETTING REPORTS/SEQUENTIAL/YYYY-MM-DD`.

If the user requested only data and prompt export, stop after reporting the generated paths. Prompt creation by itself does not authorize reasoning-model use.
