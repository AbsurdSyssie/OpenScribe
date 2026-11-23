"""Presence metadata regression tests for the recent consultation sidebar."""

from uuid import uuid4
from sqlalchemy import select

from app.models import TranscriptVersion
from app.schemas.transcripts import TranscriptStart
from app.services.structured_transcripts import append_transcript_turns
from app.services.transcripts import start_transcript, commit_transcript_text
from app.web import transcribe_workspace as workspace


def _presence(db, owner, transcript):
    items = workspace.list_transcript_history_page(db, owner)["items"]
    return next(item.has_transcript_content for item in items if item.id == transcript.id)


def test_saved_flags_cover_blank_edits_and_version_deletion(db_session, make_user, monkeypatch):
    owner = make_user()
    transcript = start_transcript(db_session, owner, TranscriptStart())
    assert transcript.current_draft_has_content is False
    assert _presence(db_session, owner, transcript) is False
    commit_transcript_text(db_session, owner, transcript_id=transcript.id, plaintext="   ", preview_redaction=False)
    assert _presence(db_session, owner, transcript) is False
    commit_transcript_text(db_session, owner, transcript_id=transcript.id, plaintext="Synthetic text", preview_redaction=False)
    assert transcript.current_draft_has_content is True
    commit_transcript_text(db_session, owner, transcript_id=transcript.id, plaintext="\n\t", preview_redaction=False)
    assert transcript.current_draft_has_content is False
    assert _presence(db_session, owner, transcript) is True

    def fail_decrypt(*_args, **_kwargs):
        raise AssertionError("history content presence should use metadata")

    monkeypatch.setattr(workspace, "transcript_draft_text_service", fail_decrypt)
    monkeypatch.setattr(workspace, "transcript_version_text_service", fail_decrypt)
    assert _presence(db_session, owner, transcript) is True
    for version in db_session.scalars(select(TranscriptVersion).where(TranscriptVersion.transcript_id == transcript.id)):
        if version.has_content:
            db_session.delete(version)
    db_session.commit()
    assert _presence(db_session, owner, transcript) is False


def test_legacy_null_flags_preserve_historical_content(db_session, make_user):
    owner = make_user()
    transcript = start_transcript(db_session, owner, TranscriptStart())
    commit_transcript_text(db_session, owner, transcript_id=transcript.id, plaintext="Older text", preview_redaction=False)
    commit_transcript_text(db_session, owner, transcript_id=transcript.id, plaintext=" ", preview_redaction=False)
    transcript.current_draft_has_content = None
    for version in db_session.scalars(select(TranscriptVersion).where(TranscriptVersion.transcript_id == transcript.id)):
        version.has_content = None
    db_session.commit()
    assert _presence(db_session, owner, transcript) is True
    for version in db_session.scalars(select(TranscriptVersion).where(TranscriptVersion.transcript_id == transcript.id)):
        db_session.delete(version)
    db_session.commit()
    assert _presence(db_session, owner, transcript) is False


def test_structured_transcript_update_sets_draft_flag(db_session, make_user):
    owner = make_user()
    transcript = start_transcript(db_session, owner, TranscriptStart())
    append_transcript_turns(db_session, transcript=transcript, scope_id=uuid4(), turns=[
        {"text": "Synthetic speech", "speaker_id": "0"},
    ])
    db_session.commit()
    assert transcript.current_draft_has_content is True
    assert _presence(db_session, owner, transcript) is True
