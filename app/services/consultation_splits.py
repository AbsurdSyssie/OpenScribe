"""Passive, owner-only persistence boundary for consultation splitting.

This module deliberately has no routes, tasks, provider calls, or state-machine
transitions.  It exists so later slices cannot create split rows without the
same ownership, retention, and encryption checks.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    ConsultationSplitAnalysis,
    ConsultationSplitAnalysisStatus,
    ConsultationSplitBatch,
    ConsultationSplitBatchStatus,
    ConsultationSplitBatchTopic,
    ConsultationSplitDraft,
    ConsultationSplitDraftStatus,
    ConsultationSplitDraftTopic,
    ConsultationSplitExecution,
    ConsultationSplitExecutionKind,
    ConsultationSplitExecutionStatus,
    ConsultationSplitTopicDisposition,
    ConsultationSplitTopicOutcome,
    ConsultationSplitTopicOutcomeStatus,
    AttemptKind,
    AttemptStatus,
    ProviderFeatureType,
    ProviderAttempt,
    PromptTemplate,
    PromptTemplateVersion,
    RedactionRun,
    TemplateScope,
    TeamLlmConfig,
    Transcript,
    TranscriptVersion,
    TaskDispatchKind,
    TaskDispatchState,
    TaskDispatchOutbox,
    TaskDispatchSourceKind,
    QuotaResource,
    User,
    utcnow,
)
from app.services.content_crypto import (
    decrypt_json_for_owner,
    decrypt_text_for_owner,
    encrypt_json_for_owner,
    encrypt_text_for_owner,
    is_encrypted_envelope,
)
from app.services.llm_adapters.runtime import (
    request_output_token_cap,
    snapshot_contains_secret_key,
    validate_provider_snapshot_for_config,
)
from app.services.quotas import cancel_provider_attempt, reserve_provider_attempt
from app.services.task_outbox import add_pending_task_dispatch, cancel_pending_task_dispatch
from app.services.transcripts import get_active_owner_transcript, transcript_is_expired


_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_TABLE_ANALYSIS = "consultation_split_analyses"
_TABLE_DRAFT_TOPIC = "consultation_split_draft_topics"
_TABLE_BATCH = "consultation_split_batches"
_TABLE_BATCH_TOPIC = "consultation_split_batch_topics"
_TABLE_OUTCOME = "consultation_split_topic_outcomes"
_TABLE_EXECUTION = "consultation_split_executions"


_SPLIT_EXECUTION_PHASES: dict[
    ConsultationSplitExecutionKind,
    tuple[AttemptKind, ProviderFeatureType, TaskDispatchKind],
] = {
    ConsultationSplitExecutionKind.analysis: (
        AttemptKind.consultation_split_analysis,
        ProviderFeatureType.consultation_split_analysis,
        TaskDispatchKind.consultation_split_analysis,
    ),
    ConsultationSplitExecutionKind.generation: (
        AttemptKind.consultation_split_generation,
        ProviderFeatureType.consultation_split_generation,
        TaskDispatchKind.consultation_split_generation,
    ),
    ConsultationSplitExecutionKind.verification: (
        AttemptKind.consultation_split_verification,
        ProviderFeatureType.consultation_split_verification,
        TaskDispatchKind.consultation_split_verification,
    ),
}


def split_execution_phase_mapping(
    kind: ConsultationSplitExecutionKind,
) -> tuple[AttemptKind, ProviderFeatureType, TaskDispatchKind]:
    """Return the one-to-one metadata mapping for a split provider phase."""
    try:
        return _SPLIT_EXECUTION_PHASES[kind]
    except KeyError as exc:
        raise AppError(422, "consultation_split_execution_kind_invalid", "Split execution kind is invalid") from exc


def _forbidden(message: str = "Consultation split content is restricted to normal team users") -> AppError:
    return AppError(403, "forbidden", message)


def _internal_scope_error() -> AppError:
    return AppError(500, "consultation_split_scope_invalid", "Consultation split content is unavailable")


def _require_normal_team_owner(actor: User) -> None:
    if actor.is_system_admin or actor.team_id is None:
        raise _forbidden()


def _validate_fingerprint(value: str) -> str:
    if not isinstance(value, str) or _FINGERPRINT.fullmatch(value) is None:
        raise AppError(422, "validation_error", "Source fingerprint must be a lowercase SHA-256 digest")
    return value


def _require_json(value: Any, *, field: str, allow_list: bool = False) -> dict[str, Any] | list[Any]:
    if isinstance(value, dict) or (allow_list and isinstance(value, list)):
        return value
    expected = "object or list" if allow_list else "object"
    raise AppError(422, "validation_error", f"{field} must be a JSON {expected}")


def _require_title(value: str) -> str:
    title = " ".join(value.split()) if isinstance(value, str) else ""
    if not title or len(title) > 255:
        raise AppError(422, "validation_error", "Topic title must contain at most 255 characters")
    return title


def require_split_owner_transcript(db: Session, actor: User, *, transcript_id: UUID) -> Transcript:
    """Return only an active transcript whose owner and team still match actor."""
    _require_normal_team_owner(actor)
    transcript = get_active_owner_transcript(db, actor, transcript_id=transcript_id)
    if transcript.team_id != actor.team_id:
        # This should be impossible for a healthy root, so do not reveal it.
        raise _internal_scope_error()
    return transcript


def _validate_transcript_version(db: Session, *, transcript: Transcript, transcript_version_id: UUID | None) -> TranscriptVersion | None:
    if transcript_version_id is None:
        return None
    version = db.get(TranscriptVersion, transcript_version_id)
    if version is None or version.transcript_id != transcript.id:
        raise AppError(422, "validation_error", "Transcript version does not belong to this consultation")
    return version


def _validate_redaction_run(
    db: Session, *, transcript: Transcript, transcript_version: TranscriptVersion | None, redaction_run_id: UUID | None
) -> RedactionRun | None:
    if redaction_run_id is None:
        return None
    run = db.get(RedactionRun, redaction_run_id)
    if run is None or run.transcript_id != transcript.id or run.owner_user_id != transcript.owner_user_id or run.team_id != transcript.team_id:
        raise AppError(422, "validation_error", "Redaction run does not belong to this consultation")
    if transcript_version is None or run.transcript_version_id != transcript_version.id:
        raise AppError(422, "validation_error", "Redaction run does not match the transcript version")
    return run


def _require_split_llm_config(
    db: Session,
    *,
    transcript: Transcript,
    llm_config_id: UUID | None,
) -> TeamLlmConfig:
    """Return a durable config identity usable by a production split execution.

    Eligibility and model selection remain the later provider-execution
    boundary.  This persistence boundary only proves that the execution is
    attached to a real config in the transcript's team.
    """
    if llm_config_id is None:
        raise AppError(422, "consultation_split_llm_config_required", "A consultation split LLM config is required")
    # Serialize this validation with config deletion.  Once this lock is held,
    # deletion either observes the queued execution or completes first, in
    # which case this lookup fails rather than queueing a detached row.
    config = db.scalar(
        select(TeamLlmConfig).where(TeamLlmConfig.id == llm_config_id).with_for_update()
    )
    if config is None or config.team_id != transcript.team_id:
        raise AppError(422, "consultation_split_llm_config_invalid", "The consultation split LLM config is unavailable")
    return config


def _validate_split_provider_snapshot(
    provider_snapshot: dict[str, Any] | None,
    *,
    llm_config_id: UUID,
    config: TeamLlmConfig,
    expected_model: str | None = None,
) -> dict[str, Any] | None:
    """Validate the non-secret execution snapshot before it is encrypted.

    The later runtime constructs this with ``LlmProviderSnapshot.to_dict``.
    Keeping the config ID in both slots detects accidental retargeting without
    placing a Vault reference or credential on the execution.
    """
    if provider_snapshot is None:
        raise AppError(
            422,
            "consultation_split_provider_snapshot_required",
            "A consultation split provider snapshot is required",
        )
    snapshot = _require_json(provider_snapshot, field="provider snapshot")
    assert isinstance(snapshot, dict)
    snapshot_config_id = snapshot.get("llm_config_id")
    try:
        parsed_snapshot_config_id = UUID(snapshot_config_id) if isinstance(snapshot_config_id, str) else None
    except ValueError:
        parsed_snapshot_config_id = None
    if parsed_snapshot_config_id != llm_config_id:
        raise AppError(
            422,
            "consultation_split_provider_snapshot_config_mismatch",
            "The consultation split provider snapshot does not match its LLM config",
        )
    try:
        return validate_provider_snapshot_for_config(snapshot, config=config, expected_model=expected_model)
    except AppError as exc:
        if exc.code == "llm_provider_snapshot_config_mismatch":
            raise AppError(
                422,
                "consultation_split_provider_snapshot_config_mismatch",
                "The consultation split provider snapshot does not match its LLM config",
            ) from exc
        raise AppError(
            422,
            "consultation_split_provider_snapshot_invalid",
            "The provider snapshot is invalid",
        ) from exc


def _scope_matches_transcript(row: Any, transcript: Transcript) -> bool:
    return (
        row.owner_user_id == transcript.owner_user_id
        and row.team_id == transcript.team_id
        and row.transcript_id == transcript.id
        and row.retention_expires_at == transcript.retention_expires_at
    )


def _require_parent_scope(db: Session, actor: User, parent: Any) -> Transcript:
    transcript = require_split_owner_transcript(db, actor, transcript_id=parent.transcript_id)
    if not _scope_matches_transcript(parent, transcript):
        raise _internal_scope_error()
    return transcript


def _require_lineage(db: Session, actor: User, row: Any) -> Transcript:
    """Validate a persisted split row and every ancestor before use.

    Caller-selected references are checked at analysis creation and receive 422.
    Once a split row exists, a broken denormalized chain is internal corruption
    and must fail closed without exposing patient content.
    """
    transcript = _require_parent_scope(db, actor, row)
    if isinstance(row, ConsultationSplitAnalysis):
        try:
            _validate_fingerprint(row.source_fingerprint)
        except AppError as exc:
            raise _internal_scope_error() from exc
        version = db.get(TranscriptVersion, row.transcript_version_id) if row.transcript_version_id else None
        if row.transcript_version_id is not None and (version is None or version.transcript_id != transcript.id):
            raise _internal_scope_error()
        run = db.get(RedactionRun, row.redaction_run_id) if row.redaction_run_id else None
        if row.redaction_run_id is not None and (
            run is None or version is None or run.transcript_id != transcript.id
            or run.owner_user_id != transcript.owner_user_id or run.team_id != transcript.team_id
            or run.transcript_version_id != version.id
        ):
            raise _internal_scope_error()
        return transcript

    parent: Any | None = None
    if isinstance(row, ConsultationSplitDraft):
        parent = db.get(ConsultationSplitAnalysis, row.analysis_id)
    elif isinstance(row, ConsultationSplitBatch):
        parent = db.get(ConsultationSplitAnalysis, row.analysis_id)
    elif isinstance(row, ConsultationSplitDraftTopic):
        parent = db.get(ConsultationSplitDraft, row.draft_id)
    elif isinstance(row, ConsultationSplitBatchTopic):
        parent = db.get(ConsultationSplitBatch, row.batch_id)
    elif isinstance(row, ConsultationSplitTopicOutcome):
        parent = db.get(ConsultationSplitBatchTopic, row.batch_topic_id)
    elif isinstance(row, ConsultationSplitExecution):
        if (row.analysis_id is None) == (row.batch_id is None):
            raise _internal_scope_error()
        parent = db.get(ConsultationSplitAnalysis, row.analysis_id) if row.analysis_id else db.get(ConsultationSplitBatch, row.batch_id)
    else:
        raise _internal_scope_error()
    if parent is None or not _scope_matches_transcript(parent, transcript):
        raise _internal_scope_error()
    _require_lineage(db, actor, parent)
    if isinstance(row, (ConsultationSplitDraft, ConsultationSplitBatch)) and row.source_fingerprint != parent.source_fingerprint:
        raise _internal_scope_error()
    return transcript


def _require_available_template_pair(
    db: Session, actor: User, *, template_id: UUID | None, template_version_id: UUID | None
) -> tuple[PromptTemplate | None, PromptTemplateVersion | None]:
    if template_id is None and template_version_id is None:
        return None, None
    if template_id is None or template_version_id is None:
        raise AppError(422, "validation_error", "Template and template version must be chosen together")
    template = db.get(PromptTemplate, template_id)
    version = db.get(PromptTemplateVersion, template_version_id)
    is_available = template is not None and template.is_active and (
        (template.scope is TemplateScope.user and template.owner_user_id == actor.id)
        or (template.scope is TemplateScope.team and template.team_id == actor.team_id)
    )
    if not is_available or version is None or version.template_id != template_id:
        raise AppError(422, "validation_error", "Template selection is not available")
    return template, version


def create_split_analysis(
    db: Session,
    actor: User,
    *,
    transcript_id: UUID,
    source_fingerprint: str,
    transcript_version_id: UUID | None = None,
    redaction_run_id: UUID | None = None,
    source_snapshot: dict[str, Any] | None = None,
    candidate_template_snapshot: dict[str, Any] | None = None,
    provider_snapshot: dict[str, Any] | None = None,
    proposal: dict[str, Any] | None = None,
) -> ConsultationSplitAnalysis:
    transcript = require_split_owner_transcript(db, actor, transcript_id=transcript_id)
    fingerprint = _validate_fingerprint(source_fingerprint)
    version = _validate_transcript_version(db, transcript=transcript, transcript_version_id=transcript_version_id)
    _validate_redaction_run(db, transcript=transcript, transcript_version=version, redaction_run_id=redaction_run_id)
    analysis = ConsultationSplitAnalysis(
        id=uuid4(), owner_user_id=transcript.owner_user_id, team_id=transcript.team_id,
        transcript_id=transcript.id, transcript_version_id=transcript_version_id, redaction_run_id=redaction_run_id,
        source_fingerprint=fingerprint, status=ConsultationSplitAnalysisStatus.queued, retention_expires_at=transcript.retention_expires_at,
    )
    analysis.source_snapshot_encrypted = encrypt_json_for_owner(db, owner_user_id=analysis.owner_user_id,
        table=_TABLE_ANALYSIS, field="source_snapshot_encrypted", record_id=analysis.id,
        plaintext=_require_json(source_snapshot, field="source snapshot") if source_snapshot is not None else None)
    analysis.candidate_template_snapshot_encrypted = encrypt_json_for_owner(db, owner_user_id=analysis.owner_user_id,
        table=_TABLE_ANALYSIS, field="candidate_template_snapshot_encrypted", record_id=analysis.id,
        plaintext=_require_json(candidate_template_snapshot, field="candidate template snapshot") if candidate_template_snapshot is not None else None)
    analysis.provider_snapshot_encrypted = encrypt_json_for_owner(db, owner_user_id=analysis.owner_user_id,
        table=_TABLE_ANALYSIS, field="provider_snapshot_encrypted", record_id=analysis.id,
        plaintext=_require_json(provider_snapshot, field="provider snapshot") if provider_snapshot is not None else None)
    analysis.proposal_encrypted = encrypt_json_for_owner(db, owner_user_id=analysis.owner_user_id,
        table=_TABLE_ANALYSIS, field="proposal_encrypted", record_id=analysis.id,
        plaintext=_require_json(proposal, field="proposal") if proposal is not None else None)
    db.add(analysis)
    db.flush()
    return analysis


def create_split_draft(db: Session, actor: User, *, analysis: ConsultationSplitAnalysis) -> ConsultationSplitDraft:
    transcript = _require_lineage(db, actor, analysis)
    draft = ConsultationSplitDraft(
        id=uuid4(), analysis_id=analysis.id, owner_user_id=transcript.owner_user_id, team_id=transcript.team_id,
        transcript_id=transcript.id, source_fingerprint=analysis.source_fingerprint,
        status=ConsultationSplitDraftStatus.active, retention_expires_at=transcript.retention_expires_at,
    )
    db.add(draft)
    db.flush()
    return draft


def create_split_draft_topic(
    db: Session, actor: User, *, draft: ConsultationSplitDraft, title: str, topic_order: int,
    is_primary: bool, disposition: ConsultationSplitTopicDisposition, template_id: UUID | None = None,
    template_version_id: UUID | None = None, topic_uuid: UUID | None = None,
) -> ConsultationSplitDraftTopic:
    transcript = _require_lineage(db, actor, draft)
    _require_available_template_pair(db, actor, template_id=template_id, template_version_id=template_version_id)
    if not 0 <= topic_order <= 5:
        raise AppError(422, "validation_error", "Topic order must be between 0 and 5")
    if is_primary and disposition is not ConsultationSplitTopicDisposition.separate_note:
        raise AppError(422, "validation_error", "Primary topic must be a separate note")
    row = ConsultationSplitDraftTopic(
        id=uuid4(), draft_id=draft.id, owner_user_id=transcript.owner_user_id, team_id=transcript.team_id,
        transcript_id=transcript.id, topic_uuid=topic_uuid or uuid4(), topic_order=topic_order, is_primary=is_primary,
        disposition=disposition, template_id=template_id, template_version_id=template_version_id,
        retention_expires_at=transcript.retention_expires_at,
    )
    row.title_encrypted = encrypt_text_for_owner(
        db, owner_user_id=row.owner_user_id, table=_TABLE_DRAFT_TOPIC, field="title_encrypted", record_id=row.id,
        plaintext=_require_title(title),
    ) or ""
    db.add(row)
    db.flush()
    return row


def create_split_batch(
    db: Session, actor: User, *, analysis: ConsultationSplitAnalysis, intent_id: UUID | None = None, confirmed_plan: dict[str, Any],
    clinical_snapshot: dict[str, Any], source_snapshot: dict[str, Any], template_snapshot: dict[str, Any],
    pii_snapshot: dict[str, Any], provider_snapshot: dict[str, Any], note_options_snapshot: dict[str, Any],
    materialization_transcript_version_id: UUID | None = None,
) -> ConsultationSplitBatch:
    transcript = _require_lineage(db, actor, analysis)
    materialization_version = _validate_transcript_version(
        db,
        transcript=transcript,
        transcript_version_id=materialization_transcript_version_id,
    )
    if materialization_version is None:
        raise AppError(
            422,
            "consultation_split_materialization_version_required",
            "A consultation split batch requires a materialization transcript version",
        )
    batch = ConsultationSplitBatch(
        id=uuid4(), intent_id=intent_id, analysis_id=analysis.id, owner_user_id=transcript.owner_user_id, team_id=transcript.team_id,
        transcript_id=transcript.id, materialization_transcript_version_id=materialization_version.id,
        source_fingerprint=analysis.source_fingerprint,
        status=ConsultationSplitBatchStatus.generation_queued,
        retention_expires_at=transcript.retention_expires_at,
    )
    batch.confirmed_plan_encrypted = encrypt_json_for_owner(db, owner_user_id=batch.owner_user_id, table=_TABLE_BATCH,
        field="confirmed_plan_encrypted", record_id=batch.id, plaintext=_require_json(confirmed_plan, field="confirmed plan")) or ""
    batch.clinical_snapshot_encrypted = encrypt_json_for_owner(db, owner_user_id=batch.owner_user_id, table=_TABLE_BATCH,
        field="clinical_snapshot_encrypted", record_id=batch.id, plaintext=_require_json(clinical_snapshot, field="clinical snapshot")) or ""
    batch.source_snapshot_encrypted = encrypt_json_for_owner(db, owner_user_id=batch.owner_user_id, table=_TABLE_BATCH,
        field="source_snapshot_encrypted", record_id=batch.id, plaintext=_require_json(source_snapshot, field="source snapshot")) or ""
    batch.template_snapshot_encrypted = encrypt_json_for_owner(db, owner_user_id=batch.owner_user_id, table=_TABLE_BATCH,
        field="template_snapshot_encrypted", record_id=batch.id, plaintext=_require_json(template_snapshot, field="template snapshot")) or ""
    batch.pii_snapshot_encrypted = encrypt_json_for_owner(db, owner_user_id=batch.owner_user_id, table=_TABLE_BATCH,
        field="pii_snapshot_encrypted", record_id=batch.id, plaintext=_require_json(pii_snapshot, field="PII snapshot")) or ""
    batch.provider_snapshot_encrypted = encrypt_json_for_owner(db, owner_user_id=batch.owner_user_id, table=_TABLE_BATCH,
        field="provider_snapshot_encrypted", record_id=batch.id, plaintext=_require_json(provider_snapshot, field="provider snapshot")) or ""
    batch.note_options_snapshot_encrypted = encrypt_json_for_owner(db, owner_user_id=batch.owner_user_id, table=_TABLE_BATCH,
        field="note_options_snapshot_encrypted", record_id=batch.id, plaintext=_require_json(note_options_snapshot, field="note options snapshot")) or ""
    db.add(batch)
    db.flush()
    return batch


def create_split_batch_topic(
    db: Session, actor: User, *, batch: ConsultationSplitBatch, title: str, topic_order: int, is_primary: bool,
    disposition: ConsultationSplitTopicDisposition, template_snapshot: dict[str, Any], topic_uuid: UUID | None = None,
) -> ConsultationSplitBatchTopic:
    transcript = _require_lineage(db, actor, batch)
    if not 0 <= topic_order <= 5:
        raise AppError(422, "validation_error", "Topic order must be between 0 and 5")
    if is_primary and disposition is not ConsultationSplitTopicDisposition.separate_note:
        raise AppError(422, "validation_error", "Primary topic must be a separate note")
    row = ConsultationSplitBatchTopic(
        id=uuid4(), batch_id=batch.id, owner_user_id=transcript.owner_user_id, team_id=transcript.team_id,
        transcript_id=transcript.id, topic_uuid=topic_uuid or uuid4(), topic_order=topic_order, is_primary=is_primary,
        disposition=disposition, retention_expires_at=transcript.retention_expires_at,
    )
    row.title_encrypted = encrypt_text_for_owner(db, owner_user_id=row.owner_user_id, table=_TABLE_BATCH_TOPIC,
        field="title_encrypted", record_id=row.id, plaintext=_require_title(title)) or ""
    row.template_snapshot_encrypted = encrypt_json_for_owner(db, owner_user_id=row.owner_user_id, table=_TABLE_BATCH_TOPIC,
        field="template_snapshot_encrypted", record_id=row.id, plaintext=_require_json(template_snapshot, field="template snapshot")) or ""
    db.add(row)
    db.flush()
    return row


def create_split_topic_outcome(
    db: Session, actor: User, *, batch_topic: ConsultationSplitBatchTopic,
    output: dict[str, Any] | list[Any] | None = None,
) -> ConsultationSplitTopicOutcome:
    transcript = _require_lineage(db, actor, batch_topic)
    row = ConsultationSplitTopicOutcome(
        id=uuid4(), batch_topic_id=batch_topic.id, owner_user_id=transcript.owner_user_id, team_id=transcript.team_id,
        transcript_id=transcript.id, status=ConsultationSplitTopicOutcomeStatus.pending,
        retention_expires_at=transcript.retention_expires_at,
    )
    row.output_encrypted = encrypt_json_for_owner(db, owner_user_id=row.owner_user_id, table=_TABLE_OUTCOME,
        field="output_encrypted", record_id=row.id,
        plaintext=_require_json(output, field="topic output", allow_list=True) if output is not None else None)
    db.add(row)
    db.flush()
    return row


def _create_split_analysis_execution_for_test(
    db: Session, actor: User, *, analysis: ConsultationSplitAnalysis, attempt_no: int,
    provider_snapshot: dict[str, Any] | None = None, request_payload: dict[str, Any] | list[Any] | None = None,
    recoverable_response: dict[str, Any] | list[Any] | None = None,
) -> ConsultationSplitExecution:
    """Create passive data for tests only.

    Production work must use :func:`queue_split_execution`, which creates the
    execution, its quota reservation, and durable dispatch intent together.
    """
    return _create_split_execution(db, actor, analysis=analysis, batch=None, kind=ConsultationSplitExecutionKind.analysis,
        attempt_no=attempt_no, provider_snapshot=provider_snapshot, request_payload=request_payload,
        recoverable_response=recoverable_response)


def _create_split_batch_execution_for_test(
    db: Session, actor: User, *, batch: ConsultationSplitBatch, kind: ConsultationSplitExecutionKind, attempt_no: int,
    provider_snapshot: dict[str, Any] | None = None, request_payload: dict[str, Any] | list[Any] | None = None,
    recoverable_response: dict[str, Any] | list[Any] | None = None,
) -> ConsultationSplitExecution:
    """Create passive data for tests only; see ``queue_split_execution``."""
    if kind not in {ConsultationSplitExecutionKind.generation, ConsultationSplitExecutionKind.verification}:
        raise AppError(422, "validation_error", "Batch executions must be generation or verification")
    return _create_split_execution(db, actor, analysis=None, batch=batch, kind=kind, attempt_no=attempt_no,
        provider_snapshot=provider_snapshot, request_payload=request_payload, recoverable_response=recoverable_response)


def _create_split_execution(
    db: Session, actor: User, *, analysis: ConsultationSplitAnalysis | None, batch: ConsultationSplitBatch | None,
    kind: ConsultationSplitExecutionKind, attempt_no: int, provider_snapshot: dict[str, Any] | None,
    request_payload: dict[str, Any] | list[Any] | None, recoverable_response: dict[str, Any] | list[Any] | None,
) -> ConsultationSplitExecution:
    parent = analysis or batch
    if parent is None or (analysis is not None and batch is not None) or attempt_no < 1:
        raise AppError(422, "validation_error", "Split execution parent or attempt is invalid")
    transcript = _require_lineage(db, actor, parent)
    row = ConsultationSplitExecution(
        id=uuid4(), analysis_id=analysis.id if analysis else None, batch_id=batch.id if batch else None,
        owner_user_id=transcript.owner_user_id, team_id=transcript.team_id, transcript_id=transcript.id,
        kind=kind, attempt_no=attempt_no, status=ConsultationSplitExecutionStatus.queued,
        retention_expires_at=transcript.retention_expires_at,
    )
    row.provider_snapshot_encrypted = encrypt_json_for_owner(db, owner_user_id=row.owner_user_id, table=_TABLE_EXECUTION,
        field="provider_snapshot_encrypted", record_id=row.id,
        plaintext=_require_json(provider_snapshot, field="provider snapshot") if provider_snapshot is not None else None)
    row.request_payload_encrypted = encrypt_json_for_owner(db, owner_user_id=row.owner_user_id, table=_TABLE_EXECUTION,
        field="request_payload_encrypted", record_id=row.id,
        plaintext=_require_json(request_payload, field="request payload", allow_list=True) if request_payload is not None else None)
    row.recoverable_response_encrypted = encrypt_json_for_owner(db, owner_user_id=row.owner_user_id, table=_TABLE_EXECUTION,
        field="recoverable_response_encrypted", record_id=row.id,
        plaintext=_require_json(recoverable_response, field="recoverable response", allow_list=True) if recoverable_response is not None else None)
    db.add(row)
    db.flush()
    return row


def _locked_split_parent(
    db: Session,
    actor: User,
    *,
    analysis: ConsultationSplitAnalysis | None,
    batch: ConsultationSplitBatch | None,
    kind: ConsultationSplitExecutionKind,
) -> tuple[ConsultationSplitAnalysis | ConsultationSplitBatch, Transcript]:
    """Lock User -> Transcript -> split parent before allocating an attempt."""
    if (analysis is None) == (batch is None):
        raise AppError(422, "validation_error", "Split execution parent is invalid")
    if analysis is not None and kind is not ConsultationSplitExecutionKind.analysis:
        raise AppError(422, "validation_error", "Analysis executions must use the analysis phase")
    if batch is not None and kind not in {
        ConsultationSplitExecutionKind.generation,
        ConsultationSplitExecutionKind.verification,
    }:
        raise AppError(422, "validation_error", "Batch executions must use generation or verification")

    parent = analysis or batch
    assert parent is not None
    transcript = _require_lineage(db, actor, parent)
    owner = db.scalar(select(User).where(User.id == transcript.owner_user_id).with_for_update())
    if owner is None or owner.id != actor.id or owner.team_id != transcript.team_id or owner.is_system_admin:
        raise _internal_scope_error()
    locked_transcript = db.scalar(select(Transcript).where(Transcript.id == transcript.id).with_for_update())
    if locked_transcript is None or locked_transcript.retention_expires_at != transcript.retention_expires_at:
        raise _internal_scope_error()
    if analysis is not None:
        locked_parent = db.scalar(
            select(ConsultationSplitAnalysis)
            .where(ConsultationSplitAnalysis.id == analysis.id)
            .with_for_update()
        )
    else:
        locked_parent = db.scalar(
            select(ConsultationSplitBatch)
            .where(ConsultationSplitBatch.id == batch.id)
            .with_for_update()
        )
    if locked_parent is None:
        raise _internal_scope_error()
    _require_lineage(db, actor, locked_parent)
    return locked_parent, locked_transcript


def queue_split_execution(
    db: Session,
    actor: User,
    *,
    kind: ConsultationSplitExecutionKind,
    reserved_units: int,
    reservation_valid_until: datetime,
    analysis: ConsultationSplitAnalysis | None = None,
    batch: ConsultationSplitBatch | None = None,
    llm_config_id: UUID | None = None,
    provider_adapter: str | None = None,
    provider_model: str | None = None,
    provider_snapshot: dict[str, Any] | None = None,
    request_payload: dict[str, Any] | None = None,
    expected_model: str | None = None,
) -> tuple[ConsultationSplitExecution, ProviderAttempt, TaskDispatchOutbox]:
    """Queue one split phase with its reservation and durable outbox intent.

    This function flushes only.  Its caller must commit the transaction before
    using ``try_publish_task_dispatch_safely`` with the returned dispatch task
    id.  Provider credentials never cross this boundary; the request payload
    is transcript-derived content and is owner-encrypted before persistence.
    """
    if reserved_units <= 0:
        raise AppError(422, "validation_error", "Split quota estimate must be positive")
    attempt_kind, _feature_type, dispatch_kind = split_execution_phase_mapping(kind)
    parent, transcript = _locked_split_parent(
        db, actor, analysis=analysis, batch=batch, kind=kind
    )
    config = _require_split_llm_config(db, transcript=transcript, llm_config_id=llm_config_id)
    snapshot = _validate_split_provider_snapshot(
        provider_snapshot, llm_config_id=config.id, config=config, expected_model=expected_model
    )
    assert snapshot is not None
    snapshot_adapter = str(snapshot["adapter_kind"])
    snapshot_base_url = str(snapshot["base_url"])
    snapshot_model = str(snapshot["model"])
    if provider_adapter is not None and provider_adapter != snapshot_adapter:
        raise AppError(422, "consultation_split_provider_snapshot_metadata_mismatch", "Split provider metadata does not match its snapshot")
    if provider_model is not None and provider_model != snapshot_model:
        raise AppError(422, "consultation_split_provider_snapshot_metadata_mismatch", "Split provider metadata does not match its snapshot")
    if not isinstance(request_payload, dict) or not request_payload or snapshot_contains_secret_key(request_payload):
        raise AppError(422, "consultation_split_request_payload_required", "A consultation split request payload is required")
    request_model = request_payload.get("model")
    request_cap = request_output_token_cap(request_payload)
    if request_model != snapshot_model or not isinstance(request_cap, int) or isinstance(request_cap, bool) or request_cap <= 0:
        raise AppError(422, "consultation_split_request_payload_invalid", "The consultation split request payload is invalid")
    # Keep all durable queue records inside one savepoint.  The reservation
    # helper has its own nested savepoint for an idempotency race, which is
    # safe inside this enclosing boundary.  A caller can catch a quota or
    # reservation error and still commit unrelated outer-transaction work.
    with db.begin_nested():
        existing_attempt_no = db.scalar(
            select(func.max(ConsultationSplitExecution.attempt_no)).where(
                ConsultationSplitExecution.analysis_id == parent.id
                if isinstance(parent, ConsultationSplitAnalysis)
                else ConsultationSplitExecution.batch_id == parent.id,
                ConsultationSplitExecution.kind == kind,
            )
        )
        attempt_no = int(existing_attempt_no or 0) + 1
        execution = ConsultationSplitExecution(
            id=uuid4(),
            analysis_id=parent.id if isinstance(parent, ConsultationSplitAnalysis) else None,
            batch_id=parent.id if isinstance(parent, ConsultationSplitBatch) else None,
            owner_user_id=transcript.owner_user_id,
            team_id=transcript.team_id,
            transcript_id=transcript.id,
            llm_config_id=config.id,
            kind=kind,
            attempt_no=attempt_no,
            status=ConsultationSplitExecutionStatus.queued,
            provider_adapter=snapshot_adapter,
            provider_base_url=snapshot_base_url,
            provider_model=snapshot_model,
            retention_expires_at=transcript.retention_expires_at,
        )
        execution.provider_snapshot_encrypted = encrypt_json_for_owner(
            db,
            owner_user_id=execution.owner_user_id,
            table=_TABLE_EXECUTION,
            field="provider_snapshot_encrypted",
            record_id=execution.id,
            plaintext=snapshot,
        )
        execution.request_payload_encrypted = encrypt_json_for_owner(
            db,
            owner_user_id=execution.owner_user_id,
            table=_TABLE_EXECUTION,
            field="request_payload_encrypted",
            record_id=execution.id,
            plaintext=request_payload,
        )
        db.add(execution)
        db.flush()
        attempt = reserve_provider_attempt(
            db,
            team_id=transcript.team_id,
            owner_user_id=transcript.owner_user_id,
            resource=QuotaResource.tokens,
            attempt_kind=attempt_kind,
            correlation_id=execution.id,
            attempt_number=1,
            reserved_units=reserved_units,
            reservation_valid_until=reservation_valid_until,
            transcript_id=transcript.id,
            consultation_split_execution_id=execution.id,
            provider_adapter=snapshot_adapter,
            provider_model=snapshot_model,
        )
        dispatch = add_pending_task_dispatch(
            db,
            dispatch_kind=dispatch_kind,
            source_id=execution.id,
        )
    return execution, attempt, dispatch


def _validate_split_execution_for_runtime(
    db: Session,
    *,
    execution_id: UUID,
    expected_execution_status: ConsultationSplitExecutionStatus,
    expected_attempt_status: AttemptStatus,
) -> bool:
    """Validate one split execution without claiming or changing state."""
    now = utcnow()
    identity = db.scalar(
        select(ConsultationSplitExecution).where(ConsultationSplitExecution.id == execution_id)
    )
    if identity is None:
        return False
    # Blocking locks are deliberate here.  This task does not claim work, so
    # contention must wait rather than masquerade as a missing row/success.
    owner = db.scalar(select(User).where(User.id == identity.owner_user_id).with_for_update())
    if owner is None:
        return False
    transcript = db.scalar(
        select(Transcript)
        .where(Transcript.id == identity.transcript_id)
        .with_for_update()
    )
    if transcript is None or transcript_is_expired(transcript, now=now):
        return False
    execution = db.scalar(
        select(ConsultationSplitExecution)
        .where(ConsultationSplitExecution.id == execution_id)
        .with_for_update()
    )
    if execution is None or execution.status is not expected_execution_status:
        return False
    if execution.kind is ConsultationSplitExecutionKind.analysis:
        if execution.analysis_id is None or execution.batch_id is not None:
            return False
        parent = db.scalar(
            select(ConsultationSplitAnalysis)
            .where(ConsultationSplitAnalysis.id == execution.analysis_id)
            .with_for_update()
        )
    elif execution.kind in {
        ConsultationSplitExecutionKind.generation,
        ConsultationSplitExecutionKind.verification,
    }:
        if execution.analysis_id is not None or execution.batch_id is None:
            return False
        parent = db.scalar(
            select(ConsultationSplitBatch)
            .where(ConsultationSplitBatch.id == execution.batch_id)
            .with_for_update()
        )
    else:
        return False
    if parent is None or not _scope_matches_transcript(parent, transcript):
        return False
    try:
        _require_lineage(db, owner, parent)
    except AppError:
        return False
    if (
        execution.owner_user_id != owner.id
        or execution.team_id != owner.team_id
        or owner.team_id != transcript.team_id
        or execution.transcript_id != transcript.id
        or execution.retention_expires_at != transcript.retention_expires_at
    ):
        return False
    config = db.get(TeamLlmConfig, execution.llm_config_id) if execution.llm_config_id is not None else None
    if config is None or config.team_id != transcript.team_id:
        return False
    if execution.provider_snapshot_encrypted is None:
        return False
    try:
        snapshot = read_split_execution_json(
            db,
            owner,
            execution=execution,
            field="provider_snapshot_encrypted",
        )
        if not isinstance(snapshot, dict):
            return False
        _validate_split_provider_snapshot(
            snapshot,
            llm_config_id=config.id,
            config=config,
            expected_model=execution.provider_model,
        )
        if (
            execution.provider_adapter != snapshot["adapter_kind"]
            or execution.provider_base_url != snapshot["base_url"]
            or execution.provider_model != snapshot["model"]
        ):
            return False
        request_payload = read_split_execution_json(
            db,
            owner,
            execution=execution,
            field="request_payload_encrypted",
        )
        request_cap = request_output_token_cap(request_payload) if isinstance(request_payload, dict) else None
        if (
            not isinstance(request_payload, dict)
            or not request_payload
            or request_payload.get("model") != snapshot["model"]
            or not isinstance(request_cap, int)
            or isinstance(request_cap, bool)
            or request_cap <= 0
        ):
            return False
    except AppError:
        return False
    attempt = db.scalar(
        select(ProviderAttempt)
        .where(ProviderAttempt.consultation_split_execution_id == execution.id)
        .with_for_update()
    )
    if attempt is None or attempt.status is not expected_attempt_status:
        return False
    if expected_attempt_status is AttemptStatus.reserved and attempt.reservation_valid_until <= now:
        return False
    if expected_attempt_status is AttemptStatus.submitted and (
        attempt.deadline_at is None or attempt.deadline_at <= now
    ):
        return False
    if attempt.provider_adapter != snapshot["adapter_kind"] or attempt.provider_model != snapshot["model"]:
        return False
    expected_attempt, _feature, expected_dispatch = split_execution_phase_mapping(execution.kind)
    dispatch = db.scalar(
        select(TaskDispatchOutbox)
        .where(
            TaskDispatchOutbox.source_kind == TaskDispatchSourceKind.consultation_split_execution,
            TaskDispatchOutbox.source_id == execution.id,
        )
        .with_for_update()
    )
    if (
        dispatch is None
        or dispatch.dispatch_kind is not expected_dispatch
        or dispatch.state not in {TaskDispatchState.pending, TaskDispatchState.published}
    ):
        return False
    return (
        attempt.owner_user_id == owner.id
        and attempt.team_id == transcript.team_id
        and attempt.transcript_id == transcript.id
        and attempt.correlation_id == execution.id
        and attempt.attempt_number == 1
        and attempt.attempt_kind is expected_attempt
    )


def _validate_split_execution_for_reuse(
    db: Session,
    *,
    execution_id: UUID,
    expected_execution_status: ConsultationSplitExecutionStatus,
    expected_attempt_status: AttemptStatus,
) -> bool:
    """Validate a cached queued or processing execution without side effects."""
    return _validate_split_execution_for_runtime(
        db,
        execution_id=execution_id,
        expected_execution_status=expected_execution_status,
        expected_attempt_status=expected_attempt_status,
    )


def validate_queued_split_execution_for_runtime(db: Session, *, execution_id: UUID) -> bool:
    """Claim-free queued task boundary for the no-provider task scaffold.

    This public task-facing validator is deliberately queued-only. Duplicate
    delivery cannot consume a reservation or block the later provider runtime.
    """
    return _validate_split_execution_for_runtime(
        db,
        execution_id=execution_id,
        expected_execution_status=ConsultationSplitExecutionStatus.queued,
        expected_attempt_status=AttemptStatus.reserved,
    )


def cancel_unpublished_split_analysis_work(
    db: Session, actor: User, *, transcript_id: UUID
) -> int:
    """Cancel only queued, unpublished analysis reservations for one owner.

    This is the future preference-off boundary.  It cannot select generation
    or verification executions and it never touches a submitted attempt.
    Caller owns the transaction and no route invokes it in this slice.
    """
    transcript = require_split_owner_transcript(db, actor, transcript_id=transcript_id)
    db.scalar(select(User).where(User.id == actor.id).with_for_update())
    db.scalar(select(Transcript).where(Transcript.id == transcript.id).with_for_update())
    executions = db.scalars(
        select(ConsultationSplitExecution)
        .where(
            ConsultationSplitExecution.transcript_id == transcript.id,
            ConsultationSplitExecution.kind == ConsultationSplitExecutionKind.analysis,
            ConsultationSplitExecution.status == ConsultationSplitExecutionStatus.queued,
        )
        .order_by(ConsultationSplitExecution.id)
        .with_for_update()
    ).all()
    cancelled = 0
    for execution in executions:
        attempt = db.scalar(
            select(ProviderAttempt)
            .where(ProviderAttempt.consultation_split_execution_id == execution.id)
            .with_for_update()
        )
        if attempt is None or attempt.status is not AttemptStatus.reserved:
            continue
        dispatch = db.scalar(
            select(TaskDispatchOutbox)
            .where(
                TaskDispatchOutbox.dispatch_kind == split_execution_phase_mapping(execution.kind)[2],
                TaskDispatchOutbox.source_kind == TaskDispatchSourceKind.consultation_split_execution,
                TaskDispatchOutbox.source_id == execution.id,
            )
            .with_for_update()
        )
        if dispatch is None or not cancel_pending_task_dispatch(db, task_id=dispatch.task_id):
            continue
        cancel_provider_attempt(db, attempt_id=attempt.id)
        execution.status = ConsultationSplitExecutionStatus.cancelled
        execution.error_code = "consultation_split_preference_disabled"
        execution.completed_at = utcnow()
        analysis = db.get(ConsultationSplitAnalysis, execution.analysis_id)
        if analysis is not None and analysis.status in {
            ConsultationSplitAnalysisStatus.queued,
            ConsultationSplitAnalysisStatus.processing,
        }:
            analysis.status = ConsultationSplitAnalysisStatus.failed
            analysis.error_code = "consultation_split_preference_disabled"
            analysis.completed_at = execution.completed_at
        cancelled += 1
    db.flush()
    return cancelled


def reconcile_split_draft_staleness(
    db: Session, actor: User, *, draft: ConsultationSplitDraft, current_server_fingerprint: str,
) -> ConsultationSplitDraft:
    """Monotonic stale transition; later source equality never revives a draft."""
    _require_lineage(db, actor, draft)
    fingerprint = _validate_fingerprint(current_server_fingerprint)
    if draft.status is ConsultationSplitDraftStatus.active and draft.source_fingerprint != fingerprint:
        draft.status = ConsultationSplitDraftStatus.stale
    return draft


def _decrypt_json_slot(db: Session, actor: User, row: Any, *, table: str, field: str, allow_list: bool = False) -> dict[str, Any] | list[Any] | None:
    transcript = _require_lineage(db, actor, row)
    if not _scope_matches_transcript(row, transcript):
        raise _internal_scope_error()
    stored = getattr(row, field)
    if stored is None:
        return None
    if not isinstance(stored, str) or not is_encrypted_envelope(stored):
        raise _internal_scope_error()
    value = decrypt_json_for_owner(db, owner_user_id=row.owner_user_id, table=table, field=field, record_id=row.id, stored_value=stored)
    if not isinstance(value, dict) and not (allow_list and isinstance(value, list)):
        raise _internal_scope_error()
    return value


def read_split_analysis_json(db: Session, actor: User, *, analysis: ConsultationSplitAnalysis, field: str) -> dict[str, Any] | None:
    if field not in {"source_snapshot_encrypted", "candidate_template_snapshot_encrypted", "provider_snapshot_encrypted", "proposal_encrypted"}:
        raise AppError(500, "consultation_split_field_invalid", "Consultation split content is unavailable")
    return _decrypt_json_slot(db, actor, analysis, table=_TABLE_ANALYSIS, field=field)  # type: ignore[return-value]


def read_split_batch_json(db: Session, actor: User, *, batch: ConsultationSplitBatch, field: str) -> dict[str, Any] | None:
    if field not in {
        "confirmed_plan_encrypted", "clinical_snapshot_encrypted", "source_snapshot_encrypted",
        "template_snapshot_encrypted", "pii_snapshot_encrypted", "provider_snapshot_encrypted",
        "note_options_snapshot_encrypted",
    }:
        raise AppError(500, "consultation_split_field_invalid", "Consultation split content is unavailable")
    return _decrypt_json_slot(db, actor, batch, table=_TABLE_BATCH, field=field)  # type: ignore[return-value]


def read_split_batch_phi_index(
    db: Session, actor: User, *, batch: ConsultationSplitBatch,
) -> list[dict[str, Any]]:
    """Return the frozen, validated placeholder mapping for owner materialization."""
    snapshot = read_split_batch_json(db, actor, batch=batch, field="pii_snapshot_encrypted")
    raw_index = snapshot.get("phi_index") if isinstance(snapshot, dict) else None
    if not isinstance(raw_index, list):
        raise AppError(500, "consultation_split_batch_invalid", "Split batch is unavailable")
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in raw_index:
        if not isinstance(item, dict):
            raise AppError(500, "consultation_split_batch_invalid", "Split batch is unavailable")
        index, value, entity_type = item.get("index"), item.get("value"), item.get("type")
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or index < 1
            or index in seen
            or not isinstance(value, str)
            or not value.strip()
            or not isinstance(entity_type, str)
            or not entity_type.strip()
            or item.get("placeholder") != f"[PHI-{index}]"
        ):
            raise AppError(500, "consultation_split_batch_invalid", "Split batch is unavailable")
        seen.add(index)
        result.append(dict(item))
    return result


def read_split_batch_topic_template_snapshot(
    db: Session, actor: User, *, topic: ConsultationSplitBatchTopic,
) -> dict[str, Any]:
    value = _decrypt_json_slot(db, actor, topic, table=_TABLE_BATCH_TOPIC, field="template_snapshot_encrypted")
    if not isinstance(value, dict):
        raise _internal_scope_error()
    return value


def read_split_topic_outcome_output(
    db: Session, actor: User, *, outcome: ConsultationSplitTopicOutcome,
) -> dict[str, Any] | list[Any] | None:
    return _decrypt_json_slot(db, actor, outcome, table=_TABLE_OUTCOME, field="output_encrypted", allow_list=True)


def read_split_topic_outcome_verified_output(
    db: Session, actor: User, *, outcome: ConsultationSplitTopicOutcome,
) -> dict[str, Any] | list[Any] | None:
    """Read a verified candidate without ever replacing accepted output."""
    return _decrypt_json_slot(
        db, actor, outcome, table=_TABLE_OUTCOME,
        field="verified_output_encrypted", allow_list=True,
    )


def read_split_execution_json(
    db: Session, actor: User, *, execution: ConsultationSplitExecution, field: str,
) -> dict[str, Any] | list[Any] | None:
    if field not in {"provider_snapshot_encrypted", "request_payload_encrypted", "recoverable_response_encrypted"}:
        raise AppError(500, "consultation_split_field_invalid", "Consultation split content is unavailable")
    return _decrypt_json_slot(db, actor, execution, table=_TABLE_EXECUTION, field=field, allow_list=field != "provider_snapshot_encrypted")


def read_split_topic_title(db: Session, actor: User, *, topic: ConsultationSplitDraftTopic | ConsultationSplitBatchTopic) -> str:
    transcript = _require_lineage(db, actor, topic)
    if not _scope_matches_transcript(topic, transcript) or not is_encrypted_envelope(topic.title_encrypted):
        raise _internal_scope_error()
    table = _TABLE_DRAFT_TOPIC if isinstance(topic, ConsultationSplitDraftTopic) else _TABLE_BATCH_TOPIC
    value = decrypt_text_for_owner(db, owner_user_id=topic.owner_user_id, table=table, field="title_encrypted",
        record_id=topic.id, stored_value=topic.title_encrypted)
    if not isinstance(value, str):
        raise _internal_scope_error()
    return value
