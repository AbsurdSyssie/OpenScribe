"""Claim-free pre-submit boundary for queued consultation-split analysis.

This module resolves a queued execution's credential outside a database
transaction, then reacquires the final owner-to-dispatch lock chain and proves
that the stored request still matches the current source. It does not submit
an attempt, invoke a provider, or wire a task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from google.auth.credentials import Credentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    AttemptKind,
    AttemptStatus,
    ConsultationSplitAnalysis,
    ConsultationSplitAnalysisStatus,
    ConsultationSplitExecution,
    ConsultationSplitExecutionKind,
    ConsultationSplitExecutionStatus,
    LlmAuthMode,
    ProviderAttempt,
    TaskDispatchOutbox,
    TaskDispatchSourceKind,
    TaskDispatchState,
    TeamLlmConfig,
    Transcript,
    User,
    utcnow,
)
from app.services.consultation_split_analysis import prepare_split_analysis_request
from app.services.consultation_split_locks import lock_consultation_split_source_scope
from app.services.consultation_split_sources import current_consultation_split_analysis_source_matches
from app.services.consultation_splits import (
    read_split_analysis_json,
    read_split_execution_json,
    split_execution_phase_mapping,
)
from app.services.llm_adapters.runtime import validate_provider_snapshot_for_config
from app.services.llm_adapters.types import LlmProviderSnapshot
from app.services.llm_credentials import resolve_generation_credential
from app.services.quotas import cancel_provider_attempt
from app.services.task_outbox import cancel_pending_task_dispatch
from app.services.transcripts import transcript_is_expired


PrepareSplitAnalysisSubmissionOutcome = Literal["prepared", "failed", "stale", "noop"]

# Generation has a distinct immutable batch contract.  Keep the public
# pre-submit entry point here with analysis, while its implementation lives
# beside the generation runtime to avoid a second, drifting request builder.
def prepare_queued_split_generation_for_submission(db: Session, *, execution_id: UUID):
    """Resolve a generation credential outside the final submission transaction.

    The returned private tuple is consumed only by the generation runtime.
    It revalidates the frozen batch snapshots and never reads live clinical or
    template content.
    """
    from app.services.consultation_split_generation_runtime import _prepared
    return _prepared(db, execution_id)


@dataclass(frozen=True, slots=True)
class PreparedSplitAnalysisSubmission:
    """Opaque caller-memory values for an immediate future submit transition."""

    execution_id: UUID
    attempt_id: UUID
    provider_snapshot: LlmProviderSnapshot
    request_body: dict[str, object]
    credential: object | None


@dataclass(frozen=True, slots=True)
class PrepareSplitAnalysisSubmissionResult:
    """Content-safe result; error codes contain metadata only."""

    outcome: PrepareSplitAnalysisSubmissionOutcome
    prepared: PreparedSplitAnalysisSubmission | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class _CredentialConfigIdentity:
    config_id: UUID
    team_id: UUID
    auth_mode: LlmAuthMode
    vault_secret_ref: str


@dataclass(slots=True)
class _LockedAnalysisWork:
    owner: User
    transcript: Transcript
    execution: ConsultationSplitExecution
    analysis: ConsultationSplitAnalysis
    attempt: ProviderAttempt
    dispatch: TaskDispatchOutbox


def _credential_config_identity(config: TeamLlmConfig) -> _CredentialConfigIdentity:
    return _CredentialConfigIdentity(
        config_id=config.id,
        team_id=config.team_id,
        auth_mode=config.auth_mode,
        vault_secret_ref=config.vault_secret_ref,
    )


def _credential_matches_auth_mode(config: TeamLlmConfig, credential: object | None) -> bool:
    if config.auth_mode in {LlmAuthMode.none, LlmAuthMode.google_adc}:
        return credential is None
    if config.auth_mode is LlmAuthMode.bearer:
        return isinstance(credential, str) and bool(credential.strip())
    if config.auth_mode is LlmAuthMode.google_service_account:
        return isinstance(credential, Credentials)
    return False


def _preliminary_config(db: Session, *, execution_id: UUID) -> TeamLlmConfig | None:
    """Read and detach the bound config in a short, separate transaction."""
    with Session(bind=db.get_bind(), future=True) as preliminary_db:
        execution = preliminary_db.get(ConsultationSplitExecution, execution_id)
        if (
            execution is None
            or execution.kind is not ConsultationSplitExecutionKind.analysis
            or execution.status is not ConsultationSplitExecutionStatus.queued
            or execution.llm_config_id is None
        ):
            return None
        config = preliminary_db.get(TeamLlmConfig, execution.llm_config_id)
        if config is None:
            return None
        # Every mapped column is loaded by get(). Detaching before close keeps
        # credential resolution from issuing a hidden database read.
        preliminary_db.expunge(config)
        return config


def _lock_viable_analysis_work(db: Session, *, execution_id: UUID) -> _LockedAnalysisWork | None:
    """Acquire the final lock chain and reject work already won elsewhere."""
    identity = db.scalar(select(ConsultationSplitExecution).where(ConsultationSplitExecution.id == execution_id))
    if identity is None:
        return None
    scope = lock_consultation_split_source_scope(
        db,
        owner_user_id=identity.owner_user_id,
        transcript_id=identity.transcript_id,
    )
    if scope is None:
        return None
    owner = scope.owner
    transcript = scope.transcript
    execution = db.scalar(
        select(ConsultationSplitExecution)
        .where(ConsultationSplitExecution.id == execution_id)
        .with_for_update()
    )
    if (
        execution is None
        or execution.kind is not ConsultationSplitExecutionKind.analysis
        or execution.status is not ConsultationSplitExecutionStatus.queued
        or execution.analysis_id is None
        or execution.batch_id is not None
    ):
        return None
    analysis = db.scalar(
        select(ConsultationSplitAnalysis)
        .where(ConsultationSplitAnalysis.id == execution.analysis_id)
        .with_for_update()
    )
    if analysis is None or analysis.status is not ConsultationSplitAnalysisStatus.queued:
        return None
    attempt = db.scalar(
        select(ProviderAttempt)
        .where(ProviderAttempt.consultation_split_execution_id == execution.id)
        .with_for_update()
    )
    dispatch = db.scalar(
        select(TaskDispatchOutbox)
        .where(
            TaskDispatchOutbox.source_kind == TaskDispatchSourceKind.consultation_split_execution,
            TaskDispatchOutbox.source_id == execution.id,
        )
        .with_for_update()
    )
    expected_attempt_kind, _feature, expected_dispatch_kind = split_execution_phase_mapping(execution.kind)
    if (
        attempt is None
        or attempt.status is not AttemptStatus.reserved
        or attempt.attempt_kind is not expected_attempt_kind
        or expected_attempt_kind is not AttemptKind.consultation_split_analysis
        or attempt.owner_user_id != owner.id
        or attempt.team_id != transcript.team_id
        or attempt.transcript_id != transcript.id
        or attempt.correlation_id != execution.id
        or attempt.attempt_number != 1
        or dispatch is None
        or dispatch.dispatch_kind is not expected_dispatch_kind
        or dispatch.state not in {TaskDispatchState.pending, TaskDispatchState.published}
    ):
        return None
    if (
        owner.team_id is None
        or owner.is_system_admin
        or transcript.owner_user_id != owner.id
        or transcript.team_id != owner.team_id
        or execution.owner_user_id != owner.id
        or execution.team_id != owner.team_id
        or execution.transcript_id != transcript.id
        or analysis.owner_user_id != owner.id
        or analysis.team_id != owner.team_id
        or analysis.transcript_id != transcript.id
    ):
        return None
    return _LockedAnalysisWork(owner, transcript, execution, analysis, attempt, dispatch)


def _terminal_failure(
    db: Session,
    *,
    work: _LockedAnalysisWork,
    code: str,
    stale: bool = False,
    cancel_execution: bool = False,
) -> PrepareSplitAnalysisSubmissionResult:
    """Cancel an unsubmitted reservation and commit one safe terminal state."""
    now = utcnow()
    cancel_provider_attempt(db, attempt_id=work.attempt.id, now=now)
    if work.dispatch.state is TaskDispatchState.pending:
        cancel_pending_task_dispatch(db, task_id=work.dispatch.task_id)
    work.execution.status = (
        ConsultationSplitExecutionStatus.cancelled if cancel_execution or stale else ConsultationSplitExecutionStatus.failed
    )
    work.execution.error_code = code
    work.execution.completed_at = now
    work.analysis.status = ConsultationSplitAnalysisStatus.stale if stale else ConsultationSplitAnalysisStatus.failed
    work.analysis.error_code = code
    work.analysis.completed_at = now
    db.commit()
    return PrepareSplitAnalysisSubmissionResult("stale" if stale else "failed", error_code=code)


def prepare_queued_split_analysis_for_submission(
    db: Session,
    *,
    execution_id: UUID,
) -> PrepareSplitAnalysisSubmissionResult:
    """Resolve and prove queued analysis work without submitting it.

    The passed session must be transaction-free. Credential resolution uses a
    detached config after a separate read transaction has closed. On success,
    this function returns with the final database transaction and row locks
    active; the caller must immediately perform its conditional submit
    transition, then commit or roll back. Terminal failures commit here. No-op
    results roll back and release all locks.
    """
    if db.in_transaction():
        raise AppError(
            500,
            "consultation_split_pre_submit_transaction_active",
            "Consultation split preparation requires a clean database session",
        )

    preliminary_config = _preliminary_config(db, execution_id=execution_id)
    credential: object | None = None
    credential_failed = preliminary_config is None
    preliminary_identity = _credential_config_identity(preliminary_config) if preliminary_config is not None else None
    if preliminary_config is not None:
        try:
            credential = resolve_generation_credential(preliminary_config)
            credential_failed = not _credential_matches_auth_mode(preliminary_config, credential)
        except (AppError, TypeError, ValueError):
            credential_failed = True

    work = _lock_viable_analysis_work(db, execution_id=execution_id)
    if work is None:
        db.rollback()
        return PrepareSplitAnalysisSubmissionResult("noop")

    now = utcnow()
    if transcript_is_expired(work.transcript, now=now):
        return _terminal_failure(
            db,
            work=work,
            code="consultation_split_source_expired",
            cancel_execution=True,
        )
    if work.attempt.reservation_valid_until <= now:
        return _terminal_failure(
            db,
            work=work,
            code="consultation_split_reservation_expired",
            cancel_execution=True,
        )
    if (
        work.execution.retention_expires_at != work.transcript.retention_expires_at
        or work.analysis.retention_expires_at != work.transcript.retention_expires_at
    ):
        return _terminal_failure(db, work=work, code="consultation_split_retention_binding_invalid")

    config = (
        db.scalar(select(TeamLlmConfig).where(TeamLlmConfig.id == work.execution.llm_config_id).with_for_update())
        if work.execution.llm_config_id is not None
        else None
    )
    if (
        config is None
        or config.team_id != work.transcript.team_id
        or preliminary_identity is None
        or _credential_config_identity(config) != preliminary_identity
    ):
        return _terminal_failure(db, work=work, code="consultation_split_provider_config_invalid")

    try:
        execution_snapshot = read_split_execution_json(
            db,
            work.owner,
            execution=work.execution,
            field="provider_snapshot_encrypted",
        )
        validated_snapshot = validate_provider_snapshot_for_config(
            execution_snapshot,
            config=config,
            expected_model=work.execution.provider_model,
        )
        if (
            work.execution.provider_adapter != validated_snapshot["adapter_kind"]
            or work.execution.provider_base_url != validated_snapshot["base_url"]
            or work.execution.provider_model != validated_snapshot["model"]
        ):
            raise AppError(422, "consultation_split_provider_snapshot_metadata_mismatch", "Invalid snapshot")
        analysis_snapshot = read_split_analysis_json(
            db,
            work.owner,
            analysis=work.analysis,
            field="provider_snapshot_encrypted",
        )
        if analysis_snapshot != validated_snapshot:
            raise AppError(422, "consultation_split_provider_snapshot_mismatch", "Invalid snapshot")
        source_snapshot = read_split_analysis_json(
            db,
            work.owner,
            analysis=work.analysis,
            field="source_snapshot_encrypted",
        )
        candidate_snapshot = read_split_analysis_json(
            db,
            work.owner,
            analysis=work.analysis,
            field="candidate_template_snapshot_encrypted",
        )
        provider_snapshot = LlmProviderSnapshot(
            llm_config_id=str(validated_snapshot["llm_config_id"]),
            provider_preset=str(validated_snapshot["provider_preset"]),
            adapter_kind=str(validated_snapshot["adapter_kind"]),
            base_url=str(validated_snapshot["base_url"]),
            model=str(validated_snapshot["model"]),
            auth_mode=str(validated_snapshot["auth_mode"]),
            provider_config=dict(validated_snapshot["provider_config"]),
        )
        canonical_request = prepare_split_analysis_request(
            owner_user_id=work.owner.id,
            provider_snapshot=provider_snapshot,
            source_snapshot=source_snapshot,
            candidate_template_snapshot=candidate_snapshot,
        ).request_body
        stored_request = read_split_execution_json(
            db,
            work.owner,
            execution=work.execution,
            field="request_payload_encrypted",
        )
        if stored_request != canonical_request:
            raise AppError(422, "consultation_split_request_payload_invalid", "Invalid request")
    except (AppError, UnicodeDecodeError):
        return _terminal_failure(db, work=work, code="consultation_split_provider_binding_invalid")

    if not current_consultation_split_analysis_source_matches(
        db,
        work.owner,
        transcript=work.transcript,
        analysis=work.analysis,
    ):
        return _terminal_failure(
            db,
            work=work,
            code="consultation_split_source_stale",
            stale=True,
            cancel_execution=True,
        )
    # Source proof and decryption can take long enough to cross a retention or
    # quota deadline. Recheck at the last possible point before handing the
    # caller values that can be submitted.
    final_now = utcnow()
    if transcript_is_expired(work.transcript, now=final_now):
        return _terminal_failure(
            db,
            work=work,
            code="consultation_split_source_expired",
            cancel_execution=True,
        )
    if work.attempt.reservation_valid_until <= final_now:
        return _terminal_failure(
            db,
            work=work,
            code="consultation_split_reservation_expired",
            cancel_execution=True,
        )
    if (
        work.execution.retention_expires_at != work.transcript.retention_expires_at
        or work.analysis.retention_expires_at != work.transcript.retention_expires_at
    ):
        return _terminal_failure(db, work=work, code="consultation_split_retention_binding_invalid")
    if credential_failed or not _credential_matches_auth_mode(config, credential):
        return _terminal_failure(db, work=work, code="consultation_split_credential_unavailable")

    return PrepareSplitAnalysisSubmissionResult(
        "prepared",
        prepared=PreparedSplitAnalysisSubmission(
            execution_id=work.execution.id,
            attempt_id=work.attempt.id,
            provider_snapshot=provider_snapshot,
            request_body=canonical_request,
            credential=credential,
        ),
    )
