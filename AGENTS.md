# Standalone Outlier Scraper Guidance

- This repo is standalone. Do not import from or write into `nba-props-pipeline`.
- The NBA pipeline may be read as reference only.
- Never log tokens, cookies, raw auth headers, full Outlier payloads, or book-odds values in discovery reports.
- Keep `MLB` and `WNBA` V1 focused on discovery and props. Do not port RG, CTG, Hard Rock, DvP, playbook grading, or NBA edge scoring.
- Preserve unknown teams and markets through `_raw` fields instead of dropping rows.
- Run `pytest` before reporting completion.

# Known Technical Constraints
- **Playwright Event Loops**: Never use `time.sleep()` when waiting for page state changes or polling in Playwright scripts (e.g., `login.py`). It blocks the async event loop and stalls network requests. Always use `page.wait_for_timeout()` instead.
- **OpenAI Rate Limits**: The reasoning pipeline often triggers `429 Too Many Requests` due to the size of the daily data packs. Always ensure the OpenAI API client is initialized with `max_retries=5` (or greater) to allow exponential backoff to succeed.
