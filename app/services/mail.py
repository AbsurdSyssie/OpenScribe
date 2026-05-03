import logging
import os
import sys
from dataclasses import dataclass, field
from typing import TextIO

import httpx

from app.errors import AppError
from app.services.vault import read_mail_resend_api_key

MAIL_TRANSPORT_DISABLED = "disabled"
MAIL_TRANSPORT_STDOUT = "stdout"
MAIL_TRANSPORT_RESEND = "resend"
MAIL_TRANSPORTS = {MAIL_TRANSPORT_DISABLED, MAIL_TRANSPORT_STDOUT, MAIL_TRANSPORT_RESEND}
MAIL_STDOUT_ALLOWED_ENVIRONMENTS = {"local", "dev", "development", "test", "testing"}
RESEND_EMAILS_URL = "https://api.resend.com/emails"
RESEND_TIMEOUT_SECONDS = 15.0

mail_logger = logging.getLogger("openscribe.mail")


@dataclass(frozen=True, slots=True)
class MailConfig:
    transport: str = MAIL_TRANSPORT_DISABLED
    app_environment: str = "production"
    app_public_url: str | None = None
    from_address: str | None = None
    from_name: str = "OpenScribe"
    reply_to: str | None = None
    resend_api_key: str | None = field(default=None, repr=False)
    resend_api_key_vault_ref: str | None = None


@dataclass(frozen=True, slots=True)
class MailMessage:
    purpose: str
    to_email: str
    subject: str
    text_body: str
    html_body: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class MailSendResult:
    status: str
    provider: str
    provider_message_id: str | None = None


def _optional_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _mail_app_environment_from_env() -> str:
    return (
        _optional_env("APP_ENV")
        or _optional_env("ENVIRONMENT")
        or _optional_env("ENV")
        or "production"
    ).lower()


def _normalize_app_environment(value: str | None) -> str:
    return (value or "production").strip().lower() or "production"


def load_mail_config_from_env() -> MailConfig:
    transport = (_optional_env("MAIL_TRANSPORT") or MAIL_TRANSPORT_DISABLED).lower()
    if transport not in MAIL_TRANSPORTS:
        raise AppError(
            500,
            "mail_transport_invalid",
            "Mail transport configuration is invalid",
            {"transport": transport},
        )
    return MailConfig(
        transport=transport,
        app_environment=_mail_app_environment_from_env(),
        app_public_url=_optional_env("APP_PUBLIC_URL"),
        from_address=_optional_env("MAIL_FROM_ADDRESS"),
        from_name=_optional_env("MAIL_FROM_NAME") or "OpenScribe",
        reply_to=_optional_env("MAIL_REPLY_TO"),
        resend_api_key=_optional_env("RESEND_API_KEY"),
        resend_api_key_vault_ref=_optional_env("RESEND_API_KEY_VAULT_REF"),
    )


def validate_mail_config(config: MailConfig) -> None:
    if config.transport == MAIL_TRANSPORT_DISABLED:
        return
    if config.transport == MAIL_TRANSPORT_STDOUT:
        environment = _normalize_app_environment(config.app_environment)
        if environment not in MAIL_STDOUT_ALLOWED_ENVIRONMENTS:
            raise AppError(
                500,
                "mail_stdout_not_allowed",
                "Stdout mail transport is only allowed in local or test environments",
                {"environment": environment},
            )
    if not config.from_address:
        raise AppError(500, "mail_from_missing", "Mail sender address is not configured")
    if not config.app_public_url:
        raise AppError(500, "mail_public_url_missing", "Public application URL is not configured")
    if config.transport == MAIL_TRANSPORT_RESEND and not (config.resend_api_key or config.resend_api_key_vault_ref):
        raise AppError(500, "mail_resend_secret_missing", "Resend API key is not configured")


def send_transactional_email(
    message: MailMessage,
    *,
    config: MailConfig | None = None,
    stdout: TextIO | None = None,
) -> MailSendResult:
    resolved_config = config or load_mail_config_from_env()
    if resolved_config.transport == MAIL_TRANSPORT_DISABLED:
        mail_logger.info(
            "mail_skipped_disabled",
            extra={"event": "mail_skipped_disabled", "purpose": message.purpose},
        )
        return MailSendResult(status="skipped", provider=MAIL_TRANSPORT_DISABLED)

    validate_mail_config(resolved_config)
    if resolved_config.transport == MAIL_TRANSPORT_STDOUT:
        return _send_stdout(message, resolved_config, stdout=stdout)
    if resolved_config.transport == MAIL_TRANSPORT_RESEND:
        return _send_resend(message, resolved_config)
    raise AppError(500, "mail_transport_invalid", "Mail transport configuration is invalid")


def _send_stdout(message: MailMessage, config: MailConfig, *, stdout: TextIO | None = None) -> MailSendResult:
    stream = stdout or sys.stdout
    print("----- OpenScribe transactional email -----", file=stream)
    print(f"Purpose: {message.purpose}", file=stream)
    print(f"From: {config.from_name} <{config.from_address}>", file=stream)
    if config.reply_to:
        print(f"Reply-To: {config.reply_to}", file=stream)
    print(f"To: {message.to_email}", file=stream)
    print(f"Subject: {message.subject}", file=stream)
    print("", file=stream)
    print(message.text_body, file=stream)
    if message.html_body:
        print("", file=stream)
        print("HTML:", file=stream)
        print(message.html_body, file=stream)
    print("----- end email -----", file=stream)
    mail_logger.info(
        "mail_sent_stdout",
        extra={"event": "mail_sent_stdout", "purpose": message.purpose},
    )
    return MailSendResult(status="sent", provider=MAIL_TRANSPORT_STDOUT)


def _send_resend(message: MailMessage, config: MailConfig) -> MailSendResult:
    api_key = _resolve_resend_api_key(config)
    payload = _resend_payload(message, config)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "openscribe/1.0",
    }
    if message.idempotency_key:
        headers["Idempotency-Key"] = message.idempotency_key
    try:
        response = httpx.post(RESEND_EMAILS_URL, json=payload, headers=headers, timeout=RESEND_TIMEOUT_SECONDS)
    except httpx.TimeoutException as exc:
        raise AppError(502, "mail_resend_timeout", "Resend mail request timed out") from exc
    except httpx.HTTPError as exc:
        raise AppError(502, "mail_resend_unavailable", "Resend mail service is unavailable") from exc

    if response.status_code >= 400:
        code = _resend_error_code(response)
        error_code = "mail_resend_send_failed"
        if response.status_code in {401, 403}:
            error_code = "mail_resend_auth_failed"
        elif response.status_code == 429:
            error_code = "mail_resend_rate_limited"
        raise AppError(
            502,
            error_code,
            "Resend mail send failed",
            {"provider_status_code": response.status_code, "provider_error_code": code},
        )

    provider_message_id = _resend_message_id(response)
    mail_logger.info(
        "mail_sent_resend",
        extra={"event": "mail_sent_resend", "purpose": message.purpose, "provider_message_id": provider_message_id},
    )
    return MailSendResult(status="sent", provider=MAIL_TRANSPORT_RESEND, provider_message_id=provider_message_id)


def _resolve_resend_api_key(config: MailConfig) -> str:
    if config.resend_api_key:
        return config.resend_api_key
    if config.resend_api_key_vault_ref:
        return read_mail_resend_api_key(secret_ref=config.resend_api_key_vault_ref)
    raise AppError(500, "mail_resend_secret_missing", "Resend API key is not configured")


def _resend_payload(message: MailMessage, config: MailConfig) -> dict[str, object]:
    payload: dict[str, object] = {
        "from": _format_sender(config),
        "to": [message.to_email],
        "subject": message.subject,
        "text": message.text_body,
    }
    if message.html_body:
        payload["html"] = message.html_body
    if config.reply_to:
        payload["reply_to"] = config.reply_to
    return payload


def _format_sender(config: MailConfig) -> str:
    if config.from_name:
        return f"{config.from_name} <{config.from_address}>"
    return str(config.from_address)


def _resend_message_id(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    message_id = payload.get("id") if isinstance(payload, dict) else None
    return str(message_id) if message_id else None


def _resend_error_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    name = payload.get("name") or payload.get("code")
    return str(name) if name else None
