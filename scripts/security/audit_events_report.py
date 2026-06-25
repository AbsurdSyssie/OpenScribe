#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.db import SessionLocal
from app.services.audit_detection import parse_since, summarize_security_audit_events


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize metadata-only security audit events and detection signals.")
    parser.add_argument("--since", default="24h", help="Window start: 24h, 7d, or ISO timestamp. Default: 24h.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    parser.add_argument("--login-failure-threshold", type=int, default=5)
    parser.add_argument("--access-denied-threshold", type=int, default=5)
    parser.add_argument("--csrf-threshold", type=int, default=5)
    parser.add_argument("--validation-threshold", type=int, default=3)
    args = parser.parse_args()

    since = parse_since(args.since)
    with SessionLocal() as db:
        report = summarize_security_audit_events(
            db,
            since=since,
            login_failure_threshold=args.login_failure_threshold,
            access_denied_threshold=args.access_denied_threshold,
            csrf_threshold=args.csrf_threshold,
            validation_threshold=args.validation_threshold,
        )

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    print(f"Security audit report since {report['since']}")
    print(f"Events: {report['event_count']}")
    print("\nAction counts:")
    for action, count in report["action_counts"].items():
        print(f"  {action}: {count}")
    print("\nSignals:")
    if not report["signals"]:
        print("  none")
    for signal in report["signals"]:
        route = f" route={signal['route']}" if signal.get("route") else ""
        actor = f" actor={signal['actor_user_id']}" if signal.get("actor_user_id") else ""
        team = f" team={signal['team_id']}" if signal.get("team_id") else ""
        action = f" action={signal['action']}" if signal.get("action") else ""
        print(
            f"  [{signal['severity']}] {signal['signal']} count={signal['count']} key={signal['key']}"
            f"{action}{route}{actor}{team}"
        )
        if signal.get("note"):
            print(f"    {signal['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
