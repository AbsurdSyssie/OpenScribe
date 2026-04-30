from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import SmartPhrase, User, utcnow
from app.schemas.smart_phrases import SmartPhraseCreate, SmartPhraseUpdate


DEFAULT_SMART_PHRASE_TRIGGER = "CESRF"
DEFAULT_SMART_PHRASE_EXPANSION = (
    "Discussed cauda equina red flags with the patient, including new saddle numbness, "
    "new bladder or bowel dysfunction, new leg weakness, or worsening bilateral sciatica. "
    "Advised them to seek urgent medical attention if these symptoms develop."
)
DEFAULT_SMART_PHRASE_DESCRIPTION = "Cauda equina red flags safety-netting"


def _require_personal_phrase_user(actor: User) -> None:
    if actor.is_system_admin or actor.team_id is None:
        raise AppError(403, "forbidden", "Smart phrases are available only to normal team users")


def _raise_trigger_conflict(exc: IntegrityError | None = None) -> None:
    raise AppError(
        409,
        "conflict",
        "Smart phrase trigger already exists",
        {"resource": "smart_phrase", "field": "trigger"},
    ) from exc


def _resolve_phrase_for_owner(db: Session, actor: User, *, smart_phrase_id: UUID) -> SmartPhrase:
    _require_personal_phrase_user(actor)
    phrase = db.scalar(
        select(SmartPhrase).where(
            SmartPhrase.id == smart_phrase_id,
            SmartPhrase.owner_user_id == actor.id,
        )
    )
    if phrase is None:
        raise AppError(
            404,
            "not_found",
            "Smart phrase not found",
            {"resource": "smart_phrase", "smart_phrase_id": str(smart_phrase_id)},
        )
    return phrase


def _ensure_unique_trigger(
    db: Session,
    actor: User,
    *,
    trigger: str,
    current_smart_phrase_id: UUID | None = None,
) -> None:
    stmt = select(SmartPhrase).where(
        SmartPhrase.owner_user_id == actor.id,
        func.lower(SmartPhrase.trigger) == trigger.lower(),
    )
    if current_smart_phrase_id is not None:
        stmt = stmt.where(SmartPhrase.id != current_smart_phrase_id)
    if db.scalar(stmt) is not None:
        _raise_trigger_conflict()


def ensure_default_smart_phrase_for_user(db: Session, user: User, *, commit: bool = False) -> SmartPhrase | None:
    if user.is_system_admin or user.team_id is None:
        return None
    existing = db.scalar(
        select(SmartPhrase).where(
            SmartPhrase.owner_user_id == user.id,
            func.lower(SmartPhrase.trigger) == DEFAULT_SMART_PHRASE_TRIGGER.lower(),
        )
    )
    if existing is not None:
        return existing
    phrase = SmartPhrase(
        id=uuid4(),
        owner_user_id=user.id,
        trigger=DEFAULT_SMART_PHRASE_TRIGGER,
        expansion_text=DEFAULT_SMART_PHRASE_EXPANSION,
        description=DEFAULT_SMART_PHRASE_DESCRIPTION,
        times_used=0,
    )
    db.add(phrase)
    if commit:
        db.commit()
        db.refresh(phrase)
    return phrase


def list_personal_smart_phrases(db: Session, actor: User) -> list[SmartPhrase]:
    _require_personal_phrase_user(actor)
    return list(
        db.scalars(
            select(SmartPhrase)
            .where(SmartPhrase.owner_user_id == actor.id)
            .order_by(func.lower(SmartPhrase.trigger).asc(), SmartPhrase.id.asc())
        )
    )


def list_available_smart_phrases(db: Session, actor: User) -> list[SmartPhrase]:
    return list_personal_smart_phrases(db, actor)


def create_personal_smart_phrase(db: Session, actor: User, payload: SmartPhraseCreate) -> SmartPhrase:
    _require_personal_phrase_user(actor)
    _ensure_unique_trigger(db, actor, trigger=payload.trigger)
    phrase = SmartPhrase(
        id=uuid4(),
        owner_user_id=actor.id,
        trigger=payload.trigger,
        expansion_text=payload.expansion_text,
        description=payload.description,
        times_used=0,
    )
    db.add(phrase)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        _raise_trigger_conflict(exc)
    except Exception:
        db.rollback()
        raise
    db.refresh(phrase)
    return phrase


def update_personal_smart_phrase(
    db: Session,
    actor: User,
    *,
    smart_phrase_id: UUID,
    payload: SmartPhraseUpdate,
) -> SmartPhrase:
    phrase = _resolve_phrase_for_owner(db, actor, smart_phrase_id=smart_phrase_id)
    if payload.trigger is not None:
        _ensure_unique_trigger(db, actor, trigger=payload.trigger, current_smart_phrase_id=phrase.id)
        phrase.trigger = payload.trigger
    if payload.expansion_text is not None:
        phrase.expansion_text = payload.expansion_text
    if "description" in payload.model_fields_set:
        phrase.description = payload.description
    phrase.updated_at = utcnow()
    db.add(phrase)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        _raise_trigger_conflict(exc)
    except Exception:
        db.rollback()
        raise
    db.refresh(phrase)
    return phrase


def delete_personal_smart_phrase(db: Session, actor: User, *, smart_phrase_id: UUID) -> None:
    phrase = _resolve_phrase_for_owner(db, actor, smart_phrase_id=smart_phrase_id)
    db.delete(phrase)
    db.commit()


def mark_personal_smart_phrase_used(db: Session, actor: User, *, smart_phrase_id: UUID) -> SmartPhrase:
    phrase = _resolve_phrase_for_owner(db, actor, smart_phrase_id=smart_phrase_id)
    phrase.times_used = int(phrase.times_used or 0) + 1
    phrase.last_used_at = utcnow()
    phrase.updated_at = utcnow()
    db.add(phrase)
    db.commit()
    db.refresh(phrase)
    return phrase
