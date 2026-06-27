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
