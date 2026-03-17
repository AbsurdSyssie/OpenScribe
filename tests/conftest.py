import os
import fcntl
from collections.abc import Generator
from typing import Callable

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.models import AccountRequest, AccountRequestStatus, Team, TeamRole, TeamStatus, User, UserOnboardingState, UserStatus
from app.normalization import normalize_email, normalize_team_name_key
from app.services.admin import hash_password
from tests.db_utils import (
    ensure_database_exists,
    ensure_safe_test_database_url,
    ensure_safe_test_rate_limit_storage_url,
)

from app.db import Base, get_db


TEST_RATE_LIMIT_STORAGE_URL = ensure_safe_test_rate_limit_storage_url()
os.environ["RATE_LIMIT_STORAGE_URL"] = TEST_RATE_LIMIT_STORAGE_URL

from app.main import app


TEST_DATABASE_URL = ensure_safe_test_database_url()
ensure_database_exists(TEST_DATABASE_URL)
test_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, future=True)
rate_limit_redis = Redis.from_url(TEST_RATE_LIMIT_STORAGE_URL)
TEST_RUN_LOCK_PATH = "/tmp/openscribe_pytest.lock"


def reset_public_schema() -> None:
    test_engine.dispose()
    with test_engine.connect() as connection:
        connection = connection.execution_options(isolation_level="AUTOCOMMIT")
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public AUTHORIZATION CURRENT_USER"))
        connection.execute(text("GRANT ALL ON SCHEMA public TO CURRENT_USER"))


@pytest.fixture(scope="session", autouse=True)
def test_run_lock() -> Generator[None, None, None]:
    lock_handle = open(TEST_RUN_LOCK_PATH, "w")
    try:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise pytest.Exit(
                "Another pytest run is already using the shared OpenScribe test database. "
                "Wait for it to finish before starting another test run."
            ) from exc
        yield
    finally:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            lock_handle.close()


@pytest.fixture(autouse=True)
def reset_database(request: pytest.FixtureRequest) -> Generator[None, None, None]:
    if request.node.get_closest_marker("migration"):
        yield
        return

    rate_limit_redis.flushdb()
    reset_public_schema()
    Base.metadata.create_all(bind=test_engine)
    yield
    rate_limit_redis.flushdb()


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def make_team(db_session: Session) -> Callable[..., Team]:
    def factory(
        *,
        name: str = "Primary Team",
        status: TeamStatus = TeamStatus.active,
        default_retention_days: int = 30,
    ) -> Team:
        team = Team(
            name=name,
            name_key=normalize_team_name_key(name),
            status=status,
            default_retention_days=default_retention_days,
        )
        db_session.add(team)
        db_session.commit()
        db_session.refresh(team)
        return team

    return factory


@pytest.fixture
def make_user(db_session: Session, make_team: Callable[..., Team]) -> Callable[..., User]:
    def factory(
        *,
        email: str = "user@example.com",
        full_name: str | None = None,
        password: str = "password-1",
        team: Team | None = None,
        team_role: TeamRole | None = TeamRole.user,
        is_system_admin: bool = False,
        status: UserStatus = UserStatus.active,
        mfa_required: bool = True,
        onboarding_state: UserOnboardingState = UserOnboardingState.complete,
        must_change_password: bool = False,
        mfa_enabled: bool = True,
    ) -> User:
        resolved_team = None if is_system_admin else (team or make_team())
        user = User(
            full_name=full_name,
            email=normalize_email(email),
            password_hash=hash_password(password),
            team_id=resolved_team.id if resolved_team else None,
            team_role=None if is_system_admin else team_role,
            is_system_admin=is_system_admin,
            status=status,
            must_change_password=must_change_password,
            onboarding_state=onboarding_state,
            mfa_required=mfa_required,
            mfa_enabled=mfa_enabled,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        return user

    return factory


@pytest.fixture
def make_account_request(db_session: Session) -> Callable[..., AccountRequest]:
    def factory(
        *,
        requested_name: str = "Requester Name",
        requested_email: str = "request@example.com",
        requested_team_name: str = "Primary Team",
        request_details: str | None = "Please create my account",
        status: AccountRequestStatus = AccountRequestStatus.pending,
    ) -> AccountRequest:
        account_request = AccountRequest(
            requested_name=requested_name,
            requested_email=normalize_email(requested_email),
            requested_team_name=requested_team_name,
            requested_team_name_key=normalize_team_name_key(requested_team_name),
            request_details=request_details,
            status=status,
        )
        db_session.add(account_request)
        db_session.commit()
        db_session.refresh(account_request)
        return account_request

    return factory


@pytest.fixture(autouse=True)
def stub_vault_secret_write(monkeypatch: pytest.MonkeyPatch):
    def fake_write_team_stt_bearer_token(*, team_id, config_id, bearer_token):
        return f"secret:openscribe/stt/team/{team_id}/config/{config_id}"

    def fake_delete_team_stt_bearer_token(*, team_id, config_id):
        return None

    def fake_read_team_stt_bearer_token(*, team_id, config_id):
        return "test-stt-token"

    monkeypatch.setattr("app.services.stt.write_team_stt_bearer_token", fake_write_team_stt_bearer_token)
    monkeypatch.setattr("app.services.stt.delete_team_stt_bearer_token", fake_delete_team_stt_bearer_token)
    monkeypatch.setattr("app.services.stt.read_team_stt_bearer_token", fake_read_team_stt_bearer_token)
