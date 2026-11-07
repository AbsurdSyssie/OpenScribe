from datetime import timedelta
from uuid import uuid4

import pytest

from app.models import RedactionEntity, RedactionRun, RedactionRunStatus, Transcript, TranscriptStatus, utcnow
from app.services.content_crypto import encrypt_text_for_owner
from app.services.structured_transcripts import append_transcript_turns, canonical_transcript_projection, edit_transcript_turn, store_turns_on_version
from app.services.templates import (
    _build_followup_generation_messages,
    _build_quick_action_generation_messages,
    _build_template_generation_messages,
    process_generated_document,
    queue_document_generation_from_template,
)
from app.services.transcripts import _create_transcript_version_from_text


def test_diarised_generation_guidance_distinguishes_source_roles_from_speaker_numbers():
    transcript = (
        "Clinician: What brought you in?\n"
        "Patient: I have had a cough.\nSpeaker 1: An unidentified additional voice."
    )
    builders = (
        lambda: _build_template_generation_messages(
            template_name="Synthetic", prompt_text="Use supported facts only.",
            transcript_text=transcript, diarised_transcript=True,
        ),
        lambda: _build_followup_generation_messages(
            transcript_text=transcript, follow_up_prompt_text="Write a synthetic follow-up.",
            diarised_transcript=True,
        ),
        lambda: _build_quick_action_generation_messages(
            transcript_text=transcript, quick_action_text="Write a synthetic summary.",
            diarised_transcript=True,
        ),
    )

    for build in builders:
        system_message, user_message = build()
        guidance = system_message.lower()
        assert "explicit clinician: or patient: channel label" in guidance
        assert "source-provided role attribution" in guidance
        assert "do not rename the speaker" in guidance
        assert "speaker n labels are automated speaker labels that may be inaccurate" in guidance
        assert "infer clinical roles from context for those labels" in guidance
        assert "preserve uncertainty" in guidance
        assert "do not assume there are only two speakers" in guidance
        assert transcript in user_message


@pytest.mark.parametrize("telehealth", [False, True])
def test_worker_uses_frozen_speakers_and_redacted_source_after_transcript_edit(
    db_session, make_user, make_llm_config, make_llm_selection, make_template, monkeypatch, telehealth,
):
    owner = make_user()
    config = make_llm_config(team=owner.team, has_secret=False)
    make_llm_selection(config=config, model_name_override=config.model_name)
    template = make_template(owner=owner, actor=owner, prompt_text="Use supported facts only.")
    transcript = Transcript(
        owner_user_id=owner.id, team_id=owner.team_id, status=TranscriptStatus.ready,
        retention_days_applied=30, retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    turns = append_transcript_turns(
        db_session, transcript=transcript, scope_id=uuid4(),
        turns=[
            {"speaker_id": "patient" if telehealth else "0", "speaker_role": "patient" if telehealth else None, "text": "Alice Smith reports cough."},
            {"speaker_id": "clinician" if telehealth else "1", "speaker_role": "clinician" if telehealth else None, "text": "When did it start?"},
        ],
    )
    version = _create_transcript_version_from_text(
        db_session, transcript=transcript, owner_user_id=owner.id,
        plaintext=canonical_transcript_projection(turns),
    )
    store_turns_on_version(db_session, version=version, owner_user_id=owner.id, turns=turns)
    run_id, entity_id = uuid4(), uuid4()
    run = RedactionRun(
        id=run_id, transcript_id=transcript.id, transcript_version_id=version.id,
        owner_user_id=owner.id, team_id=owner.team_id, status=RedactionRunStatus.succeeded,
        redacted_text_encrypted=encrypt_text_for_owner(
            db_session, owner_user_id=owner.id, table="redaction_runs", field="redacted_text_encrypted",
            record_id=run_id, plaintext="[PHI-1] reports cough.\nWhen did it start?",
        ),
        mapping_hash="synthetic", entity_count=1, api_provider="native_presidio",
    )
    db_session.add(run)
    db_session.add(RedactionEntity(
        id=entity_id, redaction_run_id=run_id, entity_order=1, entity_type="PERSON",
        placeholder="[PHI-1]", normalized_value_hash="synthetic", occurrence_count=1,
        original_value_encrypted=encrypt_text_for_owner(
            db_session, owner_user_id=owner.id, table="redaction_entities", field="original_value_encrypted",
            record_id=entity_id, plaintext="Alice Smith",
        ),
    ))
    db_session.commit()
    captured = {}
    monkeypatch.setattr("app.services.templates.try_publish_task_dispatch_safely", lambda *args: None)
    monkeypatch.setattr("app.services.transcripts._attempt_preview_redaction", lambda *args, **kwargs: None)

    def fake_provider(**kwargs):
        captured.update(kwargs["request_body"])
        return '{"title":"Synthetic draft","content":"Synthetic reviewed draft."}', {
            "input_tokens": 5, "output_tokens": 5, "total_tokens": 10, "duration_ms": 1,
        }

    monkeypatch.setattr("app.services.templates._generate_freeform_output_openai", fake_provider)
    document = queue_document_generation_from_template(
        db_session, owner, transcript_id=transcript.id, template_id=template.id,
    )
    edit_transcript_turn(
        db_session, owner, transcript_id=transcript.id, turn_id=turns[0]["id"],
        expected_version_id=version.id, text="Later synthetic correction.",
    )
    processed = process_generated_document(db_session, document_id=document.id)
    assert processed.status.value == "ready"
    source = captured["messages"][1]["content"]
    assert ("Patient: [PHI-1] reports cough." if telehealth else "Speaker 1: [PHI-1] reports cough.") in source
    assert ("Clinician: When did it start?" if telehealth else "Speaker 2: When did it start?") in source
    if telehealth:
        assert "Speaker 1:" not in source
        assert "Patient: Patient:" not in source
    assert "Alice Smith" not in source
    assert "Later synthetic correction" not in source
    assert "automated speaker labels" in captured["messages"][0]["content"].lower()
