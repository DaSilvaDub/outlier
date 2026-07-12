---
name: no-reasoning-unless-asked
enabled: true
event: bash
action: warn
pattern: "python\\s+-m\\s+outlier_scrapers\\.(reasoning|gemini_research|c_research|claude_reasoning|claude_synthesis|run_desk)|daily_job\\s+.*--run-reasoning|pytest\\s+.*test_(reasoning|gemini_research|claude_reasoning|claude_synthesis|c_research)"
---

**HOUSE RULE — reasoning is OFF by default (all ents).**

You are about to invoke a paid / network reasoning-desk path.

**Only proceed if the user explicitly asked this turn** for desk/reasoning
(e.g. "run the desk", "run reasoning", "run A/B/C/D/E").

If they did **not** clearly ask: **abort this command**. Do pack/scrapers/offline
unit tests/code work instead, or ask the user first.

Canonical rule: `AGENTS.md` → "Never run reasoning models unless explicitly asked"
and `docs/ENT-SYNC-GLOBAL-PROMPT.md`.
