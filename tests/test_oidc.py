import asyncio
import hashlib
import logging
from dataclasses import replace
from datetime import timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from joserfc import jwt
from joserfc.jwk import RSAKey
from sqlalchemy import func, select
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from app.errors import AppError
from app.main import redact_oidc_callback_query_from_access_log
from app.models import (
    OidcAuthorizationRequest,
    SessionStatus,
    UserOidcIdentity,
    UserSession,
    UserStatus,
    utcnow,
)
from app.services.auth import SESSION_COOKIE_NAME, session_token_hash
from app.services.csrf import CSRF_COOKIE_NAME
from app.services import oidc, vault
from app.services.oidc import (
    OIDC_CODE_VERIFIER_COOKIE_NAME,
    OIDC_STATE_COOKIE_NAME,
    OidcAuthorizationStart,
    OidcConfig,
    OidcProtocolError,
    OidcVerifiedIdentity,
)


OIDC_ENV_NAMES = (
    "OIDC_ENABLED",
    "OIDC_PROVIDER_KEY",
    "OIDC_PROVIDER_NAME",
    "OIDC_ISSUER",
    "OIDC_DISCOVERY_URL",
    "OIDC_CLIENT_ID",
    "OIDC_CLIENT_SECRET",
    "OIDC_CLIENT_SECRET_VAULT_REF",
    "OIDC_SUBJECT_HASH_SECRET",
    "OIDC_SUBJECT_HASH_SECRET_VAULT_REF",
    "OIDC_CLIENT_AUTH_METHOD",
    "OIDC_REDIRECT_URI",
    "OIDC_SCOPES",
    "OIDC_RESPONSE_MODE",
    "OIDC_ALLOWED_ID_TOKEN_ALGORITHMS",
    "OIDC_ACR_VALUES",
    "OIDC_REQUIRED_ACR_VALUES",
    "GOOGLE_OIDC_ENABLED",
    "GOOGLE_OIDC_CLIENT_ID",
    "GOOGLE_OIDC_CLIENT_SECRET",
    "GOOGLE_OIDC_CLIENT_SECRET_VAULT_REF",
    "GOOGLE_OIDC_REDIRECT_URI",
    "GOOGLE_OIDC_RESPONSE_MODE",
    "MICROSOFT_OIDC_ENABLED",
    "MICROSOFT_OIDC_CLIENT_ID",
    "MICROSOFT_OIDC_CLIENT_SECRET",
    "MICROSOFT_OIDC_CLIENT_SECRET_VAULT_REF",
    "MICROSOFT_OIDC_REDIRECT_URI",
    "MICROSOFT_OIDC_RESPONSE_MODE",
    "MICROSOFT_ALLOWED_EMAIL_DOMAINS",
    "CIS2_OIDC_ENABLED",
    "CIS2_OIDC_ISSUER",
    "CIS2_OIDC_DISCOVERY_URL",
    "CIS2_OIDC_CLIENT_ID",
    "CIS2_OIDC_CLIENT_SECRET",
    "CIS2_OIDC_CLIENT_SECRET_VAULT_REF",
    "CIS2_OIDC_CLIENT_AUTH_METHOD",
    "CIS2_OIDC_REDIRECT_URI",
    "CIS2_OIDC_SCOPES",
    "CIS2_OIDC_RESPONSE_MODE",
    "CIS2_OIDC_ALLOWED_ID_TOKEN_ALGORITHMS",
    "CIS2_OIDC_ACR_VALUES",
    "CIS2_OIDC_REQUIRED_ACR_VALUES",
)


class _VaultResponse:
    def __init__(self, status_code: int, payload: object | None = None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def clear_oidc_vault_caches():
    vault.get_or_create_platform_oidc_subject_hash_secret.cache_clear()
    vault.read_oidc_client_secret.cache_clear()
    yield
    vault.get_or_create_platform_oidc_subject_hash_secret.cache_clear()
    vault.read_oidc_client_secret.cache_clear()


def _set_oidc_environment(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    for name in OIDC_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    values = {
        "OIDC_ENABLED": "true",
        "OIDC_PROVIDER_KEY": "synthetic",
        "OIDC_PROVIDER_NAME": "Synthetic identity",
        "OIDC_ISSUER": "https://identity.invalid",
        "OIDC_CLIENT_ID": "openscribe-test",
        "OIDC_CLIENT_SECRET": "synthetic-secret",
        "OIDC_SUBJECT_HASH_SECRET": "synthetic-subject-hash-secret-32-bytes",
        "OIDC_CLIENT_AUTH_METHOD": "client_secret_basic",
        "OIDC_REDIRECT_URI": "https://openscribe.invalid/auth/oidc/synthetic/callback",
        "OIDC_RESPONSE_MODE": "form_post",
        "OIDC_ALLOWED_ID_TOKEN_ALGORITHMS": "RS256",
    }
    values.update(overrides)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _set_google_microsoft_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in OIDC_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    values = {
        "OIDC_SUBJECT_HASH_SECRET": "synthetic-subject-hash-secret-32-bytes",
        "GOOGLE_OIDC_ENABLED": "true",
        "GOOGLE_OIDC_CLIENT_ID": "google-client",
        "GOOGLE_OIDC_CLIENT_SECRET": "google-secret",
        "GOOGLE_OIDC_REDIRECT_URI": "http://testserver/auth/oidc/google/callback",
        "GOOGLE_OIDC_RESPONSE_MODE": "query",
        "MICROSOFT_OIDC_ENABLED": "true",
        "MICROSOFT_OIDC_CLIENT_ID": "microsoft-client",
        "MICROSOFT_OIDC_CLIENT_SECRET": "microsoft-secret",
        "MICROSOFT_OIDC_REDIRECT_URI": "http://testserver/auth/oidc/microsoft/callback",
        "MICROSOFT_OIDC_RESPONSE_MODE": "query",
        "MICROSOFT_ALLOWED_EMAIL_DOMAINS": "nhs.net,nhs.uk,*.nhs.uk",
    }
    monkeypatch.setenv("APP_ENV", "test")
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _set_cis2_environment(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    for name in OIDC_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    values = {
        "OIDC_SUBJECT_HASH_SECRET": "synthetic-subject-hash-secret-32-bytes",
        "CIS2_OIDC_ENABLED": "true",
        "CIS2_OIDC_ISSUER": "https://care-identity.invalid",
        "CIS2_OIDC_CLIENT_ID": "cis2-client",
        "CIS2_OIDC_CLIENT_SECRET": "cis2-secret",
        "CIS2_OIDC_REDIRECT_URI": "https://openscribe.invalid/auth/oidc/cis2/callback",
        "CIS2_OIDC_RESPONSE_MODE": "query",
        "CIS2_OIDC_ALLOWED_ID_TOKEN_ALGORITHMS": "RS256",
    }
    values.update(overrides)
    monkeypatch.setenv("APP_ENV", "test")
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _config(*, response_mode: str = "query") -> OidcConfig:
    return OidcConfig(
        provider_key="synthetic",
        provider_name="Synthetic identity",
        issuer="https://identity.invalid",
        discovery_url="https://identity.invalid/.well-known/openid-configuration",
        client_id="openscribe-test",
        client_secret="synthetic-secret",
        subject_hash_secret=b"synthetic-subject-hash-secret-32-bytes",
        client_auth_method="client_secret_basic",
        redirect_uri="https://openscribe.invalid/auth/oidc/synthetic/callback",
        scopes=("openid",),
        response_mode=response_mode,
        allowed_signing_algorithms=("RS256",),
        requested_acr_values=(),
        required_acr_values=frozenset(),
    )


def _authorization_request(
    db_session,
    *,
    state: str,
    code_verifier: str,
    provider_key: str = "synthetic",
    purpose: str = "login",
    user_id=None,
    user_session_id=None,
) -> OidcAuthorizationRequest:
    transaction = OidcAuthorizationRequest(
        provider_key=provider_key,
        state_hash=hashlib.sha256(state.encode("utf-8")).hexdigest(),
        nonce="synthetic-nonce",
        code_verifier_hash=hashlib.sha256(code_verifier.encode("utf-8")).hexdigest(),
        purpose=purpose,
        user_id=user_id,
        user_session_id=user_session_id,
        expires_at=utcnow() + timedelta(minutes=5),
    )
    db_session.add(transaction)
    db_session.commit()
    return transaction


def test_oidc_disabled_does_not_expose_a_partially_configured_provider(monkeypatch):
    for name in OIDC_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OIDC_ENABLED", "false")
    monkeypatch.setenv("OIDC_ISSUER", "not a URL")

    assert oidc.oidc_config() is None


def test_no_enabled_oidc_provider_does_not_resolve_vault_secrets(monkeypatch):
    for name in OIDC_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    def unexpected_vault_call(**_kwargs):
        raise AssertionError("Vault must not be read when OIDC is disabled")

    monkeypatch.setattr(
        oidc,
        "get_or_create_platform_oidc_subject_hash_secret",
        unexpected_vault_call,
    )
    monkeypatch.setattr(oidc, "read_oidc_client_secret", unexpected_vault_call)

    assert oidc.oidc_configs() == ()


def test_oidc_environment_secrets_take_precedence_without_vault(monkeypatch):
    subject_secret = "environment-subject-hash-secret-32-bytes"
    client_secret = "environment-client-secret"
    _set_oidc_environment(
        monkeypatch,
        OIDC_SUBJECT_HASH_SECRET=subject_secret,
        OIDC_CLIENT_SECRET=client_secret,
        OIDC_SUBJECT_HASH_SECRET_VAULT_REF="secret:ignored/subject",
        OIDC_CLIENT_SECRET_VAULT_REF="secret:ignored/client",
    )

    def unexpected_vault_call(**_kwargs):
        raise AssertionError("environment secrets must take precedence over Vault")

    monkeypatch.setattr(
        oidc,
        "get_or_create_platform_oidc_subject_hash_secret",
        unexpected_vault_call,
    )
    monkeypatch.setattr(oidc, "read_oidc_client_secret", unexpected_vault_call)

    config = oidc.oidc_config("synthetic")

    assert config is not None
    assert config.subject_hash_secret == subject_secret.encode()
    assert config.client_secret == client_secret
    assert subject_secret not in repr(config)
    assert client_secret not in repr(config)


def test_builtin_oidc_providers_use_shared_default_subject_key_and_scoped_client_refs(
    monkeypatch,
):
    _set_google_microsoft_environment(monkeypatch)
    monkeypatch.delenv("OIDC_SUBJECT_HASH_SECRET", raising=False)
    monkeypatch.delenv("OIDC_SUBJECT_HASH_SECRET_VAULT_REF", raising=False)
    monkeypatch.delenv("GOOGLE_OIDC_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MICROSOFT_OIDC_CLIENT_SECRET", raising=False)
    monkeypatch.setenv(
        "GOOGLE_OIDC_CLIENT_SECRET_VAULT_REF",
        "secret:openscribe/oidc/google",
    )
    monkeypatch.setenv(
        "MICROSOFT_OIDC_CLIENT_SECRET_VAULT_REF",
        "secret:openscribe/oidc/microsoft",
    )
    subject_calls = []
    client_calls = []

    def subject_secret(*, secret_ref=None):
        subject_calls.append(secret_ref)
        return "vault-subject-hash-secret-at-least-32-bytes"

    def client_secret(*, secret_ref, provider_key):
        client_calls.append((secret_ref, provider_key))
        return f"vault-{provider_key}-client-secret"

    monkeypatch.setattr(
        oidc,
        "get_or_create_platform_oidc_subject_hash_secret",
        subject_secret,
    )
    monkeypatch.setattr(oidc, "read_oidc_client_secret", client_secret)

    configs = oidc.oidc_configs()

    assert subject_calls == [None]
    assert client_calls == [
        ("secret:openscribe/oidc/google", "google"),
        ("secret:openscribe/oidc/microsoft", "microsoft"),
    ]
    assert [config.client_secret for config in configs] == [
        "vault-google-client-secret",
        "vault-microsoft-client-secret",
    ]
    assert len({config.subject_hash_secret for config in configs}) == 1


def test_cis2_disabled_does_not_expose_a_partially_configured_provider(monkeypatch):
    for name in OIDC_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CIS2_OIDC_ENABLED", "false")
    monkeypatch.setenv("CIS2_OIDC_ISSUER", "https://care-identity.invalid")
    monkeypatch.setenv("CIS2_OIDC_CLIENT_ID", "cis2-client")
    monkeypatch.setenv("CIS2_OIDC_CLIENT_SECRET_VAULT_REF", "secret:openscribe/oidc/cis2")

    assert oidc.oidc_configs() == ()


def test_cis2_config_uses_its_dedicated_secure_defaults(monkeypatch):
    _set_cis2_environment(monkeypatch, CIS2_OIDC_CLIENT_SECRET="")
    monkeypatch.setenv("CIS2_OIDC_CLIENT_SECRET_VAULT_REF", "secret:openscribe/oidc/cis2")
    calls = []

    def client_secret(*, secret_ref, provider_key):
        calls.append((secret_ref, provider_key))
        return "vault-cis2-client-secret"

    monkeypatch.setattr(oidc, "read_oidc_client_secret", client_secret)

    config = oidc.oidc_config("cis2")

    assert config is not None
    assert config.provider_key == "cis2"
    assert config.provider_name == "Care Identity"
    assert config.discovery_url == "https://care-identity.invalid/.well-known/openid-configuration"
    assert config.client_auth_method == "client_secret_post"
    assert config.client_secret == "vault-cis2-client-secret"
    assert config.scopes == ("openid",)
    assert config.allowed_signing_algorithms == ("RS256",)
    assert calls == [("secret:openscribe/oidc/cis2", "cis2")]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"CIS2_OIDC_ISSUER": ""}, "CIS2_OIDC_ISSUER is required"),
        ({"CIS2_OIDC_CLIENT_ID": ""}, "CIS2_OIDC_CLIENT_ID"),
        ({"CIS2_OIDC_CLIENT_AUTH_METHOD": "client_secret_basic"}, "must be client_secret_post"),
        ({"CIS2_OIDC_RESPONSE_MODE": "form_post"}, "must be query"),
        (
            {"CIS2_OIDC_CLIENT_SECRET": ""},
            "CIS2_OIDC_CLIENT_SECRET.*CIS2_OIDC_CLIENT_SECRET_VAULT_REF.*required",
        ),
        ({"CIS2_OIDC_REDIRECT_URI": ""}, "CIS2_OIDC_REDIRECT_URI is required"),
        ({"CIS2_OIDC_REDIRECT_URI": "https://openscribe.invalid/auth/oidc/callback"}, "path must be /auth/oidc/cis2/callback"),
        ({"CIS2_OIDC_SCOPES": "profile"}, "CIS2_OIDC_SCOPES must contain openid"),
        ({"CIS2_OIDC_ALLOWED_ID_TOKEN_ALGORITHMS": "HS256"}, "supported asymmetric algorithms"),
        ({"CIS2_OIDC_ACR_VALUES": "aal2", "CIS2_OIDC_REQUIRED_ACR_VALUES": "aal3"}, "must be a subset"),
    ],
)
def test_cis2_configuration_fails_closed(monkeypatch, overrides, message):
    _set_cis2_environment(monkeypatch, **overrides)

    with pytest.raises(RuntimeError, match=message):
        oidc.oidc_config("cis2")


def test_cis2_requires_https_callback_in_production(monkeypatch):
    _set_cis2_environment(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CIS2_OIDC_REDIRECT_URI", "http://openscribe.invalid/auth/oidc/cis2/callback")

    with pytest.raises(RuntimeError, match="absolute HTTPS URL"):
        oidc.oidc_config("cis2")


def test_cis2_uses_supported_query_response_mode_in_production(monkeypatch):
    _set_cis2_environment(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")

    config = oidc.oidc_config("cis2")

    assert config is not None
    assert config.response_mode == "query"


def test_custom_oidc_provider_reads_its_scoped_vault_client_secret(monkeypatch):
    _set_oidc_environment(monkeypatch, OIDC_CLIENT_SECRET="")
    monkeypatch.setenv(
        "OIDC_CLIENT_SECRET_VAULT_REF",
        "secret:openscribe/oidc/synthetic",
    )
    calls = []

    def client_secret(*, secret_ref, provider_key):
        calls.append((secret_ref, provider_key))
        return "vault-custom-client-secret"

    monkeypatch.setattr(oidc, "read_oidc_client_secret", client_secret)

    config = oidc.oidc_config("synthetic")

    assert config is not None
    assert config.client_secret == "vault-custom-client-secret"
    assert calls == [
        ("secret:openscribe/oidc/synthetic", "synthetic"),
    ]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"OIDC_PROVIDER_KEY": "bad key"}, "OIDC_PROVIDER_KEY"),
        ({"OIDC_ISSUER": ""}, "OIDC_ISSUER is required"),
        (
            {"OIDC_CLIENT_SECRET": ""},
            "OIDC_CLIENT_SECRET.*OIDC_CLIENT_SECRET_VAULT_REF.*required",
        ),
        ({"OIDC_SUBJECT_HASH_SECRET": "too-short"}, "OIDC_SUBJECT_HASH_SECRET"),
        ({"OIDC_ISSUER": f"https://identity.invalid/{'a' * 2048}"}, "must not exceed 2048"),
        ({"OIDC_REDIRECT_URI": "https://openscribe.invalid/callback?leak=true"}, "must not contain a query string"),
        ({"OIDC_SCOPES": "profile email"}, "must contain openid"),
        ({"OIDC_ALLOWED_ID_TOKEN_ALGORITHMS": "HS256"}, "supported asymmetric algorithms"),
        ({"OIDC_ACR_VALUES": "loa1", "OIDC_REQUIRED_ACR_VALUES": "loa2"}, "must be a subset"),
    ],
)
def test_oidc_configuration_fails_closed(monkeypatch, overrides, message):
    monkeypatch.setenv("APP_ENV", "test")
    _set_oidc_environment(monkeypatch, **overrides)

    with pytest.raises(RuntimeError, match=message):
        oidc.oidc_config()


def test_production_oidc_requires_form_post_and_https(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    _set_oidc_environment(monkeypatch, OIDC_RESPONSE_MODE="query")

    with pytest.raises(RuntimeError, match="form_post is required in production"):
        oidc.oidc_config()

    _set_oidc_environment(
        monkeypatch,
        OIDC_RESPONSE_MODE="form_post",
        OIDC_ISSUER="http://identity.invalid",
    )
    with pytest.raises(RuntimeError, match="absolute HTTPS URL"):
        oidc.oidc_config()


def test_http_oidc_callback_rejects_form_post_mode(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    _set_oidc_environment(
        monkeypatch,
        OIDC_REDIRECT_URI="http://localhost:8080/auth/oidc/synthetic/callback",
        OIDC_RESPONSE_MODE="form_post",
    )

    with pytest.raises(RuntimeError, match="form_post requires an HTTPS redirect URI"):
        oidc.oidc_config()


def test_vault_reads_existing_oidc_subject_hash_secret(
    monkeypatch,
    clear_oidc_vault_caches,
):
    existing = "existing-vault-subject-hash-secret-32-bytes"
    requests = []

    def get(url, **kwargs):
        requests.append((url, kwargs))
        return _VaultResponse(
            200,
            {"data": {"data": {"subject_hash_secret": existing}}},
        )

    monkeypatch.setattr(vault, "_vault_headers", lambda: {"X-Vault-Token": "synthetic"})
    monkeypatch.setattr(vault.httpx, "get", get)
    monkeypatch.setattr(
        vault.httpx,
        "post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an existing secret must not be overwritten")
        ),
    )

    resolved = vault.get_or_create_platform_oidc_subject_hash_secret()

    assert resolved == existing
    assert requests == [
        (
            f"{vault.VAULT_ADDR.rstrip('/')}/v1/{vault.VAULT_KV_MOUNT}/data/"
            "openscribe/platform/oidc-subject-hash",
            {"headers": {"X-Vault-Token": "synthetic"}, "timeout": 10.0},
        )
    ]


def test_vault_creates_oidc_subject_hash_secret_with_create_only_cas(
    monkeypatch,
    clear_oidc_vault_caches,
):
    generated = "generated-vault-subject-hash-secret-32-bytes"
    writes = []
    monkeypatch.setattr(vault, "_vault_headers", lambda: {"X-Vault-Token": "synthetic"})
    monkeypatch.setattr(vault.secrets, "token_urlsafe", lambda _size: generated)
    monkeypatch.setattr(vault.httpx, "get", lambda *_args, **_kwargs: _VaultResponse(404))

    def post(url, **kwargs):
        writes.append((url, kwargs))
        return _VaultResponse(200, {})

    monkeypatch.setattr(vault.httpx, "post", post)

    resolved = vault.get_or_create_platform_oidc_subject_hash_secret(
        secret_ref="secret:openscribe/platform/oidc-subject-hash",
    )

    assert resolved == generated
    assert len(writes) == 1
    assert writes[0][1]["json"] == {
        "options": {"cas": 0},
        "data": {"subject_hash_secret": generated},
    }


def test_vault_oidc_subject_hash_cas_loser_uses_winning_value(
    monkeypatch,
    clear_oidc_vault_caches,
):
    generated = "losing-generated-subject-hash-secret-32-bytes"
    winner = "winning-vault-subject-hash-secret-32-bytes"
    reads = iter(
        (
            _VaultResponse(404),
            _VaultResponse(
                200,
                {"data": {"data": {"subject_hash_secret": winner}}},
            ),
        )
    )
    monkeypatch.setattr(vault, "_vault_headers", lambda: {"X-Vault-Token": "synthetic"})
    monkeypatch.setattr(vault.secrets, "token_urlsafe", lambda _size: generated)
    monkeypatch.setattr(vault.httpx, "get", lambda *_args, **_kwargs: next(reads))
    monkeypatch.setattr(vault.httpx, "post", lambda *_args, **_kwargs: _VaultResponse(400))

    resolved = vault.get_or_create_platform_oidc_subject_hash_secret(
        secret_ref="secret:openscribe/platform/oidc-subject-hash",
    )

    assert resolved == winner
    assert resolved != generated


@pytest.mark.parametrize(
    "secret_ref",
    [
        "other:openscribe/platform/oidc-subject-hash",
        "secret:openscribe/platform/csrf",
        "secret:openscribe/oidc/google",
        "secret:openscribe/platform/oidc-subject-hash/extra",
    ],
)
def test_vault_oidc_subject_hash_rejects_refs_outside_exact_platform_path(
    monkeypatch,
    clear_oidc_vault_caches,
    secret_ref,
):
    monkeypatch.setattr(
        vault.httpx,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid references must be rejected before Vault access")
        ),
    )

    with pytest.raises(AppError):
        vault.get_or_create_platform_oidc_subject_hash_secret(
            secret_ref=secret_ref,
        )


def test_vault_reads_generic_client_secret_from_provider_scoped_ref(
    monkeypatch,
    clear_oidc_vault_caches,
):
    client_secret = "vault-provider-client-secret"
    requested_urls = []

    def get(url, **_kwargs):
        requested_urls.append(url)
        return _VaultResponse(
            200,
            {"data": {"data": {"client_secret": client_secret}}},
        )

    monkeypatch.setattr(vault, "_vault_headers", lambda: {"X-Vault-Token": "synthetic"})
    monkeypatch.setattr(vault.httpx, "get", get)

    resolved = vault.read_oidc_client_secret(
        secret_ref="secret:openscribe/oidc/google",
        provider_key="google",
    )

    assert resolved == client_secret
    assert requested_urls == [
        f"{vault.VAULT_ADDR.rstrip('/')}/v1/secret/data/openscribe/oidc/google"
    ]


@pytest.mark.parametrize(
    "secret_ref",
    [
        "other:openscribe/oidc/google",
        "secret:openscribe/oidc/microsoft",
        "secret:openscribe/platform/oidc/google",
        "secret:openscribe/oidc/google/extra",
    ],
)
def test_vault_oidc_client_secret_rejects_refs_outside_exact_provider_path(
    monkeypatch,
    clear_oidc_vault_caches,
    secret_ref,
):
    monkeypatch.setattr(
        vault.httpx,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid references must be rejected before Vault access")
        ),
    )

    with pytest.raises(AppError):
        vault.read_oidc_client_secret(
            secret_ref=secret_ref,
            provider_key="google",
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"data": {"data": {}}},
        {
            "data": {
                "data": {
                    "client_secret": {"unexpected": "must-not-appear-in-error"},
                }
            }
        },
        ["must-not-appear-in-error"],
    ],
    ids=("missing", "non-string", "malformed-envelope"),
)
def test_vault_oidc_client_secret_missing_or_malformed_fails_closed(
    monkeypatch,
    clear_oidc_vault_caches,
    payload,
):
    raw_secret = "must-not-appear-in-error"
    monkeypatch.setattr(vault, "_vault_headers", lambda: {"X-Vault-Token": "synthetic"})
    monkeypatch.setattr(
        vault.httpx,
        "get",
        lambda *_args, **_kwargs: _VaultResponse(200, payload),
    )

    with pytest.raises(AppError) as rejected:
        vault.read_oidc_client_secret(
            secret_ref="secret:openscribe/oidc/google",
            provider_key="google",
        )

    assert raw_secret not in str(rejected.value)
    assert raw_secret not in repr(rejected.value)


def test_short_vault_subject_hash_secret_fails_closed_without_disclosure(monkeypatch):
    short_secret = "sensitive-but-short"
    _set_oidc_environment(monkeypatch, OIDC_SUBJECT_HASH_SECRET="")
    monkeypatch.setattr(
        oidc,
        "get_or_create_platform_oidc_subject_hash_secret",
        lambda **_kwargs: short_secret,
    )

    with pytest.raises(RuntimeError) as rejected:
        oidc.oidc_config("synthetic")

    assert short_secret not in str(rejected.value)
    assert short_secret not in repr(rejected.value)


def test_google_microsoft_and_cis2_can_be_configured_and_routed_together(
    client,
    db_session,
    make_team,
    make_user,
    monkeypatch,
):
    _set_google_microsoft_environment(monkeypatch)
    monkeypatch.setenv("CIS2_OIDC_ENABLED", "true")
    monkeypatch.setenv("CIS2_OIDC_ISSUER", "https://care-identity.invalid")
    monkeypatch.setenv("CIS2_OIDC_CLIENT_ID", "cis2-client")
    monkeypatch.setenv("CIS2_OIDC_CLIENT_SECRET", "cis2-secret")
    monkeypatch.setenv("CIS2_OIDC_REDIRECT_URI", "http://testserver/auth/oidc/cis2/callback")
    monkeypatch.setenv("CIS2_OIDC_RESPONSE_MODE", "query")

    configs = oidc.oidc_configs()
    assert [(config.provider_key, config.provider_name) for config in configs] == [
        ("google", "Google"),
        ("microsoft", "Microsoft"),
        ("cis2", "Care Identity"),
    ]
    assert oidc.oidc_config() is None
    assert oidc.oidc_config("GOOGLE") == configs[0]
    assert oidc.oidc_config("microsoft") == configs[1]
    assert oidc.oidc_config("cis2") == configs[2]

    team = make_team(name="Multi-provider UI team")
    user = make_user(email="multi-provider@example.invalid", password="password-1", team=team)

    login_page = client.get("/login")
    assert login_page.status_code == 200
    assert 'action="/auth/oidc/google/login"' in login_page.text
    assert 'action="/auth/oidc/microsoft/login"' in login_page.text
    assert 'action="/auth/oidc/cis2/login"' in login_page.text
    assert login_page.text.count('class="auth-divider"') == 1
    assert 'aria-label="Other sign-in options"' in login_page.text
    assert 'aria-label="Sign in with Google"' in login_page.text
    assert 'src="/static/google-sign-in.svg"' in login_page.text
    assert "Sign in with Microsoft" in login_page.text
    assert "Sign in with Care Identity" in login_page.text
    assert "smartcard" not in login_page.text.lower()
    assert "nhs.net" not in login_page.text.lower()
    assert "Continue with" not in login_page.text

    assert client.post(
        "/login",
        data={"email": user.email, "password": "password-1"},
        follow_redirects=False,
    ).status_code == 303
    account_page = client.get("/workspace/account")
    assert account_page.status_code == 200
    assert 'action="/settings/account/oidc/google/link"' in account_page.text
    assert 'action="/settings/account/oidc/microsoft/link"' in account_page.text
    assert 'action="/settings/account/oidc/cis2/link"' in account_page.text
    assert account_page.text.count('class="connected-account"') == 3
    assert account_page.text.count("Not connected") == 3
    assert "Continue to Google" in account_page.text
    assert "Continue to Microsoft" in account_page.text
    assert "Continue to Care Identity" in account_page.text
    assert "Use Care Identity to sign in." in account_page.text
    # Only the email and password change forms ask for TOTP while both providers are unlinked.
    assert account_page.text.count('name="mfa_code"') == 2
    assert "Opening ${provider}…" in account_page.text

    google_config = oidc.oidc_config("google")
    assert google_config is not None
    oidc.link_oidc_identity(
        db_session,
        user,
        google_config,
        issuer=google_config.issuer,
        subject="compact-connected-account",
    )
    linked_page = client.get("/workspace/account")
    assert linked_page.status_code == 200
    assert 'action="/settings/account/oidc/google/unlink"' in linked_page.text
    assert "Disconnect Google" in linked_page.text
    assert ">Connected<" in linked_page.text
    # Disconnecting keeps the existing fresh-TOTP field when MFA is active.
    assert linked_page.text.count('name="mfa_code"') == 3


def test_microsoft_accepts_documented_s256_when_discovery_omits_pkce_metadata(
    monkeypatch,
):
    _set_google_microsoft_environment(monkeypatch)
    config = oidc.oidc_config("microsoft")
    assert config is not None
    metadata = {
        "issuer": oidc.OIDC_MICROSOFT_ISSUER_TEMPLATE,
        "authorization_endpoint": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_endpoint": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "jwks_uri": "https://login.microsoftonline.com/common/discovery/v2.0/keys",
        "response_types_supported": ["code"],
        "response_modes_supported": ["query", "form_post"],
        "token_endpoint_auth_methods_supported": ["client_secret_post"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return metadata

    class FakeMetadataClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers):
            assert url == config.discovery_url
            assert headers == {"Accept": "application/json"}
            return FakeResponse()

    monkeypatch.setattr(oidc.httpx, "AsyncClient", FakeMetadataClient)

    assert asyncio.run(oidc._load_provider_metadata(config)) == metadata

    metadata["code_challenge_methods_supported"] = ["plain"]
    with pytest.raises(OidcProtocolError, match="does not support S256 PKCE"):
        asyncio.run(oidc._load_provider_metadata(config))


def test_custom_provider_still_requires_advertised_s256_pkce(monkeypatch):
    config = _config(response_mode="query")
    metadata = {
        "issuer": config.issuer,
        "authorization_endpoint": "https://identity.invalid/authorize",
        "token_endpoint": "https://identity.invalid/token",
        "jwks_uri": "https://identity.invalid/jwks",
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return metadata

    class FakeMetadataClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url, *, headers):
            assert headers == {"Accept": "application/json"}
            return FakeResponse()

    monkeypatch.setattr(oidc.httpx, "AsyncClient", FakeMetadataClient)

    with pytest.raises(OidcProtocolError, match="does not support S256 PKCE"):
        asyncio.run(oidc._load_provider_metadata(config))


@pytest.mark.parametrize(
    "authorization_endpoint",
    (
        "https://identity.invalid:bad/authorize",
        "https://identity.invalid;script-src/authorize",
        "https://[bad/authorize",
    ),
)
def test_oidc_discovery_rejects_authorization_hosts_unsafe_for_redirect_and_csp(
    monkeypatch,
    authorization_endpoint,
):
    config = _config(response_mode="query")
    metadata = {
        "issuer": config.issuer,
        "authorization_endpoint": authorization_endpoint,
        "token_endpoint": "https://identity.invalid/token",
        "jwks_uri": "https://identity.invalid/jwks",
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        "response_modes_supported": ["query"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return metadata

    class FakeMetadataClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url, *, headers):
            assert headers == {"Accept": "application/json"}
            return FakeResponse()

    monkeypatch.setattr(oidc.httpx, "AsyncClient", FakeMetadataClient)

    with pytest.raises(OidcProtocolError, match="invalid authorization_endpoint"):
        asyncio.run(oidc._load_provider_metadata(config))


def test_microsoft_common_issuer_requires_matching_signed_tenant(monkeypatch):
    _set_google_microsoft_environment(monkeypatch)
    config = oidc.oidc_config("microsoft")
    assert config is not None
    tenant_id = "11111111-2222-4333-8444-555555555555"
    issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"

    assert oidc._validated_token_issuer(
        config,
        {"iss": issuer, "tid": tenant_id.upper()},
    ) == issuer

    rejected_claims = (
        {"iss": issuer},
        {"iss": issuer, "tid": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"},
        {"iss": "https://login.microsoftonline.com/common/v2.0", "tid": tenant_id},
        {"iss": f"https://login.microsoftonline.com.attacker.invalid/{tenant_id}/v2.0", "tid": tenant_id},
    )
    for claims in rejected_claims:
        with pytest.raises(OidcProtocolError):
            oidc._validated_token_issuer(config, claims)


@pytest.mark.parametrize(
    ("email", "accepted"),
    [
        ("clinician@nhs.net", True),
        ("clinician@nhs.uk", True),
        ("clinician@trust.nhs.uk", True),
        ("clinician@dept.trust.nhs.uk", True),
        ("clinician@nhs.uk.attacker.invalid", False),
        ("clinician@evilnhs.uk", False),
        ("clinician@trust.nhs.net", False),
    ],
)
def test_microsoft_email_domain_policy_accepts_only_exact_or_real_subdomains(
    monkeypatch,
    email,
    accepted,
):
    _set_google_microsoft_environment(monkeypatch)
    config = oidc.oidc_config("microsoft")
    assert config is not None
    claims = {"email": email}

    if accepted:
        assert oidc._policy_email(config, claims) == email
    else:
        with pytest.raises(OidcProtocolError):
            oidc._policy_email(config, claims)


def test_microsoft_email_policy_requires_a_configured_claim(monkeypatch):
    _set_google_microsoft_environment(monkeypatch)
    config = oidc.oidc_config("microsoft")
    assert config is not None

    with pytest.raises(OidcProtocolError, match="required email claim"):
        oidc._policy_email(config, {"sub": "synthetic-subject"})


def test_oidc_authorization_consumes_state_cookie_and_pkce_verifier_once(db_session, monkeypatch):
    config = _config()

    async def metadata(_config):
        return {"authorization_endpoint": "https://identity.invalid/authorize"}

    monkeypatch.setattr(oidc, "_load_provider_metadata", metadata)
    started = asyncio.run(oidc.begin_oidc_authorization(db_session, config, purpose="login"))

    stored = db_session.scalar(select(OidcAuthorizationRequest))
    assert stored is not None
    assert stored.state_hash != started.state
    assert stored.code_verifier_hash != started.code_verifier

    consumed = oidc.consume_oidc_authorization(
        db_session,
        provider_key=config.provider_key,
        state=started.state,
        state_cookie=started.state,
        code_verifier=started.code_verifier,
    )

    assert consumed.purpose == "login"
    assert consumed.nonce == stored.nonce
    assert db_session.scalar(select(func.count()).select_from(OidcAuthorizationRequest)) == 0
    with pytest.raises(AppError) as replay:
        oidc.consume_oidc_authorization(
            db_session,
            provider_key=config.provider_key,
            state=started.state,
            state_cookie=started.state,
            code_verifier=started.code_verifier,
        )
    assert replay.value.code == "oidc_state_invalid"


def test_oidc_authorization_rejects_cookie_mismatch_and_burns_a_bad_pkce_attempt(db_session):
    state = "synthetic-state"
    verifier = "synthetic-code-verifier"
    _authorization_request(db_session, state=state, code_verifier=verifier)

    with pytest.raises(AppError) as cookie_mismatch:
        oidc.consume_oidc_authorization(
            db_session,
            provider_key="synthetic",
            state=state,
            state_cookie="other-browser-state",
            code_verifier=verifier,
        )
    assert cookie_mismatch.value.code == "oidc_state_invalid"
    assert db_session.scalar(select(func.count()).select_from(OidcAuthorizationRequest)) == 1

    with pytest.raises(AppError) as bad_verifier:
        oidc.consume_oidc_authorization(
            db_session,
            provider_key="synthetic",
            state=state,
            state_cookie=state,
            code_verifier="wrong-verifier",
        )
    assert bad_verifier.value.code == "oidc_state_invalid"
    assert db_session.scalar(select(func.count()).select_from(OidcAuthorizationRequest)) == 0


def test_oidc_authorization_rejects_provider_mixup_without_consuming_state(db_session):
    state = "google-bound-state"
    verifier = "google-bound-verifier"
    _authorization_request(
        db_session,
        provider_key="google",
        state=state,
        code_verifier=verifier,
    )

    with pytest.raises(AppError) as mismatch:
        oidc.consume_oidc_authorization(
            db_session,
            provider_key="microsoft",
            state=state,
            state_cookie=state,
            code_verifier=verifier,
        )
    assert mismatch.value.code == "oidc_provider_mismatch"
    assert db_session.scalar(select(func.count()).select_from(OidcAuthorizationRequest)) == 1

    consumed = oidc.consume_oidc_authorization(
        db_session,
        provider_key="google",
        state=state,
        state_cookie=state,
        code_verifier=verifier,
    )
    assert consumed.provider_key == "google"
    assert db_session.scalar(select(func.count()).select_from(OidcAuthorizationRequest)) == 0


def test_oidc_identity_linking_is_unique_and_never_auto_links_email(db_session, make_team, make_user):
    team = make_team(name="Synthetic OIDC team")
    first = make_user(email="first@example.invalid", team=team)
    second = make_user(email="second@example.invalid", team=team)
    config = _config()

    linked = oidc.link_oidc_identity(
        db_session, first, config, issuer=config.issuer, subject="subject-1"
    )
    assert linked.user_id == first.id

    with pytest.raises(AppError) as reused_identity:
        oidc.link_oidc_identity(
            db_session, second, config, issuer=config.issuer, subject="subject-1"
        )
    assert reused_identity.value.code == "oidc_identity_unavailable"

    with pytest.raises(AppError) as second_identity_for_provider:
        oidc.link_oidc_identity(
            db_session, first, config, issuer=config.issuer, subject="subject-2"
        )
    assert second_identity_for_provider.value.code == "oidc_provider_already_linked"

    with pytest.raises(AppError) as matching_email:
        oidc.authenticate_oidc_identity(
            db_session, config, issuer=config.issuer, subject=second.email
        )
    assert matching_email.value.code == "oidc_identity_not_linked"
    assert oidc.linked_oidc_identity(db_session, second, config) is None


def test_oidc_subject_lookup_uses_a_versioned_keyed_digest():
    config = _config()
    subject = "predictable-subject-123"

    digest = oidc.oidc_subject_hash(config, subject)

    assert digest.startswith("hmac-sha256:v1:")
    assert subject not in digest
    assert digest != oidc.oidc_subject_hash(
        replace(config, subject_hash_secret=b"different-subject-hash-secret-32-bytes"),
        subject,
    )


def test_oidc_linked_login_requires_an_active_user_and_updates_login_times(db_session, make_team, make_user):
    team = make_team(name="Synthetic linked login team")
    active = make_user(email="active@example.invalid", team=team)
    suspended = make_user(email="suspended@example.invalid", team=team, status=UserStatus.suspended)
    config = _config()
    active_identity = oidc.link_oidc_identity(
        db_session, active, config, issuer=config.issuer, subject="active-subject"
    )
    suspended_identity = oidc.link_oidc_identity(
        db_session, suspended, config, issuer=config.issuer, subject="suspended-subject"
    )

    authenticated = oidc.authenticate_oidc_identity(
        db_session, config, issuer=config.issuer, subject="active-subject"
    )
    db_session.refresh(active_identity)
    db_session.refresh(authenticated)
    assert authenticated.id == active.id
    assert active_identity.last_used_at is not None
    assert authenticated.last_login_at is not None

    with pytest.raises(AppError) as inactive:
        oidc.authenticate_oidc_identity(
            db_session, config, issuer=config.issuer, subject="suspended-subject"
        )
    assert inactive.value.status_code == 403
    assert inactive.value.code == "forbidden"
    db_session.refresh(suspended_identity)
    db_session.refresh(suspended)
    assert suspended_identity.last_used_at is None
    assert suspended.last_login_at is None


def test_oidc_login_rejects_a_system_administrator_identity(db_session, make_user):
    administrator = make_user(
        email="oidc-admin@example.invalid",
        is_system_admin=True,
    )
    config = _config()
    oidc.link_oidc_identity(
        db_session,
        administrator,
        config,
        issuer=config.issuer,
        subject="administrator-subject",
    )

    with pytest.raises(AppError) as rejected:
        oidc.authenticate_oidc_identity(
            db_session,
            config,
            issuer=config.issuer,
            subject="administrator-subject",
        )

    assert rejected.value.status_code == 403
    assert rejected.value.code == "oidc_system_admin_forbidden"


def test_oidc_exchange_validates_signature_audience_nonce_and_required_acr(monkeypatch):
    signing_key = RSAKey.import_key(
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
    )
    signing_key.ensure_kid()
    now = int(utcnow().timestamp())
    config = replace(
        _config(),
        requested_acr_values=("AAL3",),
        required_acr_values=frozenset({"AAL3"}),
    )

    async def metadata(_config):
        return {
            "token_endpoint": "https://identity.invalid/token",
            "jwks_uri": "https://identity.invalid/jwks",
            "id_token_signing_alg_values_supported": ["RS256"],
        }

    def encoded_id_token(*, audience="openscribe-test", nonce="expected-nonce", acr="AAL3"):
        return jwt.encode(
            {"alg": "RS256", "kid": signing_key.kid},
            {
                "iss": config.issuer,
                "sub": "verified-subject",
                "aud": audience,
                "iat": now,
                "exp": now + 300,
                "nonce": nonce,
                "acr": acr,
            },
            signing_key,
            algorithms=["RS256"],
        )

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"keys": [signing_key.as_dict(private=False)]}

    class FakeClient:
        token_value = encoded_id_token()

        def __init__(self, **_kwargs):
            pass

        async def fetch_token(self, *_args, **_kwargs):
            return {"access_token": "synthetic-access-token", "id_token": self.token_value}

        async def aclose(self):
            return None

    class FakeJwksClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers):
            assert url == "https://identity.invalid/jwks"
            assert headers == {"Accept": "application/json"}
            return FakeResponse()

    monkeypatch.setattr(oidc, "_load_provider_metadata", metadata)
    monkeypatch.setattr(oidc, "AsyncOAuth2Client", FakeClient)
    monkeypatch.setattr(oidc.httpx, "AsyncClient", FakeJwksClient)

    verified = asyncio.run(
        oidc.exchange_oidc_code_for_identity(
            config,
            authorization_response_url=f"{config.redirect_uri}?code=synthetic&state=state",
            state="state",
            code_verifier="verifier",
            nonce="expected-nonce",
        )
    )
    assert verified == OidcVerifiedIdentity(
        subject="verified-subject",
        issuer=config.issuer,
        email=None,
        acr="AAL3",
    )

    FakeClient.token_value = encoded_id_token(audience="different-client")
    with pytest.raises(OidcProtocolError):
        asyncio.run(
            oidc.exchange_oidc_code_for_identity(
                config,
                authorization_response_url=f"{config.redirect_uri}?code=synthetic&state=state",
                state="state",
                code_verifier="verifier",
                nonce="expected-nonce",
            )
        )

    for rejected_token in (
        encoded_id_token(nonce="different-nonce"),
        encoded_id_token(acr="AAL2"),
    ):
        FakeClient.token_value = rejected_token
        with pytest.raises(OidcProtocolError):
            asyncio.run(
                oidc.exchange_oidc_code_for_identity(
                    config,
                    authorization_response_url=f"{config.redirect_uri}?code=synthetic&state=state",
                    state="state",
                    code_verifier="verifier",
                    nonce="expected-nonce",
            )
        )


def test_oidc_exchange_classifies_token_endpoint_failure_without_leaking_detail(monkeypatch):
    config = _config()
    secret_detail = "synthetic-provider-secret-detail"

    async def metadata(_config):
        return {
            "token_endpoint": "https://identity.invalid/token",
            "jwks_uri": "https://identity.invalid/jwks",
            "id_token_signing_alg_values_supported": ["RS256"],
        }

    class FailingClient:
        def __init__(self, **_kwargs):
            pass

        async def fetch_token(self, *_args, **_kwargs):
            raise httpx.ConnectError(secret_detail)

        async def aclose(self):
            return None

    monkeypatch.setattr(oidc, "_load_provider_metadata", metadata)
    monkeypatch.setattr(oidc, "AsyncOAuth2Client", FailingClient)

    with pytest.raises(OidcProtocolError) as rejected:
        asyncio.run(
            oidc.exchange_oidc_code_for_identity(
                config,
                authorization_response_url=f"{config.redirect_uri}?code=synthetic&state=state",
                state="state",
                code_verifier="verifier",
                nonce="expected-nonce",
            )
        )

    assert rejected.value.stage == "token_exchange"
    assert secret_detail not in str(rejected.value)
    assert secret_detail not in repr(rejected.value)


def test_cis2_client_secret_post_sends_credentials_in_the_token_form_not_authorization_header():
    config = OidcConfig(
        provider_key="cis2",
        provider_name="Care Identity",
        issuer="https://care-identity.invalid",
        discovery_url="https://care-identity.invalid/.well-known/openid-configuration",
        client_id="cis2-client",
        client_secret="synthetic-cis2-client-secret",
        subject_hash_secret=b"synthetic-subject-hash-secret-32-bytes",
        client_auth_method="client_secret_post",
        redirect_uri="https://openscribe.invalid/auth/oidc/cis2/callback",
        scopes=("openid",),
        response_mode="query",
        allowed_signing_algorithms=("RS256",),
        requested_acr_values=(),
        required_acr_values=frozenset(),
    )
    captured = {}

    async def token_endpoint(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        captured["form"] = parse_qs(request.content.decode("ascii"), keep_blank_values=True)
        return httpx.Response(200, json={"access_token": "synthetic-token", "token_type": "Bearer"})

    async def exchange() -> None:
        client = oidc.AsyncOAuth2Client(
            client_id=config.client_id,
            client_secret=config.client_secret,
            token_endpoint_auth_method=config.client_auth_method,
            redirect_uri=config.redirect_uri,
            state="synthetic-state",
            transport=httpx.MockTransport(token_endpoint),
        )
        try:
            token = await client.fetch_token(
                "https://care-identity.invalid/token",
                authorization_response=f"{config.redirect_uri}?code=synthetic-code&state=synthetic-state",
                code_verifier="synthetic-verifier",
            )
        finally:
            await client.aclose()
        assert token["access_token"] == "synthetic-token"

    asyncio.run(exchange())

    assert captured["authorization"] is None
    assert captured["form"]["client_id"] == [config.client_id]
    assert captured["form"]["client_secret"] == [config.client_secret]
    assert captured["form"]["grant_type"] == ["authorization_code"]


def test_browser_oidc_login_start_sets_bounded_callback_cookies(client, monkeypatch):
    import app.routes.web_oidc as web_oidc

    config = _config(response_mode="query")
    seen = {}

    async def begin(db, supplied_config, *, purpose, user=None, user_session=None):
        seen.update(
            config=supplied_config,
            purpose=purpose,
            user=user,
            user_session=user_session,
        )
        return OidcAuthorizationStart(
            authorization_url="https://identity.invalid/authorize?synthetic=true",
            state="browser-state",
            code_verifier="browser-verifier",
        )

    monkeypatch.setattr(web_oidc, "oidc_config", lambda provider_key: config if provider_key == "synthetic" else None)
    monkeypatch.setattr(web_oidc, "begin_oidc_authorization", begin)

    response = client.post("/auth/oidc/synthetic/login", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "https://identity.invalid/authorize?synthetic=true"
    assert "https://identity.invalid" in response.headers["Content-Security-Policy"]
    assert "synthetic=true" not in response.headers["Content-Security-Policy"]
    assert seen == {"config": config, "purpose": "login", "user": None, "user_session": None}
    cookies = "\n".join(response.headers.get_list("set-cookie")).lower()
    assert f"{OIDC_STATE_COOKIE_NAME}=browser-state" in cookies
    assert f"{OIDC_CODE_VERIFIER_COOKIE_NAME}=browser-verifier" in cookies
    assert "path=/auth/oidc/synthetic/callback" in cookies
    assert "httponly" in cookies
    assert "samesite=lax" in cookies
    assert "max-age=600" in cookies


def test_browser_oidc_link_start_reauthenticates_and_binds_current_session(
    client,
    make_team,
    make_user,
    make_totp_method,
    monkeypatch,
):
    import app.routes.web_oidc as web_oidc

    team = make_team(name="Synthetic browser link team")
    user = make_user(email="link@example.invalid", password="password-1", team=team)
    login = client.post(
        "/login",
        data={"email": user.email, "password": "password-1"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    # Linking confirms both accounts: the local password here and the provider
    # identity during authorization. It must not demand a second local TOTP.
    make_totp_method(user=user)
    config = _config(response_mode="query")
    seen = {}

    async def begin(db, supplied_config, *, purpose, user=None, user_session=None):
        seen.update(purpose=purpose, user_id=user.id, user_session_id=user_session.id)
        return OidcAuthorizationStart(
            authorization_url="https://identity.invalid/link",
            state="link-state",
            code_verifier="link-verifier",
        )

    monkeypatch.setattr(web_oidc, "oidc_config", lambda provider_key: config if provider_key == "synthetic" else None)
    monkeypatch.setattr(web_oidc, "begin_oidc_authorization", begin)

    rejected = client.post(
        "/settings/account/oidc/synthetic/link",
        data={"current_password": "wrong-password"},
        follow_redirects=False,
    )
    assert rejected.status_code == 303
    assert "Current+password+is+incorrect" in rejected.headers["location"]
    assert seen == {}

    response = client.post(
        "/settings/account/oidc/synthetic/link",
        data={"current_password": "password-1"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "https://identity.invalid/link"
    assert "https://identity.invalid" in response.headers["Content-Security-Policy"]
    assert seen["purpose"] == "link"
    assert seen["user_id"] == user.id
    assert seen["user_session_id"] is not None


def test_browser_oidc_link_start_logs_only_bounded_discovery_failure(
    client,
    make_team,
    make_user,
    monkeypatch,
    caplog,
):
    import app.routes.web_oidc as web_oidc

    team = make_team(name="Synthetic failed link team")
    user = make_user(email="failed-link@example.invalid", password="password-1", team=team)
    assert client.post(
        "/login",
        data={"email": user.email, "password": "password-1"},
        follow_redirects=False,
    ).status_code == 303
    config = _config(response_mode="query")
    sensitive_detail = "sensitive-provider-diagnostic"

    async def rejected_begin(*_args, **_kwargs):
        raise OidcProtocolError(sensitive_detail, stage="discovery")

    monkeypatch.setattr(
        web_oidc,
        "oidc_config",
        lambda provider_key: config if provider_key == "synthetic" else None,
    )
    monkeypatch.setattr(web_oidc, "begin_oidc_authorization", rejected_begin)
    caplog.set_level(logging.INFO, logger="openscribe.oidc")

    response = client.post(
        "/settings/account/oidc/synthetic/link",
        data={"current_password": "password-1"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "Single+sign-on+is+temporarily+unavailable" in response.headers["location"]
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "oidc_authorization_start_failure" in messages
    assert "provider_key=synthetic" in messages
    assert "purpose=link" in messages
    assert "reason_code=provider_unavailable" in messages
    assert "protocol_stage=discovery" in messages
    assert "status_code=502" in messages
    assert sensitive_detail not in messages


def test_browser_oidc_callback_logs_in_only_the_linked_subject(
    client,
    db_session,
    make_team,
    make_user,
    monkeypatch,
):
    import app.routes.web_oidc as web_oidc

    team = make_team(name="Synthetic callback login team")
    user = make_user(
        email="callback@example.invalid",
        team=team,
        mfa_required=False,
        mfa_enabled=False,
    )
    config = _config(response_mode="query")
    oidc.link_oidc_identity(
        db_session, user, config, issuer=config.issuer, subject="callback-subject"
    )
    state = "callback-state"
    verifier = "callback-verifier"
    _authorization_request(db_session, state=state, code_verifier=verifier)
    client.cookies.set(OIDC_STATE_COOKIE_NAME, state, path="/auth/oidc/synthetic/callback")
    client.cookies.set(OIDC_CODE_VERIFIER_COOKIE_NAME, verifier, path="/auth/oidc/synthetic/callback")
    seen = {}

    async def exchange(supplied_config, **kwargs):
        seen.update(kwargs)
        return OidcVerifiedIdentity(
            subject="callback-subject",
            issuer=config.issuer,
            email="callback@example.invalid",
            acr=None,
        )

    monkeypatch.setattr(web_oidc, "oidc_config", lambda provider_key: config if provider_key == "synthetic" else None)
    monkeypatch.setattr(web_oidc, "exchange_oidc_code_for_identity", exchange)

    response = client.get(
        f"/auth/oidc/synthetic/callback?code=synthetic-code&state={state}",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/workspace"
    assert seen["state"] == state
    assert seen["code_verifier"] == verifier
    assert seen["nonce"] == "synthetic-nonce"
    assert "synthetic-code" in seen["authorization_response_url"]
    assert client.cookies.get(SESSION_COOKIE_NAME)
    assert db_session.scalar(select(func.count()).select_from(OidcAuthorizationRequest)) == 0
    cleared = "\n".join(response.headers.get_list("set-cookie")).lower()
    assert f"{OIDC_STATE_COOKIE_NAME}=" in cleared
    assert f"{OIDC_CODE_VERIFIER_COOKIE_NAME}=" in cleared


def test_browser_oidc_callback_logs_only_bounded_failure_stage(
    client,
    db_session,
    monkeypatch,
    caplog,
):
    import app.routes.web_oidc as web_oidc

    config = _config(response_mode="query")
    state = "sensitive-callback-state"
    verifier = "sensitive-callback-verifier"
    code = "sensitive-authorization-code"
    _authorization_request(db_session, state=state, code_verifier=verifier)
    client.cookies.set(OIDC_STATE_COOKIE_NAME, state, path="/auth/oidc/synthetic/callback")
    client.cookies.set(OIDC_CODE_VERIFIER_COOKIE_NAME, verifier, path="/auth/oidc/synthetic/callback")

    async def rejected_exchange(*_args, **_kwargs):
        raise OidcProtocolError("bounded failure", stage="token_exchange")

    monkeypatch.setattr(web_oidc, "oidc_config", lambda provider_key: config if provider_key == "synthetic" else None)
    monkeypatch.setattr(web_oidc, "exchange_oidc_code_for_identity", rejected_exchange)
    caplog.set_level(logging.INFO, logger="openscribe.oidc")

    response = client.get(
        f"/auth/oidc/synthetic/callback?code={code}&state={state}",
        follow_redirects=False,
    )

    assert response.status_code == 401
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "oidc_callback_failure" in messages
    assert "provider_key=synthetic" in messages
    assert "purpose=login" in messages
    assert "reason_code=oidc_authentication_failed" in messages
    assert "protocol_stage=token_exchange" in messages
    assert state not in messages
    assert verifier not in messages
    assert code not in messages


def test_browser_oidc_form_post_callback_rejects_duplicate_security_fields(
    client,
    db_session,
    monkeypatch,
):
    import app.routes.web_oidc as web_oidc

    config = _config(response_mode="form_post")
    state = "duplicate-state"
    verifier = "duplicate-verifier"
    _authorization_request(db_session, state=state, code_verifier=verifier)
    client.cookies.set(OIDC_STATE_COOKIE_NAME, state, path="/auth/oidc/synthetic/callback")
    client.cookies.set(OIDC_CODE_VERIFIER_COOKIE_NAME, verifier, path="/auth/oidc/synthetic/callback")
    monkeypatch.setattr(web_oidc, "oidc_config", lambda provider_key: config if provider_key == "synthetic" else None)

    response = client.post(
        "/auth/oidc/synthetic/callback",
        content=f"code=synthetic&state={state}&state=attacker-state",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert db_session.scalar(select(func.count()).select_from(OidcAuthorizationRequest)) == 1


def test_browser_oidc_form_post_callback_enforces_streamed_body_limit(
    client,
    db_session,
    monkeypatch,
):
    import app.routes.web_oidc as web_oidc

    config = _config(response_mode="form_post")
    state = "bounded-state"
    verifier = "bounded-verifier"
    _authorization_request(db_session, state=state, code_verifier=verifier)
    client.cookies.set(OIDC_STATE_COOKIE_NAME, state, path="/auth/oidc/synthetic/callback")
    client.cookies.set(OIDC_CODE_VERIFIER_COOKIE_NAME, verifier, path="/auth/oidc/synthetic/callback")
    monkeypatch.setattr(web_oidc, "oidc_config", lambda provider_key: config if provider_key == "synthetic" else None)

    response = client.post(
        "/auth/oidc/synthetic/callback",
        content=f"code=synthetic&state={state}&padding={'x' * 9000}",
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "content-length": "1",
        },
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert db_session.scalar(select(func.count()).select_from(OidcAuthorizationRequest)) == 1


@pytest.mark.parametrize(("response_mode", "method"), [("form_post", "GET"), ("query", "POST")])
def test_oidc_callback_rejects_a_method_that_does_not_match_response_mode(
    client,
    db_session,
    monkeypatch,
    response_mode,
    method,
):
    import app.routes.web_oidc as web_oidc

    state = f"method-state-{response_mode}"
    verifier = f"method-verifier-{response_mode}"
    _authorization_request(db_session, state=state, code_verifier=verifier)
    client.cookies.set(OIDC_STATE_COOKIE_NAME, state, path="/auth/oidc/synthetic/callback")
    client.cookies.set(OIDC_CODE_VERIFIER_COOKIE_NAME, verifier, path="/auth/oidc/synthetic/callback")
    monkeypatch.setattr(
        web_oidc,
        "oidc_config",
        lambda provider_key: _config(response_mode=response_mode) if provider_key == "synthetic" else None,
    )

    response = client.request(
        method,
        f"/auth/oidc/synthetic/callback?code=synthetic&state={state}",
        follow_redirects=False,
    )

    assert response.status_code == 405
    assert db_session.scalar(select(func.count()).select_from(OidcAuthorizationRequest)) == 1


def test_browser_oidc_link_callback_requires_the_bound_full_session_and_rotates_it(
    client,
    db_session,
    make_team,
    make_user,
    monkeypatch,
):
    import app.routes.web_oidc as web_oidc

    team = make_team(name="Synthetic callback link team")
    user = make_user(email="callback-link@example.invalid", password="password-1", team=team)
    login = client.post(
        "/login",
        data={"email": user.email, "password": "password-1"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    original_token = client.cookies.get(SESSION_COOKIE_NAME)
    original_session = db_session.scalar(
        select(UserSession).where(UserSession.session_token_hash == session_token_hash(original_token))
    )
    assert original_session is not None

    config = _config(response_mode="form_post")
    state = "callback-link-state"
    verifier = "callback-link-verifier"
    _authorization_request(
        db_session,
        state=state,
        code_verifier=verifier,
        purpose="link",
        user_id=user.id,
        user_session_id=original_session.id,
    )
    client.cookies.set(OIDC_STATE_COOKIE_NAME, state, path="/auth/oidc/synthetic/callback")
    client.cookies.set(OIDC_CODE_VERIFIER_COOKIE_NAME, verifier, path="/auth/oidc/synthetic/callback")
    # SameSite=Lax session cookies are absent on a cross-site form_post.
    client.cookies.delete(SESSION_COOKIE_NAME)

    async def exchange(_config, **_kwargs):
        return OidcVerifiedIdentity(
            subject="new-linked-subject",
            issuer=config.issuer,
            email=None,
            acr=None,
        )

    monkeypatch.setattr(web_oidc, "oidc_config", lambda provider_key: config if provider_key == "synthetic" else None)
    monkeypatch.setattr(web_oidc, "exchange_oidc_code_for_identity", exchange)

    response = client.post(
        "/auth/oidc/synthetic/callback",
        data={"code": "synthetic-link-code", "state": state},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "Single+sign-on+linked" in response.headers["location"]
    identity = db_session.scalar(select(UserOidcIdentity).where(UserOidcIdentity.user_id == user.id))
    assert identity is not None
    assert identity.subject_hash == oidc.oidc_subject_hash(config, "new-linked-subject")
    db_session.refresh(original_session)
    assert original_session.status is SessionStatus.revoked
    assert client.cookies.get(SESSION_COOKIE_NAME) != original_token


def test_browser_cis2_link_then_login_uses_the_linked_issuer_and_subject_only(
    client,
    db_session,
    make_team,
    make_user,
    monkeypatch,
):
    import app.routes.web_oidc as web_oidc

    _set_cis2_environment(monkeypatch)
    config = oidc.oidc_config("cis2")
    assert config is not None
    team = make_team(name="CIS2 browser flow team")
    user = make_user(
        email="care-identity-owner@example.invalid",
        password="password-1",
        team=team,
        mfa_required=False,
        mfa_enabled=False,
    )
    email_matching_user = make_user(
        email="care-identity-subject@example.invalid",
        password="password-1",
        team=team,
        mfa_required=False,
        mfa_enabled=False,
    )
    subject = email_matching_user.email
    assert client.post(
        "/login",
        data={"email": user.email, "password": "password-1"},
        follow_redirects=False,
    ).status_code == 303
    original_token = client.cookies.get(SESSION_COOKIE_NAME)
    original_session = db_session.scalar(
        select(UserSession).where(UserSession.session_token_hash == session_token_hash(original_token))
    )
    assert original_session is not None

    starts = []

    async def begin(db, supplied_config, *, purpose, user=None, user_session=None):
        assert supplied_config == config
        state = f"cis2-{purpose}-state"
        verifier = f"cis2-{purpose}-verifier"
        starts.append((purpose, user.id if user is not None else None))
        db.add(
            OidcAuthorizationRequest(
                provider_key="cis2",
                state_hash=hashlib.sha256(state.encode("utf-8")).hexdigest(),
                nonce=f"cis2-{purpose}-nonce",
                code_verifier_hash=hashlib.sha256(verifier.encode("utf-8")).hexdigest(),
                purpose=purpose,
                user_id=user.id if user is not None else None,
                user_session_id=user_session.id if user_session is not None else None,
                expires_at=utcnow() + timedelta(minutes=5),
            )
        )
        db.commit()
        return OidcAuthorizationStart(
            authorization_url="https://care-identity.invalid/authorize?synthetic=true",
            state=state,
            code_verifier=verifier,
        )

    async def exchange(supplied_config, **kwargs):
        assert supplied_config == config
        assert kwargs["state"].startswith("cis2-")
        return OidcVerifiedIdentity(
            subject=subject,
            issuer=config.issuer,
            email=None,
            acr=None,
        )

    monkeypatch.setattr(web_oidc, "begin_oidc_authorization", begin)
    monkeypatch.setattr(web_oidc, "exchange_oidc_code_for_identity", exchange)

    link_start = client.post(
        "/settings/account/oidc/cis2/link",
        data={"current_password": "password-1"},
        follow_redirects=False,
    )
    assert link_start.status_code == 303
    assert link_start.headers["location"] == "https://care-identity.invalid/authorize?synthetic=true"
    link_callback = client.get(
        "/auth/oidc/cis2/callback?code=synthetic-link-code&state=cis2-link-state",
        follow_redirects=False,
    )
    assert link_callback.status_code == 303
    assert "Single+sign-on+linked" in link_callback.headers["location"]
    identity = db_session.scalar(
        select(UserOidcIdentity).where(UserOidcIdentity.provider_key == "cis2")
    )
    assert identity is not None
    assert identity.user_id == user.id
    assert identity.issuer == config.issuer
    assert identity.subject_hash == oidc.oidc_subject_hash(config, subject)
    db_session.refresh(original_session)
    assert original_session.status is SessionStatus.revoked
    assert client.cookies.get(SESSION_COOKIE_NAME) != original_token

    # The subject happens to equal another account's email, but CIS2 email is
    # absent and the linked issuer-and-subject pair must still select the owner.
    client.cookies.delete(SESSION_COOKIE_NAME)
    client.cookies.delete(CSRF_COOKIE_NAME)
    login_start = client.post("/auth/oidc/cis2/login", follow_redirects=False)
    assert login_start.status_code == 303
    assert login_start.headers["location"] == "https://care-identity.invalid/authorize?synthetic=true"
    login_callback = client.get(
        "/auth/oidc/cis2/callback?code=synthetic-login-code&state=cis2-login-state",
        follow_redirects=False,
    )
    assert login_callback.status_code == 303
    assert login_callback.headers["location"] == "/workspace"
    login_token = client.cookies.get(SESSION_COOKIE_NAME)
    login_session = db_session.scalar(
        select(UserSession).where(UserSession.session_token_hash == session_token_hash(login_token))
    )
    assert login_session is not None
    assert login_session.user_id == user.id
    assert login_session.user_id != email_matching_user.id
    assert starts == [("link", user.id), ("login", None)]


def test_oidc_callback_query_is_removed_from_the_asgi_access_log_target():
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "scheme": "https",
        "method": "GET",
        "server": ("openscribe.invalid", 443),
        "client": ("127.0.0.1", 12345),
        "root_path": "",
        "path": "/auth/oidc/synthetic/callback",
        "raw_path": b"/auth/oidc/synthetic/callback",
        "query_string": b"code=synthetic-secret-code&state=synthetic-state",
        "headers": [],
    }
    request = Request(scope)
    observed = {}

    async def call_next(next_request):
        observed["query_string"] = next_request.scope["query_string"]
        observed["params"] = next_request.state.oidc_callback_query
        return PlainTextResponse("ok")

    response = asyncio.run(redact_oidc_callback_query_from_access_log(request, call_next))

    assert response.status_code == 200
    assert observed["query_string"] == b""
    assert observed["params"] == {
        "code": "synthetic-secret-code",
        "state": "synthetic-state",
    }
    assert scope["query_string"] == b""
