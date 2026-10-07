"""Focused persistence and durable-gate coverage for manual split review."""

from datetime import timedelta
from uuid import uuid4

import pytest

from app.errors import AppError
from app.models import (
    ConsultationSplitAnalysis,
    ConsultationSplitAnalysisStatus,
    ConsultationSplitBatch,
    ConsultationSplitBatchStatus,
    ConsultationSplitIntent,
    ConsultationSplitIntentStatus,
    Transcript,
    TranscriptIngestionMode,
    TranscriptStatus,
    utcnow,
)
from app.services.consultation_split_gates import analysis_split_enabled, batch_split_enabled, intent_split_enabled, transcript_split_enabled
from app.services.transcripts import update_transcript


def _transcript(owner, *, marked=False, expired=False):
    return Transcript(
        owner_user_id=owner.id,
        team_id=owner.team_id,
        title="Synthetic consultation",
        multiple_problems=marked,
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() - timedelta(seconds=1) if expired else utcnow() + timedelta(days=30),
    )


def test_multiple_problems_owner_update_persists_and_preserves_owner_expiry_boundaries(
    db_session, make_user, monkeypatch
):
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    owner = make_user(email="multiple-problems-owner@example.com")
    other = make_user(email="multiple-problems-other@example.com", team=owner.team)
    transcript = _transcript(owner)
    expired = _transcript(owner, expired=True)
    db_session.add_all([transcript, expired])
    db_session.commit()

    saved = update_transcript(
        db_session, owner, transcript_id=transcript.id, title=None,
        ingestion_mode=None, structured_context_json=None, multiple_problems=True,
    )
    db_session.expire_all()
    assert db_session.get(Transcript, transcript.id).multiple_problems is True
    assert transcript_split_enabled(db_session, owner, saved) is True

    with pytest.raises(AppError) as foreign:
        update_transcript(
            db_session, other, transcript_id=transcript.id, title=None,
            ingestion_mode=None, structured_context_json=None, multiple_problems=False,
        )
    assert foreign.value.status_code == 403

    with pytest.raises(AppError) as expired_error:
        update_transcript(
            db_session, owner, transcript_id=expired.id, title=None,
            ingestion_mode=None, structured_context_json=None, multiple_problems=True,
        )
    assert expired_error.value.status_code == 404


def test_multiple_problems_survives_combined_structured_context_update(db_session, make_user):
    owner = make_user(email="multiple-problems-structured-update@example.com")
    transcript = _transcript(owner)
    db_session.add(transcript)
    db_session.commit()

    saved = update_transcript(
        db_session,
        owner,
        transcript_id=transcript.id,
        title=None,
        ingestion_mode=None,
        structured_context_json={"profile": "emis", "sections": {"problem": ["Synthetic problem"]}},
        multiple_problems=True,
    )

    db_session.expire_all()
    persisted = db_session.get(Transcript, transcript.id)
    assert persisted is not None
    assert persisted.multiple_problems is True
    assert persisted.working_note_mode.value == "structured"
    assert saved.multiple_problems is True


def test_manual_snapshot_enables_only_its_accepted_analysis_and_batch_after_unmark(
    db_session, make_user, monkeypatch
):
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    owner = make_user(email="multiple-problems-snapshot@example.com")
    transcript = _transcript(owner, marked=True)
    db_session.add(transcript)
    db_session.flush()
    analysis = ConsultationSplitAnalysis(
        owner_user_id=owner.id, team_id=owner.team_id, transcript_id=transcript.id,
        source_fingerprint="a" * 64, status=ConsultationSplitAnalysisStatus.not_required,
        retention_expires_at=transcript.retention_expires_at,
    )
    db_session.add(analysis)
    db_session.flush()
    intent = ConsultationSplitIntent(
        owner_user_id=owner.id, team_id=owner.team_id, transcript_id=transcript.id,
        analysis_id=analysis.id, client_idempotency_key=uuid4(), manual_review_requested=True,
        status=ConsultationSplitIntentStatus.confirmed,
        generation_snapshot_encrypted="encrypted", retention_expires_at=transcript.retention_expires_at,
    )
    db_session.add(intent)
    db_session.flush()
    batch = ConsultationSplitBatch(
        intent_id=intent.id, analysis_id=analysis.id, owner_user_id=owner.id, team_id=owner.team_id,
        transcript_id=transcript.id, source_fingerprint="b" * 64,
        confirmed_plan_encrypted="encrypted", clinical_snapshot_encrypted="encrypted",
        source_snapshot_encrypted="encrypted", template_snapshot_encrypted="encrypted",
        pii_snapshot_encrypted="encrypted", provider_snapshot_encrypted="encrypted",
        note_options_snapshot_encrypted="encrypted", status=ConsultationSplitBatchStatus.ready,
        retention_expires_at=transcript.retention_expires_at,
    )
    db_session.add(batch)
    db_session.commit()

    transcript.multiple_problems = False
    db_session.commit()
    assert transcript_split_enabled(db_session, owner, transcript) is False
    assert analysis_split_enabled(db_session, owner, analysis_id=analysis.id) is True
    assert batch_split_enabled(db_session, owner, batch=batch) is True

    unrelated = ConsultationSplitAnalysis(
        owner_user_id=owner.id, team_id=owner.team_id, transcript_id=transcript.id,
        source_fingerprint="c" * 64, status=ConsultationSplitAnalysisStatus.not_required,
        retention_expires_at=transcript.retention_expires_at,
    )
    db_session.add(unrelated)
    db_session.commit()
    assert analysis_split_enabled(db_session, owner, analysis_id=unrelated.id) is False


def test_accepted_intents_are_exact_and_bypassed_intents_stop_granting_access(
    db_session, make_user, monkeypatch
):
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    owner = make_user(email="multiple-problems-exact-intent@example.com")
    transcript = _transcript(owner)
    db_session.add(transcript)
    db_session.flush()
    analysis = ConsultationSplitAnalysis(
        owner_user_id=owner.id, team_id=owner.team_id, transcript_id=transcript.id,
        source_fingerprint="d" * 64, status=ConsultationSplitAnalysisStatus.not_required,
        retention_expires_at=transcript.retention_expires_at,
    )
    db_session.add(analysis)
    db_session.flush()
    manual = ConsultationSplitIntent(
        owner_user_id=owner.id, team_id=owner.team_id, transcript_id=transcript.id,
        analysis_id=analysis.id, client_idempotency_key=uuid4(), manual_review_requested=True,
        generation_snapshot_encrypted="encrypted", retention_expires_at=transcript.retention_expires_at,
    )
    automatic = ConsultationSplitIntent(
        owner_user_id=owner.id, team_id=owner.team_id, transcript_id=transcript.id,
        analysis_id=analysis.id, client_idempotency_key=uuid4(), manual_review_requested=False,
        generation_snapshot_encrypted="encrypted", retention_expires_at=transcript.retention_expires_at,
    )
    db_session.add_all([manual, automatic])
    db_session.commit()

    assert analysis_split_enabled(db_session, owner, analysis_id=analysis.id) is True
    assert intent_split_enabled(db_session, owner, intent=manual) is True
    assert intent_split_enabled(db_session, owner, intent=automatic) is True

    manual.status = ConsultationSplitIntentStatus.bypassed
    automatic.status = ConsultationSplitIntentStatus.bypassed
    db_session.commit()
    assert analysis_split_enabled(db_session, owner, analysis_id=analysis.id) is False
    assert intent_split_enabled(db_session, owner, intent=manual) is False
    assert intent_split_enabled(db_session, owner, intent=automatic) is False
