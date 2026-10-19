"""Generation must wait until a live-transcription source is resolved."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.errors import AppError
from app.models import (
    ConsultationSplitIntent,
    ConsultationSplitIntentStatus,
    GeneratedDocument,
    Transcript,
    TranscriptIngestionMode,
    TranscriptStatus,
    TranscriptStreamSession,
    TranscriptStreamStatus,
    TranscriptVersion,
    UserAppPreference,
    utcnow,
)
from app.services.consultation_split_sources import prepare_source_bound_consultation_split_analysis
from app.services.content_crypto import encrypt_text_for_owner
from app.services.consultation_split_confirmation import confirm_split_draft
from app.schemas.consultation_split import ConsultationSplitDraftConfirmRequest
from app.services.templates import _ensure_generation_can_be_queued
from app.services.transcripts import snapshot_current_transcript_version


def _transcript(db_session, owner, *, text="Synthetic saved source"):
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=owner.team_id,
        title="Synthetic stream barrier",
        ingestion_mode=TranscriptIngestionMode.live_chunked,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    if text:
        transcript.current_draft_text_encrypted = encrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="transcripts",
            field="current_draft_text_encrypted",
            record_id=transcript.id,
            plaintext=text,
        )
        db_session.add(transcript)
    db_session.flush()
    return transcript


def _stream_session(db_session, owner, transcript, *, status):
    session = TranscriptStreamSession(
        transcript_id=transcript.id,
        owner_user_id=owner.id,
        team_id=owner.team_id,
        selection_snapshot_json={"provider_preset": "deepgram"},
        status=status,
        lease_expires_at=utcnow() + timedelta(minutes=5),
    )
    db_session.add(session)
    db_session.flush()
    return session


@pytest.mark.parametrize(
    "status",
    [
        TranscriptStreamStatus.active,
        TranscriptStreamStatus.finalizing,
        TranscriptStreamStatus.failed,
        TranscriptStreamStatus.recovering,
    ],
)
def test_ordinary_generation_gate_and_version_snapshot_reject_unresolved_stream(
    db_session, make_user, status,
):
    owner = make_user(email=f"stream-generation-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    _stream_session(db_session, owner, transcript, status=status)

    with pytest.raises(AppError) as queue_blocked:
        _ensure_generation_can_be_queued(db_session, transcript=transcript)
    assert queue_blocked.value.code == "stream_session_unresolved"

    with pytest.raises(AppError) as snapshot_blocked:
        snapshot_current_transcript_version(db_session, transcript=transcript)
    assert snapshot_blocked.value.code == "stream_session_unresolved"
    assert transcript.status is TranscriptStatus.ready
    assert db_session.scalar(
        select(TranscriptVersion.id).where(TranscriptVersion.transcript_id == transcript.id).limit(1)
    ) is None
    assert db_session.scalar(
        select(GeneratedDocument.id).where(GeneratedDocument.transcript_id == transcript.id).limit(1)
    ) is None


@pytest.mark.parametrize(
    "status", [TranscriptStreamStatus.completed, TranscriptStreamStatus.discarded]
)
def test_resolved_stream_allows_ordinary_generation_snapshot(db_session, make_user, status):
    owner = make_user(email=f"stream-generation-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    _stream_session(db_session, owner, transcript, status=status)

    locked_transcript, waiting = _ensure_generation_can_be_queued(
        db_session, transcript=transcript,
    )
    version = snapshot_current_transcript_version(db_session, transcript=locked_transcript)

    assert waiting is False
    assert version.transcript_id == transcript.id


def test_empty_source_split_resolution_cannot_bypass_unresolved_stream(
    db_session, make_user,
):
    owner = make_user(email=f"stream-generation-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner, text="")
    _stream_session(db_session, owner, transcript, status=TranscriptStreamStatus.active)

    with pytest.raises(AppError) as blocked:
        prepare_source_bound_consultation_split_analysis(
            db_session, owner, transcript_id=transcript.id,
        )

    assert blocked.value.code == "stream_session_unresolved"


def test_fresh_split_confirmation_cannot_accept_analysis_during_unresolved_stream(
    db_session, make_user, monkeypatch,
):
    owner = make_user(email=f"stream-generation-confirm-{uuid4()}@example.com")
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    db_session.add(UserAppPreference(
        user_id=owner.id,
        preferences_json={"split_consultations_into_separate_notes": True},
    ))
    transcript = _transcript(db_session, owner)
    _stream_session(db_session, owner, transcript, status=TranscriptStreamStatus.active)
    intent = ConsultationSplitIntent(
        owner_user_id=owner.id,
        team_id=owner.team_id,
        transcript_id=transcript.id,
        analysis_id=None,
        client_idempotency_key=uuid4(),
        retention_expires_at=transcript.retention_expires_at,
        generation_snapshot_encrypted="",
        status=ConsultationSplitIntentStatus.analysis_pending,
    )
    db_session.add(intent)
    db_session.flush()

    with pytest.raises(AppError) as blocked:
        confirm_split_draft(
            db_session,
            owner,
            transcript_id=transcript.id,
            payload=ConsultationSplitDraftConfirmRequest(
                intent_id=intent.id,
                expected_updated_at=utcnow(),
            ),
        )

    assert blocked.value.code == "stream_session_unresolved"
