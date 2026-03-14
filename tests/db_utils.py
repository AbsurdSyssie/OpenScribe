import os
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import create_engine, text


DEFAULT_DATABASE_URL = "postgresql+psycopg://ambient:ambient@localhost:5432/ambient_scribe"
DEFAULT_TEST_DATABASE_URL = "postgresql+psycopg://ambient:ambient@localhost:5432/ambient_scribe_test"


def database_url() -> str:
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def test_database_url() -> str:
    return os.getenv("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


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


def admin_database_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, parts.fragment))


def database_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def ensure_database_exists(url: str) -> None:
    admin_url = admin_database_url(url)
    db_name = database_name(url)
    admin_engine = create_engine(admin_url, future=True, isolation_level="AUTOCOMMIT")

    with admin_engine.connect() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": db_name},
        ).scalar()
        if not exists:
            connection.execute(text(f'CREATE DATABASE "{db_name}"'))

    admin_engine.dispose()
