# Standalone Outlier Multi-Sport Scraper

This repo is intentionally separate from `nba-props-pipeline`.

V2 supports Outlier discovery, props, insights, and line-movement export for
`MLB` and `WNBA`.
The NBA pipeline is read-only reference only; this project does not import from it
and does not write into it.

## Session State

Place a fresh Outlier Playwright storage state at one of:

- `config/.outlier_session/storage_state.json`
- `config/outlier_session.json`

If live commands report `auth_required` from an HTTP `401`, capture a fresh
session before retrying. HTTP `403` can also be a transient throttle/WAF block
and is retried by the API client before being reported as a normal fetch error.

The `.outlier_session` path has precedence over the legacy `outlier_session.json`.
These files contain live auth material. They are ignored by git, but this folder is
under OneDrive, so keep that sync behavior in mind before storing long-lived tokens.

## Re-auth (capture a fresh session)

Use the built-in login command. It opens a browser; log in fully (email +
password/OTP) until you reach the app, then press ENTER to capture the session
to `config/.outlier_session/storage_state.json`:

```powershell
python -m playwright install chromium   # first time only
python -m outlier_scrapers.login
```

Notes:

- Manual capture is the supported default. If `OUTLIER_EMAIL` / `OUTLIER_PASSWORD`
  are set, it will best-effort autofill; for emailed one-time codes set
  `OUTLIER_OTP_CODE` or write the 6-digit code into
  `config/.outlier_session/otp_code.txt` while it waits.
- The command only saves a session if it detects you reached the app, so it will
  not write a stale-success file.
- After it saves, run `discover` to confirm the session works (status `ok`, not
  `auth_required`).

## Commands

```powershell
python -m outlier_scrapers.discover --league MLB --league WNBA
python -m outlier_scrapers.discover --league MLB --league WNBA --deep
python -m outlier_scrapers.props --league MLB --all
python -m outlier_scrapers.props --league WNBA --all
python -m outlier_scrapers.insights --league MLB --all
python -m outlier_scrapers.insights --league WNBA --all
python -m outlier_scrapers.line_movement --league MLB --all
python -m outlier_scrapers.line_movement --league WNBA --all
python -m outlier_scrapers.cards --league WNBA
python -m outlier_scrapers.refresh --league MLB --league WNBA --discover --props --insights --line-movement --cards --games --game-line-movement --game-cards
```

`--all` is shorthand for `--props --insights --line-movement --cards --games --game-line-movement --game-cards`, so the
usual one-command run is:

```powershell
python -m outlier_scrapers.refresh --league WNBA --all
```

## Daily Auto-Refresh and Headless Auth

To automate the daily pipeline, use the `daily_job` orchestrator. It verifies authentication, spawns a headless browser to re-auth if necessary (listening for emailed OTP codes via IMAP), runs the complete explicitly ordered refresh for all given leagues, and finally builds the combined offline Pack.

```powershell
python -m outlier_scrapers.daily_job --leagues MLB,WNBA
```

You can append `--run-reasoning` to opt into the paid Prompt A reasoning pass after
packing. It uses the OpenAI Responses API with the fixed model `gpt-5.5`,
`reasoning.effort="xhigh"`, no web tools, and `store=False`.

```powershell
python -m outlier_scrapers.daily_job --leagues MLB,WNBA --run-reasoning
```

The reasoning runner requires `OPENAI_API_KEY` in the process environment or the
ignored project-root `.env`. API billing is separate from a ChatGPT subscription.
Never commit, print, or copy the key into prompts, logs, packs, or temporary source
trees.

The result is written to `packs/YYYY-MM-DD/chatgpt_a.md`. Daily-job runs compare a
request hash covering the candidates, Prompt A, role rules, model, and effort; an
unchanged request reuses the existing output without another API charge. A changed
request replaces the old analysis. Pack generation remains available if reasoning
fails, but the requested daily job returns a nonzero exit code.

Prompt A can also be run independently. Existing output is preserved unless
`--force` is supplied:

```powershell
python -m outlier_scrapers.reasoning --date YYYY-MM-DD
python -m outlier_scrapers.reasoning --date YYYY-MM-DD --force
```

`xhigh` reasoning can be slower and more expensive than lower-effort calls. Check
API billing and project limits before enabling it in a scheduled task.

The new standalone runners for Prompts B (Gemini), D (Claude reasoning), and E (Claude synthesis) can also be invoked directly:

```powershell
python -m outlier_scrapers.gemini_research --date YYYY-MM-DD
python -m outlier_scrapers.claude_reasoning --date YYYY-MM-DD
python -m outlier_scrapers.claude_synthesis --date YYYY-MM-DD
```

These require the matching environment variables (`GEMINI_API_KEY` for B; `ANTHROPIC_API_KEY` for D/E) and consume paid API quota. Prompt E (synthesis) requires the outputs of A, B, and D to already exist for the date. See the runbook for full details and caveats: Prompt B is a single Google-Search-grounded generation pass (not the full multi-step Gemini Deep Research UI); Prompt C remains a manual Deep Research paste.

### Full AI Research Desk Orchestration

After a pack is produced (by `daily_job` or `pack`), use the canonical `outlier-ai-desk` agent/skill to run the complete desk:

- Invokes A/B/D/E runners (only when their keys are present and hashes indicate a change is needed)
- Writes `reasoning_status.json` (FULL / PARTIAL / DATA_ONLY)
- Always guarantees a final betting report (uses successful `claude_e.md` or falls back to local pack synthesis)
- Runs `pytest` before completion

The skill lives at `.agents/skills/outlier-ai-desk/SKILL.md`. It is the recommended way to finish a slate instead of calling the individual runners by hand. When paid models are unavailable or rate-limited, it transparently falls back to the `synthesize-outlier-pack` logic so you still get a usable report.

To run this daily at ~8:00 AM local time via Windows Task Scheduler, create a basic task that executes the script within the project virtual environment. Do not hardcode secrets in the task; rely on the user's persisted environment variables or the `.env` file in the project root.

Example Task Scheduler action:
- **Program/script**: `C:\path\to\your\venv\Scripts\python.exe`
- **Add arguments**: `-m outlier_scrapers.daily_job --leagues MLB,WNBA`
- **Start in**: `C:\Users\dasil\OneDrive\Documents\outlier`

## Triage cards

`cards` joins the latest normalized props, line movement, EV, and insights into
one ranked per-`market_id` view, written to `data/<LEAGUE>/cards/`:

- `cards_latest.json` — full payload (both boards, coverage, snapshot skew).
- `cards_latest.html` — self-contained, openable review artifact.

It reads `*_latest.json` only, so run it after the scrape feeds (refresh does
this for you when `--cards`/`--all` is passed; `cards` runs last and is skipped
if upstream props/line-movement failed). Two boards: **A** = verified Outlier EV
(`AVERAGE` method), **B** = signal candidates (descriptive composite; the
`proxy_market` edge is a market signal, not EV). `refresh --cards` alone needs no
API session — it just rebuilds the board from existing files.

For a fast smoke run, limit market-detail calls:

```powershell
python -m outlier_scrapers.line_movement --league MLB --limit 25 --workers 4
```

Line movement reads market IDs from the latest normalized props file. Refresh
props first for the current slate, or use `--require-fresh-props` to stop before
fetching if that props file is stale.

Persistent HTTP `403` market-detail failures get up to three cooldown mop-up
rounds by default. Use `--retry-403-max-rounds`,
`--retry-403-cooldown-seconds`, `--retry-403-workers`, or
`--no-retry-failed-403` to tune or disable that recovery. Only residual 403s
advance to the next round; terminal errors such as 404s remain fetch errors.

When Outlier returns `market.evOutcomes`, line movement also normalizes EV
candidates into the `ev_records` secondary array and surfaces EV counts in the
status report.

## Tests

```powershell
pytest
```
