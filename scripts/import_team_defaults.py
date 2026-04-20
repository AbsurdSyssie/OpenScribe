#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.errors import AppError
from app.models import User
from app.services.default_assets import import_team_assets_to_defaults


def _resolve_admin(db: Session, *, admin_email: str | None) -> User:
    if admin_email:
        admin = db.scalar(select(User).where(User.email == admin_email.strip().lower(), User.is_system_admin.is_(True)))
        if admin is None:
            raise SystemExit(f"System admin not found for email: {admin_email}")
        return admin

    admins = list(db.scalars(select(User).where(User.is_system_admin.is_(True)).order_by(User.created_at.asc(), User.id.asc())))
    if not admins:
        raise SystemExit("No system admin accounts found")
    if len(admins) > 1:
        emails = ", ".join(admin.email for admin in admins)
        raise SystemExit(f"Multiple system admin accounts found; pass --admin-email. Available: {emails}")
    return admins[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy one team's team-scoped templates and quick actions into default assets.")
    parser.add_argument("--team-name", required=True, help="Source team name to copy from.")
    parser.add_argument("--admin-email", default=None, help="System admin email to attribute the copied defaults to.")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    engine = create_engine(database_url, future=True)
    with Session(engine) as db:
        admin = _resolve_admin(db, admin_email=args.admin_email)
        try:
            summary = import_team_assets_to_defaults(db, admin, source_team_name=args.team_name)
        except AppError as exc:
            print(exc.message)
            return 1

    print(
        f"Imported defaults from {summary.source_team_name}: "
        f"templates={summary.templates_imported}, quick_actions={summary.quick_actions_imported}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
