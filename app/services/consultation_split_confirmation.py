"""Atomic, owner-only confirmation of a reviewed consultation split.

Confirmation freezes the redacted sources and template selections, then queues
the single generation execution in the same transaction.  It never invokes a
provider or resolves credentials.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    ConsultationSplitAnalysis,
    ConsultationSplitAnalysisStatus,
    ConsultationSplitBatch,
    ConsultationSplitDraft,
    ConsultationSplitDraftStatus,
    ConsultationSplitDraftTopic,
    ConsultationSplitIntent,
    ConsultationSplitIntentStatus,
    ConsultationSplitTopicDisposition,
    PromptTemplate,
    PromptTemplateVersion,
    TeamRole,
    TemplateScope,
    User,
)
from app.schemas.consultation_split import (
    ConsultationSplitDraftConfirmRequest,
    ConsultationSplitDraftConfirmResponse,
)
from app.services.consultation_split_intents import _submitted_generation_snapshot
from app.services.consultation_split_generation_queue import queue_confirmed_split_generation
from app.services.consultation_split_locks import lock_consultation_split_source_scope
from app.services.consultation_split_sources import current_consultation_split_analysis_source_matches
from app.services.consultation_splits import (
    create_split_batch,
    create_split_batch_topic,
    create_split_topic_outcome,
    read_split_analysis_json,
    read_split_topic_title,
)
from app.services.preferences import consultation_splitting_enabled, consultation_splitting_feature_enabled
from app.services.templates import (
    _note_generation_options_for_user,
    _structured_section_definitions_snapshot,
    _template_version_config,
)
from app.services.transcripts import snapshot_current_transcript_version, transcript_is_expired


_TABLE_ANALYSIS = "consultation_split_analyses"


@dataclass(frozen=True, slots=True)
class _TemplateSnapshot:
    template_id: UUID
    version_id: UUID
    value: dict[str, object]


def _require_owner(db: Session, actor: User) -> None:
    if not consultation_splitting_feature_enabled() or not consultation_splitting_enabled(db, actor):
        raise AppError(403, "consultation_split_disabled", "Consultation splitting is not enabled")
    if (
        actor.is_system_admin
        or actor.team_id is None
        or actor.team_role not in {TeamRole.user, TeamRole.leader}
    ):
        raise AppError(403, "forbidden", "Consultation split content is restricted to the owning user")


def _not_found(transcript_id: UUID) -> AppError:
    return AppError(404, "not_found", "Transcript not found", {"resource": "transcript", "transcript_id": str(transcript_id)})


def _response(batch: ConsultationSplitBatch, *, replayed: bool) -> ConsultationSplitDraftConfirmResponse:
    separate_count = sum(topic.disposition is ConsultationSplitTopicDisposition.separate_note for topic in batch.topics)
    return ConsultationSplitDraftConfirmResponse(
        batch_id=batch.id,
        status=batch.status.value,
        separate_note_count=separate_count,
        topic_count=len(batch.topics),
        created_at=batch.created_at,
        updated_at=batch.updated_at,
        idempotency_replayed=replayed,
    )


def _locked_intent(db: Session, *, actor: User, transcript_id: UUID, intent_id: UUID) -> ConsultationSplitIntent:
    intent = db.scalar(select(ConsultationSplitIntent).where(ConsultationSplitIntent.id == intent_id).with_for_update())
    if (
        intent is None
        or intent.owner_user_id != actor.id
        or intent.team_id != actor.team_id
        or intent.transcript_id != transcript_id
    ):
        raise AppError(404, "not_found", "Consultation split intent not found", {"resource": "consultation_split_intent"})
    return intent


def _locked_template_snapshots(
    db: Session,
    actor: User,
    *,
    requested: dict[UUID, UUID],
) -> dict[UUID, _TemplateSnapshot]:
    """Lock exact selected versions in UUID order, then freeze plaintext only for encryption."""
    result: dict[UUID, _TemplateSnapshot] = {}
    for template_id in sorted(requested, key=str):
        template = db.scalar(
            select(PromptTemplate)
            .where(
                PromptTemplate.id == template_id,
                PromptTemplate.is_active.is_(True),
                ((PromptTemplate.scope == TemplateScope.user) & (PromptTemplate.owner_user_id == actor.id))
                | ((PromptTemplate.scope == TemplateScope.team) & (PromptTemplate.team_id == actor.team_id)),
            )
            .with_for_update()
        )
        if template is None:
            raise AppError(409, "consultation_split_template_unavailable", "A selected template is no longer available")
        version = db.scalar(
            select(PromptTemplateVersion)
            .where(
                PromptTemplateVersion.id == requested[template_id],
                PromptTemplateVersion.template_id == template.id,
            )
            .with_for_update()
        )
        if version is None:
            raise AppError(409, "consultation_split_template_unavailable", "A selected template version is no longer available")
        config = _template_version_config(version)
        result[template_id] = _TemplateSnapshot(
            template_id=template.id,
            version_id=version.id,
            value={
                "template_id": str(template.id),
                "template_version_id": str(version.id),
                "template_version_no": version.version_no,
                "name": template.name,
                "description": template.description,
                "mode": version.mode.value,
                "prompt_text": version.prompt_text,
                "config": deepcopy(version.config_json),
                "structured_sections": _structured_section_definitions_snapshot(config),
            },
        )
    return result


def confirm_split_draft(
    db: Session,
    actor: User,
    *,
    transcript_id: UUID,
    payload: ConsultationSplitDraftConfirmRequest,
) -> ConsultationSplitDraftConfirmResponse:
    """Persist one immutable batch or return its same-intent replay.

    The lock sequence is owner -> transcript -> dictations -> intent -> analysis
    -> draft parent -> templates/versions -> draft topics (each collection is
    ordered by stable UUID/order).
    No caller body is consulted after a matching batch exists.
    """
    _require_owner(db, actor)
    scope = lock_consultation_split_source_scope(db, owner_user_id=actor.id, transcript_id=transcript_id)
    if scope is None or scope.owner.team_id != actor.team_id or scope.owner.is_system_admin or transcript_is_expired(scope.transcript):
        raise _not_found(transcript_id)
    owner, transcript = scope.owner, scope.transcript
    intent = _locked_intent(db, actor=owner, transcript_id=transcript.id, intent_id=payload.intent_id)
    if intent.retention_expires_at != transcript.retention_expires_at:
        raise AppError(500, "consultation_split_scope_invalid", "Consultation split content is unavailable")

    # Idempotency is intentionally before timestamp, source, and payload checks.
    existing = db.scalar(
        select(ConsultationSplitBatch)
        .where(ConsultationSplitBatch.intent_id == intent.id)
        .with_for_update()
    )
    if existing is not None:
        if existing.owner_user_id != owner.id or existing.transcript_id != transcript.id:
            raise AppError(500, "consultation_split_scope_invalid", "Consultation split content is unavailable")
        return _response(existing, replayed=True)

    if intent.status is ConsultationSplitIntentStatus.bypassed:
        raise AppError(409, "consultation_split_intent_consumed", "This split request was continued as one note")
    if intent.status is not ConsultationSplitIntentStatus.analysis_pending:
        raise AppError(409, "consultation_split_intent_unavailable", "This split request is unavailable")
    if intent.analysis_id is None:
        raise AppError(409, "consultation_split_analysis_unavailable", "A ready split analysis is required")
    analysis = db.scalar(select(ConsultationSplitAnalysis).where(ConsultationSplitAnalysis.id == intent.analysis_id).with_for_update())
    if (
        analysis is None
        or analysis.owner_user_id != owner.id
        or analysis.team_id != owner.team_id
        or analysis.transcript_id != transcript.id
        or analysis.retention_expires_at != transcript.retention_expires_at
        or analysis.status is not ConsultationSplitAnalysisStatus.ready
    ):
        raise AppError(409, "consultation_split_analysis_unavailable", "A ready split analysis is required")
    # The draft parent serializes replacement.  Read its template references
    # while it is held, then take template/version locks before child-topic
    # locks. This is the global template-delete/replace order.
    draft = db.scalar(select(ConsultationSplitDraft).where(ConsultationSplitDraft.analysis_id == analysis.id).with_for_update())
    if draft is None:
        raise AppError(404, "not_found", "Consultation split draft not found", {"resource": "consultation_split_draft"})
    topic_references = db.execute(
        select(
            ConsultationSplitDraftTopic.id,
            ConsultationSplitDraftTopic.template_id,
            ConsultationSplitDraftTopic.template_version_id,
        )
        .where(ConsultationSplitDraftTopic.draft_id == draft.id)
        .order_by(ConsultationSplitDraftTopic.topic_order, ConsultationSplitDraftTopic.id)
    ).all()
    if draft.status is not ConsultationSplitDraftStatus.active:
        raise AppError(409, "consultation_split_draft_unavailable", "Consultation split draft is unavailable")
    if payload.expected_updated_at != draft.updated_at:
        raise AppError(409, "consultation_split_draft_conflict", "Consultation split draft changed in another tab")
    if not current_consultation_split_analysis_source_matches(db, owner, transcript=transcript, analysis=analysis):
        raise AppError(409, "consultation_split_source_stale", "Consultation sources changed before confirmation")

    # Confirm the browser-owned intent's exact template version as well as every
    # selected topic version; do not silently replace either with a newer one.
    submitted = _submitted_generation_snapshot(db, intent=intent)
    requested: dict[UUID, UUID] = {submitted["template_id"]: submitted["version_id"]}
    for _topic_id, template_id, template_version_id in topic_references:
        if template_id is None or template_version_id is None:
            continue
        previous = requested.setdefault(template_id, template_version_id)
        if previous != template_version_id:
            raise AppError(409, "consultation_split_template_unavailable", "A selected template has inconsistent versions")
    template_rows = _locked_template_snapshots(db, owner, requested=requested)

    # Only now lock the children. A draft replacement cannot run after the
    # parent lock, and deletion shares template -> version -> topic ordering.
    topics = db.scalars(
        select(ConsultationSplitDraftTopic)
        .where(ConsultationSplitDraftTopic.draft_id == draft.id)
        .order_by(ConsultationSplitDraftTopic.topic_order, ConsultationSplitDraftTopic.id)
        .with_for_update()
    ).all()

    separate = [topic for topic in topics if topic.disposition is ConsultationSplitTopicDisposition.separate_note]
    if not 2 <= len(separate) <= 6 or len(topics) > 6:
        raise AppError(422, "validation_error", "Confirming a split requires two to six separate-note topics")
    if sum(topic.is_primary for topic in separate) != 1:
        raise AppError(422, "validation_error", "Exactly one separate-note topic must be primary")
    if len({topic.topic_uuid for topic in topics}) != len(topics):
        raise AppError(409, "consultation_split_draft_invalid", "Consultation split draft is unavailable")

    for topic in topics:
        if topic.template_id is None or topic.template_version_id is None:
            if topic.disposition is ConsultationSplitTopicDisposition.separate_note:
                raise AppError(409, "consultation_split_template_unavailable", "A separate-note topic needs an available template")
            continue

    source_snapshot = read_split_analysis_json(db, owner, analysis=analysis, field="source_snapshot_encrypted")
    if not isinstance(source_snapshot, dict):
        raise AppError(409, "consultation_split_source_stale", "Consultation sources changed before confirmation")
    source_state = source_snapshot.get("source_state")
    sources = source_snapshot.get("sources")
    if not isinstance(source_state, dict) or not isinstance(sources, dict):
        raise AppError(409, "consultation_split_source_stale", "Consultation sources changed before confirmation")
    confirmed_topics: list[dict[str, object]] = []
    for topic in topics:
        title = read_split_topic_title(db, owner, topic=topic)
        template = template_rows.get(topic.template_id) if topic.template_id else None
        confirmed_topics.append({
            "topic_uuid": str(topic.topic_uuid), "title": title,
            "order": topic.topic_order, "is_primary": topic.is_primary,
            "disposition": topic.disposition.value,
            "template": deepcopy(template.value) if template else None,
        })
    plan = {"intent_id": str(intent.id), "analysis_id": str(analysis.id), "topics": confirmed_topics}
    template_snapshot = {
        "intent_template": deepcopy(template_rows[submitted["template_id"]].value),
        "topic_templates": {str(key): deepcopy(value.value) for key, value in template_rows.items()},
    }
    clinical_snapshot = {"clinical_nlp_hints": deepcopy(source_snapshot.get("clinical_nlp_hints", []))}
    pii_snapshot = {"manual_pii": deepcopy(source_state.get("manual_pii", [])), "phi_index": deepcopy(source_snapshot.get("phi_index", []))}

    dispatch_task_id = None
    try:
        with db.begin_nested():
            # A source-only analysis truthfully has no transcript
            # version/redaction run. Documents still require immutable
            # transcript-version lineage, so freeze the current (possibly
            # empty) draft only after all confirmation validation has passed.
            # Keeping this in the confirmation savepoint means a rejected
            # confirmation cannot leave an unused empty version behind.
            materialization_version = snapshot_current_transcript_version(
                db,
                transcript=transcript,
                allow_empty=True,
                mark_transcript_ready=False,
            )
            if materialization_version.transcript_id != transcript.id:
                raise AppError(500, "consultation_split_scope_invalid", "Consultation split content is unavailable")
            batch = create_split_batch(
                db, owner, analysis=analysis, intent_id=intent.id, confirmed_plan=plan,
                clinical_snapshot=clinical_snapshot, source_snapshot=source_snapshot,
                template_snapshot=template_snapshot, pii_snapshot=pii_snapshot,
                provider_snapshot={}, note_options_snapshot=dict(_note_generation_options_for_user(db, user_id=owner.id)),
                materialization_transcript_version_id=materialization_version.id,
            )
            for topic, plan_topic in zip(topics, confirmed_topics, strict=True):
                batch_topic = create_split_batch_topic(
                    db, owner, batch=batch, title=str(plan_topic["title"]), topic_order=topic.topic_order,
                    is_primary=topic.is_primary, disposition=topic.disposition,
                    template_snapshot=plan_topic["template"] or {}, topic_uuid=topic.topic_uuid,
                )
                create_split_topic_outcome(db, owner, batch_topic=batch_topic)
            draft.status = ConsultationSplitDraftStatus.confirmed
            intent.status = ConsultationSplitIntentStatus.confirmed
            db.flush()
            queued = queue_confirmed_split_generation(db, batch=batch)
            dispatch_task_id = queued.dispatch_task_id
    except IntegrityError:
        # A competing request may have committed after our initial replay check.
        batch = db.scalar(select(ConsultationSplitBatch).where(ConsultationSplitBatch.intent_id == intent.id).with_for_update())
        if batch is None:
            raise
        return _response(batch, replayed=True)
    db.commit()
    if dispatch_task_id is not None:
        from app.services.task_outbox import try_publish_task_dispatch_safely
        try_publish_task_dispatch_safely(dispatch_task_id)
    return _response(batch, replayed=False)
