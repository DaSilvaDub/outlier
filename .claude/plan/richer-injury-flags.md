# Plan: P0 richer injury flags

**Branch:** `feat/richer-injury-flags`
**Choice:** Option A — compact single-column `injury_flags` string
**Status:** Implemented (Execute)

## Format
`Name (Status; Body; ret YYYY-MM-DD): analysis…`

- Body = nested `injury.injury`
- Return = `returnDate` date-only
- Analysis truncated at 160 chars
- Missing pieces omitted; multi-player joined with ` | `

## Files
- `outlier_scrapers/pack.py` — `_format_injury`, `_injury_return_date`, `_INJURY_ANALYSIS_MAX_CHARS`
- `tests/test_pack.py` — expectations + richer case
- `tests/test_games.py` — e2e asserts body present

## Out of scope
- New CSV columns, IL smart-filter, headline/shortText tokens, fetch-chain changes
