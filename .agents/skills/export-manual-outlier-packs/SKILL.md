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

Pack write now includes `packs/<date>/identity_audit.json`. After the job, check that file (or the `Pitcher identity audit:` log line) for `mismatch_count` / `unconfirmed_count`. Fail-closed SO identity rows are `A_FLAGGED` and must not be treated as Board A.

To run the same sequence unattended every morning:
`powershell -File C:\Users\dasil\Dev\GitHub\outlier\scripts\run_daily_pipeline.ps1`
(see the script header for a one-time `schtasks` install). The wrapper uses `--leagues MLB,WNBA`. A confirmed empty WNBA slate is non-fatal; when WNBA games return they are packed automatically. The pitcher identity audit is MLB SO only.

> [!TIP]
> **Handling Off-Slate / Hiatus Leagues**: If a sport has no games scheduled for today (e.g., WNBA mid-season break or off-season), specify only active leagues via `--leagues <ACTIVE_LEAGUES>` (e.g., `--leagues MLB`). This prevents `projections.py` date-mismatch errors and stale feed-health rejections caused by sportsbooks posting advance lines for future dates.

> [!NOTE]
> This pipeline typically takes around **20 minutes** to complete, largely due to the MLB line-movement scraper. Set a long timer (e.g., 300-600 seconds) and do not kill the process if it appears stuck on line-movement.

### 2. Generate Prompt Documents & Organize Export Folders
Run the `organize_today_run2.py` script to generate prompts AND organize them into the `generic_prompts`, `totals_prompts`, and other output folders on the Desktop and G Drive:
`python scripts/organize_today_run2.py`

This will automatically invoke `generate_prompts.py` and replace the latest export buckets for today's slate.

The ordered Q → R → W → X → S sequence is excluded by default. Generate it only when the user explicitly asks for it by adding `--include-sequential-prompts` to the `organize_today_run2.py` command, which will generate them and populate the `desk2_prompts` export bucket.

The script structures the output into two pipelines:
- `Desk1_Automated/`: five specialized master prompt lanes (HitRate prompts are intentionally not generated — dropped pending a redesign):
  - `1_Master_Cards_MLB_pack_YYYY-MM-DD.txt`, `1_Master_Cards_WNBA_pack_YYYY-MM-DD.txt`, `1_Master_Cards_Both_pack_YYYY-MM-DD.txt`: Filtered for 2.0+ Unit Recommended Candidates, each further restricted to its Master Card market whitelist (MLB: Moneyline, Spread, Strikeouts, Total Bases; WNBA: Moneyline, Spread, Points, Assists, Rebounds, Points+Assists, Points+Rebounds, Rebounds+Assists, Points+Assists+Rebounds) and odds -250 through +150; each file is omitted when no rows qualify.
  - `2_Master_Totals_pack_YYYY-MM-DD.txt`: Game Totals and Team Totals ONLY (Alternate Team Totals excluded).
  - `3_Master_Alt_Total_MLB_pack_YYYY-MM-DD.txt` and `3_Master_Alt_Total_WNBA_pack_YYYY-MM-DD.txt`: strict league-specific non-spread alternate game/team bankroll lines (L5>=75%, L10>=75%, full-game, Hard Rock/Fanatics/Midnite/DraftKings/Novig, odds -1000 through -110) when qualifying rows exist. The compatibility `*_alt_bankroll_props.csv` files remain mixed, but SPREAD rows are excluded from these prompt payloads.
  - `4_Master_Alt_Player_Prop_pack_YYYY-MM-DD.txt`: strict full-game Hard Rock/Fanatics/Midnite/DraftKings/Novig player alternate lines (L5>=75%, L10>=75%, odds -1000 through -110); omitted when no rows qualify.
  - `5_Master_Alt_Spread_MLB_pack_YYYY-MM-DD.txt` and `5_Master_Alt_Spread_WNBA_pack_YYYY-MM-DD.txt`: dedicated strict full-game spreads from `mlb_alt_spreads.csv` / `wnba_alt_spreads.csv`, with authoritative `selection` and explicit `signed_line`; omitted when no rows qualify. Prefix `5_` preserves all existing prompt numbers and downstream consumers.

  "Alt Total" and "Alt Player Prop" are both bankroll-style plays (low variance, high probability) — the market type differs (game/team total vs. player prop), not the underlying strategy, and their filter thresholds are aligned.
- `Desk2_Manual/` (opt-in only): The strict sequential phase prompts (`1_PhaseQ...`, `2_PhaseR...`, etc.), generated only with `--include-sequential-prompts`.

Each run archives old pack files into an `archive/` subfolder.

### 3. Consolidated Playable Props Export (`today` folder)

When the user requests to view or export playable props directly into the `today` folders:
1. Extract candidate rows from `packs/YYYY-MM-DD/candidates.csv` across:
   - **Player Props (`SO`)**: Pitcher Strikeouts (full-game, whitelisted per House Rules).
   - **Team Props (`TEAM_PROP`)**: Team Run Totals.
   - **Game Lines (`GAMELINE`)**: Game Totals, Moneylines, and Spreads with edge.
2. Generate consolidated files:
   - `playable_props.csv` and `playable_props_YYYY-MM-DD.csv`
   - `playable_props.md` and `playable_props_YYYY-MM-DD.md`
   Including: Selection, Type, Matchup, Line, Price, Book, Model Win %, Implied %, Edge %, Hit Rate %, Projection Mean, Board, Actionable, Flags.
3. Save using `safe_copy` retry loops into:
   - `C:\Users\dasil\OneDrive\Desktop\today`
   - `C:\Users\dasil\OneDrive\Desktop\today\extracted_data_YYYY-MM-DD_latest`
   - `G:\My Drive\today`
   - `G:\My Drive\today\extracted_data_YYYY-MM-DD_latest`

### 4. Run or Hand Off for Analysis

If the user also explicitly asks in the current turn to analyze generated prompts, route by folder:

- `Desk1_Automated/`: follow `.agents/skills/analyze-outlier-generic-prompts/SKILL.md`. Its shared CLI runner can send all latest master prompts to Codex, Claude, Gemini, and Grok, then save reports under `BETTING REPORTS/GENERIC/YYYY-MM-DD`.
- `Desk2_Manual/`: follow `.agents/skills/analyze-outlier-sequential-prompts/SKILL.md`. Its shared CLI runner executes Q → R → W → X → S with the fixed provider assignments and saves phase reports under `BETTING REPORTS/SEQUENTIAL/YYYY-MM-DD`.

If the user requested only data and prompt export, stop after reporting the generated paths. Prompt creation by itself does not authorize reasoning-model use.

### 5. Post-Game Slate Accuracy Audit

When the user asks to check actual game stats against candidate props:
1. Query the official MLB Stats API schedule and box scores:
   `https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=YYYY-MM-DD&hydrate=boxscore,linescore`
2. Extract actual starter pitcher strikeouts (`stats.pitching.strikeOuts`) and final team runs (`teams.<side>.score`).
3. Grade each selection as **HIT (WIN)** or **MISS (LOSS)** against the line.
4. Calculate and report:
   - Strikeout prop hit count, loss count, and overall win percentage.
   - Board `A_FLAGGED` vs Board `B` win percentage and ROI.
   - Team total under/over hit count and win percentage.

