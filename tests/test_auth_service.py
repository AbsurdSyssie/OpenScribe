from app.services.admin import hash_password
from app.services.auth import recovery_code_hash, session_token_hash, verify_password


def test_verify_password_accepts_matching_scrypt_hash():
    password_hash = hash_password("password-1")

    assert verify_password("password-1", password_hash) is True


def test_verify_password_rejects_wrong_password_and_malformed_hash():
    password_hash = hash_password("password-1")

    assert verify_password("password-2", password_hash) is False
    assert verify_password("password-1", "not-a-valid-hash") is False


def test_session_and_recovery_hashes_are_deterministic_and_not_plaintext():
    assert session_token_hash("token-1") == session_token_hash("token-1")
    assert recovery_code_hash("code-1") == recovery_code_hash("code-1")
    assert session_token_hash("token-1") != "token-1"
    assert recovery_code_hash("code-1") != "code-1"
