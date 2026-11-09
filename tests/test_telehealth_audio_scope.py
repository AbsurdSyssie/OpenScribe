"""Requested browser-audio provenance is strictly bounded and not proof of isolation."""
import pytest
from pydantic import ValidationError
from sqlalchemy import CheckConstraint

from app.models import TelehealthCaptureSession
from app.schemas.telehealth import TelehealthCaptureStart, TelehealthCaptureDetail


def test_requested_audio_scope_is_validated_without_raw_app_identity():
    default = TelehealthCaptureStart.model_validate({"timeline_sample_rate": 48000})
    assert default.requested_audio_scope == "unknown"
    for scope in ("call", "system"):
        value = TelehealthCaptureStart.model_validate({
            "timeline_sample_rate": 48000, "requested_audio_scope": scope,
        })
        assert value.requested_audio_scope == scope
    for invalid in ("any process", "all systems", "", None, 42):
        with pytest.raises(ValidationError):
            TelehealthCaptureStart.model_validate({
                "timeline_sample_rate": 48000, "requested_audio_scope": invalid,
            })


def test_requested_audio_scope_is_constrained_and_returned_as_metadata():
    table = TelehealthCaptureSession.__table__
    assert "requested_audio_scope" in table.columns
    column = table.columns["requested_audio_scope"]
    assert column.nullable is False
    assert any(
        isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_telehealth_capture_sessions_audio_scope"
        for constraint in table.constraints
    )
    assert "requested_audio_scope" in TelehealthCaptureDetail.model_fields
