"""End-to-end persistence regressions for clinician-requested split review."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.errors import AppError
from app.models import (
    ConsultationSplitAnalysis,
    ConsultationSplitAnalysisStatus,
    ConsultationSplitBatch,
    ConsultationSplitIntent,
    ConsultationSplitIntentStatus,
    ProviderAttempt,
    TaskDispatchOutbox,
    Transcript,
    TranscriptIngestionMode,
    TranscriptStatus,
    TranscriptWorkingNoteMode,
    utcnow,
)
from app.schemas.consultation_split import (
    ConsultationSplitDraftConfirmRequest,
    ConsultationSplitDraftReplace,
    ConsultationSplitDraftTopicReplace,
)
from app.services.consultation_split_confirmation import confirm_split_draft
from app.services.consultation_split_drafts import (
    initialize_or_reuse_split_draft,
    replace_split_draft,
)
from app.services.consultation_split_intents import create_or_replay_consultation_split_intent
from app.services.consultation_split_sources import prepare_source_bound_consultation_split_analysis
from app.services.consultation_splits import create_split_analysis, read_split_batch_json
from app.services.transcripts import set_freeform_working_note_text, update_transcript


def _marked_manual_source(db, owner, make_llm_config, make_llm_selection, monkeypatch):
    """Create an owner source with automatic splitting deliberately disabled."""
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    config = make_llm_config(
        team=owner.team,
        actor=owner,
        available_models_json=["gpt-4o-mini"],
    )
    make_llm_selection(
        config=config,
        actor=owner,
        allowed_models_json=config.available_models_json,
    )
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=owner.team_id,
        title="Synthetic marked consultation",
        multiple_problems=True,
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db.add(transcript)
    db.flush()
    set_freeform_working_note_text(
        db,
        transcript=transcript,
        plaintext="Synthetic clinician working note",
    )
    transcript.working_note_mode = TranscriptWorkingNoteMode.freeform
    db.commit()
    return transcript


def _prepared_not_required_analysis(db, owner, transcript, *, topic_count):
    """Persist a truthful current no-split result from actual prepared sources."""
    prepared = prepare_source_bound_consultation_split_analysis(
        db,
        owner,
        transcript_id=transcript.id,
    )
    analysis = create_split_analysis(
        db,
        owner,
        transcript_id=transcript.id,
        source_fingerprint=prepared.source_state.source_fingerprint,
        transcript_version_id=prepared.source_state.transcript_version_id,
        redaction_run_id=prepared.source_state.redaction_run_id,
        source_snapshot=prepared.source_snapshot,
        candidate_template_snapshot=prepared.candidate_template_snapshot,
        proposal={
            "topics": [] if topic_count == 0 else [{
                "topic_uuid": str(uuid4()),
                "title": "Synthetic detected topic",
                "is_primary": True,
                "disposition": "separate_note",
                "template_id": None,
            }],
        },
    )
    analysis.status = ConsultationSplitAnalysisStatus.not_required
    db.commit()
    return analysis


def _unmark(db, owner, transcript):
    return update_transcript(
        db,
        owner,
        transcript_id=transcript.id,
        title=None,
        ingestion_mode=None,
        structured_context_json=None,
        multiple_problems=False,
    )


@pytest.mark.parametrize("topic_count", [0, 1], ids=["zero-detected", "one-detected"])
def test_marked_not_required_analysis_can_be_repaired_and_confirmed_after_unmark(
    db_session,
    make_user,
    make_template,
    make_llm_config,
    make_llm_selection,
    monkeypatch,
    topic_count,
):
    owner = make_user(email=f"manual-review-workflow-{uuid4()}@example.com")
    transcript = _marked_manual_source(
        db_session, owner, make_llm_config, make_llm_selection, monkeypatch
    )
    template = make_template(
        owner=owner,
        actor=owner,
        name="Synthetic selected template",
        prompt_text="Synthetic selected prompt",
    )
    analysis = _prepared_not_required_analysis(
        db_session, owner, transcript, topic_count=topic_count
    )

    started = create_or_replay_consultation_split_intent(
        db_session,
        owner,
        transcript_id=transcript.id,
        client_idempotency_key=uuid4(),
        selected_template_id=template.id,
    )
    assert started.intent is not None
    assert started.intent.analysis_id == analysis.id
    assert started.intent.manual_review_requested is True
    assert started.analysis_outcome == "not_required"
    assert started.created_new_analysis_work is False

    _unmark(db_session, owner, transcript)
    initialized = initialize_or_reuse_split_draft(
        db_session,
        owner,
        transcript_id=transcript.id,
    )
    assert initialized.analysis_id == analysis.id
    assert len(initialized.topics) == topic_count
    existing_primary_uuid = initialized.topics[0].topic_uuid if initialized.topics else None

    saved = replace_split_draft(
        db_session,
        owner,
        transcript_id=transcript.id,
        payload=ConsultationSplitDraftReplace(
            expected_updated_at=initialized.updated_at,
            topics=[
                ConsultationSplitDraftTopicReplace(
                    topic_uuid=existing_primary_uuid,
                    title="Synthetic clinician topic one",
                    is_primary=True,
                    disposition="separate_note",
                    template_id=template.id,
                ),
                ConsultationSplitDraftTopicReplace(
                    title="Synthetic clinician topic two",
                    is_primary=False,
                    disposition="separate_note",
                    template_id=template.id,
                ),
            ],
        ),
    )
    before_confirmation = {
        "attempts": len(db_session.scalars(select(ProviderAttempt)).all()),
        "outbox": len(db_session.scalars(select(TaskDispatchOutbox)).all()),
    }
    confirmed = confirm_split_draft(
        db_session,
        owner,
        transcript_id=transcript.id,
        payload=ConsultationSplitDraftConfirmRequest(
            intent_id=started.intent.id,
            expected_updated_at=saved.updated_at,
        ),
    )

    batch = db_session.get(ConsultationSplitBatch, confirmed.batch_id)
    assert confirmed.idempotency_replayed is False
    assert confirmed.separate_note_count == 2
    assert batch is not None and batch.intent_id == started.intent.id
    assert db_session.get(ConsultationSplitIntent, started.intent.id).status is ConsultationSplitIntentStatus.confirmed
    assert [topic["title"] for topic in read_split_batch_json(
        db_session, owner, batch=batch, field="confirmed_plan_encrypted"
    )["topics"]] == ["Synthetic clinician topic one", "Synthetic clinician topic two"]
    assert len(db_session.scalars(select(ProviderAttempt)).all()) == before_confirmation["attempts"] + 1
    assert len(db_session.scalars(select(TaskDispatchOutbox)).all()) == before_confirmation["outbox"] + 1


def test_marked_create_replays_same_manual_intent_after_unmark_without_new_work(
    db_session,
    make_user,
    make_template,
    make_llm_config,
    make_llm_selection,
    monkeypatch,
):
    owner = make_user(email=f"manual-replay-workflow-{uuid4()}@example.com")
    transcript = _marked_manual_source(
        db_session, owner, make_llm_config, make_llm_selection, monkeypatch
    )
    template = make_template(owner=owner, actor=owner)
    analysis = _prepared_not_required_analysis(db_session, owner, transcript, topic_count=0)
    key = uuid4()

    first = create_or_replay_consultation_split_intent(
        db_session,
        owner,
        transcript_id=transcript.id,
        client_idempotency_key=key,
        selected_template_id=template.id,
    )
    assert first.intent is not None
    assert first.intent.analysis_id == analysis.id
    assert first.intent.manual_review_requested is True
    assert first.created_new_intent is True
    assert first.created_new_analysis_work is False

    counts = {
        "analyses": len(db_session.scalars(select(ConsultationSplitAnalysis)).all()),
        "attempts": len(db_session.scalars(select(ProviderAttempt)).all()),
        "outbox": len(db_session.scalars(select(TaskDispatchOutbox)).all()),
    }
    _unmark(db_session, owner, transcript)

    replay = create_or_replay_consultation_split_intent(
        db_session,
        owner,
        transcript_id=transcript.id,
        client_idempotency_key=key,
        selected_template_id=template.id,
    )
    assert replay.intent is not None and replay.intent.id == first.intent.id
    assert replay.intent.manual_review_requested is True
    assert replay.created_new_intent is False
    assert replay.created_new_analysis_work is False
    assert {
        "analyses": len(db_session.scalars(select(ConsultationSplitAnalysis)).all()),
        "attempts": len(db_session.scalars(select(ProviderAttempt)).all()),
        "outbox": len(db_session.scalars(select(TaskDispatchOutbox)).all()),
    } == counts

    with pytest.raises(AppError) as denied:
        create_or_replay_consultation_split_intent(
            db_session,
            owner,
            transcript_id=transcript.id,
            client_idempotency_key=uuid4(),
            selected_template_id=template.id,
        )
    assert denied.value.code == "consultation_split_disabled"
    assert {
        "analyses": len(db_session.scalars(select(ConsultationSplitAnalysis)).all()),
        "attempts": len(db_session.scalars(select(ProviderAttempt)).all()),
        "outbox": len(db_session.scalars(select(TaskDispatchOutbox)).all()),
    } == counts
