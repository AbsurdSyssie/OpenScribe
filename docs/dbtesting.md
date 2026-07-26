# Database Testing

This is the database-specific test reference: how tests select/reset databases, how ordinary and real-connection tests are isolated, and which persistence boundaries must remain covered. General test execution is in [testing.md](testing.md).

## Database split

Tests use `TEST_DATABASE_URL`, never `DATABASE_URL`.

Default local names:

- application/manual UI: `ambient_scribe`;
- sequential tests: `ambient_scribe_test`;
- xdist worker `gw0`: `ambient_scribe_test_gw0`;
- xdist worker `gw1`: `ambient_scribe_test_gw1`, and so on.

The worker suffix is derived from `PYTEST_XDIST_WORKER`; only unset/`master` or `gw<digits>` values are accepted. The derived PostgreSQL identifier must fit PostgreSQL's 63-byte limit.

Enforcement lives in:

- [tests/db_utils.py](../tests/db_utils.py)
- [tests/conftest.py](../tests/conftest.py)
- [tests/test_migrations.py](../tests/test_migrations.py)

## Safety guard

Before any destructive test setup, the helper resolves both application and test URLs. If the selected test URL matches `DATABASE_URL`, pytest fails immediately.

The test helper may create missing test databases and reset their `public` schemas. It must never drop or recreate the application database.

A global `/tmp/openscribe_pytest.lock` prevents a sequential run and an xdist controller from sharing/resetting the same local PostgreSQL or Redis infrastructure concurrently. Xdist workers inherit the controller's protected run.

## Ordinary database tests

The first ordinary test in a worker whose fixture closure requires `db_session`:

1. resets that worker database's `public` schema;
2. creates canonical tables from `Base.metadata`;
3. opens the test connection and root transaction.

Later ordinary database tests reuse the canonical schema. `db_session` uses SQLAlchemy `join_transaction_mode="create_savepoint"`, so application `commit()`/`rollback()` calls can execute while fixture teardown rolls back the root transaction.

The `client` and `raw_client` fixtures bind request-created sessions to the same connection/savepoint model. This keeps route-side commits isolated without per-test schema recreation.

Pure/static tests that do not resolve database fixtures skip PostgreSQL and Redis reset work.

## Real connection tests

Tests that need independent committed sessions, threads, database locks, live servers, or cross-session visibility use the `real_db_connections` marker.

These tests:

- use engine-bound sessions rather than the root rollback fixture;
- truncate trusted `Base.metadata` application tables with `RESTART IDENTITY CASCADE` before and after the test;
- exclude `alembic_version` because it is not application metadata;
- must be used sparingly and intentionally.

Do not use hand-written untrusted table names for cleanup.

## Migration tests

Migration tests own the `public` schema lifecycle and do not use the ordinary pre-test schema path. They exercise Alembic upgrades/downgrades and database constraints against actual migration state.

After a migration test, teardown invalidates the worker's canonical-schema readiness flag even if the test fails. The next ordinary database-backed test rebuilds canonical metadata before opening its rollback-isolated connection.

Test engines use `NullPool` so connections do not retain stale cached plans across schema drops.

## Redis isolation

Sequential database-backed tests clear the configured test limiter store before and after each test.

Xdist workers share the configured Redis database but use distinct SlowAPI `key_prefix` values. Cleanup scans/deletes only that worker's `LIMITS:LIMITER/<prefix>/*` keys in bounded batches; one worker must not flush another worker's keys or the application's Redis databases.

## Schema and normalization boundaries

Database/service tests should prove the following where applicable.

### Teams

- original display name is retained;
- canonical `name_key` uses normalization, trim, whitespace collapse, and case folding;
- normalized duplicates fail with conflict;
- punctuation that is not normalized away remains significant.

### Users

- emails are normalized before persistence;
- uniqueness is case-insensitive;
- new passwords are Argon2id hashes, never plaintext;
- manager-created users start in password-change onboarding;
- normal users receive per-user content-key metadata as required by current crypto services.

### Sessions and MFA

- opaque session/trusted-device/email tokens are hash-only in the database;
- auth levels and lifecycle states are explicit;
- suspension/disable/recovery/password changes revoke required authority;
- encrypted TOTP envelopes are owner/method-bound;
- recovery codes remain hash-only.

### Manager lifecycle

- leaders are limited to non-system-admin users in their own team;
- suspension blocks login without deleting content;
- reactivation applies the implemented password/MFA reset semantics;
- manager deletion is hard delete;
- preserved account-request rows clear nullable links to a deleted user.

## Provider configuration boundaries

STT, LLM, de-identification, clinical NLP, and hallucination-check tests should cover:

- team-scoped uniqueness and selection constraints;
- ready/pending setup status and active/selectable filters;
- leader selection versus system-admin credential authority;
- no raw secret or unrestricted Vault reference in API responses;
- remote HTTPS/local-development HTTP transport rules;
- provider-specific discovery and model validation;
- credential replacement/revision promotion and durable cleanup intents;
- team deletion blockers/cascades/cleanup;
- no content visibility expansion through provider management.

Provider metadata and selection rows must remain separate from owner transcript content.

## Transcript and content boundaries

### Ownership and retention

- transcript roots store owner/team and server-snapshotted retention;
- user payloads cannot extend retention;
- expired roots are filtered by every owner content service before physical cleanup;
- cross-owner access fails without disclosing content/existence beyond the route contract;
- system administrators cannot own transcripts.

### Encryption

Designated fields must contain versioned ciphertext envelopes in PostgreSQL, not plaintext. Tests should cover:

- encrypt/decrypt round trips through service APIs;
- owner-bound associated data and wrong-owner failure;
- Vault/key outage and malformed envelope failure;
- no plaintext fallback;
- password/account recovery preserving content-key access;
- deletion of key/content rows according to lifecycle rules.

### Ingestion

- source audio uses bounded Vault references for queued processing/retry;
- job creation and task-dispatch outbox creation are transactional;
- duplicate task delivery is idempotent/claim-safe;
- processing stale/deadline reconciliation is deterministic;
- result text appends in the expected sequence and is encrypted;
- terminal cleanup clears/durably queues source-audio deletion;
- a pre-dispatch credential failure does not consume provider quota.

### Generated documents and redaction

- generated content, request snapshots, sections, working-note/dictation snapshots, and redaction/PII data follow encryption/ownership rules;
- provider requests receive redacted source text;
- reidentification is owner-side after safe generation parsing;
- deletion/retention cascades cover all transcript-derived children;
- audit and task/outbox rows contain metadata only.

## Durable outbox, quota, and cleanup

Database tests should cover the transaction and concurrency boundaries for:

- deterministic task-dispatch IDs and payload mismatch rejection;
- source row + outbox atomic creation;
- immediate publish plus one-second Beat fallback;
- `FOR UPDATE SKIP LOCKED` publication without duplicate broker sends;
- retry/backoff and terminal failure after `TASK_OUTBOX_MAX_ATTEMPTS`;
- quota reservation, expansion, submission, settlement, cancellation, and stale deadlines;
- retention cleanup every 10 seconds;
- transcript-audio and provider-secret cleanup jobs, live-reference guards, and rollback compensation;
- deletion helpers that terminalize attempts/remove dispatch rows before source deletion.

Exception/provider-response text must not be persisted in these metadata tables.

## Import/export and configuration assets

Tests for templates, Quick Actions, Smart Phrases, and preference rows should verify:

- personal ownership and team-scope constraints;
- normalized unique names/triggers;
- version-root/active-version invariants;
- imported bundles cannot supply owner/team/creator/active/version/usage authority;
- preflight is read-only and confirmation is one transaction;
- 1 MiB/100-entry bundle limits;
- audit rows omit uploaded content, prompts, names, expansions, and instructions where the audit contract excludes them.

## Adding a database test

Use the least powerful fixture/marker that proves the behavior:

1. keep pure validation tests independent of PostgreSQL;
2. use ordinary `db_session`/`client` rollback isolation for most service/route tests;
3. use `real_db_connections` only for committed visibility, locks, threads, or live-server behavior;
4. use migration fixtures only for Alembic/schema-transition assertions;
5. assert both successful persistence and forbidden/cascade behavior;
6. never target `DATABASE_URL` or rely on a developer's existing application rows.

## Recovery after an interrupted run

If a test process is killed while holding the global lock, verify no pytest controller/worker remains, then remove only the stale lock file if necessary. The next database-backed test rebuilds its worker schema.

Do not repair failures by pointing `TEST_DATABASE_URL` at the application database or by disabling the equality guard.

## Documentation rule

Database behavior that changes through migrations, constraints, or persistence services must update this document and the closest operational feature document. Dated compliance evidence remains a point-in-time record and is not rewritten to match newer schemas.
