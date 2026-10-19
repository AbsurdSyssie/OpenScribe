from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models import (
    AttemptKind,
    AttemptOutcome,
    AttemptStatus,
    GeneratedDocumentStatus,
    LlmAdapterKind,
    ProviderAttempt,
    ProviderSettlementBasis,
    ProviderUsageEvent,
    ProviderUsageEventType,
    TeamRole,
    TemplateMode,
    TemplateScope,
    Transcript,
    TranscriptIngestionMode,
    TranscriptStatus,
    utcnow,
)
from app.services.templates import (
    generated_document_text,
    process_generated_document,
    queue_document_generation_from_template,
    queue_followup_generation,
    queue_quick_action_generation,
)
from app.services.transcripts import set_transcript_draft_text


class _FakeOllamaResponse:
    headers = {}

    def __init__(self, frames: list[bytes]):
        self.frames = frames
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def raise_for_status(self):
        pass

    def iter_raw(self, *, chunk_size):
        yield from self.frames


@pytest.mark.parametrize(
    ("generation_kind", "expected_error"),
    [
        ("followup", "llm_provider_bad_response"),
        ("quick_action", "llm_provider_bad_response"),
        ("template_json", "llm_generation_truncated"),
    ],
)
def test_ollama_incomplete_response_fails_ordinary_generation_once(
    db_session,
    monkeypatch,
    caplog,
    make_team,
    make_user,
    make_llm_config,
    make_llm_selection,
    make_template,
    make_quick_action,
    generation_kind,
    expected_error,
):
    team = make_team(name=f"Ollama incomplete {generation_kind}")
    admin = make_user(email=f"ollama-admin-{generation_kind}@example.test", is_system_admin=True)
    owner = make_user(
        email=f"ollama-owner-{generation_kind}@example.test",
        team=team,
        team_role=TeamRole.user,
    )
    config = make_llm_config(
        team=team,
        actor=admin,
        adapter_kind=LlmAdapterKind.ollama_chat,
        base_url="http://localhost:11434",
        model_name="synthetic-model",
        available_models_json=["synthetic-model"],
        has_secret=False,
    )
    make_llm_selection(config=config, actor=admin, model_name_override="synthetic-model")
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Synthetic visit",
        current_draft_text_encrypted="",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    set_transcript_draft_text(db_session, transcript=transcript, plaintext="Synthetic consultation source.")
    db_session.commit()

    if generation_kind == "followup":
        document = queue_followup_generation(
            db_session, owner, transcript_id=transcript.id, prompt_text="Synthetic follow-up prompt",
        )
        partial_text = "partial synthetic follow-up"
        frames = [json.dumps({"message": {"content": partial_text}, "done": False}).encode()]
    elif generation_kind == "quick_action":
        quick_action = make_quick_action(
            scope=TemplateScope.user,
            owner=owner,
            actor=owner,
            name="Synthetic quick action",
            prompt_text="Write a synthetic action.",
        )
        document = queue_quick_action_generation(
            db_session, owner, transcript_id=transcript.id, quick_action_id=quick_action.id,
        )
        partial_text = "partial synthetic quick action"
        frames = [json.dumps({"message": {"content": partial_text}, "done": False}).encode()]
    else:
        template = make_template(
            scope=TemplateScope.user,
            owner=owner,
            actor=owner,
            name="Synthetic note template",
            prompt_text="Write a synthetic note.",
            mode=TemplateMode.freeform,
        )
        document = queue_document_generation_from_template(
            db_session, owner, transcript_id=transcript.id, template_id=template.id,
        )
        # This output is syntactically valid JSON despite an explicit provider
        # length finish reason, proving the adapter check precedes note parsing.
        partial_text = json.dumps({"title": "Synthetic note", "content": "Partial but parseable body."})
        frames = [
            json.dumps({"message": {"content": partial_text}, "done": False}).encode() + b"\n",
            b'{"done":true,"done_reason":"length"}',
        ]

    response = _FakeOllamaResponse(frames)
    transport_calls = []

    def fake_stream(*_args, **_kwargs):
        transport_calls.append(True)
        return response

    monkeypatch.setattr("app.services.templates.httpx.stream", fake_stream)
    caplog.set_level("INFO", logger="openscribe.usage")

    processed = process_generated_document(db_session, document_id=document.id)
    duplicate = process_generated_document(db_session, document_id=document.id)

    assert processed.status is GeneratedDocumentStatus.failed
    assert duplicate.status is GeneratedDocumentStatus.failed
    assert processed.error_code == expected_error
    assert len(transport_calls) == 1
    assert response.closed
    assert generated_document_text(
        db_session, document=processed, field="original_output_text_encrypted",
    ) == ""
    assert generated_document_text(
        db_session, document=processed, field="edited_output_text_encrypted",
    ) == ""
    assert generated_document_text(
        db_session, document=processed, field="failed_provider_output_redacted_encrypted",
    ) == ""

    attempt = db_session.scalar(select(ProviderAttempt).where(
        ProviderAttempt.generated_document_id == document.id,
        ProviderAttempt.attempt_kind == AttemptKind.llm_generation,
    ))
    assert attempt is not None
    assert attempt.status is AttemptStatus.settled
    assert attempt.outcome is AttemptOutcome.unknown
    assert attempt.settlement_basis is ProviderSettlementBasis.conservative_unknown
    assert attempt.settled_units == attempt.reserved_units
    assert attempt.reported_input_tokens is None
    assert attempt.reported_output_tokens is None

    events = list(db_session.scalars(select(ProviderUsageEvent).where(
        ProviderUsageEvent.generated_document_id == document.id,
    ).order_by(ProviderUsageEvent.created_at)))
    assert [event.event_type for event in events] == [
        ProviderUsageEventType.queued,
        ProviderUsageEventType.started,
        ProviderUsageEventType.failed,
    ]
    assert events[-1].error_code == expected_error
    assert events[-1].provider_error_code == (
        "incomplete_response" if generation_kind != "template_json" else None
    )
    assert all(event.provider_error_code != partial_text for event in events)
    assert partial_text not in caplog.text
    assert "Synthetic consultation source." not in caplog.text
    assert all(partial_text not in json.dumps(event.transport_events_json) for event in [attempt])
