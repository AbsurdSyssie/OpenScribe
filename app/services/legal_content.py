from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    LegalDocumentKind,
    LegalDocumentRoot,
    LegalDocumentVersion,
    LegalDocumentVersionState,
    OperatorLegalProfile,
    User,
    utcnow,
)
from app.schemas.legal_content import (
    LegalDocumentContent,
    LegalDocumentDraftCreate,
    LegalDocumentDraftUpdate,
    OperatorLegalProfileUpdate,
)
from app.services.security_audit import add_security_event


def _require_system_admin(actor: User) -> None:
    if not actor.is_system_admin:
        raise AppError(403, "forbidden", "System-admin legal-content access required")


def _record_legal_event(
    db: Session,
    *,
    action: str,
    actor: User,
    kind: LegalDocumentKind | None = None,
    version_no: int | None = None,
    state: LegalDocumentVersionState | None = None,
) -> None:
    details: dict[str, object] = {"category": "legal_content", "outcome": "success"}
    if kind is not None:
        details["document_kind"] = kind.value
    if version_no is not None:
        details["version_no"] = version_no
    if state is not None:
        details["state"] = state.value
    add_security_event(db, action=action, actor=actor, details=details)


def get_operator_legal_profile(db: Session) -> OperatorLegalProfile | None:
    return db.get(OperatorLegalProfile, True)


def operator_legal_setup_warnings(db: Session) -> tuple[str, ...]:
    profile = get_operator_legal_profile(db)
    warnings: list[str] = []
    if profile is None or not profile.legal_name or not profile.public_url:
        warnings.append("Operator legal identity is incomplete.")
    if profile is None or not profile.privacy_email or not profile.complaints_email:
        warnings.append("Operator privacy or complaints contact is missing.")
    if profile is None or not profile.security_contact:
        warnings.append("The security.txt contact is missing.")
    published_kinds = set(
        db.scalars(
            select(LegalDocumentRoot.kind)
            .join(LegalDocumentVersion)
            .where(LegalDocumentVersion.state == LegalDocumentVersionState.published)
        )
    )
    if LegalDocumentKind.privacy not in published_kinds:
        warnings.append("No privacy notice is published.")
    if LegalDocumentKind.cookie_storage not in published_kinds:
        warnings.append("No cookie and browser-storage notice is published.")
    return tuple(warnings)


def update_operator_legal_profile(
    db: Session,
    *,
    actor: User,
    payload: OperatorLegalProfileUpdate,
) -> OperatorLegalProfile:
    _require_system_admin(actor)
    profile = db.scalar(select(OperatorLegalProfile).where(OperatorLegalProfile.singleton_key.is_(True)).with_for_update())
    if profile is None:
        if payload.expected_revision is not None:
            raise AppError(409, "conflict", "Operator legal profile changed; reload and try again")
        profile = OperatorLegalProfile(singleton_key=True, revision=1)
    else:
        if payload.expected_revision is None or payload.expected_revision != profile.revision:
            raise AppError(409, "conflict", "Operator legal profile changed; reload and try again")
        profile.revision += 1

    for field_name, value in payload.model_dump(exclude={"expected_revision"}).items():
        setattr(profile, field_name, value)
    db.add(profile)
    try:
        _record_legal_event(db, action="operator_legal_profile_updated", actor=actor)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "conflict", "Operator legal profile changed; reload and try again") from exc
    except Exception:
        db.rollback()
        raise
    db.refresh(profile)
    return profile


def _locked_root(db: Session, *, kind: LegalDocumentKind) -> LegalDocumentRoot:
    root = db.scalar(select(LegalDocumentRoot).where(LegalDocumentRoot.kind == kind).with_for_update())
    if root is not None:
        return root
    root = LegalDocumentRoot(id=uuid4(), kind=kind)
    db.add(root)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        root = db.scalar(select(LegalDocumentRoot).where(LegalDocumentRoot.kind == kind).with_for_update())
        if root is None:
            raise AppError(409, "conflict", "Legal document root could not be created") from exc
    return root


def _next_version_number(db: Session, *, root_id: UUID) -> int:
    current = db.scalar(
        select(func.max(LegalDocumentVersion.version_no)).where(
            LegalDocumentVersion.document_root_id == root_id
        )
    )
    return int(current or 0) + 1


def create_legal_document_draft(
    db: Session,
    *,
    actor: User,
    payload: LegalDocumentDraftCreate,
    audit_action: str = "legal_document_draft_created",
) -> LegalDocumentVersion:
    _require_system_admin(actor)
    root = _locked_root(db, kind=payload.kind)
    version = LegalDocumentVersion(
        id=uuid4(),
        document_root_id=root.id,
        version_no=_next_version_number(db, root_id=root.id),
        state=LegalDocumentVersionState.draft,
        effective_on=payload.effective_on,
        blocks_json=payload.content.model_dump(mode="json")["blocks"],
        revision=1,
        author_user_id=actor.id,
    )
    db.add(version)
    try:
        _record_legal_event(
            db,
            action=audit_action,
            actor=actor,
            kind=payload.kind,
            version_no=version.version_no,
            state=version.state,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "conflict", "Legal document version changed; reload and try again") from exc
    except Exception:
        db.rollback()
        raise
    db.refresh(version)
    return version


def update_legal_document_draft(
    db: Session,
    *,
    actor: User,
    version_id: UUID,
    payload: LegalDocumentDraftUpdate,
) -> LegalDocumentVersion:
    _require_system_admin(actor)
    version = db.scalar(
        select(LegalDocumentVersion)
        .where(LegalDocumentVersion.id == version_id)
        .with_for_update()
    )
    if version is None:
        raise AppError(404, "not_found", "Legal document version not found")
    if version.state is not LegalDocumentVersionState.draft:
        raise AppError(409, "conflict", "Published legal content is immutable")
    if version.revision != payload.expected_revision:
        raise AppError(409, "conflict", "Legal document draft changed; reload and try again")
    version.effective_on = payload.effective_on
    version.blocks_json = payload.content.model_dump(mode="json")["blocks"]
    version.revision += 1
    version.author_user_id = actor.id
    db.add(version)
    try:
        _record_legal_event(
            db,
            action="legal_document_draft_updated",
            actor=actor,
            kind=version.root.kind,
            version_no=version.version_no,
            state=version.state,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(version)
    return version


def publish_legal_document_draft(
    db: Session,
    *,
    actor: User,
    version_id: UUID,
    expected_revision: int,
) -> LegalDocumentVersion:
    _require_system_admin(actor)
    identity = db.get(LegalDocumentVersion, version_id)
    if identity is None:
        raise AppError(404, "not_found", "Legal document version not found")
    root = db.scalar(
        select(LegalDocumentRoot)
        .where(LegalDocumentRoot.id == identity.document_root_id)
        .with_for_update()
    )
    if root is None:
        raise AppError(404, "not_found", "Legal document root not found")
    version = db.scalar(
        select(LegalDocumentVersion)
        .where(LegalDocumentVersion.id == version_id)
        .with_for_update()
    )
    if version is None:
        raise AppError(404, "not_found", "Legal document version not found")
    if version.state is not LegalDocumentVersionState.draft:
        raise AppError(409, "conflict", "Only a draft can be published")
    if version.revision != expected_revision:
        raise AppError(409, "conflict", "Legal document draft changed; reload and try again")

    now = utcnow()
    current = db.scalar(
        select(LegalDocumentVersion)
        .where(
            LegalDocumentVersion.document_root_id == root.id,
            LegalDocumentVersion.state == LegalDocumentVersionState.published,
        )
        .with_for_update()
    )
    if current is not None:
        current.state = LegalDocumentVersionState.superseded
        current.superseded_at = now
        current.superseded_by_user_id = actor.id
        db.add(current)
        # Clear the partial one-published-version slot before assigning it to
        # the replacement. Both changes remain inside this transaction.
        db.flush()
    version.state = LegalDocumentVersionState.published
    version.published_at = now
    version.published_by_user_id = actor.id
    db.add(version)
    try:
        _record_legal_event(
            db,
            action="legal_document_published",
            actor=actor,
            kind=root.kind,
            version_no=version.version_no,
            state=version.state,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "conflict", "Another legal document version was published; reload and try again") from exc
    except Exception:
        db.rollback()
        raise
    db.refresh(version)
    return version


def create_legal_document_rollback_draft(
    db: Session,
    *,
    actor: User,
    source_version_id: UUID,
    effective_on: date,
) -> LegalDocumentVersion:
    _require_system_admin(actor)
    source = db.get(LegalDocumentVersion, source_version_id)
    if source is None:
        raise AppError(404, "not_found", "Legal document version not found")
    if source.state is LegalDocumentVersionState.draft:
        raise AppError(409, "conflict", "Rollback source must be a published historical version")
    try:
        content = LegalDocumentContent.model_validate({"blocks": source.blocks_json})
    except ValidationError as exc:
        raise AppError(500, "legal_content_invalid", "Stored legal content failed validation") from exc
    return create_legal_document_draft(
        db,
        actor=actor,
        payload=LegalDocumentDraftCreate(kind=source.root.kind, effective_on=effective_on, content=content),
        audit_action="legal_document_rollback_draft_created",
    )


def current_published_legal_document(
    db: Session,
    *,
    kind: LegalDocumentKind,
) -> tuple[LegalDocumentVersion, LegalDocumentContent] | None:
    version = db.scalar(
        select(LegalDocumentVersion)
        .join(LegalDocumentRoot)
        .where(
            LegalDocumentRoot.kind == kind,
            LegalDocumentVersion.state == LegalDocumentVersionState.published,
        )
    )
    if version is None:
        return None
    try:
        content = LegalDocumentContent.model_validate({"blocks": version.blocks_json})
    except ValidationError as exc:
        raise AppError(500, "legal_content_invalid", "Stored legal content failed validation") from exc
    return version, content


def list_legal_document_versions(
    db: Session,
    *,
    kind: LegalDocumentKind,
) -> list[LegalDocumentVersion]:
    return list(
        db.scalars(
            select(LegalDocumentVersion)
            .join(LegalDocumentRoot)
            .where(LegalDocumentRoot.kind == kind)
            .order_by(LegalDocumentVersion.version_no.desc())
        )
    )


def published_legal_footer_state(
    db: Session,
) -> tuple[tuple[dict[str, str], ...], int | None]:
    routes = {
        LegalDocumentKind.privacy: ("Privacy", "/privacy"),
        LegalDocumentKind.cookie_storage: ("Cookies and browser storage", "/cookies"),
        LegalDocumentKind.terms: ("Terms", "/terms"),
    }
    published_versions = {
        kind: version_no
        for kind, version_no in db.execute(
            select(LegalDocumentRoot.kind, LegalDocumentVersion.version_no)
            .join(LegalDocumentVersion)
            .where(LegalDocumentVersion.state == LegalDocumentVersionState.published)
        )
    }
    links = tuple(
        {"label": label, "href": href}
        for kind, (label, href) in routes.items()
        if kind in published_versions
    )
    return links, published_versions.get(LegalDocumentKind.cookie_storage)


def published_legal_links(db: Session) -> tuple[dict[str, str], ...]:
    return published_legal_footer_state(db)[0]
