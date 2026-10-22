"""Synthetic structured transcript projection and redaction mapping coverage."""
from datetime import timedelta
from uuid import uuid4

from app.models import (
    SttAdapterKind, SttProviderPreset, TeamRole, Transcript, TranscriptStatus,
    TranscriptVersion, UserAppPreference, utcnow,
)
from app.services.structured_transcripts import (
    append_transcript_turns,
    canonical_transcript_projection,
    edit_transcript_turn,
    render_redacted_diarised_transcript,
    store_turns_on_version,
    turns_for_version,
)
from app.services.transcripts import _create_transcript_version_from_text
from app.services.consultation_split_analysis import build_split_analysis_prompt
from app.services.templates import _build_template_generation_messages
from app.services.templates import queue_document_generation_from_template
from app.services.transcripts import create_transcript_capture_settings


def _version_with_turns(db, owner, turns_spec):
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=owner.team_id,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db.add(transcript)
    db.flush()
    turns = []
    for scope_id, speaker_id, text in turns_spec:
        turns = append_transcript_turns(
            db, transcript=transcript, scope_id=scope_id,
            turns=[{"speaker_id": speaker_id, "text": text}],
        )
    projection = canonical_transcript_projection(turns)
    version = _create_transcript_version_from_text(
        db, transcript=transcript, owner_user_id=owner.id, plaintext=projection,
    )
    store_turns_on_version(db, version=version, owner_user_id=owner.id, turns=turns)
    db.commit()
    return version


def test_projection_keeps_labels_and_session_headings_out_of_transcript_text(db_session, make_user):
    owner = make_user()
    scope_id = uuid4()
    version = _version_with_turns(db_session, owner, [
        (scope_id, "0", "Synthetic first line."),
        (scope_id, "1", "Synthetic second line."),
    ])
    from app.services.content_crypto import decrypt_text_for_owner
    text = decrypt_text_for_owner(
        db_session, owner_user_id=owner.id, table="transcript_versions",
        field="text_encrypted", record_id=version.id, stored_value=version.text_encrypted,
    )
    assert text == "Synthetic first line.\nSynthetic second line."
    assert "Speaker" not in text
    assert "Recording" not in text


def test_redacted_turns_map_unicode_repeated_values_and_scope_labels(db_session, make_user):
    owner = make_user()
    first_scope, second_scope = uuid4(), uuid4()
    version = _version_with_turns(db_session, owner, [
        (first_scope, "0", "Patient saw Alice 😀"),
        (first_scope, "1", "Alice said pain"),
        (second_scope, "0", "Mr Bob arrived"),
    ])
    rendered = render_redacted_diarised_transcript(
        db_session,
        version=version,
        redacted_text="Patient saw [PHI-1] 😀\n[PHI-2] said pain\nMr [PHI-3] arrived",
        phi_index=[
            {"placeholder": "[PHI-1]", "value": "Alice"},
            {"placeholder": "[PHI-2]", "value": "Alice"},
            {"placeholder": "[PHI-3]", "value": "Bob"},
        ],
    )
    assert rendered == (
        "Recording 1\n"
        "Speaker 1: Patient saw [PHI-1] 😀\n"
        "Speaker 2: [PHI-2] said pain\n"
        "Recording 2 (speaker labels are independent of earlier recordings)\n"
        "Speaker 1: Mr [PHI-3] arrived"
    )
    assert "Alice" not in rendered and "Bob" not in rendered


def test_literal_phi_shaped_text_is_not_confused_with_a_replacement(db_session, make_user):
    owner = make_user()
    version = _version_with_turns(db_session, owner, [
        (uuid4(), "0", "The sample says [PHI-1] and Alice."),
    ])
    rendered = render_redacted_diarised_transcript(
        db_session,
        version=version,
        redacted_text="The sample says [PHI-1] and [PHI-1].",
        phi_index=[{"placeholder": "[PHI-1]", "value": "Alice"}],
    )
    assert rendered == "Recording 1\nSpeaker 1: The sample says [PHI-1] and [PHI-1]."


def test_redaction_span_crossing_turn_boundary_fails_closed(db_session, make_user):
    owner = make_user()
    scope = uuid4()
    version = _version_with_turns(db_session, owner, [
        (scope, "0", "Synthetic Alice"),
        (scope, "1", "Smith attended"),
    ])
    assert render_redacted_diarised_transcript(
        db_session,
        version=version,
        redacted_text="Synthetic [PHI-1] attended",
        phi_index=[{"placeholder": "[PHI-1]", "value": "Alice\nSmith"}],
    ) is None


def test_generation_guidance_is_added_only_for_diarised_sources():
    _, diarised_input = _build_template_generation_messages(
        template_name="Synthetic", prompt_text="Use supported facts.",
        transcript_text="Recording 1\nSpeaker 1: [PHI-1] has a cough.",
        diarised_transcript=True,
    )
    system, _ = _build_template_generation_messages(
        template_name="Synthetic", prompt_text="Use supported facts.",
        transcript_text="Synthetic flat transcript.", diarised_transcript=False,
    )
    assert "Speaker 1" in diarised_input
    assert "speaker labels that may be inaccurate" not in system.lower()
    diarised_system, _ = _build_template_generation_messages(
        template_name="Synthetic", prompt_text="Use supported facts.",
        transcript_text="Recording 1\nSpeaker 1: [PHI-1] has a cough.",
        diarised_transcript=True,
    )
    assert "speaker labels that may be inaccurate" in diarised_system.lower()
    assert "do not assume there are only two speakers" in diarised_system.lower()

    flat_prompt = build_split_analysis_prompt(transcript="Synthetic flat transcript.")
    diarised_prompt = build_split_analysis_prompt(
        transcript="Recording 1\nSpeaker 1: [PHI-1] has a cough.", diarised_transcript=True,
    )
    assert "speaker labels" not in flat_prompt.system_message.lower()
    assert "speaker labels may be inaccurate" in diarised_prompt.system_message.lower()


def test_structured_transcript_api_returns_revision_and_allows_text_only_cas(client, db_session, make_user):
    owner = make_user(email="structured-api-owner@example.com", mfa_required=False, mfa_enabled=False)
    version = _version_with_turns(db_session, owner, [(uuid4(), "0", "Synthetic line.")])
    transcript_version = db_session.get(TranscriptVersion, version.id)
    transcript = transcript_version.transcript
    login = client.post("/api/v1/auth/login", json={"email": owner.email, "password": "password-1"})
    assert login.status_code == 200

    detail = client.get(f"/api/v1/transcripts/{transcript.id}")
    assert detail.status_code == 200
    assert detail.json()["structured_revision_id"] == str(version.id)
    turn = detail.json()["structured_turns"][0]
    assert turn["speaker_label"] == "Speaker 1"

    url = f"/api/v1/transcripts/{transcript.id}/turns/{turn['id']}"
    rejected = client.patch(url, json={
        "expected_version_id": str(version.id), "text": "Synthetic edit.", "speaker_id": "0",
    })
    assert rejected.status_code == 422
    updated = client.patch(url, json={"expected_version_id": str(version.id), "text": "Synthetic edit."})
    assert updated.status_code == 200
    assert updated.json()["structured_revision_id"] != str(version.id)
    assert updated.json()["structured_turns"][0]["text"] == "Synthetic edit."


def test_queued_generation_remains_bound_to_immutable_turn_revision(
    db_session, make_team, make_user, make_llm_config, make_llm_selection, make_template,
):
    from app.models import TeamRole, TemplateScope
    team = make_team(name="Structured generation snapshot")
    admin = make_user(email="structured-gen-admin@example.com", is_system_admin=True)
    owner = make_user(email="structured-gen-owner@example.com", team=team, team_role=TeamRole.user)
    config = make_llm_config(team=team, actor=admin, has_secret=False)
    make_llm_selection(config=config, actor=admin, model_name_override=config.model_name)
    version = _version_with_turns(db_session, owner, [(uuid4(), "0", "Synthetic original utterance.")])
    transcript = db_session.get(TranscriptVersion, version.id).transcript
    template = make_template(
        scope=TemplateScope.user, owner=owner, actor=owner,
        name="Synthetic structured source", prompt_text="Use only supported facts.",
    )
    document = queue_document_generation_from_template(
        db_session, owner, transcript_id=transcript.id, template_id=template.id,
    )
    original_revision_id = document.transcript_version_id
    old_turns = turns_for_version(db_session, version=version)
    edit_transcript_turn(
        db_session, owner, transcript_id=transcript.id, turn_id=old_turns[0]["id"],
        expected_version_id=version.id, text="Synthetic edited utterance.",
    )
    db_session.refresh(document)
    assert document.transcript_version_id == original_revision_id == version.id
    assert turns_for_version(db_session, version=version) == old_turns


def test_empty_structured_revision_does_not_resurrect_older_transcript(db_session, make_user):
    from app.services.structured_transcripts import delete_transcript_turn
    from app.services.transcripts import snapshot_current_transcript_version
    from app.services.content_crypto import decrypt_text_for_owner
    owner = make_user()
    original = _version_with_turns(db_session, owner, [(uuid4(), "0", "Synthetic old content.")])
    transcript = db_session.get(TranscriptVersion, original.id).transcript
    old_turn = turns_for_version(db_session, version=original)[0]
    delete_transcript_turn(
        db_session, owner, transcript_id=transcript.id, turn_id=old_turn["id"],
        expected_version_id=original.id,
    )
    empty_revision = snapshot_current_transcript_version(
        db_session, transcript=transcript, allow_empty=True,
    )
    assert empty_revision.id != original.id
    assert turns_for_version(db_session, version=empty_revision) == []
    assert decrypt_text_for_owner(
        db_session, owner_user_id=owner.id, table="transcript_versions",
        field="text_encrypted", record_id=empty_revision.id,
        stored_value=empty_revision.text_encrypted,
    ) == ""


def test_capture_settings_freeze_preference_and_admin_policy(
    db_session, make_team, make_user, make_stt_config, make_stt_selection,
):
    team = make_team(name="Frozen diarisation capture")
    admin = make_user(email="capture-policy-admin@example.com", is_system_admin=True)
    owner = make_user(email="capture-policy-owner@example.com", team=team, team_role=TeamRole.user)
    config = make_stt_config(team=team, actor=admin, has_secret=False)
    config.provider_preset = SttProviderPreset.deepgram.value
    config.adapter_kind = SttAdapterKind.generic_rest
    config.diarization_batch_enabled = True
    config.diarization_field_name = "diarize"
    config.diarization_on_value = "yes"
    config.diarization_off_value = "no"
    db_session.add(config)
    db_session.flush()
    make_stt_selection(config=config, actor=admin)
    db_session.add(UserAppPreference(user_id=owner.id, preferences_json={"diarization_enabled": True}))
    transcript = Transcript(
        owner_user_id=owner.id, team_id=team.id, status=TranscriptStatus.ready,
        retention_days_applied=30, retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.commit()

    snapshot = create_transcript_capture_settings(db_session, owner, transcript_id=transcript.id)
    assert snapshot.stt_diarisation_enabled is True
    assert snapshot.stt_diarization_batch_enabled is True
    assert snapshot.stt_diarization_field_name == "diarize"
    assert snapshot.stt_diarization_on_value == "yes"
    assert snapshot.stt_diarization_off_value == "no"

    preference = db_session.query(UserAppPreference).filter_by(user_id=owner.id).one()
    preference.preferences_json = {"diarization_enabled": False}
    config.diarization_batch_enabled = False
    config.diarization_field_name = "changed"
    db_session.commit()
    db_session.refresh(snapshot)
    assert snapshot.stt_diarisation_enabled is True
    assert snapshot.stt_diarization_batch_enabled is True
    assert snapshot.stt_diarization_field_name == "diarize"
