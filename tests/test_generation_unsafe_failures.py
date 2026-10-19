"""Worker regressions for unsafe incomplete LLM responses."""
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import (
    AttemptKind,
    AttemptOutcome,
    AttemptStatus,
    GeneratedDocumentStatus,
    GeneratedDocumentSection,
    HallucinationCheckStatus,
    ProviderAttempt,
    ProviderSettlementBasis,
    TeamHallucinationCheckSelection,
    TeamRole,
    TemplateMode,
    TemplateScope,
    Transcript,
    TranscriptIngestionMode,
    TranscriptStatus,
    utcnow,
)
from app.services import templates
from app.services.templates import (
    generated_document_section_text,
    generated_document_text,
    process_generated_document,
    queue_document_generation_from_template,
    queue_followup_generation,
    queue_quick_action_generation,
)
from app.services.transcripts import set_transcript_draft_text


def _source_graph(db_session, make_team, make_user, make_llm_config, make_llm_selection):
    team = make_team(name=f"Unsafe generation {uuid4()}")
    admin = make_user(email=f"unsafe-admin-{uuid4()}@example.com", password="password-1", is_system_admin=True)
    owner = make_user(
        email=f"unsafe-owner-{uuid4()}@example.com",
        password="password-2",
        team=team,
        team_role=TeamRole.user,
    )
    config = make_llm_config(team=team, actor=admin, has_secret=False)
    make_llm_selection(config=config, actor=admin, model_name_override=config.model_name)
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=team.id,
        title="Synthetic visit",
        current_draft_text_encrypted=None,
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    set_transcript_draft_text(db_session, transcript=transcript, plaintext="Synthetic consultation source.")
    db_session.commit()
    return team, admin, owner, config, transcript


def _fake_openai_factory(responses, calls):
    class FakeClient:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **_kwargs):
            calls.append(True)
            content, finish_reason = next(responses)
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    finish_reason=finish_reason,
                    message=SimpleNamespace(content=content),
                )],
                usage=SimpleNamespace(prompt_tokens=5, completion_tokens=7, total_tokens=12),
            )

        def close(self):
            return None

    return FakeClient


def test_explicit_truncation_fails_followup_quick_action_and_parseable_template_without_replay(
    db_session, make_team, make_user, make_llm_config, make_llm_selection, make_template,
    make_quick_action, monkeypatch, caplog,
):
    caplog.set_level("INFO", logger="openscribe.usage")
    _team, _admin, owner, _config, transcript = _source_graph(
        db_session, make_team, make_user, make_llm_config, make_llm_selection,
    )
    template = make_template(
        scope=TemplateScope.user,
        owner=owner,
        actor=owner,
        name="Synthetic template",
        prompt_text="Write a synthetic note.",
    )
    quick_action = make_quick_action(
        scope=TemplateScope.user,
        owner=owner,
        actor=owner,
        name="Synthetic quick action",
        prompt_text="Write a short synthetic message.",
    )
    documents = [
        queue_followup_generation(
            db_session, owner, transcript_id=transcript.id, prompt_text="Write a synthetic follow-up."
        ),
        queue_quick_action_generation(
            db_session, owner, transcript_id=transcript.id, quick_action_id=quick_action.id,
        ),
        queue_document_generation_from_template(
            db_session, owner, transcript_id=transcript.id, template_id=template.id,
        ),
    ]

    # The body is deliberately parseable JSON. The provider's explicit length
    # finish reason must veto all output modes before any parser can accept it.
    response_content = '{"title":"Synthetic visit","content":"Synthetic patient response marker."}'
    calls = []
    responses = iter((response_content, "length") for _ in documents)
    monkeypatch.setattr(templates, "OpenAI", _fake_openai_factory(responses, calls))
    monkeypatch.setattr(templates, "_resolve_generation_credential", lambda _config: None)

    for document in documents:
        result = process_generated_document(db_session, document_id=document.id)
        assert result.status is GeneratedDocumentStatus.failed
        db_session.refresh(document)
        assert document.error_code == "llm_generation_truncated"
        assert "Synthetic patient response marker" not in (document.error_message or "")
        assert document.provider_error_code is None
        assert document.failed_provider_output_redacted_encrypted is None
        assert generated_document_text(
            db_session, document=document, field="original_output_text_encrypted",
        ) == ""
        assert generated_document_text(
            db_session, document=document, field="edited_output_text_encrypted",
        ) == ""

        attempt = db_session.scalar(select(ProviderAttempt).where(
            ProviderAttempt.generated_document_id == document.id,
            ProviderAttempt.attempt_kind == AttemptKind.llm_generation,
        ))
        assert attempt is not None
        assert (attempt.status, attempt.outcome, attempt.settlement_basis) == (
            AttemptStatus.settled,
            AttemptOutcome.unknown,
            ProviderSettlementBasis.conservative_unknown,
        )
        assert attempt.settled_units == attempt.reserved_units

        # Duplicate task delivery returns the terminal row and never makes a
        # second provider call.
        assert process_generated_document(db_session, document_id=document.id).status is GeneratedDocumentStatus.failed

    assert len(calls) == len(documents)
    assert "Synthetic patient response marker" not in caplog.text


@pytest.mark.parametrize(
    ("invalid_checker_json", "expected_failure_code"),
    [
        (
            '{"status":"unchanged","status":"corrected","edits":['
            '{"section_key":"problem","original":"Supported original note.",'
            '"replacement":"Unsafe duplicate patch."}]}',
            "duplicate_json_key",
        ),
        ('{"status":"unchanged","extra":' + '{"key":' * 10_000 + '0' + '}' * 10_000 + '}', "invalid_json"),
    ],
    ids=["duplicate-key", "deeply-nested"],
)
def test_invalid_checker_response_keeps_original_structured_note_ready_unchecked(
    db_session, make_team, make_user, make_llm_config, make_llm_selection, make_template,
    monkeypatch, caplog, invalid_checker_json, expected_failure_code,
):
    caplog.set_level("INFO", logger="openscribe.usage")
    team, admin, owner, _config, transcript = _source_graph(
        db_session, make_team, make_user, make_llm_config, make_llm_selection,
    )
    checker_config = make_llm_config(team=team, actor=admin, label="Synthetic checker", has_secret=False)
    db_session.add(TeamHallucinationCheckSelection(
        team_id=team.id,
        llm_config_id=checker_config.id,
        model_name_override=checker_config.model_name,
        selected_by_user_id=admin.id,
    ))
    db_session.commit()
    template = make_template(
        scope=TemplateScope.user,
        owner=owner,
        actor=owner,
        name="Synthetic structured template",
        prompt_text="Use only synthetic source evidence.",
        mode=TemplateMode.structured,
        config_json={
            "profile": "emis",
            "sections": [{
                "section_key": "problem",
                "instruction": "Summarise the problem.",
                "section_order": 0,
            }],
        },
    )
    document = queue_document_generation_from_template(
        db_session, owner, transcript_id=transcript.id, template_id=template.id,
    )
    responses = iter((
        ('{"title":"Synthetic visit","content":{"problem":"Supported original note."}}', "stop"),
        (invalid_checker_json, "stop"),
        (invalid_checker_json, "stop"),
    ))
    calls = []
    monkeypatch.setattr(templates, "OpenAI", _fake_openai_factory(responses, calls))
    monkeypatch.setattr(templates, "_resolve_generation_credential", lambda _config: None)
    monkeypatch.setenv("HALLUCINATION_CHECK_DEBUG_UI", "0")

    result = process_generated_document(db_session, document_id=document.id)

    assert result.status is GeneratedDocumentStatus.ready
    assert result.hallucination_check_status is HallucinationCheckStatus.failed_invalid_response
    assert generated_document_text(db_session, document=result, field="edited_output_text_encrypted") == "Problem\nSupported original note."
    sections = db_session.scalars(select(GeneratedDocumentSection).where(
        GeneratedDocumentSection.generated_document_id == document.id,
    )).all()
    assert [
        (section.section_key, generated_document_section_text(
            db_session, section=section, field="edited_text_encrypted",
        ))
        for section in sections
    ] == [("problem", "Supported original note.")]
    assert "Unsafe duplicate patch" not in caplog.text

    checker_attempts = db_session.scalars(select(ProviderAttempt).where(
        ProviderAttempt.generated_document_id == document.id,
        ProviderAttempt.attempt_kind == AttemptKind.llm_hallucination_check,
    ).order_by(ProviderAttempt.attempt_number)).all()
    assert len(calls) == 3
    assert len(checker_attempts) == 2
    assert all(
        (attempt.status, attempt.outcome) == (AttemptStatus.settled, AttemptOutcome.failed)
        for attempt in checker_attempts
    )
    assert [attempt.error_code for attempt in checker_attempts] == [expected_failure_code, expected_failure_code]
    assert "Unsafe duplicate patch" not in caplog.text
    checker_logs = [
        record for record in caplog.records
        if record.name == "openscribe.usage" and getattr(record, "stage", None) == "checker"
    ]
    assert [record.event for record in checker_logs].count("llm_hallucination_check_started") == 2
    failed_logs = [record for record in checker_logs if record.event == "llm_hallucination_check_failed"]
    assert len(failed_logs) == 2
    assert all(record.status == "failed_invalid_response" for record in failed_logs)
    assert all(record.error_code == expected_failure_code for record in failed_logs)
    assert all(record.levelname == "WARNING" for record in failed_logs)
