from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from authlib.integrations.base_client import OAuthError
from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.oidc.core import CodeIDToken
from email_validator import EmailNotValidError, validate_email
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.cookie_security import app_environment
from app.errors import AppError
from app.models import (
    OidcAuthorizationRequest,
    User,
    UserOidcIdentity,
    UserSession,
    SessionAuthLevel,
    SessionStatus,
    UserStatus,
    utcnow,
)
from app.security_headers import oidc_form_action_origin
from app.services.vault import (
    get_or_create_platform_oidc_subject_hash_secret,
    read_oidc_client_secret,
)


OIDC_STATE_COOKIE_NAME = "openscribe_oidc_state"
OIDC_CODE_VERIFIER_COOKIE_NAME = "openscribe_oidc_verifier"
OIDC_AUTHORIZATION_REQUEST_LIFETIME = timedelta(minutes=10)
OIDC_HTTP_TIMEOUT_SECONDS = 10.0
OIDC_SUBJECT_MAX_LENGTH = 255
OIDC_URL_MAX_LENGTH = 2048
OIDC_SUBJECT_HASH_VERSION = "v1"
OIDC_SUBJECT_HASH_SECRET_MIN_BYTES = 32
OIDC_SUBJECT_HASH_SECRET_MAX_BYTES = 4096
OIDC_PROVIDER_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
OIDC_SCOPE_PATTERN = re.compile(r'^[\x21\x23-\x5B\x5D-\x7E]{1,128}$')
OIDC_CLIENT_AUTH_METHODS = frozenset({"client_secret_basic", "client_secret_post", "none"})
OIDC_RESPONSE_MODES = frozenset({"form_post", "query"})
OIDC_MICROSOFT_ISSUER_TEMPLATE = "https://login.microsoftonline.com/{tenantid}/v2.0"
OIDC_MICROSOFT_ISSUER_PATTERN = re.compile(
    r"^https://login\.microsoftonline\.com/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/v2\.0$"
)
OIDC_ASYMMETRIC_SIGNING_ALGORITHMS = frozenset(
    {
        "RS256",
        "RS384",
        "RS512",
        "PS256",
        "PS384",
        "PS512",
        "ES256",
        "ES384",
        "ES512",
        "EdDSA",
    }
)


class OidcProtocolError(Exception):
    """A bounded OIDC failure that is safe to translate to a generic response."""

    def __init__(self, message: str, *, stage: str = "protocol") -> None:
        super().__init__(message)
        self.stage = stage


@dataclass(frozen=True, slots=True)
class OidcConfig:
    provider_key: str
    provider_name: str
    issuer: str
    discovery_url: str
    client_id: str
    client_secret: str | None = field(repr=False)
    subject_hash_secret: bytes = field(repr=False)
    client_auth_method: str
    redirect_uri: str
    scopes: tuple[str, ...]
    response_mode: str
    allowed_signing_algorithms: tuple[str, ...]
    requested_acr_values: tuple[str, ...]
    required_acr_values: frozenset[str]
    issuer_template: str | None = None
    allowed_email_domains: tuple[str, ...] = ()
    email_claim_names: tuple[str, ...] = ()
    s256_pkce_documented_out_of_band: bool = False


@dataclass(frozen=True, slots=True)
class OidcAuthorizationStart:
    authorization_url: str
    state: str
    code_verifier: str


@dataclass(frozen=True, slots=True)
class ConsumedOidcAuthorization:
    provider_key: str
    purpose: Literal["login", "link"]
    nonce: str
    user_id: Any | None
    user_session_id: Any | None


@dataclass(frozen=True, slots=True)
class OidcVerifiedIdentity:
    subject: str
    issuer: str
    email: str | None
    acr: str | None


def _env_enabled(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes"}


def _configured_url(name: str, value: str, *, require_https: bool) -> str:
    if len(value) > OIDC_URL_MAX_LENGTH:
        raise RuntimeError(f"{name} must not exceed {OIDC_URL_MAX_LENGTH} characters")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a valid absolute URL") from exc
    if parsed.scheme not in ({"https"} if require_https else {"http", "https"}):
        raise RuntimeError(f"{name} must be an absolute {'HTTPS' if require_https else 'HTTP(S)'} URL")
    if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise RuntimeError(f"{name} must be an absolute URL without credentials or a fragment")
    try:
        oidc_form_action_origin(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must contain a valid ASCII DNS or IP host and port") from exc
    return value


def _subject_hash_secret() -> bytes:
    configured = os.getenv("OIDC_SUBJECT_HASH_SECRET", "")
    if configured:
        value = configured.encode("utf-8")
    else:
        secret_ref = os.getenv("OIDC_SUBJECT_HASH_SECRET_VAULT_REF", "").strip() or None
        try:
            value = get_or_create_platform_oidc_subject_hash_secret(
                secret_ref=secret_ref,
            ).encode("utf-8")
        except AppError as exc:
            raise RuntimeError(
                "OIDC subject-hash secret could not be resolved from Vault"
            ) from exc
    if not OIDC_SUBJECT_HASH_SECRET_MIN_BYTES <= len(value) <= OIDC_SUBJECT_HASH_SECRET_MAX_BYTES:
        raise RuntimeError("OIDC_SUBJECT_HASH_SECRET must contain 32-4096 bytes")
    return value


def _response_mode(prefix: str, *, production: bool) -> str:
    default = "form_post" if production else "query"
    response_mode = os.getenv(f"{prefix}_RESPONSE_MODE", default).strip()
    if response_mode not in OIDC_RESPONSE_MODES:
        raise RuntimeError(f"{prefix}_RESPONSE_MODE must be form_post or query")
    redirect_uri = os.getenv(f"{prefix}_REDIRECT_URI", "").strip()
    if (
        response_mode == "form_post"
        and redirect_uri
        and urlsplit(redirect_uri).scheme.lower() != "https"
    ):
        raise RuntimeError(
            f"{prefix}_RESPONSE_MODE=form_post requires an HTTPS redirect URI"
        )
    if production and response_mode != "form_post":
        raise RuntimeError(
            f"{prefix}_RESPONSE_MODE=form_post is required in production to keep authorization codes out of access logs"
        )
    return response_mode


def _redirect_uri(prefix: str, provider_key: str, *, production: bool) -> str:
    raw = os.getenv(f"{prefix}_REDIRECT_URI", "").strip()
    if not raw:
        raise RuntimeError(f"{prefix}_REDIRECT_URI is required when {prefix}_ENABLED=true")
    value = _configured_url(f"{prefix}_REDIRECT_URI", raw, require_https=production)
    parsed = urlsplit(value)
    if parsed.query:
        raise RuntimeError(f"{prefix}_REDIRECT_URI must not contain a query string")
    expected_path = f"/auth/oidc/{provider_key}/callback"
    if parsed.path != expected_path:
        raise RuntimeError(f"{prefix}_REDIRECT_URI path must be {expected_path}")
    return value


def _cis2_response_mode() -> str:
    response_mode = os.getenv("CIS2_OIDC_RESPONSE_MODE", "query").strip()
    if response_mode != "query":
        raise RuntimeError(
            "CIS2_OIDC_RESPONSE_MODE must be query because CIS2 does not support form_post"
        )
    return response_mode


def _client_id(prefix: str) -> str:
    client_id = os.getenv(f"{prefix}_CLIENT_ID", "").strip()
    if not client_id or len(client_id) > 512 or any(ord(character) < 0x20 for character in client_id):
        raise RuntimeError(f"{prefix}_CLIENT_ID must contain 1-512 characters")
    return client_id


def _client_secret(prefix: str, provider_key: str) -> str:
    client_secret = os.getenv(f"{prefix}_CLIENT_SECRET", "")
    if client_secret:
        return client_secret
    secret_ref = os.getenv(f"{prefix}_CLIENT_SECRET_VAULT_REF", "").strip()
    if not secret_ref:
        raise RuntimeError(
            f"{prefix}_CLIENT_SECRET or {prefix}_CLIENT_SECRET_VAULT_REF is required when {prefix}_ENABLED=true"
        )
    try:
        return read_oidc_client_secret(
            secret_ref=secret_ref,
            provider_key=provider_key,
        )
    except AppError as exc:
        raise RuntimeError(
            f"{prefix} client secret could not be resolved from Vault"
        ) from exc


def _client_credentials(prefix: str, provider_key: str) -> tuple[str, str]:
    client_id = _client_id(prefix)
    client_secret = _client_secret(prefix, provider_key)
    return client_id, client_secret


def _custom_oidc_config(secret: bytes, *, production: bool) -> OidcConfig | None:
    if not _env_enabled("OIDC_ENABLED"):
        return None
    provider_key = os.getenv("OIDC_PROVIDER_KEY", "oidc").strip().lower()
    provider_name = os.getenv("OIDC_PROVIDER_NAME", "Single sign-on").strip()
    issuer_raw = os.getenv("OIDC_ISSUER", "").strip()
    client_id = _client_id("OIDC")
    redirect_uri_raw = os.getenv("OIDC_REDIRECT_URI", "").strip()
    client_auth_method = os.getenv("OIDC_CLIENT_AUTH_METHOD", "client_secret_basic").strip()
    response_mode = _response_mode("OIDC", production=production)

    if not OIDC_PROVIDER_KEY_PATTERN.fullmatch(provider_key):
        raise RuntimeError("OIDC_PROVIDER_KEY must contain 1-64 lowercase letters, digits, underscores, or hyphens")
    if not provider_name or len(provider_name) > 80:
        raise RuntimeError("OIDC_PROVIDER_NAME must contain 1-80 characters")
    if not issuer_raw:
        raise RuntimeError("OIDC_ISSUER is required when OIDC_ENABLED=true")
    if not redirect_uri_raw:
        raise RuntimeError("OIDC_REDIRECT_URI is required when OIDC_ENABLED=true")
    if client_auth_method not in OIDC_CLIENT_AUTH_METHODS:
        raise RuntimeError("OIDC_CLIENT_AUTH_METHOD must be client_secret_basic, client_secret_post, or none")
    client_secret = (
        None
        if client_auth_method == "none"
        else _client_secret("OIDC", provider_key)
    )
    issuer = _configured_url("OIDC_ISSUER", issuer_raw, require_https=production)
    if urlsplit(issuer).query:
        raise RuntimeError("OIDC_ISSUER must not contain a query string")
    discovery_default = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    discovery_url = _configured_url(
        "OIDC_DISCOVERY_URL",
        os.getenv("OIDC_DISCOVERY_URL", "").strip() or discovery_default,
        require_https=production,
    )
    redirect_uri = _redirect_uri("OIDC", provider_key, production=production)

    scopes = tuple(dict.fromkeys(os.getenv("OIDC_SCOPES", "openid profile email").split()))
    if "openid" not in scopes or len(scopes) > 20 or any(
        OIDC_SCOPE_PATTERN.fullmatch(scope) is None for scope in scopes
    ):
        raise RuntimeError("OIDC_SCOPES must contain openid and no more than 20 bounded scope values")

    algorithms = tuple(
        dict.fromkeys(
            item.strip()
            for item in os.getenv("OIDC_ALLOWED_ID_TOKEN_ALGORITHMS", "RS256").split(",")
            if item.strip()
        )
    )
    if not algorithms or any(item not in OIDC_ASYMMETRIC_SIGNING_ALGORITHMS for item in algorithms):
        raise RuntimeError("OIDC_ALLOWED_ID_TOKEN_ALGORITHMS must contain only supported asymmetric algorithms")

    requested_acr_values = tuple(dict.fromkeys(os.getenv("OIDC_ACR_VALUES", "").split()))
    required_acr_values = frozenset(os.getenv("OIDC_REQUIRED_ACR_VALUES", "").split())
    if (
        len(requested_acr_values) > 20
        or len(required_acr_values) > 20
        or any(len(value) > 256 or not value.isascii() for value in requested_acr_values)
        or any(len(value) > 256 or not value.isascii() for value in required_acr_values)
    ):
        raise RuntimeError("OIDC ACR settings may contain at most 20 values")
    if required_acr_values and not required_acr_values.issubset(set(requested_acr_values)):
        raise RuntimeError("OIDC_REQUIRED_ACR_VALUES must be a subset of OIDC_ACR_VALUES")

    return OidcConfig(
        provider_key=provider_key,
        provider_name=provider_name,
        issuer=issuer,
        discovery_url=discovery_url,
        client_id=client_id,
        client_secret=client_secret,
        subject_hash_secret=secret,
        client_auth_method=client_auth_method,
        redirect_uri=redirect_uri,
        scopes=scopes,
        response_mode=response_mode,
        allowed_signing_algorithms=algorithms,
        requested_acr_values=requested_acr_values,
        required_acr_values=required_acr_values,
    )


def _google_oidc_config(secret: bytes, *, production: bool) -> OidcConfig | None:
    if not _env_enabled("GOOGLE_OIDC_ENABLED"):
        return None
    client_id, client_secret = _client_credentials("GOOGLE_OIDC", "google")
    return OidcConfig(
        provider_key="google",
        provider_name="Google",
        issuer="https://accounts.google.com",
        discovery_url="https://accounts.google.com/.well-known/openid-configuration",
        client_id=client_id,
        client_secret=client_secret,
        subject_hash_secret=secret,
        client_auth_method="client_secret_post",
        redirect_uri=_redirect_uri("GOOGLE_OIDC", "google", production=production),
        scopes=("openid", "profile", "email"),
        response_mode=_response_mode("GOOGLE_OIDC", production=production),
        allowed_signing_algorithms=("RS256",),
        requested_acr_values=(),
        required_acr_values=frozenset(),
    )


def _allowed_microsoft_domains() -> tuple[str, ...]:
    raw_values = os.getenv(
        "MICROSOFT_ALLOWED_EMAIL_DOMAINS",
        "nhs.net,nhs.uk,*.nhs.uk",
    ).split(",")
    values = tuple(dict.fromkeys(value.strip().lower() for value in raw_values if value.strip()))
    domain_pattern = re.compile(
        r"^(?:\*\.)?[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
    )
    if not values or len(values) > 50 or any(
        len(value) > 255 or domain_pattern.fullmatch(value) is None for value in values
    ):
        raise RuntimeError(
            "MICROSOFT_ALLOWED_EMAIL_DOMAINS must contain 1-50 comma-separated DNS domains"
        )
    return values


def _microsoft_oidc_config(secret: bytes, *, production: bool) -> OidcConfig | None:
    if not _env_enabled("MICROSOFT_OIDC_ENABLED"):
        return None
    client_id, client_secret = _client_credentials("MICROSOFT_OIDC", "microsoft")
    return OidcConfig(
        provider_key="microsoft",
        provider_name="Microsoft",
        issuer=OIDC_MICROSOFT_ISSUER_TEMPLATE,
        issuer_template=OIDC_MICROSOFT_ISSUER_TEMPLATE,
        discovery_url="https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration",
        client_id=client_id,
        client_secret=client_secret,
        subject_hash_secret=secret,
        client_auth_method="client_secret_post",
        redirect_uri=_redirect_uri("MICROSOFT_OIDC", "microsoft", production=production),
        scopes=("openid", "profile", "email"),
        response_mode=_response_mode("MICROSOFT_OIDC", production=production),
        allowed_signing_algorithms=("RS256",),
        requested_acr_values=(),
        required_acr_values=frozenset(),
        allowed_email_domains=_allowed_microsoft_domains(),
        email_claim_names=("email", "preferred_username"),
        s256_pkce_documented_out_of_band=True,
    )


def _cis2_oidc_config(secret: bytes, *, production: bool) -> OidcConfig | None:
    if not _env_enabled("CIS2_OIDC_ENABLED"):
        return None
    provider_key = "cis2"
    prefix = "CIS2_OIDC"
    issuer_raw = os.getenv(f"{prefix}_ISSUER", "").strip()
    if not issuer_raw:
        raise RuntimeError(f"{prefix}_ISSUER is required when {prefix}_ENABLED=true")
    client_auth_method = os.getenv(f"{prefix}_CLIENT_AUTH_METHOD", "client_secret_post").strip()
    if client_auth_method != "client_secret_post":
        raise RuntimeError(
            f"{prefix}_CLIENT_AUTH_METHOD must be client_secret_post; "
            "private_key_jwt is not implemented"
        )

    client_id, client_secret = _client_credentials(prefix, provider_key)
    issuer = _configured_url(f"{prefix}_ISSUER", issuer_raw, require_https=production)
    if urlsplit(issuer).query:
        raise RuntimeError(f"{prefix}_ISSUER must not contain a query string")
    discovery_default = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    discovery_url = _configured_url(
        f"{prefix}_DISCOVERY_URL",
        os.getenv(f"{prefix}_DISCOVERY_URL", "").strip() or discovery_default,
        require_https=production,
    )
    scopes = tuple(dict.fromkeys(os.getenv(f"{prefix}_SCOPES", "openid").split()))
    if "openid" not in scopes or len(scopes) > 20 or any(
        OIDC_SCOPE_PATTERN.fullmatch(scope) is None for scope in scopes
    ):
        raise RuntimeError(f"{prefix}_SCOPES must contain openid and no more than 20 bounded scope values")

    algorithms = tuple(
        dict.fromkeys(
            item.strip()
            for item in os.getenv(f"{prefix}_ALLOWED_ID_TOKEN_ALGORITHMS", "RS256").split(",")
            if item.strip()
        )
    )
    if not algorithms or any(item not in OIDC_ASYMMETRIC_SIGNING_ALGORITHMS for item in algorithms):
        raise RuntimeError(
            f"{prefix}_ALLOWED_ID_TOKEN_ALGORITHMS must contain only supported asymmetric algorithms"
        )

    requested_acr_values = tuple(dict.fromkeys(os.getenv(f"{prefix}_ACR_VALUES", "").split()))
    required_acr_values = frozenset(os.getenv(f"{prefix}_REQUIRED_ACR_VALUES", "").split())
    if (
        len(requested_acr_values) > 20
        or len(required_acr_values) > 20
        or any(len(value) > 256 or not value.isascii() for value in requested_acr_values)
        or any(len(value) > 256 or not value.isascii() for value in required_acr_values)
    ):
        raise RuntimeError(f"{prefix} ACR settings may contain at most 20 values")
    if required_acr_values and not required_acr_values.issubset(set(requested_acr_values)):
        raise RuntimeError(f"{prefix}_REQUIRED_ACR_VALUES must be a subset of {prefix}_ACR_VALUES")

    return OidcConfig(
        provider_key=provider_key,
        provider_name="Care Identity",
        issuer=issuer,
        discovery_url=discovery_url,
        client_id=client_id,
        client_secret=client_secret,
        subject_hash_secret=secret,
        client_auth_method=client_auth_method,
        redirect_uri=_redirect_uri(prefix, provider_key, production=production),
        scopes=scopes,
        response_mode=_cis2_response_mode(),
        allowed_signing_algorithms=algorithms,
        requested_acr_values=requested_acr_values,
        required_acr_values=required_acr_values,
    )


def oidc_configs() -> tuple[OidcConfig, ...]:
    production = app_environment() in {"production", "prod"}
    enabled = (
        _env_enabled("OIDC_ENABLED")
        or _env_enabled("GOOGLE_OIDC_ENABLED")
        or _env_enabled("MICROSOFT_OIDC_ENABLED")
        or _env_enabled("CIS2_OIDC_ENABLED")
    )
    if not enabled:
        return ()
    secret = _subject_hash_secret()
    configs = tuple(
        config
        for config in (
            _google_oidc_config(secret, production=production),
            _microsoft_oidc_config(secret, production=production),
            _cis2_oidc_config(secret, production=production),
            _custom_oidc_config(secret, production=production),
        )
        if config is not None
    )
    keys = [config.provider_key for config in configs]
    if len(keys) != len(set(keys)):
        raise RuntimeError("Configured OIDC provider keys must be unique")
    return configs


def oidc_config(provider_key: str | None = None) -> OidcConfig | None:
    configs = oidc_configs()
    if provider_key is None:
        return configs[0] if len(configs) == 1 else None
    normalized = provider_key.strip().lower()
    return next((config for config in configs if config.provider_key == normalized), None)


def oidc_configured_for_environment() -> None:
    oidc_configs()


def oidc_issuer_hash(issuer: str) -> str:
    return hashlib.sha256(issuer.encode("utf-8")).hexdigest()


def oidc_subject_hash(config: OidcConfig, subject: str, *, issuer: str | None = None) -> str:
    digest = hmac.new(
        config.subject_hash_secret,
        f"{issuer or config.issuer}\0{subject}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"hmac-sha256:{OIDC_SUBJECT_HASH_VERSION}:{digest}"


def _opaque_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_provider_endpoint(config: OidcConfig, name: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise OidcProtocolError(f"OIDC metadata omitted {name}")
    try:
        return _configured_url(name, value, require_https=urlsplit(config.issuer).scheme == "https")
    except RuntimeError as exc:
        raise OidcProtocolError(f"OIDC metadata contained an invalid {name}") from exc


async def _load_provider_metadata(config: OidcConfig) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(
            timeout=OIDC_HTTP_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as client:
            response = await client.get(
                config.discovery_url,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            metadata = response.json()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise OidcProtocolError("OIDC discovery failed", stage="discovery") from exc

    expected_metadata_issuer = config.issuer_template or config.issuer
    if not isinstance(metadata, dict) or metadata.get("issuer") != expected_metadata_issuer:
        raise OidcProtocolError(
            "OIDC discovery issuer did not match configuration",
            stage="discovery",
        )
    _validate_provider_endpoint(config, "authorization_endpoint", metadata.get("authorization_endpoint"))
    _validate_provider_endpoint(config, "token_endpoint", metadata.get("token_endpoint"))
    _validate_provider_endpoint(config, "jwks_uri", metadata.get("jwks_uri"))

    response_types = metadata.get("response_types_supported")
    if not isinstance(response_types, list) or "code" not in response_types:
        raise OidcProtocolError(
            "OIDC provider does not support authorization code flow",
            stage="discovery",
        )
    pkce_methods = metadata.get("code_challenge_methods_supported")
    pkce_is_documented_out_of_band = (
        pkce_methods is None and config.s256_pkce_documented_out_of_band
    )
    if not pkce_is_documented_out_of_band and (
        not isinstance(pkce_methods, list) or "S256" not in pkce_methods
    ):
        raise OidcProtocolError(
            "OIDC provider does not support S256 PKCE",
            stage="discovery",
        )
    response_modes = metadata.get("response_modes_supported", ["query"])
    if not isinstance(response_modes, list) or config.response_mode not in response_modes:
        raise OidcProtocolError(
            "OIDC provider does not support the configured response mode",
            stage="discovery",
        )
    auth_methods = metadata.get("token_endpoint_auth_methods_supported", ["client_secret_basic"])
    if not isinstance(auth_methods, list) or config.client_auth_method not in auth_methods:
        raise OidcProtocolError(
            "OIDC provider does not support the configured client authentication method",
            stage="discovery",
        )

    advertised_algorithms = metadata.get("id_token_signing_alg_values_supported")
    if not isinstance(advertised_algorithms, list) or not set(config.allowed_signing_algorithms).intersection(
        advertised_algorithms
    ):
        raise OidcProtocolError(
            "OIDC provider does not advertise an allowed ID-token signing algorithm",
            stage="discovery",
        )
    return metadata


async def begin_oidc_authorization(
    db: Session,
    config: OidcConfig,
    *,
    purpose: Literal["login", "link"],
    user: User | None = None,
    user_session: UserSession | None = None,
) -> OidcAuthorizationStart:
    if purpose == "login" and (user is not None or user_session is not None):
        raise ValueError("Login OIDC requests must not be user-bound")
    if purpose == "link" and (user is None or user_session is None):
        raise ValueError("Link OIDC requests must be bound to a user and session")

    metadata = await _load_provider_metadata(config)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    client = AsyncOAuth2Client(
        client_id=config.client_id,
        client_secret=config.client_secret,
        token_endpoint_auth_method=config.client_auth_method,
        scope=" ".join(config.scopes),
        redirect_uri=config.redirect_uri,
        code_challenge_method="S256",
    )
    authorize_kwargs: dict[str, str] = {
        "nonce": nonce,
        "response_mode": config.response_mode,
    }
    if config.requested_acr_values:
        authorize_kwargs["acr_values"] = " ".join(config.requested_acr_values)
    try:
        authorization_url, returned_state = client.create_authorization_url(
            metadata["authorization_endpoint"],
            state=state,
            code_verifier=code_verifier,
            **authorize_kwargs,
        )
    finally:
        await client.aclose()
    if returned_state != state:
        raise OidcProtocolError("OIDC client changed the authorization state")

    db.execute(
        delete(OidcAuthorizationRequest).where(
            OidcAuthorizationRequest.expires_at <= utcnow()
        )
    )
    db.add(
        OidcAuthorizationRequest(
            provider_key=config.provider_key,
            state_hash=_opaque_hash(state),
            nonce=nonce,
            code_verifier_hash=_opaque_hash(code_verifier),
            purpose=purpose,
            user_id=user.id if user is not None else None,
            user_session_id=user_session.id if user_session is not None else None,
            expires_at=utcnow() + OIDC_AUTHORIZATION_REQUEST_LIFETIME,
        )
    )
    db.commit()
    return OidcAuthorizationStart(
        authorization_url=authorization_url,
        state=state,
        code_verifier=code_verifier,
    )


def consume_oidc_authorization(
    db: Session,
    *,
    provider_key: str,
    state: str,
    state_cookie: str | None,
    code_verifier: str | None,
) -> ConsumedOidcAuthorization:
    if not state or not state_cookie or not code_verifier:
        raise AppError(401, "oidc_state_invalid", "Single sign-on could not be verified")
    if not hmac.compare_digest(state, state_cookie):
        raise AppError(401, "oidc_state_invalid", "Single sign-on could not be verified")

    transaction = db.scalar(
        select(OidcAuthorizationRequest)
        .where(OidcAuthorizationRequest.state_hash == _opaque_hash(state))
        .with_for_update()
    )
    if transaction is None or transaction.expires_at <= utcnow():
        if transaction is not None:
            db.delete(transaction)
            db.commit()
        raise AppError(401, "oidc_state_invalid", "Single sign-on could not be verified")
    if not hmac.compare_digest(transaction.provider_key, provider_key):
        raise AppError(401, "oidc_provider_mismatch", "Single sign-on could not be verified")
    if not hmac.compare_digest(transaction.code_verifier_hash, _opaque_hash(code_verifier)):
        db.delete(transaction)
        db.commit()
        raise AppError(401, "oidc_state_invalid", "Single sign-on could not be verified")

    if transaction.purpose not in {"login", "link"}:
        db.delete(transaction)
        db.commit()
        raise AppError(401, "oidc_state_invalid", "Single sign-on could not be verified")

    purpose: Literal["login", "link"] = transaction.purpose
    consumed = ConsumedOidcAuthorization(
        provider_key=transaction.provider_key,
        purpose=purpose,
        nonce=transaction.nonce,
        user_id=transaction.user_id,
        user_session_id=transaction.user_session_id,
    )
    db.delete(transaction)
    db.commit()
    return consumed


def _issuer_matches_config(config: OidcConfig, issuer: str) -> bool:
    if config.issuer_template is None:
        return issuer == config.issuer
    return (
        config.issuer_template == OIDC_MICROSOFT_ISSUER_TEMPLATE
        and OIDC_MICROSOFT_ISSUER_PATTERN.fullmatch(issuer) is not None
    )


def _validated_token_issuer(config: OidcConfig, claims: dict[str, Any]) -> str:
    issuer = claims.get("iss")
    if not isinstance(issuer, str) or not _issuer_matches_config(config, issuer):
        raise OidcProtocolError("OIDC issuer claim did not match configuration")
    if config.issuer_template is None:
        return issuer
    if config.issuer_template != OIDC_MICROSOFT_ISSUER_TEMPLATE:
        raise OidcProtocolError("OIDC issuer template was unsupported")
    match = OIDC_MICROSOFT_ISSUER_PATTERN.fullmatch(issuer)
    tenant_id = claims.get("tid")
    if match is None or not isinstance(tenant_id, str) or match.group(1).lower() != tenant_id.lower():
        raise OidcProtocolError("Microsoft tenant issuer did not match the signed tenant claim")
    return issuer


def _domain_is_allowed(domain: str, allowed_domains: tuple[str, ...]) -> bool:
    for allowed in allowed_domains:
        if allowed.startswith("*."):
            suffix = allowed[2:]
            if domain != suffix and domain.endswith(f".{suffix}"):
                return True
        elif domain == allowed:
            return True
    return False


def _policy_email(config: OidcConfig, claims: dict[str, Any]) -> str | None:
    if not config.allowed_email_domains:
        return None
    candidate = next(
        (
            value
            for claim_name in config.email_claim_names
            if isinstance((value := claims.get(claim_name)), str) and value
        ),
        None,
    )
    if candidate is None:
        raise OidcProtocolError("OIDC identity omitted the required email claim")
    try:
        normalized = validate_email(candidate, check_deliverability=False).normalized
    except EmailNotValidError as exc:
        raise OidcProtocolError("OIDC email claim was invalid") from exc
    domain = normalized.rsplit("@", 1)[1].lower()
    if not _domain_is_allowed(domain, config.allowed_email_domains):
        raise OidcProtocolError("OIDC email domain was not permitted")
    return normalized


async def exchange_oidc_code_for_identity(
    config: OidcConfig,
    *,
    authorization_response_url: str,
    state: str,
    code_verifier: str,
    nonce: str,
) -> OidcVerifiedIdentity:
    metadata = await _load_provider_metadata(config)
    client = AsyncOAuth2Client(
        client_id=config.client_id,
        client_secret=config.client_secret,
        token_endpoint_auth_method=config.client_auth_method,
        scope=" ".join(config.scopes),
        state=state,
        redirect_uri=config.redirect_uri,
        code_challenge_method="S256",
        timeout=OIDC_HTTP_TIMEOUT_SECONDS,
    )
    try:
        try:
            token = await client.fetch_token(
                metadata["token_endpoint"],
                authorization_response=authorization_response_url,
                code_verifier=code_verifier,
            )
        except (httpx.HTTPError, OAuthError, KeyError, TypeError, ValueError) as exc:
            raise OidcProtocolError(
                "OIDC token exchange failed",
                stage="token_exchange",
            ) from exc
        id_token = token.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise OidcProtocolError(
                "OIDC token response omitted id_token",
                stage="token_response",
            )

        try:
            jwks_uri = metadata["jwks_uri"]
            # Signing keys are public. Fetch them with a plain HTTP client so
            # OAuth access-token injection and OAuth-only request arguments
            # cannot affect the JWKS request.
            async with httpx.AsyncClient(
                timeout=OIDC_HTTP_TIMEOUT_SECONDS,
                follow_redirects=False,
            ) as jwks_client:
                response = await jwks_client.get(
                    jwks_uri,
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                jwks = response.json()
                key_set = KeySet.import_key_set(jwks)
        except (httpx.HTTPError, JoseError, KeyError, TypeError, ValueError) as exc:
            raise OidcProtocolError(
                "OIDC signing-key retrieval failed",
                stage="signing_keys",
            ) from exc
        allowed_algorithms = tuple(
            algorithm
            for algorithm in config.allowed_signing_algorithms
            if algorithm in metadata["id_token_signing_alg_values_supported"]
        )
        try:
            decoded = jwt.decode(id_token, key=key_set, algorithms=allowed_algorithms)
            token_claims = decoded.claims
            issuer = _validated_token_issuer(config, token_claims)
            claims = CodeIDToken(
                token_claims,
                decoded.header,
                {
                    "iss": {"essential": True, "values": [issuer]},
                    "sub": {"essential": True},
                    "aud": {"essential": True, "values": [config.client_id]},
                    "exp": {"essential": True},
                    "iat": {"essential": True},
                },
                {
                    "nonce": nonce,
                    "client_id": config.client_id,
                    "access_token": token.get("access_token"),
                },
            )
            claims.validate(leeway=60)
        except OidcProtocolError as exc:
            raise OidcProtocolError(
                str(exc),
                stage="id_token_validation",
            ) from exc
        except (JoseError, KeyError, TypeError, ValueError) as exc:
            raise OidcProtocolError(
                "OIDC token validation failed",
                stage="id_token_validation",
            ) from exc
    finally:
        await client.aclose()

    subject = claims.get("sub")
    if (
        not isinstance(subject, str)
        or not subject
        or len(subject) > OIDC_SUBJECT_MAX_LENGTH
        or not subject.isascii()
        or any(ord(character) < 0x20 for character in subject)
    ):
        raise OidcProtocolError("OIDC subject claim was invalid")
    acr = claims.get("acr")
    if acr is not None and not isinstance(acr, str):
        raise OidcProtocolError("OIDC assurance claim was invalid")
    if config.required_acr_values and acr not in config.required_acr_values:
        raise OidcProtocolError("OIDC authentication assurance was insufficient")
    email = _policy_email(config, claims)
    return OidcVerifiedIdentity(subject=subject, issuer=issuer, email=email, acr=acr)


def linked_oidc_identity(db: Session, user: User, config: OidcConfig) -> UserOidcIdentity | None:
    return db.scalar(
        select(UserOidcIdentity).where(
            UserOidcIdentity.user_id == user.id,
            UserOidcIdentity.provider_key == config.provider_key,
        )
    )


def resolve_oidc_link_session(
    db: Session,
    *,
    user_id: Any,
    user_session_id: Any,
) -> tuple[User, UserSession] | None:
    session = db.scalar(
        select(UserSession)
        .options(
            joinedload(UserSession.user).joinedload(User.team),
            joinedload(UserSession.user).joinedload(User.mfa_methods),
        )
        .where(
            UserSession.id == user_session_id,
            UserSession.user_id == user_id,
        )
    )
    if session is None:
        return None
    if (
        session.status is not SessionStatus.active
        or session.expires_at <= utcnow()
        or session.auth_level is not SessionAuthLevel.full
        or session.user.status is not UserStatus.active
    ):
        return None
    return session.user, session


def link_oidc_identity(
    db: Session,
    user: User,
    config: OidcConfig,
    *,
    issuer: str,
    subject: str,
) -> UserOidcIdentity:
    if not _issuer_matches_config(config, issuer):
        raise ValueError("OIDC identity issuer does not match its provider configuration")
    issuer_hash = oidc_issuer_hash(issuer)
    subject_hash = oidc_subject_hash(config, subject, issuer=issuer)
    existing = db.scalar(
        select(UserOidcIdentity).where(
            UserOidcIdentity.issuer_hash == issuer_hash,
            UserOidcIdentity.subject_hash == subject_hash,
        )
    )
    if existing is not None:
        if existing.user_id != user.id:
            raise AppError(409, "oidc_identity_unavailable", "That single sign-on identity is already linked")
        current_slot = linked_oidc_identity(db, user, config)
        if current_slot is not None and current_slot.id != existing.id:
            raise AppError(409, "oidc_provider_already_linked", "This account already has a linked identity for that provider")
        if existing.provider_key != config.provider_key:
            existing.provider_key = config.provider_key
            db.add(existing)
            db.commit()
            db.refresh(existing)
        return existing
    if linked_oidc_identity(db, user, config) is not None:
        raise AppError(409, "oidc_provider_already_linked", "This account already has a linked identity for that provider")

    identity = UserOidcIdentity(
        user_id=user.id,
        provider_key=config.provider_key,
        issuer=issuer,
        issuer_hash=issuer_hash,
        subject_hash=subject_hash,
    )
    db.add(identity)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "oidc_identity_unavailable", "That single sign-on identity cannot be linked") from exc
    db.refresh(identity)
    return identity


def unlink_oidc_identity(db: Session, user: User, config: OidcConfig) -> None:
    identity = linked_oidc_identity(db, user, config)
    if identity is None:
        raise AppError(404, "oidc_identity_not_linked", "No linked identity was found for that provider")
    db.delete(identity)
    db.commit()


def authenticate_oidc_identity(
    db: Session,
    config: OidcConfig,
    *,
    issuer: str,
    subject: str,
) -> User:
    if not _issuer_matches_config(config, issuer):
        raise ValueError("OIDC identity issuer does not match its provider configuration")
    identity = db.scalar(
        select(UserOidcIdentity)
        .options(
            joinedload(UserOidcIdentity.user).joinedload(User.team),
            joinedload(UserOidcIdentity.user).joinedload(User.mfa_methods),
        )
        .where(
            UserOidcIdentity.issuer_hash == oidc_issuer_hash(issuer),
            UserOidcIdentity.subject_hash
            == oidc_subject_hash(config, subject, issuer=issuer),
        )
    )
    if identity is None:
        raise AppError(401, "oidc_identity_not_linked", "No OpenScribe account is linked to this identity")
    user = identity.user
    if user.is_system_admin:
        raise AppError(
            403,
            "oidc_system_admin_forbidden",
            "System administrator accounts must use password sign-in",
        )
    if user.status is not UserStatus.active:
        raise AppError(403, "forbidden", "User account is not active", {"status": user.status.value})
    now = utcnow()
    identity.last_used_at = now
    user.last_login_at = now
    db.add(identity)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
