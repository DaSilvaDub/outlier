from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

from .auth import (
    build_cookie_header,
    extract_access_token,
    persist_storage_state,
    read_otp_code,
    write_otp_status,
    write_session_metadata,
)
from .browser_helpers import click_first_visible, is_on_props_page, wait_for_visible
from .paths import otp_code_file, storage_state_file
from .registry import supported_leagues

# Provenance: login/OTP flow adapted from
# C:\Users\dasil\Dev\GitHub\nba-props-pipeline\scrapers\outlier\Props_outlier_improved.py
# (login_and_save) as inspected on 2026-06-19. Manual capture is the supported
# default; credential autofill is best-effort and always falls back to manual.

LOGIN_URL = "https://app.outlier.bet/login"
APP_URL = "https://app.outlier.bet"

EMAIL_SELECTORS = [
    'input[type="email"]',
    'input[name="email"]',
    'input[placeholder*="email" i]',
    'input[id*="email" i]',
    "#email",
    'input[type="text"]',
]
PASSWORD_SELECTORS = [
    'input[type="password"]',
    'input[name="password"]',
    'input[autocomplete="current-password"]',
    "#password",
]
CONTINUE_SELECTORS = [
    'button:has-text("Continue")',
    'button:has-text("Next")',
    'button:has-text("Email")',
    'button[type="submit"]',
]
SUBMIT_SELECTORS = [
    'button[type="submit"]',
    'button:has-text("Log in")',
    'button:has-text("Login")',
    'button:has-text("Sign in")',
    'input[type="submit"]',
]
CODE_SELECTORS = [
    'input[autocomplete="one-time-code"]',
    'input[name="code"]',
    "#code",
    'input[inputmode="numeric"]',
]
CODE_SUBMIT_SELECTORS = [
    'button:has-text("Confirm code")',
    'button[type="submit"]',
    'input[type="submit"]',
]


def _is_interactive() -> bool:
    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except Exception:
        return False


def _logged_in(page: Any, league: str) -> bool:
    url = str(getattr(page, "url", "") or "").lower()
    if not url or "login" in url or "signin" in url:
        return False
    if is_on_props_page(page, league):
        return True
    # Generic success: on the app and off the login route.
    return "app.outlier.bet" in url


def _try_credential_autofill(page: Any, *, timeout_seconds: int) -> None:
    """Best-effort autofill when OUTLIER_EMAIL/OUTLIER_PASSWORD are set."""
    email = os.getenv("OUTLIER_EMAIL", "").strip()
    password = os.getenv("OUTLIER_PASSWORD", "").strip()
    if not email:
        return

    email_input = wait_for_visible(page, EMAIL_SELECTORS, timeout=4000)
    if email_input:
        try:
            email_input.click()
            email_input.fill(email)
        except Exception:
            return

    password_input = wait_for_visible(page, PASSWORD_SELECTORS, timeout=2500)
    if not password_input and click_first_visible(page, CONTINUE_SELECTORS, timeout=2500):
        password_input = wait_for_visible(page, PASSWORD_SELECTORS, timeout=5000)

    if password_input and password:
        try:
            password_input.click()
            password_input.fill(password)
        except Exception:
            pass
        if not click_first_visible(page, SUBMIT_SELECTORS, timeout=3000):
            try:
                password_input.press("Enter")
            except Exception:
                pass

    # Emailed one-time code path: prefer OUTLIER_OTP_CODE / otp_code.txt.
    code_input = wait_for_visible(page, CODE_SELECTORS, timeout=4000)
    if not code_input:
        return
    code = (
        os.getenv("OUTLIER_LOGIN_CODE", "").strip()
        or os.getenv("OUTLIER_OTP_CODE", "").strip()
    )
    if not code:
        otp_code_file().unlink(missing_ok=True)
        write_otp_status(
            "waiting_for_code",
            code_file=str(otp_code_file()),
            timeout_seconds=timeout_seconds,
        )
        deadline = time.monotonic() + max(30, timeout_seconds)
        while time.monotonic() < deadline and not code:
            code = read_otp_code()
            if code:
                break
            page.wait_for_timeout(2000)
    if not code:
        return
    try:
        write_otp_status("submitting_code")
        code_input.click()
        code_input.fill(code)
        if not click_first_visible(page, CODE_SUBMIT_SELECTORS, timeout=3000):
            code_input.press("Enter")
    except Exception:
        pass


def capture_session(*, league: str, timeout_seconds: int, headless: bool) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "Playwright is required. Install with:\n"
            "  pip install playwright\n"
            "  python -m playwright install chromium"
        )
        return 2

    interactive = _is_interactive()
    target = storage_state_file()
    write_otp_status("waiting_for_login", final_url=LOGIN_URL, league=league)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            _try_credential_autofill(page, timeout_seconds=timeout_seconds)

            if interactive and not headless:
                print(
                    "\nA browser window is open. Log in to Outlier "
                    "(email + password/OTP) until you reach the app.\n"
                    "Then press ENTER here to capture the session..."
                )
                input()
            else:
                # Non-interactive (or headless): poll until login completes.
                deadline = time.monotonic() + max(30, timeout_seconds)
                while time.monotonic() < deadline and not _logged_in(page, league):
                    page.wait_for_timeout(2000)

            # Capture context-wide storage (cookies + localStorage across all
            # pages and popups), not just the original tab's URL. Login usually
            # finishes via a redirect or popup, so we trust an explicit ENTER and
            # validate by what we actually captured rather than the page URL.
            storage_state = context.storage_state()
        finally:
            browser.close()

    token_ok = False
    try:
        extract_access_token(storage_state)
        token_ok = True
    except Exception:
        token_ok = False
    cookie_ok = bool(build_cookie_header(storage_state))

    if not (token_ok or cookie_ok):
        write_otp_status("failed")
        print(
            "No Outlier auth material was captured (no token in localStorage and "
            "no outlier.bet cookies).\n"
            "Make sure you logged in INSIDE the browser window this command opened "
            "(not your normal browser), and reached the app before pressing ENTER. "
            "Then re-run."
        )
        return 1

    persist_storage_state(storage_state)

    write_session_metadata(
        {
            "captured_at_url": APP_URL,
            "league_hint": league,
            "token_found": token_ok,
        }
    )
    write_otp_status("authenticated", token_found=token_ok)

    print(f"Saved session -> {target}")
    if not token_ok:
        print(
            "WARNING: saved the session, but no access token was found in localStorage. "
            "Cookies may still authenticate the API; run `discover` to confirm. "
            "If it returns auth_required, share the storage_state shape so the auth "
            "parser can be adjusted."
        )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture a fresh Outlier session (Playwright storage_state)."
    )
    parser.add_argument(
        "--league",
        choices=supported_leagues(),
        default="MLB",
        help="League used only to recognize the props page on success (default: MLB).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Seconds to wait for login completion in non-interactive/headless mode.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run headless (only works with credential+OTP env autofill).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return capture_session(
        league=args.league,
        timeout_seconds=args.timeout,
        headless=args.headless,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
