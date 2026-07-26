# OpenScribe Contributor Brief

## Source of truth

Use [`AGENTS.md`](AGENTS.md) as the repository-wide contributor rule set and [`docs/README.md`](docs/README.md) as the documentation index.

When prose conflicts with implementation, inspect in this order:

1. Alembic migrations and database constraints;
2. models and service/domain code;
3. FastAPI routes/dependencies and schemas;
4. focused tests and `app/api_route_audit.py`;
5. maintained operational documentation;
6. historical roadmap/brief/plan files.

Do not implement obsolete table names, watcher abstractions, route paths, or role rules from historical documents.

## Non-negotiable architecture

### Privacy and ownership

- Transcript-derived content belongs to exactly one normal user/team leader.
- Team and provider policy context does not grant content visibility.
- Team leaders/system administrators can manage authorized metadata/policy/accounts but cannot read another user's transcript, Working note, dictation, generated documents, prompts, redaction/PII, or source audio.
- System-administrator accounts are admin-only and cannot own transcripts.
- Content access is owner-first and cross-owner lookups use non-disclosing behavior where defined.
- Do not add transcript-derived sharing/export without an explicit privacy/authorization design.

### Transcript lifecycle

- The transcript root owns retention/deletion for transcript-derived content.
- Persisted ingestion modes are `whole_file` and `live_chunked`.
- Whole-file/live jobs, Working note, dictation, generated documents, redaction/PII, source-audio cleanup, and related lifecycle metadata follow current service/model relationships.
- Team retention is snapshotted server-side and cannot be extended by user payload.
- Expired roots are denied before periodic physical cleanup.
- Manual transcript/user/team deletion is hard delete under current cascades/cleanup; there is no undo grace period.

### Reversible versus destructive account actions

- Suspension is the reversible manager access-stop action.
- Reactivation currently forces password-change onboarding and clears prior MFA trust.
- Eligible leaders can suspend/reactivate and hard-delete eligible non-system-admin users in their own team.
- System administrators can manage across teams subject to self/protected/last-active-admin rules.
- Hard user/team deletion removes content/assets/auth/key/provider state through current cascades and durable external cleanup.

The old rule that team leaders cannot delete users is obsolete.

### Encryption and secrets

- Designated owner/authentication content is encrypted with versioned AES-GCM envelopes under per-user DEKs.
- Vault Transit wraps DEKs under the deployment KEK.
- Password reset/recovery does not rotate/destroy the user DEK.
- Provider credentials and selected platform secrets live in Vault/deployment identity, not PostgreSQL.
- Provider drafts/revisions inherit required credentials by copying them to draft-owned unique versioned Vault paths; they do not alias the active root reference.
- Cleanup uses durable exact-reference intents, retries, compensation, and live-reference guards.
- Crypto/Vault/redaction/provider errors fail closed where content/confidentiality requires it; never fall back to plaintext.

### Queued work and quotas

- Business rows and deterministic task-dispatch outbox rows are committed transactionally.
- Immediate broker publish is attempted; Beat retries pending outbox rows every second.
- Retention, source-audio cleanup, provider-secret cleanup, and quota lifecycle run every 10 seconds.
- Provider credentials are resolved before an attempt is marked submitted.
- Definite pre-dispatch credential failure does not consume provider quota.
- Duplicate delivery uses database claims/idempotency; losing workers cannot fail/settle winning work.
- Queue/outbox/attempt/audit/usage payloads contain metadata only.

### Generated content

- Every generation creates an owner-only generated document.
- Transcript, Working note, and dictation are distinct labelled sources.
- Dirty Working-note edits save before enqueue; generation blocks if all saved sources are empty.
- Source content is redacted before LLM dispatch and allowed placeholders are reidentified afterward.
- Structured EMIS output uses only:
  - `problem`
  - `history`
  - `family_history`
  - `social_history`
  - `examination`
  - `comment`
  - `tasks`
  - `investigations`
- Backend validation remains authoritative.
- Generated text is always a draft requiring clinician review.

### Reusable assets

Current reusable assets are platform/team/personal Templates, platform/team/personal Quick Actions, and personal Smart Phrases with explicit version/scope/authorization rules.

- Team assets are directly discoverable/usable under current authorization; there is no implemented watcher model.
- Copy/duplicate/import creates independent roots/versions as defined by current services.
- Bundle import/export transfers portable content only, never ownership/team/creator/version/active/usage authority.
- Reusable configuration must not contain patient/transcript content.

The old watcher/fork-as-primary-sharing model is obsolete.

### Provider policy

- System administrators provision credential-bearing providers/configs.
- Leaders select/clear eligible options for their own team.
- Consultation STT and post-consultation dictation STT can use separate purpose selections.
- Multiple LLM adapters/presets, including Gemini Enterprise, are supported under current policy.
- De-identification and clinical NLP selections remain distinct.
- Queued work snapshots execution metadata so later policy edits do not retarget existing work.
- Provider management never grants content visibility.

The old “one fixed transcription/pseudonymisation provider” description is obsolete.

## Browser and API boundaries

- Canonical user workspace: `/workspace`.
- `/home` remains the current normal-user post-login compatibility landing until migration completes.
- `/transcribe` and `/settings` redirect to canonical workspace routes.
- Canonical system-admin workspace: `/admin`.
- `/api/v1` is the JSON boundary and every route must appear in `app/api_route_audit.py`.
- Browser unsafe cookie-authorized requests require current CSRF and same-origin checks.
- Session/trusted-device/CSRF cookies remain `HttpOnly`.
- Production browser runtime dependencies remain same-origin and CSP-compatible.
- Authenticated/content/API pages use no-store behavior according to current middleware.

## Engineering workflow

For each change:

1. state target behavior and current behavior;
2. identify affected schema/models/services/routes/workers/UI/configuration;
3. evaluate ownership, privacy, encryption, retention, deletion, provider, quota, outbox, and audit consequences;
4. implement the smallest coherent vertical slice;
5. add/update unit, integration/API, migration, authorization, lifecycle, browser, and security tests as applicable;
6. update `app/api_route_audit.py` for API route changes;
7. update the closest operational document and root README/index for user-facing/setup changes;
8. keep historical evidence immutable and add newer dated evidence instead.

A mandatory per-day `docs/progress/<date>.md` file is not part of the current repository workflow. Use commits, PR descriptions, focused plans/issues, tests, and maintained docs as the change record unless a task explicitly requires a progress artifact.

## Testing and documentation

- `docs/testing.md`: general/API/UI/security workflows;
- `docs/dbtesting.md`: database isolation/migration/real-connection behavior;
- `scripts/audit_api_auth.py`: route manifest/access probe;
- `.github/scripts/check-operational-docs.py`: maintained-doc path/link consistency;
- `.github/workflows/docker-smoke.yml`: documentation/script/Compose/build/start/restart health gate.

Do not remove tests or documentation to make a change pass. Do not copy secrets, raw provider responses, or real patient/transcript/note content into fixtures, logs, issues, evidence, or documentation.
