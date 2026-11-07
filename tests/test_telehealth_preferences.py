"""Owner-only Telehealth UI preferences and first-use guidance acknowledgement.

Pure/schema tests plus existing rollback-isolated PostgreSQL fixtures.
"""
from app.schemas.preferences import UserAppPreferencesUpsert
from app.services.preferences import set_user_app_preferences
from app.web.presentation import user_app_preferences_response


def test_telehealth_preference_fields_have_safe_defaults():
    upsert = UserAppPreferencesUpsert()
    assert upsert.show_telehealth_control is True
    assert upsert.telehealth_guidance_acknowledged is False
    assert "show_telehealth_control" not in upsert.model_fields_set
    assert "telehealth_guidance_acknowledged" not in upsert.model_fields_set


def test_telehealth_hidden_setting_survives_legacy_full_preferences_posts(
    db_session, make_team, make_user,
):
    team = make_team(name="Synthetic Telehealth Preference Team")
    owner = make_user(
        email="telehealth-pref@example.com", password="password-1", team=team,
    )
    saved = set_user_app_preferences(
        db_session, owner,
        UserAppPreferencesUpsert(
            show_telehealth_control=False,
            telehealth_guidance_acknowledged=True,
        ),
    )
    assert saved.preferences_json["show_telehealth_control"] is False
    assert saved.preferences_json["telehealth_guidance_acknowledged"] is True
    detail = user_app_preferences_response(saved)
    assert detail.show_telehealth_control is False
    assert detail.telehealth_guidance_acknowledged is True

    # A workspace client which predates Telehealth only knows existing fields
    # and sends a whole-preference POST. It cannot accidentally re-show the
    # hidden Telehealth control or undo the recorded first-use notice.
    saved = set_user_app_preferences(
        db_session, owner, UserAppPreferencesUpsert(
            preferred_recording_mode="whole_file",
            diarization_enabled=True,
        ),
    )
    detail = user_app_preferences_response(saved)
    assert detail.show_telehealth_control is False
    assert detail.telehealth_guidance_acknowledged is True
    assert detail.diarization_enabled is True

    # A newer client can explicitly re-enable the control without resetting
    # the monotonic acknowledgement or unrelated preferences.
    saved = set_user_app_preferences(
        db_session, owner, UserAppPreferencesUpsert(
            show_telehealth_control=True,
            preferred_recording_mode="whole_file",
            diarization_enabled=True,
        ),
    )
    detail = user_app_preferences_response(saved)
    assert detail.show_telehealth_control is True
    assert detail.telehealth_guidance_acknowledged is True
    assert detail.diarization_enabled is True
