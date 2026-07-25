# Production DEK/KEK Plan

## Purpose

Define a production-grade DEK/KEK design for OpenScribe that:

- encrypts transcript-derived content at rest
- supports the implemented encrypted TOTP-seed path without changing transcript authorization
- preserves the current owner-only access model
- allows password reset and account recovery without data loss
- avoids home-grown cryptographic primitives
- fits the current FastAPI, SQLAlchemy, Celery, Postgres, and Vault stack

This plan is deliberately opinionated. It favors widely used primitives and infrastructure over cleverness.

## Recommended stack

### Packages

- `cryptography`
  - use `cryptography.hazmat.primitives.ciphers.aead.AESGCM`
  - reason: standard Python crypto package, OpenSSL-backed, supports AEAD and associated data
- `hvac`
  - use the maintained Python Vault client instead of hand-written HTTP calls for new key-management code
  - reason: better support for Transit, auth flows, token renewal integration, and testability

### Vault infrastructure

- Vault Transit as the KEK layer
- Vault Agent auto-auth in front of the app and workers
- AppRole for the current non-Kubernetes deployment model

### Storage

- Postgres stores ciphertext envelopes for text/JSON transcript-derived content
- Postgres stores only wrapped DEKs, never plaintext DEKs
- Vault stores KEKs and performs wrap/unwrap or data-key operations
- Vault KV remains for provider secrets only, not owner content blobs

## Why this stack

This aligns best with current production guidance:

- Vault Transit is explicitly designed for “cryptography as a service” and data-key generation.
- Vault AppRole is intended for machine/service workflows, and HashiCorp recommends batch tokens with it.
- Vault Agent auto-auth is the supported way to obtain and renew Vault tokens without hard-coding a long-lived app token.
- OWASP guidance is clear that keys should not be stored in plaintext and should be protected in a vault/HSM-style service.
- `cryptography` AEAD gives us supported AES-GCM primitives without inventing our own cipher format.

## Production decisions

### 1. Use one DEK per content-owning user

Keep the architecture already described in this repo:

- one random DEK per normal user / team leader account that may own transcripts
- the implemented authentication path also provisions a DEK for every newly created local user, including teamless system-admin-only accounts, because TOTP seeds are encrypted under the owning user's DEK

This amends the earlier system-admin exclusion rather than changing content ownership: a DEK may protect authentication material, but it does not make a system administrator a transcript owner or grant transcript readability.

This preserves account recovery:

- passwords remain auth credentials only
- resetting a password does not touch the user DEK
- MFA reset does not touch the user DEK
- account recovery restores access to the account, not visibility to admins

### 2. Use Vault Transit to wrap the DEK

Recommended KEK shape:

- Transit mount: `transit/`
- key name: `openscribe-user-content-kek`

Recommended DEK lifecycle:

1. On user creation, call Vault Transit `generate_data_key`.
2. Receive:
   - plaintext DEK
   - Vault-wrapped DEK ciphertext
3. Store only the wrapped DEK in Postgres.
4. Discard the plaintext DEK after use.
5. When content must be encrypted/decrypted, unwrap the DEK via Transit and hold it only in request/task memory.

This is better than generating the DEK in app code because it uses Vault RNG and a standard data-key workflow.

### 3. Use app-side AEAD for the actual content

Encrypt content in the application with `AESGCM`, not by sending every transcript value to Vault Transit.

Reason:

- far better throughput than per-field network round trips to Vault
- works naturally for DB-stored ciphertext
- still preserves KEK separation because the DEK is wrapped by Vault
- matches the common envelope-encryption pattern used in production web apps

### 4. Bind ciphertext to owner and field using AAD

Every encrypted value should include associated data derived from immutable ownership/location metadata.

Recommended AAD template:

```text
openscribe:v1:{table}:{field}:{owner_user_id}:{record_id}
```

Examples:

- `openscribe:v1:transcripts:current_draft_text_encrypted:{owner}:{transcript_id}`
- `openscribe:v1:generated_documents:edited_output_text_encrypted:{owner}:{document_id}`

This prevents ciphertext from being copied between rows or users without detection.

### 5. Do not use Vault KV as the user-content store

Current Vault KV usage for retry audio is an operational stopgap, not the long-term content-encryption design.

Production rule:

- Vault Transit is the KEK service
- Vault KV is for small secrets and references
- transcript-derived content should live in Postgres or a dedicated blob store as application-encrypted ciphertext

## Recommended schema shape

### `user_encryption_keys`

Add or implement:

- `id`
- `user_id`
- `dek_version`
- `wrapped_dek`
- `kek_mount`
- `kek_key_name`
- `kek_key_version`
- `status`
  - `active`
  - `retiring`
  - `retired`
- `created_at`
- `rotated_at`
- unique active-key constraint per user

Notes:

- `wrapped_dek` stores the Vault Transit ciphertext returned by `generate_data_key`
- `kek_key_version` is the Transit key version used when the DEK was wrapped
- `dek_version` is the application-facing version referenced by ciphertext envelopes

### Ciphertext envelope

Keep the existing `Text` columns for now and store a compact JSON envelope string.

Recommended envelope:

```json
{
  "v": 1,
  "alg": "AES-256-GCM",
  "dkv": 1,
  "n": "<base64 nonce>",
  "ct": "<base64 ciphertext+tag>"
}
```

Why keep the current `Text` columns:

- far less schema churn
- easier backfill
- current API and UI code already flows through these fields
- these fields are not used for SQL search predicates today

Do not add one nonce/tag column per field. The envelope is simpler and easier to version.

## Recommended package and service boundary

### New service module

Add a dedicated service such as `app/services/content_crypto.py`.

Recommended public surface:

- `ensure_user_dek(db, user_id)`
- `get_active_user_key(db, user_id)`
- `encrypt_text_for_owner(db, owner_user_id, table, field, record_id, plaintext)`
- `decrypt_text_for_owner(db, owner_user_id, table, field, record_id, envelope_text)`
- `encrypt_bytes_for_owner(...)`
- `decrypt_bytes_for_owner(...)`
- `rewrap_user_dek(...)`
- `rotate_user_dek(...)`

### Recommended implementation choices

- use `hvac` for Transit and auth flows
- use `AESGCM.generate_key(bit_length=256)` only if Vault data-key generation is not adopted
- use `AESGCM.encrypt/decrypt` with 96-bit random nonces
- do not implement custom ciphers, custom padding, or custom MAC composition
- do not use ORM “magic encrypted fields” as the primary abstraction

The last point matters for this repo: ownership and AAD need service-layer context that generic ORM field wrappers do not naturally have.

## Vault production setup

### Replace the current static token model

Current code uses a static `VAULT_TOKEN` and hand-written HTTP calls.

Production replacement:

- Vault Agent on app and worker hosts
- Agent auto-auth using AppRole
- app talks to local Agent/Vault listener
- `hvac` uses the short-lived token supplied by Agent

This is the recommended path for the current infrastructure shape because:

- the deployment is service-oriented, not end-user interactive
- AppRole is designed for machines/services
- Agent handles renewal and avoids pinning a root/service token in app config

### Transit key operations to use

- `generate_data_key`
  - create the user DEK and wrapped DEK
- `decrypt_data`
  - recover plaintext DEK for in-memory use
- `rewrap_data`
  - rewrap stored DEKs after KEK rotation without exposing plaintext DEKs again
- `rotate_key`
  - rotate the KEK version in Vault

## Current DB and webapp implications

### What can stay the same

- owner-only authorization logic stays the same
- API routes stay the same
- UI still receives plaintext from the backend after authz passes
- transcript deletion remains transcript-root based
- password reset and MFA recovery flows remain auth-only workflows

### What must change in the DB layer

#### 1. `*_encrypted` fields must stop storing plaintext

Today these are plaintext despite the names.

At minimum, the following must move to ciphertext envelopes:

- `transcripts.current_draft_text_encrypted`
- `transcript_versions.text_encrypted`
- `redaction_runs.redacted_text_encrypted`
- `redaction_entities.original_value_encrypted`
- `transcript_ingestion_jobs.result_text_encrypted`
- `generated_documents.original_output_text_encrypted`
- `generated_documents.edited_output_text_encrypted`
- `generated_documents.failed_provider_output_redacted_encrypted`
- `generated_document_sections.original_text_encrypted`
- `generated_document_sections.edited_text_encrypted`

#### 2. Some non-`*_encrypted` fields need classification

The current schema has content-bearing fields that are not obviously safe metadata:

- `generated_documents.follow_up_prompt_text`
- `generated_documents.prompt_snapshot_text`
- `transcripts.structured_context_json`
- `generated_documents.structured_context_json`

Implementation rule:

- if the field can contain transcript-derived content, encrypt it under the owner DEK
- if the field is pure configuration metadata, leave it plaintext

This classification must be explicit before rollout.

#### 3. Async worker rows need reliable owner key resolution

`transcript_ingestion_jobs` currently does not carry `owner_user_id` or `team_id`.

For robust worker-side encryption/decryption, add:

- `owner_user_id`
- `team_id`

Reason:

- Celery workers should not depend on fragile multi-hop joins for key resolution
- queued jobs should snapshot the ownership context they need to resolve the correct DEK

This aligns better with the repo’s architectural rule that transcript-derived records should carry owner/team scope where defined.

### What must change in the webapp/service layer

#### Write path

Before any confidential content hits SQLAlchemy:

1. authorize the caller
2. resolve owner user id
3. fetch active wrapped DEK row
4. unwrap DEK through Vault Transit
5. encrypt with `AESGCM`
6. store the envelope string

#### Read path

Before any confidential content is returned:

1. load the row
2. verify owner access first
3. resolve the owner DEK
4. decrypt the envelope
5. return plaintext to the existing schema/response model

Do not decrypt before authz.

### Current API naming implication

Current request/response schemas use names like `current_draft_text_encrypted` even though they carry plaintext over the wire.

Recommendation:

- do not rename the public API in the same slice as the encryption rollout
- keep the API contract stable
- change only the DB persistence semantics first

This is awkward naming, but it minimizes rollout risk.

## Audio and retry blob implications

### Text data

Text and JSON transcript-derived content should use the per-user DEK envelope described above.

### Whole-file retry audio

Do not keep long-term retry/source audio in Vault KV.

For the current infrastructure, the safest path is:

- encrypt retry audio with the owner DEK in the app
- store the ciphertext in Postgres as `BYTEA` plus small metadata fields
- keep retention short and deletion explicit

If object storage is introduced later, keep the same envelope format and move only the blob backing store.

The important architectural rule is:

- Vault is the KEK/key-management layer
- not the repository for retained user-content blobs

## Account recovery and password reset

### Supported recovery behavior

This design preserves the current model:

- admin resets password
- user completes onboarding/MFA again
- historical transcript-derived content remains decryptable because the wrapped DEK did not change

### What recovery does not mean

This does not give leaders or system admins application-level access to the user’s decrypted content.

Operationally, anyone with both:

- database access
- Vault access to unwrap DEKs

is part of the infrastructure trust boundary. That is normal for server-side encrypted SaaS systems, but it is not a product permission grant.

## Deletion and retention implications

### Transcript deletion

No architectural change:

- transcript root remains the retention root
- deleting the transcript deletes transcript-derived ciphertext rows and blob refs immediately

### User deletion

Delete order must be:

1. transcript-derived rows and encrypted blobs
2. any queued-job retry blobs
3. wrapped DEK rows in `user_encryption_keys`
4. user row

Do not delete the wrapped DEK first. That can strand ciphertext that still needs hard deletion from external stores.

### KEK rotation vs DEK rotation

- KEK rotation:
  - rotate the Transit key
  - rewrap stored `wrapped_dek` values with `rewrap_data`
  - no content re-encryption needed
- DEK rotation:
  - create a new DEK version for the user
  - new writes use the new DEK
  - background job re-encrypts old content
  - retire old DEK only after all owned content is migrated

## Rollout plan

### Current MFA implementation and production gap

The current development/pre-production implementation stores new and re-enrolled TOTP seeds as AES-256-GCM envelopes using the existing per-user DEK. AAD follows the existing format with `user_mfa_methods`, `secret`, the owning user UUID, and the MFA-method UUID. Legacy plaintext TOTP rows remain readable temporarily without read-time migration or key creation.

This compatibility path is not production-complete. Before production enforcement, explicitly plan and deliver:

- a plaintext-TOTP backfill and removal of the plaintext fallback;
- a decision whether MFA/authentication material requires a separate DEK or key purpose from transcript-derived content;
- Vault Agent/auth-path operation, timeouts, availability monitoring, and alerting suitable for an MFA dependency;
- KEK/DEK rotation and envelope/key-version history; and
- an operator recovery/runbook, including the destructive unreadable-key reset procedure.

For current behavior and the reset tool's dry-run/`--apply` semantics, see [mfa-secret-encryption.md](mfa-secret-encryption.md).

### Phase 0: foundation decision

Before coding:

- adopt `cryptography` and `hvac`
- commit to Vault Transit + Agent/AppRole
- explicitly reject “password-derived content encryption”
- explicitly reject “Vault KV as owner-content store”

### Phase 1: key infrastructure

- add `user_encryption_keys`
- add Transit client wrapper using `hvac`
- add user-DEK creation on account creation and account approval flows
- backfill DEKs for existing content-owning users

### Phase 2: transcript root encryption

- encrypt/decrypt transcript draft, committed versions, ingestion result text
- add worker-side owner-key resolution for ingestion jobs

### Phase 3: generated docs and redaction

- encrypt/decrypt generated docs, sections, redaction outputs, and any transcript-derived prompt snapshots

### Phase 4: retry audio and other retained blobs

- remove owner-content blob persistence from Vault KV
- move to application-encrypted blob persistence

### Phase 5: backfill and rotation

- detect plaintext rows
- batch-migrate to ciphertext envelopes
- add KEK rewrap and DEK rotation jobs

## Checklist before coding

### Target behavior

- ciphertext at rest for transcript-derived content
- one wrapped DEK per content-owning user
- password reset does not affect data access
- admin role does not imply transcript readability

### Affected schema/modules/endpoints

Schema:

- `user_encryption_keys`
- owner/team fields for async job rows
- possible blob-metadata columns for encrypted retry/source audio

Modules:

- `app/services/vault.py`
- new `app/services/content_crypto.py`
- `app/services/transcripts.py`
- `app/services/templates.py`
- `app/services/redaction.py`
- `app/services/admin.py`
- `app/services/auth.py`
- `app/main.py`

### Affected tests

- migration tests
- encryption round-trip unit tests
- auth/ownership tests
- worker/job tests
- transcript delete/user delete cascade tests
- backfill tests
- account recovery/password reset regression tests

### Architecture risks

- decrypting before authz
- leaving content-bearing JSON/prompt fields in plaintext
- deleting DEKs before ciphertext/blob cleanup completes
- storing owner-content blobs in Vault KV long term
- accidentally granting admin read access via operational helper routes

## Checkpoints during implementation

### Schema checkpoint

- one active DEK row per content-owning user
- ingestion jobs carry owner context needed by workers
- ciphertext envelope version is explicit and stable

### Auth and ownership checkpoint

- decrypt only after owner authz
- leaders and system admins still manage metadata but do not read transcript-derived content

### Lifecycle and deletion checkpoint

- transcript-root deletion still cascades cleanly
- user deletion removes ciphertext rows/blobs, then wrapped DEK rows
- retention jobs can still hard-delete all transcript-derived children

### Docs and tests checkpoint

- API docs clarify that encrypted-at-rest does not change over-the-wire response semantics
- setup docs describe Vault Agent/AppRole requirements
- tests prove password reset does not break owner decryptability

## Explicit non-recommendations

Do not do these:

- do not derive content keys from the user password
- do not keep using a static root Vault token in production
- do not use Vault KV as the long-term content store
- do not ship custom crypto primitives or custom cipher formats beyond a small envelope wrapper
- do not rely on transparent ORM encryption packages for owner-scoped transcript content

## Sources

- HashiCorp Vault Transit: https://developer.hashicorp.com/vault/docs/secrets/transit
- HashiCorp Vault AppRole: https://developer.hashicorp.com/vault/docs/auth/approle
- HashiCorp Vault Agent auto-auth: https://developer.hashicorp.com/vault/docs/agent-and-proxy/autoauth
- `hvac` Transit usage: https://python-hvac.org/en/stable/usage/secrets_engines/transit.html
- OWASP Key Management Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Key_Management_Cheat_Sheet.html
- `cryptography` AEAD / AESGCM: https://cryptography.io/en/latest/hazmat/primitives/aead/
