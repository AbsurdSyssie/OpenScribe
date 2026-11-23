import json

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


def test_runtime_policy_grants_only_scoped_runtime_operations(monkeypatch):
    from app.services import vault

    monkeypatch.setattr(bootstrap_local_vault, "VAULT_KV_MOUNT", "app-secrets")
    monkeypatch.setattr(vault, "VAULT_TRANSIT_MOUNT", "app-transit")
    monkeypatch.setattr(vault, "VAULT_USER_CONTENT_KEK_KEY_NAME", "content-kek")
    for name in (
        "RESEND_API_KEY_VAULT_REF",
        "CSRF_SECRET_VAULT_REF",
        "OIDC_SUBJECT_HASH_SECRET_VAULT_REF",
    ):
        monkeypatch.delenv(name, raising=False)

    paths = json.loads(bootstrap_local_vault.runtime_policy())["path"]

    assert paths["app-secrets/data/openscribe/stt/team/*"]["capabilities"] == ["create", "read", "update"]
    assert paths["app-secrets/metadata/openscribe/stt/team/*"]["capabilities"] == ["delete"]
    assert paths["app-secrets/data/openscribe/oidc/*"]["capabilities"] == ["read"]
    assert paths["app-transit/decrypt/content-kek"]["capabilities"] == ["update"]
    assert paths["app-transit/rewrap/content-kek"]["capabilities"] == ["update"]
    assert paths["app-transit/datakey/plaintext/content-kek"]["capabilities"] == ["update"]
    assert paths["auth/token/lookup-self"]["capabilities"] == ["read"]
    assert paths["auth/token/renew-self"]["capabilities"] == ["update"]
    assert "app-secrets/data/*" not in paths
    assert "sys/*" not in paths
    assert "auth/token/create" not in paths


def test_bootstrap_renews_healthy_scoped_token_without_root_token(tmp_path, monkeypatch):
    unseal_key = tmp_path / "unseal-key"
    runtime_token = tmp_path / "runtime-token"
    root_token = tmp_path / "root-token"
    unseal_key.write_text("synthetic-unseal-key", encoding="utf-8")
    runtime_token.write_text("synthetic-runtime-token", encoding="utf-8")
    monkeypatch.setattr(bootstrap_local_vault, "UNSEAL_KEY_FILE", unseal_key)
    monkeypatch.setattr(bootstrap_local_vault, "ROOT_TOKEN_FILE", root_token)
    monkeypatch.setattr(bootstrap_local_vault, "RUNTIME_TOKEN_FILE", runtime_token)
    monkeypatch.setattr(bootstrap_local_vault, "_wait_for_vault", lambda: None)
    monkeypatch.delenv("LOCAL_VAULT_FORCE_PROVISION", raising=False)
    calls = {"renew": 0, "clients": []}

    class FakeToken:
        def lookup_self(self):
            return {"data": {"policies": ["openscribe-runtime"], "period": 86400}}

        def renew_self(self):
            calls["renew"] += 1

    class FakeSystem:
        def is_initialized(self):
            return True

        def is_sealed(self):
            return False

    class FakeClient:
        def __init__(self, *, url, token=None):
            self.url = url
            self.token = token
            self.sys = FakeSystem()
            self.auth = type("Auth", (), {"token": FakeToken()})()
            calls["clients"].append(self)

    monkeypatch.setattr(bootstrap_local_vault.hvac, "Client", FakeClient)
    monkeypatch.setattr(bootstrap_local_vault, "ensure_vault_kv_ready", lambda: pytest.fail("healthy restart must not provision"))
    monkeypatch.setattr(bootstrap_local_vault, "ensure_user_content_transit_ready", lambda: pytest.fail("healthy restart must not provision"))

    bootstrap_local_vault.bootstrap_local_vault()

    assert not root_token.exists()
    assert calls["renew"] == 1
    assert calls["clients"][-1].token == "synthetic-runtime-token"


def test_initialized_vault_without_runtime_or_root_token_fails_closed(tmp_path, monkeypatch):
    unseal_key = tmp_path / "unseal-key"
    root_token = tmp_path / "root-token"
    runtime_token = tmp_path / "runtime-token"
    unseal_key.write_text("synthetic-unseal-key", encoding="utf-8")
    monkeypatch.setattr(bootstrap_local_vault, "UNSEAL_KEY_FILE", unseal_key)
    monkeypatch.setattr(bootstrap_local_vault, "ROOT_TOKEN_FILE", root_token)
    monkeypatch.setattr(bootstrap_local_vault, "RUNTIME_TOKEN_FILE", runtime_token)
    monkeypatch.setattr(bootstrap_local_vault, "_wait_for_vault", lambda: None)

    class FakeSystem:
        def is_initialized(self):
            return True

        def is_sealed(self):
            return False

    class FakeClient:
        def __init__(self, *, url, token=None):
            self.sys = FakeSystem()

    monkeypatch.setattr(bootstrap_local_vault.hvac, "Client", FakeClient)

    with pytest.raises(SystemExit, match="root token is missing"):
        bootstrap_local_vault.bootstrap_local_vault()

    assert not runtime_token.exists()
