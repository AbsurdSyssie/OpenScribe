# Original Project Phases

## Status

**Historical foundation roadmap.** This file records the original high-level build sequence. Most listed capabilities are implemented, but several names and abstractions were superseded during development. It is not a current schema, route, or backlog contract.

Use the [documentation index](README.md) for current operational behavior and `AGENTS.md` for implementation invariants. Use migrations/models/services/tests as the source of truth for schema and lifecycle details.

## Original phases

### Phase 1: foundation

- repository bootstrap;
- Docker Compose;
- PostgreSQL;
- Redis;
- Vault;
- FastAPI skeleton;
- Alembic migrations;
- pytest setup;
- baseline documentation.

Current state: implemented. The repository now supports both host development and a persistent restartable single-host Docker profile. See [setup.md](setup.md), [docker.md](docker.md), and [testing.md](testing.md).

### Phase 2: identity and onboarding

- teams and users;
- account requests;
- sessions;
- password reset;
- MFA;
- account lock/deactivation behavior.

Current state: implemented and expanded to include activation/setup email, trusted devices, manager recovery, suspension/reactivation, hard deletion, self-service account changes, and encrypted TOTP seeds. See [auth.md](auth.md) and [security.md](security.md).

### Phase 3: transcript core

- per-user encryption keys;
- transcript roots and versions;
- creation on recording start;
- draft update and commit/version flows;
- transcript-root deletion cascade.

Current state: implemented and expanded to whole-file/live ingestion, working notes, dictation, queued jobs, encrypted content, and owner workspace behavior. See [transcript-capture.md](transcript-capture.md) and [workspace.md](workspace.md).

### Phase 4: pseudonymisation persistence

- redaction runs/entities;
- lazy/reused redaction for transcript versions.

Current state: implemented and expanded to owner-visible minimized PII metadata, explicit owner-only reveal, manual PII, generation-time redaction, and post-generation reidentification. See [security.md](security.md) and [api.md](api.md).

### Phase 5: reusable assets

- Templates and versions;
- Quick Actions and versions;
- discoverability/personal/team behavior;
- watch/fork concepts.

Current state: implemented through platform default, team, and personal assets plus versioning/duplication/import/export. The original watcher abstraction is not the current contract. Smart Phrases are personal editor configuration. See [api.md](api.md), [editor-smart-phrases.md](editor-smart-phrases.md), and the role tutorials.

### Phase 6: generated documents

- generated documents and sections;
- freeform and structured EMIS generation;
- JSON validation;
- immediate deletion.

Current state: implemented and expanded to durable outbox/quota/provider-attempt lifecycle, encrypted source/request/output snapshots, follow-ups, Quick Actions, working note/dictation sources, redaction/reidentification, optimistic edits, and safe failure metadata. See [api.md](api.md), [llm-providers.md](llm-providers.md), and [transcript-capture.md](transcript-capture.md).

### Phase 7: provider layer

Original terms included generic provider, team credential, team policy, user preference, and usage-event concepts.

Current state: implemented through explicit STT/LLM/de-identification provider/config/assignment/selection models, Vault-backed credential lifecycle, purpose-specific policy, user preferences, provider attempts/quotas, and usage events. The original generic table names are not the current schema. See [stt-config.md](stt-config.md), [llm-providers.md](llm-providers.md), [usage_tab.md](usage_tab.md), and [environment.md](environment.md).

### Phase 8: audit, retention, and jobs

- audit events;
- background jobs;
- retention cleanup;
- user/team deletion workflows.

Current state: implemented and expanded to security audit sanitization, durable task-dispatch outbox, provider quota attempts, temporary source-audio cleanup, provider-secret cleanup, encrypted-content deletion boundaries, and hard-delete preflight/cascades. See [security.md](security.md), [environment.md](environment.md), [dbtesting.md](dbtesting.md), and [admin_workspace_function_map.md](admin_workspace_function_map.md).

## Current planning rule

Do not append new work to this historical phase list. Create a focused plan/issue that states the current implementation, intended change, migrations/services/tests, security/privacy consequences, and documentation updates. Once implemented, update the relevant operational reference rather than leaving the plan as the only description.
