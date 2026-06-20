# Standalone Outlier Multi-Sport Scraper

This repo is intentionally separate from `nba-props-pipeline`.

V1 supports Outlier discovery and props export for `MLB` and `WNBA`.
The NBA pipeline is read-only reference only; this project does not import from it
and does not write into it.

## Session State

Place a fresh Outlier Playwright storage state at one of:

- `config/.outlier_session/storage_state.json`
- `config/outlier_session.json`

The current saved sessions in the NBA repo returned `403`, so live commands will
likely require re-auth before they can fetch data.

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
python -m outlier_scrapers.props --league MLB --all
python -m outlier_scrapers.props --league WNBA --all
python -m outlier_scrapers.refresh --league MLB --league WNBA --discover --props
```

## Tests

```powershell
pytest
```
