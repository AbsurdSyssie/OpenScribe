# Current Feature Backlog

## Status

This file contains only confirmed remaining product/engineering work. Completed implementation plans for dictation, Resend/account recovery, Working note, live STT, persistent Docker, provider credentials, and other shipped slices were removed from the TODO and are documented in the operational references linked below.

Use issues/focused plans for implementation detail. Do not use this file as a substitute for migrations/services/tests/security review.

## Highest-priority product transitions

### Retire the `/home` compatibility landing

Current state:

- normal-user/team-leader login still lands on `/home`;
- `/workspace` is the canonical Scribe/Account/Preferences/Library/Team shell;
- `/transcribe` and `/settings` redirect to canonical workspace routes.

Remaining:

- redirect full normal-user/team-leader login directly to `/workspace`;
- ensure every `/home` feedback/action has a canonical equivalent;
- update browser/auth tests and remove stale return paths;
- retire `/home` only after explicit regression validation.

References: [home_brief.md](home_brief.md), [workspace.md](workspace.md), [auth.md](auth.md).

### Remove or decide compatibility/prototype routes

Review and explicitly retain, promote, or remove:

- `/legacy-admin`;
- `/admin2`;
- `/transcribe-glm-2`;
- `/transcribe-claude`;
- `/transcriber_col_changes`.

Do not let preview routes become accidental product contracts. Any promotion must include route/auth/cache/CSP/browser coverage and documentation updates.

## Capture quality and resilience

### Controlled microphone/VAD tuning

Run synthetic/approved real-microphone testing across:

- short medicine names;
- doses/units;
- fast stop/start speech;
- background room noise;
- quiet speakers/laptop microphones;
- long uninterrupted speech;
- browser backgrounding/device changes.

Tune only with measured regression evidence:

- live pre-roll/overlap;
- silence/redemption thresholds;
- forced-flush boundary;
- whole-file voice-only pre/trailing buffers;
- minimum accepted speech duration.

References: [live_stt.md](live_stt.md), [transcript-capture.md](transcript-capture.md).

### Durable live-chunk recovery

Current live capture has durable server jobs after acceptance but no durable browser-side queue for a failed/unaccepted local chunk.

Potential focused work:

- bounded browser/reconnect retry state without storing transcript text;
- gap/sequence diagnostics using metadata only;
- safe resume after reload/network interruption;
- multi-device/session ownership rules;
- explicit user messaging that avoids claiming lossless capture.

### Dictation refinements

Post-consultation dictation is implemented, including purpose-specific STT selection, preview/save, append-only segments, editable combined text, generation inclusion, encryption, and workspace UI.

Remaining candidates:

- targeted freeform + structured generation coverage for dictation source precedence;
- optional explicit “finalize pass” only if a real lifecycle need is identified;
- controlled real-mic VAD tuning;
- UX refinement for prompting after consultation stop without interrupting queued transcription.

Do not re-plan the implemented tables/routes/provider policy as future work.

## Authentication and recovery

### Recovery-code self-service

Recovery-code generation/hash storage exists. A documented recovery-code option on the MFA challenge with forced TOTP re-enrollment is not part of the current route contract.

A focused implementation would require:

- one-time code verification/consumption;
- restricted recovery authority, not immediate indefinite full access;
- forced TOTP re-enrollment and replacement recovery-code decision;
- session/trusted-device revocation;
- rate limiting/audit/no-enumeration behavior;
- browser/API/route-audit tests.

References: [account_recovery_brief.md](account_recovery_brief.md), [auth.md](auth.md).

### External identity provider decision

No Auth0/external-IdP account authority exists today. Before implementation define per-account auth authority, linking/provisioning, password/MFA recovery owner, app-session/trusted-device behavior, deprovisioning, audit, migration, rollback, and break-glass policy.

Do not run local and external password recovery against one identity without an explicit model.

## Admin and operations

### Provider draft/cleanup observability

Potential improvements:

- list/age stale abandoned drafts/revisions;
- operator metrics for pending/failed provider-secret cleanup;
- safe retry/escalation actions with live-reference guards;
- alerting without raw reference/credential/provider-body exposure.

### Production Vault identity and key operations

Persistent Docker intentionally uses local bootstrap/root material. Production work remains:

- least-privilege runtime identities/auto-auth;
- token renewal and mount/key provisioning;
- coordinated PostgreSQL/Vault backup/restore drills;
- KEK rewrap/rotation and optional DEK rotation tooling;
- documented key-loss/destructive recovery procedure.

References: [dek-kek-production-plan.md](dek-kek-production-plan.md), [docker.md](docker.md), [security.md](security.md).

### Runtime topology

For production scale/isolation:

- split Celery workers by queue;
- monitor web/worker/Beat independently;
- use managed/hardened PostgreSQL, Redis, and Vault;
- add production deployment/upgrade/rollback runbooks;
- monitor outbox, quota, retention, source-audio, and provider-secret cleanup.

The single-host Compose profile remains a baseline, not the target production architecture.

### Broader browser E2E coverage

Expand Playwright coverage in the order listed in [transcribe-playwright-checklist.md](transcribe-playwright-checklist.md), then add an admin E2E matrix for team/user/provider/quota/destructive flows. Use synthetic content and disposable infrastructure only.

## Reporting and audit

Potential Usage improvements:

- shorter/custom ranges;
- P50/P95 latency;
- configurable columns/grouping;
- stronger failed-token accounting where provider telemetry supports it;
- privacy-reviewed active-seat/login adoption metrics;
- maintained pricing source for richer cost reporting.

Keep security/audit IP/activity views separate from provider Usage. Do not derive/report content classifications without an explicit privacy-reviewed metadata design.

Reference: [usage_tab.md](usage_tab.md).

## Frontend direction

A Next.js frontend is not implemented and is not required merely to finish current workspace consolidation. Before adopting it, make a separate architecture decision covering deployment/origins, session/CSRF, SSR/cache, route migration/rollback, CSP/local assets, and preservation of backend authorization.

Reference: [frontend-roadmap.md](frontend-roadmap.md).

## Clinical/product safety work

Deployment owners still need non-code operational material/processes for:

- recording consent and local clinical governance;
- clinician training that transcripts/generated notes remain drafts;
- hazard log and incident escalation;
- provider/subprocessor/residency/retention assessments;
- controlled validation with synthetic/approved material;
- explicit responsibility for reviewing identity, medicines/doses/allergies, diagnoses, examination/investigation, plan, follow-up, and safety-netting.

These must not place real patient content into repository docs/test fixtures.

## Backlog discipline

For each selected item:

1. state current implementation and exact target behavior;
2. identify affected schema/services/routes/workers/UI/configuration;
3. evaluate owner/privacy/encryption/retention/deletion/provider/quota/outbox consequences;
4. implement focused tests and route-audit changes;
5. update the closest operational document and README/index;
6. remove the item from this backlog when complete rather than leaving a duplicate historical plan.
