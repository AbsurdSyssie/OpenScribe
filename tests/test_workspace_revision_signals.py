"""Redis revision hints are advisory, tenant-scoped and fail open to DB reads."""

from types import SimpleNamespace
from unittest.mock import Mock

from app.services import workspace_revision_signals as revisions


def test_owner_and_team_changes_publish_after_commit_only(monkeypatch):
    monkeypatch.setattr(revisions, "revisions_enabled", lambda: True)

    class Transcript:
        owner_user_id = "owner-one"
        team_id = "team-one"
        current_draft_text_encrypted = "must-not-be-sent-to-redis"

    class QuickAction:
        owner_user_id = None
        team_id = "team-one"

    class TranscriptStreamDelivery:
        owner_user_id = "owner-one"

    pipeline = Mock()
    fake_client = Mock()
    fake_client.pipeline.return_value = pipeline
    monkeypatch.setattr(revisions, "_redis_client", lambda: fake_client)

    session = SimpleNamespace(
        new={Transcript(), QuickAction(), TranscriptStreamDelivery()},
        dirty=set(), deleted=set(), info={},
    )
    revisions._capture_workspace_changes(session)
    assert pipeline.execute.call_count == 0
    revisions._publish_workspace_changes(session)
    owner_key = revisions._key("owner", "owner-one")
    team_key = revisions._key("team", "team-one")
    pipeline.incr.assert_any_call(owner_key)
    pipeline.incr.assert_any_call(team_key)
    assert pipeline.incr.call_count == 2  # high-frequency delivery is ignored
    assert "must-not-be-sent-to-redis" not in repr(pipeline.mock_calls)

    revisions._publish_workspace_changes(session)
    assert pipeline.execute.call_count == 1  # no duplicate publication


def test_rollback_discards_pending_changes(monkeypatch):
    monkeypatch.setattr(revisions, "revisions_enabled", lambda: True)

    class UserAppPreference:
        user_id = "owner-one"

    session = SimpleNamespace(new={UserAppPreference()}, dirty=set(), deleted=set(), info={})
    revisions._capture_workspace_changes(session)
    assert session.info
    revisions._discard_workspace_changes(session)
    assert session.info == {}


def test_revision_read_is_scoped_and_redis_outage_uses_full_refresh(monkeypatch):
    monkeypatch.setattr(revisions, "revisions_enabled", lambda: True)
    client = Mock()
    client.mget.return_value = [b"12", None, b"4"]
    monkeypatch.setattr(revisions, "_redis_client", lambda: client)
    assert revisions.read_workspace_revisions("owner-one", "team-one") == (12, 0, 4)
    client.mget.assert_called_once_with([
        revisions._key("owner", "owner-one"), revisions._key("global"),
        revisions._key("team", "team-one"),
    ])

    from redis import RedisError
    client.mget.side_effect = RedisError("synthetic outage")
    assert revisions.read_workspace_revisions("owner-one", "team-one") is None


def test_child_section_uses_loaded_document_identity_without_database_reads(monkeypatch):
    monkeypatch.setattr(revisions, "revisions_enabled", lambda: True)
    from uuid import uuid4

    document_id = uuid4()

    class GeneratedDocumentSection:
        generated_document_id = document_id

    parent = SimpleNamespace(owner_user_id="owner-one", team_id="team-one")
    session = SimpleNamespace(
        new={GeneratedDocumentSection()}, dirty=set(), deleted=set(), info={},
        identity_map={("GeneratedDocument", document_id): parent},
        identity_key=lambda model, pk: (model.__name__, pk[0]),
    )
    revisions._capture_workspace_changes(session)
    owners, teams, global_change = session.info["_workspace_revision_pending"]
    assert owners == {"owner-one"}
    assert teams == set()
    assert global_change is False


def test_stream_lifecycle_status_signals_ignore_heartbeat_updates(monkeypatch):
    """Only stream lifecycle transitions (not lease heartbeats) wake workspace SSE."""
    from uuid import uuid4

    from sqlalchemy.orm.attributes import set_committed_value

    from app.models import TranscriptStreamSession, TranscriptStreamStatus

    monkeypatch.setattr(revisions, "revisions_enabled", lambda: True)
    owner_id = uuid4()
    stream = TranscriptStreamSession(id=uuid4(), owner_user_id=owner_id)
    set_committed_value(stream, "status", TranscriptStreamStatus.active)
    set_committed_value(stream, "lease_expires_at", None)
    stream.lease_expires_at = "changed-heartbeat-only"
    session = SimpleNamespace(new=set(), dirty={stream}, deleted=set(), info={})

    revisions._capture_workspace_changes(session)
    assert session.info == {}  # no revision for a frequent stream lease update

    stream.status = TranscriptStreamStatus.failed
    revisions._capture_workspace_changes(session)
    owners, teams, global_change = session.info[revisions._PENDING_KEY]
    assert owners == {str(owner_id)}
    assert teams == set()
    assert global_change is False


def test_stream_window_recovery_status_signals_through_loaded_session(monkeypatch):
    from uuid import uuid4

    from sqlalchemy.orm.attributes import set_committed_value

    from app.models import (
        TranscriptStreamSession,
        TranscriptStreamStatus,
        TranscriptStreamWindow,
        TranscriptStreamWindowStatus,
    )

    monkeypatch.setattr(revisions, "revisions_enabled", lambda: True)
    owner_id = uuid4()
    stream = TranscriptStreamSession(id=uuid4(), owner_user_id=owner_id)
    set_committed_value(stream, "status", TranscriptStreamStatus.failed)
    window = TranscriptStreamWindow(session_id=stream.id)
    set_committed_value(window, "status", TranscriptStreamWindowStatus.failed)
    window.status = TranscriptStreamWindowStatus.recovering
    session = SimpleNamespace(
        new=set(), dirty={window}, deleted=set(), info={},
        identity_map={(TranscriptStreamSession, stream.id): stream},
        identity_key=lambda model, pk: (model, pk[0]),
    )

    revisions._capture_workspace_changes(session)
    owners, teams, global_change = session.info[revisions._PENDING_KEY]
    assert owners == {str(owner_id)}
    assert teams == set()
    assert global_change is False


def test_noop_orm_changes_do_not_write_workspace_revisions(monkeypatch):
    from uuid import uuid4

    from sqlalchemy.orm import Session, make_transient_to_detached

    from app.models import Transcript

    monkeypatch.setattr(revisions, "revisions_enabled", lambda: True)
    redis_client = Mock()
    monkeypatch.setattr(revisions, "_redis_client", lambda: redis_client)
    with Session() as session:
        transcript = Transcript(
            id=uuid4(), owner_user_id=uuid4(), team_id=uuid4(), title="synthetic",
        )
        make_transient_to_detached(transcript)
        session.add(transcript)
        transcript.title = "synthetic"  # ORM marks dirty, value did not change
        assert transcript in session.dirty
        assert not session.is_modified(transcript)
        revisions._capture_workspace_changes(session)
        revisions._publish_workspace_changes(session)
        redis_client.pipeline.assert_not_called()

        transcript.title = "changed"
        revisions._capture_workspace_changes(session)
        revisions._publish_workspace_changes(session)
        redis_client.pipeline.return_value.incr.assert_called_once_with(
            revisions._key("owner", str(transcript.owner_user_id))
        )


def test_session_heartbeat_is_ignored_but_revocation_signals(monkeypatch):
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from sqlalchemy.orm import Session, make_transient_to_detached

    from app.models import SessionStatus, UserSession

    monkeypatch.setattr(revisions, "revisions_enabled", lambda: True)
    redis_client = Mock()
    monkeypatch.setattr(revisions, "_redis_client", lambda: redis_client)
    owner_id = uuid4()
    with Session() as session:
        seen_at = datetime.now(UTC)
        user_session = UserSession(
            id=uuid4(), user_id=owner_id,
            last_seen_at=seen_at, status=SessionStatus.active,
        )
        make_transient_to_detached(user_session)
        session.add(user_session)
        user_session.last_seen_at = seen_at + timedelta(seconds=30)
        revisions._capture_workspace_changes(session)
        revisions._publish_workspace_changes(session)
        redis_client.pipeline.assert_not_called()

        user_session.status = SessionStatus.revoked
        revisions._capture_workspace_changes(session)
        revisions._publish_workspace_changes(session)
        redis_client.pipeline.return_value.incr.assert_called_once_with(
            revisions._key("owner", str(owner_id))
        )


def test_new_and_deleted_rows_still_publish_revisions(monkeypatch):
    from uuid import uuid4

    from sqlalchemy.orm import Session, make_transient_to_detached

    from app.models import UserSession

    monkeypatch.setattr(revisions, "revisions_enabled", lambda: True)
    owner_id = uuid4()
    with Session() as session:
        new = UserSession(id=uuid4(), user_id=owner_id)
        session.add(new)
        revisions._capture_workspace_changes(session)
        assert session.info[revisions._PENDING_KEY][0] == {str(owner_id)}
        revisions._discard_workspace_changes(session)
        session.expunge(new)
        make_transient_to_detached(new)
        session.add(new)
        session.delete(new)
        revisions._capture_workspace_changes(session)
        assert session.info[revisions._PENDING_KEY][0] == {str(owner_id)}
