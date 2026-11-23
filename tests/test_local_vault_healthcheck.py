import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import check_local_vault


class _Response:
    def __init__(self, status: int, payload: bytes) -> None:
        self.status = status
        self.payload = payload

    def read(self, size: int) -> bytes:
        return self.payload[:size]


class _Connection:
    def __init__(self, status: int, payload: bytes) -> None:
        self.response = _Response(status, payload)
        self.request_args = None
        self.closed = False

    def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
        self.request_args = (method, path, headers)

    def getresponse(self) -> _Response:
        return self.response

    def close(self) -> None:
        self.closed = True


def _configure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    token_file = tmp_path / "runtime-token"
    token_file.write_text("scoped-token\n", encoding="utf-8")
    monkeypatch.setenv("LOCAL_VAULT_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("VAULT_ADDR", "http://vault.local:8200")


def test_runtime_healthcheck_accepts_exact_policy_and_positive_ttl(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, tmp_path)
    connection = _Connection(
        200,
        json.dumps({"data": {"policies": ["openscribe-runtime"], "ttl": 1}}).encode(),
    )
    monkeypatch.setattr(check_local_vault.http.client, "HTTPConnection", lambda *a, **k: connection)

    check_local_vault.check_runtime()

    assert connection.request_args == (
        "GET",
        "/v1/auth/token/lookup-self",
        {"X-Vault-Token": "scoped-token"},
    )
    assert connection.closed


@pytest.mark.parametrize(
    "payload",
    [
        {"data": {"policies": [], "ttl": 10}},
        {"data": {"policies": ["openscribe-runtime", "default"], "ttl": 10}},
        {"data": {"policies": ["openscribe-runtime"], "ttl": 0}},
        {"data": {"policies": ["openscribe-runtime"], "ttl": "invalid"}},
        {},
    ],
)
def test_runtime_healthcheck_rejects_wrong_policy_or_nonpositive_or_invalid_ttl(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: dict
) -> None:
    _configure(monkeypatch, tmp_path)
    connection = _Connection(200, json.dumps(payload).encode())
    monkeypatch.setattr(check_local_vault.http.client, "HTTPConnection", lambda *a, **k: connection)

    with pytest.raises(Exception):
        check_local_vault.check_runtime()

    assert connection.closed


def test_runtime_healthcheck_rejects_unavailable_or_invalid_vault_address(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, tmp_path)
    connection = _Connection(503, b"unavailable")
    monkeypatch.setattr(check_local_vault.http.client, "HTTPConnection", lambda *a, **k: connection)

    with pytest.raises(ValueError):
        check_local_vault.check_runtime()
    assert connection.closed

    monkeypatch.setenv("VAULT_ADDR", "http://user:password@vault.local:8200")
    with pytest.raises(ValueError, match="Invalid Vault address"):
        check_local_vault.check_runtime()


def test_runtime_compose_healthcheck_uses_lightweight_script_entrypoint() -> None:
    for compose_path in ("docker-compose.yml", "docker-compose.demo.yml"):
        compose = Path(compose_path).read_text(encoding="utf-8")
        assert 'test: ["CMD", "python", "scripts/check_local_vault.py"]' in compose

    code = (
        "import runpy, sys; runpy.run_path('scripts/check_local_vault.py', run_name='healthcheck_import'); "
        "assert 'hvac' not in sys.modules; assert 'bootstrap_local_vault' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-S", "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
    )
    assert result.returncode == 0, result.stderr
