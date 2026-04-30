from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, VerificationError, VerifyMismatchError
from argon2.low_level import Type


ARGON2_MEMORY_COST_KIB = 19 * 1024
ARGON2_TIME_COST = 2
ARGON2_PARALLELISM = 1

_password_hasher = PasswordHasher(
    time_cost=ARGON2_TIME_COST,
    memory_cost=ARGON2_MEMORY_COST_KIB,
    parallelism=ARGON2_PARALLELISM,
    type=Type.ID,
)


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if password_hash.startswith("$argon2id$"):
        try:
            return _password_hasher.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, Argon2Error):
            return False
    return False


def password_needs_rehash(password_hash: str) -> bool:
    if not password_hash.startswith("$argon2id$"):
        return True
    try:
        return _password_hasher.check_needs_rehash(password_hash)
    except Argon2Error:
        return True
