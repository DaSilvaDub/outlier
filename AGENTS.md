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
