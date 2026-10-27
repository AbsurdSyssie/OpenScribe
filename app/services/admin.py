import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Integer, cast, func, select, true, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, joinedload

from app.cookie_security import app_environment
from app.errors import AppError
from app.models import (
    AttemptKind,
    AttemptOutcome,
    AttemptStatus,
    AccountRequest,
    AccountRequestStatus,
    AuthEmailToken,
    AuthEmailTokenPurpose,
    DefaultPromptTemplate,
    DefaultPromptTemplateVersion,
    DefaultQuickAction,
    DefaultQuickActionVersion,
    DeidentificationProvider,
    GeneratedDocument,
    ConsultationSplitExecution,
    ConsultationSplitExecutionKind,
    ConsultationSplitExecutionStatus,
    ConsultationSplitIntent,
    ConsultationSplitAnalysis,
    ConsultationSplitBatch,
    PromptTemplate,
    PromptTemplateVersion,
    ProviderUsageEvent,
    ProviderAttempt,
    ProviderUsageEventType,
    ProviderFeatureType,
    QuickAction,
    QuickActionVersion,
    SecurityAuditEventHold,
    Team,
    TeamClinicalNlpSelection,
    TeamDeidentificationProviderAssignment,
    TeamDeidentificationSelection,
    TeamHallucinationCheckSelection,
    TeamLlmBackupSelection,
    TeamLlmConfig,
    TeamLlmSelection,
    TeamRole,
    TeamSttConfig,
    ProviderSecretCleanupKind,
    TeamSttSelection,
    TemplateScope,
    Transcript,
    TranscriptIngestionJob,
    TranscriptIngestionJobKind,
    TranscriptIngestionJobStatus,
    TemplateSuggestionJob,
    TemplateSuggestionStatus,
    User,
    UserMfaMethod,
    UserOnboardingState,
    UserRecoveryMode,
    UserRecoveryCode,
    UserStatus,
    utcnow,
)
from app.normalization import normalize_email, normalize_team_name_key
from app.quota_policy import TEAM_QUOTA_LIMITS
from app.schemas import (
    AccountRequestApprove,
    AccountRequestCreate,
    AccountRequestReject,
    TeamCreate,
    UserCreate,
)
from app.services.auth import revoke_sessions_for_user, revoke_trusted_devices_for_user
from app.services.content_crypto import ensure_user_dek
from app.services.default_assets import ensure_builtin_default_assets, seed_team_default_assets
from app.services.passwords import hash_password, validate_password_strength
from app.services.security_audit import record_security_event
from app.services.smart_phrases import ensure_default_smart_phrase_for_user
from app.services.provider_secret_cleanup import queue_provider_secret_cleanup
from app.services.transcripts import process_transcript_audio_cleanup_jobs, queue_retry_source_cleanup_for_transcripts
from app.services.quota_lifecycle import delete_dispatches_for_sources, terminalize_attempts_for_owner

audit_logger = logging.getLogger("openscribe.audit")
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = int(os.getenv("MAX_RETENTION_DAYS", "90"))


def validate_retention_days(value: int) -> int:
    if value < MIN_RETENTION_DAYS or value > MAX_RETENTION_DAYS:
        raise AppError(
            422,
            "business_rule_violation",
            f"Retention must be between {MIN_RETENTION_DAYS} and {MAX_RETENTION_DAYS} days",
            {
                "field": "default_retention_days",
                "min": MIN_RETENTION_DAYS,
                "max": MAX_RETENTION_DAYS,
            },
        )
    return value


@dataclass(slots=True)
class AdminUsageWindowSummary:
    label: str
    provider_completed_count: int
    provider_failed_count: int
    provider_input_tokens: int
    provider_output_tokens: int
    provider_total_tokens: int
    provider_estimated_cost_usd: float
    ingestion_job_count: int
    ingestion_failed_count: int
    whole_file_count: int
    live_chunk_count: int
    ingested_bytes: int
    ingested_duration_seconds: float
    provider_completed_delta_pct: float | None = None
    provider_input_tokens_delta_pct: float | None = None
    provider_output_tokens_delta_pct: float | None = None
    provider_total_tokens_delta_pct: float | None = None
    ingested_duration_delta_pct: float | None = None

    @property
    def ingested_megabytes(self) -> float:
        return round(self.ingested_bytes / (1024 * 1024), 1)

    @property
    def ingested_hours(self) -> float:
        return round(self.ingested_duration_seconds / 3600, 2)

    @property
    def provider_success_rate(self) -> float:
        attempts = self.provider_completed_count + self.provider_failed_count
        if attempts <= 0:
            return 0.0
        return round((self.provider_completed_count / attempts) * 100, 1)

    @property
    def ingestion_failure_rate(self) -> float:
        if self.ingestion_job_count <= 0:
            return 0.0
        return round((self.ingestion_failed_count / self.ingestion_job_count) * 100, 1)


@dataclass(slots=True)
class AdminUsageTeamRow:
    team_id: UUID
    team_name: str
    provider_completed_count: int = 0
    provider_failed_count: int = 0
    provider_input_tokens: int = 0
    provider_output_tokens: int = 0
    provider_total_tokens: int = 0
    provider_estimated_cost_usd: float = 0.0
    ingestion_job_count: int = 0
    ingestion_failed_count: int = 0
    whole_file_count: int = 0
    live_chunk_count: int = 0
    ingested_bytes: int = 0
    ingested_duration_seconds: float = 0.0
    last_activity_at: datetime | None = None
    activity_share_pct: float = 0.0

    @property
    def ingested_megabytes(self) -> float:
        return round(self.ingested_bytes / (1024 * 1024), 1)

    @property
    def ingested_hours(self) -> float:
        return round(self.ingested_duration_seconds / 3600, 2)

    @property
    def provider_success_rate(self) -> float:
        attempts = self.provider_completed_count + self.provider_failed_count
        if attempts <= 0:
            return 0.0
        return round((self.provider_completed_count / attempts) * 100, 1)

    @property
    def ingestion_failure_rate(self) -> float:
        if self.ingestion_job_count <= 0:
            return 0.0
        return round((self.ingestion_failed_count / self.ingestion_job_count) * 100, 1)

    @property
    def avg_tokens_per_generation(self) -> float:
        if self.provider_completed_count <= 0:
            return 0.0
        return round(self.provider_total_tokens / self.provider_completed_count, 1)

    @property
    def avg_input_tokens_per_generation(self) -> float:
        if self.provider_completed_count <= 0:
            return 0.0
        return round(self.provider_input_tokens / self.provider_completed_count, 1)

    @property
    def avg_output_tokens_per_generation(self) -> float:
        if self.provider_completed_count <= 0:
            return 0.0
        return round(self.provider_output_tokens / self.provider_completed_count, 1)

    @property
    def live_share_pct(self) -> float:
        total = self.whole_file_count + self.live_chunk_count
        if total <= 0:
            return 0.0
        return round((self.live_chunk_count / total) * 100, 1)


@dataclass(slots=True)
class AdminUsageUserRow:
    user_id: UUID
    email: str
    full_name: str | None
    provider_completed_count: int = 0
    provider_failed_count: int = 0
    provider_input_tokens: int = 0
    provider_output_tokens: int = 0
    provider_total_tokens: int = 0
    provider_estimated_cost_usd: float = 0.0
    ingestion_job_count: int = 0
    ingestion_failed_count: int = 0
    whole_file_count: int = 0
    live_chunk_count: int = 0
    ingested_bytes: int = 0
    ingested_duration_seconds: float = 0.0
    last_activity_at: datetime | None = None
    activity_share_pct: float = 0.0

    @property
    def ingested_megabytes(self) -> float:
        return round(self.ingested_bytes / (1024 * 1024), 1)

    @property
    def ingested_hours(self) -> float:
        return round(self.ingested_duration_seconds / 3600, 2)

    @property
    def provider_success_rate(self) -> float:
        attempts = self.provider_completed_count + self.provider_failed_count
        if attempts <= 0:
            return 0.0
        return round((self.provider_completed_count / attempts) * 100, 1)

    @property
    def ingestion_failure_rate(self) -> float:
        if self.ingestion_job_count <= 0:
            return 0.0
        return round((self.ingestion_failed_count / self.ingestion_job_count) * 100, 1)

    @property
    def avg_tokens_per_generation(self) -> float:
        if self.provider_completed_count <= 0:
            return 0.0
        return round(self.provider_total_tokens / self.provider_completed_count, 1)

    @property
    def avg_input_tokens_per_generation(self) -> float:
        if self.provider_completed_count <= 0:
            return 0.0
        return round(self.provider_input_tokens / self.provider_completed_count, 1)

    @property
    def avg_output_tokens_per_generation(self) -> float:
        if self.provider_completed_count <= 0:
            return 0.0
        return round(self.provider_output_tokens / self.provider_completed_count, 1)

    @property
    def live_share_pct(self) -> float:
        total = self.whole_file_count + self.live_chunk_count
        if total <= 0:
            return 0.0
        return round((self.live_chunk_count / total) * 100, 1)


@dataclass(slots=True)
class AdminUsageKpiCard:
    label: str
    value: str
    detail: str
    delta_text: str
    delta_tone: str = "neutral"


@dataclass(slots=True)
class AdminUsageTrendPoint:
    label: str
    short_label: str
    bucket_date: str = ""
    provider_completed_count: int = 0
    provider_failed_count: int = 0
    provider_input_tokens: int = 0
    provider_output_tokens: int = 0
    provider_total_tokens: int = 0
    ingestion_job_count: int = 0
    ingestion_failed_count: int = 0
    ingested_duration_seconds: float = 0.0
    generation_height_pct: float = 0.0
    input_token_height_pct: float = 0.0
    output_token_height_pct: float = 0.0
    ingestion_height_pct: float = 0.0
    audio_height_pct: float = 0.0
    failure_height_pct: float = 0.0

    @property
    def ingested_hours(self) -> float:
        return round(self.ingested_duration_seconds / 3600, 2)


@dataclass(slots=True)
class AdminUsageProviderRow:
    provider_adapter: str
    model_name: str
    completed_count: int = 0
    failed_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    avg_input_tokens: float = 0.0
    avg_output_tokens: float = 0.0
    avg_total_tokens: float = 0.0
    avg_duration_ms: float = 0.0
    avg_provider_duration_ms: float = 0.0

    @property
    def success_rate(self) -> float:
        attempts = self.completed_count + self.failed_count
        if attempts <= 0:
            return 0.0
        return round((self.completed_count / attempts) * 100, 1)


@dataclass(slots=True)
class AdminUsageGeneratorRow:
    generator_type: str
    ready_count: int = 0
    failed_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    avg_input_tokens: float = 0.0
    avg_output_tokens: float = 0.0
    avg_total_tokens: float = 0.0
    avg_duration_ms: float = 0.0


@dataclass(slots=True)
class AdminUsageIngestionRow:
    stt_adapter_kind: str
    job_kind: str
    job_count: int = 0
    failed_count: int = 0
    ingested_bytes: int = 0
    ingested_duration_seconds: float = 0.0

    @property
    def ingested_megabytes(self) -> float:
        return round(self.ingested_bytes / (1024 * 1024), 1)

    @property
    def ingested_hours(self) -> float:
        return round(self.ingested_duration_seconds / 3600, 2)

    @property
    def failure_rate(self) -> float:
        if self.job_count <= 0:
            return 0.0
        return round((self.failed_count / self.job_count) * 100, 1)


@dataclass(slots=True)
class AdminUsageFailureRow:
    source: str
    code: str
    count: int


@dataclass(slots=True)
class AdminUsageSplitFailureRow:
    """A deliberately narrow, content-free split failure projection."""

    timestamp: datetime
    stage: str
    execution_id: UUID
    operation_id: UUID
    error_code: str
    provider_error_code: str | None
    provider_http_status: int | None


@dataclass(slots=True)
class AdminUsageOperationalRow:
    timestamp: datetime
    stage: str
    reference_id: UUID
    outcome: str
    error_code: str
    provider_error_code: str | None = None
    provider_http_status: int | None = None
    retry_count: int = 0
    model: str | None = None
    provider_adapter: str | None = None
    duration_ms: int | None = None


_SAFE_SPLIT_APPLICATION_ERROR_CODES = frozenset(
    {
        "consultation_split_analysis_invalid_output",
        "consultation_split_credential_unavailable",
        "consultation_split_generation_invalid_output",
        "consultation_split_materialization_binding_invalid",
        "consultation_split_preference_disabled",
        "consultation_split_provider_binding_invalid",
        "consultation_split_provider_config_invalid",
        "consultation_split_provider_failed",
        "consultation_split_reservation_expired",
        "consultation_split_retention_binding_invalid",
        "consultation_split_source_expired",
        "consultation_split_source_stale",
    }
)
_SAFE_SPLIT_PROVIDER_ERROR_CODES = frozenset(
    {
        "authentication_error",
        "bad_request",
        "connection_error",
        "http_status_error",
        "invalid_api_key",
        "invalid_request",
        "invalid_request_error",
        "model_not_found",
        "not_found",
        "permission_denied",
        "provider_conflict",
        "provider_server_error",
        "quota_exceeded",
        "rate_limit_exceeded",
        "timeout",
    }
)
RECENT_SPLIT_FAILURE_LIMIT = 25
RECENT_OPERATIONAL_LIMIT = 50
_SAFE_OPERATIONAL_CODES = frozenset({
    "authentication_error", "bad_request", "connection_error", "generation_enqueue_failed",
    "generation_snapshot_invalid", "hallucination_check_failed", "hallucination_check_invalid_config",
    "hallucination_check_invalid_response", "http_status_error", "ingestion_enqueue_failed",
    "ingestion_failed", "invalid_api_key", "invalid_json", "invalid_request", "invalid_request_error",
    "ingestion_source_expired", "ingestion_processing_stale", "template_suggestion_provider_unavailable",
    "template_suggestion_preference_disabled",
    "llm_generation_attempt_conflict", "llm_generation_attempt_missing", "llm_generation_failed",
    "llm_execution_context_unsupported",
    "llm_generation_invalid_json", "llm_generation_preparation_failed", "llm_provider_bad_response",
    "ingestion_retry_expired", "ingestion_retry_unavailable", "ingestion_source_unavailable",
    "audio_duration_probe_failed", "audio_normalization_failed", "llm_provider_timeout",
    "llm_provider_unreachable", "model_not_found", "not_found",
    "permission_denied", "provider_conflict", "provider_server_error", "quota_exceeded",
    "rate_limit_exceeded", "timeout", "transcript_wait_timeout",
})


def _safe_split_application_error_code(value: object) -> str:
    return value if isinstance(value, str) and value in _SAFE_SPLIT_APPLICATION_ERROR_CODES else "other_split_failure"


def _safe_split_provider_error_code(value: object) -> str | None:
    return value if isinstance(value, str) and value in _SAFE_SPLIT_PROVIDER_ERROR_CODES else None


def _safe_split_provider_http_status(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599 else None


def _safe_operational_code(value: object) -> str:
    return value if isinstance(value, str) and value in _SAFE_OPERATIONAL_CODES else "other_failure"


def _recent_operational_rows(
    db: Session, *, since: datetime, until: datetime, team_id: UUID | None, reference: str | None = None,
) -> list[AdminUsageOperationalRow]:
    """Return a bounded metadata-only failure/recovery projection for support."""
    from app.models import GeneratedDocumentStatus

    reference_uuid = None
    if reference:
        try:
            reference_uuid = UUID(reference.strip())
        except (ValueError, AttributeError):
            return []

    stmt = select(
        ProviderAttempt.correlation_id, ProviderAttempt.attempt_number, ProviderAttempt.attempt_kind,
        ProviderAttempt.status, ProviderAttempt.outcome, ProviderAttempt.error_code, ProviderAttempt.provider_error_code,
        ProviderAttempt.provider_http_status, ProviderAttempt.settled_at, ProviderAttempt.provider_model,
        ProviderAttempt.transport_events_json, ProviderAttempt.provider_adapter,
    ).where(
        func.coalesce(ProviderAttempt.settled_at, ProviderAttempt.authorized_at) >= since,
        func.coalesce(ProviderAttempt.settled_at, ProviderAttempt.authorized_at) < until,
        ProviderAttempt.status.in_((AttemptStatus.settled, AttemptStatus.submitted)),
    )
    if team_id is not None:
        stmt = stmt.where(ProviderAttempt.team_id == team_id)
    if reference_uuid is not None:
        stmt = stmt.where(ProviderAttempt.correlation_id == reference_uuid)
    stmt = stmt.order_by(ProviderAttempt.authorized_at.desc(), ProviderAttempt.attempt_number.desc()).limit(500)
    grouped: dict[tuple[UUID, AttemptKind], list[object]] = {}
    for row in db.execute(stmt):
        grouped.setdefault((row.correlation_id, row.attempt_kind), []).append(row)

    labels = {
        AttemptKind.llm_generation: "Generation",
        AttemptKind.llm_hallucination_check: "Note check",
        AttemptKind.llm_template_suggestion: "Template suggestion",
        AttemptKind.consultation_split_analysis: "Split analysis",
        AttemptKind.consultation_split_generation: "Split generation",
        AttemptKind.consultation_split_verification: "Split verification",
    }
    result: list[AdminUsageOperationalRow] = []
    for (correlation_id, attempt_kind), attempts in grouped.items():
        attempts.sort(key=lambda item: (item.attempt_number, item.settled_at or until))
        final = attempts[-1]
        events = [event for item in attempts for event in (item.transport_events_json or []) if isinstance(event, dict)]
        transport_retry_count = sum(
            max(0, len(item.transport_events_json or []) - 1) for item in attempts
        )
        failures = [event for event in events if event.get("code") in {"rate_limited", "timeout", "connection_error", "provider_error"}]
        previous_failed_attempts = [item for item in attempts if item.outcome is AttemptOutcome.failed]
        if not failures and not previous_failed_attempts and final.outcome is not AttemptOutcome.unknown:
            continue
        source = previous_failed_attempts[-1] if previous_failed_attempts else final
        has_retry = len(events) > 1 or len(attempts) > 1
        recovered = final.outcome is AttemptOutcome.succeeded and bool(failures or previous_failed_attempts) and has_retry
        if final.status is AttemptStatus.submitted:
            outcome = "Retrying" if failures and has_retry else "In progress"
        elif final.outcome is AttemptOutcome.failed:
            outcome = "Failed"
        elif final.outcome is AttemptOutcome.unknown:
            terminal_event = events[-1] if events else {}
            known_failure = terminal_event.get("code") in {
                "rate_limited", "timeout", "connection_error", "provider_error"
            } or _safe_operational_code(final.error_code) != "other_failure" or (
                _safe_split_provider_error_code(final.provider_error_code) is not None
                or (_safe_split_provider_http_status(final.provider_http_status) or 0) >= 400
            )
            outcome = "Failed" if known_failure else "Usage unknown" if terminal_event.get("code") == "success" else "Unknown outcome"
        elif recovered:
            outcome = "Recovered after retry"
        else:
            continue
        stage = labels.get(attempt_kind, "Provider operation")
        last_event = events[-1] if events else {}
        event_time = None
        try:
            if last_event.get("occurred_at"):
                event_time = datetime.fromisoformat(last_event["occurred_at"].replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError):
            event_time = None
        last_error_event = next((event for event in reversed(events) if event.get("code") != "success"), {})
        event_code = {
            "rate_limited": "rate_limit_exceeded", "timeout": "timeout",
            "connection_error": "connection_error", "provider_error": "provider_server_error",
        }.get(last_error_event.get("code"))
        safe_code = _safe_operational_code(source.error_code or event_code)
        provider_status = _safe_split_provider_http_status(
            last_error_event.get("http_status") if last_error_event else source.provider_http_status
        )
        provider_error = _safe_split_provider_error_code(source.provider_error_code)
        if event_code:
            provider_error = event_code if event_code in _SAFE_SPLIT_PROVIDER_ERROR_CODES else provider_error
        result.append(AdminUsageOperationalRow(
            timestamp=event_time or final.settled_at or source.settled_at or until,
            stage=stage,
            reference_id=correlation_id,
            outcome=outcome,
            error_code=safe_code,
            provider_error_code=provider_error,
            provider_http_status=provider_status,
            retry_count=transport_retry_count + max(0, len(attempts) - 1),
            model=final.provider_model,
            provider_adapter=final.provider_adapter,
            duration_ms=sum(int(event.get("duration_ms", 0)) for event in events if isinstance(event.get("duration_ms"), int)),
        ))

    doc_stmt = select(
        GeneratedDocument.id, GeneratedDocument.completed_at, GeneratedDocument.error_code,
        GeneratedDocument.provider_error_code, GeneratedDocument.provider_http_status,
        GeneratedDocument.model_used, GeneratedDocument.duration_ms,
    ).where(
        GeneratedDocument.status == GeneratedDocumentStatus.failed,
        GeneratedDocument.completed_at >= since,
        GeneratedDocument.completed_at < until,
        GeneratedDocument.consultation_split_batch_topic_id.is_(None),
    )
    if team_id is not None:
        doc_stmt = doc_stmt.where(GeneratedDocument.team_id == team_id)
    if reference_uuid is not None:
        doc_stmt = doc_stmt.where(GeneratedDocument.id == reference_uuid)
    doc_stmt = doc_stmt.order_by(GeneratedDocument.completed_at.desc()).limit(RECENT_OPERATIONAL_LIMIT)
    seen_keys = {(reference_id, labels.get(kind, "Provider operation")) for reference_id, kind in grouped}
    for row in db.execute(doc_stmt):
        if (row.id, "Generation") in seen_keys:
            continue
        result.append(AdminUsageOperationalRow(
            timestamp=row.completed_at, stage="Generation", reference_id=row.id, outcome="Failed",
            error_code=_safe_operational_code(row.error_code),
            provider_error_code=_safe_split_provider_error_code(row.provider_error_code),
            provider_http_status=_safe_split_provider_http_status(row.provider_http_status),
            model=row.model_used, duration_ms=row.duration_ms,
        ))

    usage_stmt = select(
        ProviderUsageEvent.created_at, ProviderUsageEvent.generated_document_id,
        ProviderUsageEvent.status, ProviderUsageEvent.error_code, ProviderUsageEvent.provider_error_code,
        ProviderUsageEvent.provider_http_status, ProviderUsageEvent.model_name,
        ProviderUsageEvent.provider_duration_ms,
    ).where(
        ProviderUsageEvent.created_at >= since,
        ProviderUsageEvent.created_at < until,
        ProviderUsageEvent.event_type.in_((ProviderUsageEventType.failed, ProviderUsageEventType.enqueue_failed)),
        ProviderUsageEvent.generated_document_id.is_not(None),
        ProviderUsageEvent.feature_type == ProviderFeatureType.llm_generation,
    )
    if team_id is not None:
        usage_stmt = usage_stmt.where(ProviderUsageEvent.team_id == team_id)
    if reference_uuid is not None:
        usage_stmt = usage_stmt.where(ProviderUsageEvent.generated_document_id == reference_uuid)
    usage_stmt = usage_stmt.order_by(ProviderUsageEvent.created_at.desc()).limit(RECENT_OPERATIONAL_LIMIT * 3)
    for row in db.execute(usage_stmt):
        stage = "Note check" if (row.status or "").startswith("hallucination_check:") else "Generation"
        if (row.generated_document_id, stage) in seen_keys or (row.generated_document_id, stage) in {
            (item.reference_id, item.stage) for item in result
        }:
            continue
        result.append(AdminUsageOperationalRow(
            timestamp=row.created_at, stage=stage, reference_id=row.generated_document_id,
            outcome="Failed", error_code=_safe_operational_code(row.error_code),
            provider_error_code=_safe_split_provider_error_code(row.provider_error_code),
            provider_http_status=_safe_split_provider_http_status(row.provider_http_status),
            model=row.model_name, duration_ms=row.provider_duration_ms,
        ))

    suggestion_stmt = select(
        TemplateSuggestionJob.id, TemplateSuggestionJob.completed_at, TemplateSuggestionJob.error_code,
    ).where(
        TemplateSuggestionJob.completed_at >= since,
        TemplateSuggestionJob.completed_at < until,
        TemplateSuggestionJob.status == TemplateSuggestionStatus.failed,
    )
    if team_id is not None:
        suggestion_stmt = suggestion_stmt.where(TemplateSuggestionJob.team_id == team_id)
    # Only the correlation identifier is searchable for this job family.
    if reference_uuid is not None:
        suggestion_stmt = suggestion_stmt.where(TemplateSuggestionJob.id == reference_uuid)
    suggestion_stmt = suggestion_stmt.order_by(TemplateSuggestionJob.completed_at.desc()).limit(RECENT_OPERATIONAL_LIMIT)
    for row in db.execute(suggestion_stmt):
        if (row.id, "Template suggestion") in {(item.reference_id, item.stage) for item in result}:
            continue
        result.append(AdminUsageOperationalRow(
            timestamp=row.completed_at, stage="Template suggestion", reference_id=row.id,
            outcome="Failed", error_code=_safe_operational_code(row.error_code),
        ))

    ingestion_stmt = select(
        TranscriptIngestionJob.id, TranscriptIngestionJob.completed_at,
        TranscriptIngestionJob.automatic_retry_count, TranscriptIngestionJob.status,
        TranscriptIngestionJob.error_code,
    ).where(
        TranscriptIngestionJob.completed_at >= since,
        TranscriptIngestionJob.completed_at < until,
        (TranscriptIngestionJob.status == TranscriptIngestionJobStatus.failed)
        | (TranscriptIngestionJob.automatic_retry_count > 0),
    )
    if team_id is not None:
        ingestion_stmt = ingestion_stmt.where(TranscriptIngestionJob.team_id == team_id)
    if reference_uuid is not None:
        ingestion_stmt = ingestion_stmt.where(TranscriptIngestionJob.id == reference_uuid)
    ingestion_stmt = ingestion_stmt.order_by(TranscriptIngestionJob.completed_at.desc()).limit(RECENT_OPERATIONAL_LIMIT)
    for row in db.execute(ingestion_stmt):
        result.append(AdminUsageOperationalRow(
            timestamp=row.completed_at, stage="Speech ingestion", reference_id=row.id,
            outcome="Recovered after retry" if row.status is not TranscriptIngestionJobStatus.failed and row.automatic_retry_count else "Failed",
            error_code=_safe_operational_code(row.error_code), retry_count=int(row.automatic_retry_count or 0),
        ))
    result.sort(key=lambda row: row.timestamp, reverse=True)
    return result[:RECENT_OPERATIONAL_LIMIT]


def _operational_health(db: Session, *, team_id: UUID | None, now: datetime) -> dict[str, object]:
    from app.models import GeneratedDocumentStatus, ConsultationSplitExecutionStatus

    since = now - timedelta(minutes=15)
    transport = func.jsonb_array_elements(ProviderAttempt.transport_events_json).table_valued("value").alias("transport_event")
    event = cast(transport.c.value, JSONB)
    event_code = event["code"].astext
    event_status = cast(event["http_status"].astext, Integer)
    event_duration = cast(event["duration_ms"].astext, Integer)
    event_time = cast(event["occurred_at"].astext, DateTime(timezone=True))
    transport_stmt = select(
        func.count(event).label("observed_calls"),
        func.count(event).filter((event_status == 429) | (event_code == "rate_limited")).label("http_429"),
        func.count(event).filter(event_status == 503).label("http_503"),
        func.count(event).filter(event_code == "timeout").label("timeouts"),
        func.count(event).filter(event_code.in_(("rate_limited", "timeout", "connection_error", "provider_error"))).label("degraded_calls"),
        func.avg(event_duration).label("avg_duration_ms"),
    ).select_from(ProviderAttempt).join(transport, true()).where(
        ProviderAttempt.submitted_at >= since - timedelta(seconds=180),
        ProviderAttempt.submitted_at < now,
        event_time >= since, event_time < now,
    )
    if team_id is not None:
        transport_stmt = transport_stmt.where(ProviderAttempt.team_id == team_id)
    attempt_row = db.execute(transport_stmt).one()

    # Older attempts have no request-event list. Count their one submitted
    # provider call from the terminal safe metadata, without double counting
    # rows that already have request events.
    legacy_stmt = select(
        func.count(ProviderAttempt.id).label("observed_calls"),
        func.count(ProviderAttempt.id).filter(ProviderAttempt.provider_http_status == 429).label("http_429"),
        func.count(ProviderAttempt.id).filter(ProviderAttempt.provider_http_status == 503).label("http_503"),
        func.count(ProviderAttempt.id).filter(
            (ProviderAttempt.provider_error_code == "timeout") | (ProviderAttempt.error_code == "llm_provider_timeout")
        ).label("timeouts"),
        func.count(ProviderAttempt.id).filter(
            (ProviderAttempt.provider_http_status >= 400)
            | ProviderAttempt.provider_error_code.in_(_SAFE_SPLIT_PROVIDER_ERROR_CODES)
            | ProviderAttempt.error_code.in_(("llm_provider_timeout", "llm_provider_unreachable"))
        ).label("degraded_calls"),
    ).where(
        ProviderAttempt.submitted_at >= since, ProviderAttempt.submitted_at < now,
        func.jsonb_array_length(ProviderAttempt.transport_events_json) == 0,
    )
    if team_id is not None:
        legacy_stmt = legacy_stmt.where(ProviderAttempt.team_id == team_id)
    legacy = db.execute(legacy_stmt).one()
    overdue_stmt = select(func.count(ProviderAttempt.id)).where(
        ProviderAttempt.status == AttemptStatus.submitted, ProviderAttempt.deadline_at < now,
    )
    if team_id is not None:
        overdue_stmt = overdue_stmt.where(ProviderAttempt.team_id == team_id)
    overdue_attempts = int(db.scalar(overdue_stmt) or 0)

    queued = []
    for model, active_statuses in (
        (GeneratedDocument, (GeneratedDocumentStatus.queued,)),
        (TranscriptIngestionJob, (TranscriptIngestionJobStatus.queued,)),
        (TemplateSuggestionJob, (TemplateSuggestionStatus.queued,)),
        (ConsultationSplitExecution, (ConsultationSplitExecutionStatus.queued,)),
    ):
        stmt = select(func.min(model.created_at)).where(model.status.in_(active_statuses))
        if team_id is not None:
            stmt = stmt.where(model.team_id == team_id)
        queued.append(db.scalar(stmt))
    oldest = min((value for value in queued if value is not None), default=None)

    latency = attempt_row.avg_duration_ms
    if latency is None:
        latency_stmt = select(func.avg(ProviderUsageEvent.provider_duration_ms)).where(
            ProviderUsageEvent.created_at >= since, ProviderUsageEvent.provider_duration_ms.is_not(None),
        )
        if team_id is not None:
            latency_stmt = latency_stmt.where(ProviderUsageEvent.team_id == team_id)
        latency = db.scalar(latency_stmt)
    queue_wait_sum_ms = 0.0
    queue_wait_count = 0
    for model in (GeneratedDocument, TranscriptIngestionJob, TemplateSuggestionJob):
        received_at = model.worker_received_at
        wait_stmt = select(
            func.coalesce(func.sum(func.extract("epoch", received_at - model.created_at) * 1000), 0),
            func.count(model.id),
        ).where(received_at >= since)
        if team_id is not None:
            wait_stmt = wait_stmt.where(model.team_id == team_id)
        wait_sum, wait_count = db.execute(wait_stmt).one()
        queue_wait_sum_ms += float(wait_sum or 0)
        queue_wait_count += int(wait_count or 0)
    http_429 = int(attempt_row.http_429 or 0) + int(legacy.http_429 or 0)
    http_503 = int(attempt_row.http_503 or 0) + int(legacy.http_503 or 0)
    timeouts = int(attempt_row.timeouts or 0) + int(legacy.timeouts or 0)
    degraded_calls = int(attempt_row.degraded_calls or 0) + int(legacy.degraded_calls or 0)
    observed_calls = int(attempt_row.observed_calls or 0) + int(legacy.observed_calls or 0)
    degradation_pct = round(100 * degraded_calls / observed_calls, 1) if observed_calls else None
    alerts = []
    if oldest is not None and (now - oldest).total_seconds() > 60:
        alerts.append("queued_work_over_60_seconds")
    if overdue_attempts:
        alerts.append("provider_attempts_overdue")
    if degraded_calls >= 5 and degradation_pct is not None and degradation_pct >= 20:
        alerts.append("provider_errors_elevated")
    return {
        "observed_calls": observed_calls,
        "http_429": http_429, "http_503": http_503,
        "timeouts": timeouts, "degraded_calls": degraded_calls, "degradation_pct": degradation_pct,
        "overdue_attempts": overdue_attempts, "alerts": alerts,
        "oldest_queued_at": oldest,
        "oldest_queue_age_seconds": max(0, int((now - oldest).total_seconds())) if oldest else None,
        "avg_queue_wait_ms": round(queue_wait_sum_ms / queue_wait_count, 1) if queue_wait_count else None,
        "avg_provider_latency_ms": round(float(latency), 1) if latency is not None else None,
    }


def _failure_event_clause():
    return ProviderUsageEvent.event_type.in_(
        (ProviderUsageEventType.failed, ProviderUsageEventType.enqueue_failed)
    )


def _coerce_decimal(value: Decimal | float | int | None) -> float:
    if value is None:
        return 0.0
    return float(value)


def _format_delta_pct(current: int | float, previous: int | float) -> float | None:
    if previous == 0:
        if current == 0:
            return 0.0
        return None
    return round(((current - previous) / previous) * 100, 1)


def _delta_text(delta_pct: float | None, *, positive_is_good: bool = True) -> tuple[str, str]:
    if delta_pct is None:
        return ("New activity", "neutral")
    if delta_pct == 0:
        return ("Flat vs previous window", "neutral")
    direction = "up" if delta_pct > 0 else "down"
    magnitude = abs(delta_pct)
    tone = "good" if (delta_pct > 0) == positive_is_good else "warning"
    return (f"{direction} {magnitude:.1f}% vs previous window", tone)


def _format_token_count(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def _format_bytes_compact(value: int) -> str:
    if value >= 1024 * 1024 * 1024:
        return f"{value / (1024 * 1024 * 1024):.2f} GB"
    return f"{value / (1024 * 1024):.1f} MB"


def _trend_height(value: int | float, *, max_value: int | float) -> float:
    if max_value <= 0:
        return 0.0
    return round((float(value) / float(max_value)) * 100, 1)


def _provider_usage_window_summary(db: Session, *, since, until, team_id: UUID | None) -> dict[str, int | float]:
    stmt = select(
        func.count(ProviderUsageEvent.id)
        .filter(ProviderUsageEvent.event_type == ProviderUsageEventType.completed)
        .label("provider_completed_count"),
        func.count(ProviderUsageEvent.id)
        .filter(_failure_event_clause())
        .label("provider_failed_count"),
        func.coalesce(func.sum(ProviderUsageEvent.prompt_tokens), 0).label("provider_input_tokens"),
        func.coalesce(func.sum(ProviderUsageEvent.completion_tokens), 0).label("provider_output_tokens"),
        func.coalesce(func.sum(ProviderUsageEvent.total_tokens), 0).label("provider_total_tokens"),
        func.coalesce(func.sum(ProviderUsageEvent.estimated_cost_usd), 0).label("provider_estimated_cost_usd"),
    ).where(ProviderUsageEvent.created_at >= since, ProviderUsageEvent.created_at < until)
    if team_id is not None:
        stmt = stmt.where(ProviderUsageEvent.team_id == team_id)
    row = db.execute(stmt).one()
    return {
        "provider_completed_count": int(row.provider_completed_count or 0),
        "provider_failed_count": int(row.provider_failed_count or 0),
        "provider_input_tokens": int(row.provider_input_tokens or 0),
        "provider_output_tokens": int(row.provider_output_tokens or 0),
        "provider_total_tokens": int(row.provider_total_tokens or 0),
        "provider_estimated_cost_usd": _coerce_decimal(row.provider_estimated_cost_usd),
    }


def _ingestion_duration_expression():
    return func.coalesce(
        TranscriptIngestionJob.source_audio_duration_seconds,
        TranscriptIngestionJob.declared_duration_seconds,
        0.0,
    )


def _ingestion_window_summary(db: Session, *, since, until, team_id: UUID | None) -> dict[str, int | float]:
    stmt = select(
        func.count(TranscriptIngestionJob.id).label("ingestion_job_count"),
        func.count(TranscriptIngestionJob.id)
        .filter(TranscriptIngestionJob.status == TranscriptIngestionJobStatus.failed)
        .label("ingestion_failed_count"),
        func.count(TranscriptIngestionJob.id)
        .filter(TranscriptIngestionJob.job_kind == TranscriptIngestionJobKind.audio_file)
        .label("whole_file_count"),
        func.count(TranscriptIngestionJob.id)
        .filter(TranscriptIngestionJob.job_kind == TranscriptIngestionJobKind.live_chunk)
        .label("live_chunk_count"),
        func.coalesce(func.sum(TranscriptIngestionJob.source_audio_size_bytes), 0).label("ingested_bytes"),
        func.coalesce(func.sum(_ingestion_duration_expression()), 0.0).label("ingested_duration_seconds"),
    ).where(TranscriptIngestionJob.created_at >= since, TranscriptIngestionJob.created_at < until)
    if team_id is not None:
        stmt = stmt.where(TranscriptIngestionJob.team_id == team_id)
    row = db.execute(stmt).one()
    return {
        "ingestion_job_count": int(row.ingestion_job_count or 0),
        "ingestion_failed_count": int(row.ingestion_failed_count or 0),
        "whole_file_count": int(row.whole_file_count or 0),
        "live_chunk_count": int(row.live_chunk_count or 0),
        "ingested_bytes": int(row.ingested_bytes or 0),
        "ingested_duration_seconds": float(row.ingested_duration_seconds or 0.0),
    }


def _team_usage_rows(db: Session, *, since, team_id: UUID | None) -> list[AdminUsageTeamRow]:
    rows_by_team: dict[UUID, AdminUsageTeamRow] = {}

    provider_stmt = (
        select(
            Team.id.label("team_id"),
            Team.name.label("team_name"),
            func.count(ProviderUsageEvent.id)
            .filter(ProviderUsageEvent.event_type == ProviderUsageEventType.completed)
            .label("provider_completed_count"),
            func.count(ProviderUsageEvent.id)
            .filter(_failure_event_clause())
            .label("provider_failed_count"),
            func.coalesce(func.sum(ProviderUsageEvent.prompt_tokens), 0).label("provider_input_tokens"),
            func.coalesce(func.sum(ProviderUsageEvent.completion_tokens), 0).label("provider_output_tokens"),
            func.coalesce(func.sum(ProviderUsageEvent.total_tokens), 0).label("provider_total_tokens"),
            func.coalesce(func.sum(ProviderUsageEvent.estimated_cost_usd), 0).label("provider_estimated_cost_usd"),
            func.max(ProviderUsageEvent.created_at).label("provider_last_activity_at"),
        )
        .join(Team, Team.id == ProviderUsageEvent.team_id)
        .where(ProviderUsageEvent.created_at >= since)
        .group_by(Team.id, Team.name)
        .order_by(Team.name.asc())
    )
    if team_id is not None:
        provider_stmt = provider_stmt.where(ProviderUsageEvent.team_id == team_id)
    for row in db.execute(provider_stmt):
        rows_by_team[row.team_id] = AdminUsageTeamRow(
            team_id=row.team_id,
            team_name=row.team_name,
            provider_completed_count=int(row.provider_completed_count or 0),
            provider_failed_count=int(row.provider_failed_count or 0),
            provider_input_tokens=int(row.provider_input_tokens or 0),
            provider_output_tokens=int(row.provider_output_tokens or 0),
            provider_total_tokens=int(row.provider_total_tokens or 0),
            provider_estimated_cost_usd=_coerce_decimal(row.provider_estimated_cost_usd),
            last_activity_at=row.provider_last_activity_at,
        )

    ingestion_stmt = (
        select(
            Team.id.label("team_id"),
            Team.name.label("team_name"),
            func.count(TranscriptIngestionJob.id).label("ingestion_job_count"),
            func.count(TranscriptIngestionJob.id)
            .filter(TranscriptIngestionJob.status == TranscriptIngestionJobStatus.failed)
            .label("ingestion_failed_count"),
            func.count(TranscriptIngestionJob.id)
            .filter(TranscriptIngestionJob.job_kind == TranscriptIngestionJobKind.audio_file)
            .label("whole_file_count"),
            func.count(TranscriptIngestionJob.id)
            .filter(TranscriptIngestionJob.job_kind == TranscriptIngestionJobKind.live_chunk)
            .label("live_chunk_count"),
            func.coalesce(func.sum(TranscriptIngestionJob.source_audio_size_bytes), 0).label("ingested_bytes"),
            func.coalesce(func.sum(_ingestion_duration_expression()), 0.0).label("ingested_duration_seconds"),
            func.max(TranscriptIngestionJob.created_at).label("ingestion_last_activity_at"),
        )
        .join(Team, Team.id == TranscriptIngestionJob.team_id)
        .where(TranscriptIngestionJob.created_at >= since)
        .group_by(Team.id, Team.name)
        .order_by(Team.name.asc())
    )
    if team_id is not None:
        ingestion_stmt = ingestion_stmt.where(TranscriptIngestionJob.team_id == team_id)
    for row in db.execute(ingestion_stmt):
        usage = rows_by_team.get(row.team_id)
        if usage is None:
            usage = AdminUsageTeamRow(team_id=row.team_id, team_name=row.team_name)
            rows_by_team[row.team_id] = usage
        usage.ingestion_job_count = int(row.ingestion_job_count or 0)
        usage.ingestion_failed_count = int(row.ingestion_failed_count or 0)
        usage.whole_file_count = int(row.whole_file_count or 0)
        usage.live_chunk_count = int(row.live_chunk_count or 0)
        usage.ingested_bytes = int(row.ingested_bytes or 0)
        usage.ingested_duration_seconds = float(row.ingested_duration_seconds or 0.0)
        if usage.last_activity_at is None or (row.ingestion_last_activity_at and row.ingestion_last_activity_at > usage.last_activity_at):
            usage.last_activity_at = row.ingestion_last_activity_at

    total_tokens = sum(row.provider_total_tokens for row in rows_by_team.values())
    total_hours = sum(row.ingested_duration_seconds for row in rows_by_team.values())
    total_activity = float(total_tokens) + total_hours * 1000.0
    for usage in rows_by_team.values():
        activity_value = float(usage.provider_total_tokens) + usage.ingested_duration_seconds * 1000.0
        usage.activity_share_pct = round((activity_value / total_activity) * 100, 1) if total_activity > 0 else 0.0

    return sorted(
        rows_by_team.values(),
        key=lambda row: (-row.activity_share_pct, -row.provider_total_tokens, -row.ingested_duration_seconds, row.team_name.lower()),
    )


def _user_usage_rows(db: Session, *, since, team_id: UUID) -> list[AdminUsageUserRow]:
    rows_by_user: dict[UUID, AdminUsageUserRow] = {}

    provider_stmt = (
        select(
            User.id.label("user_id"),
            User.email.label("email"),
            User.full_name.label("full_name"),
            func.count(ProviderUsageEvent.id)
            .filter(ProviderUsageEvent.event_type == ProviderUsageEventType.completed)
            .label("provider_completed_count"),
            func.count(ProviderUsageEvent.id)
            .filter(_failure_event_clause())
            .label("provider_failed_count"),
            func.coalesce(func.sum(ProviderUsageEvent.prompt_tokens), 0).label("provider_input_tokens"),
            func.coalesce(func.sum(ProviderUsageEvent.completion_tokens), 0).label("provider_output_tokens"),
            func.coalesce(func.sum(ProviderUsageEvent.total_tokens), 0).label("provider_total_tokens"),
            func.coalesce(func.sum(ProviderUsageEvent.estimated_cost_usd), 0).label("provider_estimated_cost_usd"),
            func.max(ProviderUsageEvent.created_at).label("provider_last_activity_at"),
        )
        .join(User, User.id == ProviderUsageEvent.owner_user_id)
        .where(
            ProviderUsageEvent.created_at >= since,
            ProviderUsageEvent.team_id == team_id,
        )
        .group_by(User.id, User.email, User.full_name)
        .order_by(User.email.asc())
    )
    for row in db.execute(provider_stmt):
        rows_by_user[row.user_id] = AdminUsageUserRow(
            user_id=row.user_id,
            email=row.email,
            full_name=row.full_name,
            provider_completed_count=int(row.provider_completed_count or 0),
            provider_failed_count=int(row.provider_failed_count or 0),
            provider_input_tokens=int(row.provider_input_tokens or 0),
            provider_output_tokens=int(row.provider_output_tokens or 0),
            provider_total_tokens=int(row.provider_total_tokens or 0),
            provider_estimated_cost_usd=_coerce_decimal(row.provider_estimated_cost_usd),
            last_activity_at=row.provider_last_activity_at,
        )

    ingestion_stmt = (
        select(
            User.id.label("user_id"),
            User.email.label("email"),
            User.full_name.label("full_name"),
            func.count(TranscriptIngestionJob.id).label("ingestion_job_count"),
            func.count(TranscriptIngestionJob.id)
            .filter(TranscriptIngestionJob.status == TranscriptIngestionJobStatus.failed)
            .label("ingestion_failed_count"),
            func.count(TranscriptIngestionJob.id)
            .filter(TranscriptIngestionJob.job_kind == TranscriptIngestionJobKind.audio_file)
            .label("whole_file_count"),
            func.count(TranscriptIngestionJob.id)
            .filter(TranscriptIngestionJob.job_kind == TranscriptIngestionJobKind.live_chunk)
            .label("live_chunk_count"),
            func.coalesce(func.sum(TranscriptIngestionJob.source_audio_size_bytes), 0).label("ingested_bytes"),
            func.coalesce(func.sum(_ingestion_duration_expression()), 0.0).label("ingested_duration_seconds"),
            func.max(TranscriptIngestionJob.created_at).label("ingestion_last_activity_at"),
        )
        .join(User, User.id == TranscriptIngestionJob.owner_user_id)
        .where(
            TranscriptIngestionJob.created_at >= since,
            TranscriptIngestionJob.team_id == team_id,
        )
        .group_by(User.id, User.email, User.full_name)
        .order_by(User.email.asc())
    )
    for row in db.execute(ingestion_stmt):
        usage = rows_by_user.get(row.user_id)
        if usage is None:
            usage = AdminUsageUserRow(user_id=row.user_id, email=row.email, full_name=row.full_name)
            rows_by_user[row.user_id] = usage
        usage.ingestion_job_count = int(row.ingestion_job_count or 0)
        usage.ingestion_failed_count = int(row.ingestion_failed_count or 0)
        usage.whole_file_count = int(row.whole_file_count or 0)
        usage.live_chunk_count = int(row.live_chunk_count or 0)
        usage.ingested_bytes = int(row.ingested_bytes or 0)
        usage.ingested_duration_seconds = float(row.ingested_duration_seconds or 0.0)
        if usage.last_activity_at is None or (row.ingestion_last_activity_at and row.ingestion_last_activity_at > usage.last_activity_at):
            usage.last_activity_at = row.ingestion_last_activity_at

    total_tokens = sum(row.provider_total_tokens for row in rows_by_user.values())
    total_hours = sum(row.ingested_duration_seconds for row in rows_by_user.values())
    total_activity = float(total_tokens) + total_hours * 1000.0
    for usage in rows_by_user.values():
        activity_value = float(usage.provider_total_tokens) + usage.ingested_duration_seconds * 1000.0
        usage.activity_share_pct = round((activity_value / total_activity) * 100, 1) if total_activity > 0 else 0.0

    return sorted(
        rows_by_user.values(),
        key=lambda row: (-row.activity_share_pct, -row.provider_total_tokens, -row.ingested_duration_seconds, row.email.lower()),
    )


def _usage_kpi_cards(*, current: AdminUsageWindowSummary, previous: AdminUsageWindowSummary, period_label: str = "30 days") -> list[AdminUsageKpiCard]:
    generation_delta_text, generation_delta_tone = _delta_text(
        _format_delta_pct(current.provider_completed_count, previous.provider_completed_count)
    )
    input_tokens_delta_text, input_tokens_delta_tone = _delta_text(
        _format_delta_pct(current.provider_input_tokens, previous.provider_input_tokens)
    )
    output_tokens_delta_text, output_tokens_delta_tone = _delta_text(
        _format_delta_pct(current.provider_output_tokens, previous.provider_output_tokens)
    )
    audio_delta_text, audio_delta_tone = _delta_text(
        _format_delta_pct(current.ingested_duration_seconds, previous.ingested_duration_seconds)
    )
    failure_delta_text, failure_delta_tone = _delta_text(
        _format_delta_pct(current.provider_failed_count + current.ingestion_failed_count, previous.provider_failed_count + previous.ingestion_failed_count),
        positive_is_good=False,
    )
    return [
        AdminUsageKpiCard(
            label=f"Generated · {period_label}",
            value=str(current.provider_completed_count),
            detail=f"{current.provider_success_rate:.1f}% provider success rate",
            delta_text=generation_delta_text,
            delta_tone=generation_delta_tone,
        ),
        AdminUsageKpiCard(
            label=f"Input tokens · {period_label}",
            value=_format_token_count(current.provider_input_tokens),
            detail=f"Across {current.provider_completed_count} completed generations",
            delta_text=input_tokens_delta_text,
            delta_tone=input_tokens_delta_tone,
        ),
        AdminUsageKpiCard(
            label=f"Output tokens · {period_label}",
            value=_format_token_count(current.provider_output_tokens),
            detail=f"{_format_token_count(current.provider_total_tokens)} total tokens overall",
            delta_text=output_tokens_delta_text,
            delta_tone=output_tokens_delta_tone,
        ),
        AdminUsageKpiCard(
            label="Audio processed",
            value=f"{current.ingested_hours:.2f}h",
            detail=f"{_format_bytes_compact(current.ingested_bytes)} uploaded",
            delta_text=audio_delta_text,
            delta_tone=audio_delta_tone,
        ),
        AdminUsageKpiCard(
            label=f"Failures · {period_label}",
            value=str(current.provider_failed_count + current.ingestion_failed_count),
            detail=f"{current.ingestion_failure_rate:.1f}% ingestion failure rate",
            delta_text=failure_delta_text,
            delta_tone=failure_delta_tone,
        ),
    ]


def _usage_bucket_start(value, bucket: str):
    value = value.replace(hour=0, minute=0, second=0, microsecond=0)
    if bucket == "week":
        return value - timedelta(days=value.weekday())
    if bucket == "month":
        return value.replace(day=1)
    return value


def _next_usage_bucket(value, bucket: str):
    if bucket == "month":
        return value.replace(year=value.year + 1, month=1) if value.month == 12 else value.replace(month=value.month + 1)
    return value + timedelta(days=7 if bucket == "week" else 1)


def _usage_trend_points(db: Session, *, since, team_id: UUID | None, until=None, bucket: str = "day") -> list[AdminUsageTrendPoint]:
    start_bucket = _usage_bucket_start(since, bucket)
    end_bucket = _usage_bucket_start((until - timedelta(microseconds=1)) if until is not None else utcnow(), bucket)
    points: dict[object, AdminUsageTrendPoint] = {}
    cursor = start_bucket
    while cursor <= end_bucket:
        key = cursor.date()
        label = cursor.strftime("%b %Y" if bucket == "month" else "%d %b %Y")
        points[key] = AdminUsageTrendPoint(
            label=label,
            short_label=cursor.strftime("%b %y" if bucket == "month" else "%d %b"),
            bucket_date=key.isoformat(),
        )
        cursor = _next_usage_bucket(cursor, bucket)

    provider_stmt = select(
        func.date_trunc(bucket, ProviderUsageEvent.created_at).label("bucket_day"),
        func.count(ProviderUsageEvent.id)
        .filter(ProviderUsageEvent.event_type == ProviderUsageEventType.completed)
        .label("provider_completed_count"),
        func.count(ProviderUsageEvent.id)
        .filter(_failure_event_clause())
        .label("provider_failed_count"),
        func.coalesce(func.sum(ProviderUsageEvent.prompt_tokens), 0).label("provider_input_tokens"),
        func.coalesce(func.sum(ProviderUsageEvent.completion_tokens), 0).label("provider_output_tokens"),
        func.coalesce(func.sum(ProviderUsageEvent.total_tokens), 0).label("provider_total_tokens"),
    ).where(ProviderUsageEvent.created_at >= since).group_by("bucket_day").order_by("bucket_day")
    if until is not None:
        provider_stmt = provider_stmt.where(ProviderUsageEvent.created_at < until)
    if team_id is not None:
        provider_stmt = provider_stmt.where(ProviderUsageEvent.team_id == team_id)
    for row in db.execute(provider_stmt):
        bucket_day = row.bucket_day.date()
        point = points.get(bucket_day)
        if point is None:
            continue
        point.provider_completed_count = int(row.provider_completed_count or 0)
        point.provider_failed_count = int(row.provider_failed_count or 0)
        point.provider_input_tokens = int(row.provider_input_tokens or 0)
        point.provider_output_tokens = int(row.provider_output_tokens or 0)
        point.provider_total_tokens = int(row.provider_total_tokens or 0)

    ingestion_stmt = select(
        func.date_trunc(bucket, TranscriptIngestionJob.created_at).label("bucket_day"),
        func.count(TranscriptIngestionJob.id).label("ingestion_job_count"),
        func.count(TranscriptIngestionJob.id)
        .filter(TranscriptIngestionJob.status == TranscriptIngestionJobStatus.failed)
        .label("ingestion_failed_count"),
        func.coalesce(func.sum(_ingestion_duration_expression()), 0.0).label("ingested_duration_seconds"),
    ).where(TranscriptIngestionJob.created_at >= since).group_by("bucket_day").order_by("bucket_day")
    if until is not None:
        ingestion_stmt = ingestion_stmt.where(TranscriptIngestionJob.created_at < until)
    if team_id is not None:
        ingestion_stmt = ingestion_stmt.where(TranscriptIngestionJob.team_id == team_id)
    for row in db.execute(ingestion_stmt):
        bucket_day = row.bucket_day.date()
        point = points.get(bucket_day)
        if point is None:
            continue
        point.ingestion_job_count = int(row.ingestion_job_count or 0)
        point.ingestion_failed_count = int(row.ingestion_failed_count or 0)
        point.ingested_duration_seconds = float(row.ingested_duration_seconds or 0.0)

    trend_points = list(points.values())
    max_generation = max((point.provider_completed_count for point in trend_points), default=0)
    max_input_tokens = max((point.provider_input_tokens for point in trend_points), default=0)
    max_output_tokens = max((point.provider_output_tokens for point in trend_points), default=0)
    max_ingestion = max((point.ingestion_job_count for point in trend_points), default=0)
    max_audio = max((point.ingested_duration_seconds for point in trend_points), default=0.0)
    max_failures = max((point.provider_failed_count + point.ingestion_failed_count for point in trend_points), default=0)
    for point in trend_points:
        point.generation_height_pct = _trend_height(point.provider_completed_count, max_value=max_generation)
        point.input_token_height_pct = _trend_height(point.provider_input_tokens, max_value=max_input_tokens)
        point.output_token_height_pct = _trend_height(point.provider_output_tokens, max_value=max_output_tokens)
        point.ingestion_height_pct = _trend_height(point.ingestion_job_count, max_value=max_ingestion)
        point.audio_height_pct = _trend_height(point.ingested_duration_seconds, max_value=max_audio)
        point.failure_height_pct = _trend_height(point.provider_failed_count + point.ingestion_failed_count, max_value=max_failures)
    return trend_points


def _usage_comparison_trend_points(
    current: list[AdminUsageTrendPoint], previous: list[AdminUsageTrendPoint]
) -> list[dict[str, object]]:
    metric_getters = {
        "input": lambda point: point.provider_input_tokens,
        "output": lambda point: point.provider_output_tokens,
        "audio": lambda point: point.ingested_hours,
        "failure": lambda point: point.provider_failed_count + point.ingestion_failed_count,
    }
    maxima = {
        metric: max((getter(point) for point in [*current, *previous]), default=0)
        for metric, getter in metric_getters.items()
    }
    rows = []
    for index, current_point in enumerate(current):
        previous_point = (
            previous[index]
            if index < len(previous)
            else AdminUsageTrendPoint(label="No matching prior bucket", short_label=current_point.short_label)
        )
        row: dict[str, object] = {
            "label": current_point.label,
            "short_label": current_point.short_label,
            "bucket_date": current_point.bucket_date,
            "previous_label": previous_point.label,
            "previous_bucket_date": previous_point.bucket_date,
        }
        for metric, getter in metric_getters.items():
            current_value = getter(current_point)
            previous_value = getter(previous_point)
            row[f"current_{metric}"] = current_value
            row[f"previous_{metric}"] = previous_value
            row[f"current_{metric}_height_pct"] = _trend_height(current_value, max_value=maxima[metric])
            row[f"previous_{metric}_height_pct"] = _trend_height(previous_value, max_value=maxima[metric])
        rows.append(row)
    return rows


def _provider_usage_rows(db: Session, *, since, team_id: UUID | None) -> list[AdminUsageProviderRow]:
    stmt = select(
        func.coalesce(ProviderUsageEvent.provider_adapter, "unknown").label("provider_adapter"),
        func.coalesce(ProviderUsageEvent.model_name, "default").label("model_name"),
        func.count(ProviderUsageEvent.id)
        .filter(ProviderUsageEvent.event_type == ProviderUsageEventType.completed)
        .label("completed_count"),
        func.count(ProviderUsageEvent.id)
        .filter(_failure_event_clause())
        .label("failed_count"),
        func.coalesce(func.sum(ProviderUsageEvent.prompt_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(ProviderUsageEvent.completion_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(ProviderUsageEvent.total_tokens), 0).label("total_tokens"),
        func.coalesce(func.sum(ProviderUsageEvent.estimated_cost_usd), 0).label("estimated_cost_usd"),
        func.coalesce(func.avg(ProviderUsageEvent.prompt_tokens).filter(ProviderUsageEvent.event_type == ProviderUsageEventType.completed), 0.0).label("avg_input_tokens"),
        func.coalesce(func.avg(ProviderUsageEvent.completion_tokens).filter(ProviderUsageEvent.event_type == ProviderUsageEventType.completed), 0.0).label("avg_output_tokens"),
        func.coalesce(func.avg(ProviderUsageEvent.total_tokens).filter(ProviderUsageEvent.event_type == ProviderUsageEventType.completed), 0.0).label("avg_total_tokens"),
        func.coalesce(func.avg(ProviderUsageEvent.duration_ms).filter(ProviderUsageEvent.event_type == ProviderUsageEventType.completed), 0.0).label("avg_duration_ms"),
        func.coalesce(func.avg(ProviderUsageEvent.provider_duration_ms).filter(ProviderUsageEvent.event_type == ProviderUsageEventType.completed), 0.0).label("avg_provider_duration_ms"),
    ).where(ProviderUsageEvent.created_at >= since)
    if team_id is not None:
        stmt = stmt.where(ProviderUsageEvent.team_id == team_id)
    stmt = stmt.group_by("provider_adapter", "model_name").order_by(func.coalesce(func.sum(ProviderUsageEvent.total_tokens), 0).desc(), "provider_adapter", "model_name")
    return [
        AdminUsageProviderRow(
            provider_adapter=row.provider_adapter,
            model_name=row.model_name,
            completed_count=int(row.completed_count or 0),
            failed_count=int(row.failed_count or 0),
            input_tokens=int(row.input_tokens or 0),
            output_tokens=int(row.output_tokens or 0),
            total_tokens=int(row.total_tokens or 0),
            estimated_cost_usd=_coerce_decimal(row.estimated_cost_usd),
            avg_input_tokens=round(float(row.avg_input_tokens or 0.0), 1),
            avg_output_tokens=round(float(row.avg_output_tokens or 0.0), 1),
            avg_total_tokens=round(float(row.avg_total_tokens or 0.0), 1),
            avg_duration_ms=round(float(row.avg_duration_ms or 0.0), 1),
            avg_provider_duration_ms=round(float(row.avg_provider_duration_ms or 0.0), 1),
        )
        for row in db.execute(stmt)
    ]


def _generator_usage_rows(db: Session, *, since, team_id: UUID | None) -> list[AdminUsageGeneratorRow]:
    from app.models import GeneratedDocument, GeneratedDocumentStatus

    stmt = select(
        GeneratedDocument.generator_type.label("generator_type"),
        func.count(GeneratedDocument.id)
        .filter(GeneratedDocument.status == GeneratedDocumentStatus.ready)
        .label("ready_count"),
        func.count(GeneratedDocument.id)
        .filter(GeneratedDocument.status == GeneratedDocumentStatus.failed)
        .label("failed_count"),
        func.coalesce(func.sum(GeneratedDocument.input_token_count), 0).label("input_tokens"),
        func.coalesce(func.sum(GeneratedDocument.output_token_count), 0).label("output_tokens"),
        func.coalesce(func.sum(GeneratedDocument.total_token_count), 0).label("total_tokens"),
        func.coalesce(func.avg(GeneratedDocument.input_token_count), 0.0).label("avg_input_tokens"),
        func.coalesce(func.avg(GeneratedDocument.output_token_count), 0.0).label("avg_output_tokens"),
        func.coalesce(func.avg(GeneratedDocument.total_token_count), 0.0).label("avg_total_tokens"),
        func.coalesce(func.avg(GeneratedDocument.duration_ms), 0.0).label("avg_duration_ms"),
    ).where(GeneratedDocument.created_at >= since)
    if team_id is not None:
        stmt = stmt.where(GeneratedDocument.team_id == team_id)
    stmt = stmt.group_by("generator_type").order_by(func.count(GeneratedDocument.id).desc())
    return [
        AdminUsageGeneratorRow(
            generator_type=row.generator_type.value.replace("_", " "),
            ready_count=int(row.ready_count or 0),
            failed_count=int(row.failed_count or 0),
            input_tokens=int(row.input_tokens or 0),
            output_tokens=int(row.output_tokens or 0),
            total_tokens=int(row.total_tokens or 0),
            avg_input_tokens=round(float(row.avg_input_tokens or 0.0), 1),
            avg_output_tokens=round(float(row.avg_output_tokens or 0.0), 1),
            avg_total_tokens=round(float(row.avg_total_tokens or 0.0), 1),
            avg_duration_ms=round(float(row.avg_duration_ms or 0.0), 1),
        )
        for row in db.execute(stmt)
    ]


def _ingestion_usage_rows(db: Session, *, since, team_id: UUID | None) -> list[AdminUsageIngestionRow]:
    stmt = select(
        func.coalesce(TranscriptIngestionJob.stt_adapter_kind, "unknown").label("stt_adapter_kind"),
        TranscriptIngestionJob.job_kind.label("job_kind"),
        func.count(TranscriptIngestionJob.id).label("job_count"),
        func.count(TranscriptIngestionJob.id)
        .filter(TranscriptIngestionJob.status == TranscriptIngestionJobStatus.failed)
        .label("failed_count"),
        func.coalesce(func.sum(TranscriptIngestionJob.source_audio_size_bytes), 0).label("ingested_bytes"),
        func.coalesce(func.sum(_ingestion_duration_expression()), 0.0).label("ingested_duration_seconds"),
    ).where(TranscriptIngestionJob.created_at >= since)
    if team_id is not None:
        stmt = stmt.where(TranscriptIngestionJob.team_id == team_id)
    stmt = stmt.group_by("stt_adapter_kind", TranscriptIngestionJob.job_kind).order_by(func.count(TranscriptIngestionJob.id).desc())
    return [
        AdminUsageIngestionRow(
            stt_adapter_kind=row.stt_adapter_kind,
            job_kind=row.job_kind.value.replace("_", " "),
            job_count=int(row.job_count or 0),
            failed_count=int(row.failed_count or 0),
            ingested_bytes=int(row.ingested_bytes or 0),
            ingested_duration_seconds=float(row.ingested_duration_seconds or 0.0),
        )
        for row in db.execute(stmt)
    ]


def _failure_rows(db: Session, *, since, team_id: UUID | None) -> list[AdminUsageFailureRow]:
    rows_by_key: dict[tuple[str, str], int] = {}

    provider_stmt = select(
        ProviderUsageEvent.feature_type.label("feature_type"),
        ProviderUsageEvent.error_code.label("error_code"),
        ProviderUsageEvent.provider_error_code.label("provider_error_code"),
        ProviderUsageEvent.provider_http_status.label("provider_http_status"),
        func.count(ProviderUsageEvent.id).label("count"),
    ).where(ProviderUsageEvent.created_at >= since, _failure_event_clause())
    if team_id is not None:
        provider_stmt = provider_stmt.where(ProviderUsageEvent.team_id == team_id)
    provider_stmt = provider_stmt.group_by("feature_type", "error_code", "provider_error_code", "provider_http_status")
    for row in db.execute(provider_stmt):
        split_source = {
            ProviderFeatureType.consultation_split_analysis: "Split analysis",
            ProviderFeatureType.consultation_split_generation: "Split generation",
        }.get(row.feature_type)
        if split_source is not None:
            code = _safe_split_application_error_code(row.error_code)
            provider_code = _safe_split_provider_error_code(row.provider_error_code)
            provider_http_status = _safe_split_provider_http_status(row.provider_http_status)
            if provider_code is not None:
                code = f"{code} · provider {provider_code}"
            if provider_http_status is not None:
                code = f"{code} · HTTP {provider_http_status}"
            source = split_source
        else:
            source = "LLM generation"
            code = row.error_code or row.provider_error_code or "unknown"
        key = (source, code)
        rows_by_key[key] = rows_by_key.get(key, 0) + int(row.count or 0)

    ingestion_stmt = select(
        func.coalesce(TranscriptIngestionJob.error_code, "unknown").label("code"),
        func.count(TranscriptIngestionJob.id).label("count"),
    ).where(
        TranscriptIngestionJob.created_at >= since,
        TranscriptIngestionJob.status == TranscriptIngestionJobStatus.failed,
    )
    if team_id is not None:
        ingestion_stmt = ingestion_stmt.where(TranscriptIngestionJob.team_id == team_id)
    ingestion_stmt = ingestion_stmt.group_by("code")
    for row in db.execute(ingestion_stmt):
        rows_by_key[("Speech ingestion", row.code)] = int(row.count or 0)

    failure_rows = [
        AdminUsageFailureRow(source=source, code=code, count=count)
        for (source, code), count in rows_by_key.items()
    ]
    failure_rows.sort(key=lambda row: (-row.count, row.source, row.code))
    return failure_rows[:8]


def _recent_split_failure_rows(
    db: Session, *, since: datetime, until: datetime, team_id: UUID | None
) -> list[AdminUsageSplitFailureRow]:
    """Return only support-correlation metadata for recent failed split executions.

    This deliberately queries selected scalar columns rather than ORM objects:
    encrypted payloads, topic metadata, transcript references, and user identity
    cannot enter the usage presentation by accident.
    """
    failure_timestamp = func.coalesce(
        ConsultationSplitExecution.completed_at, ConsultationSplitExecution.created_at
    )
    stmt = (
        select(
            failure_timestamp.label("timestamp"),
            ConsultationSplitExecution.kind,
            ConsultationSplitExecution.id,
            ConsultationSplitExecution.analysis_id,
            ConsultationSplitExecution.batch_id,
            ConsultationSplitExecution.error_code,
            ConsultationSplitExecution.provider_error_code,
            ConsultationSplitExecution.provider_http_status,
        )
        .where(
            failure_timestamp >= since,
            failure_timestamp < until,
            ConsultationSplitExecution.status == ConsultationSplitExecutionStatus.failed,
            ConsultationSplitExecution.kind.in_(
                (ConsultationSplitExecutionKind.analysis, ConsultationSplitExecutionKind.generation)
            ),
        )
        .order_by(
            failure_timestamp.desc(),
            ConsultationSplitExecution.id.desc(),
        )
        .limit(RECENT_SPLIT_FAILURE_LIMIT)
    )
    if team_id is not None:
        stmt = stmt.where(ConsultationSplitExecution.team_id == team_id)

    rows: list[AdminUsageSplitFailureRow] = []
    for row in db.execute(stmt):
        is_analysis = row.kind is ConsultationSplitExecutionKind.analysis
        operation_id = row.analysis_id if is_analysis else row.batch_id
        # The model constraint makes these parents non-null for the selected
        # kinds. Retaining this guard keeps an inconsistent legacy row out of
        # the browser instead of exposing a malformed projection.
        if operation_id is None:
            continue
        rows.append(
            AdminUsageSplitFailureRow(
                timestamp=row.timestamp,
                stage="Analysis" if is_analysis else "Generation",
                execution_id=row.id,
                operation_id=operation_id,
                error_code=_safe_split_application_error_code(row.error_code),
                provider_error_code=_safe_split_provider_error_code(row.provider_error_code),
                provider_http_status=_safe_split_provider_http_status(row.provider_http_status),
            )
        )
    return rows


def _earliest_split_failure_timestamp(db: Session) -> datetime | None:
    return db.scalar(
        select(
            func.min(
                func.coalesce(ConsultationSplitExecution.completed_at, ConsultationSplitExecution.created_at)
            )
        ).where(
            ConsultationSplitExecution.status == ConsultationSplitExecutionStatus.failed,
            ConsultationSplitExecution.kind.in_(
                (ConsultationSplitExecutionKind.analysis, ConsultationSplitExecutionKind.generation)
            ),
        )
    )


def admin_usage_overview(
    db: Session, *, team_id: UUID | None = None, range_key: str = "30d",
    monitor_reference: str | None = None, monitor_since: datetime | None = None,
    monitor_lookup_error: str | None = None,
) -> dict[str, object]:
    now = utcnow()
    range_definitions = {
        "30d": ("Last 30 days", timedelta(days=30), "day"),
        "90d": ("Last 90 days", timedelta(days=90), "day"),
        "1y": ("Last year", timedelta(days=365), "week"),
    }
    resolved_range_key = range_key if range_key in {*range_definitions, "all"} else "30d"
    if resolved_range_key == "all":
        provider_since = db.scalar(select(func.min(ProviderUsageEvent.created_at)))
        ingestion_since = db.scalar(select(func.min(TranscriptIngestionJob.created_at)))
        split_failure_since = _earliest_split_failure_timestamp(db)
        generated_since = db.scalar(select(func.min(GeneratedDocument.created_at)))
        suggestion_since = db.scalar(select(func.min(TemplateSuggestionJob.created_at)))
        attempt_since = db.scalar(select(func.min(ProviderAttempt.authorized_at)))
        available_starts = [
            value for value in (provider_since, ingestion_since, split_failure_since, generated_since,
                                suggestion_since, attempt_since) if value is not None
        ]
        range_since = min(available_starts) if available_starts else now - timedelta(days=29)
        range_label = "All available data"
        range_bucket = "month"
        comparison_since = None
    else:
        range_label, range_delta, range_bucket = range_definitions[resolved_range_key]
        range_since = now - range_delta
        comparison_since = range_since - range_delta
    operational_since = max(range_since, monitor_since) if monitor_since is not None else range_since
    windows = []
    window_definitions = (
        ("Last 24 hours", timedelta(hours=24)),
        ("Last 7 days", timedelta(days=7)),
        ("Last 30 days", timedelta(days=30)),
    )
    for label, delta in window_definitions:
        since = now - delta
        previous_since = since - delta
        current_provider = _provider_usage_window_summary(db, since=since, until=now, team_id=team_id)
        current_ingestion = _ingestion_window_summary(db, since=since, until=now, team_id=team_id)
        previous_provider = _provider_usage_window_summary(db, since=previous_since, until=since, team_id=team_id)
        previous_ingestion = _ingestion_window_summary(db, since=previous_since, until=since, team_id=team_id)
        windows.append(
            AdminUsageWindowSummary(
                label=label,
                provider_completed_delta_pct=_format_delta_pct(
                    current_provider["provider_completed_count"],
                    previous_provider["provider_completed_count"],
                ),
                provider_input_tokens_delta_pct=_format_delta_pct(
                    current_provider["provider_input_tokens"],
                    previous_provider["provider_input_tokens"],
                ),
                provider_output_tokens_delta_pct=_format_delta_pct(
                    current_provider["provider_output_tokens"],
                    previous_provider["provider_output_tokens"],
                ),
                provider_total_tokens_delta_pct=_format_delta_pct(
                    current_provider["provider_total_tokens"],
                    previous_provider["provider_total_tokens"],
                ),
                ingested_duration_delta_pct=_format_delta_pct(
                    current_ingestion["ingested_duration_seconds"],
                    previous_ingestion["ingested_duration_seconds"],
                ),
                **current_provider,
                **current_ingestion,
            )
        )
    selected_team = db.get(Team, team_id) if team_id is not None else None
    current_summary = AdminUsageWindowSummary(
        label=range_label,
        **_provider_usage_window_summary(db, since=range_since, until=now, team_id=team_id),
        **_ingestion_window_summary(db, since=range_since, until=now, team_id=team_id),
    )
    previous_summary = AdminUsageWindowSummary(
        label="Previous equal period",
        **_provider_usage_window_summary(db, since=now, until=now, team_id=team_id),
        **_ingestion_window_summary(db, since=now, until=now, team_id=team_id),
    )
    trend_points = _usage_trend_points(db, since=range_since, team_id=team_id, until=now, bucket=range_bucket)
    if comparison_since is not None:
        previous_summary = AdminUsageWindowSummary(
            label="Previous equal period",
            **_provider_usage_window_summary(db, since=comparison_since, until=range_since, team_id=team_id),
            **_ingestion_window_summary(db, since=comparison_since, until=range_since, team_id=team_id),
        )
        previous_trend_points = _usage_trend_points(
            db, since=comparison_since, until=range_since, team_id=team_id, bucket=range_bucket
        )
    else:
        previous_trend_points = [AdminUsageTrendPoint(label="No prior period", short_label=point.short_label) for point in trend_points]
    usage_has_activity = any(
        point.provider_completed_count
        or point.provider_failed_count
        or point.provider_input_tokens
        or point.provider_output_tokens
        or point.ingestion_job_count
        or point.ingestion_failed_count
        for point in trend_points
    )
    return {
        "usage_window_summaries": windows,
        "usage_summary": current_summary,
        "usage_kpi_cards": _usage_kpi_cards(current=current_summary, previous=previous_summary, period_label=range_label),
        "usage_range_key": resolved_range_key,
        "usage_range_label": range_label,
        "usage_range_bucket": range_bucket,
        "usage_has_comparison": comparison_since is not None,
        "usage_trend_points": trend_points,
        "usage_comparison_trend_points": _usage_comparison_trend_points(trend_points, previous_trend_points),
        "usage_has_activity": usage_has_activity,
        "usage_team_rows": _team_usage_rows(db, since=range_since, team_id=team_id),
        "usage_user_rows": _user_usage_rows(db, since=range_since, team_id=team_id) if team_id is not None else [],
        "usage_provider_rows": _provider_usage_rows(db, since=range_since, team_id=team_id),
        "usage_generator_rows": _generator_usage_rows(db, since=range_since, team_id=team_id),
        "usage_ingestion_rows": _ingestion_usage_rows(db, since=range_since, team_id=team_id),
        "usage_failure_rows": _failure_rows(db, since=range_since, team_id=team_id),
        "usage_recent_split_failure_rows": _recent_split_failure_rows(
            db, since=range_since, until=now, team_id=team_id
        ),
        "usage_recent_operational_rows": _recent_operational_rows(
            db, since=operational_since, until=now, team_id=team_id,
            reference=monitor_reference,
        ),
        "usage_operational_health": _operational_health(db, team_id=team_id, now=now),
        "usage_monitor_reference": monitor_reference or "",
        "usage_monitor_since": monitor_since.isoformat(timespec="minutes") if monitor_since else "",
        "usage_monitor_lookup_error": monitor_lookup_error,
        "usage_scope_team": selected_team,
    }


def generate_temporary_password() -> str:
    return secrets.token_urlsafe(18)


BREAK_GLASS_TEMPORARY_PASSWORD_LIFETIME_MINUTES = int(os.getenv("BREAK_GLASS_TEMPORARY_PASSWORD_LIFETIME_MINUTES", "60"))


def reset_user_password_to_temporary(
    db: Session,
    user: User,
    *,
    actor: User,
    reset_mfa: bool = False,
    break_glass: bool = False,
) -> tuple[str, datetime]:
    if user.status is not UserStatus.active:
        raise AppError(403, "forbidden", "User account is not active", {"status": user.status.value})

    now = utcnow()
    temporary_password = generate_temporary_password()
    expires_at = now + timedelta(minutes=BREAK_GLASS_TEMPORARY_PASSWORD_LIFETIME_MINUTES)
    user.password_hash = hash_password(temporary_password)
    user.must_change_password = True
    user.onboarding_state = UserOnboardingState.pending_password_change
    user.temporary_password_expires_at = expires_at
    user.recovery_started_at = now
    user.recovery_started_by_user_id = actor.id
    user.recovery_mode = (
        UserRecoveryMode.break_glass_account_recovery
        if reset_mfa and break_glass
        else UserRecoveryMode.break_glass_password_reset
        if break_glass
        else UserRecoveryMode.manager_account_recovery
        if reset_mfa
        else UserRecoveryMode.manager_password_reset
    )
    if reset_mfa:
        user.mfa_enabled = False
        for method in db.scalars(select(UserMfaMethod).where(UserMfaMethod.user_id == user.id)):
            db.delete(method)
        for code in db.scalars(select(UserRecoveryCode).where(UserRecoveryCode.user_id == user.id)):
            db.delete(code)

    reset_purposes = {
        AuthEmailTokenPurpose.password_reset,
        AuthEmailTokenPurpose.manager_password_reset,
        AuthEmailTokenPurpose.manager_account_recovery,
    }
    active_tokens = db.scalars(
        select(AuthEmailToken).where(
            AuthEmailToken.user_id == user.id,
            AuthEmailToken.purpose.in_(reset_purposes),
            AuthEmailToken.used_at.is_(None),
        )
    )
    for token in active_tokens:
        token.used_at = now
        db.add(token)

    db.add(user)
    db.commit()
    revoke_reason = "break_glass_recovery" if break_glass else ("manager_account_recovery" if reset_mfa else "manager_password_reset")
    revoke_sessions_for_user(db, user, reason=revoke_reason)
    revoke_trusted_devices_for_user(db, user, reason=revoke_reason)
    return temporary_password, expires_at


def create_team(db: Session, payload: TeamCreate, *, actor: User) -> Team:
    stripped_name = payload.name.strip()
    default_retention_days = validate_retention_days(payload.default_retention_days)
    quota_defaults = {
        field: getattr(payload, field)
        for field in TEAM_QUOTA_LIMITS
    }
    for field, maximum in TEAM_QUOTA_LIMITS.items():
        value = quota_defaults[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > maximum:
            raise AppError(422, "quota_limit_invalid", "Team quota default is invalid", {"field": field, "max": maximum})
    team = Team(
        name=stripped_name,
        name_key=normalize_team_name_key(stripped_name),
        status=payload.status,
        default_retention_days=default_retention_days,
        **quota_defaults,
    )
    db.add(team)
    try:
        db.flush()
        ensure_builtin_default_assets(db, actor)
        seed_team_default_assets(db, team=team, actor=actor)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "conflict", "Team already exists", {"resource": "team", "field": "name"}) from exc
    db.refresh(team)
    record_security_event(
        db,
        action="team_created",
        actor=actor,
        team_id=team.id,
        details={"category": "account", "outcome": "success", "object_type": "team", "object_id": str(team.id), "status": team.status.value},
    )
    return team


def update_team_quota_defaults(
    db: Session,
    actor: User,
    *,
    team_id: UUID,
    default_daily_token_limit: int,
    default_monthly_token_limit: int,
    default_daily_audio_seconds_limit: int,
    default_monthly_audio_seconds_limit: int,
) -> Team:
    if not actor.is_system_admin:
        raise AppError(403, "forbidden", "System-admin team quota access required")
    team = db.get(Team, team_id)
    if team is None:
        raise AppError(404, "not_found", "Team not found")
    requested = {
        "default_daily_token_limit": default_daily_token_limit,
        "default_monthly_token_limit": default_monthly_token_limit,
        "default_daily_audio_seconds_limit": default_daily_audio_seconds_limit,
        "default_monthly_audio_seconds_limit": default_monthly_audio_seconds_limit,
    }
    for field, maximum in TEAM_QUOTA_LIMITS.items():
        value = requested[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > maximum:
            raise AppError(422, "quota_limit_invalid", "Team quota default is invalid", {"field": field, "max": maximum})
    previous = {field: getattr(team, field) for field in TEAM_QUOTA_LIMITS}
    for field, value in requested.items():
        setattr(team, field, value)
    db.add(team)
    db.commit()
    db.refresh(team)
    record_security_event(
        db,
        action="team_quota_defaults_updated",
        actor=actor,
        team_id=team.id,
        details={
            "category": "account",
            "outcome": "success",
            "old_limits": previous,
            "new_limits": requested,
        },
    )
    return team


def list_teams(db: Session) -> list[Team]:
    return list(db.scalars(select(Team).order_by(Team.created_at.desc())))


def update_team_default_retention(db: Session, actor: User, *, team_id: UUID, default_retention_days: int) -> Team:
    if not actor.is_system_admin:
        raise AppError(403, "forbidden", "System-admin team retention access required")
    team = db.get(Team, team_id)
    if team is None:
        raise AppError(404, "not_found", "Team not found")
    old_days = team.default_retention_days
    team.default_retention_days = validate_retention_days(default_retention_days)
    db.add(team)
    db.commit()
    db.refresh(team)
    record_security_event(
        db,
        action="team_default_retention_updated",
        actor=actor,
        team_id=team.id,
        details={"category": "account", "outcome": "success", "old_days": old_days, "new_days": team.default_retention_days},
    )
    return team


def _resolve_team_for_admin_delete(db: Session, actor: User, *, team_id: UUID) -> Team:
    if not actor.is_system_admin:
        raise AppError(403, "forbidden", "System-admin team deletion access required")
    team = db.get(Team, team_id)
    if team is None:
        raise AppError(404, "not_found", "Team not found", {"resource": "team", "team_id": str(team_id)})
    return team


def _resolve_manageable_team(db: Session, *, actor: User | None, payload: UserCreate) -> tuple[Team | None, TeamRole | None]:
    if payload.is_system_admin:
        if actor is not None and not actor.is_system_admin:
            raise AppError(403, "forbidden", "Only system admins may create system-admin accounts")
        team = None
        team_role = None
    else:
        if payload.team_id is None:
            raise AppError(
                422,
                "business_rule_violation",
                "Team is required for non-system-admin users",
                {"field": "team_id"},
            )
        if payload.team_role is None:
            raise AppError(
                422,
                "business_rule_violation",
                "Team role is required for non-system-admin users",
                {"field": "team_role"},
            )
        team = db.get(Team, payload.team_id)
        if not team:
            raise AppError(404, "not_found", "Team not found", {"resource": "team", "team_id": str(payload.team_id)})
        team_role = payload.team_role

        if actor is not None and not actor.is_system_admin:
            if actor.team_role is not TeamRole.leader or actor.team_id != team.id:
                raise AppError(403, "forbidden", "Leaders may only manage users in their own team")

    return team, team_role


def _create_user_record(db: Session, payload: UserCreate, *, actor: User | None, onboarding_state: UserOnboardingState) -> User:
    team, team_role = _resolve_manageable_team(db, actor=actor, payload=payload)
    user = User(
        full_name=payload.full_name.strip() if payload.full_name else None,
        email=normalize_email(payload.email),
        password_hash=hash_password(payload.temporary_password),
        team_id=team.id if team else None,
        team_role=team_role,
        is_system_admin=payload.is_system_admin,
        status=payload.status,
        mfa_required=payload.mfa_required,
        mfa_enabled=False,
        must_change_password=onboarding_state is UserOnboardingState.pending_password_change,
        onboarding_state=onboarding_state,
        example_consultation_pending=(not payload.is_system_admin and team is not None),
        **(
            {
                "daily_token_limit": team.default_daily_token_limit,
                "monthly_token_limit": team.default_monthly_token_limit,
                "daily_audio_seconds_limit": team.default_daily_audio_seconds_limit,
                "monthly_audio_seconds_limit": team.default_monthly_audio_seconds_limit,
            }
            if team is not None and not payload.is_system_admin
            else {}
        ),
    )
    db.add(user)
    return user


def create_user(db: Session, payload: UserCreate, *, actor: User | None = None) -> User:
    user = _create_user_record(
        db,
        payload,
        actor=actor,
        onboarding_state=UserOnboardingState.pending_password_change,
    )
    try:
        db.flush()
        ensure_user_dek(db, user=user)
        if not user.is_system_admin:
            ensure_default_smart_phrase_for_user(db, user, commit=False)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "conflict", "User already exists", {"resource": "user", "field": "email"}) from exc
    except Exception:
        db.rollback()
        raise
    db.refresh(user)
    record_security_event(
        db,
        action="user_created",
        actor=actor,
        target=user,
        team_id=user.team_id,
        details={
            "category": "account",
            "outcome": "success",
            "target_user_id": str(user.id),
            "target_team_role": user.team_role.value if user.team_role else None,
            "target_status": user.status.value,
            "target_is_system_admin": user.is_system_admin,
        },
    )
    return user


def list_users(db: Session) -> list[User]:
    stmt = select(User).options(joinedload(User.team)).order_by(User.created_at.desc())
    return list(db.scalars(stmt).unique())


def list_manageable_users(db: Session, actor: User) -> list[User]:
    stmt = select(User).options(joinedload(User.team)).order_by(User.created_at.desc())
    if actor.is_system_admin:
        return list(db.scalars(stmt).unique())
    if actor.team_role is not TeamRole.leader or actor.team_id is None:
        raise AppError(403, "forbidden", "User-management access required")
    stmt = stmt.where(User.team_id == actor.team_id, User.is_system_admin.is_(False))
    return list(db.scalars(stmt).unique())


def _active_system_admin_count(db: Session) -> int:
    return (
        db.scalar(
            select(func.count(User.id)).where(
                User.is_system_admin.is_(True),
                User.status == UserStatus.active,
            )
        )
        or 0
    )


def _log_account_lifecycle_event(*, db: Session, actor: User, target: User, event: str) -> None:
    audit_logger.info(
        "account_lifecycle",
        extra={
            "event": event,
            "actor_user_id": str(actor.id),
            "actor_is_system_admin": actor.is_system_admin,
            "actor_team_id": str(actor.team_id) if actor.team_id else None,
            "target_user_id": str(target.id),
            "target_team_id": str(target.team_id) if target.team_id else None,
            "target_team_role": target.team_role.value if target.team_role else None,
            "target_status": target.status.value,
            "target_is_system_admin": target.is_system_admin,
        },
    )
    record_security_event(
        db,
        action=event,
        actor=actor,
        target=target,
        team_id=target.team_id,
        details={
            "category": "account",
            "outcome": "success",
            "target_user_id": str(target.id),
            "target_team_role": target.team_role.value if target.team_role else None,
            "target_status": target.status.value,
            "target_is_system_admin": target.is_system_admin,
        },
    )


def _get_manageable_user(db: Session, actor: User, user_id) -> User:
    # User deletion cascades the optional app-preference row.  Take the same
    # owner lock as preference writers and consultation-split runtime before
    # deciding or deleting, so a preference turn-off cannot commit mid-flight.
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise AppError(404, "not_found", "User not found", {"resource": "user", "user_id": str(user_id)})
    if actor.id == user.id:
        raise AppError(403, "forbidden", "You may not manage your own account")
    if actor.is_system_admin:
        return user
    if actor.team_role is not TeamRole.leader or actor.team_id is None:
        raise AppError(403, "forbidden", "User-management access required")
    if user.is_system_admin:
        raise AppError(403, "forbidden", "Leaders may not manage system-admin accounts")
    if user.team_id != actor.team_id:
        raise AppError(403, "forbidden", "Leaders may only manage users in their own team")
    return user


def suspend_user(db: Session, actor: User, user_id) -> User:
    user = _get_manageable_user(db, actor, user_id)
    if user.status is UserStatus.suspended:
        raise AppError(409, "conflict", "User is already suspended", {"status": user.status.value})
    if user.is_system_admin and user.status is UserStatus.active and _active_system_admin_count(db) <= 1:
        raise AppError(409, "conflict", "Cannot suspend the last active system-admin account")

    user.status = UserStatus.suspended
    db.add(user)
    db.commit()
    revoke_sessions_for_user(db, user, reason="user_suspended")
    revoke_trusted_devices_for_user(db, user, reason="user_suspended")
    db.refresh(user)
    _log_account_lifecycle_event(db=db, actor=actor, target=user, event="account_suspended")
    return user


def reactivate_user(db: Session, actor: User, user_id) -> User:
    user = _get_manageable_user(db, actor, user_id)
    if user.status not in {UserStatus.suspended, UserStatus.disabled}:
        raise AppError(409, "conflict", "User is not eligible for reactivation", {"status": user.status.value})

    user.status = UserStatus.active
    user.must_change_password = True
    user.onboarding_state = UserOnboardingState.pending_password_change
    user.mfa_enabled = False

    for method in list(user.mfa_methods):
        db.delete(method)
    for code in list(user.recovery_codes):
        db.delete(code)

    db.add(user)
    db.commit()
    revoke_sessions_for_user(db, user, reason="user_reactivated_reset")
    revoke_trusted_devices_for_user(db, user, reason="user_reactivated_reset")
    db.refresh(user)
    _log_account_lifecycle_event(db=db, actor=actor, target=user, event="account_reactivated")
    return user


def _delete_user_rows(db: Session, actor: User, *, user: User) -> list[UUID]:
    now = utcnow()
    owns_active_audit_hold = db.scalar(
        select(SecurityAuditEventHold.id).where(
            SecurityAuditEventHold.owner_user_id == user.id,
            SecurityAuditEventHold.released_at.is_(None),
            SecurityAuditEventHold.expires_at > now,
        ).limit(1)
    )
    if owns_active_audit_hold is not None:
        raise AppError(
            409,
            "conflict",
            "Release or transfer this system administrator's active audit holds before deleting the account",
        )

    expired_audit_holds = db.scalars(
        select(SecurityAuditEventHold).where(
            SecurityAuditEventHold.owner_user_id == user.id,
            SecurityAuditEventHold.released_at.is_(None),
            SecurityAuditEventHold.expires_at <= now,
        )
    )
    for hold in expired_audit_holds:
        # A held event no longer has an active hold once its fixed deadline
        # passes. Mark that state before the owner FK is cleared on deletion.
        hold.owner_user_id = None
        hold.released_at = hold.expires_at
        db.add(hold)

    linked_requests = db.scalars(select(AccountRequest).where(AccountRequest.linked_user_id == user.id))
    for request in linked_requests:
        request.linked_user_id = None
        db.add(request)

    reviewed_requests = db.scalars(select(AccountRequest).where(AccountRequest.reviewed_by_user_id == user.id))
    for request in reviewed_requests:
        request.reviewed_by_user_id = None
        db.add(request)

    created_auth_email_tokens = db.scalars(select(AuthEmailToken).where(AuthEmailToken.created_by_user_id == user.id))
    for token in created_auth_email_tokens:
        token.created_by_user_id = actor.id
        db.add(token)

    stt_configs_created = db.scalars(select(TeamSttConfig).where(TeamSttConfig.created_by_user_id == user.id))
    for config in stt_configs_created:
        config.created_by_user_id = actor.id
        db.add(config)

    stt_configs_updated = db.scalars(select(TeamSttConfig).where(TeamSttConfig.updated_by_user_id == user.id))
    for config in stt_configs_updated:
        config.updated_by_user_id = actor.id
        db.add(config)

    stt_selections = db.scalars(select(TeamSttSelection).where(TeamSttSelection.selected_by_user_id == user.id))
    for selection in stt_selections:
        selection.selected_by_user_id = actor.id
        db.add(selection)

    llm_configs_created = db.scalars(select(TeamLlmConfig).where(TeamLlmConfig.created_by_user_id == user.id))
    for config in llm_configs_created:
        config.created_by_user_id = actor.id
        db.add(config)

    llm_configs_updated = db.scalars(select(TeamLlmConfig).where(TeamLlmConfig.updated_by_user_id == user.id))
    for config in llm_configs_updated:
        config.updated_by_user_id = actor.id
        db.add(config)

    llm_selections = db.scalars(select(TeamLlmSelection).where(TeamLlmSelection.selected_by_user_id == user.id))
    for selection in llm_selections:
        selection.selected_by_user_id = actor.id
        db.add(selection)

    hallucination_check_selections = db.scalars(select(TeamHallucinationCheckSelection).where(TeamHallucinationCheckSelection.selected_by_user_id == user.id))
    for selection in hallucination_check_selections:
        selection.selected_by_user_id = actor.id
        db.add(selection)

    deidentification_providers_created = db.scalars(select(DeidentificationProvider).where(DeidentificationProvider.created_by_user_id == user.id))
    for provider in deidentification_providers_created:
        provider.created_by_user_id = actor.id
        db.add(provider)

    deidentification_providers_updated = db.scalars(select(DeidentificationProvider).where(DeidentificationProvider.updated_by_user_id == user.id))
    for provider in deidentification_providers_updated:
        provider.updated_by_user_id = actor.id
        db.add(provider)

    deidentification_assignments = db.scalars(select(TeamDeidentificationProviderAssignment).where(TeamDeidentificationProviderAssignment.assigned_by_user_id == user.id))
    for assignment in deidentification_assignments:
        assignment.assigned_by_user_id = actor.id
        db.add(assignment)

    deidentification_selections = db.scalars(select(TeamDeidentificationSelection).where(TeamDeidentificationSelection.selected_by_user_id == user.id))
    for selection in deidentification_selections:
        selection.selected_by_user_id = actor.id
        db.add(selection)

    clinical_nlp_selections = db.scalars(select(TeamClinicalNlpSelection).where(TeamClinicalNlpSelection.selected_by_user_id == user.id))
    for selection in clinical_nlp_selections:
        selection.selected_by_user_id = actor.id
        db.add(selection)

    team_templates_created = db.scalars(select(PromptTemplate).where(PromptTemplate.scope == TemplateScope.team, PromptTemplate.created_by_user_id == user.id))
    for template in team_templates_created:
        template.created_by_user_id = actor.id
        db.add(template)

    template_versions_created = db.scalars(select(PromptTemplateVersion).where(PromptTemplateVersion.created_by_user_id == user.id))
    for version in template_versions_created:
        version.created_by_user_id = actor.id
        db.add(version)

    default_templates_created = db.scalars(select(DefaultPromptTemplate).where(DefaultPromptTemplate.created_by_user_id == user.id))
    for template in default_templates_created:
        template.created_by_user_id = actor.id
        db.add(template)

    default_template_versions_created = db.scalars(select(DefaultPromptTemplateVersion).where(DefaultPromptTemplateVersion.created_by_user_id == user.id))
    for version in default_template_versions_created:
        version.created_by_user_id = actor.id
        db.add(version)

    personal_templates = db.scalars(select(PromptTemplate).where(PromptTemplate.scope == TemplateScope.user, PromptTemplate.owner_user_id == user.id))
    for template in personal_templates:
        db.delete(template)

    team_quick_actions_created = db.scalars(select(QuickAction).where(QuickAction.scope == TemplateScope.team, QuickAction.created_by_user_id == user.id))
    for quick_action in team_quick_actions_created:
        quick_action.created_by_user_id = actor.id
        db.add(quick_action)

    quick_action_versions_created = db.scalars(select(QuickActionVersion).where(QuickActionVersion.created_by_user_id == user.id))
    for version in quick_action_versions_created:
        version.created_by_user_id = actor.id
        db.add(version)

    default_quick_actions_created = db.scalars(select(DefaultQuickAction).where(DefaultQuickAction.created_by_user_id == user.id))
    for quick_action in default_quick_actions_created:
        quick_action.created_by_user_id = actor.id
        db.add(quick_action)

    default_quick_action_versions_created = db.scalars(select(DefaultQuickActionVersion).where(DefaultQuickActionVersion.created_by_user_id == user.id))
    for version in default_quick_action_versions_created:
        version.created_by_user_id = actor.id
        db.add(version)

    personal_quick_actions = db.scalars(select(QuickAction).where(QuickAction.scope == TemplateScope.user, QuickAction.owner_user_id == user.id))
    for quick_action in personal_quick_actions:
        db.delete(quick_action)

    transcripts = db.scalars(select(Transcript).where(Transcript.owner_user_id == user.id))
    transcript_rows = list(transcripts)
    transcript_ids = [transcript.id for transcript in transcript_rows]
    terminalize_attempts_for_owner(db, user.id, utcnow())
    cleanup_job_ids = queue_retry_source_cleanup_for_transcripts(db, transcript_ids=transcript_ids)
    if transcript_ids:
        # SQLAlchemy removes transcript versions/redaction runs before the
        # transcript-root cascade. Detach split provenance first so account
        # deletion follows the same boundary as direct transcript deletion.
        db.execute(
            update(ConsultationSplitAnalysis)
            .where(ConsultationSplitAnalysis.transcript_id.in_(transcript_ids))
            .values(transcript_version_id=None, redaction_run_id=None)
        )
        db.execute(
            update(ConsultationSplitBatch)
            .where(ConsultationSplitBatch.transcript_id.in_(transcript_ids))
            .values(materialization_transcript_version_id=None)
        )
        delete_dispatches_for_sources(
            db,
            generated_document_ids=list(
                db.scalars(select(GeneratedDocument.id).where(GeneratedDocument.transcript_id.in_(transcript_ids)))
            ),
            ingestion_job_ids=list(
                db.scalars(select(TranscriptIngestionJob.id).where(TranscriptIngestionJob.transcript_id.in_(transcript_ids)))
            ),
            consultation_split_execution_ids=list(
                db.scalars(select(ConsultationSplitExecution.id).where(ConsultationSplitExecution.transcript_id.in_(transcript_ids)))
            ),
            consultation_split_intent_ids=list(
                db.scalars(select(ConsultationSplitIntent.id).where(ConsultationSplitIntent.transcript_id.in_(transcript_ids)))
            ),
        )
    for transcript in transcript_rows:
        db.delete(transcript)

    db.flush()
    db.delete(user)
    return cleanup_job_ids


def delete_user(db: Session, actor: User, user_id) -> None:
    user = _get_manageable_user(db, actor, user_id)
    if user.is_system_admin and user.status is UserStatus.active and _active_system_admin_count(db) <= 1:
        raise AppError(409, "conflict", "Cannot delete the last active system-admin account")

    revoke_sessions_for_user(db, user, reason="user_deleted")
    revoke_trusted_devices_for_user(db, user, reason="user_deleted")
    target_user_id = user.id
    target_team_id = user.team_id
    target_team_role = user.team_role.value if user.team_role else None
    target_is_system_admin = user.is_system_admin
    cleanup_job_ids = _delete_user_rows(db, actor, user=user)
    db.commit()
    process_transcript_audio_cleanup_jobs(db, job_ids=cleanup_job_ids)
    audit_logger.info(
        "account_lifecycle",
        extra={
            "event": "account_deleted",
            "actor_user_id": str(actor.id),
            "actor_is_system_admin": actor.is_system_admin,
            "actor_team_id": str(actor.team_id) if actor.team_id else None,
            "target_user_id": str(target_user_id),
            "target_team_id": str(target_team_id) if target_team_id else None,
            "target_team_role": target_team_role,
            "target_status": "deleted",
            "target_is_system_admin": target_is_system_admin,
        },
    )
    record_security_event(
        db,
        action="account_deleted",
        actor=actor,
        team_id=target_team_id,
        details={
            "category": "account",
            "outcome": "success",
            "target_user_id": str(target_user_id),
            "target_team_role": target_team_role,
            "target_status": "deleted",
            "target_is_system_admin": target_is_system_admin,
        },
    )


def delete_team(db: Session, actor: User, *, team_id: UUID) -> None:
    team = _resolve_team_for_admin_delete(db, actor, team_id=team_id)
    team_users = list(db.scalars(select(User).where(User.team_id == team.id).order_by(User.created_at.asc(), User.id.asc())))
    team_user_ids = [user.id for user in team_users]
    team_id_for_audit = team.id
    team_user_count = len(team_users)
    for user in team_users:
        if user.is_system_admin:
            record_security_event(
                db,
                action="team_delete_blocked",
                actor=actor,
                team_id=team.id,
                details={
                    "category": "account",
                    "outcome": "blocked",
                    "reason_code": "team_contains_system_admin",
                    "object_type": "team",
                    "object_id": str(team.id),
                    "blocked_user_id": str(user.id),
                    "team_user_count": team_user_count,
                },
            )
            raise AppError(409, "conflict", "Cannot delete a team that still contains a system-admin account", {"team_id": str(team.id), "user_id": str(user.id)})

    transcript_audio_cleanup_job_ids: list[UUID] = []
    try:
        provider_events = db.scalars(select(ProviderUsageEvent).where(ProviderUsageEvent.team_id == team.id))
        for event in provider_events:
            db.delete(event)

        team_requests = db.scalars(select(AccountRequest).where(AccountRequest.requested_team_name_key == team.name_key))
        for request in team_requests:
            db.delete(request)

        for selection in db.scalars(select(TeamSttSelection).where(TeamSttSelection.team_id == team.id)):
            db.delete(selection)
        llm_selection = db.scalar(select(TeamLlmSelection).where(TeamLlmSelection.team_id == team.id))
        if llm_selection is not None:
            db.delete(llm_selection)
        backup_selection = db.scalar(select(TeamLlmBackupSelection).where(TeamLlmBackupSelection.team_id == team.id))
        if backup_selection is not None:
            db.delete(backup_selection)
        hallucination_check_selection = db.scalar(select(TeamHallucinationCheckSelection).where(TeamHallucinationCheckSelection.team_id == team.id))
        if hallucination_check_selection is not None:
            db.delete(hallucination_check_selection)
        deidentification_selection = db.scalar(select(TeamDeidentificationSelection).where(TeamDeidentificationSelection.team_id == team.id))
        if deidentification_selection is not None:
            db.delete(deidentification_selection)
        clinical_nlp_selection = db.scalar(select(TeamClinicalNlpSelection).where(TeamClinicalNlpSelection.team_id == team.id))
        if clinical_nlp_selection is not None:
            db.delete(clinical_nlp_selection)
        for assignment in db.scalars(select(TeamDeidentificationProviderAssignment).where(TeamDeidentificationProviderAssignment.team_id == team.id)):
            db.delete(assignment)
        db.flush()

        for config in db.scalars(select(TeamSttConfig).where(TeamSttConfig.team_id == team.id)):
            if config.vault_secret_ref:
                queue_provider_secret_cleanup(db, kind=ProviderSecretCleanupKind.stt, secret_refs=[config.vault_secret_ref])
            db.delete(config)
        for config in db.scalars(select(TeamLlmConfig).where(TeamLlmConfig.team_id == team.id)):
            if config.vault_secret_ref:
                queue_provider_secret_cleanup(db, kind=ProviderSecretCleanupKind.llm, secret_refs=[config.vault_secret_ref])
            db.delete(config)

        for template in db.scalars(select(PromptTemplate).where(PromptTemplate.scope == TemplateScope.team, PromptTemplate.team_id == team.id)):
            db.delete(template)
        for quick_action in db.scalars(select(QuickAction).where(QuickAction.scope == TemplateScope.team, QuickAction.team_id == team.id)):
            db.delete(quick_action)
        db.flush()

        for user in team_users:
            # Team deletion takes each owner root in the same stable order as
            # the initial user list.  This covers the cascaded optional
            # preference row and prevents a split runtime from crossing the
            # deletion boundary for that owner.
            locked_user = db.scalar(select(User).where(User.id == user.id).with_for_update())
            if locked_user is None:
                raise AppError(409, "conflict", "User changed during team deletion", {"user_id": str(user.id)})
            transcript_audio_cleanup_job_ids.extend(_delete_user_rows(db, actor, user=locked_user))

        if team_user_ids:
            for event in db.scalars(select(ProviderUsageEvent).where(ProviderUsageEvent.owner_user_id.in_(team_user_ids))):
                db.delete(event)

        db.flush()
        db.delete(team)
        db.commit()
    except Exception:
        db.rollback()
        raise

    process_transcript_audio_cleanup_jobs(db, job_ids=transcript_audio_cleanup_job_ids)
    record_security_event(
        db,
        action="team_deleted",
        actor=actor,
        team_id=None,
        details={"category": "account", "outcome": "success", "object_type": "team", "object_id": str(team_id_for_audit), "team_user_count": team_user_count},
    )


def user_count(db: Session) -> int:
    return db.scalar(select(func.count(User.id))) or 0


BOOTSTRAP_ADMIN_LOCK_KEY = 5_802_001_202_608_03


def bootstrap_admin_is_configured() -> bool:
    return bool(os.getenv("BOOTSTRAP_ADMIN_TOKEN", "").strip()) or app_environment() not in {"production", "prod"}


def _verify_bootstrap_admin_token(submitted_token: str | None) -> None:
    configured_token = os.getenv("BOOTSTRAP_ADMIN_TOKEN", "").strip()
    if not configured_token:
        if app_environment() in {"production", "prod"}:
            raise AppError(403, "forbidden", "System-administrator bootstrap is not configured")
        return
    if not submitted_token or not secrets.compare_digest(submitted_token, configured_token):
        raise AppError(403, "forbidden", "System-administrator bootstrap credential is invalid")


def create_bootstrap_admin(db: Session, *, email: str, password: str, bootstrap_token: str | None = None) -> User:
    _verify_bootstrap_admin_token(bootstrap_token)
    try:
        db.execute(select(func.pg_advisory_xact_lock(BOOTSTRAP_ADMIN_LOCK_KEY)))
        if user_count(db) != 0:
            raise AppError(403, "forbidden", "Bootstrap is disabled once a user exists")
        validate_password_strength(password)
        payload = UserCreate(
            email=email,
            temporary_password=password,
            is_system_admin=True,
            mfa_required=True,
        )
        user = _create_user_record(
            db,
            payload,
            actor=None,
            onboarding_state=UserOnboardingState.pending_totp_enrollment,
        )
        user.must_change_password = False
        db.flush()
        ensure_user_dek(db, user=user)
        ensure_builtin_default_assets(db, user)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "conflict", "User already exists", {"resource": "user", "field": "email"}) from exc
    except Exception:
        db.rollback()
        raise
    db.refresh(user)
    record_security_event(
        db,
        action="bootstrap_system_admin_created",
        actor=user,
        target=user,
        details={"category": "account", "outcome": "success", "target_user_id": str(user.id), "target_is_system_admin": True},
    )
    return user


def create_account_request(db: Session, payload: AccountRequestCreate) -> AccountRequest | None:
    normalized_email = normalize_email(payload.requested_email)
    team_name = payload.requested_team_name.strip()
    team_name_key = normalize_team_name_key(team_name)

    existing_user = db.scalar(select(User).where(User.email == normalized_email))
    if existing_user is not None:
        return None

    duplicate_pending = db.scalar(
        select(AccountRequest).where(
            AccountRequest.requested_email == normalized_email,
            AccountRequest.requested_team_name_key == team_name_key,
            AccountRequest.status == AccountRequestStatus.pending,
        )
    )
    if duplicate_pending is not None:
        return None

    request = AccountRequest(
        requested_name=payload.requested_name.strip(),
        requested_email=normalized_email,
        requested_team_name=team_name,
        requested_team_name_key=team_name_key,
        request_details=payload.request_details.strip() if payload.request_details else None,
    )
    db.add(request)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        diagnostic = getattr(getattr(exc, "orig", None), "diag", None)
        if getattr(diagnostic, "constraint_name", None) == "uq_account_requests_pending_email_team":
            return None
        raise
    db.refresh(request)
    record_security_event(
        db,
        action="account_request_created",
        team_id=None,
        details={
            "category": "account",
            "outcome": "success",
            "object_type": "account_request",
            "object_id": str(request.id),
            "requested_team_name_key": request.requested_team_name_key,
        },
    )
    return request


def _request_scope_clause(actor: User):
    if actor.is_system_admin:
        return True
    if actor.team_role is not TeamRole.leader or actor.team is None:
        raise AppError(403, "forbidden", "Account-request review access required")
    return AccountRequest.requested_team_name_key == actor.team.name_key


def list_manageable_account_requests(db: Session, actor: User) -> list[AccountRequest]:
    stmt = select(AccountRequest).order_by(AccountRequest.created_at.desc())
    scope = _request_scope_clause(actor)
    if scope is not True:
        stmt = stmt.where(scope)
    return list(db.scalars(stmt))


def _get_manageable_account_request(db: Session, actor: User, request_id) -> AccountRequest:
    request = db.get(AccountRequest, request_id)
    if request is None:
        raise AppError(404, "not_found", "Account request not found", {"resource": "account_request", "request_id": str(request_id)})
    scope = _request_scope_clause(actor)
    if scope is not True and request.requested_team_name_key != actor.team.name_key:
        raise AppError(403, "forbidden", "Account-request review access required")
    return request


def approve_account_request(db: Session, actor: User, request_id, payload: AccountRequestApprove) -> tuple[AccountRequest, User]:
    request = _get_manageable_account_request(db, actor, request_id)
    if request.status is not AccountRequestStatus.pending:
        raise AppError(409, "conflict", "Account request is no longer pending", {"resource": "account_request"})

    if actor.is_system_admin:
        team_id = payload.team_id
    else:
        team_id = actor.team_id

    user = _create_user_record(
        db,
        UserCreate(
            full_name=payload.full_name or request.requested_name,
            email=request.requested_email,
            temporary_password=payload.temporary_password,
            team_id=team_id,
            team_role=payload.team_role,
            is_system_admin=False,
            mfa_required=payload.mfa_required,
        ),
        actor=actor,
        onboarding_state=UserOnboardingState.pending_password_change,
    )
    request.status = AccountRequestStatus.approved
    request.review_notes = payload.review_notes.strip() if payload.review_notes else None
    request.reviewed_by_user_id = actor.id
    request.linked_user = user
    request.reviewed_at = utcnow()
    db.add(request)
    try:
        db.flush()
        ensure_user_dek(db, user=user)
        ensure_default_smart_phrase_for_user(db, user, commit=False)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "conflict", "User already exists", {"resource": "user", "field": "email"}) from exc
    except Exception:
        db.rollback()
        raise
    db.refresh(request)
    db.refresh(user)
    record_security_event(
        db,
        action="account_request_approved",
        actor=actor,
        target=user,
        team_id=user.team_id,
        details={
            "category": "account",
            "outcome": "success",
            "object_type": "account_request",
            "object_id": str(request.id),
            "target_user_id": str(user.id),
            "target_team_role": user.team_role.value if user.team_role else None,
        },
    )
    return request, user


def reject_account_request(db: Session, actor: User, request_id, payload: AccountRequestReject) -> AccountRequest:
    request = _get_manageable_account_request(db, actor, request_id)
    if request.status is not AccountRequestStatus.pending:
        raise AppError(409, "conflict", "Account request is no longer pending", {"resource": "account_request"})
    request.status = AccountRequestStatus.rejected
    request.review_notes = payload.review_notes.strip()
    request.reviewed_by_user_id = actor.id
    request.reviewed_at = utcnow()
    db.add(request)
    db.commit()
    db.refresh(request)
    record_security_event(
        db,
        action="account_request_rejected",
        actor=actor,
        details={"category": "account", "outcome": "success", "object_type": "account_request", "object_id": str(request.id), "requested_team_name_key": request.requested_team_name_key},
    )
    return request
