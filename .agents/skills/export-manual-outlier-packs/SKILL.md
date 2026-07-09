---
name: export-manual-outlier-packs
description: >-
  Runs the daily Outlier pipeline locally without automated reasoning models, and exports the data into tailored prompt documents for manual upload to Claude, Grok, Gemini, and ChatGPT. Cleans the target Desktop folder of extraneous data.
---

# Export Manual Outlier Packs

## Overview
Use this skill when the user wants to run the daily betting pipeline but bypasses the automated reasoning models in favor of manually uploading the pack data to external web LLMs (Claude, Grok, Gemini, ChatGPT).

## Workflow

### 1. Run Pipeline Locally
Execute the pipeline in data-only mode to generate the briefing and candidates data:
`python -m outlier_scrapers.daily_job --analysis-profile local`

> [!NOTE]
> This pipeline typically takes around **20 minutes** to complete, largely due to the MLB line-movement scraper. Set a long timer (e.g., 300-600 seconds) and do not kill the process if it appears stuck on line-movement.

### 2. Generate Tailored Model Prompts
Run the provided helper script to automatically clean the target directory and generate the prompt files for all models:
`python .agents/skills/export-manual-outlier-packs/scripts/generate_prompts.py`
