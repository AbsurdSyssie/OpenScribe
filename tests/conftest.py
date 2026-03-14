import os
from collections.abc import Generator
from typing import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.models import AccountRequest, AccountRequestStatus, Team, TeamRole, TeamStatus, User, UserOnboardingState, UserStatus
from app.normalization import normalize_email, normalize_team_name_key
from app.services.admin import hash_password
from tests.db_utils import ensure_database_exists, ensure_safe_test_database_url

from app.db import Base, get_db
from app.main import app


TEST_DATABASE_URL = ensure_safe_test_database_url()
ensure_database_exists(TEST_DATABASE_URL)
test_engine = create_engine(TEST_DATABASE_URL, future=True)
TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, future=True)


@pytest.fixture(autouse=True)
def reset_database(request: pytest.FixtureRequest) -> Generator[None, None, None]:
    if request.node.get_closest_marker("migration"):
        yield
        return

    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield
    with test_engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(text(f"TRUNCATE TABLE {table.name} RESTART IDENTITY CASCADE"))


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
