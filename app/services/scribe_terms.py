from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from pydantic import ValidationError

from app.errors import AppError
from app.models import (
    LegalDocumentKind,
    LegalDocumentRoot,
    LegalDocumentVersion,
    LegalDocumentVersionState,
    ScribeTermsAcceptance,
    SessionStatus,
    User,
    UserSession,
    utcnow,
)
from app.schemas.legal_content import LegalDocumentContent


def current_scribe_terms_version_no(db: Session) -> int | None:
    return db.scalar(
        select(LegalDocumentVersion.version_no)
        .join(LegalDocumentRoot)
        .where(
            LegalDocumentRoot.kind == LegalDocumentKind.terms,
            LegalDocumentVersion.state == LegalDocumentVersionState.published,
        )
    )


def required_scribe_terms_version(
    db: Session, *, user: User, session: UserSession
) -> int | None:
    """Resolve and return the unaccepted Terms version pinned to this session."""
    if user.is_system_admin:
        return None
    pinned = session.scribe_terms_version_no
    if pinned is None:
        # Lock only legacy sessions. populate_existing avoids trusting a stale
        # identity-map value if another request resolved this session first.
        locked = db.scalar(
            select(UserSession)
            .where(UserSession.id == session.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked is None:
            raise AppError(401, "unauthorized", "Session is no longer available")
        if (
            locked.user_id != user.id
            or locked.status is not SessionStatus.active
            or locked.expires_at <= utcnow()
        ):
            db.commit()
            raise AppError(401, "unauthorized", "Session is no longer available")
        if locked.scribe_terms_version_no is None:
            published = current_scribe_terms_version_no(db)
            locked.scribe_terms_version_no = published if published is not None else 0
            db.add(locked)
            # The pin must survive requests that end in a 403 or a read-only
            # response, whose dependency cleanup does not commit.
            db.commit()
        else:
            # Another request resolved this legacy session while we waited.
            # Commit immediately to release the row lock before any content work.
            db.commit()
        pinned = locked.scribe_terms_version_no
        session.scribe_terms_version_no = pinned
    if pinned is None or pinned == 0:
        return None
    accepted = db.scalar(
        select(ScribeTermsAcceptance.id).where(
            ScribeTermsAcceptance.user_id == user.id,
            ScribeTermsAcceptance.version_no == pinned,
        )
    )
    return None if accepted is not None else pinned


def read_scribe_terms_version(
    db: Session, *, version_no: int
) -> tuple[LegalDocumentVersion, LegalDocumentContent]:
    version = db.scalar(
        select(LegalDocumentVersion)
        .join(LegalDocumentRoot)
        .where(
            LegalDocumentRoot.kind == LegalDocumentKind.terms,
            LegalDocumentVersion.version_no == version_no,
            LegalDocumentVersion.state.in_(
                [LegalDocumentVersionState.published, LegalDocumentVersionState.superseded]
            ),
        )
    )
    if version is None:
        raise AppError(404, "legal_document_unavailable", "The Terms for this session are no longer available")
    try:
        content = LegalDocumentContent.model_validate({"blocks": version.blocks_json})
    except ValidationError as exc:
        raise AppError(500, "legal_content_invalid", "Stored legal content failed validation") from exc
    return version, content


def accept_scribe_terms(
    db: Session, *, user: User, session: UserSession, version_no: int
) -> None:
    required = required_scribe_terms_version(db, user=user, session=session)
    pinned = session.scribe_terms_version_no
    if user.is_system_admin or pinned is None or pinned <= 0 or version_no != pinned:
        raise AppError(409, "scribe_terms_version_mismatch", "The submitted Terms version does not match this session")
    if required is None:
        # The positive pin was checked above, so no outstanding requirement
        # means this is a successful retry of an existing acceptance.
        return
    # A missing/expired text row must never yield a new acceptance.
    read_scribe_terms_version(db, version_no=pinned)
    # The unique constraint plus DO NOTHING makes concurrent retries idempotent;
    # it never replaces the original server timestamp.
    db.execute(
        insert(ScribeTermsAcceptance)
        .values(user_id=user.id, version_no=version_no)
        .on_conflict_do_nothing(constraint="uq_scribe_terms_acceptances_user_version")
    )
    db.flush()
