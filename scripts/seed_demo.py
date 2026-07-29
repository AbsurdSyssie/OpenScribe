from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.models import Team
from app.services.local_demo import bootstrap_local_demo


DEFAULT_MARKER_PATH = Path("/app/.local/demo/bootstrap-complete")
DEMO_BOOTSTRAP_VERSION = "openscribe_local_demo_v2"


def require_demo_bootstrap_enabled() -> None:
    environment = os.getenv("APP_ENV", "").strip().lower()
    if environment not in {"local", "dev", "development"}:
        raise SystemExit(
            "The demo seed may run only when APP_ENV is local or development."
        )
    enabled = os.getenv("DEMO_BOOTSTRAP_ENABLED", "").strip().lower()
    if enabled not in {"1", "true", "yes"}:
        raise SystemExit(
            "DEMO_BOOTSTRAP_ENABLED=true is required to run the demo seed."
        )


def _required_env(name: str, default: str) -> str:
    value = os.getenv(name, default).strip()
    if not value:
        raise SystemExit(f"{name} must not be empty")
    return value


def marker_matches_database(db: Session, *, marker_path: Path) -> bool:
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
        team_id = UUID(str(payload["team_id"]))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False
    if payload.get("version") != DEMO_BOOTSTRAP_VERSION:
        return False
    return db.scalar(select(Team.id).where(Team.id == team_id).limit(1)) is not None


def main() -> None:
    require_demo_bootstrap_enabled()
    marker_path = Path(os.getenv("DEMO_BOOTSTRAP_MARKER", str(DEFAULT_MARKER_PATH)))
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    team_name = _required_env("DEMO_TEAM_NAME", "OpenScribe Demo Team")
    password = _required_env("DEMO_PASSWORD", "OpenScribeLocal27")
    admin_email = _required_env("DEMO_ADMIN_EMAIL", "admin@openscribe.local")
    leader_email = _required_env("DEMO_LEADER_EMAIL", "leader@openscribe.local")
    clinician_email = _required_env("DEMO_CLINICIAN_EMAIL", "clinician@openscribe.local")

    engine = create_engine(database_url, future=True)
    with Session(engine) as db:
        if marker_path.is_file() and marker_matches_database(db, marker_path=marker_path):
            print("Local demo data already exists. No accounts or content were changed.")
            return
        accounts = bootstrap_local_demo(
            db,
            team_name=team_name,
            admin_email=admin_email,
            leader_email=leader_email,
            clinician_email=clinician_email,
            password=password,
        )

    marker_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_marker = marker_path.with_suffix(".tmp")
    temporary_marker.write_text(
        json.dumps(
            {
                "team_id": accounts["team_id"],
                "version": DEMO_BOOTSTRAP_VERSION,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary_marker.replace(marker_path)

    print("OpenScribe local demo is ready.")
    print("URL: http://127.0.0.1:8080")
    print(f"Admin: {accounts['admin_email']}")
    print(f"Team leader: {accounts['leader_email']}")
    print(f"Clinician: {accounts['clinician_email']}")
    print("Guide: docs/local-demo.md")


if __name__ == "__main__":
    main()
