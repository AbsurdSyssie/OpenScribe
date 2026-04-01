import pytest

from app.dev_safety import (
    ensure_safe_dev_bind,
    find_exposed_services,
    is_local_bind_host,
    parse_docker_compose_port_host,
    resolve_dev_bind_host,
)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_is_local_bind_host_accepts_loopback_values(host):
    assert is_local_bind_host(host) is True


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.com", "", None])
def test_is_local_bind_host_rejects_non_loopback_values(host):
    assert is_local_bind_host(host) is False


def test_ensure_safe_dev_bind_rejects_remote_bind_without_opt_in():
    with pytest.raises(ValueError, match="DEV_ALLOW_REMOTE_BIND=true"):
        ensure_safe_dev_bind(host="192.168.1.10", allow_remote=False)


def test_ensure_safe_dev_bind_allows_remote_bind_with_explicit_opt_in():
    ensure_safe_dev_bind(host="192.168.1.10", allow_remote=True)


def test_ensure_safe_dev_bind_allows_localhost_without_opt_in():
    ensure_safe_dev_bind(host="127.0.0.1", allow_remote=False)


def test_resolve_dev_bind_host_defaults_to_loopback():
    assert resolve_dev_bind_host(host=None, allow_remote=False) == "127.0.0.1"


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "[::]"])
def test_resolve_dev_bind_host_normalizes_wildcard_to_loopback_without_opt_in(host):
    assert resolve_dev_bind_host(host=host, allow_remote=False) == "127.0.0.1"


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "[::]"])
def test_resolve_dev_bind_host_allows_wildcard_with_opt_in(host):
    assert resolve_dev_bind_host(host=host, allow_remote=True) == "0.0.0.0"


def test_resolve_dev_bind_host_allows_explicit_remote_bind_with_opt_in():
    assert resolve_dev_bind_host(host="192.168.1.10", allow_remote=True) == "192.168.1.10"


def test_resolve_dev_bind_host_rejects_remote_bind_without_opt_in():
    with pytest.raises(ValueError, match="DEV_ALLOW_REMOTE_BIND=true"):
        resolve_dev_bind_host(host="192.168.1.10", allow_remote=False)


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("127.0.0.1:5432\n", "127.0.0.1"),
        ("0.0.0.0:6379\n", "0.0.0.0"),
        ("[::1]:8200\n", "::1"),
        (":::8200\n", "::"),
        ("", None),
    ],
)
def test_parse_docker_compose_port_host(output, expected):
    assert parse_docker_compose_port_host(output) == expected


def test_find_exposed_services_reports_non_local_bindings():
    exposed = find_exposed_services(
        {
            "postgres:5432": "127.0.0.1",
            "redis:6379": "0.0.0.0",
            "vault:8200": "::",
        }
    )
    assert exposed == ["redis:6379=0.0.0.0", "vault:8200=::"]
