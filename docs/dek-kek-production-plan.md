# DEK/KEK Architecture and Production Hardening

## Status

The application-layer envelope-encryption design in the original plan is implemented. The old statement that `*_encrypted` fields still contained plaintext is obsolete.

Current implemented behavior is documented in [security.md](security.md), [mfa-secret-encryption.md](mfa-secret-encryption.md), [transcript-capture.md](transcript-capture.md), and the implementation in `app/services/content_crypto.py` and `app/services/vault.py`.

This document now distinguishes the active cryptographic contract from production infrastructure hardening that remains deployment work.

## Implemented cryptographic contract

### Key hierarchy

- Each local user has per-user DEK metadata because the key can protect owner content and/or the user's encrypted TOTP seed.
- System-administrator possession of a DEK for authentication material does not make that account a transcript owner or grant transcript readability.
- PostgreSQL stores only the wrapped DEK and key metadata, never a plaintext DEK.
- Vault Transit owns the deployment KEK named by `VAULT_USER_CONTENT_KEK_KEY_NAME` (sample/default `openscribe-user-content-kek`).
- Password, MFA, activation, and account-recovery workflows do not rotate/delete the DEK unless the user/content lifecycle itself is being destroyed/reset explicitly.

### Content encryption

- Application code encrypts designated text/JSON fields with AES-256-GCM using `cryptography`.
- Envelopes are versioned and include nonce/ciphertext/tag material in a compact serialized form.
- Associated data binds ciphertext to immutable logical context such as owner, table/field, and record identifier.
- Decryption occurs in service code only after owner authorization and retention checks.
- Worker tasks resolve durable owner IDs from database rows rather than relying on a browser session.
- Encryption/decryption/Vault errors fail closed and never silently persist/return plaintext.

### Encrypted data classes

The current services encrypt designated fields including:

- transcript draft and committed version text;
- ingestion result text;
- Working-note freeform/structured content;
- post-consultation dictation combined text/segments;
- generated-document request/source/output/edit data;
- generated-document sections;
- redaction output and detected/manual PII values;
- encrypted TOTP seed envelopes.

Transcript/generated-document titles and selected operational metadata remain plaintext by design. See [security.md](security.md) for the current boundary.

### Vault usage

- Transit wraps/unwraps DEKs.
- KV-v2 stores small provider/platform secrets and bounded temporary source-audio blobs/references needed for asynchronous retry.
- PostgreSQL remains the primary content store for application-encrypted text/JSON.
- Temporary audio in Vault KV is an operational retry mechanism, not a long-term user-content archive.

### Deletion and recovery

- Transcript-root retention/deletion owns transcript-derived ciphertext children.
- User hard deletion removes owner content/key metadata through current cascades/services and durably cleans applicable Vault material.
- PostgreSQL plus Vault storage/bootstrap/key state form one recoverable encrypted-content set.
- Restoring the database without matching Vault Transit state can make data permanently unreadable.
- Losing Vault/key material is not fixed by issuing a new password or root token.

## Production hardening still required

The persistent local Docker profile uses a root-token/unseal-file bootstrap suitable only for controlled local/single-host development. Production should replace that operational model without changing the application cryptographic contract.

### Vault authentication

Preferred production direction:

- pre-provision Transit/KV mounts and the KEK;
- use least-privilege application/worker identities;
- use a deployment-appropriate auto-auth mechanism (for example Vault Agent with AppRole for a non-Kubernetes service deployment, or workload-native identity in an orchestrator);
- inject/renew short-lived tokens without baking long-lived/root tokens into images or `.env`;
- deny runtime access to unrelated Vault paths/operations;
- separate operator break-glass authority from application runtime authority.

The current Vault client implementation can be replaced or wrapped with `hvac` where that improves Transit/auth/token-renewal support, but package choice must preserve existing service APIs, error handling, tests, and no-secret logging. Do not describe `hvac`/Vault Agent/AppRole as already implemented merely because they are recommended here.

### KEK and DEK rotation

Production operations should define/test:

- Vault KEK rotation;
- Transit rewrap of stored wrapped DEKs without exposing plaintext DEKs unnecessarily;
- application DEK rotation/versioning where required;
- mixed envelope/key-version reads during rollout;
- interruption/idempotency/retry behavior;
- audit metadata without key/plaintext leakage;
- backup compatibility and rollback limits.

Rotation must not create administrative content-read access or couple keys to passwords.

### Availability and caching

Before adding a DEK cache, define:

- strict in-memory-only scope;
- short bounded TTL;
- process-local eviction on security/lifecycle events where practical;
- no persistence/logging/metrics labels containing keys/content;
- acceptable behavior during Vault outage;
- worker/web consistency.

Default fail-closed behavior is preferable to an unbounded key cache.

### Backup and restore

Production runbooks must coordinate:

- PostgreSQL backup;
- Vault storage/key state backup;
- bootstrap/unseal/recovery material according to the chosen Vault mode;
- restoration into an isolated environment;
- validation that representative owner ciphertext can decrypt;
- worker/Beat/provider-secret/audio-cleanup reconciliation after restore;
- documented key-loss/destructive recovery procedure.

The repository's `scripts/reset_unreadable_owner_content.py` is a destructive loss-handling tool, not key recovery. Use `--apply` only under an approved decision to delete unreadable content and issue fresh key state.

## Non-negotiable rules

- No plaintext DEKs in PostgreSQL, logs, queue payloads, audit, usage, or exceptions.
- No custom cipher/MAC/padding construction.
- Unique nonces under each key and versioned envelope parsing.
- Owner/field/record-bound associated data.
- No plaintext fallback on crypto/Vault errors.
- No password-derived content keys.
- No manager/admin content recovery path.
- No database-only backup claim for encrypted-content recoverability.
- No long-lived Vault root token as the production application identity.

## Verification

Changes to crypto/key lifecycle require focused tests for:

- encrypt/decrypt round trips and envelope versions;
- wrong owner/field/record AAD failure;
- malformed envelope and unavailable/wrong Vault key failure;
- user/password/MFA recovery preserving content access;
- key/content cleanup on transcript/user/team deletion;
- no plaintext in persisted fields, logs, audit, task payloads, or errors;
- restore/rewrap/rotation tooling where introduced.

Once a production hardening item is implemented, update the maintained security/environment/deployment documentation rather than treating this plan as evidence that it already exists.
