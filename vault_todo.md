# Vault TODO

Current repo state is development-only:

- [docker-compose.yml](/home/oscar/Documents/Code_Projects/OpenScribe/docker-compose.yml) runs `vault server -dev`
- Vault uses the dev root token
- there is no persistent Vault storage volume/config
- provider secrets disappear when Vault restarts

## Production Vault changes

- replace dev mode with normal Vault server mode
- add a real Vault config file, for example `ops/vault/vault.hcl`
- use persistent storage, preferably integrated Raft storage
- add a persistent Docker volume for `/vault/data`
- stop using the root token in application config
- create a scoped app policy limited to the OpenScribe secret paths
- use a dedicated non-root service token at minimum
- better: move app auth to AppRole or Vault Agent later
- initialize and unseal Vault for production
- keep the KV v2 mount stable, or update `VAULT_KV_MOUNT` consistently
- keep Vault bound to localhost or a private interface only

## Minimum viable production shape

- `storage "raft"` in Vault config
- mounted persistent volume for Vault data
- `vault server -config=/vault/config/vault.hcl`
- a scoped non-root token in `VAULT_TOKEN`
- `VAULT_ADDR` still pointing at the local Vault listener

## App-side code notes

No schema change is required for persistent Vault. Postgres already stores only `vault_secret_ref`.

Current app code in [app/services/vault.py](/home/oscar/Documents/Code_Projects/OpenScribe/app/services/vault.py) assumes:

- `VAULT_ADDR`
- `VAULT_TOKEN`
- `VAULT_KV_MOUNT`

That means the smallest production setup change is operational, not architectural:

- run persistent Vault
- create a scoped app token
- replace the dev `VAULT_TOKEN`

## Later hardening

- switch from static `VAULT_TOKEN` to AppRole or Vault Agent
- add auto-unseal
- document backup/restore for Raft snapshots
- add startup health checks that verify the KV mount exists and the app token can read/write the required paths
