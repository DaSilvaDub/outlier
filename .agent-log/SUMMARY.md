# Project state — read first

_Last updated: 2026-06-29 by codex_

## Current state
- Fresh 2026-06-29 MLB/WNBA pack generated at 11:19 EDT with 25 candidates.
- External reasoning A/B/D failed after bounded attempts: OpenAI HTTP 429, Gemini zero quota, and Anthropic insufficient credits; E was gated and not invoked.
- Local fallback report rebuilt successfully; `reasoning_status.json` is `PARTIAL` and points to `manual_betting_report.md`.
- Report-to-CSV validation passed for all 25 exact market IDs, selections, lines, and prices.
- Focused tests passed 46/46 and the full suite passed 195/195.

## Open follow-ups
- [ ] Restore OpenAI capacity, Gemini quota, and Anthropic credits before expecting a FULL desk.
- [ ] Investigate 10 MLB prop line-movement fetch errors and zero WNBA candidate/game rows.
- [ ] Prevent pytest missing-key tests from reloading the real `.env` and making live API calls.

## How to update this file
Overwrite the two sections above at the end of your session. Keep it short —
this is the 10-second briefing, not the full history (that's in the dated
entries and `git log`).
