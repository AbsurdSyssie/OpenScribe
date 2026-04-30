#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.errors import AppError
from app.services.mail import MailMessage, load_mail_config_from_env, send_transactional_email


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a test transactional email through configured OpenScribe mail transport.")
    parser.add_argument("--to", required=True, help="Recipient email address.")
    parser.add_argument("--env-file", default=".env", help="Environment file to load before sending.")
    parser.add_argument("--subject", default="OpenScribe test email", help="Email subject.")
    args = parser.parse_args()

    load_env_file(REPO_ROOT / args.env_file)
    config = load_mail_config_from_env()
    message = MailMessage(
        purpose="mail_transport_test",
        to_email=args.to,
        subject=args.subject,
        text_body=(
            "This is an OpenScribe transactional email test.\n\n"
            "If you received this, the configured mail transport can send messages."
        ),
        html_body=(
            "<p>This is an OpenScribe transactional email test.</p>"
            "<p>If you received this, the configured mail transport can send messages.</p>"
        ),
        idempotency_key=f"openscribe-test-{uuid4()}",
    )
    try:
        result = send_transactional_email(message, config=config)
    except AppError as exc:
        print(f"Mail test failed: {exc.code}: {exc.message}", file=sys.stderr)
        if exc.details:
            print(f"Details: {exc.details}", file=sys.stderr)
        return 1

    print(f"Mail test {result.status} via {result.provider}.")
    if result.provider_message_id:
        print(f"Provider message id: {result.provider_message_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
