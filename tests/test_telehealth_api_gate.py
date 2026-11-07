"""Default-off Telehealth API and persisted one-time notice contract."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.errors import AppError
from app.routes.api_routes import start_telehealth_capture_route
from app.schemas.preferences import UserAppPreferencesUpsert
from app.schemas.telehealth import TelehealthCaptureStart
from app.services.preferences import set_user_app_preferences


def test_telehealth_api_remains_disabled_by_default(db_session, make_team, make_user, monkeypatch):
    team = make_team(name="Telehealth Flag Team")
    owner = make_user(email="telehealth-flag@example.com", password="password-1", team=team)
    monkeypatch.delenv("TELEHEALTH_API_ENABLED", raising=False)
    with pytest.raises(AppError) as error:
        start_telehealth_capture_route(
            transcript_id=uuid4(),
            payload=TelehealthCaptureStart(timeline_sample_rate=48000),
            context=SimpleNamespace(user=owner),
            db=db_session,
        )
    assert error.value.status_code == 404
    assert error.value.code == "not_found"


def test_first_use_guidance_required_even_when_api_is_explicitly_enabled(
    db_session, make_team, make_user, monkeypatch,
):
    team = make_team(name="Telehealth Guidance Gate")
    owner = make_user(email="telehealth-guidance-gate@example.com", password="password-1", team=team)
    monkeypatch.setenv("TELEHEALTH_API_ENABLED", "true")
    transcript_id = uuid4()
    payload = TelehealthCaptureStart(timeline_sample_rate=48000)
    context = SimpleNamespace(user=owner)
    capture_called = []
    monkeypatch.setattr(
        "app.routes.api_routes.begin_telehealth_capture",
        lambda *args, **kwargs: capture_called.append(kwargs) or SimpleNamespace(id=uuid4()),
    )
    monkeypatch.setattr(
        "app.routes.api_routes.telehealth_capture_detail",
        lambda *args, **kwargs: {"status": "active", "capture_session_id": kwargs["capture_session_id"]},
    )

    with pytest.raises(AppError) as error:
        start_telehealth_capture_route(
            transcript_id=transcript_id, payload=payload,
            context=context, db=db_session,
        )
    assert error.value.code == "telehealth_guidance_required"
    assert capture_called == []

    set_user_app_preferences(
        db_session, owner,
        UserAppPreferencesUpsert(telehealth_guidance_acknowledged=True),
    )
    result = start_telehealth_capture_route(
        transcript_id=transcript_id, payload=payload,
        context=context, db=db_session,
    )
    assert result["status"] == "active"
    assert len(capture_called) == 1
    assert capture_called[0]["timeline_sample_rate"] == 48000
