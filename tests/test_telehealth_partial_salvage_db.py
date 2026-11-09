"""Database regressions for explicit Telehealth partial-source acceptance.

All content and provider results in this module are synthetic. These tests
exercise the durable owner decision and the source-version boundary; they do
not make provider requests or alter quota accounting.
"""
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.errors import AppError
from app.models import (
    TelehealthCapturePart,
    TelehealthCaptureSession,
    Transcript,
    TranscriptIngestionJob,
    TranscriptIngestionJobKind,
    TranscriptIngestionJobStatus,
    TranscriptIngestionMode,
    TranscriptStatus,
    TranscriptVersion,
    utcnow,
)
from app.services.content_crypto import decrypt_text_for_owner
from app.services.structured_transcripts import (
    _encrypt_current_turns,
    append_transcript_turns,
    canonical_transcript_projection,
    store_turns_on_version,
    turns_for_version,
)
from app.services.transcripts import _create_transcript_version_from_text
from app.services.telehealth_apply import (
    apply_completed_telehealth_capture,
    use_available_telehealth_capture,
)


def _transcript(db_session, owner):
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=owner.team_id,
        status=TranscriptStatus.failed,
        ingestion_mode=TranscriptIngestionMode.whole_file,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=1),
    )
    db_session.add(transcript)
    db_session.flush()
    return transcript


def _capture(db_session, owner, transcript, *, channels=("clinician", "patient"), failure_code="transcription_failed"):
    session = TelehealthCaptureSession(
        transcript_id=transcript.id,
        owner_user_id=owner.id,
        team_id=owner.team_id,
        timeline_sample_rate=48_000,
        status="failed",
        failure_code=failure_code,
        failure_channel="patient",
        incomplete_acknowledged_at=utcnow(),
    )
    db_session.add(session)
    db_session.flush()
    parts = {}
    jobs = {}
    for sequence, channel in enumerate(channels):
        job = TranscriptIngestionJob(
            transcript_id=transcript.id,
            owner_user_id=owner.id,
            team_id=owner.team_id,
            job_kind=TranscriptIngestionJobKind.audio_file,
            source_filename=f"synthetic-{channel}.wav",
            status=(TranscriptIngestionJobStatus.completed if channel == "clinician"
                    else TranscriptIngestionJobStatus.failed),
            result_text_encrypted=("synthetic-ciphertext" if channel == "clinician" else None),
            source_audio_vault_ref=("synthetic-vault-ref" if channel == "patient" else None),
            source_audio_expires_at=(utcnow() + timedelta(hours=1) if channel == "patient" else None),
        )
        db_session.add(job)
        db_session.flush()
        part = TelehealthCapturePart(
            id=uuid4(), session_id=session.id, transcript_id=transcript.id,
            owner_user_id=owner.id, team_id=owner.team_id, channel=channel,
            sequence_no=sequence, idempotency_key=uuid4(),
            status=("transcribed" if channel == "clinician" else "failed"),
            ingestion_job_id=job.id,
            timeline_start_frame=sequence * 48_000,
            timeline_end_frame=(sequence + 1) * 48_000,
            timeline_sample_rate=48_000, file_sample_rate=16_000,
            file_samples=16_000,
            timing_spans_json=[{
                "file_start_sample": 0, "file_end_sample": 16_000,
                "timeline_start_frame": sequence * 48_000,
                "timeline_end_frame": (sequence + 1) * 48_000,
            }],
            source_audio_size_bytes=3244,
            source_audio_fingerprint=("a" if channel == "clinician" else "b") * 64,
            source_audio_duration_seconds=1.0,
        )
        db_session.add(part)
        parts[channel] = part
        jobs[channel] = job
    db_session.commit()
    return session, parts, jobs


def _stub_mapped_speech(monkeypatch, *, silent=False):
    """Stub only the provider-result boundary; retain real encryption/version logic."""
    if silent:
        monkeypatch.setattr("app.services.telehealth_apply._mapped_turns", lambda *a, **k: ())
    else:
        from app.services.telehealth_merge import TelehealthSourceTurn
        monkeypatch.setattr(
            "app.services.telehealth_apply._mapped_turns",
            lambda _db, *, part, **_kw: (TelehealthSourceTurn(
                part.id, part.channel, part.sequence_no, 0,
                f"Synthetic {part.channel} speech", part.sequence_no * 1000,
                part.sequence_no * 1000 + 300,
            ),),
        )
    monkeypatch.setattr("app.services.transcripts._attempt_preview_redaction", lambda *a, **k: None)
    monkeypatch.setattr("app.services.transcripts._trigger_waiting_generation_dispatches", lambda *a, **k: None)


def _publish_existing_turns(db, transcript, entries):
    turns = []
    for scope_id, speaker_id, text in entries:
        turns = append_transcript_turns(
            db, transcript=transcript, scope_id=scope_id,
            turns=[{"speaker_id": speaker_id, "text": text}],
        )
    _encrypt_current_turns(db, transcript=transcript, turns=turns)
    projection = canonical_transcript_projection(turns)
    version = _create_transcript_version_from_text(
        db, transcript=transcript, owner_user_id=transcript.owner_user_id, plaintext=projection,
    )
    store_turns_on_version(db, version=version, owner_user_id=transcript.owner_user_id, turns=turns)
    db.commit()
    return turns, version


def _versions(db, transcript):
    return list(db.scalars(select(TranscriptVersion).where(
        TranscriptVersion.transcript_id == transcript.id,
    ).order_by(TranscriptVersion.version_no)))


def test_owner_acceptance_applies_successful_sibling_and_commits_partial_atomically(
    db_session, make_team, make_user, monkeypatch,
):
    team = make_team(name="Partial acceptance")
    owner = make_user(email="partial-accept@example.com", password="password-1", team=team)
    transcript = _transcript(db_session, owner)
    session, parts, jobs = _capture(db_session, owner, transcript)
    _stub_mapped_speech(monkeypatch)

    accepted = use_available_telehealth_capture(
        db_session, owner, transcript_id=transcript.id, capture_session_id=session.id,
    )

    assert accepted.status == "partial"
    assert accepted.incomplete_acknowledged_at is not None
    assert transcript.status is TranscriptStatus.ready
    assert transcript.status is TranscriptStatus.ready
    assert jobs["clinician"].status is TranscriptIngestionJobStatus.applied
    assert jobs["clinician"].applied_at is not None
    assert jobs["patient"].status is TranscriptIngestionJobStatus.failed
    assert parts["patient"].status == "failed"
    versions = _versions(db_session, transcript)
    assert len(versions) == 1
    assert "Synthetic clinician speech" in decrypt_text_for_owner(
        db_session, owner_user_id=owner.id, table="transcript_versions", field="text_encrypted",
        record_id=versions[0].id, stored_value=versions[0].text_encrypted,
    )
    saved_turns = turns_for_version(db_session, version=versions[0])
    warning = next(turn for turn in saved_turns if turn.get("source_warning"))
    assert "INCOMPLETE TELEHEALTH RECORDING" in warning["text"]
    assert "patient 1000-2000ms" in warning["text"]
    saved_turns = turns_for_version(db_session, version=versions[0])
    warning = next(turn for turn in saved_turns if turn.get("source_warning"))
    assert "INCOMPLETE TELEHEALTH RECORDING" in warning["text"]
    assert "patient 1000-2000ms" in warning["text"]
    # Replay acknowledges the same durable decision and never publishes twice.
    replay = use_available_telehealth_capture(
        db_session, owner, transcript_id=transcript.id, capture_session_id=session.id,
    )
    assert replay.id == session.id
    assert len(_versions(db_session, transcript)) == 1


def test_partial_retry_recovery_adds_new_version_once_and_keeps_prior_edits_and_deletions(
    db_session, make_team, make_user, monkeypatch,
):
    team = make_team(name="Partial recovery")
    owner = make_user(email="partial-recovery@example.com", password="password-1", team=team)
    transcript = _transcript(db_session, owner)
    session, parts, jobs = _capture(db_session, owner, transcript)
    original, _ = _publish_existing_turns(db_session, transcript, [
        (uuid4(), "clinician", "Original phrase before clinician edit"),
        (uuid4(), "patient", "Earlier saved answer"),
        (uuid4(), "patient", "Deleted earlier phrase"),
    ])
    edited = [dict(turn) for turn in original[:2]]
    edited[0]["text"] = "Clinician-edited phrase"
    _encrypt_current_turns(db_session, transcript=transcript, turns=edited)
    edited_version = _create_transcript_version_from_text(
        db_session, transcript=transcript, owner_user_id=owner.id,
        plaintext=canonical_transcript_projection(edited),
    )
    store_turns_on_version(db_session, version=edited_version, owner_user_id=owner.id, turns=edited)
    db_session.commit()
    _stub_mapped_speech(monkeypatch)
    use_available_telehealth_capture(
        db_session, owner, transcript_id=transcript.id, capture_session_id=session.id,
    )
    first_count = len(_versions(db_session, transcript))

    jobs["patient"].status = TranscriptIngestionJobStatus.completed
    jobs["patient"].result_text_encrypted = "synthetic-recovered-ciphertext"
    parts["patient"].status = "transcribed"
    db_session.commit()
    assert apply_completed_telehealth_capture(db_session, capture_session_id=session.id) is True
    recovery_count = len(_versions(db_session, transcript))
    assert recovery_count == first_count + 1
    versions = _versions(db_session, transcript)
    recovered = versions[-1]
    recovered_text = decrypt_text_for_owner(
        db_session, owner_user_id=owner.id, table="transcript_versions", field="text_encrypted",
        record_id=recovered.id, stored_value=recovered.text_encrypted,
    )
    assert "Clinician-edited phrase" in recovered_text
    assert "Earlier saved answer" in recovered_text
    assert "Deleted earlier phrase" not in recovered_text
    assert "Synthetic patient speech" in recovered_text
    recovered_turns = turns_for_version(db_session, version=recovered)
    assert {turn["id"] for turn in recovered_turns if turn["text"] in {
        "Clinician-edited phrase", "Earlier saved answer",
    }} == {turn["id"] for turn in edited}
    # Provider duplicate/reconciliation delivery is idempotent.
    assert apply_completed_telehealth_capture(db_session, capture_session_id=session.id) is True
    assert len(_versions(db_session, transcript)) == recovery_count
    assert jobs["clinician"].status is TranscriptIngestionJobStatus.applied
    assert jobs["patient"].status is TranscriptIngestionJobStatus.applied


def test_silent_accepted_capture_does_not_create_clinical_text_or_fake_readiness(
    db_session, make_team, make_user, monkeypatch,
):
    team = make_team(name="Silent partial")
    owner = make_user(email="partial-silent@example.com", password="password-1", team=team)
    transcript = _transcript(db_session, owner)
    session, parts, jobs = _capture(
        db_session, owner, transcript, channels=("clinician",), failure_code="source_ended",
    )
    _publish_existing_turns(
        db_session, transcript, [(uuid4(), None, "Previously saved synthetic clinical text")],
    )
    _stub_mapped_speech(monkeypatch, silent=True)
    accepted = use_available_telehealth_capture(
        db_session, owner, transcript_id=transcript.id, capture_session_id=session.id,
    )
    assert accepted.status == "partial"
    assert accepted.failure_code == "source_ended"
    versions = _versions(db_session, transcript)
    assert len(versions) == 2
    source = decrypt_text_for_owner(
        db_session, owner_user_id=owner.id, table="transcript_versions", field="text_encrypted",
        record_id=versions[-1].id, stored_value=versions[-1].text_encrypted,
    )
    assert "Previously saved synthetic clinical text" in source
    assert "INCOMPLETE TELEHEALTH RECORDING" in source
    assert "uncaptured interval" in source
    assert "Synthetic clinician speech" not in source
    from app.services.structured_transcripts import edit_transcript_turn, delete_transcript_turn
    warning_turn = next(turn for turn in turns_for_version(db_session, version=versions[-1]) if turn.get("source_warning"))
    with pytest.raises(AppError, match="source warnings cannot be edited"):
        edit_transcript_turn(
            db_session, owner, transcript_id=transcript.id,
            turn_id=warning_turn["id"], expected_version_id=versions[-1].id,
            text="Synthetic edited warning",
        )
    with pytest.raises(AppError, match="source warnings cannot be deleted"):
        delete_transcript_turn(
            db_session, owner, transcript_id=transcript.id,
            turn_id=warning_turn["id"], expected_version_id=versions[-1].id,
        )
    assert jobs["clinician"].status is TranscriptIngestionJobStatus.applied
    assert transcript.status is TranscriptStatus.ready
    assert parts["clinician"].status == "transcribed"


def test_all_silent_acceptance_is_rootready_but_warning_alone_cannot_be_snapshotted(
    db_session, make_team, make_user, monkeypatch,
):
    from app.services.transcripts import snapshot_current_transcript_version

    team = make_team(name="Silent no result")
    owner = make_user(email="partial-empty@example.com", password="password-1", team=team)
    transcript = _transcript(db_session, owner)
    session, _parts, jobs = _capture(
        db_session, owner, transcript, channels=("clinician",), failure_code="source_ended",
    )
    _stub_mapped_speech(monkeypatch, silent=True)
    accepted = use_available_telehealth_capture(
        db_session, owner, transcript_id=transcript.id, capture_session_id=session.id,
    )
    assert accepted.status == "partial"
    assert transcript.status is TranscriptStatus.ready
    assert jobs["clinician"].status is TranscriptIngestionJobStatus.applied
    assert _versions(db_session, transcript) == []
    with pytest.raises(AppError):
        snapshot_current_transcript_version(db_session, transcript=transcript)


def test_failed_unapplied_sources_accept_as_partial_and_empty_root_becomes_ready(
    db_session, make_team, make_user,
):
    from app.services.transcripts import snapshot_current_transcript_version

    team = make_team(name="Failed partial source")
    owner = make_user(email="partial-failed-only@example.com", password="password-1", team=team)
    transcript = _transcript(db_session, owner)
    session, parts, jobs = _capture(db_session, owner, transcript)
    for channel in ("clinician", "patient"):
        jobs[channel].status = TranscriptIngestionJobStatus.failed
        jobs[channel].result_text_encrypted = None
        jobs[channel].source_audio_vault_ref = f"synthetic-vault-ref-{channel}"
        jobs[channel].source_audio_expires_at = utcnow() + timedelta(hours=1)
        parts[channel].status = "failed"
    db_session.commit()
    patient_ref = jobs["patient"].source_audio_vault_ref

    accepted = use_available_telehealth_capture(
        db_session, owner, transcript_id=transcript.id, capture_session_id=session.id,
    )

    assert accepted.status == "partial"
    assert transcript.status is TranscriptStatus.ready
    assert transcript.current_draft_turns_encrypted is not None
    assert jobs["patient"].status is TranscriptIngestionJobStatus.failed
    assert jobs["patient"].source_audio_vault_ref == patient_ref
    assert _versions(db_session, transcript) == []
    with pytest.raises(AppError):
        snapshot_current_transcript_version(db_session, transcript=transcript)


def test_recovery_defers_behind_another_writer_then_reconciles_without_provider_work(
    db_session, make_team, make_user, monkeypatch,
):
    from app.services.telehealth_api import telehealth_capture_detail

    team = make_team(name="Deferred partial recovery")
    owner = make_user(email="partial-deferred@example.com", password="password-1", team=team)
    transcript = _transcript(db_session, owner)
    session, parts, jobs = _capture(db_session, owner, transcript)
    _stub_mapped_speech(monkeypatch)
    writer = TelehealthCaptureSession(
        transcript_id=transcript.id, owner_user_id=owner.id, team_id=owner.team_id,
        timeline_sample_rate=48_000, status="processing",
    )
    db_session.add(writer)
    db_session.commit()

    accepted = use_available_telehealth_capture(
        db_session, owner, transcript_id=transcript.id, capture_session_id=session.id,
    )
    assert accepted.status == "partial"
    db_session.refresh(jobs["clinician"])
    assert jobs["clinician"].status is TranscriptIngestionJobStatus.completed
    assert jobs["clinician"].applied_at is None
    deferred_detail = telehealth_capture_detail(
        db_session, owner, transcript_id=transcript.id, capture_session_id=session.id,
    )
    assert next(part for part in deferred_detail.parts if part.id == parts["clinician"].id).included_in_transcript is False
    assert _versions(db_session, transcript) == []

    writer.status = "completed"
    db_session.commit()
    assert apply_completed_telehealth_capture(db_session, capture_session_id=session.id) is True
    assert jobs["clinician"].status is TranscriptIngestionJobStatus.applied
    assert len(_versions(db_session, transcript)) == 1
    assert parts["clinician"].status == "transcribed"
    applied_detail = telehealth_capture_detail(
        db_session, owner, transcript_id=transcript.id, capture_session_id=session.id,
    )
    assert next(part for part in applied_detail.parts if part.id == parts["clinician"].id).included_in_transcript is True


def test_generation_wait_ignores_only_accepted_partial_jobs(
    db_session, make_team, make_user,
):
    from app.services.templates import _has_generation_wait_ingestion_jobs

    team = make_team(name="Partial generation wait")
    owner = make_user(email="partial-wait@example.com", password="password-1", team=team)
    transcript = _transcript(db_session, owner)
    session, _parts, jobs = _capture(db_session, owner, transcript)
    session.status = "partial"
    # Completed work in an accepted partial remains retryable/reconcilable,
    # but it must not hold every generation request for the root.
    jobs["patient"].status = TranscriptIngestionJobStatus.completed
    jobs["patient"].result_text_encrypted = "synthetic-recovered-ciphertext"
    db_session.commit()
    upper_bound = utcnow() + timedelta(seconds=1)
    assert _has_generation_wait_ingestion_jobs(
        db_session, transcript_id=transcript.id, created_at_upper_bound=upper_bound,
    ) is False

    ordinary = TranscriptIngestionJob(
        transcript_id=transcript.id, owner_user_id=owner.id, team_id=owner.team_id,
        job_kind=TranscriptIngestionJobKind.audio_file,
        source_filename="synthetic-unrelated.wav",
        status=TranscriptIngestionJobStatus.completed,
        result_text_encrypted="synthetic-unrelated-pending-result",
    )
    db_session.add(ordinary)
    db_session.commit()
    assert _has_generation_wait_ingestion_jobs(
        db_session, transcript_id=transcript.id, created_at_upper_bound=upper_bound,
    ) is True


def test_job_failure_keeps_ordinary_root_failed_but_accepted_partial_nonblocking(
    db_session, make_team, make_user,
):
    from app.services.transcripts import _mark_job_failed

    team = make_team(name="Partial failure status")
    owner = make_user(email="partial-fail-status@example.com", password="password-1", team=team)
    ordinary_transcript = _transcript(db_session, owner)
    ordinary = TranscriptIngestionJob(
        transcript_id=ordinary_transcript.id, owner_user_id=owner.id, team_id=owner.team_id,
        job_kind=TranscriptIngestionJobKind.audio_file, source_filename="synthetic-ordinary.wav",
        status=TranscriptIngestionJobStatus.processing,
    )
    db_session.add(ordinary)
    db_session.commit()
    _mark_job_failed(
        db_session, ordinary_transcript, ordinary,
        code="synthetic_failure", message="Synthetic failure",
    )
    assert ordinary_transcript.status is TranscriptStatus.failed

    partial_transcript = _transcript(db_session, owner)
    session, _parts, jobs = _capture(db_session, owner, partial_transcript)
    session.status = "partial"
    partial_transcript.status = TranscriptStatus.ready
    jobs["patient"].source_filename = "synthetic-patient.wav"
    db_session.commit()
    _mark_job_failed(
        db_session, partial_transcript, jobs["patient"],
        code="synthetic_retry_failure", message="Synthetic retry failure",
    )
    assert partial_transcript.status is TranscriptStatus.ready


def test_expiring_accepted_missing_audio_keeps_partial_nonblocking_but_other_capture_blocks(
    db_session, make_team, make_user, monkeypatch,
):
    from app.services.transcripts import _resolved_transcript_status, expire_ingestion_source_audio

    team = make_team(name="Accepted partial expiry")
    owner = make_user(email="partial-expiry@example.com", password="password-1", team=team)
    transcript = _transcript(db_session, owner)
    session, _parts, jobs = _capture(db_session, owner, transcript)
    monkeypatch.setattr("app.services.telehealth_apply._mapped_turns", lambda *a, **k: ())
    use_available_telehealth_capture(
        db_session, owner, transcript_id=transcript.id, capture_session_id=session.id,
    )
    now = utcnow()
    jobs["patient"].source_audio_expires_at = now - timedelta(seconds=1)
    db_session.commit()
    monkeypatch.setattr(
        "app.services.transcripts.process_transcript_audio_cleanup_jobs", lambda *a, **k: None,
    )
    assert expire_ingestion_source_audio(db_session, now=now) == 1
    db_session.refresh(jobs["patient"])
    db_session.refresh(transcript)
    db_session.refresh(session)
    assert jobs["patient"].source_audio_expired_at is not None
    assert jobs["patient"].source_audio_vault_ref is None
    assert session.status == "partial"
    assert transcript.status is TranscriptStatus.ready

    unrelated = TelehealthCaptureSession(
        transcript_id=transcript.id, owner_user_id=owner.id, team_id=owner.team_id,
        timeline_sample_rate=48_000, status="processing",
    )
    db_session.add(unrelated)
    db_session.commit()
    assert _resolved_transcript_status(db_session, transcript=transcript) is TranscriptStatus.transcribing


def test_use_available_api_is_feature_gated_csrf_protected_and_owner_only(
    raw_client, db_session, make_team, make_user, monkeypatch,
):
    team = make_team(name="Partial API boundary")
    owner = make_user(email="partial-api-owner@example.com", password="password-1", team=team)
    admin = make_user(email="partial-api-admin@example.com", password="password-1", is_system_admin=True)
    transcript = _transcript(db_session, owner)
    session, _parts, _jobs = _capture(db_session, owner, transcript)
    _stub_mapped_speech(monkeypatch)
    url = f"/api/v1/transcripts/{transcript.id}/telehealth/captures/{session.id}/use-available"

    assert raw_client.post("/api/v1/auth/login", json={
        "email": owner.email, "password": "password-1",
    }).status_code == 200
    from app.services.csrf import CSRF_COOKIE_NAME
    csrf = raw_client.cookies.get(CSRF_COOKIE_NAME, "")
    monkeypatch.setenv("TELEHEALTH_API_ENABLED", "false")
    assert raw_client.post(url, headers={
        "Origin": "http://testserver", "X-CSRF-Token": csrf,
    }).status_code == 404

    monkeypatch.setenv("TELEHEALTH_API_ENABLED", "true")
    assert raw_client.post(url).status_code == 403
    assert raw_client.post(url, headers={
        "Origin": "http://testserver", "X-CSRF-Token": "invalid",
    }).status_code == 403
    success = raw_client.post(url, headers={
        "Origin": "http://testserver", "X-CSRF-Token": csrf,
    })
    assert success.status_code == 200, success.text
    assert success.json()["status"] == "partial"

    # A system administrator has no transcript-content authority.
    raw_client.post("/api/v1/auth/logout", headers={
        "Origin": "http://testserver", "X-CSRF-Token": csrf,
    })
    assert raw_client.post("/api/v1/auth/login", json={
        "email": admin.email, "password": "password-1",
    }).status_code == 200
    admin_csrf = raw_client.cookies.get(CSRF_COOKIE_NAME, "")
    denied = raw_client.post(url, headers={
        "Origin": "http://testserver", "X-CSRF-Token": admin_csrf,
    })
    assert denied.status_code == 404


def test_acceptance_rejects_nonfailed_capture_and_expired_root(
    db_session, make_team, make_user,
):
    team = make_team(name="Partial guards")
    owner = make_user(email="partial-guards@example.com", password="password-1", team=team)
    transcript = _transcript(db_session, owner)
    session, _parts, _jobs = _capture(db_session, owner, transcript)
    session.status = "processing"
    db_session.commit()
    with pytest.raises(AppError) as rejected:
        use_available_telehealth_capture(
            db_session, owner, transcript_id=transcript.id, capture_session_id=session.id,
        )
    assert rejected.value.status_code == 409

    session.status = "failed"
    transcript.retention_expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()
    with pytest.raises(AppError) as expired:
        use_available_telehealth_capture(
            db_session, owner, transcript_id=transcript.id, capture_session_id=session.id,
        )
    assert expired.value.status_code == 404


def test_partial_source_listing_is_oldest_first_paginated_and_owner_filtered(
    db_session, make_team, make_user,
):
    from app.services.telehealth_api import partial_telehealth_capture_details

    team = make_team(name="Partial listing")
    owner = make_user(email="partial-list@example.com", password="password-1", team=team)
    transcript = _transcript(db_session, owner)
    captures = []
    for _ in range(3):
        capture, _parts, _jobs = _capture(db_session, owner, transcript)
        capture.status = "partial"
        db_session.commit()
        captures.append(capture)

    page = partial_telehealth_capture_details(
        db_session, owner, transcript_id=transcript.id, offset=0, limit=2,
    )
    next_page = partial_telehealth_capture_details(
        db_session, owner, transcript_id=transcript.id, offset=2, limit=2,
    )
    assert [item.capture_session_id for item in page.items] == [row.id for row in captures[:2]]
    assert page.next_offset == 2
    assert [item.capture_session_id for item in next_page.items] == [captures[2].id]
    assert next_page.next_offset is None
