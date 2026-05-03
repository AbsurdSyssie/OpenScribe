from io import StringIO

import httpx
import pytest

from app.errors import AppError
from app.services.mail import (
    MAIL_TRANSPORT_DISABLED,
    MAIL_TRANSPORT_RESEND,
    MAIL_TRANSPORT_STDOUT,
    RESEND_EMAILS_URL,
    MailConfig,
    MailMessage,
    load_mail_config_from_env,
    send_transactional_email,
    validate_mail_config,
)


def _message() -> MailMessage:
    return MailMessage(
        purpose="account_activation",
        to_email="user@example.com",
        subject="Set up your OpenScribe account",
        text_body="Use this setup link.",
        html_body="<p>Use this setup link.</p>",
    )


def test_mail_config_defaults_to_disabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MAIL_TRANSPORT", raising=False)
    monkeypatch.delenv("MAIL_FROM_ADDRESS", raising=False)
    monkeypatch.delenv("APP_PUBLIC_URL", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("ENV", raising=False)

    config = load_mail_config_from_env()

    assert config.transport == MAIL_TRANSPORT_DISABLED
    assert config.app_environment == "production"
    validate_mail_config(config)


def test_mail_config_reads_app_environment_aliases(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "testing")

    config = load_mail_config_from_env()

    assert config.app_environment == "testing"


def test_invalid_mail_transport_is_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MAIL_TRANSPORT", "smtp")

    with pytest.raises(AppError) as exc_info:
        load_mail_config_from_env()

    assert exc_info.value.code == "mail_transport_invalid"


def test_stdout_transport_is_rejected_outside_local_or_test():
    config = MailConfig(
        transport=MAIL_TRANSPORT_STDOUT,
        app_environment="production",
        app_public_url="https://openscribe.example.com",
        from_address="no-reply@example.com",
    )

    with pytest.raises(AppError) as exc_info:
        validate_mail_config(config)

    assert exc_info.value.code == "mail_stdout_not_allowed"
    assert exc_info.value.details == {"environment": "production"}


def test_stdout_transport_requires_sender_and_public_url():
    config = MailConfig(transport=MAIL_TRANSPORT_STDOUT, app_environment="test")

    with pytest.raises(AppError) as exc_info:
        validate_mail_config(config)

    assert exc_info.value.code == "mail_from_missing"

    with pytest.raises(AppError) as exc_info:
        validate_mail_config(MailConfig(transport=MAIL_TRANSPORT_STDOUT, app_environment="test", from_address="no-reply@example.com"))

    assert exc_info.value.code == "mail_public_url_missing"


def test_stdout_transport_writes_local_email():
    stream = StringIO()
    config = MailConfig(
        transport=MAIL_TRANSPORT_STDOUT,
        app_environment="test",
        app_public_url="https://openscribe.example.com",
        from_address="no-reply@example.com",
        from_name="OpenScribe Test",
        reply_to="support@example.com",
    )

    result = send_transactional_email(_message(), config=config, stdout=stream)

    output = stream.getvalue()
    assert result.status == "sent"
    assert result.provider == MAIL_TRANSPORT_STDOUT
    assert "Purpose: account_activation" in output
    assert "From: OpenScribe Test <no-reply@example.com>" in output
    assert "Reply-To: support@example.com" in output
    assert "To: user@example.com" in output
    assert "Use this setup link." in output


def test_disabled_transport_skips_without_delivery():
    stream = StringIO()

    result = send_transactional_email(_message(), config=MailConfig(), stdout=stream)

    assert result.status == "skipped"
    assert result.provider == MAIL_TRANSPORT_DISABLED
    assert stream.getvalue() == ""


def test_resend_config_requires_secret_and_hides_env_key_from_repr(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MAIL_TRANSPORT", MAIL_TRANSPORT_RESEND)
    monkeypatch.setenv("MAIL_FROM_ADDRESS", "no-reply@example.com")
    monkeypatch.setenv("APP_PUBLIC_URL", "https://openscribe.example.com")
    monkeypatch.setenv("RESEND_API_KEY", "re_secret")

    config = load_mail_config_from_env()

    validate_mail_config(config)
    assert config.resend_api_key == "re_secret"
    assert "re_secret" not in repr(config)

    with pytest.raises(AppError) as exc_info:
        validate_mail_config(
            MailConfig(
                transport=MAIL_TRANSPORT_RESEND,
                from_address="no-reply@example.com",
                app_public_url="https://openscribe.example.com",
            )
        )

    assert exc_info.value.code == "mail_resend_secret_missing"


def test_resend_transport_sends_expected_payload(monkeypatch: pytest.MonkeyPatch):
    captured = {}

    def fake_post(url, *, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return httpx.Response(200, json={"id": "email-123"}, request=httpx.Request("POST", url))

    monkeypatch.setattr("app.services.mail.httpx.post", fake_post)
    config = MailConfig(
        transport=MAIL_TRANSPORT_RESEND,
        app_public_url="https://openscribe.example.com",
        from_address="no-reply@example.com",
        from_name="OpenScribe Test",
        reply_to="support@example.com",
        resend_api_key="re_secret",
    )
    message = MailMessage(
        purpose="password_reset",
        to_email="user@example.com",
        subject="Reset your password",
        text_body="Reset link.",
        html_body="<p>Reset link.</p>",
        idempotency_key="mail-test-1",
    )

    result = send_transactional_email(message, config=config)

    assert result.status == "sent"
    assert result.provider == MAIL_TRANSPORT_RESEND
    assert result.provider_message_id == "email-123"
    assert captured["url"] == RESEND_EMAILS_URL
    assert captured["json"] == {
        "from": "OpenScribe Test <no-reply@example.com>",
        "to": ["user@example.com"],
        "subject": "Reset your password",
        "text": "Reset link.",
        "html": "<p>Reset link.</p>",
        "reply_to": "support@example.com",
    }
    assert captured["headers"]["Authorization"] == "Bearer re_secret"
    assert captured["headers"]["User-Agent"] == "openscribe/1.0"
    assert captured["headers"]["Idempotency-Key"] == "mail-test-1"
    assert captured["timeout"] == 15.0


def test_resend_transport_maps_provider_errors_without_secret(monkeypatch: pytest.MonkeyPatch):
    def fake_post(url, *, json, headers, timeout):
        return httpx.Response(
            403,
            json={"name": "invalid_api_key", "message": "bad key"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("app.services.mail.httpx.post", fake_post)
    config = MailConfig(
        transport=MAIL_TRANSPORT_RESEND,
        app_public_url="https://openscribe.example.com",
        from_address="no-reply@example.com",
        resend_api_key="re_secret",
    )

    with pytest.raises(AppError) as exc_info:
        send_transactional_email(_message(), config=config)

    assert exc_info.value.code == "mail_resend_auth_failed"
    assert exc_info.value.details == {"provider_status_code": 403, "provider_error_code": "invalid_api_key"}
    assert "re_secret" not in repr(exc_info.value)


def test_resend_transport_can_read_api_key_from_vault_ref(monkeypatch: pytest.MonkeyPatch):
    captured = {}

    monkeypatch.setattr("app.services.mail.read_mail_resend_api_key", lambda *, secret_ref: "re_from_vault")

    def fake_post(url, *, json, headers, timeout):
        captured["authorization"] = headers["Authorization"]
        return httpx.Response(200, json={"id": "email-456"}, request=httpx.Request("POST", url))

    monkeypatch.setattr("app.services.mail.httpx.post", fake_post)
    config = MailConfig(
        transport=MAIL_TRANSPORT_RESEND,
        app_public_url="https://openscribe.example.com",
        from_address="no-reply@example.com",
        resend_api_key_vault_ref="secret:openscribe/mail/resend",
    )

    result = send_transactional_email(_message(), config=config)

    assert result.provider_message_id == "email-456"
    assert captured["authorization"] == "Bearer re_from_vault"
