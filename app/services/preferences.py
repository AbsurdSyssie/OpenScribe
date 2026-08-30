from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import PromptTemplate, QuickAction, TeamRole, TemplateScope, User, UserAppPreference
from app.schemas.preferences import UserAppPreferencesUpsert
from app.services.security_audit import record_security_event


def _require_user_app_preference_scope(actor: User) -> None:
    if actor.is_system_admin or actor.team_id is None or actor.team_role not in {TeamRole.user, TeamRole.leader}:
        raise AppError(403, "forbidden", "User app preferences are restricted to normal team users")


def _visible_template_ids(db: Session, actor: User) -> set[UUID]:
    rows = db.scalars(
        select(PromptTemplate.id).where(
            PromptTemplate.is_active.is_(True),
            (
                ((PromptTemplate.scope == TemplateScope.user) & (PromptTemplate.owner_user_id == actor.id))
                | ((PromptTemplate.scope == TemplateScope.team) & (PromptTemplate.team_id == actor.team_id))
            ),
        )
    )
    return set(rows)


def _visible_quick_action_ids(db: Session, actor: User) -> set[UUID]:
    rows = db.scalars(
        select(QuickAction.id).where(
            QuickAction.is_active.is_(True),
            (
                ((QuickAction.scope == TemplateScope.user) & (QuickAction.owner_user_id == actor.id))
                | ((QuickAction.scope == TemplateScope.team) & (QuickAction.team_id == actor.team_id))
            ),
        )
    )
    return set(rows)


def _serialize_preferences_payload(payload: UserAppPreferencesUpsert) -> dict[str, object]:
    preferences: dict[str, object] = {}
    if payload.favorite_quick_action_ids:
        preferences["favorite_quick_action_ids"] = [str(value) for value in payload.favorite_quick_action_ids]
    if payload.favorite_template_ids:
        preferences["favorite_template_ids"] = [str(value) for value in payload.favorite_template_ids]
    if payload.default_quick_action_id is not None:
        preferences["default_quick_action_id"] = str(payload.default_quick_action_id)
    if payload.default_template_id is not None:
        preferences["default_template_id"] = str(payload.default_template_id)
    if not payload.template_suggestions_enabled:
        preferences["template_suggestions_enabled"] = False
    if payload.llm_detail_level is not None:
        preferences["llm_detail_level"] = payload.llm_detail_level.value
    if payload.note_generation_length is not None:
        preferences["note_generation_length"] = payload.note_generation_length.value
    if payload.preferred_recording_mode is not None:
        preferences["preferred_recording_mode"] = payload.preferred_recording_mode.value
    if payload.preferred_transcribe_tab is not None:
        preferences["preferred_transcribe_tab"] = payload.preferred_transcribe_tab
    return preferences


def _validate_preferences_json(db: Session, actor: User, preferences_json: dict[str, object], *, strict: bool) -> dict[str, object]:
    template_ids = _visible_template_ids(db, actor)
    quick_action_ids = _visible_quick_action_ids(db, actor)

    normalized: dict[str, object] = {}

    raw_favorite_quick_action_ids = preferences_json.get("favorite_quick_action_ids")
    if isinstance(raw_favorite_quick_action_ids, list):
        valid_ids: list[str] = []
        invalid_found = False
        for raw_value in raw_favorite_quick_action_ids:
            try:
                value = UUID(str(raw_value))
            except (TypeError, ValueError):
                invalid_found = True
                continue
            if value in quick_action_ids:
                valid_ids.append(str(value))
            else:
                invalid_found = True
        if invalid_found and strict:
            raise AppError(422, "business_rule_violation", "Selected quick action favourites are not available for this user", {"field": "favorite_quick_action_ids"})
        if valid_ids:
            normalized["favorite_quick_action_ids"] = valid_ids

    raw_favorite_template_ids = preferences_json.get("favorite_template_ids")
    if isinstance(raw_favorite_template_ids, list):
        valid_ids = []
        invalid_found = False
        for raw_value in raw_favorite_template_ids:
            try:
                value = UUID(str(raw_value))
            except (TypeError, ValueError):
                invalid_found = True
                continue
            if value in template_ids:
                valid_ids.append(str(value))
            else:
                invalid_found = True
        if invalid_found and strict:
            raise AppError(422, "business_rule_violation", "Selected template favourites are not available for this user", {"field": "favorite_template_ids"})
        if valid_ids:
            normalized["favorite_template_ids"] = valid_ids

    raw_default_quick_action_id = preferences_json.get("default_quick_action_id")
    if raw_default_quick_action_id is not None:
        try:
            default_quick_action_id = UUID(str(raw_default_quick_action_id))
        except (TypeError, ValueError):
            if strict:
                raise AppError(422, "business_rule_violation", "Default quick action is invalid", {"field": "default_quick_action_id"})
        else:
            if default_quick_action_id not in quick_action_ids:
                if strict:
                    raise AppError(422, "business_rule_violation", "Default quick action is not available for this user", {"field": "default_quick_action_id"})
            else:
                normalized["default_quick_action_id"] = str(default_quick_action_id)

    raw_default_template_id = preferences_json.get("default_template_id")
    if raw_default_template_id is not None:
        try:
            default_template_id = UUID(str(raw_default_template_id))
        except (TypeError, ValueError):
            if strict:
                raise AppError(422, "business_rule_violation", "Default template is invalid", {"field": "default_template_id"})
        else:
            if default_template_id not in template_ids:
                if strict:
                    raise AppError(422, "business_rule_violation", "Default template is not available for this user", {"field": "default_template_id"})
            else:
                normalized["default_template_id"] = str(default_template_id)

    if preferences_json.get("template_suggestions_enabled") is False:
        normalized["template_suggestions_enabled"] = False

    raw_llm_detail_level = preferences_json.get("llm_detail_level")
    if isinstance(raw_llm_detail_level, str) and raw_llm_detail_level in {"concise", "balanced", "detailed"}:
        normalized["llm_detail_level"] = raw_llm_detail_level

    raw_note_generation_length = preferences_json.get("note_generation_length")
    if isinstance(raw_note_generation_length, str) and raw_note_generation_length in {"short", "normal", "long"}:
        normalized["note_generation_length"] = raw_note_generation_length

    raw_preferred_recording_mode = preferences_json.get("preferred_recording_mode")
    if isinstance(raw_preferred_recording_mode, str) and raw_preferred_recording_mode in {"whole_file", "live_chunked"}:
        normalized["preferred_recording_mode"] = raw_preferred_recording_mode

    raw_preferred_transcribe_tab = preferences_json.get("preferred_transcribe_tab")
    if isinstance(raw_preferred_transcribe_tab, str) and raw_preferred_transcribe_tab in {"output", "followups"}:
        normalized["preferred_transcribe_tab"] = raw_preferred_transcribe_tab

    return normalized


def get_user_app_preferences(db: Session, actor: User) -> UserAppPreference | None:
    _require_user_app_preference_scope(actor)
    preference = db.scalar(select(UserAppPreference).where(UserAppPreference.user_id == actor.id))
    if preference is None:
        return None
    normalized = _validate_preferences_json(db, actor, preference.preferences_json or {}, strict=False)
    if normalized != (preference.preferences_json or {}):
        preference.preferences_json = normalized
        db.add(preference)
        db.commit()
        db.refresh(preference)
    return preference


def template_suggestions_enabled(db: Session, actor: User) -> bool:
    """Return whether suggestions are enabled without exposing preference content."""
    _require_user_app_preference_scope(actor)
    preference = db.scalar(select(UserAppPreference).where(UserAppPreference.user_id == actor.id))
    return preference is None or preference.preferences_json.get("template_suggestions_enabled") is not False


def set_user_app_preferences(db: Session, actor: User, payload: UserAppPreferencesUpsert) -> UserAppPreference:
    _require_user_app_preference_scope(actor)
    serialized = _serialize_preferences_payload(payload)
    normalized = _validate_preferences_json(db, actor, serialized, strict=True)
    preference = db.scalar(select(UserAppPreference).where(UserAppPreference.user_id == actor.id))
    was_template_suggestions_enabled = preference is None or preference.preferences_json.get("template_suggestions_enabled") is not False
    will_template_suggestions_enabled = normalized.get("template_suggestions_enabled") is not False
    if was_template_suggestions_enabled and not will_template_suggestions_enabled:
        from app.services.template_suggestions import cancel_queued_template_suggestions_for_owner

        cancel_queued_template_suggestions_for_owner(db, actor)
    if preference is None:
        preference = UserAppPreference(id=uuid4(), user_id=actor.id, preferences_json=normalized)
    else:
        preference.preferences_json = normalized
    db.add(preference)
    db.commit()
    db.refresh(preference)
    record_security_event(
        db,
        action="user_app_preferences_set",
        actor=actor,
        target=actor,
        team_id=actor.team_id,
        details={"category": "template", "outcome": "success", "object_type": "user_app_preferences", "object_id": str(preference.id), "keys": sorted(normalized.keys())},
    )
    return preference


def clear_user_app_preferences(db: Session, actor: User) -> None:
    _require_user_app_preference_scope(actor)
    preference = db.scalar(select(UserAppPreference).where(UserAppPreference.user_id == actor.id))
    if preference is None:
        raise AppError(404, "not_found", "User app preferences not found", {"resource": "user_app_preferences", "user_id": str(actor.id)})
    preference_id = preference.id
    db.delete(preference)
    db.commit()
    record_security_event(db, action="user_app_preferences_cleared", actor=actor, target=actor, team_id=actor.team_id, details={"category": "template", "outcome": "success", "object_type": "user_app_preferences", "object_id": str(preference_id)})
