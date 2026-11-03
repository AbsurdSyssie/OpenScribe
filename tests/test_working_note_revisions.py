from uuid import UUID, uuid4
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy.orm import sessionmaker

from app.errors import AppError
from app.models import TeamRole, Transcript, TranscriptIngestionMode, TranscriptStatus, TranscriptWorkingNoteMode, User, utcnow
from app.schemas.transcripts import WorkingNoteUpdate
from app.services.transcripts import clear_working_note, save_working_note, update_transcript


def _login(client, email: str, password: str):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def test_working_note_revision_replay_and_superseded_operations(client, db_session, make_team, make_user):
    team = make_team(name="Working Note Revisions")
    owner = make_user(
        email="working-revision-owner@example.com",
        password="password-1",
        team=team,
        team_role=TeamRole.user,
        mfa_required=False,
        mfa_enabled=False,
    )
    assert _login(client, "working-revision-owner@example.com", "password-1").status_code == 200
    started = client.post("/api/v1/transcripts/start", json={"title": "Revision test", "ingestion_mode": "whole_file"})
    assert started.status_code == 201
    transcript_id = started.json()["id"]

    empty = client.get(f"/api/v1/transcripts/{transcript_id}/working-note")
    assert empty.status_code == 200
    assert empty.json()["revision"] == 0

    first_operation = str(uuid4())
    first_payload = {
        "mode": "freeform",
        "freeform_text": "Review blood pressure next week.",
        "expected_revision": 0,
        "operation_id": first_operation,
    }
    saved = client.patch(f"/api/v1/transcripts/{transcript_id}/working-note", json=first_payload)
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1

    same_id_as_save_clear = client.request(
        "DELETE",
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={"expected_revision": 0, "operation_id": first_operation},
    )
    assert same_id_as_save_clear.status_code == 409
    stale_clear_after_save = client.request(
        "DELETE",
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={"expected_revision": 0, "operation_id": str(uuid4())},
    )
    assert stale_clear_after_save.status_code == 409
    still_saved = client.get(f"/api/v1/transcripts/{transcript_id}/working-note")
    assert still_saved.json()["freeform_text"] == "Review blood pressure next week."

    replay = client.patch(f"/api/v1/transcripts/{transcript_id}/working-note", json=first_payload)
    assert replay.status_code == 200
    assert replay.json()["revision"] == 1
    assert replay.json()["freeform_text"] == "Review blood pressure next week."

    altered_replay = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={**first_payload, "freeform_text": "Different content."},
    )
    assert altered_replay.status_code == 409

    second_operation = str(uuid4())
    stale = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={
            "mode": "freeform",
            "freeform_text": "Stale content.",
            "expected_revision": 0,
            "operation_id": second_operation,
        },
    )
    assert stale.status_code == 409

    current = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={
            "mode": "freeform",
            "freeform_text": "Review blood pressure in two weeks.",
            "expected_revision": 1,
            "operation_id": second_operation,
        },
    )
    assert current.status_code == 200
    assert current.json()["revision"] == 2

    clear_operation = str(uuid4())
    clear_payload = {"expected_revision": 2, "operation_id": clear_operation}
    cleared = client.request("DELETE", f"/api/v1/transcripts/{transcript_id}/working-note", json=clear_payload)
    assert cleared.status_code == 204
    assert cleared.headers["X-Working-Note-Revision"] == "3"
    clear_replay = client.request("DELETE", f"/api/v1/transcripts/{transcript_id}/working-note", json=clear_payload)
    assert clear_replay.status_code == 204
    assert clear_replay.headers["X-Working-Note-Revision"] == "3"

    stale_after_clear = client.patch(f"/api/v1/transcripts/{transcript_id}/working-note", json=first_payload)
    assert stale_after_clear.status_code == 409
    detail = client.get(f"/api/v1/transcripts/{transcript_id}/working-note")
    assert detail.status_code == 200
    assert detail.json()["revision"] == 3
    assert detail.json()["mode"] is None

    structured_operation = str(uuid4())
    structured_payload = {
        "mode": "structured",
        "structured_note": {"profile": "emis", "sections": {"problem": [" Hypertension "]}},
        "expected_revision": 3,
        "operation_id": structured_operation,
    }
    structured = client.patch(f"/api/v1/transcripts/{transcript_id}/working-note", json=structured_payload)
    assert structured.status_code == 200
    assert structured.json()["revision"] == 4
    normalized_replay = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={
            **structured_payload,
            "structured_note": {"profile": "emis", "sections": {"problem": "Hypertension"}},
        },
    )
    assert normalized_replay.status_code == 200
    assert normalized_replay.json()["revision"] == 4

    transcript = db_session.get(Transcript, UUID(transcript_id))
    update_transcript(
        db_session,
        owner,
        transcript_id=transcript.id,
        title=None,
        ingestion_mode=None,
        structured_context_json={"profile": "emis", "sections": {"problem": ["Follow-up"]}},
        expected_updated_at=transcript.working_note_updated_at,
    )
    assert transcript.working_note_revision == 5
    assert transcript.working_note_last_operation_id is None
    superseded_replay = client.patch(f"/api/v1/transcripts/{transcript_id}/working-note", json=structured_payload)
    assert superseded_replay.status_code == 409

    assert transcript.working_note_revision == 5


def test_working_note_revision_fields_must_be_paired(client, db_session, make_team, make_user):
    team = make_team(name="Working Note Revision Validation")
    owner = make_user(
        email="working-revision-validation@example.com",
        password="password-1",
        team=team,
        team_role=TeamRole.user,
        mfa_required=False,
        mfa_enabled=False,
    )
    assert _login(client, "working-revision-validation@example.com", "password-1").status_code == 200
    started = client.post("/api/v1/transcripts/start", json={"title": "Revision validation", "ingestion_mode": "whole_file"})
    transcript_id = started.json()["id"]

    response = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={"mode": "freeform", "freeform_text": "A note.", "expected_revision": 0},
    )
    assert response.status_code == 422

    boolean_revision = client.patch(
        f"/api/v1/transcripts/{transcript_id}/working-note",
        json={
            "mode": "freeform",
            "freeform_text": "A note.",
            "expected_revision": False,
            "operation_id": str(uuid4()),
        },
    )
    assert boolean_revision.status_code == 422

    with pytest.raises(AppError) as unpaired_service_call:
        clear_working_note(
            db_session,
            owner,
            transcript_id=UUID(transcript_id),
            expected_revision=0,
        )
    assert unpaired_service_call.value.status_code == 422


def test_replay_still_enforces_owner_expiry_and_root_existence(client, db_session, make_team, make_user):
    team = make_team(name="Working Note Replay Access")
    owner = make_user(
        email="working-replay-owner@example.com",
        password="password-1",
        team=team,
        team_role=TeamRole.user,
        mfa_required=False,
        mfa_enabled=False,
    )
    other = make_user(
        email="working-replay-other@example.com",
        password="password-2",
        team=team,
        team_role=TeamRole.user,
        mfa_required=False,
        mfa_enabled=False,
    )
    assert _login(client, "working-replay-owner@example.com", "password-1").status_code == 200
    started = client.post("/api/v1/transcripts/start", json={"title": "Replay access", "ingestion_mode": "whole_file"})
    assert started.status_code == 201
    transcript_id = started.json()["id"]
    operation = str(uuid4())
    payload = WorkingNoteUpdate(
        mode="freeform",
        freeform_text="Owner only content.",
        expected_revision=0,
        operation_id=operation,
    )
    save_working_note(db_session, owner, transcript_id=UUID(transcript_id), payload=payload)

    with pytest.raises(AppError) as forbidden:
        save_working_note(db_session, other, transcript_id=UUID(transcript_id), payload=payload)
    assert forbidden.value.status_code == 403

    transcript = db_session.get(Transcript, UUID(transcript_id))
    transcript.retention_expires_at = utcnow()
    db_session.add(transcript)
    db_session.commit()
    with pytest.raises(AppError) as expired:
        save_working_note(db_session, owner, transcript_id=UUID(transcript_id), payload=payload)
    assert expired.value.status_code == 404

    another = client.post("/api/v1/transcripts/start", json={"title": "Deleted root", "ingestion_mode": "whole_file"})
    assert another.status_code == 201
    deleted_id = another.json()["id"]
    deleted_root = db_session.get(Transcript, UUID(deleted_id))
    db_session.delete(deleted_root)
    db_session.commit()
    with pytest.raises(AppError) as deleted:
        save_working_note(db_session, owner, transcript_id=UUID(deleted_id), payload=WorkingNoteUpdate(
            mode="freeform",
            freeform_text="Must not recreate deleted content.",
            expected_revision=0,
            operation_id=str(uuid4()),
        ))
    assert deleted.value.status_code == 404


@pytest.mark.real_db_connections
def test_concurrent_working_note_writers_from_one_revision_have_one_winner(db_session, make_user):
    owner = make_user(email=f"working-revision-race-{uuid4()}@example.com")
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=owner.team_id,
        title="Concurrent revision test",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()
    owner_id, transcript_id = owner.id, transcript.id
    session_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, future=True)
    start = Barrier(3)

    def write(text: str):
        with session_factory() as session:
            actor = session.get(User, owner_id)
            assert actor is not None
            start.wait(timeout=5)
            try:
                save_working_note(
                    session,
                    actor,
                    transcript_id=transcript_id,
                    payload=WorkingNoteUpdate(
                        mode=TranscriptWorkingNoteMode.freeform,
                        freeform_text=text,
                        expected_revision=0,
                        operation_id=uuid4(),
                    ),
                )
                return "saved"
            except AppError as exc:
                assert exc.status_code == 409
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(write, "First synthetic note.")
        second = executor.submit(write, "Second synthetic note.")
        start.wait(timeout=5)
        outcomes = [first.result(timeout=10), second.result(timeout=10)]

    assert sorted(outcomes) == ["conflict", "saved"]
    db_session.expire_all()
    persisted = db_session.get(Transcript, transcript_id)
    assert persisted.working_note_revision == 1
