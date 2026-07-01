# Project state — read first

_Last updated: 2026-06-30 by codex_

## Current state
- Prompt C is automated as a grounded Gemini injury/lineup pass on branch
  `codex/prompt-c-research` (`2bf8ffd`), based on canonical `master` history.
- Prompt C anchors research to the pack date/as-of timestamp and rejects any finding whose
  market ID, selection, line, or price differs from `candidates.csv`.
- A/B/C/D/E desk wiring is present without the recovery commit's recreated support stubs or
  fake local-synthesis placeholder.
- Focused tests pass 12/12; the full suite passes 207/207; Ruff is clean.

## Open follow-ups
- [ ] Run a paid Prompt C smoke test when Gemini quota is intentionally available.
- [ ] Restore OpenAI capacity, Gemini quota, and Anthropic credits before expecting a FULL desk.
- [ ] Investigate 10 MLB prop line-movement fetch errors and zero WNBA candidate/game rows.
- [ ] Prevent pytest missing-key tests from reloading the real `.env` and making live API calls.
- [ ] Do not push the disconnected local recovery commit `d05eb21`; use `2bf8ffd` instead.

## How to update this file
Overwrite the two sections above at the end of your session. Keep it short —
this is the 10-second briefing, not the full history (that's in the dated
entries and `git log`).
