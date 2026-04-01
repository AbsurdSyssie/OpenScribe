# At-Rest Encryption Rollout Plan

## Purpose

Implement real application-layer encryption for all transcript-derived content, using a DEK/KEK model that:

- keeps transcript-derived content private to the owning user
- allows password resets without data loss
- allows account recovery without tying data access to the user password
- preserves the existing owner-only authorization model
- preserves hard-delete and retention behavior

## Current state

The repo already marks confidential fields with `*_encrypted`, but they are currently plain application strings, not ciphertext.

Current confidential content fields:

- `transcripts.current_draft_text_encrypted`
- `transcript_versions.text_encrypted`
- `redaction_runs.redacted_text_encrypted`
- `redaction_entities.original_value_encrypted`
- `generated_documents.original_output_text_encrypted`
- `generated_documents.edited_output_text_encrypted`
- `generated_document_sections.original_text_encrypted`
- `generated_document_sections.edited_text_encrypted`
- `transcript_ingestion_jobs.result_text_encrypted`

Current secret handling:

- Vault is already used for provider secrets in [app/services/vault.py](/home/oscar/Documents/Code_Projects/OpenScribe/app/services/vault.py)
- `user_encryption_keys` exists in the architecture docs, but is not implemented in the runtime path yet
- transcript services currently write plaintext directly into `*_encrypted` columns

This means the repo has the right field names and architecture intent, but not the actual encryption layer.

## Target behavior

### Core design

- Each normal user gets one random DEK at account creation.
- The DEK is never stored plaintext in Postgres.
- The DEK is wrapped by a KEK managed in Vault.
- Postgres stores only the wrapped DEK and key metadata.
- All transcript-derived content is encrypted with the user DEK before hitting the database.
- Decryption happens only in backend service code after ownership checks.

### Password reset and recovery

- User passwords remain authentication material only.
- User passwords do not encrypt transcript-derived data.
- Admin password reset does not rotate or destroy the user DEK.
- After password reset, the user can still decrypt their historical content because the same wrapped DEK remains available.
- Admins still do not get transcript visibility by default; recovery means the account remains usable, not that admins can read the data.

This is the only design that satisfies both:

- admin-managed recovery/reset
- transcript confidentiality independent of the user password

## Checklist Before Coding

### Target behavior

- real ciphertext at rest for all transcript-derived text
- no plaintext transcript-derived data in Postgres rows
- no password-coupled encryption
- no widened transcript visibility for leaders or system admins

### Affected schema/modules/endpoints

Schema:

- add `user_encryption_keys`
- likely add encryption metadata fields to confidential tables

Modules:

- [app/services/transcripts.py](/home/oscar/Documents/Code_Projects/OpenScribe/app/services/transcripts.py)
- [app/services/templates.py](/home/oscar/Documents/Code_Projects/OpenScribe/app/services/templates.py)
- [app/services/redaction.py](/home/oscar/Documents/Code_Projects/OpenScribe/app/services/redaction.py)
- [app/services/vault.py](/home/oscar/Documents/Code_Projects/OpenScribe/app/services/vault.py)
- user/account creation flows in [app/main.py](/home/oscar/Documents/Code_Projects/OpenScribe/app/main.py) and admin services
- schema/response layers that currently expose `*_encrypted` as plaintext text

Endpoints:

- all transcript create/update/commit endpoints
- audio ingestion paths
- note generation/follow-up/quick action paths
- transcript fetch/list/detail endpoints

### Affected tests

- migration tests
- transcript API tests
- authorization tests
- deletion cascade tests
- worker/job tests
- encryption/decryption unit tests
- browser UI tests that render transcript text

### Architecture risks

- breaking owner-only access by decrypting before authz
- leaking plaintext through logs, exceptions, or debug output
- partial rollout leaving mixed plaintext/ciphertext reads without version handling
- breaking deletion semantics if ciphertext blobs move outside transcript-root ownership
- changing structured-note behavior accidentally while encrypting note payloads

### Reference docs

- [AGENTS.md](/home/oscar/Documents/Code_Projects/OpenScribe/AGENTS.md)
- [docs/DatabasePlan.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/DatabasePlan.md)
- [docs/goals.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/goals.md)

## Proposed architecture

### 1. Key hierarchy

- KEK: Vault-managed key, preferably via Transit
- DEK: random 256-bit symmetric key per user
- wrapped DEK: stored in Postgres in `user_encryption_keys`

Recommended `user_encryption_keys` fields:

- `id`
- `user_id`
- `wrapped_dek`
- `kek_key_name`
- `kek_key_version`
- `is_active`
- `created_at`
- `rotated_at`

### 2. Ciphertext envelope

Do not store raw ciphertext bytes in the text columns without structure.

Store a versioned envelope, for example JSON:

```json
{
  "v": 1,
  "alg": "aes-256-gcm",
  "kid": "transit/openscribe-user-content/42",
  "nonce": "...",
  "ciphertext": "..."
}
```

This gives a clean path for:

- future rotation
- algorithm changes
- mixed old/new row reads during migration

### 3. Encryption service boundary

Add a dedicated content-encryption service, for example:

- `create_user_dek(...)`
- `wrap_user_dek(...)`
- `unwrap_user_dek(...)`
- `encrypt_user_content(user_id, plaintext)`
- `decrypt_user_content(user_id, envelope)`

Rules:

- decrypt only after owner authorization is resolved
- never log plaintext or envelopes with user text
- cache unwrapped DEKs only in-process and briefly, if at all

### 4. Ownership-preserving runtime flow

For transcript-owned content:

- resolve transcript owner
- authorize current user against owner
- unwrap owner DEK
- decrypt or encrypt as needed

For queued jobs:

- store `owner_user_id` on every transcript-derived child and queued job snapshot
- workers use `owner_user_id` to resolve the correct DEK
- no worker path should depend on the current logged-in user

## Rollout phases

### Phase 1. Key infrastructure

- add `user_encryption_keys` table and model
- add Vault helper methods for wrap/unwrap using a named KEK
- generate a DEK on user creation
- backfill DEKs for existing users

Checkpoint:

- schema checkpoint: one active DEK row per user
- auth/ownership checkpoint: no new content routes
- lifecycle/deletion checkpoint: deleting a user must delete their wrapped DEK row
- docs/tests checkpoint: migration and key-service tests added

### Phase 2. Encryption service and transcript root

- add a content crypto service
- switch transcript create/update/commit flows to encrypt on write and decrypt on read
- cover:
  - `transcripts.current_draft_text_encrypted`
  - `transcript_versions.text_encrypted`
  - `transcript_ingestion_jobs.result_text_encrypted`

Checkpoint:

- schema checkpoint: ciphertext envelope format decided and stable
- auth/ownership checkpoint: decrypt after ownership check only
- lifecycle/deletion checkpoint: transcript delete still cascades normally
- docs/tests checkpoint: transcript API and worker tests updated

### Phase 3. Transcript-derived children

- encrypt/decrypt:
  - `redaction_runs.redacted_text_encrypted`
  - `redaction_entities.original_value_encrypted`
  - `generated_documents.original_output_text_encrypted`
  - `generated_documents.edited_output_text_encrypted`
  - `generated_document_sections.original_text_encrypted`
  - `generated_document_sections.edited_text_encrypted`

Checkpoint:

- schema checkpoint: all transcript-derived content uses the same envelope version
- auth/ownership checkpoint: team leaders/admins still cannot read note/transcript content
- lifecycle/deletion checkpoint: generated docs remain rooted under transcript delete
- docs/tests checkpoint: document/redaction tests updated

### Phase 4. Backfill existing plaintext rows

- add an idempotent backfill script
- detect plaintext vs encrypted envelope format
- encrypt historical rows in batches
- emit counts only, never content

Checkpoint:

- schema checkpoint: mixed-mode reads supported during backfill window
- auth/ownership checkpoint: no fallback path exposes raw DB values directly
- lifecycle/deletion checkpoint: backfill does not create orphan content
- docs/tests checkpoint: backfill dry-run and live-run docs added

### Phase 5. Rotation and recovery hardening

- add DEK rotation flow per user
- keep prior wrapped DEK until all owned content is re-encrypted
- add audit events for key creation, wrap/unwrap failures, and rotation completion
- add operational docs for password reset, recovery, and incident response

Checkpoint:

- schema checkpoint: DEK versioning and active key semantics remain explicit
- auth/ownership checkpoint: no admin content-reading path introduced
- lifecycle/deletion checkpoint: deleting a user removes all transcript-derived content and key records
- docs/tests checkpoint: rotation and recovery tests added

## Important implementation decisions

### Use Vault Transit for KEK operations

Prefer Transit over storing raw KEKs in app config. Vault should perform wrap/unwrap or encrypt/decrypt operations for the DEK lifecycle.

### Keep one DEK per user in MVP

This matches the documented architecture and keeps migration scope manageable. Do not silently switch to per-record DEKs in this slice.

### Do not use the user password as the KEK

That would block admin recovery and contradict the repo’s architecture.

### Keep templates and quick actions out of this slice

Unless they intentionally store transcript-derived text, they stay normal configuration data. The target is transcript-derived content first.

## Testing plan

### Unit tests

- DEK generation/wrap/unwrap
- envelope encrypt/decrypt round-trips
- invalid envelope/version handling
- wrong-owner decryption denial at service boundary

### Integration/API tests

- transcript create/update/commit returns usable plaintext via API while DB stores ciphertext
- generated docs and redaction artifacts decrypt correctly for the owner
- queued jobs use owner DEK correctly
- password reset does not break historical transcript access

### Authorization tests

- leader/admin still cannot read transcript-derived plaintext
- metadata-only views remain metadata-only

### Migration/backfill tests

- backfill converts plaintext rows to encrypted envelopes
- mixed plaintext/ciphertext reads remain supported during rollout

### Deletion/cascade tests

- transcript delete still hard-deletes transcript-derived rows
- system-level user delete removes wrapped DEK rows and transcript-derived children

### MFA/auth flow tests

- onboarding/password change/TOTP flows remain independent of data decryption
- admin password reset preserves later owner access to old content

## Open items that need explicit decisions

- exact ciphertext envelope format in `TEXT` vs moving to `JSONB`
- whether structured context JSON should stay plaintext metadata or move under owner encryption if it becomes transcript-derived
- whether DEK unwrap should be cached per request or per worker task
- whether audit events should log unwrap attempts at per-request granularity or only failures/rotations

## Recommended execution order

1. Schema + Vault transit key helpers
2. User DEK creation/backfill
3. Transcript root encryption path
4. Generated document/redaction encryption path
5. Historical data backfill
6. Rotation + operational hardening

## Checklist Completion After Coding

This plan is complete, but implementation is not started in this document.

When the coding slice begins, completion requires:

- code complete
- tests added and passing
- docs updated
- backfill/runbook documented
- open issues explicitly noted
