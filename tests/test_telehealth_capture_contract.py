"""Pure telehealth PCM/WAV and SQLAlchemy metadata contracts, synthetic only."""
import io
import struct
import wave

import pytest

from app.errors import AppError
from app.models import TelehealthCapturePart, TelehealthCaptureSession
from app.services.telehealth_capture import _inspect_canonical_pcm16_wav


def wav_samples(samples: tuple[int, ...], rate: int = 16000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(struct.pack("<" + "h" * len(samples), *samples))
    return out.getvalue()


def test_staged_audio_inspects_real_pcm_frames_not_client_duration_claims():
    data = wav_samples((1, 4, -3, 20, -24, 1, 0, 12))
    assert data[0:4] == b"RIFF"
    assert _inspect_canonical_pcm16_wav(data) == (16000, 8, 8 / 16000)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda audio: audio + b"\x00\x00",  # Hidden trailing content rejected.
        lambda audio: audio[:-2],  # Truncated source body.
        lambda audio: audio[:4] + b"\x00" * 4 + audio[8:],  # Incorrect RIFF size.
        lambda audio: audio[:8] + b"BOOM" + audio[12:],  # Not WAV.
        lambda audio: b"",  # No speech/audio.
    ],
)
def test_rejects_malformed_or_partially_transferred_audio(mutator):
    with pytest.raises(AppError):
        _inspect_canonical_pcm16_wav(mutator(wav_samples((100, 200, 300))))


def test_rejects_stereo_and_non_pcm16_format():
    with io.BytesIO() as out:
        with wave.open(out, "wb") as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(struct.pack("<hhhh", 1, 2, 3, 4))
        with pytest.raises(AppError) as error:
            _inspect_canonical_pcm16_wav(out.getvalue())
        assert error.value.message == "Telehealth audio must be mono PCM16."


def test_staged_session_tables_have_owner_root_and_cleanup_metadata():
    session = TelehealthCaptureSession.__table__
    part = TelehealthCapturePart.__table__
    assert {"owner_user_id", "transcript_id", "team_id", "status"}.issubset(session.columns.keys())
    assert {
        "owner_user_id",
        "transcript_id",
        "team_id",
        "source_audio_vault_ref",
        "source_audio_expires_at",
        "source_audio_fingerprint",
        "idempotency_key",
        "timeline_start_frame",
        "timeline_end_frame",
        "timing_spans_json",
    }.issubset(part.columns.keys())
    assert "uq_telehealth_capture_parts_channel_sequence" in {
        item.name for item in part.constraints
    }
    assert "uq_telehealth_capture_parts_idempotency" in {
        item.name for item in part.constraints
    }
    assert "uq_telehealth_capture_sessions_open_transcript" in {
        index.name for index in session.indexes
    }



@pytest.mark.parametrize("previous_status", ["recording", "transcribing", "failed"])
def test_new_telehealth_capture_cannot_overlap_unresolved_consultation_status(
    db_session, make_team, make_user, previous_status,
):
    from datetime import timedelta
    from sqlalchemy import select
    from app.models import Transcript, TranscriptIngestionMode, TranscriptStatus, utcnow
    from app.services.telehealth_capture import begin_telehealth_capture

    team = make_team(name="Telehealth Concurrent Recorder Guard")
    owner = make_user(
        email=f"telehealth-overlap-{previous_status}@example.com",
        password="password-1", team=team,
    )
    transcript = Transcript(
        owner_user_id=owner.id, team_id=owner.team_id,
        status=TranscriptStatus(previous_status),
        ingestion_mode=TranscriptIngestionMode.whole_file,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=1),
    )
    db_session.add(transcript)
    db_session.commit()
    with pytest.raises(AppError) as error:
        begin_telehealth_capture(
            db_session, owner,
            transcript_id=transcript.id, timeline_sample_rate=48000,
        )
    assert error.value.status_code == 409
    assert error.value.code == "conflict"
    assert db_session.scalars(select(TelehealthCaptureSession).where(
        TelehealthCaptureSession.transcript_id == transcript.id,
    )).first() is None
