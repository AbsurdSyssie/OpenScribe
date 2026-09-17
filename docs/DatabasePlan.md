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

 The consultation-split schema has direct transcript-owned analysis, draft, draft-topic, batch, batch-topic, outcome, and execution rows. Their direct transcript and parent foreign keys cascade so transcript-root deletion is structurally safe. Confirmation derives owner, team, and retention from the transcript or checked parent, then atomically binds the confirmed batch to its intent and queues one bundled generation execution, reservation, and outbox row. Every new batch also binds a current immutable materialization `TranscriptVersion`; for Working-note-only or dictation-only sources, this is an encrypted empty draft version created at confirmation after freshness validation. The truthful analysis version and redaction-run links remain null for those source-only analyses. Workers require the batch-bound version and never create one. The provider-neutral runtime makes one adapter call from encrypted frozen snapshots. A trustworthy mixed response retains independently validated encrypted outcomes and can trigger one frozen-provider automatic recovery; a recovery response is parsed only for its failed-topic request set. The optional verifier cannot queue while recovery is active: it snapshots the complete set after successful recovery or stable survivors after recovery fails definitively. Submitted work without a durable response never auto-retries. Keep Available materializes survivors once, is blocked during recovery or verification, and leaves them as clinician-review drafts. New manual Retry and Keep actions enforce the default-off deployment and owner gate before root lookup; already durable work may still finish. See [consultation-splitting-preference.md](consultation-splitting-preference.md).

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

Template-note regeneration creates a new immutable generated-document child. `parent_generated_document_id`, `regeneration_lineage_id`, and positive `regeneration_revision_no` preserve its ancestry; a unique lineage/revision pair prevents duplicates and a partial unique lineage index permits one active revision only. The child retains encrypted copies of the latest clinician-edited output and new steering, while reusing the source document's frozen generation snapshots. Split topic membership is now many-to-one so a confirmed topic may retain several immutable revisions. Split revisions use only the confirmed batch/topic snapshots and retain the generic stored title `Consultation split note`; owner-only response projection may reveal the topic name.

The passive split schema adds nullable `consultation_split_batch_topic_id` and `consultation_split_topic_uuid` membership fields. Their composite foreign key proves that a split child belongs to its confirmed batch topic while retaining the stable topic UUID across batches. Deleting a generated child does not delete the batch or topic. Split topic titles, plans, source/template/PII snapshots, proposals, requests, responses, and recoverable outputs use encrypted text fields.

The passive persistence service derives every split row's owner, team, transcript and retention deadline from the active transcript or a checked parent. It validates denormalized ancestry before decrypting. Split source fingerprints are canonical lowercase SHA-256 digests. A batch references its analysis with `RESTRICT`, so deleting an analysis cannot erase confirmed provenance; direct transcript foreign keys remain the deletion root.

`consultation_split_intents` is the owner-scoped root for one logical split Generate request. It stores owner/team/transcript identity, a UUID idempotency key unique per owner, the transcript retention deadline, and an owner-DEK encrypted JSON selected-template/version and ordinary-generation snapshot. It deliberately has no live template/version foreign key or plaintext source, prompt, template, or configuration field. The service-only atomic start commits a new intent with any new reusable analysis, execution, provider-attempt reservation, and deterministic outbox row in one transaction; it publishes only after that commit. Same-key replays reuse the original intent only while the retained transcript and linked analysis remain available with matching owner/team/transcript/retention ancestry; deliberate different keys may share the same current analysis and dispatch work. Its nullable analysis link uses `SET NULL`: an independently removed analysis does not erase the idempotency record or its selected-at-submit snapshot, while direct transcript cascade remains the deletion root.

The one-note consume service locks the canonical owner, transcript, and intent scope, validates analysis and retention ancestry, and compares current clinical source state with the encrypted saved analysis state. Candidate-template metadata does not make that comparison stale. It decrypts and validates the submitted snapshot, requires the live template parent to remain active and accessible, and uses the saved version, prompt, structured sections, and options rather than a later revision. It flushes one ordinary document, quota reservation, provider attempt, and deterministic outbox row in one transaction, marks the intent `bypassed`, binds the document, commits, then tries publication. It does not resolve credentials or call a provider. A failed publish leaves the pending outbox for retry; a pre-commit failure rolls back all new rows. `bypassed` remains consumed if its child document is deleted.

 Split-draft confirmation locks owner, transcript, dictations, intent, analysis, and the draft parent. It reads selected references under that parent lock, then locks exact template parents and versions in UUID order before draft topics. This matches template deletion and replacement while the parent lock prevents a concurrent draft replacement from changing selected references. It requires an active current draft with two to six separate-note topics, exactly one primary, and an eligible selected LLM configuration/model. After the source freshness proof, it snapshots the current transcript draft with `allow_empty=True` and `mark_transcript_ready=False`, then binds that version to the immutable batch. This creates the required document lineage for source-only work without rewriting the analysis's truthful null version/redaction links. Historical batches remain nullable for migration compatibility; queued materialization rejects a missing or wrong-root binding. The materialization-version link uses `SET NULL`, so removing an individual transcript version cannot delete the split tree; transcript-root deletion still removes the tree through its direct transcript cascade. It creates the immutable batch, topics, and pending outcomes, encrypted canonical request, write-once provider snapshot, generation execution, reservation, and deterministic outbox row, then marks the draft and intent `confirmed` in the same transaction. Queue construction failure rolls all of that back. `consultation_split_batches.intent_id` is nullable for pre-confirmation/passive data but unique when present. A same-intent replay returns that batch before timestamp or source checks. Validated outcomes bind their accepting execution through nullable `accepted_execution_id` (`SET NULL` on execution deletion) with an index for provenance queries. The `validated` PostgreSQL enum label is append-only: downgrade removes only that link schema. Automatic recovery gets one attempt only after a durable parsed response and reuses the initial provider snapshot; an explicit retry resolves the current provider and receives a new quota reservation/outbox execution. Keep Available is idempotent and database-only, and its batch lock prevents concurrent recovery from creating duplicate children. Verification is implemented; only deployment-like rollout checks remain.

Historical Slice 3 foundation (superseded): every split analysis, generation, or verification execution received one reserved token attempt and one deterministic outbox row in the caller's transaction. Their source was the execution UUID; each retry created another UUID and attempt number under its parent. The attempt FK was unique and could not coexist with document or ingestion-job references. Usage events could link to an execution, but Slice 3 emitted none; a partial unique index permitted at most one completed usage event per execution. Dispatch delivery only validated queued metadata; it did not submit an attempt, change execution state, call a provider, or read encrypted content. Quota expiry, failed publishing, transcript expiry, and owner deletion terminalized attempts and removed split outbox metadata before the transcript cascade cleared nullable execution links. Team deletion retained the established provider-telemetry behaviour: it deleted its team-scoped attempt and usage rows. At that historical stage, partial recovery, clinician retry, verification, and rollout evidence had not yet been implemented; current behavior is described above and in [consultation-splitting-preference.md](consultation-splitting-preference.md).

Current bundled verification is an optional, separate split execution with its own provider attempt and deterministic outbox task. It is submitted once and never retried. Its recoverable provider response and any corrected structured output remain owner-encrypted; the accepted generation output is never replaced. The runtime rechecks the saved provider configuration identity, dispatch state, source retention, and attempt deadline before and after the provider call, so a late response cannot revive expired work. The batch stores only safe verification status, bounded reason, completion time, and correction count. Failure is fail-open after correct quota settlement: original validated outputs still materialize as clinician-review drafts.

The split-execution LLM-config reference is nullable only for passive rows created before production queueing was available. The production queue requires a real config in the transcript's team. A queued or processing execution blocks deletion of that config; a terminal execution does not. Config deletion clears the terminal FK but leaves its encrypted, non-secret provider snapshot. Split rows never hold a Vault reference or credential.

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
