"""Durable server-side continuation tests for accepted split Create intents."""

from contextlib import nullcontext
import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.errors import AppError
from app.models import (
    ConsultationSplitAnalysis,
    ConsultationSplitAnalysisStatus,
    ConsultationSplitBatch,
    ConsultationSplitDraft,
    ConsultationSplitDraftTopic,
    ConsultationSplitExecution,
    ConsultationSplitIntentStatus,
    GeneratedDocument,
    PromptTemplateVersion,
    TaskDispatchOutbox,
    TaskDispatchSourceKind,
    TaskDispatchState,
    Transcript,
    TranscriptWorkingNoteMode,
    UserAppPreference,
    utcnow,
)
from app.schemas.consultation_split import ConsultationSplitDraftConfirmRequest
from app.services.consultation_split_api import (
    read_workspace_split_analysis,
    read_workspace_split_batch,
    read_workspace_split_intent,
)
from app.services.consultation_split_confirmation import confirm_split_draft
from app.services.consultation_split_drafts import read_split_draft
from app.services.consultation_split_gates import (
    analysis_split_enabled,
    batch_split_enabled,
    intent_split_enabled,
)
from app.services.consultation_split_intent_progress import progress_consultation_split_intent
from app.services.consultation_split_intents import create_or_replay_consultation_split_intent
from app.services.consultation_split_runtime import process_consultation_split_analysis_execution
from app.services.content_crypto import encrypt_json_for_owner
from app.services.quota_lifecycle import process_quota_lifecycle
from app.services.transcripts import delete_transcripts, set_freeform_working_note_text

from tests.test_consultation_split_intents import _enabled_source


def _accepted_intent(db, owner, transcript, template, monkeypatch):
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    # Dispatch is asserted as persisted state; these tests never execute a worker.
    monkeypatch.setattr(
        "app.services.consultation_split_intents.try_publish_task_dispatch_safely",
        lambda _task_id: None,
    )
    result = create_or_replay_consultation_split_intent(
        db,
        owner,
        transcript_id=transcript.id,
        client_idempotency_key=uuid4(),
        selected_template_id=template.id,
    )
    assert result.intent is not None and result.analysis is not None
    return result.intent, result.analysis


def _finish_analysis(db, owner, analysis, *, status, topics):
    analysis.proposal_encrypted = encrypt_json_for_owner(
        db,
        owner_user_id=owner.id,
        table="consultation_split_analyses",
        field="proposal_encrypted",
        record_id=analysis.id,
        plaintext={"topics": topics},
    )
    analysis.status = status
    analysis.completed_at = utcnow()
    analysis.error_code = None
    db.commit()


def _two_topics(template_id):
    return [
        {
            "topic_uuid": str(uuid4()),
            "title": "Synthetic primary topic",
            "is_primary": True,
            "disposition": "separate_note",
            "template_id": str(template_id),
        },
        {
            "topic_uuid": str(uuid4()),
            "title": "Synthetic secondary topic",
            "is_primary": False,
            "disposition": "separate_note",
            "template_id": str(template_id),
        },
    ]


def test_progress_waits_for_queued_analysis_without_creating_drafts_or_documents(
    db_session, make_user, make_template, make_llm_config, make_llm_selection,
    make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"intent-progress-wait-{uuid4()}@example.com")
    transcript, _ = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    intent, analysis = _accepted_intent(
        db_session, owner, transcript, make_template(owner=owner, actor=owner), monkeypatch
    )
    assert analysis.status is ConsultationSplitAnalysisStatus.queued

    result = progress_consultation_split_intent(db_session, intent_id=intent.id)

    assert result.outcome == "waiting"
    assert db_session.get(ConsultationSplitAnalysis, analysis.id).status is ConsultationSplitAnalysisStatus.queued
    assert db_session.get(type(intent), intent.id).status is ConsultationSplitIntentStatus.analysis_pending
    assert db_session.scalars(select(ConsultationSplitDraft)).all() == []


def test_failed_intent_publication_is_rearmed_without_replacing_business_state(
    db_session, make_user, make_template, make_llm_config, make_llm_selection,
    make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"intent-rearm-{uuid4()}@example.com")
    transcript, _ = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    intent, _ = _accepted_intent(
        db_session, owner, transcript, make_template(owner=owner, actor=owner), monkeypatch
    )
    dispatch = db_session.scalar(select(TaskDispatchOutbox).where(
        TaskDispatchOutbox.source_kind == TaskDispatchSourceKind.consultation_split_intent,
        TaskDispatchOutbox.source_id == intent.id,
    ))
    task_id = dispatch.task_id
    dispatch.state = TaskDispatchState.failed
    dispatch.failed_at = utcnow()
    dispatch.attempt_count = 10
    db_session.commit()
    process_quota_lifecycle(db_session)
    db_session.refresh(dispatch)
    assert dispatch.task_id == task_id
    assert dispatch.state is TaskDispatchState.pending
    assert dispatch.attempt_count == 0
    assert dispatch.failed_at is None
    assert intent.status is ConsultationSplitIntentStatus.analysis_pending
    assert db_session.scalars(select(GeneratedDocument)).all() == []


def test_workspace_intent_projection_is_read_only_owner_scoped_and_analysis_bound(
    db_session, make_user, make_template, make_llm_config, make_llm_selection,
    make_user_app_preference, monkeypatch,
):
    from app.errors import AppError

    owner = make_user(email=f"intent-projection-{uuid4()}@example.com")
    transcript, _ = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    intent, analysis = _accepted_intent(
        db_session, owner, transcript, make_template(owner=owner, actor=owner), monkeypatch
    )
    intent.status = ConsultationSplitIntentStatus.failed
    intent.error_code = "Synthetic private error detail"
    db_session.commit()
    projected = read_workspace_split_intent(
        db_session, owner, transcript_id=transcript.id, analysis_id=analysis.id,
    )
    assert projected.intent_id == intent.id
    assert projected.analysis_id == analysis.id
    assert projected.error_code == "consultation_split_generation_unavailable"
    assert set(projected.model_dump()) == {
        "intent_id", "analysis_id", "status", "manual_review_requested", "error_code",
    }
    assert read_workspace_split_intent(
        db_session, owner, transcript_id=transcript.id, analysis_id=uuid4(),
    ) is None
    stranger = make_user(team=owner.team, email=f"intent-stranger-{uuid4()}@example.com")
    with pytest.raises(AppError):
        read_workspace_split_intent(
            db_session, stranger, transcript_id=transcript.id, analysis_id=analysis.id,
        )
    assert db_session.scalars(select(GeneratedDocument)).all() == []
    assert db_session.scalars(select(ConsultationSplitDraft)).all() == []


def test_not_required_progress_consumes_one_saved_intent_once_even_on_duplicate_delivery(
    db_session, make_user, make_template, make_llm_config, make_llm_selection,
    make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"intent-progress-one-note-{uuid4()}@example.com")
    transcript, _ = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    template = make_template(owner=owner, actor=owner)
    intent, analysis = _accepted_intent(db_session, owner, transcript, template, monkeypatch)
    _finish_analysis(
        db_session,
        owner,
        analysis,
        status=ConsultationSplitAnalysisStatus.not_required,
        topics=[],
    )

    first = progress_consultation_split_intent(db_session, intent_id=intent.id)
    second = progress_consultation_split_intent(db_session, intent_id=intent.id)

    documents = db_session.scalars(
        select(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript.id)
    ).all()
    refreshed_intent = db_session.get(type(intent), intent.id)
    assert first.outcome == "continued_as_one_note"
    assert second.outcome == "terminal"
    assert len(documents) == 1
    assert refreshed_intent.status is ConsultationSplitIntentStatus.bypassed
    assert refreshed_intent.generated_document_id == documents[0].id
    assert db_session.scalars(
        select(TaskDispatchOutbox).where(
            TaskDispatchOutbox.source_kind == TaskDispatchSourceKind.generated_document,
            TaskDispatchOutbox.source_id == documents[0].id,
        )
    ).one_or_none() is not None


@pytest.mark.parametrize(
    ("analysis_status", "multiple_problems", "topics"),
    [
        (ConsultationSplitAnalysisStatus.ready, False, "ready"),
        (ConsultationSplitAnalysisStatus.not_required, True, "manual"),
    ],
)
def test_review_progress_restores_one_exact_bound_draft_without_generating_documents(
    db_session, make_user, make_template, make_llm_config, make_llm_selection,
    make_user_app_preference, monkeypatch, analysis_status, multiple_problems, topics,
):
    owner = make_user(email=f"intent-progress-review-{uuid4()}@example.com")
    transcript, _ = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    if multiple_problems:
        transcript.multiple_problems = True
        db_session.commit()
    template = make_template(owner=owner, actor=owner)
    intent, analysis = _accepted_intent(db_session, owner, transcript, template, monkeypatch)
    assert intent.manual_review_requested is multiple_problems
    proposal_topics = _two_topics(template.id) if topics == "ready" else []
    _finish_analysis(db_session, owner, analysis, status=analysis_status, topics=proposal_topics)

    first = progress_consultation_split_intent(db_session, intent_id=intent.id)
    second = progress_consultation_split_intent(db_session, intent_id=intent.id)

    drafts = db_session.scalars(
        select(ConsultationSplitDraft).where(ConsultationSplitDraft.analysis_id == analysis.id)
    ).all()
    draft_topics = db_session.scalars(
        select(ConsultationSplitDraftTopic).where(ConsultationSplitDraftTopic.draft_id == drafts[0].id)
    ).all()
    assert first.outcome == second.outcome == "review_ready"
    assert len(drafts) == 1
    assert drafts[0].analysis_id == intent.analysis_id
    assert db_session.get(type(intent), intent.id).status is ConsultationSplitIntentStatus.analysis_pending
    assert len(draft_topics) == len(proposal_topics)
    assert db_session.scalars(select(GeneratedDocument)).all() == []


def test_accepted_automatic_intent_survives_preference_opt_out_through_confirmation(
    db_session, make_user, make_template, make_llm_config, make_llm_selection,
    make_user_app_preference, monkeypatch,
):
    """Opt-out rejects new work but cannot revoke this accepted owner intent."""
    owner = make_user(email=f"intent-progress-opt-out-{uuid4()}@example.com")
    transcript, _ = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    template = make_template(owner=owner, actor=owner)
    intent, analysis = _accepted_intent(db_session, owner, transcript, template, monkeypatch)
    assert intent.manual_review_requested is False
    transcript_id = transcript.id
    template_id = template.id
    intent_id = intent.id
    analysis_id = analysis.id
    execution = db_session.scalar(
        select(ConsultationSplitExecution).where(ConsultationSplitExecution.analysis_id == analysis_id)
    )
    assert execution is not None
    execution_id = execution.id

    preference = db_session.scalar(select(UserAppPreference).where(UserAppPreference.user_id == owner.id))
    assert preference is not None
    preference.preferences_json = {"split_consultations_into_separate_notes": False}
    db_session.commit()

    with pytest.raises(AppError) as new_work:
        create_or_replay_consultation_split_intent(
            db_session,
            owner,
            transcript_id=transcript_id,
            client_idempotency_key=uuid4(),
            selected_template_id=template_id,
        )
    assert new_work.value.code == "consultation_split_disabled"
    db_session.rollback()

    topics = [
        {
            "title": "Synthetic primary topic",
            "is_primary": True,
            "disposition": "separate_note",
            "template_id": None,
        },
        {
            "title": "Synthetic secondary topic",
            "is_primary": False,
            "disposition": "separate_note",
            "template_id": None,
        },
    ]
    monkeypatch.setattr(
        "app.services.consultation_split_pre_submit.resolve_generation_credential",
        lambda _config: "synthetic-token",
    )
    monkeypatch.setattr(
        "app.services.consultation_split_runtime.llm_runtime.invoke_llm",
        lambda **_kwargs: (json.dumps({"topics": topics}), {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}),
    )
    analysis_result = process_consultation_split_analysis_execution(db_session, execution_id=execution_id)
    assert (analysis_result.outcome, analysis_result.error_code) == ("ready", None)
    assert progress_consultation_split_intent(db_session, intent_id=intent_id).outcome == "review_ready"

    workspace = read_workspace_split_analysis(db_session, owner, transcript_id=transcript_id)
    assert workspace is not None and workspace.analysis_id == analysis_id
    assert read_workspace_split_intent(
        db_session, owner, transcript_id=transcript_id, analysis_id=analysis_id,
    ).intent_id == intent_id
    draft = db_session.scalar(select(ConsultationSplitDraft).where(ConsultationSplitDraft.analysis_id == analysis_id))
    assert draft is not None
    visible_draft = read_split_draft(db_session, owner, transcript_id=transcript_id)
    assert visible_draft.draft_id == draft.id
    assert visible_draft.status == "active"
    version = db_session.scalar(select(PromptTemplateVersion).where(PromptTemplateVersion.template_id == template_id))
    assert version is not None
    for topic in db_session.scalars(select(ConsultationSplitDraftTopic).where(ConsultationSplitDraftTopic.draft_id == draft.id)):
        topic.template_id = template_id
        topic.template_version_id = version.id
    db_session.commit()
    db_session.refresh(draft)

    other = make_user(email=f"intent-progress-opt-out-other-{uuid4()}@example.com", team=owner.team)
    assert read_workspace_split_analysis(db_session, other, transcript_id=transcript_id) is None
    with pytest.raises(AppError) as foreign_confirmation:
        confirm_split_draft(
            db_session,
            other,
            transcript_id=transcript_id,
            payload=ConsultationSplitDraftConfirmRequest(intent_id=intent_id, expected_updated_at=draft.updated_at),
        )
    assert foreign_confirmation.value.code == "not_found"

    confirmed = confirm_split_draft(
        db_session,
        owner,
        transcript_id=transcript_id,
        payload=ConsultationSplitDraftConfirmRequest(intent_id=intent_id, expected_updated_at=draft.updated_at),
    )
    batch = db_session.get(ConsultationSplitBatch, confirmed.batch_id)
    assert batch is not None
    assert intent_split_enabled(db_session, owner, intent=db_session.get(type(intent), intent_id)) is True
    assert batch_split_enabled(db_session, owner, batch=batch) is True
    assert read_workspace_split_batch(db_session, owner, transcript_id=transcript_id).batch_id == batch.id

    assert analysis_split_enabled(db_session, other, analysis_id=analysis_id) is False
    assert intent_split_enabled(db_session, other, intent=db_session.get(type(intent), intent_id)) is False
    assert batch_split_enabled(db_session, other, batch=batch) is False

    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "false")
    assert analysis_split_enabled(db_session, owner, analysis_id=analysis_id) is False
    assert intent_split_enabled(db_session, owner, intent=db_session.get(type(intent), intent_id)) is False
    assert batch_split_enabled(db_session, owner, batch=batch) is False


def test_stale_source_fails_intent_without_document_or_generation_dispatch(
    db_session, make_user, make_template, make_llm_config, make_llm_selection,
    make_user_app_preference, monkeypatch,
):
    owner = make_user(email=f"intent-progress-stale-{uuid4()}@example.com")
    transcript, _ = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    template = make_template(owner=owner, actor=owner)
    intent, analysis = _accepted_intent(db_session, owner, transcript, template, monkeypatch)
    set_freeform_working_note_text(db_session, transcript=transcript, plaintext="Changed synthetic source")
    _finish_analysis(
        db_session,
        owner,
        analysis,
        status=ConsultationSplitAnalysisStatus.not_required,
        topics=[],
    )

    result = progress_consultation_split_intent(db_session, intent_id=intent.id)

    refreshed_intent = db_session.get(type(intent), intent.id)
    assert result.outcome == "failed"
    assert result.error_code == refreshed_intent.error_code == "consultation_split_source_stale"
    assert refreshed_intent.status is ConsultationSplitIntentStatus.failed
    assert db_session.scalars(select(GeneratedDocument)).all() == []
    assert db_session.scalars(
        select(TaskDispatchOutbox).where(
            TaskDispatchOutbox.source_kind == TaskDispatchSourceKind.generated_document
        )
    ).all() == []


@pytest.mark.parametrize("remove_root", [False, True], ids=["expired", "deleted"])
def test_expired_or_deleted_root_stops_pending_intent_progress(
    db_session, make_user, make_template, make_llm_config, make_llm_selection,
    make_user_app_preference, monkeypatch, remove_root,
):
    owner = make_user(email=f"intent-progress-root-{uuid4()}@example.com")
    transcript, _ = _enabled_source(
        db_session, owner, make_llm_config, make_llm_selection, make_user_app_preference, monkeypatch
    )
    intent, _analysis = _accepted_intent(
        db_session, owner, transcript, make_template(owner=owner, actor=owner), monkeypatch
    )
    intent_id = intent.id
    if remove_root:
        delete_transcripts(db_session, owner, transcript_ids=[transcript.id])
    else:
        transcript.retention_expires_at = utcnow() - timedelta(seconds=1)
        intent.retention_expires_at = transcript.retention_expires_at
        db_session.commit()

    result = progress_consultation_split_intent(db_session, intent_id=intent_id)

    assert result.outcome == "terminal"
    assert db_session.scalars(select(GeneratedDocument)).all() == []
    assert db_session.scalars(select(ConsultationSplitDraft)).all() == []
    if remove_root:
        assert db_session.scalars(select(TaskDispatchOutbox).where(
            TaskDispatchOutbox.source_kind == TaskDispatchSourceKind.consultation_split_intent,
            TaskDispatchOutbox.source_id == intent_id,
        )).all() == []


def test_intent_worker_retries_unexpected_failures_without_exposing_exception_text(monkeypatch):
    from app import tasks

    class RetrySignal(Exception):
        pass

    class MinimalSession:
        def rollback(self):
            self.rolled_back = True

    session = MinimalSession()
    retry_calls = []

    def retry(**kwargs):
        retry_calls.append(kwargs)
        raise RetrySignal()

    monkeypatch.setattr(tasks, "SessionLocal", lambda: nullcontext(session))
    monkeypatch.setattr(
        tasks,
        "progress_consultation_split_intent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic private failure detail")),
    )
    monkeypatch.setattr(tasks.process_consultation_split_intent_task, "retry", retry)

    with pytest.raises(RetrySignal):
        tasks.process_consultation_split_intent_task.run(intent_id=str(uuid4()))

    assert session.rolled_back is True
    assert len(retry_calls) == 1
    assert retry_calls[0].keys() == {"countdown"}
    assert retry_calls[0]["countdown"] == 2


@pytest.mark.parametrize(("retries", "expected_countdown"), [(0, 2), (6, 60)])
def test_intent_worker_retries_waiting_analysis_with_bounded_backoff(
    monkeypatch, retries, expected_countdown,
):
    from app import tasks

    class RetrySignal(Exception):
        pass

    retry_calls = []

    def retry(**kwargs):
        retry_calls.append(kwargs)
        raise RetrySignal()

    monkeypatch.setattr(tasks, "SessionLocal", lambda: nullcontext(object()))
    monkeypatch.setattr(
        tasks,
        "progress_consultation_split_intent",
        lambda *_args, **_kwargs: SimpleNamespace(outcome="waiting"),
    )
    monkeypatch.setattr(tasks.process_consultation_split_intent_task, "retry", retry)
    tasks.process_consultation_split_intent_task.push_request(retries=retries)
    try:
        with pytest.raises(RetrySignal):
            tasks.process_consultation_split_intent_task.run(intent_id=str(uuid4()))
    finally:
        tasks.process_consultation_split_intent_task.pop_request()

    assert retry_calls == [{"countdown": expected_countdown}]
