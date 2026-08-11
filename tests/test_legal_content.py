from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.errors import AppError
from app.models import LegalDocumentKind, LegalDocumentVersionState, SecurityAuditEvent
from app.schemas.legal_content import (
    LegalDocumentContent,
    LegalDocumentDraftCreate,
    LegalDocumentDraftUpdate,
    OperatorLegalProfileUpdate,
)
from app.services.legal_content import (
    create_legal_document_draft,
    create_legal_document_rollback_draft,
    current_published_legal_document,
    list_legal_document_versions,
    operator_legal_setup_warnings,
    publish_legal_document_draft,
    update_legal_document_draft,
    update_operator_legal_profile,
)
from app.services.legal_content_markdown import (
    LegalMarkdownError,
    legal_content_to_markdown,
    parse_legal_markdown,
    parse_legal_markdown_result,
)
from app.services.legal_content_retention import (
    expire_legal_document_versions,
    place_legal_document_hold,
    release_legal_document_hold,
)
from app.services.auth import SESSION_COOKIE_NAME
from app.services.csrf import verify_csrf_token


def _content(*, text: str = "Synthetic legal notice text.") -> LegalDocumentContent:
    return LegalDocumentContent.model_validate(
        {
            "blocks": [
                {"type": "heading", "text": "Privacy information"},
                {"type": "paragraph", "text": text},
                {"type": "bullet_list", "items": ["Synthetic item one", "Synthetic item two"]},
                {
                    "type": "labelled_https_link",
                    "label": "Operator contact page",
                    "url": "https://operator.example.test/contact",
                },
            ]
        }
    )


def _draft_payload(kind: LegalDocumentKind, *, content: LegalDocumentContent | None = None) -> LegalDocumentDraftCreate:
    return LegalDocumentDraftCreate(
        kind=kind,
        effective_on=date(2026, 8, 5),
        content=content or _content(),
    )


def _publish(db_session, *, actor, kind: LegalDocumentKind, content: LegalDocumentContent | None = None):
    draft = create_legal_document_draft(db_session, actor=actor, payload=_draft_payload(kind, content=content))
    return publish_legal_document_draft(
        db_session, actor=actor, version_id=draft.id, expected_revision=draft.revision
    )


def _bind_legal_footer_queries_to_test_transaction(db_session, monkeypatch) -> None:
    """The middleware opens a short read-only session for global footer state."""
    factory = sessionmaker(
        bind=db_session.get_bind(),
        autoflush=False,
        autocommit=False,
        future=True,
        join_transaction_mode="create_savepoint",
    )
    monkeypatch.setattr("app.main.SessionLocal", factory)


def _browser_login(client, *, email: str, password: str = "password-1"):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def test_legal_content_schema_accepts_only_bounded_safe_structured_blocks():
    content = _content()

    assert [block.type for block in content.blocks] == [
        "heading",
        "paragraph",
        "bullet_list",
        "labelled_https_link",
    ]

    for invalid in (
        {"blocks": [{"type": "paragraph", "text": "<script>alert(1)</script>"}]},
        {"blocks": [{"type": "paragraph", "text": "**Markdown**"}]},
        {"blocks": [{"type": "labelled_https_link", "label": "Bad", "url": "javascript:alert(1)"}]},
        {"blocks": [{"type": "labelled_https_link", "label": "Bad", "url": "https://user:pass@example.test"}]},
        {"blocks": [{"type": "labelled_https_link", "label": "Bad", "url": "https://example.test/a b"}]},
        {"blocks": [{"type": "paragraph", "text": "Allowed", "unexpected": "field"}]},
    ):
        with pytest.raises(ValidationError):
            LegalDocumentContent.model_validate(invalid)

    with pytest.raises(ValidationError):
        OperatorLegalProfileUpdate(legal_name="<b>Operator</b>")
    with pytest.raises(ValidationError):
        OperatorLegalProfileUpdate(public_url="http://operator.example.test")
    with pytest.raises(ValidationError):
        OperatorLegalProfileUpdate(privacy_email="not-an-email")


def test_legal_markdown_round_trips_through_the_existing_block_contract():
    source = """## **Privacy** information

This paragraph has *italics*, **bold**, and ***both***.

- Synthetic *item* one
- Synthetic item two

| **Purpose** | Detail |
| --- | --- |
| Care | *Synthetic* detail |

[Operator contact page](https://operator.example.test/contact)
"""

    content = parse_legal_markdown(source)

    assert content.model_dump(mode="json") == {
        "blocks": [
            {
                "type": "heading",
                "text": [
                    {"type": "bold", "text": "Privacy"},
                    {"type": "text", "text": " information"},
                ],
            },
            {
                "type": "paragraph",
                "text": [
                    {"type": "text", "text": "This paragraph has "},
                    {"type": "italic", "text": "italics"},
                    {"type": "text", "text": ", "},
                    {"type": "bold", "text": "bold"},
                    {"type": "text", "text": ", and "},
                    {"type": "bold_italic", "text": "both"},
                    {"type": "text", "text": "."},
                ],
            },
            {
                "type": "bullet_list",
                "items": [
                    [
                        {"type": "text", "text": "Synthetic "},
                        {"type": "italic", "text": "item"},
                        {"type": "text", "text": " one"},
                    ],
                    "Synthetic item two",
                ],
            },
            {
                "type": "table",
                "headers": [
                    [{"type": "bold", "text": "Purpose"}],
                    "Detail",
                ],
                "rows": [
                    [
                        "Care",
                        [
                            {"type": "italic", "text": "Synthetic"},
                            {"type": "text", "text": " detail"},
                        ],
                    ]
                ],
            },
            {
                "type": "labelled_https_link",
                "label": "Operator contact page",
                "url": "https://operator.example.test/contact",
            },
        ]
    }
    assert parse_legal_markdown(legal_content_to_markdown(content)) == content


def test_legal_markdown_scrubs_harmless_formatting_without_removing_words():
    result = parse_legal_markdown_result(
        "Keep ~~removed styling~~, `inline code`, and ![useful alt text](https://example.test/image.png).\n\n---"
    )

    assert result.scrubbed_formatting is True
    assert result.content.blocks[0].text == (
        "Keep removed styling, inline code, and useful alt text."
    )
    canonical = parse_legal_markdown_result(legal_content_to_markdown(result.content))
    assert canonical.content == result.content
    assert canonical.scrubbed_formatting is False


def test_legal_markdown_supports_safe_inline_https_and_mailto_links():
    source = (
        "For privacy questions, email "
        "[privacy@openscribe.co.uk](mailto:privacy@openscribe.co.uk), or read "
        "[our privacy page](https://openscribe.co.uk/privacy)."
    )

    content = parse_legal_markdown(source)

    assert content.model_dump(mode="json") == {
        "blocks": [
            {
                "type": "paragraph",
                "text": [
                    {"type": "text", "text": "For privacy questions, email "},
                    {
                        "type": "link",
                        "text": "privacy@openscribe.co.uk",
                        "url": "mailto:privacy@openscribe.co.uk",
                    },
                    {"type": "text", "text": ", or read "},
                    {
                        "type": "link",
                        "text": "our privacy page",
                        "url": "https://openscribe.co.uk/privacy",
                    },
                    {"type": "text", "text": "."},
                ],
            }
        ]
    }
    assert parse_legal_markdown(legal_content_to_markdown(content)) == content


def test_legal_markdown_round_trip_keeps_link_after_multiline_text():
    source = """Information Commissioner's Office\\
Wycliffe House\\
Helpline: 0303 123 1113\\
[ICO complaints](https://www.ico.org.uk/make-a-complaint)
"""

    content = parse_legal_markdown(source)

    assert parse_legal_markdown(legal_content_to_markdown(content)) == content


def test_legal_document_accepts_long_notices_below_the_one_thousand_block_cap():
    source = "\n\n".join(f"Paragraph {index}." for index in range(170))

    content = parse_legal_markdown(source)

    assert len(content.blocks) == 170
    assert parse_legal_markdown(legal_content_to_markdown(content)) == content

    with pytest.raises(ValidationError, match="at most 1000 items"):
        LegalDocumentContent.model_validate(
            {
                "blocks": [
                    {"type": "paragraph", "text": "Synthetic text."}
                    for _ in range(1001)
                ]
            }
        )


def test_legal_table_contract_rejects_ragged_rows_and_html_inline_runs():
    with pytest.raises(ValidationError, match="same number of cells"):
        LegalDocumentContent.model_validate(
            {
                "blocks": [
                    {
                        "type": "table",
                        "headers": ["One", "Two"],
                        "rows": [["Only one"]],
                    }
                ]
            }
        )
    with pytest.raises(ValidationError, match="must not contain HTML"):
        LegalDocumentContent.model_validate(
            {
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": [{"type": "bold", "text": "<script>bad</script>"}],
                    }
                ]
            }
        )


def test_legal_block_projection_escapes_markdown_like_plain_text_without_changing_it():
    content = LegalDocumentContent.model_validate(
        {
            "blocks": [
                {"type": "heading", "text": "Use *literal* marks"},
                {"type": "paragraph", "text": "1. This stays text.\n\n--- also text"},
                {"type": "bullet_list", "items": ["A *literal* item\nwith another line"]},
            ]
        }
    )

    assert parse_legal_markdown(legal_content_to_markdown(content)) == content


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("<script>alert(1)</script>", "must not contain HTML"),
        ("1. Numbered", "Numbered lists are not supported"),
        ("- Outer\n  - Nested", "unsupported structure"),
        ("[Unsafe](http://operator.example.test)", "absolute HTTPS URL"),
        ("[Unsafe](javascript:alert(1))", "must not contain Markdown"),
        ("[Email](mailto:privacy@openscribe.co.uk?subject=Private)", "one plain email address"),
        ("```\ncode\n```", "Code blocks are not supported"),
    ],
)
def test_legal_markdown_rejects_content_outside_the_published_contract(source, message):
    with pytest.raises(LegalMarkdownError, match=message):
        parse_legal_markdown(source)


def test_profile_uses_optimistic_revision_and_requires_system_admin(db_session, make_user):
    admin = make_user(email="legal-admin@example.test", is_system_admin=True)
    ordinary_user = make_user(email="legal-user@example.test")
    payload = OperatorLegalProfileUpdate(legal_name="Synthetic Operator", public_url="https://operator.example.test")

    with pytest.raises(AppError, match="System-admin"):
        update_operator_legal_profile(db_session, actor=ordinary_user, payload=payload)

    created = update_operator_legal_profile(db_session, actor=admin, payload=payload)
    assert created.revision == 1

    updated = update_operator_legal_profile(
        db_session,
        actor=admin,
        payload=OperatorLegalProfileUpdate(
            expected_revision=created.revision,
            legal_name="Synthetic Operator Updated",
            public_url="https://operator.example.test",
        ),
    )
    assert updated.revision == 2

    with pytest.raises(AppError) as exc_info:
        update_operator_legal_profile(
            db_session,
            actor=admin,
            payload=OperatorLegalProfileUpdate(
                expected_revision=1,
                legal_name="Stale update",
                public_url="https://operator.example.test",
            ),
        )
    assert (exc_info.value.status_code, exc_info.value.code) == (409, "conflict")


def test_draft_update_detects_stale_edits_and_published_content_is_immutable(db_session, make_user):
    admin = make_user(email="legal-draft-admin@example.test", is_system_admin=True)
    draft = create_legal_document_draft(
        db_session, actor=admin, payload=_draft_payload(LegalDocumentKind.privacy)
    )
    changed = update_legal_document_draft(
        db_session,
        actor=admin,
        version_id=draft.id,
        payload=LegalDocumentDraftUpdate(
            expected_revision=draft.revision,
            effective_on=date(2026, 8, 6),
            content=_content(text="Updated synthetic legal notice text."),
        ),
    )
    assert changed.revision == 2

    with pytest.raises(AppError) as stale:
        update_legal_document_draft(
            db_session,
            actor=admin,
            version_id=draft.id,
            payload=LegalDocumentDraftUpdate(
                expected_revision=1,
                effective_on=date(2026, 8, 7),
                content=_content(),
            ),
        )
    assert (stale.value.status_code, stale.value.code) == (409, "conflict")

    published = publish_legal_document_draft(
        db_session, actor=admin, version_id=draft.id, expected_revision=changed.revision
    )
    assert published.state is LegalDocumentVersionState.published

    with pytest.raises(AppError) as immutable:
        update_legal_document_draft(
            db_session,
            actor=admin,
            version_id=draft.id,
            payload=LegalDocumentDraftUpdate(
                expected_revision=published.revision,
                effective_on=date(2026, 8, 8),
                content=_content(),
            ),
        )
    assert (immutable.value.status_code, immutable.value.code) == (409, "conflict")


def test_publish_supersedes_prior_version_and_rollback_creates_new_draft(db_session, make_user):
    admin = make_user(email="legal-publish-admin@example.test", is_system_admin=True)
    first = create_legal_document_draft(
        db_session, actor=admin, payload=_draft_payload(LegalDocumentKind.privacy, content=_content(text="Version one."))
    )
    first = publish_legal_document_draft(db_session, actor=admin, version_id=first.id, expected_revision=first.revision)
    second = create_legal_document_draft(
        db_session, actor=admin, payload=_draft_payload(LegalDocumentKind.privacy, content=_content(text="Version two."))
    )
    second = publish_legal_document_draft(db_session, actor=admin, version_id=second.id, expected_revision=second.revision)

    versions = list_legal_document_versions(db_session, kind=LegalDocumentKind.privacy)
    assert [(version.version_no, version.state) for version in versions] == [
        (2, LegalDocumentVersionState.published),
        (1, LegalDocumentVersionState.superseded),
    ]
    assert len([version for version in versions if version.state is LegalDocumentVersionState.published]) == 1
    assert first.superseded_at is not None

    rollback = create_legal_document_rollback_draft(
        db_session, actor=admin, source_version_id=first.id, effective_on=date(2026, 8, 9)
    )
    assert (rollback.version_no, rollback.state, rollback.blocks_json) == (
        3,
        LegalDocumentVersionState.draft,
        first.blocks_json,
    )
    refreshed_first = next(version for version in list_legal_document_versions(db_session, kind=LegalDocumentKind.privacy) if version.id == first.id)
    assert refreshed_first.state is LegalDocumentVersionState.superseded
    rollback_event = db_session.scalar(
        select(SecurityAuditEvent).where(
            SecurityAuditEvent.action == "legal_document_rollback_draft_created"
        )
    )
    assert rollback_event is not None
    assert rollback_event.details_json["version_no"] == 3


def test_legal_audit_events_never_contain_document_bodies(db_session, make_user):
    admin = make_user(email="legal-audit-admin@example.test", is_system_admin=True)
    body_marker = "SYNTHETIC_LEGAL_BODY_5cf21a"
    draft = create_legal_document_draft(
        db_session,
        actor=admin,
        payload=_draft_payload(LegalDocumentKind.privacy, content=_content(text=body_marker)),
    )
    publish_legal_document_draft(db_session, actor=admin, version_id=draft.id, expected_revision=draft.revision)

    events = list(
        db_session.scalars(
            select(SecurityAuditEvent)
            .where(SecurityAuditEvent.action.in_(("legal_document_draft_created", "legal_document_published")))
            .order_by(SecurityAuditEvent.created_at)
        )
    )
    assert [event.action for event in events] == ["legal_document_draft_created", "legal_document_published"]
    assert all(body_marker not in str(event.details_json) for event in events)
    assert all(event.details_json["document_kind"] == "privacy" for event in events)
    assert all(event.details_json["version_no"] == 1 for event in events)


def test_legal_change_rolls_back_when_transactional_audit_cannot_be_added(
    db_session, make_user, monkeypatch
):
    admin = make_user(email="legal-audit-atomic@example.test", is_system_admin=True)
    monkeypatch.setattr(
        "app.services.legal_content.add_security_event",
        lambda *_, **__: (_ for _ in ()).throw(RuntimeError("synthetic audit failure")),
    )

    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        create_legal_document_draft(
            db_session,
            actor=admin,
            payload=_draft_payload(LegalDocumentKind.privacy),
        )
    assert list_legal_document_versions(db_session, kind=LegalDocumentKind.privacy) == []


def test_setup_warnings_and_public_retrieval_validate_persisted_content(db_session, make_user):
    admin = make_user(email="legal-public-admin@example.test", is_system_admin=True)
    assert operator_legal_setup_warnings(db_session) == (
        "Operator legal identity is incomplete.",
        "Operator privacy or complaints contact is missing.",
        "The security.txt contact is missing.",
        "No privacy notice is published.",
        "No cookie and browser-storage notice is published.",
    )
    assert current_published_legal_document(db_session, kind=LegalDocumentKind.privacy) is None

    profile = update_operator_legal_profile(
        db_session,
        actor=admin,
        payload=OperatorLegalProfileUpdate(
            legal_name="Synthetic Operator",
            public_url="https://operator.example.test",
            privacy_email="privacy@operator.example.test",
            complaints_email="complaints@operator.example.test",
            security_contact="security@operator.example.test",
        ),
    )
    assert profile.revision == 1

    privacy = create_legal_document_draft(db_session, actor=admin, payload=_draft_payload(LegalDocumentKind.privacy))
    cookies = create_legal_document_draft(db_session, actor=admin, payload=_draft_payload(LegalDocumentKind.cookie_storage))
    privacy = publish_legal_document_draft(db_session, actor=admin, version_id=privacy.id, expected_revision=privacy.revision)
    cookies = publish_legal_document_draft(db_session, actor=admin, version_id=cookies.id, expected_revision=cookies.revision)
    assert operator_legal_setup_warnings(db_session) == ()

    current = current_published_legal_document(db_session, kind=LegalDocumentKind.privacy)
    assert current is not None
    version, content = current
    assert version.id == privacy.id
    assert content.blocks[1].text == "Synthetic legal notice text."

    privacy.blocks_json = [{"type": "paragraph", "text": "<script>invalid</script>"}]
    db_session.add(privacy)
    db_session.commit()
    with pytest.raises(AppError) as invalid:
        current_published_legal_document(db_session, kind=LegalDocumentKind.privacy)
    assert (invalid.value.status_code, invalid.value.code) == (500, "legal_content_invalid")


def test_unconfigured_public_legal_routes_and_security_txt_are_404_cookie_free(
    raw_client, db_session, monkeypatch
):
    _bind_legal_footer_queries_to_test_transaction(db_session, monkeypatch)
    for path in ("/privacy", "/cookies", "/terms", "/.well-known/security.txt"):
        response = raw_client.get(path)

        assert response.status_code == 404
        assert "set-cookie" not in response.headers

    security = raw_client.get("/.well-known/security.txt")
    assert "oscar@meddleapp.com" not in security.text
    assert "openscribe.co.uk" not in security.text


def test_public_legal_document_is_escaped_versioned_no_store_and_cookie_free(
    client, db_session, make_user, monkeypatch
):
    _bind_legal_footer_queries_to_test_transaction(db_session, monkeypatch)
    admin = make_user(email="legal-public-page-admin@example.test", is_system_admin=True)
    published = _publish(
        db_session,
        actor=admin,
        kind=LegalDocumentKind.privacy,
        content=LegalDocumentContent.model_validate(
            {
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": "Synthetic A < B & C notice.\nSecond approved line.",
                    }
                ]
            }
        ),
    )

    response = client.get("/privacy")

    assert response.status_code == 200
    assert "Version 1" in response.text
    assert f"Effective {published.effective_on.isoformat()}" in response.text
    assert "Synthetic A &lt; B &amp; C notice.\nSecond approved line." in response.text
    assert "Synthetic A < B & C notice." not in response.text
    assert 'class="legal-document__paragraph"' in response.text
    assert published.blocks_json[0]["text"] == "Synthetic A < B & C notice.\nSecond approved line."
    assert "white-space: pre-wrap" in Path("app/static/css/legal-content.css").read_text()
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Expires"] == "0"
    assert "set-cookie" not in response.headers
    assert "Content-Security-Policy" in response.headers


def test_legal_markdown_editor_uses_native_full_width_input_and_server_preview():
    script = Path("app/static/js/legal-content-editor.js").read_text()
    admin_template = Path("app/templates/admin_mockup.html").read_text()
    panel = Path("app/templates/_admin_legal_content_panel.html").read_text()

    assert 'name="markdown_source"' in panel
    assert "legal-markdown-editor" in panel
    assert 'role="tablist"' in panel
    assert 'aria-orientation="horizontal"' in panel
    assert 'id="legal-write-tab"' in panel
    assert 'aria-controls="legal-write-panel"' in panel
    assert 'id="legal-preview-tab"' in panel
    assert 'aria-controls="legal-preview-panel"' in panel
    assert 'aria-labelledby="legal-write-tab"' in panel
    assert 'aria-labelledby="legal-preview-tab"' in panel
    assert "min-height: clamp(32rem, 62vh, 58rem)" in admin_template
    assert 'href="/admin?tab=legal"' in admin_template
    assert "/admin/legal-content/preview" in script
    assert 'headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {}' in script
    assert 'response.headers.get("X-OpenScribe-Legal-Formatting")' in script
    assert "Some unsupported formatting was removed. Check the preview before publishing." in script
    assert "*italics*" in panel
    assert "**bold**" in panel
    assert "tables" in panel
    assert "Unsaved changes" in script
    assert 'event.key === "ArrowLeft"' in script
    assert 'event.key === "ArrowRight"' in script
    assert 'event.key === "Home"' in script
    assert 'event.key === "End"' in script
    assert "tab.tabIndex = selected ? 0 : -1" in script
    assert "CodeMirror" not in script
    assert "admin-legal-content.css" not in admin_template


def test_public_footer_links_only_published_documents(client, db_session, make_user, monkeypatch):
    _bind_legal_footer_queries_to_test_transaction(db_session, monkeypatch)
    admin = make_user(email="legal-footer-admin@example.test", is_system_admin=True)
    draft = create_legal_document_draft(
        db_session, actor=admin, payload=_draft_payload(LegalDocumentKind.cookie_storage)
    )
    _publish(db_session, actor=admin, kind=LegalDocumentKind.privacy)

    first = client.get("/login")
    assert first.status_code == 200
    assert 'href="/privacy"' in first.text
    assert 'href="/cookies"' not in first.text
    assert 'data-browser-storage-notice-version="unpublished:unpublished"' in first.text

    publish_legal_document_draft(
        db_session, actor=admin, version_id=draft.id, expected_revision=draft.revision
    )
    second = client.get("/login")
    assert 'href="/privacy"' in second.text
    assert 'href="/cookies"' in second.text
    assert 'href="/terms"' not in second.text
    assert 'data-browser-storage-notice-version="1:unpublished"' in second.text

    profile = update_operator_legal_profile(
        db_session,
        actor=admin,
        payload=OperatorLegalProfileUpdate(cookie_banner_summary="First synthetic summary."),
    )
    profile_only = client.get("/login")
    assert 'data-browser-storage-notice-version="1:1"' in profile_only.text

    profile = update_operator_legal_profile(
        db_session,
        actor=admin,
        payload=OperatorLegalProfileUpdate(
            expected_revision=profile.revision,
            cookie_banner_summary="Updated synthetic summary.",
        ),
    )
    summary_update = client.get("/login")
    assert 'data-browser-storage-notice-version="1:2"' in summary_update.text

    replacement = create_legal_document_draft(
        db_session, actor=admin, payload=_draft_payload(LegalDocumentKind.cookie_storage)
    )
    publish_legal_document_draft(
        db_session, actor=admin, version_id=replacement.id, expected_revision=replacement.revision
    )
    third = client.get("/login")
    assert 'data-browser-storage-notice-version="2:2"' in third.text


def test_security_txt_uses_only_configured_operator_contact_and_canonical(raw_client, db_session, make_user):
    admin = make_user(email="legal-security-admin@example.test", is_system_admin=True)
    update_operator_legal_profile(
        db_session,
        actor=admin,
        payload=OperatorLegalProfileUpdate(
            legal_name="Synthetic Operator",
            public_url="https://operator.example.test/base/",
            security_contact="security@operator.example.test",
        ),
    )

    response = raw_client.get("/.well-known/security.txt")

    assert response.status_code == 200
    assert "Contact: mailto:security@operator.example.test" in response.text
    assert "Canonical: https://operator.example.test/base/.well-known/security.txt" in response.text
    assert "oscar@meddleapp.com" not in response.text
    assert "openscribe.co.uk" not in response.text
    assert "set-cookie" not in response.headers
    assert response.headers["Cache-Control"] == "public, max-age=3600"


def test_admin_legal_writes_require_full_system_admin_session(client, db_session, make_user, monkeypatch):
    _bind_legal_footer_queries_to_test_transaction(db_session, monkeypatch)
    admin = make_user(
        email="legal-admin-write@example.com", is_system_admin=True, mfa_required=False, mfa_enabled=False
    )
    ordinary = make_user(
        email="legal-ordinary-write@example.com", mfa_required=False, mfa_enabled=False
    )
    form = {"legal_name": "Synthetic Operator", "public_url": "https://operator.example.test"}

    assert _browser_login(client, email=ordinary.email).status_code == 200
    client.get("/admin")
    forbidden = client.post(
        "/admin/legal-content/profile",
        data={**form, "_csrf_token": client.cookies["openscribe_csrf"]},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert forbidden.status_code == 403

    client.cookies.clear()
    login_response = _browser_login(client, email=admin.email)
    assert login_response.status_code == 200, login_response.text
    assert client.get("/admin").status_code == 200
    assert verify_csrf_token(
        submitted_token=client.cookies["openscribe_csrf"],
        raw_session_token=client.cookies[SESSION_COOKIE_NAME],
        anon_nonce=None,
    )
    saved = client.post(
        "/admin/legal-content/profile",
        data={**form, "_csrf_token": client.cookies["openscribe_csrf"]},
        headers={"Origin": "http://testserver", "X-CSRF-Token": client.cookies["openscribe_csrf"]},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert saved.headers["location"] == "/admin?tab=legal&kind=privacy&notice=profile_saved"


def test_system_admin_can_render_legal_content_admin_page(
    client, db_session, make_user, monkeypatch
):
    _bind_legal_footer_queries_to_test_transaction(db_session, monkeypatch)
    admin = make_user(
        email="legal-admin-page@example.com",
        is_system_admin=True,
        mfa_required=False,
        mfa_enabled=False,
    )

    assert _browser_login(client, email=admin.email).status_code == 200
    legacy = client.get("/admin/legal-content", follow_redirects=False)
    response = client.get("/admin?tab=legal")

    assert legacy.status_code == 307
    assert legacy.headers["location"] == "/admin?tab=legal&kind=privacy"
    assert response.status_code == 200
    assert "Legal content" in response.text
    assert 'class="nav-link active" href="/admin?tab=legal"' in response.text
    assert 'aria-label="Legal document kinds"' in response.text
    assert 'href="/admin?tab=legal&amp;kind=privacy"' in response.text
    assert 'name="markdown_source"' in response.text


def test_admin_markdown_preview_and_save_use_the_same_canonical_blocks(
    client, db_session, make_user, monkeypatch
):
    _bind_legal_footer_queries_to_test_transaction(db_session, monkeypatch)
    admin = make_user(
        email="legal-markdown-admin@example.com",
        is_system_admin=True,
        mfa_required=False,
        mfa_enabled=False,
    )
    source = """## **Privacy** information

First *approved* line. Email [privacy](mailto:privacy@openscribe.co.uk).
Second approved line.

- Synthetic item

| Purpose | Detail |
| --- | --- |
| Care | **Synthetic** detail |

[Contact](https://operator.example.test/contact)
"""

    assert _browser_login(client, email=admin.email).status_code == 200
    assert client.get("/admin?tab=legal").status_code == 200
    csrf_token = client.cookies["openscribe_csrf"]
    headers = {"Origin": "http://testserver", "X-CSRF-Token": csrf_token}

    preview = client.post(
        "/admin/legal-content/preview",
        data={"markdown_source": source, "_csrf_token": csrf_token},
        headers=headers,
    )

    assert preview.status_code == 200
    assert "<h2><strong>Privacy</strong> information</h2>" in preview.text
    assert (
        'First <em>approved</em> line. Email '
        '<a href="mailto:privacy@openscribe.co.uk">privacy</a>.\nSecond approved line.'
        in preview.text
    )
    assert '<table class="legal-document__table">' in preview.text
    assert "<strong>Synthetic</strong> detail" in preview.text
    assert "X-OpenScribe-Legal-Formatting" not in preview.headers
    assert list_legal_document_versions(db_session, kind=LegalDocumentKind.privacy) == []

    saved = client.post(
        "/admin/legal-content/drafts",
        data={
            "kind": "privacy",
            "effective_on": "2026-08-06",
            "markdown_source": source,
            "_csrf_token": csrf_token,
        },
        headers=headers,
        follow_redirects=False,
    )

    assert saved.status_code == 303
    version = list_legal_document_versions(db_session, kind=LegalDocumentKind.privacy)[0]
    assert version.blocks_json == parse_legal_markdown(source).model_dump(mode="json")["blocks"]
    assert str(version.id) in saved.headers["location"]


def test_admin_markdown_scrubs_harmless_formatting_and_warns_before_publication(
    client, db_session, make_user, monkeypatch
):
    _bind_legal_footer_queries_to_test_transaction(db_session, monkeypatch)
    admin = make_user(
        email="legal-markdown-scrub-admin@example.com",
        is_system_admin=True,
        mfa_required=False,
        mfa_enabled=False,
    )
    source = "Keep `all words` and ![the image description](https://example.test/image.png).\n\n---"

    assert _browser_login(client, email=admin.email).status_code == 200
    assert client.get("/admin?tab=legal").status_code == 200
    csrf_token = client.cookies["openscribe_csrf"]
    headers = {"Origin": "http://testserver", "X-CSRF-Token": csrf_token}

    preview = client.post(
        "/admin/legal-content/preview",
        data={"markdown_source": source, "_csrf_token": csrf_token},
        headers=headers,
    )

    assert preview.status_code == 200
    assert preview.headers["X-OpenScribe-Legal-Formatting"] == "scrubbed"
    assert "Keep all words and the image description." in preview.text
    assert "image.png" not in preview.text

    saved = client.post(
        "/admin/legal-content/drafts",
        data={
            "kind": "privacy",
            "effective_on": "2026-08-06",
            "markdown_source": source,
            "_csrf_token": csrf_token,
        },
        headers=headers,
        follow_redirects=False,
    )

    assert saved.status_code == 303
    assert "notice=draft_created_scrubbed" in saved.headers["location"]
    version = list_legal_document_versions(db_session, kind=LegalDocumentKind.privacy)[0]
    assert version.blocks_json == [
        {"type": "paragraph", "text": "Keep all words and the image description."}
    ]


def test_admin_markdown_validation_preserves_submitted_source(
    client, db_session, make_user, monkeypatch
):
    _bind_legal_footer_queries_to_test_transaction(db_session, monkeypatch)
    admin = make_user(
        email="legal-markdown-invalid@example.com",
        is_system_admin=True,
        mfa_required=False,
        mfa_enabled=False,
    )
    invalid_source = "1. A numbered claim that must remain visible."

    assert _browser_login(client, email=admin.email).status_code == 200
    assert client.get("/admin?tab=legal").status_code == 200
    csrf_token = client.cookies["openscribe_csrf"]
    response = client.post(
        "/admin/legal-content/drafts",
        data={
            "kind": "privacy",
            "effective_on": "2026-08-06",
            "markdown_source": invalid_source,
            "_csrf_token": csrf_token,
        },
        headers={"Origin": "http://testserver", "X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 422
    assert "Numbered lists are not supported" in response.text
    assert invalid_source in response.text
    assert list_legal_document_versions(db_session, kind=LegalDocumentKind.privacy) == []


def test_operator_profile_validation_names_field_and_preserves_safe_form_values(
    client, db_session, make_user, monkeypatch
):
    _bind_legal_footer_queries_to_test_transaction(db_session, monkeypatch)
    admin = make_user(
        email="legal-profile-validation@example.com",
        is_system_admin=True,
        mfa_required=False,
        mfa_enabled=False,
    )
    assert _browser_login(client, email=admin.email).status_code == 200
    assert client.get("/admin/legal-content").status_code == 200

    response = client.post(
        "/admin/legal-content/profile",
        data={
            "legal_name": "Synthetic Operator",
            "display_name": "Synthetic Scribe",
            "company_number": "00000000",
            "public_url": "http://operator.example.test",
            "privacy_email": "privacy@operator.example.test",
            "complaints_email": "complaints@operator.example.test",
            "security_contact": "security@operator.example.test",
            "postal_address": "1 Test Street, Test Town",
            "cookie_banner_summary": "Essential browser storage only.",
        },
    )

    assert response.status_code == 422
    assert "Public HTTPS URL must be an absolute HTTPS URL without credentials" in response.text
    assert 'value="http://operator.example.test"' in response.text
    assert 'value="Synthetic Operator"' in response.text
    assert "Operator profile is invalid" not in response.text


def test_admin_legal_writes_reject_missing_csrf(raw_client, db_session, make_user, monkeypatch):
    _bind_legal_footer_queries_to_test_transaction(db_session, monkeypatch)
    admin = make_user(
        email="legal-csrf-admin@example.com", is_system_admin=True, mfa_required=False, mfa_enabled=False
    )
    login = _browser_login(raw_client, email=admin.email)
    assert login.status_code == 200
    assert raw_client.get("/admin").status_code == 200

    rejected = raw_client.post(
        "/admin/legal-content/profile",
        data={"legal_name": "Synthetic Operator", "public_url": "https://operator.example.test"},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert rejected.status_code == 403


def test_banner_contract_and_admin_warnings_are_non_blocking(client, db_session, make_user, monkeypatch):
    _bind_legal_footer_queries_to_test_transaction(db_session, monkeypatch)
    admin = make_user(
        email="legal-warning-admin@example.com", is_system_admin=True, mfa_required=False, mfa_enabled=False
    )

    login_page = client.get("/login")
    assert login_page.status_code == 200
    assert "Cookies and browser storage" in login_page.text
    assert 'data-browser-storage-notice' in login_page.text
    assert 'data-browser-storage-notice-version="unpublished:unpublished"' in login_page.text
    assert 'data-browser-storage-dismiss' in login_page.text
    assert 'type="button"' in login_page.text
    banner_template = Path("app/templates/_legal_footer_banner.html").read_text()
    assert "data-browser-storage-notice-version" in banner_template
    assert "\n  hidden\n" not in banner_template
    banner_script = Path("app/static/js/legal-content-banner.js").read_text()
    assert "openscribe_browser_storage_notice_v1" in banner_script
    assert "window.localStorage.getItem" in banner_script
    assert "window.localStorage.setItem" in banner_script
    assert "dismissed:${noticeVersion}" in banner_script
    assert "catch (_error)" in banner_script
    assert "notice.hidden = true" in banner_script
    assert "consent" not in banner_script.lower()

    assert _browser_login(client, email=admin.email).status_code == 200
    admin_page = client.get("/admin")
    assert admin_page.status_code == 200
    assert "Operator legal identity is incomplete." in admin_page.text
    assert "No privacy notice is published." in admin_page.text
    assert "No cookie and browser-storage notice is published." in admin_page.text


def test_legal_retention_deletes_only_due_unheld_drafts_and_superseded_versions(
    db_session, make_user
):
    admin = make_user(email="legal-retention-admin@example.test", is_system_admin=True)
    now = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)

    due_draft = create_legal_document_draft(
        db_session, actor=admin, payload=_draft_payload(LegalDocumentKind.terms)
    )
    due_draft.updated_at = now - timedelta(days=366)

    first = _publish(db_session, actor=admin, kind=LegalDocumentKind.privacy)
    current = _publish(
        db_session,
        actor=admin,
        kind=LegalDocumentKind.privacy,
        content=_content(text="Current synthetic notice."),
    )
    first.superseded_at = datetime(2020, 8, 31, 12, tzinfo=timezone.utc)
    db_session.add_all([due_draft, first])
    db_session.commit()

    hold = place_legal_document_hold(
        db_session,
        actor=admin,
        version_id=first.id,
        reason="Synthetic litigation hold",
        now=now - timedelta(days=1),
    )
    assert expire_legal_document_versions(db_session, now=now) == 1
    assert db_session.get(type(due_draft), due_draft.id) is None
    assert db_session.get(type(first), first.id) is not None
    assert db_session.get(type(current), current.id) is not None

    release_legal_document_hold(db_session, actor=admin, hold_id=hold.id, now=now)
    assert expire_legal_document_versions(db_session, now=now) == 1
    assert db_session.get(type(first), first.id) is None
    assert db_session.get(type(current), current.id) is not None


def test_legal_hold_requires_system_admin_and_rejects_unsafe_reason(db_session, make_user):
    admin = make_user(email="legal-hold-admin@example.test", is_system_admin=True)
    member = make_user(email="legal-hold-member@example.test")
    draft = create_legal_document_draft(
        db_session, actor=admin, payload=_draft_payload(LegalDocumentKind.terms)
    )

    with pytest.raises(AppError, match="System-admin"):
        place_legal_document_hold(db_session, actor=member, version_id=draft.id, reason="Case 1")
    with pytest.raises(AppError, match="one line"):
        place_legal_document_hold(db_session, actor=admin, version_id=draft.id, reason="bad\nreason")
