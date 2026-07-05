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

### 2. Prepare the Target Directory
- **Path**: `C:\Users\dasil\OneDrive\Desktop\today`
- **Cleanup**: Delete all files and subdirectories in this folder EXCEPT for files matching `*_pack_*.txt`. This ensures old unrelated data is wiped while preserving previous pack generations.

### 3. Generate Tailored Model Prompts
Read the newly generated `briefing.md` and `candidates.csv` from the pipeline's output folder (`packs/<target_date>`).

Create four distinct text files in the target directory (e.g. `claude_pack_<date>.txt`, `grok_pack_<date>.txt`, etc.).

**Modifications to Briefing:**
- Remove any existing instructions that restrict the use of memory or the web (e.g. "Do not use memory or the web", "do not guess").

**Prompt Injection:**
Prepend the following instructions to the top of each file, tailored to the specific model name:
> Hello [Model Name], please analyze the following betting data and generate a final betting report.
> CRITICAL INSTRUCTIONS FOR YOU:
> 1. You MUST USE THE INTERNET / web search for any missing information you need (like injuries, news, or context) that you do not have to make your analysis. Do not just say you need it, go search for it.
> 2. I WANT NO HIGH VARIANCE PROPS in the final recommendations. Exclude any high variance props entirely.

Append the modified `briefing.md` and raw `candidates.csv` contents directly below the prompt.
