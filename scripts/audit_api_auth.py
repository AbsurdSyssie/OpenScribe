#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.db_utils import (
    ensure_database_exists,
    ensure_safe_test_database_url,
    ensure_safe_test_rate_limit_storage_url,
)


TEST_DATABASE_URL = ensure_safe_test_database_url()
TEST_RATE_LIMIT_STORAGE_URL = ensure_safe_test_rate_limit_storage_url()
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["RATE_LIMIT_STORAGE_URL"] = TEST_RATE_LIMIT_STORAGE_URL

from app.api_route_audit import AuditScenario, missing_route_specs, run_negative_audit
from app.db import Base, get_db
from app.main import app
from app.models import SessionAuthLevel, Team, TeamRole, TeamStatus, User, UserOnboardingState
from app.normalization import normalize_email, normalize_team_name_key
from app.services.admin import hash_password
from app.services.auth import create_session


ensure_database_exists(TEST_DATABASE_URL)
engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
rate_limit_redis = Redis.from_url(TEST_RATE_LIMIT_STORAGE_URL)


def reset_public_schema() -> None:
    engine.dispose()
    with engine.connect() as connection:
        connection = connection.execution_options(isolation_level="AUTOCOMMIT")
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public AUTHORIZATION CURRENT_USER"))
        connection.execute(text("GRANT ALL ON SCHEMA public TO CURRENT_USER"))


def seed_users(db: Session) -> dict[str, str]:
    team = Team(
        name="Audit Team",
        name_key=normalize_team_name_key("Audit Team"),
        status=TeamStatus.active,
        default_retention_days=30,
    )
    db.add(team)
    db.flush()

    onboarding_user = User(
        email=normalize_email("onboarding@example.com"),
        password_hash=hash_password("password-1"),
        team_id=team.id,
        team_role=TeamRole.user,
        is_system_admin=False,
        onboarding_state=UserOnboardingState.pending_password_change,
        must_change_password=True,
        mfa_required=True,
        mfa_enabled=False,
    )
    pending_mfa_user = User(
        email=normalize_email("pending.mfa@example.com"),
        password_hash=hash_password("password-1"),
        team_id=team.id,
        team_role=TeamRole.user,
        is_system_admin=False,
        onboarding_state=UserOnboardingState.complete,
        must_change_password=False,
        mfa_required=True,
        mfa_enabled=True,
    )
    full_user = User(
        email=normalize_email("full.user@example.com"),
        password_hash=hash_password("password-1"),
        team_id=team.id,
        team_role=TeamRole.user,
        is_system_admin=False,
        onboarding_state=UserOnboardingState.complete,
        must_change_password=False,
        mfa_required=False,
        mfa_enabled=False,
    )
    leader = User(
        email=normalize_email("leader@example.com"),
        password_hash=hash_password("password-1"),
        team_id=team.id,
        team_role=TeamRole.leader,
        is_system_admin=False,
        onboarding_state=UserOnboardingState.complete,
        must_change_password=False,
        mfa_required=False,
        mfa_enabled=False,
    )
    admin = User(
        email=normalize_email("admin@example.com"),
        password_hash=hash_password("password-1"),
        team_id=None,
        team_role=None,
        is_system_admin=True,
        onboarding_state=UserOnboardingState.complete,
        must_change_password=False,
        mfa_required=False,
        mfa_enabled=False,
    )
    db.add_all([onboarding_user, pending_mfa_user, full_user, leader, admin])
    db.commit()
    return {
        "onboarding": create_session(db, onboarding_user),
        "pending_mfa": create_session(db, pending_mfa_user, auth_level=SessionAuthLevel.pending_mfa),
        "full_user": create_session(db, full_user),
        "leader": create_session(db, leader),
        "admin": create_session(db, admin),
    }


def build_client() -> TestClient:
    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.state.db_session_factory = SessionLocal
    return TestClient(app)


def build_scenarios(session_tokens: dict[str, str]) -> dict[str, AuditScenario]:
    return {
        "anonymous": AuditScenario(name="anonymous"),
        "invalid_cookie": AuditScenario(name="invalid_cookie", session_cookie="invalid-session-cookie"),
        "onboarding": AuditScenario(name="onboarding", session_cookie=session_tokens["onboarding"]),
        "pending_mfa": AuditScenario(name="pending_mfa", session_cookie=session_tokens["pending_mfa"]),
        "full_user": AuditScenario(name="full_user", session_cookie=session_tokens["full_user"]),
        "leader": AuditScenario(name="leader", session_cookie=session_tokens["leader"]),
        "admin": AuditScenario(name="admin", session_cookie=session_tokens["admin"]),
    }


def print_report(results) -> None:
    for result in results:
        parts = [
            f"{result.case.method} {result.case.path}",
            f"tier={result.case.access_tier.value}",
            "ok" if result.ok else "FAIL",
        ]
        for observation in result.observations:
            parts.append(
                f"{observation.scenario}:{observation.status_code}/{observation.error_code or '-'}"
            )
        print(" | ".join(parts))


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit API route auth behavior.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON output.")
    args = parser.parse_args()

    missing = missing_route_specs()
    if missing:
        for method, path in sorted(missing):
            print(f"Missing audit spec for {method} {path}")
        return 2

    reset_public_schema()
    rate_limit_redis.flushdb()
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        session_tokens = seed_users(db)

    client = build_client()
    try:
        scenarios = build_scenarios(session_tokens)
        results = run_negative_audit(client, scenarios)
    finally:
        client.close()
        app.dependency_overrides.clear()
        rate_limit_redis.flushdb()

    if args.as_json:
        print(json.dumps([asdict(result) for result in results], indent=2, default=str))
    else:
        print_report(results)

    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
