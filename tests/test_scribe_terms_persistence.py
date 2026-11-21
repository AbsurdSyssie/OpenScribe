from datetime import date

import pytest
from sqlalchemy import select

from app.errors import AppError
from app.models import (
    LegalDocumentKind,
    ScribeTermsAcceptance,
    UserSession,
)
from app.schemas.legal_content import LegalDocumentContent, LegalDocumentDraftCreate
from app.services.auth import create_session, rotate_session, session_token_hash
from app.services.legal_content import create_legal_document_draft, publish_legal_document_draft
from app.services.scribe_terms import (
    accept_scribe_terms,
    read_scribe_terms_version,
    required_scribe_terms_version,
)


def _publish_terms(db, admin, *, text="Synthetic Scribe terms"):
    content = LegalDocumentContent.model_validate(
        {"blocks": [{"type": "heading", "text": "Terms"}, {"type": "paragraph", "text": text}]}
    )
    draft = create_legal_document_draft(
        db,
        actor=admin,
        payload=LegalDocumentDraftCreate(kind=LegalDocumentKind.terms, effective_on=date(2026, 11, 1), content=content),
    )
    return publish_legal_document_draft(db, actor=admin, version_id=draft.id, expected_revision=draft.revision)


def _session(db, user):
    token = create_session(db, user)
    return db.scalar(select(UserSession).where(UserSession.session_token_hash == session_token_hash(token)))


def test_legacy_session_pins_first_terms_publication_and_keeps_it_after_new_publication(
    db_session, make_user
):
    admin = make_user(email="terms-pin-admin@example.test", is_system_admin=True)
    user = make_user(email="terms-pin-member@example.test")
    session = _session(db_session, user)
    assert session.scribe_terms_version_no == 0

    # Simulate a pre-rollout session, then resolve its version on first Scribe use.
    session.scribe_terms_version_no = None
    db_session.flush()
    _publish_terms(db_session, admin, text="Version one")
    assert required_scribe_terms_version(db_session, user=user, session=session) == 1
    assert session.scribe_terms_version_no == 1

    _publish_terms(db_session, admin, text="Version two")
    version, content = read_scribe_terms_version(db_session, version_no=1)
    assert version.version_no == 1
    assert content.blocks[1].text == "Version one"
    assert required_scribe_terms_version(db_session, user=user, session=session) == 1


def test_acceptance_is_version_bound_idempotent_and_preserves_first_timestamp(db_session, make_user):
    admin = make_user(email="terms-accept-admin@example.test", is_system_admin=True)
    user = make_user(email="terms-accept-member@example.test")
    _publish_terms(db_session, admin)
    session = _session(db_session, user)

    with pytest.raises(AppError) as mismatch:
        accept_scribe_terms(db_session, user=user, session=session, version_no=2)
    assert mismatch.value.code == "scribe_terms_version_mismatch"

    accept_scribe_terms(db_session, user=user, session=session, version_no=1)
    first = db_session.scalar(select(ScribeTermsAcceptance).where(ScribeTermsAcceptance.user_id == user.id))
    first_timestamp = first.accepted_at
    accept_scribe_terms(db_session, user=user, session=session, version_no=1)
    rows = list(db_session.scalars(select(ScribeTermsAcceptance).where(ScribeTermsAcceptance.user_id == user.id)))
    assert len(rows) == 1
    assert rows[0].accepted_at == first_timestamp
    assert required_scribe_terms_version(db_session, user=user, session=session) is None


def test_admin_and_no_publication_have_no_requirement(db_session, make_user):
    admin = make_user(email="terms-exempt-admin@example.test", is_system_admin=True)
    member = make_user(email="terms-no-publication@example.test")
    assert required_scribe_terms_version(db_session, user=admin, session=_session(db_session, admin)) is None
    session = _session(db_session, member)
    assert session.scribe_terms_version_no == 0
    assert required_scribe_terms_version(db_session, user=member, session=session) is None


def test_new_signin_pins_latest_terms_while_rotation_preserves_old_pin(db_session, make_user):
    admin = make_user(email="terms-rotation-admin@example.test", is_system_admin=True)
    user = make_user(email="terms-rotation-member@example.test")
    _publish_terms(db_session, admin, text="Version one")
    old_login_token = create_session(db_session, user)
    old_login = db_session.scalar(
        select(UserSession).where(UserSession.session_token_hash == session_token_hash(old_login_token))
    )
    assert old_login.scribe_terms_version_no == 1

    _publish_terms(db_session, admin, text="Version two")
    new_login_token = create_session(db_session, user)
    new_login = db_session.scalar(
        select(UserSession).where(UserSession.session_token_hash == session_token_hash(new_login_token))
    )
    assert new_login.scribe_terms_version_no == 2

    rotated_token = rotate_session(db_session, old_login_token, user)
    rotated = db_session.scalar(
        select(UserSession).where(UserSession.session_token_hash == session_token_hash(rotated_token))
    )
    assert rotated.scribe_terms_version_no == 1


def test_stale_request_cannot_resolve_legacy_pin_after_session_rotation(db_session, make_user):
    admin = make_user(email="terms-stale-admin@example.test", is_system_admin=True)
    user = make_user(email="terms-stale-member@example.test")
    old_token = create_session(db_session, user)
    old_session = db_session.scalar(
        select(UserSession).where(UserSession.session_token_hash == session_token_hash(old_token))
    )
    # Model an authenticated pre-rollout request whose requirement lookup is
    # delayed until after the request rotates its session.
    old_session.scribe_terms_version_no = None
    db_session.flush()
    _publish_terms(db_session, admin)

    replacement_token = rotate_session(db_session, old_token, user)
    replacement = db_session.scalar(
        select(UserSession).where(UserSession.session_token_hash == session_token_hash(replacement_token))
    )
    assert old_session.status.value == "revoked"
    assert replacement.scribe_terms_version_no is None

    with pytest.raises(AppError) as stale_request:
        required_scribe_terms_version(db_session, user=user, session=old_session)
    assert stale_request.value.status_code == 401
    assert old_session.scribe_terms_version_no is None
