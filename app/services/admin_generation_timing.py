"""Read retained job and provider timing metadata for the admin Usage view."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AttemptKind,
    AttemptOutcome,
    GeneratedDocument,
    GeneratedDocumentGeneratorType,
    GeneratedDocumentStatus,
    ProviderAttempt,
    Transcript,
)

_JOB_LIMIT = 120
_ATTEMPT_LIMIT = 40
_BINS = (
    (0, 5_000, "<5s"), (5_000, 15_000, "5–15s"), (15_000, 30_000, "15–30s"),
    (30_000, 60_000, "30–60s"), (60_000, 180_000, "1–3m"),
    (180_000, 600_000, "3–10m"), (600_000, None, "10m+"),
)
_HTTP_FAILURES = frozenset({400, 401, 403, 404, 408, 422, 429, 500, 502, 503, 504})
_ERROR_LABELS = {
    "generation_enqueue_failed": "Dispatch failed",
    "generation_snapshot_invalid": "Invalid job input",
    "llm_generation_invalid_json": "Invalid output",
    "llm_generation_truncated": "Truncated output",
    "llm_generation_preparation_failed": "Preparation failed",
    "llm_invalid_credential": "Credential unavailable",
    "llm_provider_bad_response": "Invalid response",
    "llm_provider_timeout": "Local timeout",
    "llm_provider_unreachable": "Connection error",
    "hallucination_check_invalid_response": "Invalid output",
    "redaction_failed": "Redaction failed",
    "quota_exceeded": "Quota exceeded",
}
_TRANSPORT_LABELS = {
    "timeout": "Local timeout",
    "rate_limited": "Rate limited",
    "connection_error": "Connection error",
    "provider_error": "Provider error",
}


def _elapsed_ms(start: datetime, end: datetime) -> int:
    return (end - start) // timedelta(milliseconds=1)


def _http_label(status: object) -> str | None:
    if type(status) is not int or not 400 <= status <= 599:
        return None
    return f"HTTP {status}" if status in _HTTP_FAILURES else "Other HTTP error"


def _transport_events(attempt):
    return [event for event in attempt.transport_events_json or () if isinstance(event, dict)]


def _attempt_failures(attempt) -> set[str]:
    labels = set()
    for event in _transport_events(attempt):
        label = _http_label(event.get("http_status")) or _TRANSPORT_LABELS.get(event.get("code"))
        if label is not None:
            labels.add(label)
    attempt_label = _http_label(attempt.provider_http_status) or _ERROR_LABELS.get(attempt.error_code)
    if attempt_label is not None and not (
        attempt_label == "Local timeout" and labels.intersection({"HTTP 408", "HTTP 504"})
    ):
        labels.add(attempt_label)
    if not labels and attempt.outcome == AttemptOutcome.failed:
        labels.add("Provider failure")
    return labels


def _attempt_succeeded(attempt) -> bool:
    return attempt.outcome == AttemptOutcome.succeeded or (
        attempt.outcome == AttemptOutcome.unknown
        and attempt.error_code is None
        and any(event.get("code") == "success" for event in _transport_events(attempt))
    )


def _job_outcome(document, attempts) -> tuple[str, list[str]]:
    failure_labels = set()
    retry = backup = False
    failed_kinds = set()
    backup_ids = {attempt.id for attempt in attempts if attempt.backup_of_attempt_id is not None}
    for attempt in attempts:
        checking = attempt.attempt_kind == AttemptKind.llm_hallucination_check
        is_backup = attempt.id in backup_ids or attempt.repair_of_attempt_id in backup_ids
        role = "Backup" if is_backup else "Primary"
        if checking:
            role = "Checker " + role.lower()

        labels = _attempt_failures(attempt)
        failure_labels.update(f"{role} · {label}" for label in labels)
        if _attempt_succeeded(attempt):
            backup |= is_backup
            retry |= bool(labels) or attempt.attempt_kind in failed_kinds
        if labels:
            failed_kinds.add(attempt.attempt_kind)

    if document.status == GeneratedDocumentStatus.failed:
        final_label = _http_label(document.provider_http_status) or _ERROR_LABELS.get(document.error_code)
        if final_label and not any(label.endswith(f" · {final_label}") for label in failure_labels):
            failure_labels.add(f"Job · {final_label}")
        return "failed", sorted(failure_labels or {"Job · Cause unavailable"})
    if backup:
        return "backup", sorted(failure_labels)
    if retry:
        return "retry", sorted(failure_labels)
    return ("warning" if failure_labels else "ready"), sorted(failure_labels)


def _transport_finished_at(attempt, completed_at: datetime) -> datetime | None:
    if attempt.submitted_at is None:
        return None
    timestamps = []
    for event in _transport_events(attempt):
        value = event.get("occurred_at")
        if not isinstance(value, str):
            continue
        try:
            observed = datetime.fromisoformat(value)
        except ValueError:
            continue
        if observed.tzinfo is not None and attempt.submitted_at <= observed <= completed_at:
            timestamps.append(observed)
    if timestamps:
        return max(timestamps)
    if attempt.settled_at is not None and attempt.submitted_at <= attempt.settled_at <= completed_at:
        return attempt.settled_at
    return None


def _provider_span(attempts, completed_at: datetime) -> tuple[datetime | None, datetime | None]:
    submitted = [attempt for attempt in attempts if attempt.submitted_at is not None]
    if not submitted:
        return None, None
    final = max(submitted, key=lambda attempt: attempt.submitted_at)
    return (
        min(attempt.submitted_at for attempt in submitted),
        _transport_finished_at(final, completed_at),
    )


def _providers(attempts) -> list[dict[str, str | None]]:
    """Project distinct submitted provider/model pairs in execution order."""
    providers = []
    seen = set()
    for attempt in attempts:
        if attempt.submitted_at is None:
            continue
        stage = "checking" if attempt.attempt_kind == AttemptKind.llm_hallucination_check else "generation"
        provider = attempt.provider_adapter
        model = attempt.provider_model
        if provider is None and model is None:
            continue
        key = (stage, provider, model)
        if key in seen:
            continue
        seen.add(key)
        providers.append({"stage": stage, "provider": provider, "model": model})
    return providers


def _stages(document, attempts, total_ms: int) -> list[dict]:
    completed = document.completed_at
    generation = [attempt for attempt in attempts if attempt.attempt_kind == AttemptKind.llm_generation]
    checking = [attempt for attempt in attempts if attempt.attempt_kind == AttemptKind.llm_hallucination_check]
    generate_start, generate_end = _provider_span(generation, completed)
    check_start, check_end = _provider_span(checking, completed)
    validated = document.validated_draft_at

    # The response is often settled after the draft commits. The transport
    # timestamp marks the end of the call, and the validated draft checkpoint
    # can bound generation when a call observation is absent.
    if generate_end is None and generate_start is not None and validated is not None:
        if generate_start <= validated <= completed:
            generate_end = validated
    if check_start is not None and document.hallucination_check_completed_at is not None:
        check_end = document.hallucination_check_completed_at
    preparation_end = generate_start or document.started_at
    finalisation_start = check_end if checking else generate_end
    if not checking and document.hallucination_check_completed_at is not None:
        finalisation_start = document.hallucination_check_completed_at

    intervals = (
        ("queue", document.created_at, document.worker_received_at),
        ("preparation", document.worker_received_at, preparation_end),
        ("generation", generate_start, generate_end),
        ("checking", check_start, check_end),
        ("finalising", finalisation_start, completed),
    )
    stages = []
    last_end = document.created_at
    known_ms = 0
    for stage, start, end in intervals:
        if start is None or end is None or not last_end <= start <= end <= completed:
            continue
        duration_ms = _elapsed_ms(start, end)
        if duration_ms:
            stages.append({"key": stage, "duration_ms": duration_ms})
            known_ms += duration_ms
        last_end = end
    if known_ms < total_ms or not stages:
        stages.append({"key": "unknown", "duration_ms": total_ms - known_ms})
    return stages


def _read_attempts(db: Session, job_ids: list[UUID]):
    by_job = defaultdict(list)
    if not job_ids:
        return by_job
    ranked = select(
        ProviderAttempt.generated_document_id, ProviderAttempt.id, ProviderAttempt.attempt_kind,
        ProviderAttempt.outcome, ProviderAttempt.error_code, ProviderAttempt.provider_http_status,
        ProviderAttempt.provider_adapter, ProviderAttempt.provider_model,
        ProviderAttempt.transport_events_json, ProviderAttempt.backup_of_attempt_id,
        ProviderAttempt.repair_of_attempt_id, ProviderAttempt.submitted_at, ProviderAttempt.settled_at,
        func.row_number().over(
            partition_by=ProviderAttempt.generated_document_id,
            order_by=(ProviderAttempt.authorized_at, ProviderAttempt.id),
        ).label("position"),
    ).where(
        ProviderAttempt.generated_document_id.in_(job_ids),
        ProviderAttempt.attempt_kind.in_((AttemptKind.llm_generation, AttemptKind.llm_hallucination_check)),
    ).subquery()
    rows = db.execute(select(ranked).where(ranked.c.position <= _ATTEMPT_LIMIT).order_by(
        ranked.c.generated_document_id, ranked.c.position,
    ))
    for attempt in rows:
        by_job[attempt.generated_document_id].append(attempt)
    return by_job


def generation_response_timing(
    db: Session, *, since: datetime, until: datetime, team_id: UUID | None, bucket: str,
) -> dict[str, object]:
    """Aggregate the selected window and project only metadata for the latest jobs."""
    grouped = {}
    duration = func.extract("epoch", GeneratedDocument.completed_at - GeneratedDocument.created_at) * 1000
    ready = GeneratedDocument.status == GeneratedDocumentStatus.ready
    period = func.date_trunc(bucket, GeneratedDocument.completed_at, "UTC")
    for kind, generator in (
        ("notes", GeneratedDocumentGeneratorType.template),
        ("followups", GeneratedDocumentGeneratorType.followup),
    ):
        predicates = [
            GeneratedDocument.generator_type == generator,
            GeneratedDocument.status.in_((GeneratedDocumentStatus.ready, GeneratedDocumentStatus.failed)),
            GeneratedDocument.completed_at >= since,
            GeneratedDocument.completed_at < until,
            GeneratedDocument.completed_at >= GeneratedDocument.created_at,
            Transcript.retention_expires_at > until,
            GeneratedDocument.retention_expires_at > until,
        ]
        if team_id is not None:
            predicates.append(GeneratedDocument.team_id == team_id)
        source = select(GeneratedDocument.id).join(Transcript, GeneratedDocument.transcript_id == Transcript.id).where(*predicates)
        total_count, successful_count, average, *histogram_counts = db.execute(source.with_only_columns(
            func.count(GeneratedDocument.id),
            func.count(GeneratedDocument.id).filter(ready),
            func.avg(duration).filter(ready),
            *(func.count(GeneratedDocument.id).filter(
                ready, duration >= lower, *([duration < upper] if upper is not None else []),
            ) for lower, upper, _label in _BINS),
        )).one()
        averages = db.execute(source.with_only_columns(
            period.label("period"), func.avg(duration).label("average_ms"),
            func.count(GeneratedDocument.id).label("count"),
        ).where(ready).group_by(period).order_by(period)).all()
        documents = db.execute(source.with_only_columns(
            GeneratedDocument.id, GeneratedDocument.created_at, GeneratedDocument.worker_received_at,
            GeneratedDocument.started_at, GeneratedDocument.validated_draft_at, GeneratedDocument.completed_at,
            GeneratedDocument.hallucination_check_completed_at, GeneratedDocument.status,
            GeneratedDocument.error_code, GeneratedDocument.provider_http_status,
        ).order_by(GeneratedDocument.completed_at.desc(), GeneratedDocument.id.desc()).limit(_JOB_LIMIT)).all()
        attempts_by_job = _read_attempts(db, [doc.id for doc in documents])
        jobs = []
        for document in reversed(documents):
            attempts = attempts_by_job[document.id]
            outcome, failures = _job_outcome(document, attempts)
            total = _elapsed_ms(document.created_at, document.completed_at)
            jobs.append({
                "at": document.completed_at.astimezone(UTC).isoformat(), "total_ms": total,
                "stages": _stages(document, attempts, total), "outcome": outcome, "failures": failures,
                "providers": _providers(attempts),
            })
        outcomes = Counter(job["outcome"] for job in jobs)
        failure_counts = Counter(label for job in jobs for label in job["failures"])
        grouped[kind] = {
            "jobs": jobs,
            "averages": [{
                "at": point.period.astimezone(UTC).isoformat(), "average_ms": round(point.average_ms), "count": point.count,
            } for point in averages],
            "histogram": [{"label": label, "count": count}
                          for (_, _, label), count in zip(_BINS, histogram_counts, strict=True)],
            "outcomes": dict(outcomes),
            "failure_counts": [{"label": label, "count": count} for label, count in sorted(failure_counts.items())],
            "total_count": total_count, "detail_count": len(jobs), "detail_capped": total_count > len(jobs),
            "successful_count": successful_count, "successful_average_ms": round(average) if average is not None else None,
            "failed_count": total_count - successful_count,
        }
    return grouped
