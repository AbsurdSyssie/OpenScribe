"""Privacy-safe Telehealth browser diagnostic sink and schema tests."""
import json
import stat
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.telehealth import TelehealthBrowserDiagnosticsBatch
from app.services import telehealth_diagnostics as diagnostics


def _batch(**overrides):
    value = {
        "diagnostic_id": "0123456789abcdef0123456789abcdef",
        "events": [{"event": "picker_selected", "surface": "window", "channel": "patient"}],
    }
    value.update(overrides)
    return TelehealthBrowserDiagnosticsBatch.model_validate(value)


def test_audio_scope_diagnostics_are_closed_and_content_free():
    safe = diagnostics.safe_event({
        "event": "picker_selected", "surface": "window", "audio_scope": "system",
    })
    assert safe == {"event": "picker_selected", "surface": "window", "audio_scope": "system"}
    assert diagnostics.safe_event({
        "event": "picker_selected", "surface": "window", "audio_scope": "untrusted",
    }) is None
    assert diagnostics.safe_event({
        "event": "picker_selected", "audio_scope": "system", "window_title": "sensitive",
    }) is None


def test_closed_schema_refuses_sensitive_data_and_unbounded_payloads():
    batch = _batch()
    assert len(batch.events) == 1
    for invalid in [
        {"event": "picker_selected", "window_title": "PATIENT PRIVATE TITLE"},
        {"event": "capture_failed", "code": "Patient's real name"},
        {"event": "upload_started", "part": -1},
        {"event": "upload_started", "part": 1000001},
        {"event": "picker_selected", "surface": "https://sensitive.example"},
        {"event": "picker_selected", "part": True},
    ]:
        with pytest.raises(ValidationError):
            _batch(events=[invalid])
    with pytest.raises(ValidationError):
        _batch(diagnostic_id="PATIENT-NAME")
    with pytest.raises(ValidationError):
        _batch(events=[{"event": "picker_requested"}] * 21)
    with pytest.raises(ValidationError):
        _batch(events=[])
    assert diagnostics.safe_event({"event": [], "surface": "window"}) is None
    assert diagnostics.safe_event({"event": "picker_selected", "surface": []}) is None


def test_secure_rotating_file_sink_logs_only_metadata_without_terminal_output(tmp_path, capsys):
    diagnostics.reset_for_tests()
    path = tmp_path / "logs" / "telehealth-capture.jsonl"
    assert diagnostics.configure_file_sink(str(path))
    parsed = _batch()
    events = [event.model_dump(exclude_none=True) for event in parsed.events]
    assert diagnostics.emit_many(parsed.diagnostic_id, events) == 1
    assert diagnostics.emit_many(parsed.diagnostic_id, [
        {"event": "picker_selected", "window_title": "PATIENT PRIVATE TITLE"}
    ]) == 0
    assert capsys.readouterr() == ("", "")
    line = json.loads(path.read_text(encoding="utf-8").strip())
    assert line["event"] == "picker_selected"
    assert line["surface"] == "window"
    assert line["channel"] == "patient"
    assert line["diagnostic_id"] == parsed.diagnostic_id
    assert set(line) == {"time", "diagnostic_id", "event", "surface", "channel"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    diagnostics.reset_for_tests()


def test_unsafe_storage_fails_silently_and_does_not_affect_recording(tmp_path, capsys):
    diagnostics.reset_for_tests()
    target = tmp_path / "target.jsonl"
    target.write_text("SYNTHETIC SENTINEL", encoding="utf-8")
    target.chmod(0o644)
    assert diagnostics.configure_file_sink(str(target)) is False
    assert diagnostics.emit_many("a" * 32, [{"event": "picker_requested"}]) == 0
    assert target.read_text() == "SYNTHETIC SENTINEL"
    assert capsys.readouterr() == ("", "")
    diagnostics.reset_for_tests()


def test_symlinks_are_rejected(tmp_path):
    diagnostics.reset_for_tests()
    logs = tmp_path / "logs"
    logs.mkdir(mode=0o700)
    real = logs / "real.jsonl"
    real.write_text("", encoding="utf-8")
    (logs / "telehealth-capture.jsonl").symlink_to(real)
    assert not diagnostics.configure_file_sink(str(logs / "telehealth-capture.jsonl"))
    assert real.read_text() == ""
    diagnostics.reset_for_tests()


def test_diagnostics_route_requires_feature_and_owner(db_session, make_team, make_user, client, monkeypatch, tmp_path):
    from app.models import Transcript, TranscriptIngestionMode, TranscriptStatus, utcnow

    team = make_team(name="Synthetic Telehealth Diagnostics Team")
    owner = make_user(
        email="telehealth-diagnostics@example.com", password="password-1",
        team=team, mfa_required=False, mfa_enabled=False,
    )
    other = make_user(
        email="telehealth-diagnostics-other@example.com", password="password-1",
        team=team, mfa_required=False, mfa_enabled=False,
    )
    transcript = Transcript(
        owner_user_id=owner.id, team_id=team.id, title="Synthetic test",
        status=TranscriptStatus.ready, ingestion_mode=TranscriptIngestionMode.whole_file,
        retention_days_applied=30, retention_expires_at=utcnow() + timedelta(days=1),
    )
    db_session.add(transcript)
    db_session.commit()
    path = tmp_path / "logs" / "telehealth-capture.jsonl"
    assert diagnostics.configure_file_sink(str(path))
    endpoint = f"/api/v1/transcripts/{transcript.id}/telehealth/diagnostics"
    payload = _batch().model_dump(mode="json")
    try:
        monkeypatch.setenv("TELEHEALTH_API_ENABLED", "false")
        owner_login = client.post("/api/v1/auth/login", json={
            "email": owner.email, "password": "password-1",
        })
        assert owner_login.status_code == 200, owner_login.text
        disabled = client.post(endpoint, json=payload)
        assert disabled.status_code == 404
        assert not path.read_text(encoding="utf-8")

        monkeypatch.setenv("TELEHEALTH_API_ENABLED", "true")
        success = client.post(endpoint, json=payload)
        assert success.status_code == 200, success.text
        assert success.json()["accepted"] == 1

        invalid = client.post(endpoint, json={
            **payload, "events": [{"event": "picker_selected", "patient_name": "SENSITIVE"}],
        })
        assert invalid.status_code == 422
        assert "SENSITIVE" not in path.read_text(encoding="utf-8")

        # The same authenticated request cannot write another owner's root.
        other_login = client.post("/api/v1/auth/login", json={
            "email": other.email, "password": "password-1",
        })
        assert other_login.status_code == 200, other_login.text
        before = path.read_text(encoding="utf-8")
        forbidden = client.post(endpoint, json=payload)
        assert forbidden.status_code in {403, 404}
        assert path.read_text(encoding="utf-8") == before
    finally:
        diagnostics.reset_for_tests()
