from __future__ import annotations

import argparse
import email
import email.utils
import imaplib
import logging
import os
import re
import sys

from .paths import otp_code_file

logger = logging.getLogger(__name__)


def fetch_and_write_otp(attempt_timestamp: float) -> bool:
    server = os.getenv("OUTLIER_IMAP_SERVER", "imap.gmail.com").strip()
    user = (os.getenv("OUTLIER_IMAP_USER", "") or os.getenv("OUTLIER_EMAIL", "")).strip()
    password = os.getenv("OUTLIER_IMAP_PASSWORD", "").strip()

    if not server or not user or not password:
        logger.error(
            "IMAP credentials missing (OUTLIER_IMAP_SERVER, OUTLIER_IMAP_USER/OUTLIER_EMAIL, OUTLIER_IMAP_PASSWORD)."
        )
        return False

    sender_filter = os.getenv("OUTLIER_OTP_SENDER", "no-reply@outlier.bet").strip()

    try:
        mail = imaplib.IMAP4_SSL(server)
        mail.login(user, password)
        mail.select("inbox")

        # Basic IMAP search for ALL, we'll filter by date locally to be exact to the second
        # Using SINCE is only day-granularity in basic IMAP
        # But we can search FROM sender to reduce volume
        status, messages = mail.search(None, f'(FROM "{sender_filter}")')
        if status != "OK" or not messages[0]:
            mail.logout()
            return False

        message_ids = messages[0].split()
        # Sort so we check the newest first
        message_ids.reverse()

        valid_code: str | None = None

        for msg_id in message_ids:
            res, msg_data = mail.fetch(msg_id, "(BODY.PEEK[])")
            if res != "OK":
                continue

            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    date_tuple = email.utils.parsedate_tz(msg["Date"])
                    if date_tuple:
                        msg_time = email.utils.mktime_tz(date_tuple)
                        if msg_time < attempt_timestamp:
                            # It's older than our login attempt, and since we are iterating
                            # newest to oldest, all subsequent messages are older too.
                            break

                        # It's a new message
                        body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() in ["text/plain", "text/html"]:
                                    payload = part.get_payload(decode=True)
                                    if isinstance(payload, bytes):
                                        body += payload.decode(errors="ignore")
                        else:
                            payload = msg.get_payload(decode=True)
                            if isinstance(payload, bytes):
                                body = payload.decode(errors="ignore")

                        # Find 6-digit code
                        matches = re.findall(r"\b\d{6}\b", body)
                        if matches:
                            valid_code = matches[0]
                            break

            if valid_code:
                break

        mail.logout()

        if valid_code:
            logger.info("OTP fetch successful: otp_found=True")
            target = otp_code_file()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(valid_code, encoding="utf-8")
            return True
        else:
            logger.info("No matching OTP found after attempt timestamp.")
            return False

    except Exception as e:
        logger.error(f"Error fetching OTP via IMAP: {e}")
        return False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Outlier OTP via IMAP")
    parser.add_argument(
        "--attempt-timestamp", type=float, required=True, help="Unix timestamp of the login attempt"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = parse_args(argv)
    success = fetch_and_write_otp(args.attempt_timestamp)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
