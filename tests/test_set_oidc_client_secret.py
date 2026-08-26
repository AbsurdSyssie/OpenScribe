import pytest
from hvac import exceptions as hvac_exceptions

from scripts import set_oidc_client_secret


class _FakeKvV2:
    def __init__(self, write):
        self._write = write

    def create_or_update_secret(self, **kwargs):
        return self._write(**kwargs)


class _FakeVaultClient:
    def __init__(self, write):
        self.secrets = type(
            "FakeSecrets",
            (),
            {"kv": type("FakeKv", (), {"v2": _FakeKvV2(write)})()},
        )()


def test_oidc_secret_script_uses_hidden_confirmation_and_exact_scoped_payload(
    monkeypatch,
    capsys,
):
    raw_secret = "synthetic-google-client-secret"
    prompts = []
    writes = []
    answers = iter((raw_secret, raw_secret))

    def hidden_prompt(prompt):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr(set_oidc_client_secret.getpass, "getpass", hidden_prompt)
    monkeypatch.setattr(
        "builtins.input",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("the client secret must not use an echoed input prompt")
        ),
    )
    monkeypatch.setattr(
        set_oidc_client_secret,
        "vault_client",
        lambda: _FakeVaultClient(lambda **kwargs: writes.append(kwargs)),
    )
    monkeypatch.setattr(
        set_oidc_client_secret.sys,
        "argv",
        ["set_oidc_client_secret.py", "google"],
    )

    set_oidc_client_secret.main()

    captured = capsys.readouterr()
    assert prompts == ["Google OIDC client secret: ", "Confirm client secret: "]
    assert writes == [
        {
            "path": "openscribe/oidc/google",
            "secret": {"client_secret": raw_secret},
            "mount_point": set_oidc_client_secret.VAULT_KV_MOUNT.strip("/"),
        }
    ]
    assert raw_secret not in captured.out
    assert raw_secret not in captured.err


def test_oidc_secret_script_stores_cis2_at_its_fixed_vault_path_without_printing_the_secret(
    monkeypatch,
    capsys,
):
    raw_secret = "synthetic-cis2-client-secret"
    prompts = []
    writes = []
    answers = iter((raw_secret, raw_secret))

    def hidden_prompt(prompt):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr(set_oidc_client_secret.getpass, "getpass", hidden_prompt)
    monkeypatch.setattr(
        set_oidc_client_secret,
        "vault_client",
        lambda: _FakeVaultClient(lambda **kwargs: writes.append(kwargs)),
    )
    monkeypatch.setattr(
        set_oidc_client_secret.sys,
        "argv",
        ["set_oidc_client_secret.py", "cis2"],
    )

    set_oidc_client_secret.main()

    captured = capsys.readouterr()
    assert prompts == ["Care Identity OIDC client secret: ", "Confirm client secret: "]
    assert writes == [
        {
            "path": "openscribe/oidc/cis2",
            "secret": {"client_secret": raw_secret},
            "mount_point": set_oidc_client_secret.VAULT_KV_MOUNT.strip("/"),
        }
    ]
    assert "Care Identity" in captured.out
    assert "openscribe/oidc/cis2" in captured.out
    assert raw_secret not in captured.out
    assert raw_secret not in captured.err


def test_oidc_secret_script_confirmation_mismatch_prevents_vault_write(
    monkeypatch,
    capsys,
):
    raw_secret = "synthetic-microsoft-client-secret"
    answers = iter((raw_secret, "different-confirmation"))
    monkeypatch.setattr(
        set_oidc_client_secret.getpass,
        "getpass",
        lambda _prompt: next(answers),
    )
    monkeypatch.setattr(
        set_oidc_client_secret,
        "vault_client",
        lambda: (_ for _ in ()).throw(
            AssertionError("Vault must not be contacted after a confirmation mismatch")
        ),
    )
    monkeypatch.setattr(
        set_oidc_client_secret.sys,
        "argv",
        ["set_oidc_client_secret.py", "microsoft"],
    )

    with pytest.raises(SystemExit, match="confirmation did not match") as rejected:
        set_oidc_client_secret.main()

    captured = capsys.readouterr()
    assert raw_secret not in str(rejected.value)
    assert raw_secret not in captured.out
    assert raw_secret not in captured.err


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (
            hvac_exceptions.VaultError("synthetic-google-client-secret"),
            "Vault rejected the OIDC client-secret write",
        ),
        (
            RuntimeError("synthetic-google-client-secret"),
            "Vault is unavailable",
        ),
    ],
    ids=("vault-rejection", "vault-unavailable"),
)
def test_oidc_secret_script_vault_failure_is_generic_and_does_not_leak(
    monkeypatch,
    capsys,
    failure,
    message,
):
    raw_secret = "synthetic-google-client-secret"
    answers = iter((raw_secret, raw_secret))

    def fail_write(**_kwargs):
        raise failure

    monkeypatch.setattr(
        set_oidc_client_secret.getpass,
        "getpass",
        lambda _prompt: next(answers),
    )
    monkeypatch.setattr(
        set_oidc_client_secret,
        "vault_client",
        lambda: _FakeVaultClient(fail_write),
    )
    monkeypatch.setattr(
        set_oidc_client_secret.sys,
        "argv",
        ["set_oidc_client_secret.py", "google"],
    )

    with pytest.raises(SystemExit, match=message) as rejected:
        set_oidc_client_secret.main()

    captured = capsys.readouterr()
    assert raw_secret not in str(rejected.value)
    assert raw_secret not in repr(rejected.value)
    assert raw_secret not in captured.out
    assert raw_secret not in captured.err
