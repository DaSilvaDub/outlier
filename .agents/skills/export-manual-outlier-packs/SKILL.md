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
- `Desk1_Automated/`: Three shared master prompts for Cards, HitRate, and Totals. Any supported agent can analyze one of these exact files.
- `Desk2_Manual/`: The strict sequential phase prompts (`1_PhaseQ...`, `2_PhaseR...`, etc.).

Each run archives old pack files into an `archive/` subfolder.

### 3. Hand Off for Analysis

If the user also explicitly asks in the current turn to analyze generated prompts, route by folder:

- `Desk1_Automated/`: follow `.agents/skills/analyze-outlier-generic-prompts/SKILL.md`. Save reports under `BETTING REPORTS/GENERIC/YYYY-MM-DD`.
- `Desk2_Manual/`: follow `.agents/skills/analyze-outlier-sequential-prompts/SKILL.md`. Run Q → R → W → X → S and save phase reports under `BETTING REPORTS/SEQUENTIAL/YYYY-MM-DD`.

If the user requested only data and prompt export, stop after reporting the generated paths. Prompt creation by itself does not authorize reasoning-model use.
