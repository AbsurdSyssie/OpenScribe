from uuid import uuid4

import pytest

from app.schemas.transcripts import TranscriptStart, WorkingNoteUpdate
from app.services.dictations import append_post_consultation_dictation_audio, update_post_consultation_dictation
from app.services.structured_transcripts import append_transcript_turns
from app.services.transcripts import (
    clear_working_note,
    commit_transcript_text,
    save_working_note,
    start_transcript,
    update_transcript_title,
)


@pytest.mark.parametrize(
    "working_note",
    [
        {"mode": "freeform", "freeform_text": "Synthetic draft."},
        {"mode": "structured", "structured_note": {"sections": {"history": ["Synthetic draft."]}}},
    ],
    ids=["freeform", "structured"],
)
def test_first_working_note_save_sets_timestamp_once(db_session, make_user, working_note):
    owner = make_user()
    transcript = start_transcript(db_session, owner, TranscriptStart())
    transcript_id = transcript.id
    original_created_at = transcript.created_at
    original_expiry = transcript.retention_expires_at
    assert transcript.first_content_at is None

    update_transcript_title(db_session, owner, transcript_id=transcript_id, title="Synthetic title")
    assert transcript.first_content_at is None

    save_working_note(db_session, owner, transcript_id=transcript_id, payload=WorkingNoteUpdate(**working_note))
    first_saved_at = transcript.first_content_at
    assert first_saved_at is not None

    clear_working_note(db_session, owner, transcript_id=transcript_id, expected_updated_at=transcript.working_note_updated_at)
    save_working_note(db_session, owner, transcript_id=transcript_id, payload=WorkingNoteUpdate(**working_note))
    assert transcript.first_content_at == first_saved_at
    assert transcript.created_at == original_created_at
    assert transcript.retention_expires_at == original_expiry


def test_transcript_text_and_timestamp_roll_back_together(db_session, make_user):
    owner = make_user()
    transcript = start_transcript(db_session, owner, TranscriptStart())
    transcript_id = transcript.id
    commit_transcript_text(
        db_session, owner, transcript_id=transcript_id, plaintext="   ", preview_redaction=False,
    )
    assert transcript.first_content_at is None

    commit_transcript_text(
        db_session, owner, transcript_id=transcript_id, plaintext="Synthetic transcript.",
        commit=False, preview_redaction=False,
    )
    db_session.rollback()
    assert transcript.first_content_at is None

    commit_transcript_text(
        db_session, owner, transcript_id=transcript_id, plaintext="Synthetic transcript.", preview_redaction=False,
    )
    assert transcript.first_content_at is not None


def test_legacy_blank_consultation_keeps_original_order_after_first_save(db_session, make_user):
    owner = make_user()
    transcript = start_transcript(db_session, owner, TranscriptStart())
    transcript.track_first_content_at = False
    db_session.commit()
    original_created_at = transcript.created_at

    save_working_note(
        db_session, owner, transcript_id=transcript.id,
        payload=WorkingNoteUpdate(mode="freeform", freeform_text="Synthetic draft."),
    )

    assert transcript.first_content_at is None
    assert transcript.created_at == original_created_at


def test_saved_structured_transcript_turn_sets_timestamp(db_session, make_user):
    owner = make_user()
    transcript = start_transcript(db_session, owner, TranscriptStart())
    append_transcript_turns(
        db_session, transcript=transcript, scope_id=uuid4(), turns=[{"text": "   ", "speaker_id": "0"}],
    )
    db_session.commit()
    assert transcript.first_content_at is None

    append_transcript_turns(
        db_session, transcript=transcript, scope_id=uuid4(), turns=[{"text": "Synthetic turn.", "speaker_id": "0"}],
    )
    db_session.commit()
    assert transcript.first_content_at is not None


@pytest.mark.parametrize("source", ["typed", "asr_segment"])
def test_first_nonblank_dictation_save_sets_timestamp(db_session, make_user, monkeypatch, source):
    owner = make_user()
    transcript = start_transcript(db_session, owner, TranscriptStart())
    update_post_consultation_dictation(db_session, owner, transcript_id=transcript.id, combined_text="   ")
    assert transcript.first_content_at is None

    if source == "typed":
        update_post_consultation_dictation(
            db_session, owner, transcript_id=transcript.id, combined_text="Synthetic dictation.",
        )
    else:
        monkeypatch.setattr(
            "app.services.dictations.transcribe_post_consultation_dictation_audio",
            lambda *_args, **_kwargs: "Synthetic dictation segment.",
        )
        append_post_consultation_dictation_audio(
            db_session, owner, transcript_id=transcript.id, audio_bytes=b"synthetic audio", filename="synthetic.wav",
        )

    assert transcript.first_content_at is not None
