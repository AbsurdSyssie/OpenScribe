import io
import wave
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.errors import AppError
from app.models import (
    AttemptKind,
    AttemptOutcome,
    AttemptStatus,
    ProviderAttempt,
    TranscriptIngestionJob,
    TranscriptIngestionJobStatus,
    ProviderSettlementBasis,
    TranscriptIngestionMode,
    TranscriptStatus,
    TranscriptStreamSession,
    TranscriptStreamStatus,
    TranscriptStreamWindow,
    TranscriptStreamWindowStatus,
    SttAdapterKind,
    SttAuthMode,
    SttProviderPreset,
    utcnow,
)
from app.schemas.transcripts import TranscriptStart
from app.services.transcripts import start_transcript
from app.services.transcript_streaming import (
    authorize_stream_batch,
    commit_stream_window,
    discard_transcript_stream_gap,
    fail_transcript_stream,
    finish_transcript_stream,
    reconcile_expired_stream_sessions,
    queue_transcript_stream_recovery,
    start_transcript_stream,
)
from app.services.stt_presets import DEEPGRAM_GLOBAL_BASE_URL


def _synthetic_pcm_wav(sample_count: int, pcm_bytes: bytes | None = None) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(pcm_bytes if pcm_bytes is not None else b"\x00\x00" * sample_count)
    return output.getvalue()


def _stream_fixture(db_session, make_team, make_user, monkeypatch, *, now=None, lease_seconds=15,
                    ingestion_mode=TranscriptIngestionMode.live_chunked, start_stream=True):
    team = make_team(name=f"Synthetic stream {uuid4()}")
    owner = make_user(email=f"stream-{uuid4()}@example.com", team=team)
    admin = make_user(email=f"stream-admin-{uuid4()}@example.com", is_system_admin=True)
    owner.daily_audio_seconds_limit = 3_600
    owner.monthly_audio_seconds_limit = 10_000
    db_session.add(owner)
    db_session.commit()
    transcript = start_transcript(
        db_session, owner,
        TranscriptStart(title="Synthetic stream", ingestion_mode=ingestion_mode),
    )
    from app.models import TeamSttConfig

    config = TeamSttConfig(
        id=uuid4(), team_id=team.id, label="Synthetic Deepgram", provider_preset=SttProviderPreset.deepgram.value,
        adapter_kind=SttAdapterKind.generic_rest, base_url=DEEPGRAM_GLOBAL_BASE_URL,
        transcribe_path="/v1/listen", model_name="nova-3", available_models_json=[],
        file_field_name="file", language="en", response_text_path="text", extra_form_fields_json={"mip_opt_out": "true"},
        auth_mode=SttAuthMode.bearer, vault_secret_ref="secret:synthetic-stream-test",
        created_by_user_id=admin.id, updated_by_user_id=admin.id,
    )
    db_session.add(config)
    db_session.commit()
    started = None
    if start_stream:
        started = start_transcript_stream(
            db_session, owner, transcript_id=transcript.id,
            selection_snapshot={
                "config_id": str(config.id), "provider_preset": SttProviderPreset.deepgram.value,
                "adapter_kind": SttAdapterKind.generic_rest.value, "base_url": DEEPGRAM_GLOBAL_BASE_URL,
                "model_name": "nova-3", "language": "en", "mip_opt_out": True,
                "extra_query_params": {"mip_opt_out": "true"},
            }, now=now, lease_seconds=lease_seconds,
        )
    return owner, transcript, started, config


def test_one_sample_success_settles_by_exact_stream_samples_without_clip_measurement(
    db_session, make_team, make_user, monkeypatch,
):
    owner, transcript, started, _config = _stream_fixture(db_session, make_team, make_user, monkeypatch)
    authorization = authorize_stream_batch(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        first_sample_offset=0, sample_count=1, pcm_bytes=b"\x00\x00",
    )
    commit_stream_window(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        window_no=authorization.window_no, source_end_sample_offset=1, forwarded_sample_count=1,
        finalized_text="synthetic final", final_segment_count=1,
    )
    finish_transcript_stream(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
    )
    attempt = db_session.scalar(select(ProviderAttempt).where(
        ProviderAttempt.transcript_stream_session_id == started.session_id,
    ))
    assert authorization.dispatchable
    assert attempt is not None
    assert attempt.status is AttemptStatus.settled
    assert attempt.outcome is AttemptOutcome.succeeded
    assert attempt.settlement_basis is ProviderSettlementBasis.stream_measured
    assert attempt.stream_authorized_samples == 1
    assert attempt.reserved_units == attempt.settled_units == 1
    assert attempt.measured_audio_seconds is None


def test_expired_stream_lease_becomes_failed_generation_barrier_and_unknown_charge(
    db_session, make_team, make_user, monkeypatch,
):
    owner, transcript, started, _config = _stream_fixture(db_session, make_team, make_user, monkeypatch)
    authorization = authorize_stream_batch(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        first_sample_offset=0, sample_count=16_000, pcm_bytes=b"\x00\x00" * 16_000,
    )
    now = utcnow() + timedelta(minutes=1)
    changed = reconcile_expired_stream_sessions(db_session, now=now)
    db_session.refresh(transcript)
    session = db_session.get(TranscriptStreamSession, started.session_id)
    window = db_session.scalar(select(TranscriptStreamWindow).where(
        TranscriptStreamWindow.session_id == started.session_id,
    ))
    attempt = db_session.scalar(select(ProviderAttempt).where(
        ProviderAttempt.transcript_stream_session_id == started.session_id,
    ))
    assert changed == 1
    assert authorization.dispatchable
    assert transcript.status is TranscriptStatus.failed
    assert session is not None and session.status is TranscriptStreamStatus.failed
    assert window is not None and window.status is TranscriptStreamWindowStatus.failed
    assert attempt is not None
    assert attempt.status is AttemptStatus.settled
    assert attempt.outcome is AttemptOutcome.unknown
    assert attempt.settlement_basis is ProviderSettlementBasis.stream_authorized
    assert attempt.settled_units == attempt.reserved_units == 1


def test_stream_quota_reservation_rolls_over_at_utc_day_boundary_before_forwarding(
    db_session, make_team, make_user, monkeypatch,
):
    first = utcnow().replace(hour=23, minute=59, second=58, microsecond=0)
    owner, transcript, started, _config = _stream_fixture(
        db_session, make_team, make_user, monkeypatch, now=first, lease_seconds=60,
    )
    owner.daily_audio_seconds_limit = 2
    db_session.add(owner)
    db_session.commit()
    authorize_stream_batch(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        first_sample_offset=0, sample_count=16_000, pcm_bytes=b"\x00\x00" * 16_000,
        now=first + timedelta(seconds=1),
    )
    with pytest.raises(AppError) as checkpoint_required:
        authorize_stream_batch(
            db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
            connection_fence=started.connection_fence, fence_token=started.fence_token,
            first_sample_offset=16_000, sample_count=16_000, pcm_bytes=b"\x00\x00" * 16_000,
            now=first + timedelta(seconds=2),
        )
    assert checkpoint_required.value.code == "stream_quota_checkpoint_required"
    checkpoint = commit_stream_window(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        window_no=0, source_end_sample_offset=16_000, forwarded_sample_count=16_000,
        finalized_text="synthetic checkpoint", final_segment_count=1,
        now=first + timedelta(seconds=2),
    )
    assert checkpoint.forwarded_sample_count == 16_000
    authorize_stream_batch(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        first_sample_offset=16_000, sample_count=16_000, pcm_bytes=b"\x00\x00" * 16_000,
        now=first + timedelta(seconds=2),
    )
    authorize_stream_batch(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        first_sample_offset=32_000, sample_count=16_000, pcm_bytes=b"\x00\x00" * 16_000,
        now=first + timedelta(seconds=2),
    )
    attempts = list(db_session.scalars(select(ProviderAttempt).where(
        ProviderAttempt.transcript_stream_session_id == started.session_id,
    ).order_by(ProviderAttempt.attempt_number)))
    assert len(attempts) == 2
    assert [attempt.stream_authorized_samples for attempt in attempts] == [16_000, 32_000]
    assert attempts[0].status is AttemptStatus.settled
    assert attempts[0].settled_units == 1
    assert attempts[1].status is AttemptStatus.submitted
    assert attempts[1].reserved_units == 2

    with pytest.raises(AppError) as caught:
        authorize_stream_batch(
            db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
            connection_fence=started.connection_fence, fence_token=started.fence_token,
            first_sample_offset=48_000, sample_count=16_000, pcm_bytes=b"\x00\x00" * 16_000,
            now=first + timedelta(seconds=3),
        )
    assert caught.value.code == "quota_exceeded"
    assert len(list(db_session.scalars(select(ProviderAttempt).where(
        ProviderAttempt.transcript_stream_session_id == started.session_id,
    )))) == 2


def test_stream_duplicate_delivery_is_not_dispatchable_and_changed_payload_is_rejected(
    db_session, make_team, make_user, monkeypatch,
):
    owner, transcript, started, _config = _stream_fixture(db_session, make_team, make_user, monkeypatch)
    args = dict(
        transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        first_sample_offset=0, sample_count=2, pcm_bytes=b"\x01\x00\x02\x00",
    )
    first = authorize_stream_batch(db_session, owner.id, **args)
    replay = authorize_stream_batch(db_session, owner.id, **args)
    assert first.dispatchable is True
    assert replay.dispatchable is False
    assert replay.delivery_id == first.delivery_id
    from app.errors import AppError
    with pytest.raises(AppError) as caught:
        authorize_stream_batch(db_session, owner.id, **{**args, "pcm_bytes": b"\x03\x00\x04\x00"})
    assert caught.value.code == "stream_audio_interval_conflict"


def test_stream_start_rejects_wrong_owner_and_whole_file_root(
    db_session, make_team, make_user, monkeypatch,
):
    owner, transcript, _started, config = _stream_fixture(
        db_session, make_team, make_user, monkeypatch,
        ingestion_mode=TranscriptIngestionMode.whole_file,
        start_stream=False,
    )
    snapshot = {
        "config_id": str(config.id), "provider_preset": SttProviderPreset.deepgram.value,
        "adapter_kind": SttAdapterKind.generic_rest.value, "base_url": DEEPGRAM_GLOBAL_BASE_URL,
        "model_name": "nova-3", "language": "en", "mip_opt_out": True,
        "extra_query_params": {"mip_opt_out": "true"},
    }
    with pytest.raises(AppError) as denied_mode:
        start_transcript_stream(db_session, owner, transcript_id=transcript.id, selection_snapshot=snapshot)
    assert denied_mode.value.code == "stream_transcript_mode_unsupported"

    live_owner, live_transcript, _started, live_config = _stream_fixture(
        db_session, make_team, make_user, monkeypatch,
    )
    other_owner = make_user(email=f"stream-other-{uuid4()}@example.com", team=live_owner.team)
    db_session.add(other_owner)
    db_session.commit()
    live_snapshot = {**snapshot, "config_id": str(live_config.id)}
    with pytest.raises(AppError) as denied_owner:
        start_transcript_stream(
            db_session, other_owner, transcript_id=live_transcript.id, selection_snapshot=live_snapshot,
        )
    assert denied_owner.value.code == "not_found"


def test_expired_lease_after_committed_window_completes_without_inventing_gap(
    db_session, make_team, make_user, monkeypatch,
):
    owner, transcript, started, _config = _stream_fixture(db_session, make_team, make_user, monkeypatch)
    authorization = authorize_stream_batch(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        first_sample_offset=0, sample_count=16_000, pcm_bytes=b"\x00\x00" * 16_000,
    )
    commit_stream_window(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        window_no=authorization.window_no, source_end_sample_offset=16_000,
        forwarded_sample_count=16_000, finalized_text="synthetic final", final_segment_count=1,
    )
    changed = reconcile_expired_stream_sessions(db_session, now=utcnow() + timedelta(minutes=1))
    session = db_session.get(TranscriptStreamSession, started.session_id)
    windows = list(db_session.scalars(select(TranscriptStreamWindow).where(
        TranscriptStreamWindow.session_id == started.session_id,
    )))
    assert changed == 1
    assert session is not None and session.status is TranscriptStreamStatus.completed
    assert session.failure_code is None
    assert windows and all(window.status is not TranscriptStreamWindowStatus.failed for window in windows)


def test_failed_window_does_not_retain_audio_with_mismatched_sample_count(
    db_session, make_team, make_user, monkeypatch,
):
    owner, transcript, started, _config = _stream_fixture(db_session, make_team, make_user, monkeypatch)
    authorization = authorize_stream_batch(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        first_sample_offset=0, sample_count=16_000, pcm_bytes=b"\x00\x00" * 16_000,
    )
    vault_writes = []
    monkeypatch.setattr(
        "app.services.vault.write_transcript_ingestion_source_audio",
        lambda **kwargs: vault_writes.append(kwargs) or "secret:synthetic-mismatch",
    )
    result = fail_transcript_stream(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        error_code="stream_failed", recovery_audio_bytes=_synthetic_pcm_wav(32_000),
    )
    window = db_session.scalar(select(TranscriptStreamWindow).where(
        TranscriptStreamWindow.session_id == started.session_id,
        TranscriptStreamWindow.window_no == authorization.window_no,
    ))
    assert result.status is TranscriptStreamStatus.failed
    assert result.recovery_available is False
    assert vault_writes == []
    assert window is not None and window.recovery_audio_vault_ref is None


def test_failed_window_does_not_retain_same_length_audio_with_changed_samples(
    db_session, make_team, make_user, monkeypatch,
):
    owner, transcript, started, _config = _stream_fixture(db_session, make_team, make_user, monkeypatch)
    authorization = authorize_stream_batch(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        first_sample_offset=0, sample_count=16_000, pcm_bytes=b"\x00\x00" * 16_000,
    )
    vault_writes = []
    monkeypatch.setattr(
        "app.services.vault.write_transcript_ingestion_source_audio",
        lambda **kwargs: vault_writes.append(kwargs) or "secret:synthetic-changed-audio",
    )
    result = fail_transcript_stream(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        error_code="stream_failed", recovery_audio_bytes=_synthetic_pcm_wav(16_000, b"\x01\x00" * 16_000),
    )
    window = db_session.scalar(select(TranscriptStreamWindow).where(
        TranscriptStreamWindow.session_id == started.session_id,
        TranscriptStreamWindow.window_no == authorization.window_no,
    ))
    assert result.recovery_available is False
    assert vault_writes == []
    assert window is not None and window.recovery_audio_vault_ref is None
    with pytest.raises(AppError) as mismatch:
        queue_transcript_stream_recovery(
            db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
            window_no=authorization.window_no,
            audio_bytes=_synthetic_pcm_wav(16_000, b"\x01\x00" * 16_000),
        )
    assert mismatch.value.code == "stream_recovery_audio_mismatch"
    assert vault_writes == []
    assert db_session.scalar(select(TranscriptIngestionJob.id).where(
        TranscriptIngestionJob.transcript_stream_window_id == window.id,
    )) is None


def test_recovery_audio_digest_mapping_allows_source_offset_gaps(
    db_session, make_team, make_user, monkeypatch,
):
    owner, transcript, started, _config = _stream_fixture(db_session, make_team, make_user, monkeypatch)
    first_pcm = b"\x01\x00" * 3
    second_pcm = b"\x02\x00" * 2
    first = authorize_stream_batch(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        first_sample_offset=0, sample_count=3, pcm_bytes=first_pcm,
    )
    authorize_stream_batch(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        first_sample_offset=8, sample_count=2, pcm_bytes=second_pcm,
    )
    recovery_audio = _synthetic_pcm_wav(5, first_pcm + second_pcm)
    vault_writes = []
    monkeypatch.setattr(
        "app.services.vault.write_transcript_ingestion_source_audio",
        lambda **kwargs: vault_writes.append(kwargs) or "secret:synthetic-gapped-audio",
    )
    result = fail_transcript_stream(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        error_code="stream_failed", recovery_audio_bytes=recovery_audio,
    )
    assert result.recovery_available is True
    assert len(vault_writes) == 1
    assert first.dispatchable is True


def test_repeated_or_late_stream_failure_preserves_existing_failed_gap_barrier(
    db_session, make_team, make_user, monkeypatch,
):
    owner, transcript, started, _config = _stream_fixture(db_session, make_team, make_user, monkeypatch)
    authorization = authorize_stream_batch(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        first_sample_offset=0, sample_count=16_000, pcm_bytes=b"\x00\x00" * 16_000,
    )
    writes = []
    monkeypatch.setattr(
        "app.services.vault.write_transcript_ingestion_source_audio",
        lambda **kwargs: writes.append(kwargs) or "secret:synthetic-original-failure",
    )
    first = fail_transcript_stream(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        error_code="stream_failed", recovery_audio_bytes=_synthetic_pcm_wav(16_000),
    )
    original_window = db_session.scalar(select(TranscriptStreamWindow).where(
        TranscriptStreamWindow.session_id == started.session_id,
        TranscriptStreamWindow.window_no == authorization.window_no,
    ))
    original_deadline = original_window.recovery_audio_expires_at
    repeated = fail_transcript_stream(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        error_code="stream_provider_failed", recovery_audio_bytes=_synthetic_pcm_wav(16_000),
    )
    db_session.refresh(original_window)
    assert first.status is repeated.status is TranscriptStreamStatus.failed
    assert repeated.recovery_available is True
    assert original_window.recovery_audio_expires_at == original_deadline
    assert len(writes) == 1

    late_owner, late_transcript, late_started, _late_config = _stream_fixture(
        db_session, make_team, make_user, monkeypatch,
    )
    authorize_stream_batch(
        db_session, late_owner.id, transcript_id=late_transcript.id, session_id=late_started.session_id,
        connection_fence=late_started.connection_fence, fence_token=late_started.fence_token,
        first_sample_offset=0, sample_count=16_000, pcm_bytes=b"\x00\x00" * 16_000,
    )
    expired_at = utcnow() + timedelta(minutes=1)
    reconcile_expired_stream_sessions(db_session, now=expired_at)
    late = fail_transcript_stream(
        db_session, late_owner.id, transcript_id=late_transcript.id, session_id=late_started.session_id,
        connection_fence=late_started.connection_fence, fence_token=late_started.fence_token,
        error_code="stream_failed", recovery_audio_bytes=_synthetic_pcm_wav(16_000),
        now=expired_at + timedelta(seconds=1),
    )
    assert late.status is TranscriptStreamStatus.failed
    assert late.recovery_available is False
    assert len(writes) == 1


def test_failed_window_recovery_is_paid_once_and_does_not_double_count_capture_hourly(
    db_session, make_team, make_user, make_stt_selection, monkeypatch,
):
    owner, transcript, started, config = _stream_fixture(db_session, make_team, make_user, monkeypatch)
    make_stt_selection(config=config, actor=owner)
    from app.models import TranscriptStreamWindow
    from app.services import transcript_streaming

    # A thirty-second capture exactly fills the synthetic hourly allowance. The
    # explicitly linked recovery upload is retried provider-paid but is not a
    # second capture of new audio.
    monkeypatch.setattr(transcript_streaming, "LIVE_STT_HOURLY_DURATION_LIMIT_SECONDS", 30)
    audio = _synthetic_pcm_wav(30 * 16_000)
    stored_refs = []
    monkeypatch.setattr(
        "app.services.vault.write_transcript_ingestion_source_audio",
        lambda *, job_id, audio_bytes: stored_refs.append((job_id, audio_bytes)) or "secret:synthetic-stream-recovery",
    )
    monkeypatch.setattr(
        "app.services.transcripts.write_transcript_ingestion_source_audio",
        lambda *, job_id, audio_bytes: "secret:synthetic-retry-job",
    )
    monkeypatch.setattr("app.services.transcripts.inspect_audio_duration_seconds", lambda **_kwargs: 30.0)
    monkeypatch.setattr(
        "app.services.transcripts.ensure_stt_config_credential_ready", lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.transcripts.try_publish_task_dispatch_safely", lambda *_args, **_kwargs: None,
    )
    # A root deadline earlier than 24 hours must clamp the retained failed
    # source. The ref remains encrypted/Vault-backed and is not logged.
    now = utcnow()
    transcript.retention_expires_at = now + timedelta(hours=2)
    db_session.add(transcript)
    db_session.commit()
    for batch_no in range(30):
        authorized = authorize_stream_batch(
            db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
            connection_fence=started.connection_fence, fence_token=started.fence_token,
            first_sample_offset=batch_no * 16_000, sample_count=16_000,
            pcm_bytes=b"\x00\x00" * 16_000, now=now,
        )
    failed = fail_transcript_stream(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        error_code="stream_failed", recovery_audio_bytes=audio, now=now + timedelta(seconds=1),
    )
    window = db_session.scalar(select(TranscriptStreamWindow).where(
        TranscriptStreamWindow.session_id == started.session_id,
        TranscriptStreamWindow.window_no == authorized.window_no,
    ))
    assert failed.status is TranscriptStreamStatus.failed
    assert failed.recovery_available
    assert stored_refs == [(window.id, audio)]
    assert window.recovery_audio_expires_at == transcript.retention_expires_at

    monkeypatch.setattr(
        "app.services.transcripts.resolve_selected_team_stt",
        lambda *_args, **_kwargs: (object(), config, "nova-3", "en"),
    )
    with pytest.raises(AppError) as mismatched_audio:
        queue_transcript_stream_recovery(
            db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
            window_no=authorized.window_no,
            audio_bytes=_synthetic_pcm_wav(30 * 16_000, b"\x01\x00" * (30 * 16_000)),
            now=now + timedelta(seconds=2),
        )
    assert mismatched_audio.value.code == "stream_recovery_audio_mismatch"

    job_id = queue_transcript_stream_recovery(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        window_no=authorized.window_no, audio_bytes=audio, now=now + timedelta(seconds=2),
    )
    job = db_session.get(TranscriptIngestionJob, job_id)
    attempts = list(db_session.scalars(select(ProviderAttempt).where(
        ProviderAttempt.correlation_id == job_id,
    )))
    assert job is not None and job.status is TranscriptIngestionJobStatus.queued
    assert job.transcript_stream_window_id == window.id
    assert job.source_audio_vault_ref == "secret:synthetic-retry-job"
    assert job.source_audio_expires_at == transcript.retention_expires_at
    assert len(attempts) == 1 and attempts[0].status is AttemptStatus.reserved
    assert db_session.scalar(select(ProviderAttempt.id).where(
        ProviderAttempt.transcript_stream_session_id == started.session_id,
        ProviderAttempt.attempt_kind == AttemptKind.stt_deepgram_live_stream,
    )) is not None
    # The capture counter still contains the original sample interval once.
    with pytest.raises(AppError) as caught:
        transcript_streaming.enforce_live_stt_hourly_audio_limit(
            db_session, owner_user_id=owner.id, requested_sample_count=1, now=now + timedelta(seconds=2),
        )
    assert caught.value.code == "rate_limited"


def test_owner_can_discard_failed_window_and_restore_transcript_readiness(
    db_session, make_team, make_user, monkeypatch,
):
    owner, transcript, started, _config = _stream_fixture(db_session, make_team, make_user, monkeypatch)
    authorization = authorize_stream_batch(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        first_sample_offset=0, sample_count=16_000, pcm_bytes=b"\x00\x00" * 16_000,
    )
    failed = fail_transcript_stream(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        error_code="stream_failed",
    )
    assert failed.status is TranscriptStreamStatus.failed
    discarded = discard_transcript_stream_gap(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        window_no=authorization.window_no,
    )
    db_session.refresh(transcript)
    window = db_session.scalar(select(TranscriptStreamWindow).where(
        TranscriptStreamWindow.session_id == started.session_id,
        TranscriptStreamWindow.window_no == authorization.window_no,
    ))
    assert discarded.status is TranscriptStreamStatus.discarded
    assert window is not None and window.status is TranscriptStreamWindowStatus.discarded
    assert transcript.status is TranscriptStatus.ready


def test_recovery_retry_near_24_hour_deadline_never_extends_source_audio_retention(
    db_session, make_team, make_user, make_stt_selection, monkeypatch,
):
    base = utcnow()
    owner, transcript, started, config = _stream_fixture(
        db_session, make_team, make_user, monkeypatch, now=base, lease_seconds=60,
    )
    make_stt_selection(config=config, actor=owner)
    from app.models import TranscriptStreamWindow

    monkeypatch.setattr("app.services.transcripts.inspect_audio_duration_seconds", lambda **_kwargs: 1.0)
    monkeypatch.setattr("app.services.transcripts.ensure_stt_config_credential_ready", lambda **_kwargs: None)
    monkeypatch.setattr("app.services.transcripts.resolve_selected_team_stt", lambda *_args, **_kwargs: (object(), config, "nova-3", "en"))
    monkeypatch.setattr("app.services.transcripts.write_transcript_ingestion_source_audio", lambda **_kwargs: "secret:synthetic-retry-source")
    monkeypatch.setattr("app.services.transcripts.try_publish_task_dispatch_safely", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.services.vault.write_transcript_ingestion_source_audio", lambda **_kwargs: "secret:synthetic-failed-window")

    audio = _synthetic_pcm_wav(16_000)
    authorization = authorize_stream_batch(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        first_sample_offset=0, sample_count=16_000, pcm_bytes=b"\x00\x00" * 16_000, now=base,
    )
    fail_time = base + timedelta(seconds=1)
    fail_transcript_stream(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        error_code="stream_failed", recovery_audio_bytes=audio, now=fail_time,
    )
    window = db_session.scalar(select(TranscriptStreamWindow).where(
        TranscriptStreamWindow.session_id == started.session_id,
        TranscriptStreamWindow.window_no == authorization.window_no,
    ))
    original_deadline = window.recovery_audio_expires_at
    retry_time = fail_time + timedelta(hours=23)
    job_id = queue_transcript_stream_recovery(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        window_no=authorization.window_no, audio_bytes=audio, now=retry_time,
    )
    job = db_session.get(TranscriptIngestionJob, job_id)
    assert job is not None
    assert job.source_audio_expires_at <= original_deadline
    assert job.source_audio_expires_at > retry_time


def test_transcript_root_delete_detaches_attempt_and_queues_failed_audio_cleanup(
    db_session, make_team, make_user, monkeypatch,
):
    owner, transcript, started, _config = _stream_fixture(db_session, make_team, make_user, monkeypatch)
    audio_ref = "secret:synthetic-stream-recovery-delete"
    monkeypatch.setattr(
        "app.services.vault.write_transcript_ingestion_source_audio",
        lambda **_kwargs: audio_ref,
    )
    authorized = authorize_stream_batch(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        first_sample_offset=0, sample_count=16_000, pcm_bytes=b"\x00\x00" * 16_000,
    )
    fail_transcript_stream(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        error_code="stream_failed", recovery_audio_bytes=_synthetic_pcm_wav(16_000),
    )
    attempt = db_session.scalar(select(ProviderAttempt).where(
        ProviderAttempt.transcript_stream_session_id == started.session_id,
    ))
    assert attempt is not None
    monkeypatch.setattr("app.services.transcripts.process_transcript_audio_cleanup_jobs", lambda *_args, **_kwargs: 0)
    from app.services.transcripts import delete_transcripts
    assert delete_transcripts(db_session, owner, transcript_ids=[transcript.id]) == 1
    db_session.refresh(attempt)
    from app.models import TranscriptAudioCleanupJob
    cleanup = db_session.scalar(select(TranscriptAudioCleanupJob).where(
        TranscriptAudioCleanupJob.secret_ref == audio_ref,
    ))
    assert attempt.transcript_id is None and attempt.transcript_stream_session_id is None
    assert cleanup is not None


def test_failed_window_commit_rollback_durably_queues_orphan_vault_cleanup(
    db_session, make_team, make_user, monkeypatch,
):
    owner, transcript, started, _config = _stream_fixture(db_session, make_team, make_user, monkeypatch)
    authorization = authorize_stream_batch(
        db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
        connection_fence=started.connection_fence, fence_token=started.fence_token,
        first_sample_offset=0, sample_count=16_000, pcm_bytes=b"\x00\x00" * 16_000,
    )
    audio_ref = "secret:synthetic-orphan-stream-audio"
    monkeypatch.setattr("app.services.vault.write_transcript_ingestion_source_audio", lambda **_kwargs: audio_ref)
    original_commit = db_session.commit
    commit_calls = 0

    def fail_once_then_commit():
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 1:
            raise RuntimeError("synthetic database commit failure")
        original_commit()

    monkeypatch.setattr(db_session, "commit", fail_once_then_commit)
    with pytest.raises(RuntimeError):
        fail_transcript_stream(
            db_session, owner.id, transcript_id=transcript.id, session_id=started.session_id,
            connection_fence=started.connection_fence, fence_token=started.fence_token,
            error_code="stream_failed", recovery_audio_bytes=_synthetic_pcm_wav(16_000),
        )
    from app.models import TranscriptAudioCleanupJob
    cleanup = db_session.scalar(select(TranscriptAudioCleanupJob).where(
        TranscriptAudioCleanupJob.secret_ref == audio_ref,
    ))
    window = db_session.scalar(select(TranscriptStreamWindow).where(
        TranscriptStreamWindow.session_id == started.session_id,
        TranscriptStreamWindow.window_no == authorization.window_no,
    ))
    assert cleanup is not None
    assert window is not None and window.recovery_audio_vault_ref is None
