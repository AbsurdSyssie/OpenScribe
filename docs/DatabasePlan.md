# Persistence Architecture

## Status

The original database design mixed durable invariants with proposed table names and unimplemented watcher/generic-provider abstractions. It is retained here as a current persistence map instead.

Authoritative schema sources are:

- Alembic migrations;
- `app/models.py`;
- database/service constraints;
- [dbtesting.md](dbtesting.md);
- feature operational references in the [documentation index](README.md).

Do not implement a table/field merely because it appeared in an older version of this document.

## Durable invariants

### Ownership first

- Transcript-derived content belongs to exactly one normal user/team leader.
- `owner_user_id` represents the only content owner.
- `team_id` supplies policy/accounting context and never implies team visibility.
- System administrators manage metadata/configuration but do not own/read transcripts.
- Cross-owner content lookups generally use non-disclosing not-found behavior.

### Transcript root owns retention/deletion

The transcript root owns implemented children including:

- committed transcript versions;
- ingestion jobs/source-audio lifecycle;
- Working note;
- post-consultation dictation/segments;
- generated documents/sections/request-source snapshots;
- redaction runs/entities/manual PII;
- provider-attempt/task-dispatch/quota relationships as defined by current services.

Team retention is snapshotted server-side onto the transcript root. Expired roots are denied by services before periodic physical cleanup. Manual transcript/user/team deletion is hard delete with current cascades and durable external cleanup; there is no undo grace period.

### Configuration is not content

Reusable/configuration domains include:

- platform/team/personal Templates and immutable versions;
- platform/team/personal Quick Actions and immutable versions;
- personal Smart Phrases;
- provider configs, assignments, selections, preferences, and policy;
- quotas, attempts, usage, audit, and cleanup metadata.

Configuration visibility/management never grants owner-content access. Reusable assets must not contain patient/transcript data.

The earlier proposed `template_watchers`, `quick_action_watchers`, generic `providers`, `team_provider_credentials`, and generic `team_provider_policies` are not the current schema contract.

## Sensitivity classes

### Encrypted owner/authentication content

Current services encrypt designated fields using versioned AES-GCM envelopes under per-user DEKs wrapped by Vault Transit. Categories include:

- transcript draft/version text;
- ingestion result text;
- Working-note and dictation content;
- generated-document request/source/output/edit fields and sections;
- redacted output/original detected/manual PII values;
- TOTP seed envelopes.

Titles, IDs, status, counts, timestamps, provider labels/snapshots, and other bounded metadata can remain plaintext where explicitly designed.

### Hashed credential/bearer material

Hash-only persistence includes:

- passwords (Argon2id);
- session tokens;
- trusted-device tokens;
- activation/reset/recovery email tokens;
- recovery codes;
- provider duplicate fingerprints/subject hashes where used as non-reversible metadata.

### Vault/deployment secrets

Raw provider credentials and selected platform secrets live in Vault or deployment identity. PostgreSQL stores only bounded metadata/reference/status. Cleanup uses durable exact-reference intents with retries/live-reference guards.

## Identity and tenancy

### Teams

Teams provide organizational/policy scope. Important behavior:

- normalized unique names;
- server-owned default retention constrained by `MAX_RETENTION_DAYS`;
- one-team normal users/leaders;
- team deletion blocker for attached system administrators;
- hard deletion of normal members/content/configuration and durable external secret/key cleanup according to service rules.

### Users

Current user state includes normalized email, password hash, team/role, system-admin flag, account status, onboarding/MFA state, and base quota limits.

Lifecycle semantics:

- `suspended`: reversible manager action;
- `locked`: temporary security/auth-abuse state where used;
- `disabled`: stronger security/platform state;
- manager suspension/reactivation/delete are explicit and scoped;
- reactivation currently forces password-change onboarding and clears prior MFA trust;
- hard delete removes owner content/personal assets/auth state/key metadata through current cascades/cleanup;
- system-admin accounts are protected by self/last-active-admin rules.

The earlier “planned account-administration clarification” is implemented and must not remain described as future work.

### Authentication support

Current tables/services cover:

- account requests, including partial uniqueness for one pending normalized email/team-name pair;
- opaque hashed sessions with auth level/lifecycle;
- trusted devices;
- encrypted TOTP methods;
- hashed recovery codes;
- hashed single-use activation/reset/recovery email tokens;
- user-owned OIDC identities, unique by issuer/subject and by user/provider slot;
- short-lived OIDC authorization requests containing a provider key, hashed state and PKCE-verifier values, plus link-only user/session bindings;
- security audit metadata.

OIDC access, refresh, ID tokens, and raw subject claims are not persisted. The subject lookup uses a versioned, issuer-bound HMAC-SHA-256 digest with a dedicated deployment secret. Expired authorization requests are removed when a new flow starts; a callback consumes its row once. User deletion cascades linked identities and outstanding link requests.

Exact names/columns are defined by current models/migrations, not this summary.

## Encryption metadata

Per-user key metadata stores wrapped DEK and key/version/status information. PostgreSQL never stores plaintext DEKs.

- Password/MFA/account recovery preserves the DEK.
- Content encryption uses owner/table/field/record-bound associated data.
- Vault/key failure fails closed.
- PostgreSQL and Vault form one recoverable set.
- User/content hard deletion removes or durably cleans key material according to service rules.

See [security.md](security.md) and [dek-kek-production-plan.md](dek-kek-production-plan.md).

## Transcript and ingestion persistence

The transcript root records owner/team/title/status/ingestion mode/retention/timestamps and encrypted current text.

Persisted ingestion modes:

- `whole_file`;
- `live_chunked`.

Ingestion jobs store metadata such as kind/status/sequence/config snapshot/source byte-duration/safe error/time fields and encrypted result text. Whole-file retry source audio is referenced through bounded Vault storage rather than a PostgreSQL audio blob. `source_audio_expires_at` records the fixed deadline set at the original write; `source_audio_expired_at` records enforcement. A database check requires every live source reference/blob to have a deadline.

Creation/retry uses transactional durable task-dispatch metadata. Provider-attempt/quota reservation, source-audio cleanup, job claim/idempotency, and transcript reconciliation are service-layer workflows backed by explicit rows/constraints.

## Working note and dictation

One living Working note per transcript stores mode and encrypted freeform/structured content with optimistic-concurrency metadata. Mode locks after first non-empty save and unlocks when cleared.

Post-consultation dictation persists transcript-owned aggregate metadata, immutable segment sources, and encrypted combined/edited text. An intentionally empty edited combined value suppresses segment fallback.

Both remain distinct generation sources and follow transcript-root deletion/retention.

## Redaction and PII

Version-linked redaction runs store encrypted redacted output and encrypted entity originals. Manual PII uses owner-scoped keyed duplicate detection and encrypted original values.

Admin/leader metadata authority never grants reveal access. Owner reveal is an explicit protected action. Redaction/PII lifecycle follows the transcript root/version contracts.

## Generated documents

Generated documents persist owner/team/transcript/provider/template/action metadata, lifecycle status, encrypted request/source/output/edit/debug fields, usage/duration/error-safe metadata, and optional sections.

- Every generation creates a new row.
- Existing result remains after originating reusable asset deletion because required snapshots are retained and source FK can be cleared.
- Edits use optimistic concurrency.
- Structured output uses fixed EMIS keys.
- Follow-ups/Quick Actions are generated-document variants, not separate content-authority models.
- Checker metadata is non-content; debug content is encrypted owner-only/local-gated.

## Reusable assets

Current root/version patterns support platform defaults, team assets, and personal assets with normalized uniqueness and active-version invariants.

- Normal users own personal assets.
- Leaders manage Team Templates/Quick Actions in their own team.
- Smart Phrases are personal only.
- Import/export transfers portable latest-version content, not authority/version/history.
- Deletion is hard delete subject to generated-document snapshot/reference handling.

No watcher/sharing layer is implemented.

## Provider persistence

Current provider domains are explicit rather than one generic table:

- STT configs/drafts/selections;
- LLM configs/drafts/selections/user preferences/hallucination selection;
- de-identification providers/assignments/selections;
- clinical NLP selection;
- provider usage/attempt/quota metadata;
- durable retired-secret cleanup.

Provider rows store safe metadata/reference/fingerprint/status only. Draft credential inheritance copies to a draft-owned unique Vault path; it does not alias the active root reference.

Queued work snapshots execution metadata so later config/policy edits do not mutate existing work.

## Quotas, usage, outbox, and audit

### Quotas/attempts

User base limits plus grants/reservations/attempts provide authoritative token/audio accounting. `NULL` base means unlimited; `0` means no base allowance; positive grants can enable a zero-base window. Calendar windows and activation/reset semantics are enforced by current services.

### Durable task dispatch

Business row + deterministic task-dispatch outbox creation is transactional. Immediate broker publish is attempted; Beat retries pending rows every second. Publication uses claim/idempotency/backoff and terminal failure after `TASK_OUTBOX_MAX_ATTEMPTS`.

### Usage

Usage events/jobs/generated-document metadata provide aggregate reporting without storing content. Reporting telemetry is distinct from quota authority/reset windows.

### Security audit

`security_audit_events` stores bounded sanitized metadata. It excludes request bodies, credentials/tokens, transcript/prompt/note/dictation/PII/provider-response content. Login/reset subjects are HMAC digests where recorded.

Ordinary rows expire after six calendar months. `security_audit_event_holds` records a bounded system-administrator approval with owner, reason, review, expiry, renewal count and release metadata. One unreleased hold can exist per event; each approval is limited to 90 days. Active holds must retain an owner, and account deletion is blocked until an owned hold is released or transferred. Event deletion cascades its hold history.

### Operator legal content

`operator_legal_profiles` is a singleton, deployment-global and optional. Fixed-kind legal roots own draft, published and superseded versions containing validated structured JSON blocks. Constraints enforce positive revisions/version numbers, valid state timestamps and one current published version per root. Administrator references use `ON DELETE SET NULL`; legal history does not depend on an administrator account.

Published versions are immutable. Abandoned drafts become deletion candidates after 12 calendar months, superseded versions after six years, and active `legal_document_version_holds` exclude a version from deletion. Deleting an eligible version cascades its released hold history. Current published versions are not deletion candidates.

## External cleanup

Durable cleanup models/services handle:

- temporary transcript source audio;
- retired/orphan provider secrets;
- user/provider/team Vault material where applicable.

Cleanup retries, uses exact references and live-reference guards, and supports compensation when an external write succeeds but the database transaction rolls back.

## Database test requirements

Schema/service changes must test:

- constraints/normalization/uniqueness;
- owner/team/system-admin scope;
- retention and hard-delete cascades;
- encrypted/hashed persistence and fail-closed behavior;
- provider secret versioning/cleanup;
- outbox/attempt/quota concurrency/idempotency;
- migration upgrade/downgrade where supported;
- pure versus rollback-isolated versus real-connection test modes.

See [dbtesting.md](dbtesting.md).

## Change rule

For persistence changes:

1. define the current state and invariant impact;
2. add/modify migrations/models/services/constraints;
3. add focused database/security/lifecycle tests;
4. update the closest operational feature document and README/index when user-visible;
5. do not revive historical table proposals without an explicit new design.
