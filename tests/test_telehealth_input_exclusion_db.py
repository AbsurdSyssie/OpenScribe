"""Telehealth and ordinary-upload admission barriers (synthetic DB tests)."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.errors import AppError
from app.models import (
    ProviderAttempt,
    TaskDispatchOutbox,
    TelehealthCaptureSession,
    Transcript,
    TranscriptIngestionJob,
    TranscriptIngestionJobKind,
    TranscriptIngestionMode,
    TranscriptStatus,
    User,
    utcnow,
)
from app.services import telehealth_capture, transcripts


SYNTHETIC_AUDIO = b"synthetic telehealth exclusion audio"
UNRESOLVED_CAPTURE_STATES = (
    "active", "stopping", "ready", "interrupted", "processing", "failed",
)
TERMINAL_CAPTURE_STATES = ("completed", "discarded")


def _root(db, owner, *, expires_at=None):
    root = Transcript(
        owner_user_id=owner.id,
        team_id=owner.team_id,
        title="Synthetic input exclusion",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=expires_at or utcnow() + timedelta(days=30),
    )
    db.add(root)
    db.commit()
    db.refresh(root)
    return root


def _capture(db, owner, root, *, status):
    capture = TelehealthCaptureSession(
        transcript_id=root.id,
        owner_user_id=owner.id,
        team_id=owner.team_id,
        status=status,
        timeline_sample_rate=48000,
    )
    db.add(capture)
    db.commit()
    db.refresh(capture)
    return capture


def _fake_stt_config():
    return SimpleNamespace(
        id=uuid4(), provider_preset="custom_openai_compatible",
        adapter_kind=SimpleNamespace(value="openai_compatible_rest"),
        base_url="https://stt.example.test", transcribe_path="/v1/transcribe",
        model_field_name="model", model_name="synthetic-model",
        language_field_name="language", language="en", file_field_name="file",
        response_text_path="text", segments_path="segments",
        segment_text_field="text", segment_start_field="start",
        segment_end_field="end", segment_speaker_field="speaker",
        extra_form_fields_json={}, diarization_batch_enabled=False,
        diarization_field_name=None, diarization_on_value="true",
        diarization_off_value="false",
    )


def _patch_upload_admission(monkeypatch, *, hooks=None):
    """Make a valid synthetic upload and expose all post-barrier effects."""
    effects = hooks if hooks is not None else {name: [] for name in (
        "provider", "credential", "duration", "quota", "vault", "attempt",
        "publish", "audit",
    )}
    config = _fake_stt_config()
    monkeypatch.setattr(
        transcripts, "resolve_selected_team_stt",
        lambda *args, **kwargs: (effects["provider"].append(True), config, "synthetic-model", "en"),
    )
    monkeypatch.setattr(
        transcripts, "ensure_stt_config_credential_ready",
        lambda **kwargs: effects["credential"].append(True),
    )
    monkeypatch.setattr(
        transcripts, "inspect_audio_duration_seconds",
        lambda **kwargs: (effects["duration"].append(True), 1.25)[1],
    )
    monkeypatch.setattr(
        transcripts, "_enforce_whole_file_hourly_usage_budget",
        lambda *args, **kwargs: effects["quota"].append(True),
    )
    monkeypatch.setattr(
        transcripts, "write_transcript_ingestion_source_audio",
        lambda **kwargs: (effects["vault"].append(True), "synthetic-vault-reference")[1],
    )

    def stage_attempt(_db, *, job, **_kwargs):
        effects["attempt"].append(job.id)
        job.celery_task_id = str(uuid4())

    monkeypatch.setattr(transcripts, "_queue_ingestion_attempt_and_dispatch", stage_attempt)
    monkeypatch.setattr(
        transcripts, "try_publish_task_dispatch_safely",
        lambda task_id: effects["publish"].append(task_id),
    )
    monkeypatch.setattr(transcripts, "record_security_event", lambda *a, **k: effects["audit"].append(True))
    monkeypatch.setattr(transcripts, "diarization_preference_enabled", lambda *a, **k: False)
    monkeypatch.setattr(transcripts, "stt_config_supports_diarization", lambda *a, **k: False)
    return effects, config


@pytest.mark.parametrize("capture_status", UNRESOLVED_CAPTURE_STATES)
def test_new_audio_upload_is_rejected_before_any_provider_or_durable_side_effect(
    db_session, make_team, make_user, monkeypatch, capture_status,
):
    team = make_team(name=f"Input exclusion {capture_status}")
    owner = make_user(email=f"input-exclusion-{capture_status}@example.test", team=team)
    root = _root(db_session, owner)
    _capture(db_session, owner, root, status=capture_status)
    effects, _config = _patch_upload_admission(monkeypatch)

    with pytest.raises(AppError) as error:
        transcripts.queue_audio_file_ingestion(
            db_session, owner, transcript_id=root.id, filename="synthetic.wav",
            source_audio_blob=SYNTHETIC_AUDIO,
        )

    assert error.value.status_code == 409
    assert error.value.code == "telehealth_capture_unresolved"
    assert all(not called for called in effects.values())
    assert db_session.scalar(select(func.count(TranscriptIngestionJob.id)).where(
        TranscriptIngestionJob.transcript_id == root.id,
    )) == 0
    assert db_session.scalar(select(func.count(ProviderAttempt.id)).where(
        ProviderAttempt.transcript_id == root.id,
    )) == 0
    assert db_session.scalar(select(func.count(TaskDispatchOutbox.task_id))) == 0


@pytest.mark.parametrize("capture_status", TERMINAL_CAPTURE_STATES)
def test_terminal_telehealth_capture_allows_ordinary_audio_upload(
    db_session, make_team, make_user, monkeypatch, capture_status,
):
    team = make_team(name=f"Input release {capture_status}")
    owner = make_user(email=f"input-release-{capture_status}@example.test", team=team)
    root = _root(db_session, owner)
    _capture(db_session, owner, root, status=capture_status)
    effects, _config = _patch_upload_admission(monkeypatch)

    _transcript, job = transcripts.queue_audio_file_ingestion(
        db_session, owner, transcript_id=root.id, filename="synthetic.wav",
        source_audio_blob=SYNTHETIC_AUDIO,
    )

    assert job.job_kind is TranscriptIngestionJobKind.audio_file
    assert job.status.value == "queued"
    assert job.source_audio_vault_ref == "synthetic-vault-reference"
    assert all(len(effects[key]) == 1 for key in (
        "provider", "credential", "duration", "quota", "vault", "attempt", "publish", "audit",
    ))


def test_exact_idempotency_replay_returns_admitted_job_during_unresolved_capture(
    db_session, make_team, make_user, monkeypatch,
):
    team = make_team(name="Input replay exclusion")
    owner = make_user(email="input-replay@example.test", team=team)
    root = _root(db_session, owner)
    _capture(db_session, owner, root, status="processing")
    request_key = "same-upload-request"
    admitted = TranscriptIngestionJob(
        transcript_id=root.id, owner_user_id=owner.id, team_id=owner.team_id,
        job_kind=TranscriptIngestionJobKind.audio_file, source_filename="synthetic.wav",
        request_idempotency_key=request_key,
        source_audio_size_bytes=len(SYNTHETIC_AUDIO),
    )
    db_session.add(admitted)
    db_session.commit()
    effects, _config = _patch_upload_admission(monkeypatch)

    _transcript, replay = transcripts.queue_audio_file_ingestion(
        db_session, owner, transcript_id=root.id, filename="synthetic.wav",
        source_audio_blob=SYNTHETIC_AUDIO, request_idempotency_key=request_key,
    )

    assert replay.id == admitted.id
    assert all(not called for called in effects.values())
    assert db_session.scalar(select(func.count(TranscriptIngestionJob.id)).where(
        TranscriptIngestionJob.transcript_id == root.id,
    )) == 1
    assert db_session.scalar(select(func.count(ProviderAttempt.id)).where(
        ProviderAttempt.transcript_id == root.id,
    )) == 0
    assert db_session.scalar(select(func.count(TaskDispatchOutbox.task_id))) == 0


@pytest.mark.parametrize("capture_status", UNRESOLVED_CAPTURE_STATES)
def test_input_mode_switch_is_blocked_until_capture_is_terminal(
    db_session, make_team, make_user, capture_status,
):
    team = make_team(name=f"Input mode barrier {capture_status}")
    owner = make_user(email=f"input-mode-{capture_status}@example.test", team=team)
    root = _root(db_session, owner)
    _capture(db_session, owner, root, status=capture_status)

    with pytest.raises(AppError) as error:
        transcripts.update_transcript(
            db_session, owner, transcript_id=root.id, title=None,
            ingestion_mode=TranscriptIngestionMode.live_chunked,
            structured_context_json=None,
        )

    assert error.value.status_code == 409
    assert error.value.code == "business_rule_violation"
    assert error.value.message == "Resolve the telehealth capture before switching input mode"


@pytest.mark.parametrize("capture_status", TERMINAL_CAPTURE_STATES)
def test_input_mode_switch_is_allowed_after_terminal_capture(
    db_session, make_team, make_user, capture_status,
):
    team = make_team(name=f"Input mode release {capture_status}")
    owner = make_user(email=f"input-mode-release-{capture_status}@example.test", team=team)
    root = _root(db_session, owner)
    _capture(db_session, owner, root, status=capture_status)

    updated = transcripts.update_transcript(
        db_session, owner, transcript_id=root.id, title=None,
        ingestion_mode=TranscriptIngestionMode.live_chunked,
        structured_context_json=None,
    )

    assert updated.ingestion_mode is TranscriptIngestionMode.live_chunked


def test_upload_preserves_owner_authorization_and_expired_root_rejection(
    db_session, make_team, make_user, monkeypatch,
):
    team = make_team(name="Input ownership and expiry")
    owner = make_user(email="input-owner@example.test", team=team)
    stranger = make_user(email="input-stranger@example.test", team=team)
    root = _root(db_session, owner)
    effects, _config = _patch_upload_admission(monkeypatch)

    with pytest.raises(AppError) as forbidden:
        transcripts.queue_audio_file_ingestion(
            db_session, stranger, transcript_id=root.id, filename="synthetic.wav",
            source_audio_blob=SYNTHETIC_AUDIO,
        )
    assert forbidden.value.status_code == 403
    assert forbidden.value.code == "forbidden"

    db_session.rollback()
    root.retention_expires_at = utcnow() - timedelta(seconds=1)
    db_session.add(root)
    db_session.commit()
    with pytest.raises(AppError) as expired:
        transcripts.queue_audio_file_ingestion(
            db_session, owner, transcript_id=root.id, filename="synthetic.wav",
            source_audio_blob=SYNTHETIC_AUDIO,
        )
    assert expired.value.status_code == 404
    assert expired.value.code == "not_found"
    assert all(not called for called in effects.values())


@pytest.mark.real_db_connections
def test_concurrent_same_key_upload_admission_creates_one_job_and_one_attempt(
    db_session, make_team, make_user, monkeypatch,
):
    team = make_team(name="Concurrent input admission")
    owner = make_user(email="concurrent-input@example.test", team=team)
    root = _root(db_session, owner)
    effects, _config = _patch_upload_admission(monkeypatch)
    owner_id, root_id = owner.id, root.id
    session_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, future=True)
    start = Event()

    def submit():
        assert start.wait(5)
        with session_factory() as session:
            actor = session.get(User, owner_id)
            _transcript, job = transcripts.queue_audio_file_ingestion(
                session, actor, transcript_id=root_id, filename="synthetic.wav",
                source_audio_blob=SYNTHETIC_AUDIO,
                request_idempotency_key="concurrent-same-key",
            )
            return job.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(submit) for _ in range(2)]
        start.set()
        job_ids = [future.result(timeout=10) for future in futures]

    assert job_ids[0] == job_ids[1]
    assert len(effects["vault"]) == 1
    assert len(effects["attempt"]) == 1
    assert len(effects["publish"]) == 1
    assert db_session.scalar(select(func.count(TranscriptIngestionJob.id)).where(
        TranscriptIngestionJob.transcript_id == root_id,
    )) == 1


@pytest.mark.real_db_connections
def test_upload_waiting_on_root_lock_rechecks_capture_started_first(
    db_session, make_team, make_user, monkeypatch,
):
    team = make_team(name="Concurrent capture and upload")
    owner = make_user(email="capture-upload-race@example.test", team=team)
    root = _root(db_session, owner)
    effects, config = _patch_upload_admission(monkeypatch)
    owner_id, root_id = owner.id, root.id
    session_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, future=True)
    capture_has_root_lock, upload_at_lock, release_capture = Event(), Event(), Event()
    original_scope_lock = transcripts.lock_consultation_split_source_scope

    def observe_upload_lock(*args, **kwargs):
        upload_at_lock.set()
        return original_scope_lock(*args, **kwargs)

    monkeypatch.setattr(transcripts, "lock_consultation_split_source_scope", observe_upload_lock)

    def resolve_capture_provider(*_args, **_kwargs):
        capture_has_root_lock.set()
        assert release_capture.wait(5)
        return None, config, "synthetic-model", "en"

    monkeypatch.setattr(telehealth_capture, "resolve_selected_team_stt", resolve_capture_provider)
    monkeypatch.setattr(telehealth_capture, "ensure_stt_config_credential_ready", lambda **_kwargs: None)

    def start_capture():
        with session_factory() as session:
            actor = session.get(User, owner_id)
            return telehealth_capture.begin_telehealth_capture(
                session, actor, transcript_id=root_id, timeline_sample_rate=48000,
            ).id

    def upload():
        with session_factory() as session:
            actor = session.get(User, owner_id)
            try:
                transcripts.queue_audio_file_ingestion(
                    session, actor, transcript_id=root_id, filename="synthetic.wav",
                    source_audio_blob=SYNTHETIC_AUDIO,
                )
            except AppError as exc:
                return exc.code
            return "accepted"

    with ThreadPoolExecutor(max_workers=2) as executor:
        capture_future = executor.submit(start_capture)
        try:
            assert capture_has_root_lock.wait(5)
            upload_future = executor.submit(upload)
            assert upload_at_lock.wait(5)
            # The upload reached the shared root lock while capture still holds it.
            assert not upload_future.done()
        finally:
            release_capture.set()
        capture_id = capture_future.result(timeout=10)
        upload_result = upload_future.result(timeout=10)

    assert capture_id is not None
    assert upload_result == "telehealth_capture_unresolved"
    assert not effects["provider"]
    assert not effects["credential"]
    assert not effects["duration"]
    assert not effects["quota"]
    assert not effects["vault"]
    assert not effects["attempt"]
    assert db_session.scalar(select(func.count(TranscriptIngestionJob.id)).where(
        TranscriptIngestionJob.transcript_id == root_id,
    )) == 0
    assert db_session.scalar(select(func.count(TelehealthCaptureSession.id)).where(
        TelehealthCaptureSession.transcript_id == root_id,
        TelehealthCaptureSession.status == "active",
    )) == 1
