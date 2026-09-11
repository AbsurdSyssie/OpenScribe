"""Focused tests for the API-only consultation split analysis surface."""

from datetime import timedelta
import json
from types import SimpleNamespace
from threading import Event, Thread
from uuid import uuid4

import pytest
from limits import parse as parse_rate_limit
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from app.errors import AppError
from app.models import (
    ConsultationSplitAnalysis,
    ConsultationSplitAnalysisStatus,
    ConsultationSplitDraft,
    ConsultationSplitDraftStatus,
    ConsultationSplitDraftTopic,
    ConsultationSplitIntent,
    ConsultationSplitIntentStatus,
    ConsultationSplitBatch,
    ConsultationSplitBatchStatus,
    ConsultationSplitTopicDisposition,
    ConsultationSplitExecution,
    GeneratedDocument,
    ProviderAttempt,
    TaskDispatchOutbox,
    PromptTemplateVersion,
    TemplateMode,
    TemplateScope,
    Transcript,
    TranscriptIngestionMode,
    TranscriptStatus,
    TranscriptVersion,
    TranscriptWorkingNoteMode,
    TeamRole,
    UserAppPreference,
    utcnow,
)
from app.schemas.consultation_split import ConsultationSplitAnalysisDetail
from app.services.consultation_split_api import (
    queue_split_analysis_api,
    read_workspace_split_analysis,
)
from app.services.consultation_split_drafts import (
    initialize_or_reuse_split_draft,
    read_split_draft,
    replace_split_draft,
)
from app.schemas.consultation_split import ConsultationSplitDraftReplace, ConsultationSplitDraftTopicReplace
from app.schemas.consultation_split import (
    ConsultationSplitDraftDetail,
    ConsultationSplitDraftConfirmRequest,
    ConsultationSplitDraftConfirmResponse,
)
from app.services.content_crypto import encrypt_json_for_owner, is_encrypted_envelope
from app.web.transcribe_workspace import transcribe_workspace_response
from app.services.consultation_split_queue import QueueOrReuseSplitAnalysisResult
from app.services.consultation_split_intents import (
    CreateOrReplayConsultationSplitIntentResult,
    continue_consultation_split_intent_as_one_note,
)
from app.services.consultation_splits import create_split_analysis, create_split_draft, create_split_draft_topic
from app.services.consultation_splits import create_split_batch, create_split_batch_topic
from app.services.consultation_split_confirmation import confirm_split_draft
from app.services.consultation_split_regeneration import regenerate_confirmed_split_batch
from app.services.consultation_split_recovery import QueuedSplitRecovery
from app.services.consultation_split_generation_runtime import GENERIC_SPLIT_DOCUMENT_TITLE
from app.services.templates import delete_personal_template
from app.services.task_outbox import PUBLISH_ERROR_CODE
from app.services.transcripts import set_freeform_working_note_text
from app.web.presentation import generated_document_response


def _transcript(db_session, owner, *, expired: bool = False) -> Transcript:
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=owner.team_id,
        title="Synthetic consultation",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() - timedelta(seconds=1) if expired else utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()
    db_session.refresh(transcript)
    return transcript


def _enable_split(db_session, owner, monkeypatch):
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    db_session.add(
        UserAppPreference(
            user_id=owner.id,
            preferences_json={"split_consultations_into_separate_notes": True},
        )
    )
    db_session.commit()


def _enable_split_generation_selection(owner, make_llm_config, make_llm_selection):
    """Confirmation queues provider work, so its fixture needs a real selection."""
    config = make_llm_config(team=owner.team, actor=owner, available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=owner, allowed_models_json=config.available_models_json)
    return config


def _intent_source(
    db_session,
    owner,
    make_llm_config,
    make_llm_selection,
    make_user_app_preference,
    monkeypatch,
):
    """Create the smallest owner-owned source accepted by the 6C2c service."""
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    make_user_app_preference(
        user=owner,
        preferences_json={"split_consultations_into_separate_notes": True},
    )
    config = make_llm_config(team=owner.team, actor=owner, available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=owner, allowed_models_json=config.available_models_json)
    transcript = _transcript(db_session, owner)
    set_freeform_working_note_text(db_session, transcript=transcript, plaintext="Synthetic working note")
    transcript.working_note_mode = TranscriptWorkingNoteMode.freeform
    db_session.commit()
    return transcript


def _analysis(
    db_session,
    owner,
    transcript,
    *,
    status=ConsultationSplitAnalysisStatus.queued,
    proposal=None,
    fingerprint="a" * 64,
    candidate_template_snapshot=None,
):
    row = create_split_analysis(
        db_session,
        owner,
        transcript_id=transcript.id,
        source_fingerprint=fingerprint,
        source_snapshot={"safe": "source"},
        candidate_template_snapshot=(
            {"templates": []}
            if candidate_template_snapshot is None
            else candidate_template_snapshot
        ),
        provider_snapshot={"safe": "provider"},
        proposal=proposal,
    )
    row.status = status
    db_session.commit()
    db_session.refresh(row)
    return row


def _split_generated_document(db_session, owner, make_generated_document):
    """Create one generated split note with its title held only in the topic."""
    transcript = _transcript(db_session, owner)
    version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted="",
    )
    db_session.add(version)
    db_session.flush()
    analysis = create_split_analysis(
        db_session,
        owner,
        transcript_id=transcript.id,
        source_fingerprint="d" * 64,
        source_snapshot={"source": "synthetic"},
        candidate_template_snapshot={"templates": []},
        provider_snapshot={"provider": "synthetic"},
        proposal={"topics": ["Synthetic UTI"]},
    )
    batch = create_split_batch(
        db_session,
        owner,
        analysis=analysis,
        confirmed_plan={"topics": []},
        clinical_snapshot={},
        source_snapshot={"source": "synthetic"},
        template_snapshot={},
        pii_snapshot={},
        provider_snapshot={"provider": "synthetic"},
        note_options_snapshot={},
        materialization_transcript_version_id=version.id,
    )
    topic = create_split_batch_topic(
        db_session,
        owner,
        batch=batch,
        title="Synthetic UTI",
        topic_order=0,
        is_primary=True,
        disposition=ConsultationSplitTopicDisposition.separate_note,
        template_snapshot={},
    )
    document = make_generated_document(
        owner=owner,
        transcript=transcript,
        transcript_version=version,
        title=GENERIC_SPLIT_DOCUMENT_TITLE,
    )
    document.consultation_split_batch_topic_id = topic.id
    document.consultation_split_topic_uuid = topic.topic_uuid
    db_session.commit()
    return transcript, document


def test_split_document_topic_title_is_owner_only_display_projection(
    client,
    db_session,
    make_generated_document,
    make_user,
):
    owner = make_user(email="split-title-owner@example.com", mfa_required=False, mfa_enabled=False)
    colleague = make_user(
        email="split-title-colleague@example.com",
        team=owner.team,
        mfa_required=False,
        mfa_enabled=False,
    )
    admin = make_user(email="split-title-admin@example.com", is_system_admin=True, mfa_required=False, mfa_enabled=False)
    transcript, document = _split_generated_document(db_session, owner, make_generated_document)

    assert client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200
    documents_response = client.get(f"/api/v1/transcripts/{transcript.id}/generated-documents")
    workspace_response = client.get(f"/api/v1/transcribe/workspace?transcript_id={transcript.id}")

    assert documents_response.status_code == 200
    assert documents_response.json()[0]["title"] == "Synthetic UTI"
    assert workspace_response.status_code == 200
    assert workspace_response.json()["generated_documents"][0]["title"] == "Synthetic UTI"
    assert db_session.get(GeneratedDocument, document.id).title == GENERIC_SPLIT_DOCUMENT_TITLE

    # The serializer cannot decrypt a split-topic title for a colleague,
    # administrator, or actorless call.  API authorization additionally
    # prevents those users from obtaining the document at all.
    for actor in (colleague, admin, None):
        assert generated_document_response(db_session, document, actor=actor).title == GENERIC_SPLIT_DOCUMENT_TITLE

    for actor in (colleague, admin):
        assert client.post("/api/v1/auth/logout").status_code == 200
        assert client.post("/api/v1/auth/login", json={"email": actor.email, "password": "password-1"}).status_code == 200
        denied = client.get(f"/api/v1/transcripts/{transcript.id}/generated-documents")
        assert denied.status_code in {403, 404}
        assert "Synthetic UTI" not in denied.text


def test_confirm_split_draft_freezes_encrypted_batch_and_replays_before_timestamp(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, monkeypatch,
):
    owner = make_user(email="split-confirm@example.com", mfa_required=False, mfa_enabled=False)
    transcript = _transcript(db_session, owner)
    _enable_split(db_session, owner, monkeypatch)
    template = make_template(owner=owner, actor=owner, name="Synthetic template", prompt_text="Synthetic prompt")
    version = db_session.scalar(select(PromptTemplateVersion).where(PromptTemplateVersion.template_id == template.id))
    source = {
        "source_state": {"manual_pii": [{"id": "synthetic-pii"}]},
        "sources": {"transcript": "[PERSON_1]", "working_note": {"mode": None, "value": None}, "dictation": ""},
        "clinical_nlp_hints": [], "phi_index": [{"placeholder": "[PERSON_1]"}],
    }
    analysis = create_split_analysis(
        db_session, owner, transcript_id=transcript.id, source_fingerprint="c" * 64,
        source_snapshot=source, candidate_template_snapshot={"templates": []}, provider_snapshot={}, proposal={},
    )
    analysis.status = ConsultationSplitAnalysisStatus.ready
    intent = ConsultationSplitIntent(
        owner_user_id=owner.id, team_id=owner.team_id, transcript_id=transcript.id, analysis_id=analysis.id,
        client_idempotency_key=uuid4(), retention_expires_at=transcript.retention_expires_at,
        generation_snapshot_encrypted="",
    )
    db_session.add(intent)
    db_session.flush()
    intent.generation_snapshot_encrypted = encrypt_json_for_owner(
        db_session, owner_user_id=owner.id, table="consultation_split_intents", field="generation_snapshot_encrypted",
        record_id=intent.id, plaintext={"selected_template": {
            "template_id": str(template.id), "template_version_id": str(version.id), "template_version_no": 1,
            "name": template.name, "description": template.description, "mode": "freeform", "prompt_text": "Synthetic prompt",
            "config": None, "structured_sections": None,
        }, "generation_configuration": {"note_generation_length": "normal", "llm_detail_level": "balanced"}},
    )
    draft = create_split_draft(db_session, owner, analysis=analysis)
    for order, primary in enumerate((True, False)):
        create_split_draft_topic(
            db_session, owner, draft=draft, title=f"Topic {order}", topic_order=order, is_primary=primary,
            disposition=ConsultationSplitTopicDisposition.separate_note,
            template_id=template.id, template_version_id=version.id,
        )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.consultation_split_confirmation.current_consultation_split_analysis_source_matches",
        lambda *_args, **_kwargs: True,
    )

    # The analysis contract permits no template selection so the clinician can
    # repair it in review. Confirmation must still reject a separate note that
    # remains unassigned; it cannot queue a batch with an unusable topic.
    unassigned_topic = db_session.scalar(
        select(ConsultationSplitDraftTopic)
        .where(ConsultationSplitDraftTopic.draft_id == draft.id)
        .order_by(ConsultationSplitDraftTopic.topic_order)
        .limit(1)
    )
    assert unassigned_topic is not None
    unassigned_topic.template_id = None
    unassigned_topic.template_version_id = None
    db_session.commit()
    with pytest.raises(AppError) as missing_topic_template:
        confirm_split_draft(
            db_session, owner, transcript_id=transcript.id,
            payload=ConsultationSplitDraftConfirmRequest(intent_id=intent.id, expected_updated_at=draft.updated_at),
        )
    assert missing_topic_template.value.code == "consultation_split_template_unavailable"
    assert db_session.query(ConsultationSplitBatch).filter_by(intent_id=intent.id).count() == 0
    assert db_session.scalars(
        select(TranscriptVersion.id).where(TranscriptVersion.transcript_id == transcript.id)
    ).all() == []
    unassigned_topic.template_id = template.id
    unassigned_topic.template_version_id = version.id
    db_session.commit()

    # A failure after the batch and first child have flushed must roll the
    # complete confirmation savepoint back, leaving the draft reusable.
    from app.services import consultation_split_confirmation as confirmation_service
    original_create_outcome = confirmation_service.create_split_topic_outcome

    def fail_after_flush(*args, **kwargs):
        outcome = original_create_outcome(*args, **kwargs)
        db_session.flush()
        raise RuntimeError("synthetic post-flush failure")

    monkeypatch.setattr(confirmation_service, "create_split_topic_outcome", fail_after_flush)
    with pytest.raises(RuntimeError, match="synthetic post-flush failure"):
        confirm_split_draft(
            db_session, owner, transcript_id=transcript.id,
            payload=ConsultationSplitDraftConfirmRequest(intent_id=intent.id, expected_updated_at=draft.updated_at),
        )
    db_session.expire_all()
    assert db_session.query(ConsultationSplitBatch).filter_by(intent_id=intent.id).count() == 0
    assert db_session.get(ConsultationSplitIntent, intent.id).status is ConsultationSplitIntentStatus.analysis_pending
    assert db_session.get(ConsultationSplitDraft, draft.id).status.value == "active"
    assert db_session.scalars(
        select(TranscriptVersion.id).where(TranscriptVersion.transcript_id == transcript.id)
    ).all() == []
    monkeypatch.setattr(confirmation_service, "create_split_topic_outcome", original_create_outcome)

    # Queue construction is part of confirmation.  Without an eligible
    # selection the complete batch/tree write rolls back rather than leaving a
    # confirmed batch that cannot be dispatched.
    with pytest.raises(AppError) as no_selection:
        confirm_split_draft(
            db_session, owner, transcript_id=transcript.id,
            payload=ConsultationSplitDraftConfirmRequest(intent_id=intent.id, expected_updated_at=draft.updated_at),
        )
    assert no_selection.value.code == "business_rule_violation"
    db_session.rollback()
    assert db_session.query(ConsultationSplitBatch).filter_by(intent_id=intent.id).count() == 0
    assert db_session.get(ConsultationSplitIntent, intent.id).status is ConsultationSplitIntentStatus.analysis_pending
    assert db_session.scalars(
        select(TranscriptVersion.id).where(TranscriptVersion.transcript_id == transcript.id)
    ).all() == []
    _enable_split_generation_selection(owner, make_llm_config, make_llm_selection)

    first = confirm_split_draft(
        db_session, owner, transcript_id=transcript.id,
        payload=ConsultationSplitDraftConfirmRequest(intent_id=intent.id, expected_updated_at=draft.updated_at),
    )
    replay = confirm_split_draft(
        db_session, owner, transcript_id=transcript.id,
        payload=ConsultationSplitDraftConfirmRequest(intent_id=intent.id, expected_updated_at=utcnow()),
    )

    batch = db_session.get(ConsultationSplitBatch, first.batch_id)
    assert first.idempotency_replayed is False
    assert replay.idempotency_replayed is True
    assert replay.batch_id == first.batch_id
    assert batch.intent_id == intent.id
    assert batch.materialization_transcript_version_id is not None
    assert analysis.transcript_version_id is None
    assert batch.confirmed_plan_encrypted != "Synthetic prompt"
    assert is_encrypted_envelope(batch.confirmed_plan_encrypted)
    assert db_session.get(ConsultationSplitIntent, intent.id).status is ConsultationSplitIntentStatus.confirmed
    before = {
        "documents": db_session.query(GeneratedDocument).count(),
        "attempts": db_session.query(ProviderAttempt).count(),
        "dispatches": db_session.query(TaskDispatchOutbox).count(),
    }
    with pytest.raises(AppError) as confirmed:
        continue_consultation_split_intent_as_one_note(
            db_session, owner, transcript_id=transcript.id, intent_id=intent.id,
        )
    assert confirmed.value.code == "consultation_split_intent_confirmed"
    assert db_session.get(ConsultationSplitIntent, intent.id).status is ConsultationSplitIntentStatus.confirmed
    assert db_session.get(ConsultationSplitBatch, batch.id) is not None
    assert {
        "documents": db_session.query(GeneratedDocument).count(),
        "attempts": db_session.query(ProviderAttempt).count(),
        "dispatches": db_session.query(TaskDispatchOutbox).count(),
    } == before
    # Main Regenerate is a new immutable batch from this confirmed batch. It
    # must not recreate review/analysis or alter the original batch.
    batch.status = ConsultationSplitBatchStatus.ready
    db_session.commit()
    monkeypatch.setattr(
        "app.services.consultation_split_regeneration.try_publish_task_dispatch_safely",
        lambda *_args, **_kwargs: None,
    )
    regenerated = regenerate_confirmed_split_batch(
        db_session, owner, transcript_id=transcript.id, batch_id=batch.id,
        client_idempotency_key=uuid4(),
    )
    replayed = regenerate_confirmed_split_batch(
        db_session, owner, transcript_id=transcript.id, batch_id=batch.id,
        client_idempotency_key=db_session.get(ConsultationSplitBatch, regenerated.batch_id).intent.client_idempotency_key,
    )
    regenerated_batch = db_session.get(ConsultationSplitBatch, regenerated.batch_id)
    assert regenerated.replayed is False
    assert replayed.replayed is True
    assert replayed.batch_id == regenerated.batch_id
    assert regenerated_batch is not None and regenerated_batch.id != batch.id
    assert regenerated_batch.status is ConsultationSplitBatchStatus.generation_queued
    assert len(regenerated_batch.topics) == len(batch.topics)
    assert db_session.get(ConsultationSplitBatch, batch.id).status is ConsultationSplitBatchStatus.ready
    # The new intent link must not obstruct the transcript deletion root.
    batch_id, intent_id = batch.id, intent.id
    db_session.delete(db_session.get(Transcript, transcript.id))
    db_session.commit()
    assert db_session.get(ConsultationSplitBatch, batch_id) is None
    assert db_session.get(ConsultationSplitIntent, intent_id) is None


@pytest.mark.real_db_connections
def test_concurrent_split_confirmations_return_one_batch_and_one_replay(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, monkeypatch,
):
    """The intent lock serializes confirmation before any batch can be created."""
    owner = make_user(email=f"split-confirm-concurrent-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    _enable_split(db_session, owner, monkeypatch)
    _enable_split_generation_selection(owner, make_llm_config, make_llm_selection)
    template = make_template(owner=owner, actor=owner)
    version = db_session.scalar(select(PromptTemplateVersion).where(PromptTemplateVersion.template_id == template.id))
    analysis = create_split_analysis(
        db_session,
        owner,
        transcript_id=transcript.id,
        source_fingerprint="d" * 64,
        source_snapshot={
            "source_state": {"manual_pii": []},
            "sources": {"transcript": "[PERSON_1]", "working_note": {"mode": None, "value": None}, "dictation": ""},
            "clinical_nlp_hints": [],
            "phi_index": [],
        },
        candidate_template_snapshot={"templates": []},
        provider_snapshot={},
        proposal={},
    )
    analysis.status = ConsultationSplitAnalysisStatus.ready
    intent = ConsultationSplitIntent(
        owner_user_id=owner.id,
        team_id=owner.team_id,
        transcript_id=transcript.id,
        analysis_id=analysis.id,
        client_idempotency_key=uuid4(),
        retention_expires_at=transcript.retention_expires_at,
        generation_snapshot_encrypted="",
    )
    db_session.add(intent)
    db_session.flush()
    intent.generation_snapshot_encrypted = encrypt_json_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="consultation_split_intents",
        field="generation_snapshot_encrypted",
        record_id=intent.id,
        plaintext={"selected_template": {
            "template_id": str(template.id), "template_version_id": str(version.id), "template_version_no": version.version_no,
            "name": template.name, "description": template.description, "mode": version.mode.value,
            "prompt_text": version.prompt_text, "config": None, "structured_sections": None,
        }, "generation_configuration": {"note_generation_length": "normal", "llm_detail_level": "balanced"}},
    )
    draft = create_split_draft(db_session, owner, analysis=analysis)
    for order, primary in enumerate((True, False)):
        create_split_draft_topic(
            db_session, owner, draft=draft, title=f"Topic {order}", topic_order=order,
            is_primary=primary, disposition=ConsultationSplitTopicDisposition.separate_note,
            template_id=template.id, template_version_id=version.id,
        )
    db_session.commit()
    expected_updated_at = draft.updated_at
    db_session.rollback()
    monkeypatch.setattr(
        "app.services.consultation_split_confirmation.current_consultation_split_analysis_source_matches",
        lambda *_args, **_kwargs: True,
    )

    template_locked, release_first, second_started, second_done = Event(), Event(), Event(), Event()
    responses, errors = [], []
    from app.services import consultation_split_confirmation as confirmation_service

    original_lock_templates = confirmation_service._locked_template_snapshots

    def pause_first_template_lock(*args, **kwargs):
        template_locked.set()
        assert release_first.wait(5)
        return original_lock_templates(*args, **kwargs)

    monkeypatch.setattr(confirmation_service, "_locked_template_snapshots", pause_first_template_lock)
    session_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, future=True)

    def confirm_in_session(*, is_second: bool) -> None:
        with session_factory() as session:
            try:
                session.execute(text("SET LOCAL lock_timeout = '2s'"))
                session.execute(text("SET LOCAL statement_timeout = '5s'"))
                actor = session.get(type(owner), owner.id)
                assert actor is not None
                if is_second:
                    second_started.set()
                responses.append(confirm_split_draft(
                    session, actor, transcript_id=transcript.id,
                    payload=ConsultationSplitDraftConfirmRequest(intent_id=intent.id, expected_updated_at=expected_updated_at),
                ))
            except BaseException as exc:
                errors.append(exc)
                session.rollback()
            finally:
                if is_second:
                    second_done.set()

    first_thread = Thread(target=confirm_in_session, kwargs={"is_second": False})
    second_thread = Thread(target=confirm_in_session, kwargs={"is_second": True})
    first_thread.start()
    if not template_locked.wait(5):
        first_thread.join(1)
        raise errors[0] if errors else AssertionError("first confirmation did not reach the template lock")
    second_thread.start()
    assert second_started.wait(5)
    assert not second_done.wait(0.2), "second confirmation must wait for the intent lock"
    release_first.set()
    first_thread.join(5)
    second_thread.join(5)
    assert not first_thread.is_alive() and not second_thread.is_alive()
    assert errors == []
    assert len(responses) == 2
    assert {response.batch_id for response in responses} == {responses[0].batch_id}
    assert sorted(response.idempotency_replayed for response in responses) == [False, True]
    assert db_session.query(ConsultationSplitBatch).filter_by(intent_id=intent.id).count() == 1


def test_confirm_route_is_strict_csrf_owner_safe_and_replay_aware(client, raw_client, make_user, monkeypatch):
    owner = make_user(email="split-confirm-route@example.com", mfa_required=False, mfa_enabled=False)
    transcript_id, intent_id, batch_id = uuid4(), uuid4(), uuid4()
    path = f"/api/v1/transcripts/{transcript_id}/consultation-split-draft/confirm"
    body = {"intent_id": str(intent_id), "expected_updated_at": "2026-01-01T00:00:00Z"}
    calls = []

    def confirmed(*_args, **_kwargs):
        calls.append(True)
        return ConsultationSplitDraftConfirmResponse(
            batch_id=batch_id, status="generation_queued", separate_note_count=2, topic_count=2,
            created_at=utcnow(), updated_at=utcnow(), idempotency_replayed=len(calls) > 1,
        )

    monkeypatch.setattr("app.routes.api_routes.confirm_split_draft", confirmed)
    assert raw_client.post(path, json=body).status_code == 401
    assert raw_client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200
    assert raw_client.post(path, json=body).status_code == 403
    raw_client.post("/api/v1/auth/logout")
    assert client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200
    assert client.post(path, json={**body, "unexpected": True}).status_code == 422
    created = client.post(path, json=body)
    replay = client.post(path, json={**body, "expected_updated_at": "2027-01-01T00:00:00Z"})
    assert created.status_code == 202
    assert replay.status_code == 200
    assert created.headers["cache-control"] == "no-store"
    assert set(created.json()) == {"batch_id", "status", "separate_note_count", "topic_count", "created_at", "updated_at", "idempotency_replayed"}


def test_post_gate_off_is_explicit_and_no_store(client, make_user, monkeypatch):
    owner = make_user(email="split-api-gate@example.com", mfa_required=False, mfa_enabled=False)
    monkeypatch.delenv("CONSULTATION_SPLITTING_ENABLED", raising=False)
    assert client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200

    response = client.post("/api/v1/transcripts/%s/consultation-split-analysis" % uuid4())

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "consultation_split_disabled"
    assert response.headers["cache-control"] == "no-store"


def test_post_user_opt_in_off_is_explicit_and_does_not_queue(client, make_user, monkeypatch):
    owner = make_user(email="split-api-user-gate@example.com", mfa_required=False, mfa_enabled=False)
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    monkeypatch.setattr(
        "app.services.consultation_split_api.queue_or_reuse_split_analysis",
        lambda *_args, **_kwargs: pytest.fail("user opt-in gate must run before queueing"),
    )
    assert client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200

    response = client.post(f"/api/v1/transcripts/{uuid4()}/consultation-split-analysis")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "consultation_split_disabled"


def test_post_requires_csrf_and_owner_scope(raw_client, make_user, monkeypatch):
    owner = make_user(email="split-api-csrf@example.com", mfa_required=False, mfa_enabled=False)
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    assert raw_client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200

    response = raw_client.post(f"/api/v1/transcripts/{uuid4()}/consultation-split-analysis")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_owner_post_returns_accepted_safe_projection(client, db_session, make_user, monkeypatch):
    owner = make_user(email="split-api-owner@example.com", mfa_required=False, mfa_enabled=False)
    transcript = _transcript(db_session, owner)
    _enable_split(db_session, owner, monkeypatch)
    analysis = _analysis(db_session, owner, transcript)
    monkeypatch.setattr(
        "app.services.consultation_split_api.queue_or_reuse_split_analysis",
        lambda *_args, **_kwargs: QueueOrReuseSplitAnalysisResult("queued", analysis=analysis),
    )
    assert client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200

    response = client.post(f"/api/v1/transcripts/{transcript.id}/consultation-split-analysis")

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert set(response.json()) == {"analysis_id", "status", "error_code", "updated_at", "completed_at", "topics"}


@pytest.mark.parametrize("persisted_status", ["queued", "processing"])
def test_incomplete_queue_outcome_never_exposes_internal_lifecycle_status(
    client, db_session, make_user, monkeypatch, persisted_status,
):
    owner = make_user(
        email=f"split-api-incomplete-{persisted_status}@example.com",
        mfa_required=False,
        mfa_enabled=False,
    )
    transcript = _transcript(db_session, owner)
    _enable_split(db_session, owner, monkeypatch)
    analysis = _analysis(
        db_session,
        owner,
        transcript,
        status=ConsultationSplitAnalysisStatus(persisted_status),
    )
    monkeypatch.setattr(
        "app.services.consultation_split_api.queue_or_reuse_split_analysis",
        lambda *_args, **_kwargs: QueueOrReuseSplitAnalysisResult("incomplete", analysis=analysis),
    )
    assert client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200

    response = client.post(f"/api/v1/transcripts/{transcript.id}/consultation-split-analysis")

    assert response.status_code == 200
    assert response.json()["status"] == "incomplete"
    assert response.json()["error_code"] == "consultation_split_analysis_unavailable"
    assert "execution" not in response.text


def test_projection_has_exact_safe_topic_fields_and_no_snapshots(db_session, make_user, monkeypatch):
    owner = make_user(email="split-api-projection@example.com")
    transcript = _transcript(db_session, owner)
    template_id = uuid4()
    analysis = _analysis(
        db_session,
        owner,
        transcript,
        status=ConsultationSplitAnalysisStatus.ready,
        proposal={
            "topics": [
                {
                    "topic_uuid": str(uuid4()),
                    "title": "Chest pain",
                    "is_primary": True,
                    "disposition": "separate_note",
                    "template_id": str(template_id),
                }
            ]
        },
    )
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    db_session.add(UserAppPreference(user_id=owner.id, preferences_json={"split_consultations_into_separate_notes": True}))
    db_session.commit()
    monkeypatch.setattr(
        "app.services.consultation_split_api.queue_or_reuse_split_analysis",
        lambda *_args, **_kwargs: QueueOrReuseSplitAnalysisResult("ready", analysis=analysis),
    )

    payload = queue_split_analysis_api(db_session, owner, transcript_id=transcript.id).model_dump(mode="json")

    assert set(payload) == {"analysis_id", "status", "error_code", "updated_at", "completed_at", "topics"}
    assert set(payload["topics"][0]) == {"topic_uuid", "title", "is_primary", "disposition", "template_id"}
    assert not any(secret in str(payload) for secret in ("source_snapshot", "provider", "fingerprint", "execution", "recoverable"))


def test_malformed_encrypted_proposal_fails_closed(db_session, make_user, monkeypatch):
    owner = make_user(email="split-api-malformed@example.com")
    transcript = _transcript(db_session, owner)
    analysis = _analysis(
        db_session,
        owner,
        transcript,
        status=ConsultationSplitAnalysisStatus.ready,
        proposal={"topics": []},
    )
    analysis.proposal_encrypted = "not-an-encrypted-envelope"
    db_session.commit()
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    db_session.add(UserAppPreference(user_id=owner.id, preferences_json={"split_consultations_into_separate_notes": True}))
    db_session.commit()
    monkeypatch.setattr(
        "app.services.consultation_split_api.queue_or_reuse_split_analysis",
        lambda *_args, **_kwargs: QueueOrReuseSplitAnalysisResult("ready", analysis=analysis),
    )

    payload = queue_split_analysis_api(db_session, owner, transcript_id=transcript.id)

    assert payload.status == "incomplete"
    assert payload.error_code == "consultation_split_proposal_unavailable"
    assert payload.topics == []
    assert "not-an-encrypted-envelope" not in payload.model_dump_json()


def test_workspace_reader_is_read_only_and_maps_source_mismatch_to_stale(db_session, make_user, monkeypatch):
    owner = make_user(email="split-api-workspace@example.com")
    transcript = _transcript(db_session, owner)
    analysis = _analysis(db_session, owner, transcript)
    _enable_split(db_session, owner, monkeypatch)
    monkeypatch.setattr(
        "app.services.consultation_split_api.current_consultation_split_analysis_source_matches",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "app.services.consultation_split_api.queue_or_reuse_split_analysis",
        lambda *_args, **_kwargs: pytest.fail("workspace GET must not queue"),
    )
    monkeypatch.setattr(
        "app.services.consultation_split_api.read_split_analysis_json",
        lambda *_args, **_kwargs: pytest.fail("stale workspace state must not decrypt proposal"),
    )
    before = db_session.scalar(select(ConsultationSplitAnalysis).where(ConsultationSplitAnalysis.id == analysis.id)).updated_at

    payload = read_workspace_split_analysis(db_session, owner, transcript_id=transcript.id)

    assert payload is not None
    assert payload.status == "stale"
    assert payload.topics == []
    assert db_session.scalar(select(ConsultationSplitAnalysis).where(ConsultationSplitAnalysis.id == analysis.id)).updated_at == before


def _ready_two_topic_analysis(db_session, owner, transcript):
    first, second = uuid4(), uuid4()
    analysis = _analysis(
        db_session,
        owner,
        transcript,
        status=ConsultationSplitAnalysisStatus.ready,
        proposal={
            "topics": [
                {"topic_uuid": str(first), "title": "First synthetic topic", "is_primary": True, "disposition": "separate_note", "template_id": None},
                {"topic_uuid": str(second), "title": "Second synthetic topic", "is_primary": False, "disposition": "include_in_primary", "template_id": None},
            ]
        },
    )
    return analysis, first, second


def test_draft_initialization_is_idempotent_and_titles_are_encrypted(db_session, make_user, monkeypatch):
    owner = make_user(email="split-draft-init@example.com")
    transcript = _transcript(db_session, owner)
    analysis, first_uuid, second_uuid = _ready_two_topic_analysis(db_session, owner, transcript)
    _enable_split(db_session, owner, monkeypatch)
    monkeypatch.setattr("app.services.consultation_split_drafts._source_matches", lambda *_args: True)

    first = initialize_or_reuse_split_draft(db_session, owner, transcript_id=transcript.id)
    second = initialize_or_reuse_split_draft(db_session, owner, transcript_id=transcript.id)
    drafts = db_session.scalars(select(ConsultationSplitDraft).where(ConsultationSplitDraft.analysis_id == analysis.id)).all()
    draft = db_session.get(ConsultationSplitDraft, first.draft_id)

    assert first.draft_id == second.draft_id
    assert [topic.topic_uuid for topic in first.topics] == [first_uuid, second_uuid]
    assert len(drafts) == 1
    assert draft is not None
    stored_titles = db_session.scalars(
        select(ConsultationSplitDraftTopic.title_encrypted)
        .where(ConsultationSplitDraftTopic.draft_id == draft.id)
    ).all()
    assert all(is_encrypted_envelope(value) and "synthetic topic" not in value for value in stored_titles)


def test_completed_split_can_reopen_same_review_draft_for_a_new_immutable_batch(
    db_session, make_user, monkeypatch
):
    owner = make_user(email="split-draft-regenerate@example.com")
    transcript = _transcript(db_session, owner)
    analysis, _, _ = _ready_two_topic_analysis(db_session, owner, transcript)
    _enable_split(db_session, owner, monkeypatch)
    monkeypatch.setattr("app.services.consultation_split_drafts._source_matches", lambda *_args: True)

    initialized = initialize_or_reuse_split_draft(db_session, owner, transcript_id=transcript.id)
    draft = db_session.get(ConsultationSplitDraft, initialized.draft_id)
    assert draft is not None
    draft.status = ConsultationSplitDraftStatus.confirmed
    before = draft.updated_at
    materialization_version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted="",
    )
    db_session.add(materialization_version)
    db_session.flush()
    batch = create_split_batch(
        db_session,
        owner,
        analysis=analysis,
        confirmed_plan={"topics": []},
        clinical_snapshot={},
        source_snapshot={"source": "synthetic"},
        template_snapshot={},
        pii_snapshot={},
        provider_snapshot={"provider": "synthetic"},
        note_options_snapshot={},
        materialization_transcript_version_id=materialization_version.id,
    )
    batch.status = ConsultationSplitBatchStatus.ready
    db_session.commit()

    reopened = initialize_or_reuse_split_draft(db_session, owner, transcript_id=transcript.id)

    assert reopened.draft_id == draft.id
    assert reopened.status == "active"
    assert reopened.updated_at > before
    assert db_session.get(ConsultationSplitBatch, batch.id).status is ConsultationSplitBatchStatus.ready


@pytest.mark.parametrize(
    "topics",
    [
        [
            {"topic_uuid": str(uuid4()), "title": "No primary one", "is_primary": False, "disposition": "separate_note", "template_id": None},
            {"topic_uuid": str(uuid4()), "title": "No primary two", "is_primary": False, "disposition": "include_in_primary", "template_id": None},
        ],
        [
            {"topic_uuid": str(uuid4()), "title": "Primary one", "is_primary": True, "disposition": "separate_note", "template_id": None},
            {"topic_uuid": str(uuid4()), "title": "Primary two", "is_primary": True, "disposition": "separate_note", "template_id": None},
        ],
        [
            {"topic_uuid": str(uuid4()), "title": "Wrong primary", "is_primary": True, "disposition": "include_in_primary", "template_id": None},
            {"topic_uuid": str(uuid4()), "title": "Secondary", "is_primary": False, "disposition": "separate_note", "template_id": None},
        ],
        [
            {"topic_uuid": "00000000-0000-0000-0000-000000001234", "title": "Duplicate UUID one", "is_primary": True, "disposition": "separate_note", "template_id": None},
            {"topic_uuid": "00000000-0000-0000-0000-000000001234", "title": "Duplicate UUID two", "is_primary": False, "disposition": "include_in_primary", "template_id": None},
        ],
        [
            {"topic_uuid": str(uuid4()), "title": "Duplicate title", "is_primary": True, "disposition": "separate_note", "template_id": None},
            {"topic_uuid": str(uuid4()), "title": "  duplicate TITLE  ", "is_primary": False, "disposition": "include_in_primary", "template_id": None},
        ],
    ],
    ids=["zero-primary", "multiple-primary", "primary-wrong-disposition", "duplicate-topic-uuid", "duplicate-topic-title"],
)
def test_draft_initialization_rejects_incoherent_ready_proposal_without_persisting_draft(
    db_session, make_user, monkeypatch, topics,
):
    owner = make_user(email=f"split-draft-invalid-proposal-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    analysis = _analysis(
        db_session,
        owner,
        transcript,
        status=ConsultationSplitAnalysisStatus.ready,
        proposal={"topics": topics},
    )
    _enable_split(db_session, owner, monkeypatch)
    monkeypatch.setattr("app.services.consultation_split_drafts._source_matches", lambda *_args: True)

    with pytest.raises(AppError) as unavailable:
        initialize_or_reuse_split_draft(db_session, owner, transcript_id=transcript.id)

    assert unavailable.value.status_code == 409
    assert unavailable.value.code == "consultation_split_proposal_unavailable"
    assert db_session.scalar(select(ConsultationSplitDraft).where(ConsultationSplitDraft.analysis_id == analysis.id)) is None
    assert db_session.scalar(
        select(ConsultationSplitDraftTopic).where(ConsultationSplitDraftTopic.transcript_id == transcript.id)
    ) is None


@pytest.mark.parametrize(
    "candidate_snapshot",
    [
        None,
        {"templates": [{"id": str(uuid4()), "name": "Malformed"}]},
    ],
    ids=["missing", "malformed"],
)
def test_draft_initialization_rejects_missing_or_malformed_frozen_candidate_snapshot(
    db_session, make_user, monkeypatch, candidate_snapshot,
):
    owner = make_user(email=f"split-draft-invalid-candidates-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    analysis, _, _ = _ready_two_topic_analysis(db_session, owner, transcript)
    if candidate_snapshot is None:
        analysis.candidate_template_snapshot_encrypted = None
    else:
        analysis.candidate_template_snapshot_encrypted = encrypt_json_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="consultation_split_analyses",
            field="candidate_template_snapshot_encrypted",
            record_id=analysis.id,
            plaintext=candidate_snapshot,
        )
    db_session.commit()
    _enable_split(db_session, owner, monkeypatch)
    monkeypatch.setattr("app.services.consultation_split_drafts._source_matches", lambda *_args: True)

    with pytest.raises(AppError) as unavailable:
        initialize_or_reuse_split_draft(db_session, owner, transcript_id=transcript.id)

    assert unavailable.value.status_code == 409
    assert unavailable.value.code == "consultation_split_proposal_unavailable"
    assert db_session.scalar(select(ConsultationSplitDraft).where(ConsultationSplitDraft.analysis_id == analysis.id)) is None


def test_draft_initialization_rejects_accessibly_tampered_noncandidate_template(
    db_session, make_user, make_template, monkeypatch,
):
    owner = make_user(email=f"split-draft-noncandidate-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    accessible_template = make_template(scope=TemplateScope.user, owner=owner, actor=owner)
    analysis = _analysis(
        db_session,
        owner,
        transcript,
        status=ConsultationSplitAnalysisStatus.ready,
        proposal={"topics": [
            {
                "topic_uuid": str(uuid4()),
                "title": "Tampered template topic one",
                "is_primary": True,
                "disposition": "separate_note",
                "template_id": str(accessible_template.id),
            },
            {
                "topic_uuid": str(uuid4()),
                "title": "Tampered template topic two",
                "is_primary": False,
                "disposition": "include_in_primary",
                "template_id": None,
            },
        ]},
        candidate_template_snapshot={"templates": []},
    )
    _enable_split(db_session, owner, monkeypatch)
    monkeypatch.setattr("app.services.consultation_split_drafts._source_matches", lambda *_args: True)

    with pytest.raises(AppError) as unavailable:
        initialize_or_reuse_split_draft(db_session, owner, transcript_id=transcript.id)

    assert unavailable.value.status_code == 409
    assert unavailable.value.code == "consultation_split_proposal_unavailable"
    assert db_session.scalar(select(ConsultationSplitDraft).where(ConsultationSplitDraft.analysis_id == analysis.id)) is None
    assert db_session.scalar(
        select(ConsultationSplitDraftTopic).where(ConsultationSplitDraftTopic.transcript_id == transcript.id)
    ) is None


def test_draft_get_projects_stale_without_writing_and_put_commits_stale(db_session, make_user, monkeypatch):
    owner = make_user(email="split-draft-stale@example.com")
    transcript = _transcript(db_session, owner)
    _ready_two_topic_analysis(db_session, owner, transcript)
    _enable_split(db_session, owner, monkeypatch)
    monkeypatch.setattr("app.services.consultation_split_drafts._source_matches", lambda *_args: True)
    initialized = initialize_or_reuse_split_draft(db_session, owner, transcript_id=transcript.id)
    draft = db_session.get(ConsultationSplitDraft, initialized.draft_id)
    before = draft.updated_at
    monkeypatch.setattr("app.services.consultation_split_drafts._source_matches", lambda *_args: False)

    projected = read_split_draft(db_session, owner, transcript_id=transcript.id)
    assert projected.status == "stale"
    assert db_session.get(ConsultationSplitDraft, initialized.draft_id).status.value == "active"
    assert db_session.get(ConsultationSplitDraft, initialized.draft_id).updated_at == before
    with pytest.raises(AppError) as stale:
        replace_split_draft(
            db_session,
            owner,
            transcript_id=transcript.id,
            payload=ConsultationSplitDraftReplace(expected_updated_at=before, topics=[]),
        )
    assert stale.value.status_code == 409
    assert db_session.get(ConsultationSplitDraft, initialized.draft_id).status.value == "stale"


def test_draft_replace_preserves_existing_uuid_and_advances_timestamp(db_session, make_user, monkeypatch):
    owner = make_user(email="split-draft-replace@example.com")
    transcript = _transcript(db_session, owner)
    _, first_uuid, _ = _ready_two_topic_analysis(db_session, owner, transcript)
    _enable_split(db_session, owner, monkeypatch)
    monkeypatch.setattr("app.services.consultation_split_drafts._source_matches", lambda *_args: True)
    initialized = initialize_or_reuse_split_draft(db_session, owner, transcript_id=transcript.id)

    saved = replace_split_draft(
        db_session,
        owner,
        transcript_id=transcript.id,
        payload=ConsultationSplitDraftReplace(
            expected_updated_at=initialized.updated_at,
            topics=[
                ConsultationSplitDraftTopicReplace(
                    topic_uuid=first_uuid,
                    title="Renamed synthetic topic",
                    is_primary=True,
                    disposition="separate_note",
                    template_id=None,
                )
            ],
        ),
    )
    assert [topic.topic_uuid for topic in saved.topics] == [first_uuid]
    assert saved.topics[0].title == "Renamed synthetic topic"
    assert saved.updated_at > initialized.updated_at
    with pytest.raises(AppError) as conflict:
        replace_split_draft(
            db_session,
            owner,
            transcript_id=transcript.id,
            payload=ConsultationSplitDraftReplace(expected_updated_at=initialized.updated_at, topics=[]),
        )
    assert conflict.value.code == "consultation_split_draft_conflict"


def test_draft_seed_drops_deleted_template_and_put_requires_template_for_new_topic(db_session, make_user, make_template, monkeypatch):
    owner = make_user(email="split-draft-template-seed@example.com")
    transcript = _transcript(db_session, owner)
    template = make_template(owner=owner, actor=owner)
    analysis, _, _ = _ready_two_topic_analysis(db_session, owner, transcript)
    analysis.proposal_encrypted = encrypt_json_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="consultation_split_analyses",
        field="proposal_encrypted",
        record_id=analysis.id,
        plaintext={"topics": [
            {"topic_uuid": str(uuid4()), "title": "Template seed one", "is_primary": True, "disposition": "separate_note", "template_id": str(template.id)},
            {"topic_uuid": str(uuid4()), "title": "Template seed two", "is_primary": False, "disposition": "include_in_primary", "template_id": None},
        ]},
    )
    analysis.candidate_template_snapshot_encrypted = encrypt_json_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="consultation_split_analyses",
        field="candidate_template_snapshot_encrypted",
        record_id=analysis.id,
        plaintext={"templates": [{
            "id": str(template.id),
            "name": template.name,
            "description": template.description,
            "mode": "freeform",
        }]},
    )
    template.is_active = False
    db_session.commit()
    _enable_split(db_session, owner, monkeypatch)
    monkeypatch.setattr("app.services.consultation_split_drafts._source_matches", lambda *_args: True)

    draft = initialize_or_reuse_split_draft(db_session, owner, transcript_id=transcript.id)
    assert draft.topics[0].template_id is None
    with pytest.raises(AppError) as no_template:
        replace_split_draft(
            db_session, owner, transcript_id=transcript.id,
            payload=ConsultationSplitDraftReplace(
                expected_updated_at=draft.updated_at,
                topics=[ConsultationSplitDraftTopicReplace(title="New topic", is_primary=True, disposition="separate_note")],
            ),
        )
    assert no_template.value.code == "validation_error"


def test_draft_replace_rejects_foreign_uuid_and_uses_latest_accessible_template(db_session, make_user, make_template, monkeypatch):
    owner = make_user(email="split-draft-template-current@example.com")
    transcript = _transcript(db_session, owner)
    template = make_template(scope=TemplateScope.team, team=owner.team, actor=owner)
    version_two = PromptTemplateVersion(
        template_id=template.id, version_no=2, mode=TemplateMode.freeform,
        prompt_text="Updated template", created_by_user_id=owner.id,
    )
    db_session.add(version_two)
    db_session.commit()
    _, first_uuid, _ = _ready_two_topic_analysis(db_session, owner, transcript)
    _enable_split(db_session, owner, monkeypatch)
    monkeypatch.setattr("app.services.consultation_split_drafts._source_matches", lambda *_args: True)
    draft = initialize_or_reuse_split_draft(db_session, owner, transcript_id=transcript.id)
    with pytest.raises(AppError) as foreign:
        replace_split_draft(
            db_session, owner, transcript_id=transcript.id,
            payload=ConsultationSplitDraftReplace(
                expected_updated_at=draft.updated_at,
                topics=[ConsultationSplitDraftTopicReplace(topic_uuid=uuid4(), title="Foreign", is_primary=True, disposition="separate_note", template_id=template.id)],
            ),
        )
    assert foreign.value.code == "validation_error"
    saved = replace_split_draft(
        db_session, owner, transcript_id=transcript.id,
        payload=ConsultationSplitDraftReplace(
            expected_updated_at=draft.updated_at,
            topics=[ConsultationSplitDraftTopicReplace(topic_uuid=first_uuid, title="Current template", is_primary=True, disposition="separate_note", template_id=template.id)],
        ),
    )
    assert saved.topics[0].template_id == template.id
    assert saved.topics[0].template_version_id == version_two.id


@pytest.mark.real_db_connections
def test_draft_replace_and_template_delete_follow_template_then_topic_lock_order(
    db_session, make_user, make_template, monkeypatch,
):
    """A template delete waits for replacement's template lock, then completes."""
    owner = make_user(email=f"split-draft-template-lock-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner)
    _analysis(
        db_session,
        owner,
        transcript,
        status=ConsultationSplitAnalysisStatus.ready,
        proposal={"topics": [
            {
                "topic_uuid": str(uuid4()),
                "title": "Template-bound topic",
                "is_primary": True,
                "disposition": "separate_note",
                "template_id": str(template.id),
            },
            {
                "topic_uuid": str(uuid4()),
                "title": "Other topic",
                "is_primary": False,
                "disposition": "include_in_primary",
                "template_id": None,
            },
        ]},
        candidate_template_snapshot={"templates": [{
            "id": str(template.id),
            "name": template.name,
            "description": template.description,
            "mode": "freeform",
        }]},
    )
    _enable_split(db_session, owner, monkeypatch)
    monkeypatch.setattr("app.services.consultation_split_drafts._source_matches", lambda *_args: True)
    draft = initialize_or_reuse_split_draft(db_session, owner, transcript_id=transcript.id)
    topic = next(item for item in draft.topics if item.template_id == template.id)
    # The direct service projection uses FOR UPDATE; release this fixture
    # session before opening the two independent connections under test.
    db_session.rollback()

    session_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, future=True)
    template_locked, release_replace, delete_entered, delete_done = Event(), Event(), Event(), Event()
    errors: list[BaseException] = []
    from app.services import consultation_split_drafts as draft_service

    original_lock_topics = draft_service._lock_topics

    def pause_before_topic_lock(*args, **kwargs):
        template_locked.set()
        assert release_replace.wait(5)
        return original_lock_topics(*args, **kwargs)

    monkeypatch.setattr(draft_service, "_lock_topics", pause_before_topic_lock)

    def replace_in_first_session() -> None:
        with session_factory() as session:
            try:
                session.execute(text("SET LOCAL lock_timeout = '2s'"))
                session.execute(text("SET LOCAL statement_timeout = '5s'"))
                actor = session.get(type(owner), owner.id)
                assert actor is not None
                replace_split_draft(
                    session,
                    actor,
                    transcript_id=transcript.id,
                    payload=ConsultationSplitDraftReplace(
                        expected_updated_at=draft.updated_at,
                        topics=[
                            ConsultationSplitDraftTopicReplace(
                                topic_uuid=topic.topic_uuid,
                                title="Updated template-bound topic",
                                is_primary=True,
                                disposition="separate_note",
                                template_id=template.id,
                            ),
                        ],
                    ),
                )
            except BaseException as exc:
                errors.append(exc)
                session.rollback()

    def delete_in_second_session() -> None:
        assert template_locked.wait(5)
        with session_factory() as session:
            try:
                session.execute(text("SET LOCAL lock_timeout = '2s'"))
                session.execute(text("SET LOCAL statement_timeout = '5s'"))
                actor = session.get(type(owner), owner.id)
                assert actor is not None
                delete_entered.set()
                delete_personal_template(session, actor, template_id=template.id)
            except BaseException as exc:
                errors.append(exc)
                session.rollback()
            finally:
                delete_done.set()

    replacing = Thread(target=replace_in_first_session)
    deleting = Thread(target=delete_in_second_session)
    replacing.start()
    assert template_locked.wait(5)
    deleting.start()
    assert delete_entered.wait(5)
    assert not delete_done.wait(0.2), "template deletion must wait for the locked template"
    release_replace.set()
    replacing.join(5)
    deleting.join(5)
    assert not replacing.is_alive() and not deleting.is_alive()
    assert errors == []


@pytest.mark.real_db_connections
def test_split_confirmation_and_template_delete_share_template_first_lock_order(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, monkeypatch,
):
    """A delete waits on confirmation's template lock; confirmation keeps its snapshot."""
    owner = make_user(email=f"split-confirm-template-lock-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    _enable_split(db_session, owner, monkeypatch)
    _enable_split_generation_selection(owner, make_llm_config, make_llm_selection)
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner)
    version = db_session.scalar(select(PromptTemplateVersion).where(PromptTemplateVersion.template_id == template.id))
    analysis = create_split_analysis(
        db_session, owner, transcript_id=transcript.id, source_fingerprint="e" * 64,
        source_snapshot={"source_state": {"manual_pii": []}, "sources": {"transcript": "[PERSON_1]", "working_note": {"mode": None, "value": None}, "dictation": ""}, "clinical_nlp_hints": [], "phi_index": []},
        candidate_template_snapshot={"templates": []}, provider_snapshot={}, proposal={},
    )
    analysis.status = ConsultationSplitAnalysisStatus.ready
    intent = ConsultationSplitIntent(owner_user_id=owner.id, team_id=owner.team_id, transcript_id=transcript.id, analysis_id=analysis.id, client_idempotency_key=uuid4(), retention_expires_at=transcript.retention_expires_at, generation_snapshot_encrypted="")
    db_session.add(intent)
    db_session.flush()
    intent.generation_snapshot_encrypted = encrypt_json_for_owner(
        db_session, owner_user_id=owner.id, table="consultation_split_intents", field="generation_snapshot_encrypted", record_id=intent.id,
        plaintext={"selected_template": {"template_id": str(template.id), "template_version_id": str(version.id), "template_version_no": version.version_no, "name": template.name, "description": template.description, "mode": version.mode.value, "prompt_text": version.prompt_text, "config": None, "structured_sections": None}, "generation_configuration": {"note_generation_length": "normal", "llm_detail_level": "balanced"}},
    )
    draft = create_split_draft(db_session, owner, analysis=analysis)
    for order, primary in enumerate((True, False)):
        create_split_draft_topic(db_session, owner, draft=draft, title=f"Topic {order}", topic_order=order, is_primary=primary, disposition=ConsultationSplitTopicDisposition.separate_note, template_id=template.id, template_version_id=version.id)
    db_session.commit()
    expected_updated_at = draft.updated_at
    db_session.rollback()
    monkeypatch.setattr("app.services.consultation_split_confirmation.current_consultation_split_analysis_source_matches", lambda *_args, **_kwargs: True)

    locked, release_confirmation, delete_entered, delete_done = Event(), Event(), Event(), Event()
    errors: list[BaseException] = []
    responses = []
    from app.services import consultation_split_confirmation as confirmation_service
    original_lock_templates = confirmation_service._locked_template_snapshots

    def pause_after_template_lock(*args, **kwargs):
        result = original_lock_templates(*args, **kwargs)
        locked.set()
        assert release_confirmation.wait(5)
        return result

    monkeypatch.setattr(confirmation_service, "_locked_template_snapshots", pause_after_template_lock)
    session_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, future=True)

    def confirm() -> None:
        with session_factory() as session:
            try:
                session.execute(text("SET LOCAL lock_timeout = '2s'"))
                session.execute(text("SET LOCAL statement_timeout = '5s'"))
                actor = session.get(type(owner), owner.id)
                assert actor is not None
                responses.append(confirm_split_draft(session, actor, transcript_id=transcript.id, payload=ConsultationSplitDraftConfirmRequest(intent_id=intent.id, expected_updated_at=expected_updated_at)))
            except BaseException as exc:
                errors.append(exc)
                session.rollback()

    def delete() -> None:
        assert locked.wait(5)
        with session_factory() as session:
            try:
                session.execute(text("SET LOCAL lock_timeout = '2s'"))
                session.execute(text("SET LOCAL statement_timeout = '5s'"))
                actor = session.get(type(owner), owner.id)
                assert actor is not None
                delete_entered.set()
                delete_personal_template(session, actor, template_id=template.id)
            except BaseException as exc:
                errors.append(exc)
                session.rollback()
            finally:
                delete_done.set()

    confirming, deleting = Thread(target=confirm), Thread(target=delete)
    confirming.start()
    assert locked.wait(5)
    deleting.start()
    assert delete_entered.wait(5)
    assert not delete_done.wait(0.2), "template delete must wait for confirmation's template lock"
    release_confirmation.set()
    confirming.join(5)
    deleting.join(5)
    assert not confirming.is_alive() and not deleting.is_alive()
    assert errors == []
    assert len(responses) == 1 and responses[0].idempotency_replayed is False
    assert db_session.query(ConsultationSplitBatch).filter_by(intent_id=intent.id).count() == 1
    template_id = template.id
    db_session.expire_all()
    assert db_session.get(type(template), template_id) is None


def test_draft_request_schema_enforces_topic_bounds_and_route_is_csrf_no_store(client, raw_client, make_user, monkeypatch):
    owner = make_user(email="split-draft-route@example.com", mfa_required=False, mfa_enabled=False)
    assert client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200
    monkeypatch.setattr(
        "app.routes.api_routes.initialize_or_reuse_split_draft",
        lambda *_args, **_kwargs: __import__("app.schemas.consultation_split", fromlist=["ConsultationSplitDraftDetail"]).ConsultationSplitDraftDetail(
            draft_id=uuid4(), analysis_id=uuid4(), status="active", created_at=utcnow(), updated_at=utcnow(), topics=[]
        ),
    )
    response = client.post(f"/api/v1/transcripts/{uuid4()}/consultation-split-draft")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert raw_client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200
    assert raw_client.post(f"/api/v1/transcripts/{uuid4()}/consultation-split-draft").status_code == 403
    with pytest.raises(ValidationError):
        ConsultationSplitDraftReplace(
            expected_updated_at=utcnow(),
            topics=[ConsultationSplitDraftTopicReplace(title=str(index), is_primary=index == 0, disposition="separate_note") for index in range(7)],
        )


@pytest.mark.parametrize("method", ["post", "get", "put"])
def test_draft_routes_require_authentication(raw_client, method):
    path = f"/api/v1/transcripts/{uuid4()}/consultation-split-draft"
    response = (
        raw_client.put(path, json={"expected_updated_at": utcnow().isoformat(), "topics": []})
        if method == "put"
        else getattr(raw_client, method)(path)
    )
    assert response.status_code == 401


@pytest.mark.parametrize("operation", ["initialize", "read", "replace"])
def test_draft_services_enforce_owner_role_scope_and_expiry(
    db_session, make_team, make_user, monkeypatch, operation,
):
    owner = make_user(email=f"split-draft-gate-owner-{operation}@example.com")
    transcript = _transcript(db_session, owner)
    peer = make_user(email=f"split-draft-gate-peer-{operation}@example.com", team=owner.team)
    foreign = make_user(email=f"split-draft-gate-foreign-{operation}@example.com", team=make_team(name=f"Split foreign {operation}"))
    leader = make_user(email=f"split-draft-gate-leader-{operation}@example.com", team=owner.team, team_role=TeamRole.leader)
    admin = make_user(email=f"split-draft-gate-admin-{operation}@example.com", is_system_admin=True)
    _enable_split(db_session, peer, monkeypatch)
    _enable_split(db_session, foreign, monkeypatch)
    _enable_split(db_session, leader, monkeypatch)
    _enable_split(db_session, admin, monkeypatch)

    def invoke(actor, transcript_id):
        if operation == "initialize":
            return initialize_or_reuse_split_draft(db_session, actor, transcript_id=transcript_id)
        if operation == "read":
            return read_split_draft(db_session, actor, transcript_id=transcript_id)
        return replace_split_draft(
            db_session,
            actor,
            transcript_id=transcript_id,
            payload=ConsultationSplitDraftReplace(expected_updated_at=utcnow(), topics=[]),
        )

    for non_owner in (peer, foreign):
        with pytest.raises(AppError) as cross_owner:
            invoke(non_owner, transcript.id)
        assert cross_owner.value.status_code == 404
    with pytest.raises(AppError) as leader_cross_owner:
        invoke(leader, transcript.id)
    assert leader_cross_owner.value.status_code == 404
    with pytest.raises(AppError) as forbidden:
        invoke(admin, transcript.id)
    assert forbidden.value.status_code == 403
    assert forbidden.value.code == "forbidden"

    expired = _transcript(db_session, foreign, expired=True)
    with pytest.raises(AppError) as unavailable:
        invoke(foreign, expired.id)
    assert unavailable.value.status_code == 404


def test_draft_get_does_not_prepare_sources_or_invoke_provider_or_redaction(db_session, make_user, monkeypatch):
    owner = make_user(email="split-draft-read-no-provider@example.com")
    transcript = _transcript(db_session, owner)
    _ready_two_topic_analysis(db_session, owner, transcript)
    _enable_split(db_session, owner, monkeypatch)
    monkeypatch.setattr("app.services.consultation_split_drafts._source_matches", lambda *_args: True)
    initialized = initialize_or_reuse_split_draft(db_session, owner, transcript_id=transcript.id)
    monkeypatch.setattr("app.services.consultation_split_sources.prepare_source_bound_consultation_split_analysis", lambda *_args, **_kwargs: pytest.fail("GET prepared sources"))
    monkeypatch.setattr("app.services.consultation_split_sources.ensure_redaction_run_for_transcript_version", lambda *_args, **_kwargs: pytest.fail("GET ran redaction"))
    monkeypatch.setattr("app.services.consultation_split_queue.queue_or_reuse_split_analysis", lambda *_args, **_kwargs: pytest.fail("GET queued provider work"))

    result = read_split_draft(db_session, owner, transcript_id=transcript.id)
    assert result.draft_id == initialized.draft_id


def test_draft_http_get_does_not_write_and_stale_put_persists_before_409(client, db_session, make_user, monkeypatch):
    owner = make_user(email="split-draft-http-stale@example.com", mfa_required=False, mfa_enabled=False)
    transcript = _transcript(db_session, owner)
    _ready_two_topic_analysis(db_session, owner, transcript)
    _enable_split(db_session, owner, monkeypatch)
    monkeypatch.setattr("app.services.consultation_split_drafts._source_matches", lambda *_args: True)
    initialized = initialize_or_reuse_split_draft(db_session, owner, transcript_id=transcript.id)
    draft = db_session.get(ConsultationSplitDraft, initialized.draft_id)
    before = draft.updated_at
    monkeypatch.setattr("app.services.consultation_split_drafts._source_matches", lambda *_args: False)
    assert client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200

    read = client.get(f"/api/v1/transcripts/{transcript.id}/consultation-split-draft")
    assert read.status_code == 200
    assert read.json()["status"] == "stale"
    assert db_session.get(ConsultationSplitDraft, initialized.draft_id).status.value == "active"
    assert db_session.get(ConsultationSplitDraft, initialized.draft_id).updated_at == before
    saved = client.put(
        f"/api/v1/transcripts/{transcript.id}/consultation-split-draft",
        json={"expected_updated_at": before.isoformat(), "topics": []},
    )
    assert saved.status_code == 409
    assert saved.json()["error"]["code"] == "consultation_split_source_stale"
    assert db_session.get(ConsultationSplitDraft, initialized.draft_id).status.value == "stale"


def test_workspace_restoration_includes_only_read_only_draft_projection(db_session, make_user, monkeypatch):
    owner = make_user(email="split-draft-workspace@example.com")
    transcript = _transcript(db_session, owner)
    _enable_split(db_session, owner, monkeypatch)
    expected = ConsultationSplitDraftDetail(
        draft_id=uuid4(), analysis_id=uuid4(), status="active", created_at=utcnow(), updated_at=utcnow(), topics=[]
    )
    monkeypatch.setattr("app.web.transcribe_workspace.read_workspace_split_analysis", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.web.transcribe_workspace.read_split_draft", lambda *_args, **_kwargs: expected)
    response = transcribe_workspace_response(
        db_session,
        {
            "active_transcript": transcript,
            "recent_transcripts": [], "generated_documents": [], "available_templates": [],
            "available_quick_actions": [], "available_smart_phrases": [],
        },
        current_user=owner,
    )
    assert response.consultation_split_draft == expected


def test_current_source_draft_wins_over_newer_historical_draft(db_session, make_user, monkeypatch):
    owner = make_user(email="split-draft-current-wins@example.com")
    transcript = _transcript(db_session, owner)
    historical, _, _ = _ready_two_topic_analysis(db_session, owner, transcript)
    current = _analysis(
        db_session,
        owner,
        transcript,
        status=ConsultationSplitAnalysisStatus.ready,
        fingerprint="c" * 64,
        proposal={
            "topics": [
                {"topic_uuid": str(uuid4()), "title": "Current one", "is_primary": True, "disposition": "separate_note", "template_id": None},
                {"topic_uuid": str(uuid4()), "title": "Current two", "is_primary": False, "disposition": "include_in_primary", "template_id": None},
            ]
        },
    )
    _enable_split(db_session, owner, monkeypatch)
    monkeypatch.setattr(
        "app.services.consultation_split_drafts._source_matches",
        lambda _db, _owner, _transcript, analysis: analysis.id == historical.id,
    )
    historical_draft = initialize_or_reuse_split_draft(db_session, owner, transcript_id=transcript.id)
    monkeypatch.setattr(
        "app.services.consultation_split_drafts._source_matches",
        lambda _db, _owner, _transcript, analysis: analysis.id == current.id,
    )
    current_draft = initialize_or_reuse_split_draft(db_session, owner, transcript_id=transcript.id)
    historical_row = db_session.get(ConsultationSplitDraft, historical_draft.draft_id)
    historical_row.updated_at = utcnow() + timedelta(days=1)
    db_session.commit()

    selected = read_split_draft(db_session, owner, transcript_id=transcript.id)
    assert selected.draft_id == current_draft.draft_id
    saved = replace_split_draft(
        db_session,
        owner,
        transcript_id=transcript.id,
        payload=ConsultationSplitDraftReplace(expected_updated_at=current_draft.updated_at, topics=[]),
    )
    assert saved.draft_id == current_draft.draft_id


def test_workspace_gate_off_is_absent_and_admin_cannot_read(db_session, make_user, monkeypatch):
    owner = make_user(email="split-api-workspace-off@example.com")
    transcript = _transcript(db_session, owner)
    monkeypatch.delenv("CONSULTATION_SPLITTING_ENABLED", raising=False)
    assert read_workspace_split_analysis(db_session, owner, transcript_id=transcript.id) is None

    admin = make_user(email="split-api-admin@example.com", is_system_admin=True, mfa_required=False, mfa_enabled=False)
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    assert read_workspace_split_analysis(db_session, admin, transcript_id=transcript.id) is None


def test_foreign_and_expired_post_denied(client, db_session, make_team, make_user, monkeypatch):
    owner = make_user(email="split-api-owner-denied@example.com", mfa_required=False, mfa_enabled=False)
    foreign = make_user(
        email="split-api-foreign@example.com",
        team=make_team(name="Foreign Split Team"),
        mfa_required=False,
        mfa_enabled=False,
    )
    transcript = _transcript(db_session, owner)
    _enable_split(db_session, foreign, monkeypatch)
    assert client.post("/api/v1/auth/login", json={"email": foreign.email, "password": "password-1"}).status_code == 200

    response = client.post(f"/api/v1/transcripts/{transcript.id}/consultation-split-analysis")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"

    expired = _transcript(db_session, foreign, expired=True)
    response = client.post(f"/api/v1/transcripts/{expired.id}/consultation-split-analysis")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_team_leader_and_system_admin_cannot_queue_owner_content(db_session, make_user, monkeypatch):
    owner = make_user(email="split-api-content-owner@example.com")
    transcript = _transcript(db_session, owner)
    leader = make_user(
        email="split-api-leader@example.com",
        team=owner.team,
        team_role=TeamRole.leader,
    )
    admin = make_user(email="split-api-system-admin@example.com", is_system_admin=True)
    _enable_split(db_session, leader, monkeypatch)
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")

    with pytest.raises(AppError) as leader_error:
        queue_split_analysis_api(db_session, leader, transcript_id=transcript.id)
    with pytest.raises(AppError) as admin_error:
        queue_split_analysis_api(db_session, admin, transcript_id=transcript.id)

    assert leader_error.value.status_code == 403
    assert leader_error.value.code == "forbidden"
    assert admin_error.value.status_code == 403
    assert admin_error.value.code == "forbidden"


def test_workspace_rest_and_sse_include_shared_safe_state(client, db_session, make_user, monkeypatch):
    owner = make_user(email="split-api-shared-state@example.com", mfa_required=False, mfa_enabled=False)
    _transcript(db_session, owner)
    _enable_split(db_session, owner, monkeypatch)
    monkeypatch.setattr(
        "app.web.transcribe_workspace.read_workspace_split_analysis",
        lambda *_args, **_kwargs: ConsultationSplitAnalysisDetail(status="processing"),
    )
    expected_draft = ConsultationSplitDraftDetail(
        draft_id=uuid4(), analysis_id=uuid4(), status="active", created_at=utcnow(), updated_at=utcnow(), topics=[]
    )
    monkeypatch.setattr(
        "app.web.transcribe_workspace.read_split_draft",
        lambda *_args, **_kwargs: expected_draft,
    )
    assert client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200

    rest = client.get("/api/v1/transcribe/workspace")
    assert rest.status_code == 200
    assert rest.json()["consultation_splitting_enabled"] is True
    assert rest.json()["consultation_split_analysis"]["status"] == "processing"
    assert rest.json()["consultation_split_draft"]["draft_id"] == str(expected_draft.draft_id)

    from app.schemas import TranscribeWorkspaceDetail

    shared_payload = TranscribeWorkspaceDetail.model_validate(rest.json())
    monkeypatch.setattr(
        "app.routes.api_routes.resolve_transcribe_workspace_detail",
        lambda *_args, **_kwargs: shared_payload,
    )
    stream = client.get("/api/v1/transcribe/workspace/stream?once=true")
    assert stream.status_code == 200
    event_payload = json.loads(stream.text.split("data: ", 1)[1].splitlines()[0])
    assert event_payload["consultation_splitting_enabled"] is True
    assert event_payload["consultation_split_analysis"]["status"] == "processing"
    assert event_payload["consultation_split_draft"]["draft_id"] == str(expected_draft.draft_id)


def test_workspace_effective_consultation_splitting_capability_is_owner_and_gate_scoped(
    client,
    db_session,
    make_team,
    make_user,
    monkeypatch,
):
    team = make_team(name="Split capability scope")
    owner = make_user(email="split-capability-owner@example.com", team=team, team_role=TeamRole.user)
    no_preference = make_user(email="split-capability-no-preference@example.com", team=team, team_role=TeamRole.user)
    leader = make_user(email="split-capability-leader@example.com", team=team, team_role=TeamRole.leader)
    admin = make_user(email="split-capability-admin@example.com", is_system_admin=True)
    for actor in (owner, leader):
        db_session.add(
            UserAppPreference(
                user_id=actor.id,
                preferences_json={"split_consultations_into_separate_notes": True},
            )
        )
    db_session.commit()
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")

    for actor, expected in ((owner, True), (no_preference, False), (leader, True), (admin, False)):
        client.post("/api/v1/auth/logout")
        assert client.post(
            "/api/v1/auth/login",
            json={"email": actor.email, "password": "password-1"},
        ).status_code == 200
        response = client.get("/api/v1/transcribe/workspace")
        assert response.status_code == 200
        assert response.json()["consultation_splitting_enabled"] is expected

    client.post("/api/v1/auth/logout")
    assert client.post(
        "/api/v1/auth/login",
        json={"email": owner.email, "password": "password-1"},
    ).status_code == 200
    monkeypatch.delenv("CONSULTATION_SPLITTING_ENABLED", raising=False)
    assert client.get("/api/v1/transcribe/workspace").json()["consultation_splitting_enabled"] is False


def test_workspace_split_projection_remains_empty_for_cross_owner_leader(
    db_session,
    make_team,
    make_user,
    monkeypatch,
):
    team = make_team(name="Split projection scope")
    owner = make_user(email="split-projection-owner@example.com", team=team, team_role=TeamRole.user)
    leader = make_user(email="split-projection-leader@example.com", team=team, team_role=TeamRole.leader)
    transcript = _transcript(db_session, owner)
    _enable_split(db_session, leader, monkeypatch)

    response = transcribe_workspace_response(
        db_session,
        {
            "active_transcript": transcript,
            "recent_transcripts": [],
            "generated_documents": [],
            "available_templates": [],
            "available_quick_actions": [],
            "available_smart_phrases": [],
            "consultation_splitting_enabled": True,
        },
        current_user=leader,
    )

    assert response.consultation_splitting_enabled is True
    assert response.consultation_split_analysis is None
    assert response.consultation_split_draft is None


@pytest.mark.parametrize("operation", ["keep-available-notes", "retry-missing-notes"])
def test_partial_recovery_routes_reject_nonowners_management_roles_and_expired_roots(
    client, db_session, make_team, make_user, monkeypatch, operation,
):
    """Recovery endpoints preserve transcript-content ownership and reveal no batch metadata."""
    owner = make_user(email=f"split-recovery-owner-{operation}@example.com", mfa_required=False, mfa_enabled=False)
    peer = make_user(email=f"split-recovery-peer-{operation}@example.com", team=owner.team, mfa_required=False, mfa_enabled=False)
    cross_team = make_user(
        email=f"split-recovery-cross-team-{operation}@example.com",
        team=make_team(name=f"Split recovery cross-team {operation}"),
        mfa_required=False,
        mfa_enabled=False,
    )
    leader = make_user(email=f"split-recovery-leader-{operation}@example.com", team=owner.team, team_role=TeamRole.leader, mfa_required=False, mfa_enabled=False)
    admin = make_user(email=f"split-recovery-admin-{operation}@example.com", is_system_admin=True, mfa_required=False, mfa_enabled=False)
    transcript = _transcript(db_session, owner)
    expired = _transcript(db_session, owner, expired=True)
    batch_id = uuid4()
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    # This test exercises owner/root authorization after the effective gate;
    # each normal actor must pass that gate first.
    for actor in (owner, peer, cross_team, leader):
        db_session.add(UserAppPreference(
            user_id=actor.id, preferences_json={"split_consultations_into_separate_notes": True},
        ))
    db_session.commit()

    for actor, target in ((peer, transcript.id), (cross_team, transcript.id), (owner, expired.id)):
        client.post("/api/v1/auth/logout")
        assert client.post("/api/v1/auth/login", json={"email": actor.email, "password": "password-1"}).status_code == 200
        response = client.post(
            f"/api/v1/transcripts/{target}/consultation-split-batches/{batch_id}/{operation}"
        )
        assert response.status_code == 404
        payload = response.json()
        assert payload["error"]["code"] == "consultation_split_batch_unavailable"
        assert str(batch_id) not in response.text
        assert transcript.title not in response.text

    client.post("/api/v1/auth/logout")
    assert client.post("/api/v1/auth/login", json={"email": leader.email, "password": "password-1"}).status_code == 200
    leader_cross_owner = client.post(
        f"/api/v1/transcripts/{transcript.id}/consultation-split-batches/{batch_id}/{operation}"
    )
    assert leader_cross_owner.status_code == 404
    assert leader_cross_owner.json()["error"]["code"] == "consultation_split_batch_unavailable"

    client.post("/api/v1/auth/logout")
    assert client.post("/api/v1/auth/login", json={"email": admin.email, "password": "password-1"}).status_code == 200
    admin_denied = client.post(
        f"/api/v1/transcripts/{transcript.id}/consultation-split-batches/{batch_id}/{operation}"
    )
    assert admin_denied.status_code == 403
    assert admin_denied.json()["error"]["code"] == "forbidden"
    assert str(batch_id) not in admin_denied.text
    assert transcript.title not in admin_denied.text


@pytest.mark.parametrize("operation", ["keep-available-notes", "retry-missing-notes"])
def test_partial_recovery_routes_are_csrf_protected_and_anonymous_safe(
    raw_client, make_user, monkeypatch, operation,
):
    owner = make_user(email=f"split-recovery-csrf-{operation}@example.com", mfa_required=False, mfa_enabled=False)
    path = f"/api/v1/transcripts/{uuid4()}/consultation-split-batches/{uuid4()}/{operation}"
    monkeypatch.setattr(
        "app.routes.api_routes.keep_available_split_notes",
        lambda *_args, **_kwargs: pytest.fail("CSRF/authentication must run before Keep"),
    )
    monkeypatch.setattr(
        "app.routes.api_routes.retry_missing_split_notes",
        lambda *_args, **_kwargs: pytest.fail("CSRF/authentication must run before retry"),
    )

    assert raw_client.post(path).status_code == 401
    assert raw_client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200
    assert raw_client.post(path).status_code == 403
    assert raw_client.post(
        path,
        headers={"Origin": "http://testserver", "X-CSRF-Token": "invalid"},
    ).status_code == 403


@pytest.mark.parametrize("operation", ["keep-available-notes", "retry-missing-notes"])
@pytest.mark.parametrize("gate", ["deployment", "preference"])
def test_partial_recovery_routes_apply_effective_gate_before_root_lookup(
    client, db_session, make_user, monkeypatch, operation, gate,
):
    owner = make_user(email=f"split-route-gate-{operation}-{gate}@example.com", mfa_required=False, mfa_enabled=False)
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "false" if gate == "deployment" else "true")
    if gate == "preference":
        db_session.add(UserAppPreference(
            user_id=owner.id, preferences_json={"split_consultations_into_separate_notes": False},
        ))
        db_session.commit()
    counts = (
        db_session.scalar(select(func.count()).select_from(ProviderAttempt)),
        db_session.scalar(select(func.count()).select_from(TaskDispatchOutbox)),
    )
    transcript_id, batch_id = uuid4(), uuid4()
    assert client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200
    response = client.post(
        f"/api/v1/transcripts/{transcript_id}/consultation-split-batches/{batch_id}/{operation}"
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "consultation_split_disabled"
    assert str(transcript_id) not in response.text and str(batch_id) not in response.text
    assert counts == (
        db_session.scalar(select(func.count()).select_from(ProviderAttempt)),
        db_session.scalar(select(func.count()).select_from(TaskDispatchOutbox)),
    )


@pytest.mark.parametrize("operation", ["keep-available-notes", "retry-missing-notes"])
def test_partial_recovery_routes_return_safe_owner_success_and_replay_responses(
    client, make_user, monkeypatch, operation,
):
    owner = make_user(email=f"split-recovery-route-{operation}@example.com", mfa_required=False, mfa_enabled=False)
    transcript_id, batch_id, first_id = uuid4(), uuid4(), uuid4()
    calls = []

    if operation == "keep-available-notes":
        def keep(*_args, **_kwargs):
            calls.append(True)
            return [SimpleNamespace(id=first_id)]

        monkeypatch.setattr("app.routes.api_routes.keep_available_split_notes", keep)
        expected_statuses = [200, 200]
        expected_payloads = [
            {"batch_id": str(batch_id), "status": "completed_partial", "document_ids": [str(first_id)]},
            {"batch_id": str(batch_id), "status": "completed_partial", "document_ids": [str(first_id)]},
        ]
    else:
        def retry(*_args, **_kwargs):
            calls.append(True)
            return QueuedSplitRecovery(execution_id=first_id, replayed=len(calls) > 1)

        monkeypatch.setattr("app.routes.api_routes.retry_missing_split_notes", retry)
        expected_statuses = [202, 200]
        expected_payloads = [
            {"batch_id": str(batch_id), "execution_id": str(first_id), "idempotency_replayed": False},
            {"batch_id": str(batch_id), "execution_id": str(first_id), "idempotency_replayed": True},
        ]

    assert client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200
    path = f"/api/v1/transcripts/{transcript_id}/consultation-split-batches/{batch_id}/{operation}"
    responses = [client.post(path), client.post(path)]
    forbidden = {"content", "provider", "quota", "recoverable", "source", "output", "title", "prompt"}
    for response, expected_status, expected_payload in zip(responses, expected_statuses, expected_payloads, strict=True):
        assert response.status_code == expected_status
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["pragma"] == "no-cache"
        assert response.headers["expires"] == "0"
        assert response.json() == expected_payload
        assert not (forbidden & set(response.json()))
    assert len(calls) == 2


@pytest.mark.parametrize("operation", ["keep-available-notes", "retry-missing-notes"])
def test_partial_recovery_routes_return_active_recovery_conflicts(client, make_user, monkeypatch, operation):
    owner = make_user(email=f"split-recovery-conflict-{operation}@example.com", mfa_required=False, mfa_enabled=False)
    path = f"/api/v1/transcripts/{uuid4()}/consultation-split-batches/{uuid4()}/{operation}"

    def active_recovery(*_args, **_kwargs):
        raise AppError(409, "consultation_split_batch_unavailable", "Split batch is unavailable")

    monkeypatch.setattr(
        f"app.routes.api_routes.{'keep_available_split_notes' if operation == 'keep-available-notes' else 'retry_missing_split_notes'}",
        active_recovery,
    )
    assert client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200
    response = client.post(path)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "consultation_split_batch_unavailable"


def test_partial_recovery_transport_limit_applies_only_to_retry(client, make_user, monkeypatch):
    owner = make_user(email="split-recovery-transport-limit@example.com", mfa_required=False, mfa_enabled=False)
    transcript_id, batch_id = uuid4(), uuid4()
    monkeypatch.setattr(
        "app.routes.api_routes.retry_missing_split_notes",
        lambda *_args, **_kwargs: QueuedSplitRecovery(execution_id=uuid4(), replayed=False),
    )
    monkeypatch.setattr(
        "app.routes.api_routes.keep_available_split_notes",
        lambda *_args, **_kwargs: [SimpleNamespace(id=uuid4())],
    )
    assert client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200

    retry_path = f"/api/v1/transcripts/{transcript_id}/consultation-split-batches/{batch_id}/retry-missing-notes"
    retry_statuses = [client.post(retry_path).status_code for _ in range(21)]
    assert retry_statuses[:20] == [202] * 20
    assert retry_statuses[20] == 429

    keep_path = f"/api/v1/transcripts/{transcript_id}/consultation-split-batches/{batch_id}/keep-available-notes"
    assert [client.post(keep_path).status_code for _ in range(21)] == [200] * 21


def test_workspace_rest_and_sse_safe_split_projection_excludes_encrypted_source_fields(
    client, db_session, make_user, monkeypatch,
):
    """Owner workspace status transports only identifiers, flags, and safe lifecycle metadata."""
    owner = make_user(email="split-workspace-safe-owner@example.com", mfa_required=False, mfa_enabled=False)
    _transcript(db_session, owner)
    _enable_split(db_session, owner, monkeypatch)
    from app.schemas.consultation_split import ConsultationSplitBatchDetail

    expected = ConsultationSplitBatchDetail(
        batch_id=uuid4(), status="partially_ready", failed_topic_count=1, validated_topic_count=1,
        primary_failed=True, can_retry_missing=True, can_keep_available=True,
        active_execution_id=None, preferred_document_id=None,
    )
    monkeypatch.setattr("app.web.transcribe_workspace.read_workspace_split_batch", lambda *_args, **_kwargs: expected)
    assert client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200
    rest = client.get("/api/v1/transcribe/workspace")
    assert rest.status_code == 200
    split = rest.json()["consultation_split_batch"]
    assert split == expected.model_dump(mode="json")
    assert not ({"source_snapshot", "confirmed_plan", "provider_snapshot", "output", "title"} & set(split))

    from app.schemas import TranscribeWorkspaceDetail
    shared_payload = TranscribeWorkspaceDetail.model_validate(rest.json())
    monkeypatch.setattr("app.routes.api_routes.resolve_transcribe_workspace_detail", lambda *_args, **_kwargs: shared_payload)
    stream = client.get("/api/v1/transcribe/workspace/stream?once=true")
    assert stream.status_code == 200
    event = json.loads(stream.text.split("data: ", 1)[1].splitlines()[0])
    assert event["consultation_split_batch"] == split
    assert not ({"source_snapshot", "confirmed_plan", "provider_snapshot", "output"} & set(event["consultation_split_batch"]))


def test_intent_start_route_creates_and_replays_only_one_durable_chain(
    client,
    db_session,
    make_user,
    make_template,
    make_llm_config,
    make_llm_selection,
    make_user_app_preference,
    monkeypatch,
):
    """The API delegates one atomic call; replay remains before a later gate."""
    owner = make_user(email="split-intent-route@example.com", mfa_required=False, mfa_enabled=False)
    transcript = _intent_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    first_template = make_template(owner=owner, actor=owner, name="First intent template")
    second_template = make_template(owner=owner, actor=owner, name="Changed replay template")
    key = uuid4()
    assert client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200

    created = client.post(
        f"/api/v1/transcripts/{transcript.id}/consultation-split-intents",
        json={"client_idempotency_key": str(key), "selected_template_id": str(first_template.id)},
    )

    assert created.status_code == 202
    assert created.headers["cache-control"] == "no-store"
    assert set(created.json()) == {"intent_id", "idempotency_replayed", "analysis"}
    assert created.json()["intent_id"]
    assert created.json()["idempotency_replayed"] is False
    assert created.json()["analysis"]["status"] == "queued"
    assert db_session.query(ConsultationSplitIntent).count() == 1
    assert db_session.query(ConsultationSplitAnalysis).count() == 1
    assert db_session.query(ConsultationSplitExecution).count() == 1
    assert db_session.query(ProviderAttempt).count() == 1
    assert db_session.query(TaskDispatchOutbox).count() == 1

    # FastAPI validates the complete request before it can reach service replay.
    malformed_known_key = client.post(
        f"/api/v1/transcripts/{transcript.id}/consultation-split-intents",
        json={"client_idempotency_key": str(key), "selected_template_id": "not-a-uuid"},
    )
    extra_known_key = client.post(
        f"/api/v1/transcripts/{transcript.id}/consultation-split-intents",
        json={
            "client_idempotency_key": str(key),
            "selected_template_id": str(second_template.id),
            "unexpected": "rejected",
        },
    )
    assert malformed_known_key.status_code == 422
    assert extra_known_key.status_code == 422
    assert db_session.query(ConsultationSplitIntent).count() == 1
    assert db_session.query(ConsultationSplitAnalysis).count() == 1
    assert db_session.query(ConsultationSplitExecution).count() == 1
    assert db_session.query(ProviderAttempt).count() == 1
    assert db_session.query(TaskDispatchOutbox).count() == 1

    # A retry deliberately ignores both changed request fields and later opt-out.
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "false")
    replay = client.post(
        f"/api/v1/transcripts/{uuid4()}/consultation-split-intents",
        json={"client_idempotency_key": str(key), "selected_template_id": str(second_template.id)},
    )

    assert replay.status_code == 202
    assert replay.json()["intent_id"] == created.json()["intent_id"]
    assert replay.json()["idempotency_replayed"] is True
    assert db_session.query(ConsultationSplitIntent).count() == 1
    assert db_session.query(ConsultationSplitAnalysis).count() == 1
    assert db_session.query(ConsultationSplitExecution).count() == 1
    assert db_session.query(ProviderAttempt).count() == 1
    assert db_session.query(TaskDispatchOutbox).count() == 1


    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    different_key = client.post(
        f"/api/v1/transcripts/{transcript.id}/consultation-split-intents",
        json={"client_idempotency_key": str(uuid4()), "selected_template_id": str(second_template.id)},
    )
    assert different_key.status_code == 202
    assert different_key.json()["idempotency_replayed"] is False
    assert different_key.json()["intent_id"] != created.json()["intent_id"]
    assert db_session.query(ConsultationSplitIntent).count() == 2
    assert db_session.query(ConsultationSplitAnalysis).count() == 1
    assert db_session.query(ConsultationSplitExecution).count() == 1
    assert db_session.query(ProviderAttempt).count() == 1
    assert db_session.query(TaskDispatchOutbox).count() == 1


def test_continue_as_one_note_route_consumes_replays_and_never_recreates_deleted_child(
    client,
    db_session,
    make_user,
    make_template,
    make_llm_config,
    make_llm_selection,
    make_user_app_preference,
    monkeypatch,
):
    owner = make_user(email="split-continue-route@example.com", mfa_required=False, mfa_enabled=False)
    transcript = _intent_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    template = make_template(owner=owner, actor=owner)
    assert client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200
    started = client.post(
        f"/api/v1/transcripts/{transcript.id}/consultation-split-intents",
        json={"client_idempotency_key": str(uuid4()), "selected_template_id": str(template.id)},
    )
    assert started.status_code == 202
    path = f"/api/v1/transcripts/{transcript.id}/consultation-split-intents/{started.json()['intent_id']}/continue-as-one-note"

    created = client.post(path)
    assert created.status_code == 202
    assert created.headers["cache-control"] == "no-store"
    assert set(created.json()) == {"intent_id", "idempotency_replayed", "document", "consumed_document_deleted"}
    assert created.json()["idempotency_replayed"] is False
    assert created.json()["document"] is not None
    assert created.json()["consumed_document_deleted"] is False

    replay = client.post(path)
    assert replay.status_code == 200
    assert replay.json()["idempotency_replayed"] is True
    assert replay.json()["document"]["id"] == created.json()["document"]["id"]

    document = db_session.get(GeneratedDocument, created.json()["document"]["id"])
    assert document is not None
    db_session.delete(document)
    db_session.commit()
    deleted = client.post(path)
    assert deleted.status_code == 200
    assert deleted.json() == {
        "intent_id": started.json()["intent_id"],
        "idempotency_replayed": True,
        "document": None,
        "consumed_document_deleted": True,
    }


def test_continue_as_one_note_route_rejects_nested_transcript_mismatch_without_consuming(
    client,
    db_session,
    make_user,
    make_template,
    make_llm_config,
    make_llm_selection,
    make_user_app_preference,
    monkeypatch,
):
    owner = make_user(email="split-continue-path@example.com", mfa_required=False, mfa_enabled=False)
    transcript = _intent_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    other = _transcript(db_session, owner)
    template = make_template(owner=owner, actor=owner)
    assert client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200
    started = client.post(
        f"/api/v1/transcripts/{transcript.id}/consultation-split-intents",
        json={"client_idempotency_key": str(uuid4()), "selected_template_id": str(template.id)},
    )
    mismatch = client.post(
        f"/api/v1/transcripts/{other.id}/consultation-split-intents/{started.json()['intent_id']}/continue-as-one-note"
    )
    assert mismatch.status_code == 404
    intent = db_session.get(ConsultationSplitIntent, started.json()["intent_id"])
    assert intent is not None and intent.generated_document_id is None


@pytest.mark.parametrize(
    ("persisted_status", "analysis_outcome", "expected_status", "expected_http_status"),
    [
        ("processing", "processing", "processing", 202),
        ("ready", "ready", "ready", 200),
        ("not_required", "not_required", "not_required", 200),
        ("failed", "failed", "failed", 200),
        ("stale", "stale_conflict", "stale", 200),
        ("queued", "incomplete", "incomplete", 200),
    ],
)
def test_intent_start_route_maps_bounded_analysis_results_without_leaking_metadata(
    client, db_session, make_user, monkeypatch, persisted_status, analysis_outcome, expected_status, expected_http_status,
):
    owner = make_user(email=f"split-intent-route-projection-{persisted_status}@example.com", mfa_required=False, mfa_enabled=False)
    transcript = _transcript(db_session, owner)
    analysis = _analysis(
        db_session,
        owner,
        transcript,
        status=ConsultationSplitAnalysisStatus(persisted_status),
        proposal={"topics": []},
    )
    calls = []

    def start_once(*args, **kwargs):
        calls.append((args, kwargs))
        return CreateOrReplayConsultationSplitIntentResult(
            intent=None,
            analysis=analysis,
            execution=None,
            analysis_outcome=analysis_outcome,
            created_new_intent=False,
            created_new_analysis_work=False,
        )

    monkeypatch.setattr("app.routes.api_routes.create_or_replay_consultation_split_intent", start_once)
    assert client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200

    response = client.post(
        f"/api/v1/transcripts/{transcript.id}/consultation-split-intents",
        json={"client_idempotency_key": str(uuid4()), "selected_template_id": str(uuid4())},
    )

    assert response.status_code == expected_http_status
    assert len(calls) == 1
    assert response.json()["intent_id"] is None
    assert response.json()["idempotency_replayed"] is False
    assert response.json()["analysis"]["status"] == expected_status
    assert set(response.json()["analysis"]) == {
        "analysis_id",
        "status",
        "error_code",
        "updated_at",
        "completed_at",
        "topics",
    }
    assert not any(value in response.text for value in ("snapshot", "provider", "execution", "fingerprint", "quota", "outbox"))


def test_intent_start_route_enforces_strict_body_and_auth_csrf_scope_and_new_key_gate(
    client, raw_client, db_session, make_user, monkeypatch,
):
    owner = make_user(email="split-intent-route-owner@example.com", mfa_required=False, mfa_enabled=False)
    peer = make_user(email="split-intent-route-peer@example.com", team=owner.team, mfa_required=False, mfa_enabled=False)
    leader = make_user(email="split-intent-route-leader@example.com", team=owner.team, team_role=TeamRole.leader, mfa_required=False, mfa_enabled=False)
    admin = make_user(email="split-intent-route-admin@example.com", is_system_admin=True, mfa_required=False, mfa_enabled=False)
    transcript = _transcript(db_session, owner)
    _enable_split(db_session, peer, monkeypatch)
    _enable_split(db_session, leader, monkeypatch)
    monkeypatch.delenv("CONSULTATION_SPLITTING_ENABLED", raising=False)
    path = f"/api/v1/transcripts/{transcript.id}/consultation-split-intents"
    body = {"client_idempotency_key": str(uuid4()), "selected_template_id": str(uuid4())}

    assert raw_client.post(path, json=body).status_code == 401
    assert raw_client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200
    assert raw_client.post(path, json=body).status_code == 403
    raw_client.post("/api/v1/auth/logout")

    assert client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200
    malformed = client.post(path, json={**body, "unexpected": "rejected"})
    assert malformed.status_code == 422
    assert db_session.query(ConsultationSplitIntent).count() == 0
    disabled = client.post(path, json=body)
    assert disabled.status_code == 403
    assert disabled.json()["error"]["code"] == "consultation_split_disabled"
    assert db_session.query(ConsultationSplitIntent).count() == 0

    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    for actor, expected in ((peer, 404), (leader, 404), (admin, 403)):
        client.post("/api/v1/auth/logout")
        assert client.post("/api/v1/auth/login", json={"email": actor.email, "password": "password-1"}).status_code == 200
        denied = client.post(path, json={"client_idempotency_key": str(uuid4()), "selected_template_id": str(uuid4())})
        assert denied.status_code == expected
        if actor is admin:
            assert denied.json()["error"]["code"] == "forbidden"
    assert db_session.query(ConsultationSplitIntent).count() == 0


def _set_intent_rate_limits_for_http_test(monkeypatch, *, burst: str, daily: str) -> None:
    """Lower only this route's registered limits for a short HTTP test.

    SlowAPI reads its registered ``Limit`` instances when the request wrapper
    runs. ``monkeypatch`` restores the values, and the autouse Redis cleanup
    isolates the counters before and after this test.
    """
    from app.main import limiter
    from app.routes.api_routes import create_consultation_split_intent

    route_key = f"{create_consultation_split_intent.__module__}.{create_consultation_split_intent.__name__}"
    configured = {"llm_generation_burst": burst, "llm_generation_daily": daily}
    for limit in limiter._route_limits[route_key]:
        monkeypatch.setattr(limit, "limit", parse_rate_limit(configured[limit.scope]))


@pytest.mark.parametrize(
    ("scope", "burst_limit", "daily_limit", "expected_limit", "retry_after"),
    [
        ("burst", "1/minute", "100/day", "1 per 1 minute", "60"),
        ("daily", "100/day", "1/day", "1 per 1 day", "86400"),
    ],
)
def test_intent_start_route_rate_limit_scopes_return_controlled_http_429_before_service_work(
    client, db_session, make_user, monkeypatch, scope, burst_limit, daily_limit, expected_limit, retry_after,
):
    """Each LLM scope blocks at the wrapper before the intent service runs."""
    owner = make_user(email=f"split-intent-rate-{scope}@example.com", mfa_required=False, mfa_enabled=False)
    transcript = _transcript(db_session, owner)
    analysis = _analysis(db_session, owner, transcript)
    calls = []
    _set_intent_rate_limits_for_http_test(monkeypatch, burst=burst_limit, daily=daily_limit)

    def start_once(*args, **kwargs):
        calls.append((args, kwargs))
        return CreateOrReplayConsultationSplitIntentResult(
            intent=None,
            analysis=analysis,
            execution=None,
            analysis_outcome="queued",
            created_new_intent=False,
            created_new_analysis_work=False,
        )

    monkeypatch.setattr("app.routes.api_routes.create_or_replay_consultation_split_intent", start_once)
    assert client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200
    path = f"/api/v1/transcripts/{transcript.id}/consultation-split-intents"
    body = {"client_idempotency_key": str(uuid4()), "selected_template_id": str(uuid4())}

    accepted = client.post(path, json=body)
    limited = client.post(path, json={**body, "client_idempotency_key": str(uuid4())})

    assert accepted.status_code == 202
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert limited.json()["error"]["message"] == "Too many requests"
    assert limited.json()["error"]["details"] == {"limit": expected_limit}
    assert limited.headers["Retry-After"] == retry_after
    assert len(calls) == 1


@pytest.mark.real_db_connections
def test_intent_start_route_keeps_outbox_pending_when_immediate_broker_publish_fails(
    client,
    db_session,
    make_user,
    make_template,
    make_llm_config,
    make_llm_selection,
    make_user_app_preference,
    monkeypatch,
):
    owner = make_user(email="split-intent-route-publish@example.com", mfa_required=False, mfa_enabled=False)
    transcript = _intent_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    template = make_template(owner=owner, actor=owner)
    publish_time = utcnow()
    session_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, future=True)
    monkeypatch.setattr("app.services.task_outbox.SessionLocal", session_factory)
    monkeypatch.setattr("app.services.task_outbox.utcnow", lambda: publish_time)
    monkeypatch.setattr(
        "app.tasks.process_consultation_split_execution_task.apply_async",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic broker failure")),
    )
    assert client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"}).status_code == 200

    response = client.post(
        f"/api/v1/transcripts/{transcript.id}/consultation-split-intents",
        json={"client_idempotency_key": str(uuid4()), "selected_template_id": str(template.id)},
    )

    assert response.status_code == 202
    assert response.json()["analysis"]["status"] == "queued"
    db_session.expire_all()
    dispatch = db_session.scalar(select(TaskDispatchOutbox))
    assert dispatch is not None
    assert dispatch.state.value == "pending"
    assert dispatch.attempt_count == 1
    assert dispatch.last_error_code == PUBLISH_ERROR_CODE
    assert dispatch.next_attempt_at == publish_time + timedelta(seconds=10)
