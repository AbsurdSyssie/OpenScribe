import importlib
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models import Team, User


harness = importlib.import_module("conftest")


def test_pure_test_skips_database_reset(request, reset_database):
    assert "db_session" not in request.fixturenames
    assert reset_database == "skipped"


def test_first_database_prepare_rebuilds_schema(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(harness, "canonical_schema_ready", False)
    monkeypatch.setattr(harness, "reset_public_schema", lambda: calls.append("reset"))
    monkeypatch.setattr(harness.Base.metadata, "create_all", lambda *, bind: calls.append("create"))

    assert harness.prepare_canonical_schema() == "rebuild"
    assert calls == ["reset", "create"]
    assert harness.canonical_schema_ready is True


def test_later_database_prepare_reuses_canonical_schema(monkeypatch):
    monkeypatch.setattr(harness, "canonical_schema_ready", True)

    assert harness.prepare_canonical_schema() == "ready"


def test_transitive_database_fixture_prepares_database(client, request, reset_database):
    assert "db_session" in request.fixturenames
    assert reset_database in {"rebuild", "ready"}


@pytest.mark.real_db_connections
def test_truncate_application_tables_removes_data_and_foreign_key_rows(
    db_session,
    make_team,
    make_user,
):
    team = make_team()
    user = make_user(team=team)
    team_id = team.id
    user_id = user.id

    # Release this fixture session's read transaction before another connection
    # obtains TRUNCATE's exclusive lock.
    db_session.rollback()
    harness.truncate_application_tables()
    db_session.expire_all()

    assert db_session.scalar(select(func.count()).select_from(Team)) == 0
    assert db_session.scalar(select(func.count()).select_from(User)) == 0
    assert db_session.get(Team, team_id) is None
    assert db_session.get(User, user_id) is None


@pytest.mark.real_db_connections
def test_ordinary_transaction_commits_are_removed_by_outer_rollback():
    connection = harness.test_engine.connect()
    outer_transaction = connection.begin()
    session = harness.sessionmaker(
        bind=connection,
        autoflush=False,
        autocommit=False,
        future=True,
        join_transaction_mode="create_savepoint",
    )()
    team = Team(name="Rollback isolation team", name_key="rollback isolation team")
    try:
        session.add(team)
        session.commit()
        team_id = team.id
        assert session.get(Team, team_id) is not None
    finally:
        session.close()
        outer_transaction.rollback()
        connection.close()

    with harness.TestingSessionLocal() as verification_session:
        assert verification_session.get(Team, team_id) is None


def test_ordinary_session_rollback_remains_usable(db_session, make_team):
    first = Team(name="Rollback usability first", name_key="rollback usability first")
    db_session.add(first)
    db_session.flush()
    first_id = first.id
    db_session.rollback()

    second = make_team(name="Rollback usability second")

    assert db_session.get(Team, first_id) is None
    assert db_session.get(Team, second.id) is not None


def test_client_uses_the_ordinary_transactional_connection(client, db_session):
    factory = harness.app.state.db_session_factory

    assert factory.kw["bind"] is db_session.get_bind()
    assert factory.kw["join_transaction_mode"] == "create_savepoint"
    with factory() as app_created_session:
        assert app_created_session.get_bind() is db_session.get_bind()


@pytest.mark.real_db_connections
def test_real_connection_marker_uses_engine_bound_sessions(db_session, client, make_team):
    assert db_session.get_bind() is harness.test_engine
    assert harness.app.state.db_session_factory is harness.TestingSessionLocal
    team = make_team(name="Real connection cleanup team")
    assert db_session.get(Team, team.id) is not None


@pytest.mark.migration
def test_migration_marker_skips_normal_database_reset(reset_database):
    assert reset_database == "migration"


def test_canonical_rebuild_marks_schema_ready(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(harness, "canonical_schema_ready", False)
    monkeypatch.setattr(harness, "reset_public_schema", lambda: calls.append("reset"))
    monkeypatch.setattr(harness.Base.metadata, "create_all", lambda *, bind: calls.append("create"))

    harness.rebuild_canonical_schema()

    assert calls == ["reset", "create"]
    assert harness.canonical_schema_ready is True


def test_failed_canonical_rebuild_remains_not_ready(monkeypatch):
    monkeypatch.setattr(harness, "canonical_schema_ready", True)
    monkeypatch.setattr(harness, "reset_public_schema", lambda: None)

    def fail_create_all(*, bind):
        raise RuntimeError("create failed")

    monkeypatch.setattr(harness.Base.metadata, "create_all", fail_create_all)

    with pytest.raises(RuntimeError, match="create failed"):
        harness.rebuild_canonical_schema()

    assert harness.canonical_schema_ready is False


def test_migration_fixture_invalidates_canonical_schema_after_test_failure(monkeypatch):
    calls: list[str] = []
    request = SimpleNamespace(
        node=SimpleNamespace(get_closest_marker=lambda name: object() if name == "migration" else None)
    )

    monkeypatch.setattr(harness, "canonical_schema_ready", True)
    monkeypatch.setattr(harness, "rate_limit_redis", SimpleNamespace(flushdb=lambda: calls.append("flush")))
    monkeypatch.setattr(
        harness,
        "clear_test_rate_limit_storage",
        lambda _redis, *, key_pattern: calls.append("flush"),
    )

    lifecycle = harness.reset_database.__wrapped__(request)
    assert next(lifecycle) == "migration"
    with pytest.raises(RuntimeError, match="migration failure"):
        lifecycle.throw(RuntimeError("migration failure"))

    assert calls == ["flush", "flush"]
    assert harness.canonical_schema_ready is False


def test_migration_to_ordinary_transition_rebuilds_lazily(monkeypatch):
    calls: list[str] = []
    migration_request = SimpleNamespace(
        node=SimpleNamespace(get_closest_marker=lambda name: object() if name == "migration" else None)
    )

    monkeypatch.setattr(harness, "canonical_schema_ready", True)
    monkeypatch.setattr(
        harness,
        "clear_test_rate_limit_storage",
        lambda _redis, *, key_pattern: calls.append("flush"),
    )
    monkeypatch.setattr(harness, "reset_public_schema", lambda: calls.append("reset"))
    monkeypatch.setattr(harness.Base.metadata, "create_all", lambda *, bind: calls.append("create"))

    lifecycle = harness.reset_database.__wrapped__(migration_request)
    assert next(lifecycle) == "migration"
    with pytest.raises(StopIteration):
        next(lifecycle)

    assert calls == ["flush", "flush"]
    assert harness.canonical_schema_ready is False
    assert harness.prepare_canonical_schema() == "rebuild"
    assert calls == ["flush", "flush", "reset", "create"]
    assert harness.canonical_schema_ready is True
