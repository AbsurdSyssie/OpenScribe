"""Synthetic source tests for the structured transcript ownership/version boundary."""
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.errors import AppError
from app.models import Transcript, TranscriptStatus, TranscriptVersion, utcnow
from app.services.content_crypto import decrypt_text_for_owner
from app.services.structured_transcripts import (
    append_transcript_turns,
    canonical_transcript_projection,
    delete_transcript_turn,
    edit_transcript_turn,
    load_current_turns,
    store_turns_on_version,
    turns_for_version,
)
from app.services.transcripts import _create_transcript_version_from_text


def _source(db, owner):
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=owner.team_id,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db.add(transcript)
    db.flush()
    turns = append_transcript_turns(
        db,
        transcript=transcript,
        scope_id=uuid4(),
        turns=[{"speaker_id": "0", "start_ms": 0, "end_ms": 1000, "text": "Synthetic opening."}],
    )
    version = _create_transcript_version_from_text(
        db, transcript=transcript, owner_user_id=owner.id,
        plaintext=canonical_transcript_projection(turns),
    )
    store_turns_on_version(db, version=version, owner_user_id=owner.id, turns=turns)
    db.commit()
    return transcript, version, turns[0]


def test_expired_root_rejects_structured_edit_before_decryption(db_session, make_user, monkeypatch):
    owner = make_user()
    transcript, version, turn = _source(db_session, owner)
    transcript.retention_expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()

    def forbidden_decrypt(*args, **kwargs):
        raise AssertionError("Expired source must be rejected before content decryption")

    monkeypatch.setattr("app.services.structured_transcripts.decrypt_json_for_owner", forbidden_decrypt)
    with pytest.raises(AppError) as error:
        edit_transcript_turn(
            db_session, owner, transcript_id=transcript.id, turn_id=turn["id"],
            expected_version_id=version.id, text="Synthetic correction.",
        )
    assert error.value.status_code == 404


def test_structured_edit_rejects_other_team_member_before_decryption(db_session, make_user, monkeypatch):
    owner = make_user()
    other = make_user(email="other-speaker-review@example.com", team=owner.team)
    transcript, version, turn = _source(db_session, owner)

    def forbidden_decrypt(*args, **kwargs):
        raise AssertionError("Cross-owner source must be rejected before content decryption")

    monkeypatch.setattr("app.services.structured_transcripts.decrypt_json_for_owner", forbidden_decrypt)
    with pytest.raises(AppError) as error:
        edit_transcript_turn(
            db_session, other, transcript_id=transcript.id, turn_id=turn["id"],
            expected_version_id=version.id, text="Synthetic correction.",
        )
    assert error.value.status_code in {403, 404}


def test_turn_edit_preserves_frozen_version_and_rejects_stale_save(db_session, make_user):
    owner = make_user()
    transcript, version, turn = _source(db_session, owner)
    original_text_envelope = version.text_encrypted
    original_turn_envelope = version.structured_turns_encrypted
    edit_transcript_turn(
        db_session, owner, transcript_id=transcript.id, turn_id=turn["id"],
        expected_version_id=version.id, text="Synthetic correction.",
    )
    latest = db_session.scalar(select(TranscriptVersion).where(
        TranscriptVersion.transcript_id == transcript.id,
    ).order_by(TranscriptVersion.version_no.desc()))
    assert latest.id != version.id
    assert version.text_encrypted == original_text_envelope
    assert version.structured_turns_encrypted == original_turn_envelope
    assert turns_for_version(db_session, version=version)[0]["text"] == "Synthetic opening."
    current = load_current_turns(db_session, transcript=transcript)[0]
    assert current["speaker_id"] == "0"
    assert current["scope_id"] == turn["scope_id"]
    assert (current["start_ms"], current["end_ms"]) == (0, 1000)
    assert current["edited"] is True
    with pytest.raises(AppError) as error:
        edit_transcript_turn(
            db_session, owner, transcript_id=transcript.id, turn_id=turn["id"],
            expected_version_id=version.id, text="Synthetic stale correction.",
        )
    assert error.value.status_code == 409
    assert load_current_turns(db_session, transcript=transcript)[0]["text"] == "Synthetic correction."


def test_explicit_delete_can_remove_last_turn_without_mutating_history(db_session, make_user):
    owner = make_user()
    transcript, version, turn = _source(db_session, owner)
    delete_transcript_turn(
        db_session, owner, transcript_id=transcript.id, turn_id=turn["id"],
        expected_version_id=version.id,
    )
    assert load_current_turns(db_session, transcript=transcript) == []
    assert turns_for_version(db_session, version=version)[0]["text"] == "Synthetic opening."
    assert decrypt_text_for_owner(
        db_session, owner_user_id=owner.id, table="transcripts",
        field="current_draft_text_encrypted", record_id=transcript.id,
        stored_value=transcript.current_draft_text_encrypted,
    ) == ""


def test_missing_speaker_metadata_preserves_long_usable_transcript(db_session, make_user):
    owner = make_user()
    transcript, _, _ = _source(db_session, owner)
    source = "Synthetic long source. " * 1200
    combined = append_transcript_turns(
        db_session, transcript=transcript, turns=[], scope_id=uuid4(), fallback_text=source,
    )
    assert combined[-1]["speaker_id"] is None
    assert combined[-1]["text"] == source
    assert canonical_transcript_projection(combined).endswith(source)


def test_matching_version_includes_turn_metadata_and_exact_whitespace(db_session, make_user):
    from app.services.transcripts import _latest_matching_transcript_version

    owner = make_user()
    transcript, version, turn = _source(db_session, owner)
    assert _latest_matching_transcript_version(
        db_session, transcript=transcript, plaintext="Synthetic opening.",
    ).id == version.id
    edit_transcript_turn(
        db_session, owner, transcript_id=transcript.id, turn_id=turn["id"],
        expected_version_id=version.id, text="  Synthetic opening.  ",
    )
    current = _latest_matching_transcript_version(
        db_session, transcript=transcript, plaintext="  Synthetic opening.  ",
    )
    assert current is not None and current.id != version.id
    assert _latest_matching_transcript_version(
        db_session, transcript=transcript, plaintext="Synthetic opening.",
    ) is None


def test_pii_status_does_not_claim_an_older_revision_is_current(db_session, make_user, monkeypatch):
    from app.models import RedactionRun, RedactionRunStatus
    from app.services.content_crypto import encrypt_text_for_owner
    from app.web.transcribe_workspace import transcript_redaction_status_response

    owner = make_user()
    transcript, version, turn = _source(db_session, owner)
    run_id = uuid4()
    db_session.add(RedactionRun(
        id=run_id, transcript_id=transcript.id, transcript_version_id=version.id,
        owner_user_id=owner.id, team_id=owner.team_id, status=RedactionRunStatus.succeeded,
        redacted_text_encrypted=encrypt_text_for_owner(
            db_session, owner_user_id=owner.id, table="redaction_runs",
            field="redacted_text_encrypted", record_id=run_id, plaintext="Synthetic opening.",
        ),
        mapping_hash="synthetic", entity_count=0, api_provider="native_presidio",
    ))
    db_session.commit()
    assert transcript_redaction_status_response(db_session, transcript)["transcript_version_id"] == str(version.id)
    monkeypatch.setattr("app.services.transcripts._attempt_preview_redaction", lambda *args, **kwargs: None)
    edit_transcript_turn(
        db_session, owner, transcript_id=transcript.id, turn_id=turn["id"],
        expected_version_id=version.id, text="Synthetic new source.",
    )
    assert transcript_redaction_status_response(db_session, transcript)["status"] == "not_run"


def test_silent_structured_capture_has_a_revision_for_adding_unattributed_text(db_session, make_user, monkeypatch):
    from app.services.structured_transcripts import add_transcript_turn, current_turns_for_response
    from app.services.transcripts import _preview_redact_current_draft_if_ready

    owner = make_user()
    transcript = Transcript(
        owner_user_id=owner.id, team_id=owner.team_id, status=TranscriptStatus.ready,
        retention_days_applied=30, retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    append_transcript_turns(db_session, transcript=transcript, turns=[], scope_id=uuid4())
    db_session.commit()
    monkeypatch.setattr("app.services.transcripts._attempt_preview_redaction", lambda *args, **kwargs: pytest.fail("Silent source must not dispatch redaction"))
    _preview_redact_current_draft_if_ready(db_session, transcript=transcript)
    revision_id, turns = current_turns_for_response(db_session, transcript=transcript)
    assert revision_id is not None and turns == []
    monkeypatch.setattr("app.services.transcripts._attempt_preview_redaction", lambda *args, **kwargs: None)
    add_transcript_turn(
        db_session, owner, transcript_id=transcript.id, expected_version_id=revision_id,
        text="Synthetic missing source.", after_turn_id=None,
    )
    assert load_current_turns(db_session, transcript=transcript)[0]["speaker_id"] is None


def test_plain_live_chunk_appends_without_flattening_existing_speakers(db_session, make_user, monkeypatch):
    from app.models import TranscriptIngestionJob, TranscriptIngestionJobKind, TranscriptIngestionJobStatus
    from app.services.content_crypto import encrypt_text_for_owner
    from app.services.transcripts import _apply_completed_live_chunks

    owner = make_user()
    transcript, _, original = _source(db_session, owner)
    job_id = uuid4()
    job = TranscriptIngestionJob(
        id=job_id, transcript_id=transcript.id, owner_user_id=owner.id, team_id=owner.team_id,
        job_kind=TranscriptIngestionJobKind.live_chunk, chunk_sequence_no=1,
        source_filename="synthetic.wav", status=TranscriptIngestionJobStatus.completed,
        completed_at=utcnow(),
        result_text_encrypted=encrypt_text_for_owner(
            db_session, owner_user_id=owner.id, table="transcript_ingestion_jobs",
            field="result_text_encrypted", record_id=job_id, plaintext="Synthetic later chunk.",
        ),
    )
    db_session.add(job)
    db_session.commit()
    monkeypatch.setattr("app.services.transcripts._attempt_preview_redaction", lambda *args, **kwargs: None)
    _apply_completed_live_chunks(db_session, transcript)
    current = load_current_turns(db_session, transcript=transcript)
    assert current[0] == original
    assert current[1]["speaker_id"] is None
    assert current[1]["text"] == "Synthetic later chunk."
    assert current[1]["scope_id"] != original["scope_id"]
    assert job.status is TranscriptIngestionJobStatus.applied


def test_deleting_first_speaker_keeps_remaining_label_in_response_and_snapshot(db_session, make_user):
    from uuid import UUID
    from app.services.structured_transcripts import (
        current_turns_for_response,
        render_redacted_diarised_transcript,
    )

    owner = make_user()
    transcript, _, first = _source(db_session, owner)
    turns = append_transcript_turns(
        db_session, transcript=transcript, scope_id=UUID(first["scope_id"]),
        turns=[{"speaker_id": "1", "text": "Synthetic response."}],
    )
    version = _create_transcript_version_from_text(
        db_session, transcript=transcript, owner_user_id=owner.id,
        plaintext=canonical_transcript_projection(turns),
    )
    store_turns_on_version(db_session, version=version, owner_user_id=owner.id, turns=turns)
    db_session.commit()
    delete_transcript_turn(
        db_session, owner, transcript_id=transcript.id, turn_id=UUID(first["id"]),
        expected_version_id=version.id,
    )
    revision_id, remaining = current_turns_for_response(db_session, transcript=transcript)
    assert remaining[0]["speaker_label"] == "Speaker 2"
    latest = db_session.get(TranscriptVersion, revision_id)
    rendered = render_redacted_diarised_transcript(
        db_session, version=latest, redacted_text="Synthetic response.", phi_index=[],
    )
    assert rendered == "Recording 1\nSpeaker 2: Synthetic response."
