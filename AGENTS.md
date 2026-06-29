# Standalone Outlier Scraper Guidance

- This repo is standalone. Do not import from or write into `nba-props-pipeline`.
- The NBA pipeline may be read as reference only.
- Never log tokens, cookies, raw auth headers, full Outlier payloads, or book-odds values in discovery reports.
- Keep `MLB` and `WNBA` V1 focused on discovery and props. Do not port RG, CTG, Hard Rock, DvP, playbook grading, or NBA edge scoring.
- Preserve unknown teams and markets through `_raw` fields instead of dropping rows.
- Run `pytest` before reporting completion.

# Known Technical Constraints
- **Playwright Event Loops**: Never use `time.sleep()` when waiting for page state changes or polling in Playwright scripts (e.g., `login.py`). It blocks the async event loop and stalls network requests. Always use `page.wait_for_timeout()` instead.
- **API Rate Limits**: Large daily data packs frequently trigger `429 Too Many Requests` across OpenAI and Gemini APIs. The native SDK `max_retries` are often insufficient. Implement custom `time.sleep()` backoff loops (e.g., 30s) catching `RateLimitError` or 429 exceptions to ensure recovery.
- **Anthropic Constraints**: When using Claude APIs, ensure `max_tokens` does not exceed `8192` for Sonnet 3.5. Avoid passing unsupported kwargs like `thinking={"type": "adaptive"}` or `output_config` which will trigger `400 Bad Request`. Use exact valid model IDs (e.g., `claude-3-5-sonnet-20241022`).
- **Synthesis Fallback**: If external reasoning models (Claude/Gemini/OpenAI) fail due to hard blockers like insufficient API credits, you must bypass the external scripts and manually synthesize the final betting report yourself by directly reading the generated `briefing.md` and `candidates.csv` files.
- Use the `outlier-ai-desk` skill (`.agents/skills/outlier-ai-desk/SKILL.md`) after packing to run the full A/B/D/E desk, produce `reasoning_status.json`, and guarantee a report (with local synthesis fallback). Always run `pytest` before completion.

# Multi-Agent Sync Protocol (READ FIRST, EVERY SESSION)

Multiple AI agents work in this repo at different times: Claude (CLI + Desktop),
Codex CLI, Grok CLI, Antigravity CLI, and their desktop apps. To stay in sync,
**every agent MUST follow this protocol.** It is mandatory, not optional.

## Who am I?
State your identity in every commit using a trailer line:

    Agent: <name>

Valid names: `claude`, `codex`, `grok`, `antigravity`. Use the name that
matches the tool you are running in. The git hook reads this trailer to
attribute work, so a commit without it is logged as `unknown`.

## On session START (before touching anything)
1. Read `.agent-log/SUMMARY.md` — the current state of the project and any open
   follow-ups left by the last agent.
2. Read the most recent entries in `.agent-log/` (files are named
   `YYYY-MM-DD-<agent>[-<hash>].md`, newest dates last). Read at least the last
   3–5 to understand recent work.
3. Run `git log --oneline -15` to see what actually shipped.

## During the session
- Work normally. Keep `MLB`/`WNBA` V1 scope rules above in mind.

## On session END (before you stop — REQUIRED)
1. **Commit your work.** Never leave the repo dirty for the next agent. Use:

       git add -A
       git commit -m "<what changed>

       Agent: <name>"

   The `post-commit` hook auto-writes a log entry per commit to `.agent-log/`.
2. **Write a session note** capturing intent the diff can't show. Copy
   `.agent-log/_TEMPLATE.md` to `.agent-log/<YYYY-MM-DD>-<agent>.md` (append a
   `-2`, `-3` suffix if that file already exists today) and fill it in: what you
   did, why, what you changed, and **what you're leaving for the next agent.**
3. **Update `.agent-log/SUMMARY.md`** — overwrite the "Current state" and
   "Open follow-ups" sections so the next agent gets the gist in one read.

## Setup (run once per machine / fresh clone)
    git config core.hooksPath .githooks

Without this, the auto-logging hook will not fire.
