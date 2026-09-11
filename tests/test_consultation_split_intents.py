"""Focused persistence tests for the future split-Generate intent service."""

from datetime import timedelta
from threading import Event, Thread
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.errors import AppError
from app.models import (
    ClinicalEntity,
    ClinicalEntityRun,
    ConsultationSplitAnalysis,
    ConsultationSplitAnalysisStatus,
    ConsultationSplitExecution,
    ConsultationSplitIntent,
    ConsultationSplitIntentStatus,
    GeneratedDocument,
    PromptTemplate,
    PromptTemplateVersion,
    RedactionRunStatus,
    ProviderAttempt,
    TaskDispatchOutbox,
    TemplateMode,
    Transcript,
    TranscriptIngestionMode,
    TranscriptStatus,
    TranscriptWorkingNoteMode,
    User,
    UserAppPreference,
    utcnow,
)
from app.services.consultation_split_intents import (
    continue_consultation_split_intent_as_one_note,
    create_or_replay_consultation_split_intent,
)
from app.services import consultation_split_intents as intent_service
from app.services.consultation_split_locks import lock_consultation_split_source_scope
from app.services.consultation_split_sources import prepare_source_bound_consultation_split_analysis
from app.services.consultation_splits import create_split_analysis
from app.services.content_crypto import decrypt_json_for_owner, encrypt_json_for_owner, encrypt_text_for_owner
from app.schemas.preferences import UserAppPreferencesUpsert
from app.schemas.templates import GeneratedDocumentSectionUpdate, GeneratedDocumentUpdateRequest
from app.services.preferences import set_user_app_preferences
from app.services.dictations import update_post_consultation_dictation
from app.services.transcripts import (
    create_manual_pii_entity,
    set_freeform_working_note_text,
    set_transcript_draft_text,
)
from app.services import templates as template_service
from app.services.templates import (
    delete_personal_template,
    process_generated_document,
    update_generated_document_content,
)


def _enabled_source(db, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch):
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    make_user_app_preference(user=owner, preferences_json={
        "split_consultations_into_separate_notes": True,
        "note_generation_length": "long",
        "llm_detail_level": "detailed",
    })
    config = make_llm_config(team=owner.team, actor=owner, available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=owner, allowed_models_json=config.available_models_json)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=owner.team_id,
        title="Synthetic consultation",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db.add(transcript)
    db.flush()
    set_freeform_working_note_text(db, transcript=transcript, plaintext="Synthetic working note")
    transcript.working_note_mode = TranscriptWorkingNoteMode.freeform
    db.commit()
    return transcript, config


def _snapshot(db, owner, intent):
    return decrypt_json_for_owner(
        db,
        owner_user_id=owner.id,
        table="consultation_split_intents",
        field="generation_snapshot_encrypted",
        record_id=intent.id,
        stored_value=intent.generation_snapshot_encrypted,
    )


def _created_one_note_intent(db, owner, transcript, template):
    result = create_or_replay_consultation_split_intent(
        db,
        owner,
        transcript_id=transcript.id,
        client_idempotency_key=uuid4(),
        selected_template_id=template.id,
    )
    assert result.intent is not None
    return result.intent


def _assert_stale_consume_creates_no_generation(db, owner, *, intent, transcript):
    """A stale fallback leaves its durable intent and generation chain untouched."""
    documents_before = db.scalars(
        select(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript.id)
    ).all()
    attempts_before = db.scalars(
        select(ProviderAttempt).where(ProviderAttempt.transcript_id == transcript.id)
    ).all()
    outbox_before = db.scalars(select(TaskDispatchOutbox)).all()

    with pytest.raises(AppError) as raised:
        continue_consultation_split_intent_as_one_note(db, owner, transcript_id=transcript.id, intent_id=intent.id)

    assert raised.value.code == "consultation_split_source_stale"
    assert db.get(ConsultationSplitIntent, intent.id).status is ConsultationSplitIntentStatus.analysis_pending
    assert db.scalars(select(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript.id)).all() == documents_before
    assert db.scalars(select(ProviderAttempt).where(ProviderAttempt.transcript_id == transcript.id)).all() == attempts_before
    assert db.scalars(select(TaskDispatchOutbox)).all() == outbox_before


def test_continue_as_one_note_consumes_saved_snapshot_without_split_gate(
    db_session, make_user, make_team, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"one-note-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    template = make_template(owner=owner, actor=owner, name="Submitted template", prompt_text="submitted prompt")
    intent = _created_one_note_intent(db_session, owner, transcript, template)
    submitted_version_id = _snapshot(db_session, owner, intent)["selected_template"]["template_version_id"]

    # The consume path deliberately does not consult the later split gate.
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "false")
    result = continue_consultation_split_intent_as_one_note(db_session, owner, transcript_id=transcript.id, intent_id=intent.id)

    assert result.created_new_document is True
    assert result.document is not None
    assert result.intent.status is ConsultationSplitIntentStatus.bypassed
    assert str(result.document.template_version_id) == submitted_version_id
    assert result.document.prompt_snapshot_text == "submitted prompt"
    assert result.document.generation_snapshot_json["_openscribe_note_generation_options"] == {
        "note_generation_length": "long",
        "llm_detail_level": "detailed",
    }
    assert db_session.scalar(select(GeneratedDocument).where(GeneratedDocument.id == result.document.id)) is not None

    replay = continue_consultation_split_intent_as_one_note(db_session, owner, transcript_id=transcript.id, intent_id=intent.id)
    assert replay.created_new_document is False
    assert replay.document is not None and replay.document.id == result.document.id
    assert len(db_session.scalars(select(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript.id)).all()) == 1


def test_continue_as_one_note_rejects_changed_sources_but_not_candidate_metadata(
    db_session, make_user, make_team, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"one-note-source-{uuid4()}@example.com")
    transcript, _ = _enabled_source(db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch)
    template = make_template(owner=owner, actor=owner)
    intent = _created_one_note_intent(db_session, owner, transcript, template)
    # Candidate metadata is analysis-only and must not invalidate this fallback.
    make_template(owner=owner, actor=owner, name="Later candidate")
    assert continue_consultation_split_intent_as_one_note(db_session, owner, transcript_id=transcript.id, intent_id=intent.id).document is not None

    second_owner = make_user(email=f"one-note-source-second-{uuid4()}@example.com", team=make_team(name=f"Secondary {uuid4()}"))
    transcript2, _ = _enabled_source(db_session, second_owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch)
    template2 = make_template(owner=second_owner, actor=second_owner)
    intent2 = _created_one_note_intent(db_session, second_owner, transcript2, template2)
    set_freeform_working_note_text(db_session, transcript=transcript2, plaintext="Changed source")
    db_session.commit()
    with pytest.raises(AppError) as raised:
        continue_consultation_split_intent_as_one_note(db_session, second_owner, transcript_id=transcript2.id, intent_id=intent2.id)
    assert raised.value.code == "consultation_split_source_stale"
    assert db_session.get(ConsultationSplitIntent, intent2.id).status is ConsultationSplitIntentStatus.analysis_pending


def test_continue_as_one_note_fails_closed_when_transcript_binding_changes(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"one-note-transcript-stale-{uuid4()}@example.com")
    transcript, _ = _enabled_source(db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch)
    intent = _created_one_note_intent(db_session, owner, transcript, make_template(owner=owner, actor=owner))

    # Introducing a current transcript creates a new version/redaction binding,
    # which cannot be substituted for the analysis selected at Generate time.
    set_transcript_draft_text(db_session, transcript=transcript, plaintext="Later transcript source")
    db_session.commit()

    _assert_stale_consume_creates_no_generation(db_session, owner, intent=intent, transcript=transcript)


def test_continue_as_one_note_fails_closed_when_dictation_changes(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"one-note-dictation-stale-{uuid4()}@example.com")
    transcript, _ = _enabled_source(db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch)
    intent = _created_one_note_intent(db_session, owner, transcript, make_template(owner=owner, actor=owner))

    update_post_consultation_dictation(
        db_session, owner, transcript_id=transcript.id, combined_text="Later dictation source"
    )

    _assert_stale_consume_creates_no_generation(db_session, owner, intent=intent, transcript=transcript)


def test_continue_as_one_note_fails_closed_when_manual_pii_identity_changes(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"one-note-manual-pii-stale-{uuid4()}@example.com")
    transcript, _ = _enabled_source(db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch)
    intent = _created_one_note_intent(db_session, owner, transcript, make_template(owner=owner, actor=owner))

    create_manual_pii_entity(
        db_session, owner, transcript_id=transcript.id, entity_type="PERSON", value="Synthetic protected person"
    )
    db_session.commit()

    _assert_stale_consume_creates_no_generation(db_session, owner, intent=intent, transcript=transcript)


def test_continue_as_one_note_fails_closed_when_successful_clinical_hint_identity_changes(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"one-note-clinical-hint-stale-{uuid4()}@example.com")
    transcript, _ = _enabled_source(db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch)
    set_transcript_draft_text(db_session, transcript=transcript, plaintext="Synthetic transcript source")
    db_session.commit()
    intent = _created_one_note_intent(db_session, owner, transcript, make_template(owner=owner, actor=owner))
    analysis = db_session.get(ConsultationSplitAnalysis, intent.analysis_id)
    assert analysis is not None and analysis.transcript_version_id is not None and analysis.redaction_run_id is not None

    clinical_run = ClinicalEntityRun(
        transcript_id=transcript.id,
        transcript_version_id=analysis.transcript_version_id,
        redaction_run_id=analysis.redaction_run_id,
        owner_user_id=owner.id,
        team_id=owner.team_id,
        status=RedactionRunStatus.succeeded,
        source_text_redacted=True,
        api_provider="native",
        entity_count=1,
    )
    db_session.add(clinical_run)
    db_session.flush()
    entity = ClinicalEntity(
        id=uuid4(),
        clinical_entity_run_id=clinical_run.id,
        entity_order=1,
        entity_type="SYMPTOM",
        value_encrypted="",
        normalized_value_hash="synthetic",
    )
    entity.value_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="clinical_entities",
        field="value_encrypted",
        record_id=entity.id,
        plaintext="cough",
    ) or ""
    db_session.add(entity)
    db_session.commit()

    _assert_stale_consume_creates_no_generation(db_session, owner, intent=intent, transcript=transcript)


def test_continue_as_one_note_requires_live_parent_and_keeps_intent_unconsumed(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"one-note-template-{uuid4()}@example.com")
    transcript, _ = _enabled_source(db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch)
    template = make_template(owner=owner, actor=owner)
    intent = _created_one_note_intent(db_session, owner, transcript, template)
    template.is_active = False
    db_session.commit()
    with pytest.raises(AppError) as raised:
        continue_consultation_split_intent_as_one_note(db_session, owner, transcript_id=transcript.id, intent_id=intent.id)
    assert raised.value.code == "not_found"
    assert db_session.get(ConsultationSplitIntent, intent.id).status is ConsultationSplitIntentStatus.analysis_pending


def test_continue_as_one_note_rolls_back_and_publish_failure_keeps_pending_dispatch(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"one-note-rollback-{uuid4()}@example.com")
    transcript, _ = _enabled_source(db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch)
    template = make_template(owner=owner, actor=owner)
    intent = _created_one_note_intent(db_session, owner, transcript, template)
    monkeypatch.setattr(intent_service, "_flush_generated_document_with_quota", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("quota failure")))
    with pytest.raises(RuntimeError):
        continue_consultation_split_intent_as_one_note(db_session, owner, transcript_id=transcript.id, intent_id=intent.id)
    assert db_session.get(ConsultationSplitIntent, intent.id).status is ConsultationSplitIntentStatus.analysis_pending
    assert db_session.scalars(select(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript.id)).all() == []

    monkeypatch.undo()
    monkeypatch.setattr(intent_service, "try_publish_task_dispatch_safely", lambda *_args, **_kwargs: None)
    result = continue_consultation_split_intent_as_one_note(db_session, owner, transcript_id=transcript.id, intent_id=intent.id)
    dispatch = db_session.scalar(select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id == result.document.id))
    assert dispatch is not None and dispatch.state.value == "pending"


def test_continue_as_one_note_rolls_back_rows_flushed_before_intent_binding(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"one-note-flush-rollback-{uuid4()}@example.com")
    transcript, _ = _enabled_source(db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch)
    intent = _created_one_note_intent(db_session, owner, transcript, make_template(owner=owner, actor=owner))
    attempt_ids_before = set(db_session.scalars(select(ProviderAttempt.id).where(ProviderAttempt.transcript_id == transcript.id)).all())
    dispatch_ids_before = set(db_session.scalars(select(TaskDispatchOutbox.task_id)).all())
    flush = intent_service._flush_generated_document_with_quota

    def flush_then_fail(*args, **kwargs):
        flush(*args, **kwargs)
        raise RuntimeError("post-flush failure")

    monkeypatch.setattr(intent_service, "_flush_generated_document_with_quota", flush_then_fail)
    with pytest.raises(RuntimeError, match="post-flush"):
        continue_consultation_split_intent_as_one_note(db_session, owner, transcript_id=transcript.id, intent_id=intent.id)
    assert db_session.scalars(select(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript.id)).all() == []
    assert set(db_session.scalars(select(ProviderAttempt.id).where(ProviderAttempt.transcript_id == transcript.id)).all()) == attempt_ids_before
    assert set(db_session.scalars(select(TaskDispatchOutbox.task_id)).all()) == dispatch_ids_before
    assert db_session.get(ConsultationSplitIntent, intent.id).status is ConsultationSplitIntentStatus.analysis_pending


@pytest.mark.real_db_connections
def test_continue_as_one_note_concurrent_sessions_create_one_durable_chain(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"one-note-concurrent-{uuid4()}@example.com")
    transcript, _ = _enabled_source(db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch)
    intent = _created_one_note_intent(db_session, owner, transcript, make_template(owner=owner, actor=owner))
    owner_id, intent_id, transcript_id = owner.id, intent.id, transcript.id
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, future=True)
    errors: list[BaseException] = []

    def consume() -> None:
        with factory() as session:
            try:
                actor = session.get(User, owner_id)
                assert actor is not None
                continue_consultation_split_intent_as_one_note(session, actor, transcript_id=transcript_id, intent_id=intent_id)
            except BaseException as exc:
                errors.append(exc)

    threads = [Thread(target=consume), Thread(target=consume)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert not errors and all(not thread.is_alive() for thread in threads)
    document_ids = db_session.scalars(select(GeneratedDocument.id).where(GeneratedDocument.transcript_id == transcript_id)).all()
    assert len(document_ids) == 1
    assert len(db_session.scalars(select(ProviderAttempt).where(ProviderAttempt.generated_document_id == document_ids[0])).all()) == 1
    assert len(db_session.scalars(select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id == document_ids[0])).all()) == 1


@pytest.mark.real_db_connections
def test_continue_reloads_preloaded_generated_document_relationship_after_concurrent_bind_and_delete(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"one-note-relationship-{uuid4()}@example.com")
    transcript, _ = _enabled_source(db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch)
    intent = _created_one_note_intent(db_session, owner, transcript, make_template(owner=owner, actor=owner))
    owner_id, intent_id = owner.id, intent.id
    # Keep a loaded ``None`` in this Session while another transaction binds it.
    preloaded = db_session.get(ConsultationSplitIntent, intent_id)
    assert preloaded is not None and preloaded.generated_document is None
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, future=True)
    with factory() as session:
        actor = session.get(User, owner_id)
        assert actor is not None
        bound = continue_consultation_split_intent_as_one_note(session, actor, transcript_id=transcript.id, intent_id=intent_id).document
        assert bound is not None
        bound_id = bound.id
    replay = continue_consultation_split_intent_as_one_note(db_session, owner, transcript_id=transcript.id, intent_id=intent_id)
    assert replay.document is not None and replay.document.id == bound_id

    # Preload the child too; database SET NULL must win over this stale object.
    assert preloaded.generated_document is not None
    with factory() as session:
        child = session.get(GeneratedDocument, bound_id)
        assert child is not None
        session.delete(child)
        session.commit()
    deleted_replay = continue_consultation_split_intent_as_one_note(db_session, owner, transcript_id=transcript.id, intent_id=intent_id)
    assert deleted_replay.document is None and deleted_replay.created_new_document is False


def test_continue_rejects_unflushed_target_intent_changes_with_autoflush_disabled(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"one-note-dirty-{uuid4()}@example.com")
    transcript, _ = _enabled_source(db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch)
    intent = _created_one_note_intent(db_session, owner, transcript, make_template(owner=owner, actor=owner))
    attempt_ids_before = set(
        db_session.scalars(select(ProviderAttempt.id).where(ProviderAttempt.transcript_id == transcript.id)).all()
    )
    dispatch_ids_before = set(db_session.scalars(select(TaskDispatchOutbox.task_id)).all())
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, future=True)
    with factory() as session:
        actor = session.get(User, owner.id)
        dirty = session.get(ConsultationSplitIntent, intent.id)
        assert actor is not None and dirty is not None
        dirty.status = ConsultationSplitIntentStatus.bypassed
        with pytest.raises(AppError) as raised:
            continue_consultation_split_intent_as_one_note(session, actor, transcript_id=transcript.id, intent_id=intent.id)
        assert raised.value.code == "consultation_split_intent_dirty"
        session.rollback()
    assert db_session.get(ConsultationSplitIntent, intent.id).status is ConsultationSplitIntentStatus.analysis_pending
    assert db_session.scalars(select(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript.id)).all() == []
    assert set(
        db_session.scalars(select(ProviderAttempt.id).where(ProviderAttempt.transcript_id == transcript.id)).all()
    ) == attempt_ids_before
    assert set(db_session.scalars(select(TaskDispatchOutbox.task_id)).all()) == dispatch_ids_before


def test_continue_rejects_unflushed_target_intent_changes_with_default_autoflush(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"one-note-dirty-default-{uuid4()}@example.com")
    transcript, _ = _enabled_source(db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch)
    intent = _created_one_note_intent(db_session, owner, transcript, make_template(owner=owner, actor=owner))
    attempt_ids_before = set(
        db_session.scalars(select(ProviderAttempt.id).where(ProviderAttempt.transcript_id == transcript.id)).all()
    )
    dispatch_ids_before = set(db_session.scalars(select(TaskDispatchOutbox.task_id)).all())
    factory = sessionmaker(bind=db_session.get_bind(), future=True)
    with factory() as session:
        actor = session.get(User, owner.id)
        dirty = session.get(ConsultationSplitIntent, intent.id)
        assert actor is not None and dirty is not None
        dirty.status = ConsultationSplitIntentStatus.bypassed
        with pytest.raises(AppError) as raised:
            continue_consultation_split_intent_as_one_note(session, actor, transcript_id=transcript.id, intent_id=intent.id)
        assert raised.value.code == "consultation_split_intent_dirty"
        session.commit()

    with factory() as verification:
        persisted = verification.get(ConsultationSplitIntent, intent.id)
        assert persisted is not None
        assert persisted.status is ConsultationSplitIntentStatus.analysis_pending
        assert verification.scalars(
            select(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript.id)
        ).all() == []
        assert set(
            verification.scalars(select(ProviderAttempt.id).where(ProviderAttempt.transcript_id == transcript.id)).all()
        ) == attempt_ids_before
        assert set(verification.scalars(select(TaskDispatchOutbox.task_id)).all()) == dispatch_ids_before


def test_continue_as_one_note_deleted_child_stays_consumed_and_does_not_resolve_credentials(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"one-note-child-{uuid4()}@example.com")
    transcript, _ = _enabled_source(db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch)
    template = make_template(owner=owner, actor=owner)
    intent = _created_one_note_intent(db_session, owner, transcript, template)
    never_called = lambda *_args, **_kwargs: pytest.fail("consume reached a credential or provider boundary")
    # Guard both the ordinary-generation seams and the split-analysis adapter
    # seam. Consume may resolve selection metadata, but it must not resolve a
    # secret or contact any provider before the queued worker runs.
    monkeypatch.setattr(template_service, "_resolve_generation_credential", never_called)
    monkeypatch.setattr(template_service, "resolve_generation_credential", never_called)
    monkeypatch.setattr("app.services.llm_credentials.resolve_generation_credential", never_called)
    monkeypatch.setattr(template_service, "_generate_freeform_output_openai", never_called)
    monkeypatch.setattr("app.services.llm_adapters.runtime.invoke_llm", never_called)
    result = continue_consultation_split_intent_as_one_note(db_session, owner, transcript_id=transcript.id, intent_id=intent.id)
    assert result.document is not None
    db_session.delete(result.document)
    db_session.commit()
    replay = continue_consultation_split_intent_as_one_note(db_session, owner, transcript_id=transcript.id, intent_id=intent.id)
    assert replay.document is None and replay.created_new_document is False


def test_continue_as_one_note_uses_submitted_structured_version_after_later_revision(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"one-note-structured-{uuid4()}@example.com")
    transcript, _ = _enabled_source(db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch)
    submitted_config = {"profile": "emis", "sections": [{"section_key": "history", "instruction": "Submitted history", "section_order": 0}]}
    template = make_template(owner=owner, actor=owner, mode=TemplateMode.structured, config_json=submitted_config)
    intent = _created_one_note_intent(db_session, owner, transcript, template)
    db_session.add(PromptTemplateVersion(
        template_id=template.id,
        version_no=2,
        mode=TemplateMode.structured,
        prompt_text="Later prompt",
        config_json={"profile": "emis", "sections": [{"section_key": "tasks", "instruction": "Later tasks", "section_order": 0}]},
        created_by_user_id=owner.id,
    ))
    db_session.commit()
    document = continue_consultation_split_intent_as_one_note(db_session, owner, transcript_id=transcript.id, intent_id=intent.id).document
    assert document is not None and document.prompt_snapshot_text != "Later prompt"
    assert template_service._document_template_config(db_session, document=document).sections[0].section_key == "history"


def test_one_note_worker_uses_immutable_template_and_preference_snapshot(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"one-note-worker-snapshot-{uuid4()}@example.com")
    transcript, _ = _enabled_source(db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch)
    template = make_template(
        owner=owner,
        actor=owner,
        name="Submitted structured template",
        prompt_text="Submitted structured instruction",
        mode=TemplateMode.structured,
        config_json={
            "profile": "emis",
            "sections": [{"section_key": "history", "instruction": "Submitted history", "section_order": 0}],
        },
    )
    intent = _created_one_note_intent(db_session, owner, transcript, template)
    document = continue_consultation_split_intent_as_one_note(db_session, owner, transcript_id=transcript.id, intent_id=intent.id).document
    assert document is not None

    # These are live values that must not retarget queued work.
    db_session.add(PromptTemplateVersion(
        template_id=template.id,
        version_no=2,
        mode=TemplateMode.structured,
        prompt_text="Later structured instruction",
        config_json={
            "profile": "emis",
            "sections": [{"section_key": "tasks", "instruction": "Later tasks", "section_order": 0}],
        },
        created_by_user_id=owner.id,
    ))
    preference = db_session.scalar(select(UserAppPreference).where(UserAppPreference.user_id == owner.id))
    assert preference is not None
    preference.preferences_json = {"split_consultations_into_separate_notes": True, "note_generation_length": "short", "llm_detail_level": "concise"}
    db_session.commit()

    captured_request = {}
    monkeypatch.setattr(template_service, "_resolve_generation_credential", lambda *_args, **_kwargs: "synthetic-token")

    def capture_worker_request(**kwargs):
        captured_request.update(kwargs["request_body"])
        return '{"title":"Synthetic summary","content":{"history":"Synthetic history"}}', {
            "input_tokens": 4, "output_tokens": 3, "total_tokens": 7,
        }

    monkeypatch.setattr(template_service, "_generate_freeform_output_openai", capture_worker_request)
    processed = process_generated_document(db_session, document_id=document.id)

    assert processed.status.value == "ready"
    assert captured_request["max_completion_tokens"] == template_service.NOTE_GENERATION_LENGTH_TOKEN_CAPS["long"]
    request_text = "\n".join(message["content"] for message in captured_request["messages"])
    assert "Submitted structured instruction" in request_text
    assert "Submitted history" in request_text
    assert "Later structured instruction" not in request_text
    assert "Later tasks" not in request_text
    assert template_service.NOTE_GENERATION_DETAIL_GUIDANCE["detailed"] in request_text
    assert processed.structured_section_definitions_json == {
        "profile": "emis",
        "sections": [{"section_key": "history", "section_label": "History", "section_order": 0}],
    }
    assert processed.generation_snapshot_json["submitted_template_config"] == {
        "profile": "emis",
        "sections": [{"section_key": "history", "instruction": "Submitted history", "section_order": 0}],
    }


def test_one_note_structured_sections_keep_submitted_config_after_version_change_and_template_deletion(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    """Queued one-note output keeps its submitted structured contract forever."""
    owner = make_user(email=f"one-note-immutable-sections-{uuid4()}@example.com")
    transcript, _ = _enabled_source(db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch)
    submitted_config = {
        "profile": "emis",
        "sections": [{"section_key": "history", "instruction": "Submitted history", "section_order": 0}],
    }
    template = make_template(
        owner=owner,
        actor=owner,
        mode=TemplateMode.structured,
        config_json=submitted_config,
    )
    intent = _created_one_note_intent(db_session, owner, transcript, template)
    document = continue_consultation_split_intent_as_one_note(
        db_session, owner, transcript_id=transcript.id, intent_id=intent.id
    ).document
    assert document is not None

    submitted_version = db_session.get(PromptTemplateVersion, document.template_version_id)
    assert submitted_version is not None
    # Simulate a legacy/admin-side mutation of the referenced version.  The
    # queued document must not adopt it even before the template is deleted.
    submitted_version.config_json = {
        "profile": "emis",
        "sections": [{"section_key": "tasks", "instruction": "Later tasks", "section_order": 0}],
    }
    db_session.commit()

    monkeypatch.setattr(template_service, "_resolve_generation_credential", lambda *_args, **_kwargs: "synthetic-token")
    monkeypatch.setattr(
        template_service,
        "_generate_freeform_output_openai",
        lambda **_kwargs: ('{"title":"Synthetic","content":{"history":"Submitted history"}}', {"total_tokens": 3}),
    )
    processed = process_generated_document(db_session, document_id=document.id)
    assert [section.section_key for section in processed.sections] == ["history"]

    delete_personal_template(db_session, owner, template_id=template.id)
    db_session.refresh(processed)
    assert processed.template_version_id is None
    assert template_service._document_template_config(db_session, document=processed).sections[0].section_key == "history"

    updated = update_generated_document_content(
        db_session,
        owner,
        generated_document_id=processed.id,
        payload=GeneratedDocumentUpdateRequest(
            expected_updated_at=processed.updated_at,
            sections=[GeneratedDocumentSectionUpdate(section_key="history", section_label="History", section_order=0, text="Edited history")],
        ),
    )
    assert [section.section_key for section in updated.sections] == ["history"]
    with pytest.raises(AppError) as rejected:
        update_generated_document_content(
            db_session,
            owner,
            generated_document_id=updated.id,
            payload=GeneratedDocumentUpdateRequest(
                expected_updated_at=updated.updated_at,
                sections=[GeneratedDocumentSectionUpdate(section_key="tasks", section_label="Tasks", section_order=0, text="Later task")],
            ),
        )
    assert rejected.value.code == "business_rule_violation"


def test_continue_as_one_note_enforces_owner_admin_and_retention_scope(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"one-note-scope-{uuid4()}@example.com")
    transcript, _ = _enabled_source(db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch)
    intent = _created_one_note_intent(db_session, owner, transcript, make_template(owner=owner, actor=owner))
    teammate = make_user(email=f"one-note-teammate-{uuid4()}@example.com", team=owner.team)
    admin = make_user(email=f"one-note-admin-{uuid4()}@example.com", is_system_admin=True)
    with pytest.raises(AppError) as outsider:
        continue_consultation_split_intent_as_one_note(db_session, teammate, transcript_id=transcript.id, intent_id=intent.id)
    assert outsider.value.code == "not_found"
    with pytest.raises(AppError) as privileged:
        continue_consultation_split_intent_as_one_note(db_session, admin, transcript_id=transcript.id, intent_id=intent.id)
    assert privileged.value.code == "forbidden"
    transcript.retention_expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()
    with pytest.raises(AppError) as expired:
        continue_consultation_split_intent_as_one_note(db_session, owner, transcript_id=transcript.id, intent_id=intent.id)
    assert expired.value.code == "not_found"
    assert db_session.get(ConsultationSplitIntent, intent.id).status is ConsultationSplitIntentStatus.analysis_pending


def test_intent_gate_short_circuits_before_template_or_source_work(db_session, make_user, monkeypatch):
    owner = make_user(email=f"intent-owner-{uuid4()}@example.com")
    monkeypatch.setattr(
        "app.services.consultation_split_intents._resolve_available_template_for_user",
        lambda *_args, **_kwargs: pytest.fail("template access ran"),
    )
    monkeypatch.setattr(
        "app.services.consultation_split_intents.prepare_source_bound_consultation_split_analysis",
        lambda *_args, **_kwargs: pytest.fail("source preparation ran"),
    )

    with pytest.raises(AppError) as raised:
        create_or_replay_consultation_split_intent(
            db_session,
            owner,
            transcript_id=uuid4(),
            client_idempotency_key=uuid4(),
            selected_template_id=uuid4(),
        )

    assert raised.value.code == "consultation_split_disabled"
    assert db_session.scalar(select(ConsultationSplitIntent)) is None


@pytest.mark.real_db_connections
def test_committed_preference_opt_out_during_preparation_blocks_atomic_intent_start(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    """The final gate sees an opt-out committed while redaction has no source lock."""
    owner = make_user(email=f"intent-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    template = make_template(owner=owner, actor=owner)
    owner_id, transcript_id, template_id = owner.id, transcript.id, template.id
    session_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, future=True)
    redaction_entered, release_redaction, opt_out_done = Event(), Event(), Event()
    intent_errors: list[BaseException] = []
    opt_out_errors: list[BaseException] = []

    def blocking_redaction(_db, text, **_kwargs):
        redaction_entered.set()
        assert release_redaction.wait(5)
        return {
            "redacted_text": text,
            "phi_mapping": {},
            "phi_index": [],
            "phi_count": 0,
            "api_provider": "synthetic",
            "api_model_or_version": None,
        }

    monkeypatch.setattr(
        "app.services.consultation_split_sources.redact_transient_text",
        blocking_redaction,
    )
    monkeypatch.setattr(
        "app.services.consultation_split_intents._queue_or_reuse_prepared_split_analysis",
        lambda *_args, **_kwargs: pytest.fail("queue work ran after opt-out"),
    )

    def start_intent() -> None:
        with session_factory() as session:
            try:
                actor = session.get(User, owner_id)
                assert actor is not None
                create_or_replay_consultation_split_intent(
                    session,
                    actor,
                    transcript_id=transcript_id,
                    client_idempotency_key=uuid4(),
                    selected_template_id=template_id,
                )
            except BaseException as exc:
                intent_errors.append(exc)

    def opt_out() -> None:
        assert redaction_entered.wait(5)
        with session_factory() as session:
            try:
                actor = session.get(User, owner_id)
                assert actor is not None
                set_user_app_preferences(
                    session,
                    actor,
                    UserAppPreferencesUpsert(split_consultations_into_separate_notes=False),
                )
            except BaseException as exc:
                opt_out_errors.append(exc)
            finally:
                opt_out_done.set()

    intent_thread = Thread(target=start_intent)
    opt_out_thread = Thread(target=opt_out)
    intent_thread.start()
    assert redaction_entered.wait(5)
    opt_out_thread.start()
    assert opt_out_done.wait(5), "preparation must not hold the owner lock during redaction"
    release_redaction.set()
    intent_thread.join(10)
    opt_out_thread.join(10)

    assert not intent_thread.is_alive() and not opt_out_thread.is_alive()
    assert opt_out_errors == []
    assert len(intent_errors) == 1
    assert isinstance(intent_errors[0], AppError)
    assert intent_errors[0].code == "consultation_split_disabled"
    assert db_session.scalars(select(ConsultationSplitIntent)).all() == []
    assert db_session.scalars(select(ConsultationSplitAnalysis)).all() == []
    assert db_session.scalars(select(ConsultationSplitExecution)).all() == []
    assert db_session.scalars(select(ProviderAttempt)).all() == []
    assert db_session.scalars(select(TaskDispatchOutbox)).all() == []


@pytest.mark.real_db_connections
def test_preference_opt_out_waits_after_final_source_lock_until_intent_commit(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    """An opt-out cannot commit in the final proof-to-commit window."""
    owner = make_user(email=f"intent-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    template = make_template(owner=owner, actor=owner)
    owner_id, transcript_id, template_id = owner.id, transcript.id, template.id
    session_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, future=True)
    final_source_locked, release_preparation, writer_attempted, writer_done, intent_committed = (
        Event(), Event(), Event(), Event(), Event()
    )
    intent_errors: list[BaseException] = []
    writer_errors: list[BaseException] = []
    results = []
    original_prepare = intent_service.prepare_source_bound_consultation_split_analysis

    def hold_after_final_source_lock(*args, **kwargs):
        prepared = original_prepare(*args, **kwargs)
        final_source_locked.set()
        assert release_preparation.wait(5)
        return prepared

    from app.services import preferences as preference_service

    original_preference_lock = preference_service._lock_user_app_preference_owner

    def observe_writer_lock(*args, **kwargs):
        writer_attempted.set()
        return original_preference_lock(*args, **kwargs)

    monkeypatch.setattr(
        "app.services.consultation_split_intents.prepare_source_bound_consultation_split_analysis",
        hold_after_final_source_lock,
    )
    monkeypatch.setattr("app.services.preferences._lock_user_app_preference_owner", observe_writer_lock)
    monkeypatch.setattr("app.services.consultation_split_intents.try_publish_task_dispatch_safely", lambda *_args: intent_committed.set())

    def start_intent() -> None:
        with session_factory() as session:
            try:
                actor = session.get(User, owner_id)
                assert actor is not None
                results.append(create_or_replay_consultation_split_intent(
                    session,
                    actor,
                    transcript_id=transcript_id,
                    client_idempotency_key=uuid4(),
                    selected_template_id=template_id,
                ))
            except BaseException as exc:
                intent_errors.append(exc)

    def opt_out() -> None:
        assert final_source_locked.wait(5)
        with session_factory() as session:
            try:
                actor = session.get(User, owner_id)
                assert actor is not None
                set_user_app_preferences(
                    session,
                    actor,
                    UserAppPreferencesUpsert(split_consultations_into_separate_notes=False),
                )
            except BaseException as exc:
                writer_errors.append(exc)
            finally:
                writer_done.set()

    intent_thread = Thread(target=start_intent)
    writer_thread = Thread(target=opt_out)
    intent_thread.start()
    assert final_source_locked.wait(5)
    writer_thread.start()
    assert writer_attempted.wait(5)
    assert not writer_done.wait(0.25), "preference writer bypassed the final source lock"
    release_preparation.set()
    intent_thread.join(10)
    writer_thread.join(10)

    assert not intent_thread.is_alive() and not writer_thread.is_alive()
    assert intent_errors == [] and writer_errors == []
    assert len(results) == 1 and results[0].created_new_intent is True
    assert intent_committed.is_set()
    db_session.expire_all()
    preference = db_session.scalar(select(UserAppPreference).where(UserAppPreference.user_id == owner_id))
    assert preference is not None and preference.preferences_json == {}
    assert len(db_session.scalars(select(ConsultationSplitIntent)).all()) == 1


def test_intent_creates_one_atomic_analysis_execution_outbox_and_encrypted_snapshot(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"intent-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    template = make_template(
        owner=owner,
        actor=owner,
        name="Private synthetic template",
        description="Private synthetic description",
        prompt_text="Private synthetic prompt",
    )
    published = []
    monkeypatch.setattr("app.services.consultation_split_intents.try_publish_task_dispatch_safely", published.append)
    monkeypatch.setattr(
        "app.services.llm.read_active_team_llm_bearer_token",
        lambda *_args, **_kwargs: pytest.fail("credential resolution ran"),
    )
    monkeypatch.setattr(
        "app.services.llm_adapters.runtime.invoke_llm",
        lambda *_args, **_kwargs: pytest.fail("provider invocation ran"),
    )

    result = create_or_replay_consultation_split_intent(
        db_session,
        owner,
        transcript_id=transcript.id,
        client_idempotency_key=uuid4(),
        selected_template_id=template.id,
    )

    assert result.created_new_intent is True
    assert result.created_new_analysis_work is True
    assert result.analysis_outcome == "queued"
    assert result.intent is not None and result.analysis is not None and result.execution is not None
    assert result.intent.analysis_id == result.analysis.id
    assert result.intent.retention_expires_at == transcript.retention_expires_at
    assert "Private synthetic template" not in result.intent.generation_snapshot_encrypted
    assert "Private synthetic prompt" not in result.intent.generation_snapshot_encrypted
    snapshot = _snapshot(db_session, owner, result.intent)
    version = db_session.scalar(select(PromptTemplateVersion).where(PromptTemplateVersion.template_id == template.id))
    assert snapshot == {
        "selected_template": {
            "template_id": str(template.id),
            "template_version_id": str(version.id),
            "template_version_no": version.version_no,
            "name": "Private synthetic template",
            "description": "Private synthetic description",
            "mode": "freeform",
            "prompt_text": "Private synthetic prompt",
            "config": None,
            "structured_sections": None,
        },
        "generation_configuration": {
            "note_generation_length": "long",
            "llm_detail_level": "detailed",
        },
    }
    assert "_openscribe_wait_for_transcript" not in snapshot
    assert len(db_session.scalars(select(ConsultationSplitAnalysis)).all()) == 1
    assert len(db_session.scalars(select(ConsultationSplitExecution)).all()) == 1
    assert len(db_session.scalars(select(ProviderAttempt)).all()) == 1
    dispatch = db_session.scalar(select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id == result.execution.id))
    assert dispatch is not None and published == [dispatch.task_id]


@pytest.mark.parametrize("status", [
    ConsultationSplitAnalysisStatus.ready,
    ConsultationSplitAnalysisStatus.not_required,
    ConsultationSplitAnalysisStatus.failed,
])
def test_intent_binds_reusable_terminal_analysis_without_new_provider_work(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch, status,
):
    owner = make_user(email=f"intent-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    template = make_template(owner=owner, actor=owner)
    prepared = prepare_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)
    analysis = create_split_analysis(
        db_session,
        owner,
        transcript_id=transcript.id,
        source_fingerprint=prepared.source_state.source_fingerprint,
        transcript_version_id=prepared.source_state.transcript_version_id,
        redaction_run_id=prepared.source_state.redaction_run_id,
        source_snapshot=prepared.source_snapshot,
        candidate_template_snapshot=prepared.candidate_template_snapshot,
        proposal={"topics": []} if status is not ConsultationSplitAnalysisStatus.failed else None,
    )
    analysis.status = status
    if status is not ConsultationSplitAnalysisStatus.failed:
        # Use the persistence constructor's correctly encrypted proposal.
        assert analysis.proposal_encrypted
    db_session.commit()

    result = create_or_replay_consultation_split_intent(
        db_session,
        owner,
        transcript_id=transcript.id,
        client_idempotency_key=uuid4(),
        selected_template_id=template.id,
    )

    assert result.intent is not None and result.intent.analysis_id == analysis.id
    assert result.analysis_outcome == status.value
    assert result.created_new_analysis_work is False
    assert db_session.scalars(select(ConsultationSplitExecution)).all() == []
    assert db_session.scalars(select(ProviderAttempt)).all() == []
    assert db_session.scalars(select(TaskDispatchOutbox)).all() == []


def test_intent_rejects_incomplete_analysis_without_persisting_request(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"intent-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    template = make_template(owner=owner, actor=owner)
    prepared = prepare_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)
    analysis = create_split_analysis(
        db_session,
        owner,
        transcript_id=transcript.id,
        source_fingerprint=prepared.source_state.source_fingerprint,
        transcript_version_id=prepared.source_state.transcript_version_id,
        redaction_run_id=prepared.source_state.redaction_run_id,
        source_snapshot=prepared.source_snapshot,
        candidate_template_snapshot=prepared.candidate_template_snapshot,
    )
    db_session.commit()

    result = create_or_replay_consultation_split_intent(
        db_session,
        owner,
        transcript_id=transcript.id,
        client_idempotency_key=uuid4(),
        selected_template_id=template.id,
    )

    assert result.intent is None
    assert result.analysis is not None and result.analysis.id == analysis.id
    assert result.analysis_outcome == "incomplete"
    assert db_session.scalars(select(ConsultationSplitIntent)).all() == []


def test_intent_replay_ignores_changed_payload_and_later_disabled_preference(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"intent-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    first_template = make_template(owner=owner, actor=owner, name="First")
    second_template = make_template(owner=owner, actor=owner, name="Second")
    key = uuid4()
    first = create_or_replay_consultation_split_intent(
        db_session, owner, transcript_id=transcript.id, client_idempotency_key=key, selected_template_id=first_template.id
    )
    assert first.intent is not None
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "false")
    monkeypatch.setattr(
        "app.services.consultation_split_intents.prepare_source_bound_consultation_split_analysis",
        lambda *_args, **_kwargs: pytest.fail("replay touched new source"),
    )

    replay = create_or_replay_consultation_split_intent(
        db_session, owner, transcript_id=uuid4(), client_idempotency_key=key, selected_template_id=second_template.id
    )

    assert replay.intent is not None and replay.intent.id == first.intent.id
    assert replay.analysis is not None and replay.analysis.id == first.analysis.id
    assert replay.created_new_intent is False
    assert _snapshot(db_session, owner, replay.intent)["selected_template"]["template_id"] == str(first_template.id)
    assert len(db_session.scalars(select(ConsultationSplitIntent)).all()) == 1


def test_intent_replay_keeps_idempotency_record_when_analysis_is_deleted(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"intent-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    template = make_template(owner=owner, actor=owner)
    key = uuid4()
    first = create_or_replay_consultation_split_intent(
        db_session, owner, transcript_id=transcript.id, client_idempotency_key=key, selected_template_id=template.id
    )
    assert first.intent is not None and first.analysis is not None
    db_session.delete(first.analysis)
    db_session.commit()
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "false")

    replay = create_or_replay_consultation_split_intent(
        db_session, owner, transcript_id=uuid4(), client_idempotency_key=key, selected_template_id=uuid4()
    )

    assert replay.intent is not None and replay.intent.id == first.intent.id
    assert replay.intent.analysis_id is None
    assert replay.analysis is None
    assert replay.analysis_outcome == "analysis_missing"


def test_intent_replay_rejects_expired_transcript_before_returning_analysis(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"intent-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    template = make_template(owner=owner, actor=owner)
    key = uuid4()
    first = create_or_replay_consultation_split_intent(
        db_session, owner, transcript_id=transcript.id, client_idempotency_key=key, selected_template_id=template.id
    )
    assert first.analysis is not None
    transcript.retention_expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()
    monkeypatch.setattr(
        "app.services.consultation_split_intents.prepare_source_bound_consultation_split_analysis",
        lambda *_args, **_kwargs: pytest.fail("expired replay prepared a source"),
    )

    with pytest.raises(AppError) as raised:
        create_or_replay_consultation_split_intent(
            db_session, owner, transcript_id=transcript.id, client_idempotency_key=key, selected_template_id=template.id
        )

    assert raised.value.code == "not_found"


def test_intent_replay_rejects_linked_analysis_outside_its_transcript_scope(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"intent-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    template = make_template(owner=owner, actor=owner)
    key = uuid4()
    first = create_or_replay_consultation_split_intent(
        db_session, owner, transcript_id=transcript.id, client_idempotency_key=key, selected_template_id=template.id
    )
    assert first.analysis is not None
    other_transcript = Transcript(
        owner_user_id=owner.id,
        team_id=owner.team_id,
        title="Synthetic unrelated consultation",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=transcript.retention_expires_at,
    )
    db_session.add(other_transcript)
    db_session.flush()
    first.analysis.transcript_id = other_transcript.id
    db_session.commit()

    with pytest.raises(AppError) as raised:
        create_or_replay_consultation_split_intent(
            db_session, owner, transcript_id=transcript.id, client_idempotency_key=key, selected_template_id=template.id
        )

    assert raised.value.code == "consultation_split_scope_invalid"


@pytest.mark.real_db_connections
def test_concurrent_same_key_calls_create_one_intent_and_one_analysis_dispatch_chain(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    """Source locking and the intent key leave concurrent callers with one winner."""
    owner = make_user(email=f"intent-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    template = make_template(owner=owner, actor=owner)
    monkeypatch.setattr("app.services.consultation_split_intents.try_publish_task_dispatch_safely", lambda *_args: None)
    # This test exercises the intent/database race.  Keep the already-tested
    # dynamic-redaction implementation out of the concurrent setup path: its
    # native-provider bootstrap has its own fixed-row initialization race.
    monkeypatch.setattr(
        "app.services.consultation_split_sources.redact_transient_text",
        lambda _db, text, **_kwargs: {
            "redacted_text": text,
            "phi_mapping": {},
            "phi_index": [],
            "phi_count": 0,
            "api_provider": "synthetic",
            "api_model_or_version": None,
        },
    )

    owner_id, transcript_id, template_id, key = owner.id, transcript.id, template.id, uuid4()
    session_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, future=True)
    start = Event()
    results: list[tuple[object, object, bool, bool]] = []
    errors: list[BaseException] = []

    def create_from_separate_session() -> None:
        assert start.wait(5)
        with session_factory() as session:
            try:
                actor = session.get(User, owner_id)
                assert actor is not None
                result = create_or_replay_consultation_split_intent(
                    session,
                    actor,
                    transcript_id=transcript_id,
                    client_idempotency_key=key,
                    selected_template_id=template_id,
                )
                assert result.intent is not None and result.analysis is not None
                results.append((result.intent.id, result.analysis.id, result.created_new_intent, result.created_new_analysis_work))
            except BaseException as exc:
                errors.append(exc)

    first = Thread(target=create_from_separate_session)
    second = Thread(target=create_from_separate_session)
    first.start()
    second.start()
    start.set()
    first.join(10)
    second.join(10)

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert len(results) == 2
    assert {result[0] for result in results} == {results[0][0]}
    assert {result[1] for result in results} == {results[0][1]}
    assert sorted(result[2] for result in results) == [False, True]
    assert sorted(result[3] for result in results) == [False, True]
    db_session.expire_all()
    assert len(db_session.scalars(select(ConsultationSplitIntent)).all()) == 1
    assert len(db_session.scalars(select(ConsultationSplitAnalysis)).all()) == 1
    assert len(db_session.scalars(select(ConsultationSplitExecution)).all()) == 1
    assert len(db_session.scalars(select(ProviderAttempt)).all()) == 1
    assert len(db_session.scalars(select(TaskDispatchOutbox)).all()) == 1


@pytest.mark.real_db_connections
def test_replay_waits_for_worker_execution_before_analysis_lock_chain(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    """Replay takes owner/root then execution -> analysis, matching pre-submit."""
    owner = make_user(email=f"intent-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    template = make_template(owner=owner, actor=owner)
    monkeypatch.setattr("app.services.consultation_split_intents.try_publish_task_dispatch_safely", lambda *_args: None)
    first = create_or_replay_consultation_split_intent(
        db_session,
        owner,
        transcript_id=transcript.id,
        client_idempotency_key=uuid4(),
        selected_template_id=template.id,
    )
    assert first.intent is not None and first.execution is not None and first.analysis is not None

    owner_id, transcript_id, key, execution_id = owner.id, transcript.id, first.intent.client_idempotency_key, first.execution.id
    session_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, future=True)
    worker_has_execution, release_worker, replay_attempted, reuse_entered = Event(), Event(), Event(), Event()
    errors: list[BaseException] = []
    original_reuse = intent_service._reuse_result

    def observe_reuse(*args, **kwargs):
        reuse_entered.set()
        return original_reuse(*args, **kwargs)

    monkeypatch.setattr("app.services.consultation_split_intents._reuse_result", observe_reuse)

    def worker_style_lock_chain() -> None:
        with session_factory() as session:
            try:
                scope = lock_consultation_split_source_scope(
                    session,
                    owner_user_id=owner_id,
                    transcript_id=transcript_id,
                )
                assert scope is not None
                assert session.scalar(
                    select(ConsultationSplitExecution)
                    .where(ConsultationSplitExecution.id == execution_id)
                    .with_for_update()
                ) is not None
                worker_has_execution.set()
                assert release_worker.wait(5)
                assert session.scalar(
                    select(ConsultationSplitAnalysis)
                    .where(ConsultationSplitAnalysis.id == first.analysis.id)
                    .with_for_update()
                ) is not None
            except BaseException as exc:
                errors.append(exc)
            finally:
                session.rollback()

    def replay() -> None:
        assert worker_has_execution.wait(5)
        with session_factory() as session:
            try:
                actor = session.get(User, owner_id)
                assert actor is not None
                replay_attempted.set()
                result = create_or_replay_consultation_split_intent(
                    session,
                    actor,
                    transcript_id=transcript_id,
                    client_idempotency_key=key,
                    selected_template_id=template.id,
                )
                assert result.intent is not None and result.intent.id == first.intent.id
            except BaseException as exc:
                errors.append(exc)

    worker = Thread(target=worker_style_lock_chain)
    replay_thread = Thread(target=replay)
    worker.start()
    assert worker_has_execution.wait(5)
    replay_thread.start()
    assert replay_attempted.wait(5)
    assert not reuse_entered.wait(0.25), "replay reached analysis before the source/worker lock chain released"
    release_worker.set()
    worker.join(10)
    replay_thread.join(10)

    assert not worker.is_alive() and not replay_thread.is_alive()
    assert errors == []
    assert reuse_entered.is_set()


@pytest.mark.real_db_connections
def test_concurrent_different_keys_create_two_intents_and_share_one_analysis_dispatch_chain(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    """Two deliberate Generate actions share one current source analysis run."""
    owner = make_user(email=f"intent-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    template = make_template(owner=owner, actor=owner)
    monkeypatch.setattr("app.services.consultation_split_intents.try_publish_task_dispatch_safely", lambda *_args: None)
    monkeypatch.setattr(
        "app.services.consultation_split_sources.redact_transient_text",
        lambda _db, text, **_kwargs: {
            "redacted_text": text,
            "phi_mapping": {},
            "phi_index": [],
            "phi_count": 0,
            "api_provider": "synthetic",
            "api_model_or_version": None,
        },
    )

    owner_id, transcript_id, template_id = owner.id, transcript.id, template.id
    session_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, future=True)
    start = Event()
    results: list[tuple[object, object, bool, bool]] = []
    errors: list[BaseException] = []

    def create_from_separate_session(key) -> None:
        assert start.wait(5)
        with session_factory() as session:
            try:
                actor = session.get(User, owner_id)
                assert actor is not None
                result = create_or_replay_consultation_split_intent(
                    session,
                    actor,
                    transcript_id=transcript_id,
                    client_idempotency_key=key,
                    selected_template_id=template_id,
                )
                assert result.intent is not None and result.analysis is not None
                results.append((result.intent.id, result.analysis.id, result.created_new_intent, result.created_new_analysis_work))
            except BaseException as exc:
                errors.append(exc)

    first = Thread(target=create_from_separate_session, args=(uuid4(),))
    second = Thread(target=create_from_separate_session, args=(uuid4(),))
    first.start()
    second.start()
    start.set()
    first.join(10)
    second.join(10)

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert len(results) == 2
    assert len({result[0] for result in results}) == 2
    assert {result[1] for result in results} == {results[0][1]}
    assert [result[2] for result in results] == [True, True]
    assert sorted(result[3] for result in results) == [False, True]
    db_session.expire_all()
    assert len(db_session.scalars(select(ConsultationSplitIntent)).all()) == 2
    assert len(db_session.scalars(select(ConsultationSplitAnalysis)).all()) == 1
    assert len(db_session.scalars(select(ConsultationSplitExecution)).all()) == 1
    assert len(db_session.scalars(select(ProviderAttempt)).all()) == 1
    assert len(db_session.scalars(select(TaskDispatchOutbox)).all()) == 1


def test_intent_rolls_back_provisional_request_and_new_work_on_queue_failure(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"intent-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    template = make_template(owner=owner, actor=owner)
    monkeypatch.setattr(
        "app.services.consultation_split_queue.queue_split_execution",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AppError(429, "quota_exceeded", "Quota exhausted")),
    )

    with pytest.raises(AppError) as raised:
        create_or_replay_consultation_split_intent(
            db_session, owner, transcript_id=transcript.id, client_idempotency_key=uuid4(), selected_template_id=template.id
        )

    assert raised.value.code == "quota_exceeded"
    assert db_session.scalars(select(ConsultationSplitIntent)).all() == []
    assert db_session.scalars(select(ConsultationSplitAnalysis)).all() == []
    assert db_session.scalars(select(ConsultationSplitExecution)).all() == []
    assert db_session.scalars(select(ProviderAttempt)).all() == []
    assert db_session.scalars(select(TaskDispatchOutbox)).all() == []


def test_multiple_intent_keys_share_one_analysis_work_and_failed_publish_leaves_outbox_pending(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"intent-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    template = make_template(owner=owner, actor=owner)
    monkeypatch.setattr("app.services.consultation_split_intents.try_publish_task_dispatch_safely", lambda *_args: None)

    first = create_or_replay_consultation_split_intent(
        db_session, owner, transcript_id=transcript.id, client_idempotency_key=uuid4(), selected_template_id=template.id
    )
    second = create_or_replay_consultation_split_intent(
        db_session, owner, transcript_id=transcript.id, client_idempotency_key=uuid4(), selected_template_id=template.id
    )

    assert first.intent is not None and second.intent is not None
    assert first.intent.analysis_id == second.intent.analysis_id
    assert first.created_new_analysis_work is True
    assert second.created_new_analysis_work is False
    assert len(db_session.scalars(select(ConsultationSplitIntent)).all()) == 2
    assert len(db_session.scalars(select(ConsultationSplitAnalysis)).all()) == 1
    assert len(db_session.scalars(select(ConsultationSplitExecution)).all()) == 1
    assert len(db_session.scalars(select(ProviderAttempt)).all()) == 1
    dispatch = db_session.scalar(select(TaskDispatchOutbox))
    assert dispatch is not None and dispatch.state.value == "pending"


def test_intent_template_must_be_active_accessibly_versioned(db_session, make_user, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch):
    owner = make_user(email=f"intent-owner-{uuid4()}@example.com")
    transcript, _config = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    inactive = PromptTemplate(
        owner_user_id=owner.id,
        scope="user",
        name="Inactive",
        is_active=False,
        created_by_user_id=owner.id,
    )
    db_session.add(inactive)
    db_session.commit()

    with pytest.raises(AppError) as raised:
        create_or_replay_consultation_split_intent(
            db_session, owner, transcript_id=transcript.id, client_idempotency_key=uuid4(), selected_template_id=inactive.id
        )

    assert raised.value.code == "not_found"
    assert db_session.scalars(select(ConsultationSplitIntent)).all() == []
