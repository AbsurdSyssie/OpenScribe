import os
from urllib.parse import urlsplit, urlunsplit

import psycopg


DEFAULT_DATABASE_URL = "postgresql+psycopg://ambient:ambient@localhost:5432/ambient_scribe"
DEFAULT_TEST_DATABASE_URL = "postgresql+psycopg://ambient:ambient@localhost:5432/ambient_scribe_test"
DEFAULT_RATE_LIMIT_STORAGE_URL = "redis://localhost:6379/0"
DEFAULT_TEST_RATE_LIMIT_STORAGE_URL = "redis://localhost:6379/15"


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
    return os.getenv("RATE_LIMIT_STORAGE_URL", DEFAULT_RATE_LIMIT_STORAGE_URL)


def test_rate_limit_storage_url() -> str:
    return os.getenv("TEST_RATE_LIMIT_STORAGE_URL", DEFAULT_TEST_RATE_LIMIT_STORAGE_URL)


def ensure_safe_test_database_url() -> str:
    app_url = database_url()
    test_url = test_database_url()

    if app_url == test_url:
        raise RuntimeError(
            "Unsafe test database configuration: TEST_DATABASE_URL must not match DATABASE_URL.\n"
            "Refusing to run tests against the application database.\n"
            f"DATABASE_URL={app_url}\n"
            f"TEST_DATABASE_URL={test_url}"
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
                cursor.execute(f'CREATE DATABASE "{db_name}"')
