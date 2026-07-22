import fnmatch
import importlib

import pytest
from limits import parse

from tests import db_utils


def test_master_worker_keeps_base_resources():
    url = "postgresql+psycopg://user:password@db.example:5433/ambient_scribe_test?sslmode=require"

    assert db_utils.validate_xdist_worker_id("master") is None
    assert db_utils.database_url_for_worker(url, "master") == url
    assert db_utils.rate_limit_key_prefix("master") == ""
    assert db_utils.rate_limit_key_pattern("master") is None


@pytest.mark.parametrize("worker_id", ["gw0", "gw12"])
def test_worker_resources_are_derived_from_safe_worker_id(worker_id):
    url = "postgresql+psycopg://user:password@db.example:5433/ambient_scribe_test?sslmode=require"

    assert db_utils.database_url_for_worker(url, worker_id) == (
        f"postgresql+psycopg://user:password@db.example:5433/ambient_scribe_test_{worker_id}?sslmode=require"
    )
    assert db_utils.rate_limit_key_prefix(worker_id) == f"openscribe_pytest_{worker_id}"
    assert db_utils.rate_limit_key_pattern(worker_id) == f"LIMITS:LIMITER/openscribe_pytest_{worker_id}/*"


@pytest.mark.parametrize("worker_id", ["worker-1", "gw", "gw-1", "gw01x", "master1"])
def test_invalid_worker_id_fails_closed(worker_id):
    with pytest.raises(RuntimeError, match="Unsafe PYTEST_XDIST_WORKER"):
        db_utils.validate_xdist_worker_id(worker_id)


def test_invalid_worker_environment_fails_during_module_import(monkeypatch):
    with monkeypatch.context() as environment:
        environment.setenv("PYTEST_XDIST_WORKER", "unsafe-worker")
        with pytest.raises(RuntimeError, match="Unsafe PYTEST_XDIST_WORKER"):
            importlib.reload(db_utils)
    importlib.reload(db_utils)


def test_worker_database_name_must_fit_postgres_identifier_limit():
    base = "a" * 60
    with pytest.raises(RuntimeError, match="63-byte identifier limit"):
        db_utils.database_url_for_worker(f"postgresql://localhost/{base}", "gw0")


def test_database_guard_rejects_application_url_matching_derived_worker_url(monkeypatch):
    with monkeypatch.context() as environment:
        environment.setenv("DATABASE_URL", "postgresql://localhost/ambient_scribe_test_gw0")
        environment.setenv("TEST_DATABASE_URL", "postgresql://localhost/ambient_scribe_test")
        environment.setenv("PYTEST_XDIST_WORKER", "gw0")
        importlib.reload(db_utils)
        with pytest.raises(RuntimeError, match="base or worker TEST_DATABASE_URL"):
            db_utils.ensure_safe_test_database_url()
    importlib.reload(db_utils)


class FakeRedis:
    def __init__(self, keys):
        self.keys = set(keys)
        self.scan_calls = []
        self.deleted_batches = []
        self.flushed = False

    def flushdb(self):
        self.flushed = True
        self.keys.clear()

    def scan(self, *, cursor, match, count):
        self.scan_calls.append((cursor, match, count))
        matches = sorted(key for key in self.keys if fnmatch.fnmatch(key, match))
        batch = matches[:count]
        return (0, batch)

    def delete(self, *keys):
        self.deleted_batches.append(keys)
        for key in keys:
            self.keys.discard(key)


def test_worker_rate_limit_cleanup_only_deletes_its_slowapi_keys():
    harness = importlib.import_module("conftest")
    gw0_pattern = db_utils.rate_limit_key_pattern("gw0")
    slowapi_key = f"LIMITS:{parse('5/5 minutes').key_for('openscribe_pytest_gw0', '127.0.0.1', 'login')}"
    redis_client = FakeRedis(
        {
            slowapi_key,
            "LIMITS:LIMITER/openscribe_pytest_gw1/127.0.0.1/login/5/5/minute",
            "unrelated:key",
        }
    )

    harness.clear_test_rate_limit_storage(redis_client, key_pattern=gw0_pattern)

    assert redis_client.flushed is False
    assert redis_client.scan_calls == [(0, gw0_pattern, 100)]
    assert redis_client.deleted_batches == [(slowapi_key,)]
    assert redis_client.keys == {
        "LIMITS:LIMITER/openscribe_pytest_gw1/127.0.0.1/login/5/5/minute",
        "unrelated:key",
    }


def test_master_rate_limit_cleanup_retains_full_test_database_flush():
    harness = importlib.import_module("conftest")
    redis_client = FakeRedis({"LIMITS:LIMITER/login", "unrelated:key"})

    harness.clear_test_rate_limit_storage(redis_client, key_pattern=db_utils.rate_limit_key_pattern("master"))

    assert redis_client.flushed is True
    assert redis_client.keys == set()
