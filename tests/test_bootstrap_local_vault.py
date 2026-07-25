import pytest

import scripts.bootstrap_local_vault as bootstrap_local_vault


def test_wait_for_vault_retries_transient_http_errors(monkeypatch):
    attempts = {"count": 0}

    class FakeResponse:
        status_code = 503

    def fake_get(url, timeout):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise bootstrap_local_vault.httpx.ReadError("connection reset")
        return FakeResponse()

    clock = {"now": 0.0}

    monkeypatch.setattr(bootstrap_local_vault.httpx, "get", fake_get)
    monkeypatch.setattr(bootstrap_local_vault.time, "time", lambda: clock["now"])
    monkeypatch.setattr(
        bootstrap_local_vault.time,
        "sleep",
        lambda seconds: clock.__setitem__("now", clock["now"] + seconds),
    )

    bootstrap_local_vault._wait_for_vault()

    assert attempts["count"] == 3


def test_retry_vault_call_retries_then_returns(monkeypatch):
    attempts = {"count": 0}
    clock = {"now": 0.0}

    def flaky_call():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("not ready")
        return "ok"

    monkeypatch.setattr(bootstrap_local_vault.time, "time", lambda: clock["now"])
    monkeypatch.setattr(
        bootstrap_local_vault.time,
        "sleep",
        lambda seconds: clock.__setitem__("now", clock["now"] + seconds),
    )

    assert bootstrap_local_vault._retry_vault_call("test call", flaky_call) == "ok"
    assert attempts["count"] == 3


def test_retry_vault_call_raises_after_timeout(monkeypatch):
    clock = {"now": 0.0}

    monkeypatch.setattr(bootstrap_local_vault.time, "time", lambda: clock["now"])
    monkeypatch.setattr(
        bootstrap_local_vault.time,
        "sleep",
        lambda seconds: clock.__setitem__("now", clock["now"] + seconds),
    )

    with pytest.raises(SystemExit, match="Vault test call failed after waiting"):
        bootstrap_local_vault._retry_vault_call("test call", lambda: (_ for _ in ()).throw(RuntimeError("still down")))


def test_read_secret_missing_file_fails_closed_and_points_to_docker_docs(tmp_path):
    missing = tmp_path / "root-token"

    with pytest.raises(SystemExit, match="root token is missing at") as excinfo:
        bootstrap_local_vault._read_secret(missing, label="root token")

    assert "docs/docker.md" in str(excinfo.value)


def test_read_secret_empty_file_fails_closed_and_points_to_docker_docs(tmp_path):
    empty = tmp_path / "root-token"
    empty.write_text("\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="root token is empty at") as excinfo:
        bootstrap_local_vault._read_secret(empty, label="root token")

    assert "docs/docker.md" in str(excinfo.value)


def test_read_secret_returns_stripped_value(tmp_path):
    secret = tmp_path / "root-token"
    secret.write_text("synthetic-not-a-real-token\n", encoding="utf-8")

    assert bootstrap_local_vault._read_secret(secret, label="root token") == "synthetic-not-a-real-token"
