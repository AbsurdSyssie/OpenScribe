# Historical Development Roadmap

## Status

This file is retained as a summary of early vertical slices. Its unchecked items and “current data model” section had become stale: transcript retention, structured job/usage observability, durable audit events, encrypted content, provider lifecycle, and destructive account/team workflows are now implemented in substantially expanded forms.

It is not the active backlog or schema reference.

Current sources:

- [auth.md](auth.md)
- [security.md](security.md)
- [api.md](api.md)
- [dbtesting.md](dbtesting.md)
- [transcript-capture.md](transcript-capture.md)
- [stt-config.md](stt-config.md)
- [llm-providers.md](llm-providers.md)
- [admin_workspace_function_map.md](admin_workspace_function_map.md)
- Alembic migrations, SQLAlchemy models, services, and tests.

## Completed early slices

### Teams, users, account requests, and onboarding

Implemented capabilities include:

- normalized team/user identity and manager-scoped creation;
- public account requests with leader/system-admin review;
- temporary-password or activation setup;
- restricted onboarding sessions;
- permanent password plus TOTP enrollment;
- optional one-time recovery codes;
- opaque hashed database sessions;
- trusted devices with bounded MFA freshness;
- self-service and manager-assisted recovery;
- suspension, reactivation, MFA reset, and hard deletion.

The original “lock/deactivate” wording was refined into explicit status and lifecycle behavior. See [auth.md](auth.md).

### Route limits and abuse controls

Redis-backed SlowAPI request limiting is implemented for authentication, account requests, uploads, and generation. Durable provider quotas use database reservations/attempts/grants and are separate from route rate limits.

A broad automatic account-lockout/unlock subsystem remains a possible focused future change, not an unfinished requirement implied by this roadmap. Any such design must address denial-of-service risk, expiry, manager scope, audit, and recovery without expanding content visibility.

### Provider configuration

The early generic provider concepts evolved into explicit STT, LLM, de-identification/clinical-NLP configs, assignments, selections, preferences, provider attempts, usage events, and durable Vault cleanup.

- Raw credentials are Vault-backed or supplied through deployment identity.
- Provider drafts/revisions use unique versioned secret references.
- Team policy is separate from credential provisioning.
- Queued work snapshots provider execution metadata.
- Quota and usage rows remain metadata-only.

### Templates, Quick Actions, Smart Phrases, and generation

Implemented capabilities include default/team/personal assets, immutable versions, copy/duplicate/import/export, personal Smart Phrases, owner-only queued generated documents, structured EMIS output, follow-ups, Quick Actions, optimistic edits, redaction/reidentification, encrypted snapshots, and durable outbox/quota lifecycle.

The earlier watcher/fork terminology is not the current persistence/API contract.

### Transcript lifecycle

Implemented capabilities include:

- owner/team transcript roots and versions;
- whole-file and live-chunked ingestion;
- encrypted current/committed text;
- working notes and post-consultation dictation;
- queued ingestion jobs and retries;
- temporary Vault-backed source-audio references with cleanup;
- team-retention snapshotting;
- expired-root denial before asynchronous physical deletion;
- transcript-root hard-delete cascades;
- redaction/PII/generated-document ownership and lifecycle.

Transcript lifecycle and retention hardening must not remain shown as unchecked planned work.

### Audit and observability

Implemented metadata includes:

- bounded `security_audit_events` with sanitization and hashed subjects;
- provider usage events;
- provider attempts/reservations/settlement;
- ingestion job metadata;
- generated-document status/usage/duration metadata;
- durable task-dispatch outbox;
- admin Usage and Audit views;
- periodic retention, audio, provider-secret, and quota lifecycle processing.

These tables/services deliberately exclude transcript, prompt, note, dictation, PII, credential, cookie, and raw provider-response content.

## Historical design decisions still valid

- Opaque hashed server-side sessions rather than JWT authority.
- Trusted devices never authenticate independently.
- Leaders are own-team scoped and never gain transcript readability.
- System administrators manage platform metadata but do not own/read transcript content.
- Hard deletion remains distinct from reversible suspension.
- Provider credentials remain outside PostgreSQL.
- Transcript-root retention/deletion owns transcript-derived content.
- Browser/API route limits and provider accounting are separate controls.

## Planning rule

Do not add unchecked features to this file. Open a focused issue/plan based on current code, identify migrations/services/routes/tests/security consequences, and update maintained operational documentation when implemented.
