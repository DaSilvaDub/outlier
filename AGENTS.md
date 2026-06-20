# Standalone Outlier Scraper Guidance

- This repo is standalone. Do not import from or write into `nba-props-pipeline`.
- The NBA pipeline may be read as reference only.
- Never log tokens, cookies, raw auth headers, full Outlier payloads, or book-odds values in discovery reports.
- Keep `MLB` and `WNBA` V1 focused on discovery and props. Do not port RG, CTG, Hard Rock, DvP, playbook grading, or NBA edge scoring.
- Preserve unknown teams and markets through `_raw` fields instead of dropping rows.
- Run `pytest` before reporting completion.

