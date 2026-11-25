from datetime import timedelta
from uuid import uuid4

from app.models import (
    AttemptKind,
    GeneratedDocument,
    GeneratedDocumentGeneratorType,
    GeneratedDocumentStatus,
    QuotaResource,
    Transcript,
    TranscriptVersion,
    utcnow,
)
from app.services import admin_generation_timing
from app.services.quotas import (
    mark_provider_attempt_submitted,
    reserve_provider_attempt,
    settle_provider_attempt_tokens,
    settle_provider_attempt_unknown_tokens,
)


def test_response_timing_keeps_recovery_outcomes_and_full_range_statistics(
    db_session, make_team, make_user, monkeypatch,
):
    monkeypatch.setattr(admin_generation_timing, "_JOB_LIMIT", 2)
    now = utcnow().replace(microsecond=0)
    team = make_team(name="Synthetic timing team")
    owner = make_user(email="timing-owner@example.com", team=team)
    transcript = Transcript(
        owner_user_id=owner.id, team_id=team.id, title="Synthetic source",
        retention_days_applied=30, retention_expires_at=now + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    version = TranscriptVersion(transcript_id=transcript.id, version_no=1, text_encrypted="Source sentinel")
    db_session.add(version)
    db_session.flush()

    def job(seconds, *, minutes_ago, kind=GeneratedDocumentGeneratorType.template,
            status=GeneratedDocumentStatus.ready):
        completed_at = now - timedelta(minutes=minutes_ago)
        document = GeneratedDocument(
            owner_user_id=owner.id, team_id=team.id, transcript_id=transcript.id,
            transcript_version_id=version.id, retention_expires_at=transcript.retention_expires_at,
            generator_type=kind, status=status, source_template_name="Synthetic template",
            title="Draft sentinel", original_output_text_encrypted="Draft body sentinel",
            edited_output_text_encrypted="Draft body sentinel",
            created_at=completed_at - timedelta(seconds=seconds), completed_at=completed_at,
        )
        db_session.add(document)
        db_session.flush()
        return document

    def attempt(document, *, submitted, observations, backup=None):
        submitted_at = document.created_at + timedelta(seconds=submitted)
        row = reserve_provider_attempt(
            db_session, team_id=team.id, owner_user_id=owner.id, transcript_id=transcript.id,
            generated_document_id=document.id, resource=QuotaResource.tokens,
            attempt_kind=AttemptKind.llm_generation, correlation_id=backup.id if backup else uuid4(),
            attempt_number=1, reserved_units=10, authorized_at=submitted_at,
            reservation_valid_until=submitted_at + timedelta(minutes=5),
            backup_of_attempt_id=backup.id if backup else None,
        )
        mark_provider_attempt_submitted(db_session, attempt_id=row.id, now=submitted_at,
                                        deadline_at=submitted_at + timedelta(minutes=2))
        row.transport_events_json = [
            {
                "occurred_at": (document.created_at + timedelta(seconds=seconds)).isoformat(),
                "request_ordinal": index,
                "duration_ms": 1_000,
                "code": code,
                "http_status": status,
                "retry_wait_ms": 0,
            }
            for index, (seconds, code, status) in enumerate(observations, 1)
        ]
        return row

    job(30, minutes_ago=10)
    failed = job(60, minutes_ago=5, status=GeneratedDocumentStatus.failed)
    failed.error_code = "private-error-sentinel"
    recovered = job(90, minutes_ago=3)
    recovered.worker_received_at = recovered.created_at + timedelta(seconds=10)
    primary = attempt(recovered, submitted=15, observations=[(65, "timeout", 504)])
    settle_provider_attempt_unknown_tokens(db_session, attempt_id=primary.id, now=recovered.created_at + timedelta(seconds=65))
    primary.error_code = "llm_provider_timeout"
    backup = attempt(recovered, submitted=66, observations=[(85, "success", None)], backup=primary)
    settle_provider_attempt_tokens(db_session, attempt_id=backup.id, reported_total_tokens=5, now=recovered.completed_at)

    followup = job(40, minutes_ago=2, kind=GeneratedDocumentGeneratorType.followup)
    retried = attempt(followup, submitted=2, observations=[(8, "rate_limited", 429), (39, "success", None)])
    settle_provider_attempt_tokens(db_session, attempt_id=retried.id, reported_total_tokens=5, now=followup.completed_at)
    job(5, minutes_ago=1, kind=GeneratedDocumentGeneratorType.quick_action)
    db_session.flush()

    result = admin_generation_timing.generation_response_timing(
        db_session, since=now - timedelta(days=1), until=now, team_id=team.id, bucket="day",
    )
    notes = result["notes"]
    assert notes["total_count"] == 3
    assert notes["detail_capped"]
    assert notes["successful_count"] == 2
    assert notes["failed_count"] == 1
    assert notes["successful_average_ms"] == 60_000
    assert notes["averages"][0]["count"] == 2
    assert sum(bucket["count"] for bucket in notes["histogram"]) == 2
    terminal, rescued = notes["jobs"]
    assert terminal["total_ms"] == 60_000
    assert terminal["stages"] == [{"key": "unknown", "duration_ms": 60_000}]
    assert terminal["outcome"] == "failed"
    assert rescued == {
        "at": recovered.completed_at.isoformat(), "total_ms": 90_000, "outcome": "backup",
        "stages": [
            {"key": "queue", "duration_ms": 10_000},
            {"key": "preparation", "duration_ms": 5_000},
            {"key": "generation", "duration_ms": 70_000},
            {"key": "finalising", "duration_ms": 5_000},
        ],
        "failures": ["Primary · HTTP 504"],
    }
    assert {"label": "Primary · HTTP 504", "count": 1} in notes["failure_counts"]
    assert result["followups"]["total_count"] == 1
    assert result["followups"]["jobs"][0]["outcome"] == "retry"
    assert result["followups"]["jobs"][0]["failures"] == ["Primary · HTTP 429"]
    assert "private-error-sentinel" not in str(result)
    assert "Draft body sentinel" not in str(result)
    assert str(owner.id) not in str(result)
