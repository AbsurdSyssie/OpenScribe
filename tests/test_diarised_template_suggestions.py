from datetime import timedelta
from uuid import uuid4

import pytest

from app.models import Transcript, TranscriptStatus, UserAppPreference, utcnow
from app.services.structured_transcripts import append_transcript_turns, canonical_transcript_projection, store_turns_on_version
from app.services.template_suggestions import (
    _excerpt, _excerpt_turns, _frozen_excerpt_turns, _messages, queue_template_suggestion,
)
from app.services.transcripts import _create_transcript_version_from_text


@pytest.mark.parametrize("limit", [0, 1, 3, 4, 5, 6, 7, 8])
def test_clipped_speaker_excerpt_is_exact_canonical_prefix(limit):
    turns = [{"text": "A😀B", "speaker_id": "0"}, {"text": "Tail", "speaker_id": "1"}]
    excerpt = canonical_transcript_projection(turns)[:limit]
    clipped = _excerpt_turns(turns, excerpt)
    assert canonical_transcript_projection(clipped) == excerpt
    assert turns[0]["text"] == "A😀B"


def test_template_suggestion_freezes_encrypted_turn_excerpt_before_later_source_changes(db_session, make_user):
    owner = make_user()
    db_session.add(UserAppPreference(user_id=owner.id, preferences_json={"template_suggestions_enabled": True}))
    transcript = Transcript(
        owner_user_id=owner.id, team_id=owner.team_id, status=TranscriptStatus.ready,
        retention_days_applied=30, retention_expires_at=utcnow() + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    turns = append_transcript_turns(
        db_session, transcript=transcript, scope_id=uuid4(),
        turns=[{"speaker_id": "0", "text": "Synthetic source sentence. " * 55}],
    )
    projection = canonical_transcript_projection(turns)
    version = _create_transcript_version_from_text(
        db_session, transcript=transcript, owner_user_id=owner.id, plaintext=projection,
    )
    store_turns_on_version(db_session, version=version, owner_user_id=owner.id, turns=turns)
    db_session.commit()
    job = queue_template_suggestion(db_session, owner, transcript_id=transcript.id)
    assert job is not None
    assert job.excerpt_turns_snapshot_encrypted
    assert "Synthetic source" not in job.excerpt_turns_snapshot_encrypted
    frozen = _frozen_excerpt_turns(db_session, job)
    append_transcript_turns(
        db_session, transcript=transcript, scope_id=uuid4(),
        turns=[{"speaker_id": "1", "text": "Later synthetic recording."}],
    )
    db_session.commit()
    assert _frozen_excerpt_turns(db_session, job) == frozen
    assert _excerpt(db_session, job) == projection
    assert canonical_transcript_projection(frozen) == projection


def test_suggestion_role_guidance_depends_on_saved_speaker_source():
    plain, _ = _messages(excerpt="Synthetic excerpt", candidates=[])
    diarised, _ = _messages(excerpt="Speaker 1: Synthetic excerpt", candidates=[], diarised_transcript=True)
    assert "Automated speaker labels" not in plain
    assert "Automated speaker labels" in diarised
    assert "preserve uncertainty" in diarised
