from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.errors import AppError
from app.models import (
    GenerationTraceEvent, ProviderAttempt, TeamLlmBackupSelection, TeamRole,
    TemplateScope, Transcript, TranscriptIngestionMode, TranscriptStatus, utcnow,
)
from app.services import generation_trace
from app.services.llm_adapters import runtime
from app.services import llm_execution
from app.services import templates
from app.services.templates import process_generated_document, queue_document_generation_from_template
from app.services.transcripts import set_transcript_draft_text


def test_enabled_trace_captures_retry_invalid_backup_and_repair_chain(
    db_session, make_team, make_user, make_llm_config, make_llm_selection,
    make_template, monkeypatch,
):
    team = make_team(name=f"Trace outcome {uuid4()}")
    admin = make_user(email=f"trace-admin-{uuid4()}@example.com", password="password-1", is_system_admin=True)
    owner = make_user(email=f"trace-owner-{uuid4()}@example.com", password="password-2", team=team, team_role=TeamRole.user)
    owner.generation_trace_enabled = True
    primary = make_llm_config(team=team, actor=admin, model_name="primary-model", available_models_json=["primary-model"])
    backup = make_llm_config(team=team, actor=admin, label="Backup", model_name="backup-model", available_models_json=["backup-model"])
    make_llm_selection(config=primary, actor=admin, model_name_override="primary-model")
    db_session.add(TeamLlmBackupSelection(team_id=team.id, llm_config_id=backup.id, model_name="backup-model"))
    transcript = Transcript(owner_user_id=owner.id, team_id=team.id, title="Synthetic",
        ingestion_mode=TranscriptIngestionMode.whole_file, status=TranscriptStatus.ready,
        retention_days_applied=30, retention_expires_at=utcnow() + timedelta(days=30))
    db_session.add(transcript)
    db_session.flush()
    set_transcript_draft_text(db_session, transcript=transcript, plaintext="Synthetic private source marker.")
    monkeypatch.setattr(templates, "_redacted_generation_source_texts",
        lambda *_args, **_kwargs: ("[PERSON_1] attended a synthetic visit.", "", [], []))
    template = make_template(scope=TemplateScope.user, owner=owner, actor=owner,
        name="Synthetic", prompt_text="Write a note.")
    db_session.commit()
    document = queue_document_generation_from_template(db_session, owner, transcript_id=transcript.id, template_id=template.id)

    malformed_raw = ' \n{"title":"Synthetic","content":\n '
    valid_raw = ' \n{"title":"Synthetic","content":"Synthetic note."}\n '
    api_key = "synthetic-api-key-never-persist"
    fake_calls: list[dict[str, object]] = []
    sleeps: list[float] = []

    monkeypatch.setattr(templates, "_resolve_generation_credential", lambda _config: api_key)
    monkeypatch.setattr(llm_execution.random, "uniform", lambda low, high: 1.0)
    monkeypatch.setattr(llm_execution.time, "sleep", lambda seconds: sleeps.append(seconds))

    def fake_transport(**kwargs):
        fake_calls.append(kwargs)
        model = kwargs["request_body"]["model"]
        if model == "primary-model":
            raise AppError(429, "llm_provider_rate_limited", "Synthetic temporary capacity", {
                "provider_http_status": 429, "temporary_capacity": True,
            })
        if len(fake_calls) == 5:
            raw = malformed_raw
        else:
            raw = valid_raw
        generation_trace.raw_model_text(raw)
        return raw.strip(), {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}

    monkeypatch.setattr(runtime, "_invoke_openai_compatible_once", fake_transport)
    result = process_generated_document(db_session, document_id=document.id)
    assert result.status.value == "ready"
    assert [call["request_body"]["model"] for call in fake_calls] == [
        "primary-model", "primary-model", "primary-model", "primary-model", "backup-model", "backup-model",
    ]
    assert sleeps == [1.0, 1.0, 1.0]

    events = list(db_session.scalars(select(GenerationTraceEvent)
        .where(GenerationTraceEvent.transcript_id == transcript.id)
        .order_by(GenerationTraceEvent.captured_at, GenerationTraceEvent.request_ordinal, GenerationTraceEvent.id)))
    assert len(events) == 6
    assert [event.request_ordinal for event in events] == [1, 2, 3, 4, 1, 1]
    assert [event.http_status for event in events[:4]] == [429, 429, 429, 429]
    assert [event.retry_wait_ms for event in events[:4]] == [1000, 1000, 1000, 0]

    attempts = {attempt.id: attempt for attempt in db_session.scalars(select(ProviderAttempt)
        .where(ProviderAttempt.generated_document_id == document.id))}
    primary_attempt = next(attempt for attempt in attempts.values() if attempt.provider_model == "primary-model")
    backup_attempt = next(attempt for attempt in attempts.values() if attempt.backup_of_attempt_id is not None)
    repair_attempt = next(attempt for attempt in attempts.values() if attempt.repair_of_attempt_id is not None)
    assert all(event.attempt_id == primary_attempt.id for event in events[:4])
    assert events[4].attempt_id == backup_attempt.id and events[5].attempt_id == repair_attempt.id
    assert (events[4].validation, events[4].decision, events[4].trace_error_code) == (
        "invalid", "repair", "llm_generation_invalid_json",
    )
    assert (events[5].validation, events[5].decision) == ("accepted", None)

    fifth = generation_trace.get_trace(db_session, owner, transcript.id, events[4].id)
    sixth = generation_trace.get_trace(db_session, owner, transcript.id, events[5].id)
    assert fifth["response"] == malformed_raw
    assert sixth["response"] == valid_raw
    assert api_key not in str([fifth["request"], sixth["request"]])
    assert "Synthetic private source marker" not in str([fifth["request"], sixth["request"]])
    assert "[PERSON_1]" in str([fifth["request"], sixth["request"]])
    assert api_key not in str([attempt.transport_events_json for attempt in attempts.values()])
