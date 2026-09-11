"""Owner-only, encrypted review drafts for a completed split analysis.

This boundary deliberately has no provider, redaction, or queue behaviour.
It only uses the already persisted READY proposal and the read-only source
fingerprint proof required to decide whether that proposal is still current.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    ConsultationSplitAnalysis,
    ConsultationSplitAnalysisStatus,
    ConsultationSplitBatch,
    ConsultationSplitBatchStatus,
    ConsultationSplitDraft,
    ConsultationSplitDraftStatus,
    ConsultationSplitDraftTopic,
    ConsultationSplitTopicDisposition,
    PromptTemplate,
    PromptTemplateVersion,
    TeamRole,
    TemplateScope,
    Transcript,
    User,
    utcnow,
)
from app.schemas.consultation_split import (
    ConsultationSplitDraftDetail,
    ConsultationSplitDraftReplace,
    ConsultationSplitDraftTopicDetail,
)
from app.services.consultation_split_analysis import (
    ValidatedSplitAnalysis,
    validate_split_template_candidate_snapshot,
)
from app.services.consultation_split_locks import lock_consultation_split_source_scope
from app.services.consultation_split_sources import current_consultation_split_analysis_source_matches
from app.services.consultation_splits import (
    create_split_draft,
    create_split_draft_topic,
    read_split_analysis_json,
    read_split_topic_title,
)
from app.services.preferences import consultation_splitting_enabled, consultation_splitting_feature_enabled
from app.services.transcripts import transcript_is_expired


_TITLE_MAX_CHARS = 255


def _disabled() -> AppError:
    return AppError(403, "consultation_split_disabled", "Consultation splitting is not enabled")


def _normal_owner(actor: User) -> None:
    # Leadership adds no access to another user's content, but leaders retain
    # the same authority as users over transcript roots they personally own.
    if actor.is_system_admin or actor.team_id is None or actor.team_role not in {TeamRole.user, TeamRole.leader}:
        raise AppError(403, "forbidden", "Consultation split content is restricted to the owning user")


def _gated_owner(db: Session, actor: User) -> None:
    if not consultation_splitting_feature_enabled() or not consultation_splitting_enabled(db, actor):
        raise _disabled()
    _normal_owner(actor)


def _locked_scope(db: Session, actor: User, *, transcript_id: UUID):
    scope = lock_consultation_split_source_scope(db, owner_user_id=actor.id, transcript_id=transcript_id)
    if scope is None or scope.owner.team_id != actor.team_id or scope.owner.is_system_admin:
        raise AppError(404, "not_found", "Transcript not found", {"resource": "transcript", "transcript_id": str(transcript_id)})
    if transcript_is_expired(scope.transcript):
        raise AppError(404, "not_found", "Transcript not found", {"resource": "transcript", "transcript_id": str(transcript_id)})
    return scope


def _locked_drafts(db: Session, *, owner: User, transcript: Transcript) -> list[ConsultationSplitDraft]:
    return db.scalars(
        select(ConsultationSplitDraft)
        .where(
            ConsultationSplitDraft.owner_user_id == owner.id,
            ConsultationSplitDraft.team_id == owner.team_id,
            ConsultationSplitDraft.transcript_id == transcript.id,
        )
        .order_by(ConsultationSplitDraft.updated_at.desc(), ConsultationSplitDraft.id.desc())
        .with_for_update()
    ).all()


def _locked_ready_analyses(db: Session, *, owner: User, transcript: Transcript) -> list[ConsultationSplitAnalysis]:
    return db.scalars(
        select(ConsultationSplitAnalysis)
        .where(
            ConsultationSplitAnalysis.owner_user_id == owner.id,
            ConsultationSplitAnalysis.team_id == owner.team_id,
            ConsultationSplitAnalysis.transcript_id == transcript.id,
            ConsultationSplitAnalysis.status == ConsultationSplitAnalysisStatus.ready,
        )
        .order_by(ConsultationSplitAnalysis.updated_at.desc(), ConsultationSplitAnalysis.id.desc())
        .with_for_update()
    ).all()


def _lock_topics(db: Session, *, draft_id: UUID) -> list[ConsultationSplitDraftTopic]:
    return db.scalars(
        select(ConsultationSplitDraftTopic)
        .where(ConsultationSplitDraftTopic.draft_id == draft_id)
        .order_by(ConsultationSplitDraftTopic.topic_order, ConsultationSplitDraftTopic.id)
        .with_for_update()
    ).all()


def _source_matches(db: Session, owner: User, transcript: Transcript, analysis: ConsultationSplitAnalysis) -> bool:
    # The helper reads existing rows only: no redaction, source preparation,
    # credential resolution, or provider call belongs on draft routes.
    return current_consultation_split_analysis_source_matches(
        db, owner, transcript=transcript, analysis=analysis
    )


def _topic_detail(db: Session, owner: User, topic: ConsultationSplitDraftTopic) -> ConsultationSplitDraftTopicDetail:
    return ConsultationSplitDraftTopicDetail(
        topic_uuid=topic.topic_uuid,
        title=read_split_topic_title(db, owner, topic=topic),
        order=topic.topic_order,
        is_primary=topic.is_primary,
        disposition=topic.disposition.value,
        template_id=topic.template_id,
        template_version_id=topic.template_version_id,
    )


def _projection(
    db: Session,
    owner: User,
    draft: ConsultationSplitDraft,
    *,
    forced_stale: bool = False,
) -> ConsultationSplitDraftDetail:
    topics = [_topic_detail(db, owner, topic) for topic in _lock_topics(db, draft_id=draft.id)]
    return ConsultationSplitDraftDetail(
        draft_id=draft.id,
        analysis_id=draft.analysis_id,
        status="stale" if forced_stale else draft.status.value,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
        topics=topics,
    )


def _validated_proposal(db: Session, owner: User, analysis: ConsultationSplitAnalysis) -> ValidatedSplitAnalysis:
    if analysis.status is not ConsultationSplitAnalysisStatus.ready:
        raise AppError(409, "consultation_split_analysis_unavailable", "A ready split analysis is required")
    try:
        proposal = read_split_analysis_json(db, owner, analysis=analysis, field="proposal_encrypted")
        if proposal is None:
            raise ValueError("missing proposal")
        validated = ValidatedSplitAnalysis.model_validate(proposal)
        # READY means the analysis found enough meaningful topics to enter
        # review.  Reassert every review invariant here: persisted encrypted
        # provider output is untrusted at this boundary, and a corrupt READY
        # row must never seed a partial or ambiguous clinician draft.
        if not 2 <= len(validated.topics) <= 6:
            raise ValueError("ready proposal has an invalid topic count")
        primaries = [topic for topic in validated.topics if topic.is_primary]
        if len(primaries) != 1:
            raise ValueError("ready proposal must have exactly one primary")
        if primaries[0].disposition != "separate_note":
            raise ValueError("ready proposal primary disposition is invalid")
        if len({topic.topic_uuid for topic in validated.topics}) != len(validated.topics):
            raise ValueError("ready proposal has duplicate topic UUIDs")
        normalized_titles = {" ".join(topic.title.split()).casefold() for topic in validated.topics}
        if len(normalized_titles) != len(validated.topics) or "" in normalized_titles:
            raise ValueError("ready proposal has duplicate or blank topic titles")
        candidates = read_split_analysis_json(
            db,
            owner,
            analysis=analysis,
            field="candidate_template_snapshot_encrypted",
        )
        if not isinstance(candidates, dict):
            raise ValueError("ready proposal has an invalid candidate snapshot")
        candidate_ids = {
            candidate.id
            for candidate in validate_split_template_candidate_snapshot(candidates)
        }
        if any(
            topic.template_id is not None and topic.template_id not in candidate_ids
            for topic in validated.topics
        ):
            raise ValueError("ready proposal selects a non-candidate template")
        return validated
    except (AppError, ValidationError, TypeError, ValueError, UnicodeDecodeError) as exc:
        raise AppError(409, "consultation_split_proposal_unavailable", "The split proposal is unavailable") from exc


def _normalize_title(value: str) -> str:
    title = " ".join(value.split()) if isinstance(value, str) else ""
    if not title or len(title) > _TITLE_MAX_CHARS:
        raise AppError(422, "validation_error", "Topic title must contain at most 255 characters")
    return title


def _latest_accessible_template(
    db: Session, actor: User, *, template_id: UUID | None, required: bool
) -> tuple[UUID | None, UUID | None]:
    if template_id is None:
        if required:
            raise AppError(422, "validation_error", "A new topic requires an available template")
        return None, None
    template = db.scalar(
        select(PromptTemplate)
        .where(
            PromptTemplate.id == template_id,
            PromptTemplate.is_active.is_(True),
            (
                ((PromptTemplate.scope == TemplateScope.user) & (PromptTemplate.owner_user_id == actor.id))
                | ((PromptTemplate.scope == TemplateScope.team) & (PromptTemplate.team_id == actor.team_id))
            ),
        )
        .with_for_update()
    )
    if template is None:
        raise AppError(422, "validation_error", "Template selection is not available")
    version = db.scalar(
        select(PromptTemplateVersion)
        .where(PromptTemplateVersion.template_id == template.id)
        .order_by(PromptTemplateVersion.version_no.desc())
        .limit(1)
        .with_for_update()
    )
    if version is None:
        raise AppError(422, "validation_error", "Template selection is not available")
    return template.id, version.id


def _proposal_template_or_none(
    db: Session, actor: User, *, template_id: UUID | None
) -> tuple[UUID | None, UUID | None]:
    """Keep a proposal usable when its formerly valid template disappeared.

    The provider selected only an ID, not a durable version.  A deleted or
    inactive choice must therefore become an explicit clinician repair, not a
    substituted template.  This intentionally applies only to proposal seed
    rows; PUT still rejects an unavailable supplied selection.
    """
    if template_id is None:
        return None, None
    try:
        return _latest_accessible_template(db, actor, template_id=template_id, required=False)
    except AppError:
        return None, None


def _validate_replacement(
    db: Session,
    actor: User,
    *,
    draft: ConsultationSplitDraft,
    payload: ConsultationSplitDraftReplace,
    proposal_topic_uuids: set[UUID],
) -> list[tuple[UUID, str, bool, ConsultationSplitTopicDisposition, UUID | None, UUID | None]]:
    # The locked draft parent serializes replacements.  Read topic UUIDs without
    # taking child locks so selected templates can be locked first, matching the
    # template-delete order (template -> FK child topic).
    existing = set(
        db.scalars(
            select(ConsultationSplitDraftTopic.topic_uuid).where(
                ConsultationSplitDraftTopic.draft_id == draft.id
            )
        ).all()
    )
    seen_uuids: set[UUID] = set()
    seen_titles: set[str] = set()
    primary_count = 0
    pending_rows: list[tuple[UUID, str, bool, ConsultationSplitTopicDisposition, UUID | None]] = []
    for order, item in enumerate(payload.topics):
        topic_uuid = item.topic_uuid or uuid4()
        if topic_uuid in seen_uuids:
            raise AppError(422, "validation_error", "Topic UUIDs must be distinct")
        if item.topic_uuid is not None and item.topic_uuid not in existing:
            raise AppError(422, "validation_error", "Topic UUID does not belong to this draft")
        seen_uuids.add(topic_uuid)
        title = _normalize_title(item.title)
        key = title.casefold()
        if key in seen_titles:
            raise AppError(422, "validation_error", "Topic titles must be distinct")
        seen_titles.add(key)
        disposition = ConsultationSplitTopicDisposition(item.disposition)
        if item.is_primary:
            primary_count += 1
            if disposition is not ConsultationSplitTopicDisposition.separate_note:
                raise AppError(422, "validation_error", "Primary topic must be a separate note")
        if item.template_id is None and item.topic_uuid is not None and topic_uuid not in proposal_topic_uuids:
            raise AppError(422, "validation_error", "A clinician-added topic requires an available template")
        if item.template_id is None and item.topic_uuid is None:
            raise AppError(422, "validation_error", "A new topic requires an available template")
        pending_rows.append((topic_uuid, title, item.is_primary, disposition, item.template_id))
    if pending_rows and primary_count != 1:
        raise AppError(422, "validation_error", "Exactly one primary topic is required")

    # Lock every referenced template/version in UUID order before any draft
    # topic lock.  This is the shared order with template deletion, whose FK
    # cleanup subsequently locks draft topics.
    resolved_templates = {
        template_id: _latest_accessible_template(
            db,
            actor,
            template_id=template_id,
            required=True,
        )
        for template_id in sorted(
            {template_id for *_row, template_id in pending_rows if template_id is not None},
            key=str,
        )
    }
    return [
        (
            topic_uuid,
            title,
            is_primary,
            disposition,
            *resolved_templates.get(template_id, (None, None)),
        )
        for topic_uuid, title, is_primary, disposition, template_id in pending_rows
    ]


def _advance_timestamp(draft: ConsultationSplitDraft) -> None:
    now = utcnow()
    old = draft.updated_at
    if old.tzinfo is None:
        now = now.replace(tzinfo=None)
    draft.updated_at = max(now, old + timedelta(microseconds=1))


def initialize_or_reuse_split_draft(
    db: Session, actor: User, *, transcript_id: UUID
) -> ConsultationSplitDraftDetail:
    """Create exactly one draft for the current READY analysis, or reuse it."""
    _gated_owner(db, actor)
    scope = _locked_scope(db, actor, transcript_id=transcript_id)
    owner, transcript = scope.owner, scope.transcript
    analysis = next(
        (row for row in _locked_ready_analyses(db, owner=owner, transcript=transcript) if _source_matches(db, owner, transcript, row)),
        None,
    )
    if analysis is None:
        raise AppError(409, "consultation_split_analysis_unavailable", "A current ready split analysis is required")
    draft = db.scalar(
        select(ConsultationSplitDraft)
        .where(ConsultationSplitDraft.analysis_id == analysis.id)
        .with_for_update()
    )
    if draft is not None:
        if draft.status is ConsultationSplitDraftStatus.confirmed:
            latest_batch_status = db.scalar(
                select(ConsultationSplitBatch.status)
                .where(
                    ConsultationSplitBatch.analysis_id == analysis.id,
                    ConsultationSplitBatch.owner_user_id == owner.id,
                    ConsultationSplitBatch.team_id == transcript.team_id,
                    ConsultationSplitBatch.transcript_id == transcript.id,
                )
                .order_by(ConsultationSplitBatch.created_at.desc())
                .limit(1)
            )
            if latest_batch_status in {
                ConsultationSplitBatchStatus.ready,
                ConsultationSplitBatchStatus.completed_partial,
                ConsultationSplitBatchStatus.failed,
            }:
                # A confirmed batch is immutable and keeps its own encrypted
                # plan. Reopening only the mutable review draft lets a new
                # Generate intent create another independent batch.
                draft.status = ConsultationSplitDraftStatus.active
                _advance_timestamp(draft)
                db.commit()
        return _projection(db, owner, draft)
    proposal = _validated_proposal(db, owner, analysis)
    try:
        with db.begin_nested():
            draft = create_split_draft(db, owner, analysis=analysis)
            for order, topic in enumerate(proposal.topics):
                template_id, version_id = _proposal_template_or_none(
                    db, owner, template_id=topic.template_id
                )
                create_split_draft_topic(
                    db, owner, draft=draft, title=_normalize_title(topic.title), topic_order=order,
                    is_primary=topic.is_primary,
                    disposition=ConsultationSplitTopicDisposition(topic.disposition),
                    template_id=template_id, template_version_id=version_id, topic_uuid=topic.topic_uuid,
                )
    except IntegrityError:
        # A concurrent initializer lost only its savepoint.  Never roll back
        # the surrounding request transaction or create another draft.
        draft = db.scalar(
            select(ConsultationSplitDraft)
            .where(ConsultationSplitDraft.analysis_id == analysis.id)
            .with_for_update()
        )
        if draft is None:
            raise
    assert draft is not None
    db.commit()
    return _projection(db, owner, draft)


def read_split_draft(
    db: Session, actor: User, *, transcript_id: UUID
) -> ConsultationSplitDraftDetail:
    """Read a draft only.  Staleness is projected, never persisted here."""
    _gated_owner(db, actor)
    scope = _locked_scope(db, actor, transcript_id=transcript_id)
    owner, transcript = scope.owner, scope.transcript
    drafts = _locked_drafts(db, owner=owner, transcript=transcript)
    if not drafts:
        raise AppError(404, "not_found", "Consultation split draft not found", {"resource": "consultation_split_draft"})
    drafts_by_analysis = {draft.analysis_id: draft for draft in drafts}
    for analysis in _locked_ready_analyses(db, owner=owner, transcript=transcript):
        if _source_matches(db, owner, transcript, analysis):
            current = drafts_by_analysis.get(analysis.id)
            if current is not None:
                return _projection(db, owner, current)
    # A stale result must not revive a draft or issue an UPDATE during GET.
    fallback = drafts[0]
    return _projection(
        db,
        owner,
        fallback,
        forced_stale=fallback.status is ConsultationSplitDraftStatus.active,
    )


def replace_split_draft(
    db: Session,
    actor: User,
    *,
    transcript_id: UUID,
    payload: ConsultationSplitDraftReplace,
) -> ConsultationSplitDraftDetail:
    """Atomically replace a current draft after an exact optimistic check."""
    _gated_owner(db, actor)
    scope = _locked_scope(db, actor, transcript_id=transcript_id)
    owner, transcript = scope.owner, scope.transcript
    drafts = _locked_drafts(db, owner=owner, transcript=transcript)
    if not drafts:
        raise AppError(404, "not_found", "Consultation split draft not found", {"resource": "consultation_split_draft"})
    drafts_by_analysis = {draft.analysis_id: draft for draft in drafts}
    analysis = next(
        (row for row in _locked_ready_analyses(db, owner=owner, transcript=transcript) if _source_matches(db, owner, transcript, row)),
        None,
    )
    if analysis is None:
        # No current proposal exists.  Mark only the most recently touched
        # active draft stale; terminal history remains truthful.
        fallback = drafts[0]
        if fallback.status is ConsultationSplitDraftStatus.active:
            fallback.status = ConsultationSplitDraftStatus.stale
            # This route owns the monotonic transition.  Commit it before the
            # conflict response so a later read cannot revive stale content.
            db.commit()
        raise AppError(409, "consultation_split_source_stale", "Consultation sources changed before the draft was saved")
    draft = drafts_by_analysis.get(analysis.id)
    if draft is None:
        raise AppError(404, "not_found", "Consultation split draft not found", {"resource": "consultation_split_draft"})
    if draft.status is not ConsultationSplitDraftStatus.active:
        raise AppError(409, "consultation_split_draft_unavailable", "Consultation split draft is unavailable")
    if payload.expected_updated_at != draft.updated_at:
        raise AppError(409, "consultation_split_draft_conflict", "Consultation split draft changed in another tab")
    proposal_topic_uuids = {topic.topic_uuid for topic in _validated_proposal(db, owner, analysis).topics}
    replacement = _validate_replacement(
        db,
        owner,
        draft=draft,
        payload=payload,
        proposal_topic_uuids=proposal_topic_uuids,
    )
    # Delete first and flush before reinsertion: this preserves stable topic
    # UUIDs while avoiding transient unique-order/one-primary violations.
    for topic in _lock_topics(db, draft_id=draft.id):
        db.delete(topic)
    db.flush()
    for order, (topic_uuid, title, is_primary, disposition, template_id, version_id) in enumerate(replacement):
        create_split_draft_topic(
            db, owner, draft=draft, title=title, topic_order=order, is_primary=is_primary,
            disposition=disposition, template_id=template_id, template_version_id=version_id,
            topic_uuid=topic_uuid,
        )
    _advance_timestamp(draft)
    db.flush()
    db.commit()
    return _projection(db, owner, draft)
