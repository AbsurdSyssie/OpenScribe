from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Any
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import User, UserEncryptionKey
from app.services.vault import (
    VAULT_TRANSIT_MOUNT,
    VAULT_USER_CONTENT_KEK_KEY_NAME,
    generate_user_content_data_key,
    unwrap_user_content_data_key,
)


ENVELOPE_VERSION = 1
ENVELOPE_ALGORITHM = "AES-256-GCM"
NONCE_SIZE_BYTES = 12
DEK_CACHE_SESSION_KEY = "openscribe_owner_unwrapped_deks"


def _aad(*, table: str, field: str, owner_user_id: UUID, record_id: UUID) -> bytes:
    return f"openscribe:v1:{table}:{field}:{owner_user_id}:{record_id}".encode("utf-8")


def _envelope_json(*, dek_version: int, nonce: bytes, ciphertext: bytes) -> str:
    return json.dumps(
        {
            "v": ENVELOPE_VERSION,
            "alg": ENVELOPE_ALGORITHM,
            "dkv": dek_version,
            "n": base64.b64encode(nonce).decode("ascii"),
            "ct": base64.b64encode(ciphertext).decode("ascii"),
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def is_encrypted_envelope(value: str | None) -> bool:
    if not value or not isinstance(value, str) or not value.startswith("{"):
        return False
    try:
        parsed = json.loads(value)
    except ValueError:
        return False
    if not isinstance(parsed, dict):
        return False
    return parsed.get("v") == ENVELOPE_VERSION and parsed.get("alg") == ENVELOPE_ALGORITHM and "n" in parsed and "ct" in parsed


def _parse_envelope(value: str) -> tuple[int, bytes, bytes]:
    try:
        parsed = json.loads(value)
    except ValueError as exc:
        raise AppError(500, "content_crypto_invalid", "Encrypted content envelope is invalid") from exc
    if not isinstance(parsed, dict) or parsed.get("v") != ENVELOPE_VERSION or parsed.get("alg") != ENVELOPE_ALGORITHM:
        raise AppError(500, "content_crypto_invalid", "Encrypted content envelope is invalid")
    try:
        dek_version = int(parsed.get("dkv") or 1)
        nonce = base64.b64decode(str(parsed["n"]), validate=True)
        ciphertext = base64.b64decode(str(parsed["ct"]), validate=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError(500, "content_crypto_invalid", "Encrypted content envelope is invalid") from exc
    return dek_version, nonce, ciphertext


def get_active_user_key(db: Session, *, user_id: UUID) -> UserEncryptionKey | None:
    return db.query(UserEncryptionKey).filter(UserEncryptionKey.user_id == user_id, UserEncryptionKey.is_active.is_(True)).one_or_none()


def _session_dek_cache(db: Session) -> dict[tuple[UUID, str], bytes]:
    cache = db.info.get(DEK_CACHE_SESSION_KEY)
    if isinstance(cache, dict):
        return cache
    cache = {}
    db.info[DEK_CACHE_SESSION_KEY] = cache
    return cache


def _owner_user(db: Session, *, owner_user_id: UUID) -> User:
    user = db.get(User, owner_user_id)
    if user is None:
        raise AppError(404, "not_found", "Owner user not found", {"resource": "user", "user_id": str(owner_user_id)})
    return user


def _owner_key_record(db: Session, *, owner_user_id: UUID, create_if_missing: bool) -> UserEncryptionKey:
    user = _owner_user(db, owner_user_id=owner_user_id)
    if create_if_missing:
        return ensure_user_dek(db, user=user)
    existing = get_active_user_key(db, user_id=owner_user_id)
    if existing is None:
        raise AppError(500, "content_crypto_missing_key", "Owner encryption key is missing")
    return existing


def _owner_dek(db: Session, *, owner_user_id: UUID, create_if_missing: bool) -> tuple[UserEncryptionKey, bytes]:
    key_record = _owner_key_record(db, owner_user_id=owner_user_id, create_if_missing=create_if_missing)
    cache_key = (owner_user_id, key_record.kek_mount, key_record.kek_key_name, key_record.wrapped_dek)
    cache = _session_dek_cache(db)
    cached = cache.get(cache_key)
    if cached is not None:
        return key_record, cached
    dek = unwrap_user_content_data_key(
        wrapped_dek=key_record.wrapped_dek,
        mount_point=key_record.kek_mount,
        key_name=key_record.kek_key_name,
    )
    cache[cache_key] = dek
    return key_record, dek


def ensure_user_dek(db: Session, *, user: User) -> UserEncryptionKey:
    """Ensure key material exists for user-owned encrypted data.

    Key eligibility is intentionally separate from transcript ownership and
    authorization. System administrators may need a DEK for authentication
    secrets without gaining access to transcript-derived content.
    """
    existing = get_active_user_key(db, user_id=user.id)
    if existing is not None:
        return existing

    plaintext_dek, wrapped_dek, key_version = generate_user_content_data_key()
    del plaintext_dek
    record = UserEncryptionKey(
        id=uuid4(),
        user_id=user.id,
        dek_version=1,
        wrapped_dek=wrapped_dek,
        kek_mount=VAULT_TRANSIT_MOUNT,
        kek_key_name=VAULT_USER_CONTENT_KEK_KEY_NAME,
        kek_key_version=key_version,
        is_active=True,
    )
    try:
        with db.begin_nested():
            db.add(record)
            db.flush()
    except IntegrityError:
        existing = get_active_user_key(db, user_id=user.id)
        if existing is None:
            raise
        return existing
    return record


def encrypt_text_for_owner(
    db: Session,
    *,
    owner_user_id: UUID,
    table: str,
    field: str,
    record_id: UUID,
    plaintext: str | None,
) -> str | None:
    if plaintext is None:
        return None
    key_record, dek = _owner_dek(db, owner_user_id=owner_user_id, create_if_missing=True)
    nonce = os.urandom(NONCE_SIZE_BYTES)
    ciphertext = AESGCM(dek).encrypt(
        nonce,
        plaintext.encode("utf-8"),
        _aad(table=table, field=field, owner_user_id=owner_user_id, record_id=record_id),
    )
    return _envelope_json(dek_version=key_record.dek_version, nonce=nonce, ciphertext=ciphertext)


def decrypt_text_for_owner(
    db: Session,
    *,
    owner_user_id: UUID,
    table: str,
    field: str,
    record_id: UUID,
    stored_value: str | None,
) -> str | None:
    if stored_value is None:
        return None
    if not is_encrypted_envelope(stored_value):
        return stored_value
    _, nonce, ciphertext = _parse_envelope(stored_value)
    _, dek = _owner_dek(db, owner_user_id=owner_user_id, create_if_missing=False)
    try:
        plaintext = AESGCM(dek).decrypt(
            nonce,
            ciphertext,
            _aad(table=table, field=field, owner_user_id=owner_user_id, record_id=record_id),
        )
    except Exception as exc:
        raise AppError(500, "content_crypto_invalid", "Encrypted content could not be decrypted") from exc
    return plaintext.decode("utf-8")


def keyed_digest_for_owner(
    db: Session,
    *,
    owner_user_id: UUID,
    purpose: str,
    value: str,
) -> str:
    _, dek = _owner_dek(db, owner_user_id=owner_user_id, create_if_missing=True)
    message = f"openscribe:v1:{purpose}:{value}".encode("utf-8")
    return hmac.new(dek, message, hashlib.sha256).hexdigest()


def encrypt_json_for_owner(
    db: Session,
    *,
    owner_user_id: UUID,
    table: str,
    field: str,
    record_id: UUID,
    plaintext: Any,
) -> str | None:
    if plaintext is None:
        return None
    serialized = json.dumps(plaintext, separators=(",", ":"), sort_keys=True)
    return encrypt_text_for_owner(
        db,
        owner_user_id=owner_user_id,
        table=table,
        field=field,
        record_id=record_id,
        plaintext=serialized,
    )


def decrypt_json_for_owner(
    db: Session,
    *,
    owner_user_id: UUID,
    table: str,
    field: str,
    record_id: UUID,
    stored_value: Any,
) -> Any:
    if stored_value is None:
        return None
    if isinstance(stored_value, (dict, list, int, float, bool)):
        return stored_value
    if not isinstance(stored_value, str):
        raise AppError(500, "content_crypto_invalid", "Encrypted content JSON is invalid")
    plaintext = decrypt_text_for_owner(
        db,
        owner_user_id=owner_user_id,
        table=table,
        field=field,
        record_id=record_id,
        stored_value=stored_value,
    )
    if plaintext is None:
        return None
    try:
        return json.loads(plaintext)
    except ValueError as exc:
        raise AppError(500, "content_crypto_invalid", "Encrypted content JSON is invalid") from exc
