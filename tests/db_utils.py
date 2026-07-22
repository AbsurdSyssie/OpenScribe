import os
import re
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql


DEFAULT_DATABASE_URL = "postgresql+psycopg://ambient:ambient@localhost:5432/ambient_scribe"
DEFAULT_TEST_DATABASE_URL = "postgresql+psycopg://ambient:ambient@localhost:5432/ambient_scribe_test"
DEFAULT_RATE_LIMIT_STORAGE_URL = "redis://localhost:6379/0"
DEFAULT_TEST_RATE_LIMIT_STORAGE_URL = "redis://localhost:6379/15"
ORIGINAL_RATE_LIMIT_STORAGE_URL_ENV = "OPENSCRIBE_TEST_ORIGINAL_RATE_LIMIT_STORAGE_URL"
POSTGRES_IDENTIFIER_MAX_BYTES = 63
_XDIST_WORKER_RE = re.compile(r"gw[0-9]+$")


def validate_xdist_worker_id(worker_id: str | None) -> str | None:
    """Return a safe xdist worker id, or None for the sequential test runner."""
    if worker_id in {None, "", "master"}:
        return None
    if not _XDIST_WORKER_RE.fullmatch(worker_id):
        raise RuntimeError(
            "Unsafe PYTEST_XDIST_WORKER value. Expected 'master' or an xdist worker "
            f"name like 'gw0', got {worker_id!r}."
        )
    return worker_id


# xdist sets this before importing each worker's conftest. Validate at module
# import so an unexpected environment cannot select a shared resource later.
PYTEST_XDIST_WORKER_ID = validate_xdist_worker_id(os.getenv("PYTEST_XDIST_WORKER"))


def normalize_postgres_host(url: str) -> str:
    parts = urlsplit(url)
    if parts.hostname != "localhost":
        return url

    username = parts.username or ""
    password = parts.password or ""
    auth = username
    if password:
        auth = f"{auth}:{password}"
    if auth:
        auth = f"{auth}@"
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{auth}127.0.0.1{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def database_url() -> str:
    return normalize_postgres_host(os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))


def test_database_url() -> str:
    return normalize_postgres_host(os.getenv("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL))


def rate_limit_storage_url() -> str:
    # xdist workers inherit conftest's test-only storage override from the
    # controller. Preserve the pre-override application URL for the safety
    # comparison so inherited state cannot hide an unsafe configuration.
    return os.getenv(
        ORIGINAL_RATE_LIMIT_STORAGE_URL_ENV,
        os.getenv("RATE_LIMIT_STORAGE_URL", DEFAULT_RATE_LIMIT_STORAGE_URL),
    )


def test_rate_limit_storage_url() -> str:
    return os.getenv("TEST_RATE_LIMIT_STORAGE_URL", DEFAULT_TEST_RATE_LIMIT_STORAGE_URL)


def database_url_for_worker(base_test_url: str, worker_id: str | None = PYTEST_XDIST_WORKER_ID) -> str:
    """Return the per-worker PostgreSQL URL without changing non-xdist runs."""
    validated_worker_id = validate_xdist_worker_id(worker_id)
    if validated_worker_id is None:
        return base_test_url

    parts = urlsplit(base_test_url)
    database = parts.path.lstrip("/")
    if not database or "/" in database:
        raise RuntimeError("TEST_DATABASE_URL must include exactly one PostgreSQL database name.")
    derived_database = f"{database}_{validated_worker_id}"
    if len(derived_database.encode("utf-8")) > POSTGRES_IDENTIFIER_MAX_BYTES:
        raise RuntimeError(
            "Worker test database name exceeds PostgreSQL's 63-byte identifier limit: "
            f"{derived_database!r}"
        )
    return urlunsplit((parts.scheme, parts.netloc, f"/{derived_database}", parts.query, parts.fragment))


def rate_limit_key_prefix(worker_id: str | None = PYTEST_XDIST_WORKER_ID) -> str:
    validated_worker_id = validate_xdist_worker_id(worker_id)
    return f"openscribe_pytest_{validated_worker_id}" if validated_worker_id is not None else ""


def rate_limit_key_pattern(worker_id: str | None = PYTEST_XDIST_WORKER_ID) -> str | None:
    """Match only SlowAPI/limits Redis keys for one xdist worker.

    limits' RedisStorage prepends ``LIMITS:`` and SlowAPI supplies the key
    prefix as the first ``LIMITER/`` key component.
    """
    prefix = rate_limit_key_prefix(worker_id)
    return f"LIMITS:LIMITER/{prefix}/*" if prefix else None


def ensure_safe_test_database_url() -> str:
    app_url = database_url()
    base_test_url = test_database_url()
    test_url = database_url_for_worker(base_test_url)

    if app_url in {base_test_url, test_url}:
        raise RuntimeError(
            "Unsafe test database configuration: DATABASE_URL must not match the base or worker TEST_DATABASE_URL.\n"
            "Refusing to run tests against the application database.\n"
            f"DATABASE_URL={app_url}\n"
            f"TEST_DATABASE_URL={base_test_url}\n"
            f"DERIVED_TEST_DATABASE_URL={test_url}"
        )

    return test_url


def ensure_safe_test_rate_limit_storage_url() -> str:
    app_url = rate_limit_storage_url()
    test_url = test_rate_limit_storage_url()

    if app_url == test_url:
        raise RuntimeError(
            "Unsafe test rate-limit configuration: TEST_RATE_LIMIT_STORAGE_URL must not match RATE_LIMIT_STORAGE_URL.\n"
            "Refusing to run tests against the application rate-limit store.\n"
            f"RATE_LIMIT_STORAGE_URL={app_url}\n"
            f"TEST_RATE_LIMIT_STORAGE_URL={test_url}"
        )

    return test_url


def admin_database_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, parts.fragment))


def database_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def ensure_database_exists(url: str) -> None:
    admin_url = admin_database_url(url)
    db_name = database_name(url)
    admin_dsn = admin_url.replace("postgresql+psycopg://", "postgresql://", 1)

    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            exists = cursor.fetchone()
            if not exists:
                cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
