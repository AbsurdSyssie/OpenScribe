from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from hvac import exceptions as hvac_exceptions


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.vault import VAULT_KV_MOUNT, vault_client


SUPPORTED_PROVIDERS = ("google", "microsoft", "cis2")
PROVIDER_NAMES = {
    "google": "Google",
    "microsoft": "Microsoft",
    "cis2": "Care Identity",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Store a Google, Microsoft, or Care Identity OIDC client secret in Vault.",
    )
    parser.add_argument("provider", choices=SUPPORTED_PROVIDERS)
    return parser


def main() -> None:
    args = _parser().parse_args()
    provider_name = PROVIDER_NAMES[args.provider]
    secret = getpass.getpass(f"{provider_name} OIDC client secret: ")
    if not secret:
        raise SystemExit("Client secret must not be empty")
    confirmation = getpass.getpass("Confirm client secret: ")
    if confirmation != secret:
        raise SystemExit("Client secret confirmation did not match")

    path = f"openscribe/oidc/{args.provider}"
    try:
        vault_client().secrets.kv.v2.create_or_update_secret(
            path=path,
            secret={"client_secret": secret},
            mount_point=VAULT_KV_MOUNT.strip("/"),
        )
    except hvac_exceptions.VaultError:
        raise SystemExit("Vault rejected the OIDC client-secret write") from None
    except Exception:
        raise SystemExit("Vault is unavailable") from None

    print(
        f"Stored {provider_name} OIDC client secret at "
        f"{VAULT_KV_MOUNT}:openscribe/oidc/{args.provider}; value not printed."
    )


if __name__ == "__main__":
    main()
