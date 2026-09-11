"""Atomic durable Create intent for consultation-splitting.

This service is deliberately below routes and browser behaviour.  It records
the logical owner Generate action, reuses or queues the canonical source-bound
analysis, and publishes only a newly-created durable outbox row after commit.
It does not create a generated document, resolve a credential, call a
provider, initialise a draft, or decide how an analysis result is consumed.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    ConsultationSplitAnalysis,
    ConsultationSplitExecution,
    ConsultationSplitIntent,
    ConsultationSplitIntentStatus,
    GeneratedDocument,
    GeneratedDocumentGeneratorType,
    GeneratedDocumentStatus,
    PromptTemplate,
    PromptTemplateVersion,
    TeamRole,
    TemplateMode,
    TranscriptWorkingNoteMode,
    TemplateScope,
    User,
)
from app.services.consultation_split_queue import (
    QueueOrReuseSplitAnalysisResult,
    QueueSplitAnalysisOutcome,
    _queue_or_reuse_prepared_split_analysis,
    _reuse_result,
)
from app.services.consultation_split_sources import (
    PreparedConsultationSplitAnalysis,
    current_consultation_split_analysis_clinical_source_matches,
    find_cached_prepared_consultation_split_analysis,
    prepare_source_bound_consultation_split_analysis,
)
from app.services.consultation_split_locks import lock_consultation_split_source_scope
from app.services.consultation_splits import require_split_owner_transcript
from app.services.transcripts import transcript_is_expired
from app.services.content_crypto import decrypt_json_for_owner, encrypt_json_for_owner
from app.services.preferences import effective_consultation_splitting_enabled
from app.services.task_outbox import try_publish_task_dispatch_safely
from app.services.templates import (
    NOTE_GENERATION_DETAIL_GUIDANCE,
    NOTE_GENERATION_OPTIONS_SNAPSHOT_KEY,
    NOTE_GENERATION_LENGTH_TOKEN_CAPS,
    _effective_dictation_text,
    _flush_generated_document_with_quota,
    _latest_template_version,
    _note_generation_options_for_user,
    _resolve_available_template_for_user,
    _structured_section_definitions_snapshot,
    _template_version_config,
    _working_note_snapshot_for_transcript,
    set_generated_document_structured_working_note_snapshot,
    set_generated_document_text,
)
from app.schemas.templates import StructuredTemplateConfig
from app.services.llm import resolve_user_llm
from app.services.transcripts import snapshot_current_transcript_version


_TABLE_INTENT = "consultation_split_intents"
_INTENT_IDEMPOTENCY_CONSTRAINT = "uq_consultation_split_intents_owner_idempotency_key"
ConsultationSplitIntentAnalysisOutcome = QueueSplitAnalysisOutcome | Literal["analysis_missing"]


@dataclass(frozen=True, slots=True)
class CreateOrReplayConsultationSplitIntentResult:
    """A content-safe result for one logical future Generate action."""

    intent: ConsultationSplitIntent | None
    analysis: ConsultationSplitAnalysis | None
    execution: ConsultationSplitExecution | None
    analysis_outcome: ConsultationSplitIntentAnalysisOutcome
    created_new_intent: bool
    created_new_analysis_work: bool


@dataclass(frozen=True, slots=True)
class ContinueConsultationSplitIntentAsOneNoteResult:
    """The ordinary document bound to an irrevocably consumed split intent."""

    intent: ConsultationSplitIntent
    document: GeneratedDocument | None
    created_new_document: bool


def _disabled() -> AppError:
    return AppError(403, "consultation_split_disabled", "Consultation splitting is not enabled")


def _require_normal_owner(actor: User) -> None:
    """Allow team users and leaders to act only on their own content."""
    if actor.is_system_admin or actor.team_id is None or actor.team_role not in {TeamRole.user, TeamRole.leader}:
        raise AppError(403, "forbidden", "Consultation split content is restricted to team users")


def _existing_intent(
    db: Session,
    *,
    owner_user_id: UUID,
    client_idempotency_key: UUID,
    lock: bool = False,
) -> ConsultationSplitIntent | None:
    statement = select(ConsultationSplitIntent).where(
        ConsultationSplitIntent.owner_user_id == owner_user_id,
        ConsultationSplitIntent.client_idempotency_key == client_idempotency_key,
    )
    if lock:
        statement = statement.with_for_update()
    return db.scalar(statement)


def _safe_replay_result(
    db: Session,
    *,
    actor: User,
    intent: ConsultationSplitIntent,
) -> CreateOrReplayConsultationSplitIntentResult:
    """Read a prior idempotency record without validating caller payloads.

    The intent is authoritative for replay.  In particular, a deleted
    analysis remains ``None`` rather than being replaced using a new source
    fingerprint, template, preference, or transcript supplied by a retry.
    """
    require_split_owner_transcript(db, actor, transcript_id=intent.transcript_id)
    scope = lock_consultation_split_source_scope(
        db,
        owner_user_id=actor.id,
        transcript_id=intent.transcript_id,
    )
    if scope is None or transcript_is_expired(scope.transcript):
        # The first lookup preserves ordinary owner/expired semantics. A
        # concurrent removal or expiry after it is an internal unavailable
        # replay, never an excuse to return linked content without the scope.
        raise AppError(500, "consultation_split_scope_invalid", "Consultation split content is unavailable")
    transcript = scope.transcript
    if (
        intent.owner_user_id != actor.id
        or intent.team_id != actor.team_id
        or intent.transcript_id != transcript.id
        or intent.retention_expires_at != transcript.retention_expires_at
    ):
        raise AppError(500, "consultation_split_scope_invalid", "Consultation split content is unavailable")

    if intent.analysis_id is None:
        return CreateOrReplayConsultationSplitIntentResult(
            intent=intent,
            analysis=None,
            execution=None,
            analysis_outcome="analysis_missing",
            created_new_intent=False,
            created_new_analysis_work=False,
        )

    analysis = db.scalar(
        select(ConsultationSplitAnalysis)
        .where(ConsultationSplitAnalysis.id == intent.analysis_id)
        .with_for_update()
    )
    if analysis is None:
        # A normal analysis delete uses SET NULL.  A non-null dangling link is
        # corruption, so do not turn it into an apparently valid replay.
        raise AppError(500, "consultation_split_scope_invalid", "Consultation split content is unavailable")
    if (
        analysis.owner_user_id != intent.owner_user_id
        or analysis.team_id != intent.team_id
        or analysis.transcript_id != intent.transcript_id
        or analysis.retention_expires_at != intent.retention_expires_at
    ):
        raise AppError(500, "consultation_split_scope_invalid", "Consultation split content is unavailable")

    decision = _reuse_result(db, actor=actor, analysis=analysis)
    return CreateOrReplayConsultationSplitIntentResult(
        intent=intent,
        analysis=analysis,
        execution=decision.execution,
        analysis_outcome=decision.outcome,
        created_new_intent=False,
        created_new_analysis_work=False,
    )


def _require_locked_available_template(
    db: Session,
    actor: User,
    *,
    template_id: UUID,
) -> tuple[PromptTemplate, PromptTemplateVersion]:
    """Resolve the current accessible template and freeze its latest version.

    The ordinary template-generation path provides the availability and
    latest-version rules.  We repeat its availability query under a parent
    lock so template deletion cannot pass an unbound live row into this
    encrypted selected-at-submit snapshot.
    """
    # Reuse the normal template availability service before acquiring a lock,
    # preserving its normal not-found response and ownership semantics.
    _resolve_available_template_for_user(db, actor, template_id=template_id)
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
        # The parent could have changed between the first availability check
        # and the lock.  Keep the established non-disclosing response.
        raise AppError(404, "not_found", "Template not found", {"resource": "template", "template_id": str(template_id)})

    # ``_latest_template_version`` is the existing version-selection rule.
    # Re-read the returned row under a lock so the exact immutable version
    # snapshot is coherent with the retained template parent.
    latest = _latest_template_version(db, template_id=template.id)
    version = db.scalar(
        select(PromptTemplateVersion)
        .where(PromptTemplateVersion.id == latest.id, PromptTemplateVersion.template_id == template.id)
        .with_for_update()
    )
    if version is None:
        raise AppError(404, "not_found", "Template version not found", {"resource": "template_version", "template_id": str(template.id)})
    return template, version


def _selected_template_generation_snapshot(
    db: Session,
    actor: User,
    *,
    template_id: UUID,
) -> dict[str, Any]:
    """Return the complete selected-at-Generate snapshot for encryption only."""
    template, version = _require_locked_available_template(db, actor, template_id=template_id)
    template_config = _template_version_config(version)
    structured_sections = _structured_section_definitions_snapshot(template_config)
    return {
        "selected_template": {
            "template_id": str(template.id),
            "template_version_id": str(version.id),
            "template_version_no": version.version_no,
            "name": template.name,
            "description": template.description,
            "mode": version.mode.value,
            "prompt_text": version.prompt_text,
            "config": deepcopy(version.config_json),
            "structured_sections": structured_sections,
        },
        "generation_configuration": dict(_note_generation_options_for_user(db, user_id=actor.id)),
    }


def _invalid_consumption_snapshot() -> AppError:
    return AppError(500, "consultation_split_snapshot_invalid", "Consultation split request is unavailable")


def _submitted_generation_snapshot(
    db: Session,
    *,
    intent: ConsultationSplitIntent,
) -> dict[str, Any]:
    """Decrypt and strictly validate the selected-at-submit template snapshot."""
    snapshot = decrypt_json_for_owner(
        db,
        owner_user_id=intent.owner_user_id,
        table=_TABLE_INTENT,
        field="generation_snapshot_encrypted",
        record_id=intent.id,
        stored_value=intent.generation_snapshot_encrypted,
    )
    if not isinstance(snapshot, dict):
        raise _invalid_consumption_snapshot()
    selected = snapshot.get("selected_template")
    options = snapshot.get("generation_configuration")
    if not isinstance(selected, dict) or not isinstance(options, dict):
        raise _invalid_consumption_snapshot()
    try:
        template_id = UUID(selected["template_id"])
        version_id = UUID(selected["template_version_id"])
    except (KeyError, TypeError, ValueError):
        raise _invalid_consumption_snapshot() from None
    if (
        not isinstance(selected.get("template_version_no"), int)
        or not isinstance(selected.get("name"), str)
        or not isinstance(selected.get("description"), (str, type(None)))
        or not isinstance(selected.get("prompt_text"), str)
        or not isinstance(selected.get("config"), (dict, type(None)))
        or selected.get("mode") not in {mode.value for mode in TemplateMode}
        or not isinstance(selected.get("structured_sections"), (dict, type(None)))
        or set(options) != {"note_generation_length", "llm_detail_level"}
        or options["note_generation_length"] not in NOTE_GENERATION_LENGTH_TOKEN_CAPS
        or options["llm_detail_level"] not in NOTE_GENERATION_DETAIL_GUIDANCE
    ):
        raise _invalid_consumption_snapshot()
    if selected["mode"] == TemplateMode.structured.value:
        try:
            config = StructuredTemplateConfig.model_validate(selected["config"])
        except Exception:
            raise _invalid_consumption_snapshot() from None
        if _structured_section_definitions_snapshot(config) != selected["structured_sections"]:
            raise _invalid_consumption_snapshot()
    return {
        "template_id": template_id,
        "version_id": version_id,
        "version_no": selected["template_version_no"],
        "name": selected["name"],
        "prompt_text": selected["prompt_text"],
        "config": deepcopy(selected["config"]),
        "mode": TemplateMode(selected["mode"]),
        "structured_sections": deepcopy(selected["structured_sections"]),
        "options": dict(options),
    }


def _require_live_submitted_template(
    db: Session,
    actor: User,
    *,
    submitted: dict[str, Any],
) -> None:
    """Require the parent still be usable without replacing the saved version.

    Availability is evaluated against the live parent.  The immutable request
    remains authoritative for prompt, mode, sections, and generation options.
    """
    template_id = submitted["template_id"]
    _resolve_available_template_for_user(db, actor, template_id=template_id)
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
        raise AppError(404, "not_found", "Template not found", {"resource": "template", "template_id": str(template_id)})
    version = db.scalar(
        select(PromptTemplateVersion)
        .where(
            PromptTemplateVersion.id == submitted["version_id"],
            PromptTemplateVersion.template_id == template.id,
            PromptTemplateVersion.version_no == submitted["version_no"],
        )
        .with_for_update()
    )
    if version is None:
        raise AppError(409, "consultation_split_template_unavailable", "The submitted template version is no longer available")


def _analysis_source_state_for_consumption(
    db: Session,
    *,
    analysis: ConsultationSplitAnalysis,
) -> dict[str, Any]:
    snapshot = decrypt_json_for_owner(
        db,
        owner_user_id=analysis.owner_user_id,
        table="consultation_split_analyses",
        field="source_snapshot_encrypted",
        record_id=analysis.id,
        stored_value=analysis.source_snapshot_encrypted,
    )
    state = snapshot.get("source_state") if isinstance(snapshot, dict) else None
    if not isinstance(state, dict):
        raise AppError(500, "consultation_split_source_invalid", "Consultation split content is unavailable")
    return state


def _consumed_intent_result(intent: ConsultationSplitIntent) -> ContinueConsultationSplitIntentAsOneNoteResult:
    return ContinueConsultationSplitIntentAsOneNoteResult(
        intent=intent,
        document=intent.generated_document,
        created_new_document=False,
    )


def _new_intent(
    db: Session,
    *,
    actor: User,
    owner_user_id: UUID,
    team_id: UUID,
    transcript_id: UUID,
    retention_expires_at: datetime,
    analysis_id: UUID | None,
    snapshot: dict[str, Any],
    client_idempotency_key: UUID,
) -> ConsultationSplitIntent:
    """Build one unflushed intent from only server-derived source ancestry."""
    if (
        owner_user_id != actor.id
        or team_id != actor.team_id
        or retention_expires_at is None
    ):
        raise AppError(500, "consultation_split_scope_invalid", "Consultation split content is unavailable")
    intent = ConsultationSplitIntent(
        id=uuid4(),
        owner_user_id=owner_user_id,
        team_id=team_id,
        transcript_id=transcript_id,
        analysis_id=analysis_id,
        client_idempotency_key=client_idempotency_key,
        status=ConsultationSplitIntentStatus.analysis_pending,
        retention_expires_at=retention_expires_at,
    )
    intent.generation_snapshot_encrypted = encrypt_json_for_owner(
        db,
        owner_user_id=intent.owner_user_id,
        table=_TABLE_INTENT,
        field="generation_snapshot_encrypted",
        record_id=intent.id,
        plaintext=snapshot,
    ) or ""
    return intent


def _insert_or_replay_intent(
    db: Session,
    *,
    actor: User,
    owner_user_id: UUID,
    team_id: UUID,
    transcript_id: UUID,
    retention_expires_at: datetime,
    analysis_id: UUID | None,
    snapshot: dict[str, Any],
    client_idempotency_key: UUID,
) -> tuple[ConsultationSplitIntent | None, CreateOrReplayConsultationSplitIntentResult | None]:
    """Insert the idempotency root before queue work, or return its winner.

    This is intentionally the only IntegrityError caught here.  The savepoint
    makes a same-key concurrent loser discard any not-yet-persisted intent
    state, reread the committed winner, and avoid a second reservation/outbox.
    """
    try:
        with db.begin_nested():
            intent = _new_intent(
                db,
                actor=actor,
                owner_user_id=owner_user_id,
                team_id=team_id,
                transcript_id=transcript_id,
                retention_expires_at=retention_expires_at,
                analysis_id=analysis_id,
                snapshot=snapshot,
                client_idempotency_key=client_idempotency_key,
            )
            db.add(intent)
            db.flush()
    except IntegrityError as exc:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint != _INTENT_IDEMPOTENCY_CONSTRAINT:
            raise
        winner = _existing_intent(
            db,
            owner_user_id=actor.id,
            client_idempotency_key=client_idempotency_key,
            lock=True,
        )
        if winner is None:
            # A correctly-reported unique conflict must have a committed row
            # by the time PostgreSQL releases the unique-index wait.
            raise AppError(500, "consultation_split_intent_unavailable", "Consultation split request is unavailable")
        return None, _safe_replay_result(db, actor=actor, intent=winner)
    return intent, None


def _without_intent(
    result: QueueOrReuseSplitAnalysisResult,
) -> CreateOrReplayConsultationSplitIntentResult:
    return CreateOrReplayConsultationSplitIntentResult(
        intent=None,
        analysis=result.analysis,
        execution=result.execution,
        analysis_outcome=result.outcome,
        created_new_intent=False,
        created_new_analysis_work=False,
    )


def _new_intent_result(
    *,
    intent: ConsultationSplitIntent,
    queue_result: QueueOrReuseSplitAnalysisResult,
) -> CreateOrReplayConsultationSplitIntentResult:
    return CreateOrReplayConsultationSplitIntentResult(
        intent=intent,
        analysis=queue_result.analysis,
        execution=queue_result.execution,
        analysis_outcome=queue_result.outcome,
        created_new_intent=True,
        created_new_analysis_work=queue_result.created_new_work,
    )


def create_or_replay_consultation_split_intent(
    db: Session,
    actor: User,
    *,
    transcript_id: UUID,
    client_idempotency_key: UUID,
    selected_template_id: UUID,
) -> CreateOrReplayConsultationSplitIntentResult:
    """Atomically bind one durable Generate intent to canonical analysis work.

    A replay lookup deliberately precedes fresh-payload validation.  Once an
    idempotency key exists, neither a later preference change nor a changed
    transcript/template request may retarget it.  New work is gated before
    transcript lookup, source preparation, redaction, provider selection, or
    any durable split write.
    """
    _require_normal_owner(actor)

    existing = _existing_intent(
        db,
        owner_user_id=actor.id,
        client_idempotency_key=client_idempotency_key,
    )
    if existing is not None:
        replay = _safe_replay_result(db, actor=actor, intent=existing)
        db.commit()
        return replay

    if not effective_consultation_splitting_enabled(db, actor):
        # This branch deliberately runs before the selected-template query,
        # transcript access, source decryption/redaction, or LLM selection.
        db.rollback()
        raise _disabled()

    try:
        # Fail inexpensive invalid template selections before source work, then
        # resolve and lock the exact snapshot again after preparation.
        _resolve_available_template_for_user(db, actor, template_id=selected_template_id)
        prepared: PreparedConsultationSplitAnalysis = prepare_source_bound_consultation_split_analysis(
            db,
            actor,
            transcript_id=transcript_id,
        )

        # Preparation can release the source locks while required redaction
        # runs.  It reacquires the owner/root lock before returning, so repeat
        # the effective gate while that lock is held.  An opt-out which
        # committed during preparation must not be followed by a new intent,
        # reservation, or dispatch.  Existing-key replays returned above stay
        # stable by design.
        if not effective_consultation_splitting_enabled(db, actor):
            db.rollback()
            raise _disabled()

        # Avoid creating a durable Generate request for passive/damaged cache
        # rows.  The full core repeats this check later to close the race with
        # another request that commits while this one snapshots its template.
        cached = find_cached_prepared_consultation_split_analysis(db, actor, prepared=prepared)
        if cached is not None:
            cached_result = _reuse_result(db, actor=actor, analysis=cached)
            if cached_result.outcome in {"incomplete", "stale_conflict"}:
                db.commit()
                return _without_intent(cached_result)
            snapshot = _selected_template_generation_snapshot(
                db,
                actor,
                template_id=selected_template_id,
            )
            intent, replay = _insert_or_replay_intent(
                db,
                actor=actor,
                owner_user_id=cached.owner_user_id,
                team_id=cached.team_id,
                transcript_id=cached.transcript_id,
                retention_expires_at=cached.retention_expires_at,
                analysis_id=cached.id,
                snapshot=snapshot,
                client_idempotency_key=client_idempotency_key,
            )
            if replay is not None:
                db.commit()
                return replay
            assert intent is not None
            db.commit()
            return _new_intent_result(intent=intent, queue_result=cached_result)

        snapshot = _selected_template_generation_snapshot(
            db,
            actor,
            template_id=selected_template_id,
        )

        # First reserve the owner/key inside an inner savepoint.  A concurrent
        # same-key caller returns the winner before it can create analysis
        # work, reserve quota, or add a dispatch row.
        source_state = prepared.source_state
        intent, replay = _insert_or_replay_intent(
            db,
            actor=actor,
            owner_user_id=source_state.owner_user_id,
            team_id=source_state.team_id,
            transcript_id=source_state.transcript_id,
            retention_expires_at=prepared.retention_expires_at,
            analysis_id=None,
            snapshot=snapshot,
            client_idempotency_key=client_idempotency_key,
        )
        if replay is not None:
            db.commit()
            return replay
        assert intent is not None

        decision = _queue_or_reuse_prepared_split_analysis(db, actor, prepared=prepared)
        queue_result = decision.result
        if queue_result.analysis is None or queue_result.outcome in {"incomplete", "stale_conflict"}:
            # The intent insert and any new nested queue work share this outer
            # transaction.  Do not preserve a request that cannot bind to a
            # canonical usable analysis.
            db.rollback()
            return _without_intent(queue_result)

        analysis = queue_result.analysis
        if (
            analysis.owner_user_id != intent.owner_user_id
            or analysis.team_id != intent.team_id
            or analysis.transcript_id != intent.transcript_id
            or analysis.retention_expires_at != intent.retention_expires_at
        ):
            raise AppError(500, "consultation_split_scope_invalid", "Consultation split content is unavailable")
        intent.analysis_id = analysis.id
        db.add(intent)
        db.commit()
    except Exception:
        # Source preparation commits only its established redaction boundary.
        # Everything introduced by this service after it returns is rolled
        # back together: intent, analysis, execution, reservation, and outbox.
        db.rollback()
        raise

    if decision.dispatch_task_id is not None:
        try_publish_task_dispatch_safely(decision.dispatch_task_id)
    return _new_intent_result(intent=intent, queue_result=queue_result)


def continue_consultation_split_intent_as_one_note(
    db: Session,
    actor: User,
    *,
    transcript_id: UUID,
    intent_id: UUID,
) -> ContinueConsultationSplitIntentAsOneNoteResult:
    """Consume one saved split Generate action as one ordinary queued note.

    This intentionally has no capability-gate, credential, provider, route, or
    browser dependency.  A saved intent is a durable clinician action: later
    split preference or deployment changes cannot cancel it.  Every new row is
    flushed in one transaction before the dispatch receives a best-effort
    publish attempt.
    """
    _require_normal_owner(actor)
    try:
        # Preserve content-owner semantics before acquiring the durable source
        # serialization chain.  Administrators and team members never reach
        # transcript-derived state through this operation.
        # Inspect the caller-owned target before any query can autoflush it.
        # The service must fail closed on a dirty intent, rather than making a
        # caller mutation durable as a side effect of this initial lookup.
        # Leave the scope lock and all later work outside this block: service-
        # created rows retain normal autoflush and transaction semantics.
        with db.no_autoflush:
            intent_identity = db.scalar(
                select(ConsultationSplitIntent).where(
                    ConsultationSplitIntent.id == intent_id,
                    ConsultationSplitIntent.owner_user_id == actor.id,
                )
            )
            if intent_identity is None:
                raise AppError(404, "not_found", "Consultation split request not found")
            # This composable service owns the target intent's state transition.
            # ``populate_existing`` below would otherwise silently discard a
            # caller's unflushed mutation of that same row.  Callers must flush
            # or roll back target-intent edits before consume.
            if db.is_modified(intent_identity, include_collections=True):
                raise AppError(500, "consultation_split_intent_dirty", "Consultation split request is unavailable")
        # The nested route resource is part of the authority boundary.  Lock
        # the expected transcript scope before checking the locked intent, so
        # a route cannot validate one transcript then consume another.
        require_split_owner_transcript(db, actor, transcript_id=transcript_id)
        scope = lock_consultation_split_source_scope(
            db,
            owner_user_id=actor.id,
            transcript_id=transcript_id,
        )
        if scope is None or transcript_is_expired(scope.transcript):
            raise AppError(404, "not_found", "Transcript not found", {"resource": "transcript"})
        intent = db.scalar(
            select(ConsultationSplitIntent)
            .where(
                ConsultationSplitIntent.id == intent_id,
                ConsultationSplitIntent.transcript_id == transcript_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if intent is None:
            raise AppError(404, "not_found", "Consultation split request not found")
        # ``populate_existing`` refreshes scalar columns but not a relationship
        # that this Session previously loaded.  Expire it while the intent lock
        # is held so replay observes a concurrent bind or ON DELETE SET NULL.
        db.expire(intent, ["generated_document"])
        if (
            intent.owner_user_id != actor.id
            or intent.team_id != actor.team_id
            or intent.transcript_id != scope.transcript.id
            or intent.retention_expires_at != scope.transcript.retention_expires_at
        ):
            raise AppError(500, "consultation_split_scope_invalid", "Consultation split content is unavailable")
        # ``bypassed`` is terminal even if retention cleanup deleted the child.
        # Never recreate work from an intent whose one-note child was removed.
        if intent.status is ConsultationSplitIntentStatus.bypassed:
            if intent.generated_document is not None and (
                intent.generated_document.owner_user_id != intent.owner_user_id
                or intent.generated_document.team_id != intent.team_id
                or intent.generated_document.transcript_id != intent.transcript_id
            ):
                raise AppError(500, "consultation_split_scope_invalid", "Consultation split content is unavailable")
            db.commit()
            return _consumed_intent_result(intent)
        # A confirmed split owns immutable batch provenance.  It is a distinct
        # terminal decision, never an alternate route to ordinary generation.
        # Check before analysis/template/provider/quota work so confirmation
        # and bypass can never produce children for the same intent.
        if intent.status is ConsultationSplitIntentStatus.confirmed:
            raise AppError(409, "consultation_split_intent_confirmed", "This split request was already confirmed")
        if intent.analysis_id is None:
            raise AppError(409, "consultation_split_analysis_unavailable", "Consultation split analysis is unavailable")
        analysis = db.scalar(
            select(ConsultationSplitAnalysis)
            .where(ConsultationSplitAnalysis.id == intent.analysis_id)
            .with_for_update()
        )
        if analysis is None or (
            analysis.owner_user_id != intent.owner_user_id
            or analysis.team_id != intent.team_id
            or analysis.transcript_id != intent.transcript_id
            or analysis.retention_expires_at != intent.retention_expires_at
        ):
            raise AppError(500, "consultation_split_scope_invalid", "Consultation split content is unavailable")
        persisted_source_state = _analysis_source_state_for_consumption(db, analysis=analysis)
        if not current_consultation_split_analysis_clinical_source_matches(
            db,
            scope.owner,
            transcript=scope.transcript,
            analysis=analysis,
            persisted_source_state=persisted_source_state,
        ):
            raise AppError(409, "consultation_split_source_stale", "Consultation sources changed before generation")

        submitted = _submitted_generation_snapshot(db, intent=intent)
        _require_live_submitted_template(db, actor, submitted=submitted)
        _, config, resolved_model_name, _ = resolve_user_llm(db, actor)
        if not resolved_model_name:
            raise AppError(422, "business_rule_violation", "No active LLM model is configured for this user", {"field": "preferred_model_name"})

        working_mode, freeform_working_note, structured_working_note = _working_note_snapshot_for_transcript(
            db, transcript=scope.transcript
        )
        dictation_snapshot = _effective_dictation_text(db, transcript=scope.transcript)
        transcript_version = snapshot_current_transcript_version(
            db,
            transcript=scope.transcript,
            allow_empty=bool(freeform_working_note.strip()) or bool(structured_working_note) or bool(dictation_snapshot),
        )
        document = GeneratedDocument(
            id=uuid4(),
            owner_user_id=intent.owner_user_id,
            team_id=intent.team_id,
            transcript_id=intent.transcript_id,
            transcript_version_id=transcript_version.id,
            redaction_run_id=analysis.redaction_run_id,
            generator_type=GeneratedDocumentGeneratorType.template,
            template_version_id=submitted["version_id"],
            llm_config_id=config.id,
            source_template_name=submitted["name"],
            prompt_snapshot_text=submitted["prompt_text"],
            structured_context_json=None,
            generation_snapshot_json={
                NOTE_GENERATION_OPTIONS_SNAPSHOT_KEY: submitted["options"],
                "generation_wait_for_transcript": False,
                "submitted_template_config": submitted["config"],
            },
            working_note_mode_snapshot=working_mode,
            freeform_working_note_snapshot_encrypted=None,
            structured_working_note_snapshot_json=None,
            structured_section_definitions_json=submitted["structured_sections"],
            status=GeneratedDocumentStatus.queued,
            title=f"{submitted['name']} output",
            document_mode=submitted["mode"],
            original_output_text_encrypted="",
            edited_output_text_encrypted="",
            is_edited=False,
            retention_expires_at=intent.retention_expires_at,
            model_used=resolved_model_name,
            llm_adapter_kind=config.adapter_kind.value,
            llm_base_url=config.base_url,
            llm_provider_config_json=dict(config.provider_config_json or {}),
        )
        document.regeneration_lineage_id = document.id
        set_generated_document_text(
            db,
            document=document,
            field="freeform_working_note_snapshot_encrypted",
            plaintext=freeform_working_note if working_mode is TranscriptWorkingNoteMode.freeform else None,
        )
        set_generated_document_structured_working_note_snapshot(
            db,
            document=document,
            plaintext=structured_working_note if working_mode is TranscriptWorkingNoteMode.structured else None,
        )
        set_generated_document_text(db, document=document, field="dictation_snapshot_encrypted", plaintext=dictation_snapshot)
        set_generated_document_text(db, document=document, field="generation_steering_text_encrypted", plaintext="")
        set_generated_document_text(db, document=document, field="original_output_text_encrypted", plaintext="")
        set_generated_document_text(db, document=document, field="edited_output_text_encrypted", plaintext="")
        document, dispatch_task_id = _flush_generated_document_with_quota(
            db,
            document=document,
            transcript=scope.transcript,
            config=config,
        )
        intent.status = ConsultationSplitIntentStatus.bypassed
        intent.generated_document_id = document.id
        db.add(intent)
        db.commit()
    except Exception:
        db.rollback()
        raise
    try_publish_task_dispatch_safely(dispatch_task_id)
    db.refresh(document)
    db.refresh(intent)
    return ContinueConsultationSplitIntentAsOneNoteResult(intent=intent, document=document, created_new_document=True)
