# AGENTS.md

## Purpose

OpenScribe is privacy-sensitive and architecture-sensitive.

Preserve established ownership, content-access, deletion, retention, encryption, provider, authentication, quota/outbox, and structured-output contracts. Do not silently redesign them.


Make use of orwells rules when writing documentation or something for the user.

A scrupulous writer, in every sentence that he writes, will ask himself at least four questions, thus:

What am I trying to say?
What words will express it?
What image or idiom will make it clearer?
Is this image fresh enough to have an effect?
And he will probably ask himself two more:

Could I put it more shortly?
Have I said anything that is avoidably ugly?
One can often be in doubt about the effect of a word or a phrase, and one needs rules that one can rely on when instinct fails. I think the following rules will cover most cases:

Never use a metaphor, simile, or other figure of speech which you are used to seeing in print.
Never use a long word where a short one will do.
If it is possible to cut a word out, always cut it out.
Never use the passive where you can use the active.
Never use a foreign phrase, a scientific word, or a jargon word if you can think of an everyday English equivalent.
Break any of these rules sooner than say anything outright barbarous.

## Sources of truth

Not every Markdown file describes current behavior. Files named `plan`, `brief`, `roadmap`, `todo`, `design`, or similar can be historical or partially implemented.

Use evidence in this order:

1. database migrations and constraints;
2. implemented service/model/route/dependency behavior;
3. focused passing tests and route-audit manifest;
4. current runtime configuration;
5. documentation explicitly listed as operational in `docs/README.md`;
6. historical plans/briefs/roadmaps.

When these disagree:

- do not choose silently;
- preserve the stricter privacy/security boundary;
- identify the conflict;
- establish implemented behavior from code, migrations, tests, and configuration;
- update or retire stale documentation;
- escalate when an architectural invariant is affected.

Primary references:

- `docs/README.md`
- `docs/security.md`
- `docs/auth.md`
- `docs/api.md`
- `docs/environment.md`
- `docs/setup.md`
- `docs/docker.md`
- `docs/testing.md`
- `docs/dbtesting.md`
- `docs/DatabasePlan.md`
- `docs/transcript-capture.md`
- `docs/stt-config.md`
- `docs/llm-providers.md`

## Architecture invariants

### Privacy and ownership

- Transcript-derived content belongs only to its owning user.
- Transcript-derived content is not team-shareable.
- Administrative or team-leader authority does not grant access to transcripts, audio, dictation, Working notes, generated notes, prompts, redaction originals, or PII values.
- Metadata access is not content access.
- System-administrator accounts must not own transcript-derived content.
- Each normal user belongs to one team.
- Team leaders may act only within their own team.

### Transcript lifecycle

- Create the transcript root before ingesting transcript-derived content.
- The transcript root is the retention and deletion root.
- Persist draft/committed/derived owner content through established encrypted fields and version boundaries.
- Team retention is snapshotted server-side and must not be extended by later edits/user payload.
- Expired roots are unavailable before asynchronous physical cleanup.
- Transcript deletion removes all transcript-derived children through established cascades and durable cleanup.
- Working note and post-consultation dictation remain separate transcript-owned generation sources.
- Persisted ingestion modes remain `whole_file` and `live_chunked` unless explicitly extended through schema/service/API changes.

### Redaction and generation

- Run redaction only at defined workflow boundaries.
- Capture finalization or ingestion reconciliation can create/reuse a redaction preview.
- Provider-bound workflows use the appropriate saved source snapshot and redaction boundary.
- Fail closed when required redaction fails.
- Generated-document edits do not mutate transcripts, Working notes, dictation, Templates, Quick Actions, or other source material.
- Preserve generated-document provenance/snapshots when originating reusable assets are deleted.
- Every generated result remains a draft requiring clinician review.

### Structured output

- Treat `app/schemas/templates.py` and `app/schemas/transcripts.py` as structured-output contracts.
- Validate provider output before persistence/display.
- Do not add profiles, section keys, or incompatible response shapes without explicit design approval.
- Do not weaken validation to accept malformed model output.
- The current EMIS keys are `problem`, `history`, `family_history`, `social_history`, `examination`, `comment`, `tasks`, and `investigations`.

### Reusable assets

- Platform/team/personal Template and Quick Action scope is explicit.
- Smart Phrases are personal only.
- Team assets remain team-scoped and are available according to current authorization; they are not transcript-derived sharing.
- Copy/duplicate/import creates an independent root/version where the current service defines it.
- Import/export transfers portable content only, never ownership/team/creator/version/active/usage authority.
- Reusable configuration must not contain patient/transcript content.
- The historical watcher/fork-reference model is not the current schema/API contract; do not introduce it implicitly.

### Account lifecycle and deletion

- Suspension is reversible; deletion is immediate/destructive.
- Suspension, locking, or disabling revokes sessions/trusted-device authority according to the current lifecycle.
- Team leaders may suspend, reactivate, and hard-delete eligible non-system-administrator users in their own team.
- System administrators may perform those actions across teams subject to protected-account safeguards.
- Managers may not suspend/reactivate/delete themselves through manager routes.
- Do not remove the final active system administrator.
- Reactivation currently forces password-change onboarding and clears previous MFA trust.
- User/team deletion must use established deletion services.
- Block deletion rather than silently skipping unresolved cleanup.

### Encryption and provider secrets

- Vault is the KEK and provider-secret layer.
- Encrypt confidential user-owned/authentication content with the owning user's DEK.
- Store provider credentials in Vault/deployment identity; store only references/non-secret metadata in PostgreSQL.
- Never expose raw credentials or unrestricted Vault references through normal responses.
- Provider drafts/revisions that inherit a required credential copy it to a draft-owned unique versioned Vault path; they do not alias the active root reference.
- Never delete a live Vault secret before the database change removing/replacing its reference commits.
- Record retired-reference cleanup durably with the database change.
- Cleanup retries failures and verifies a reference is no longer live.
- Use existing encryption, Vault, and cleanup services.
- Do not couple password recovery to content-key rotation/deletion.

### Provider policy

- System administrators provision providers and credentials.
- Team leaders select only eligible providers/options for their own team.
- STT selection is purpose-specific, including consultation transcription and post-consultation dictation.
- Provider configuration never grants access to transcript-derived content.
- Preserve team LLM policy, user preference fallback, setup/credential state, and selection rules.
- PII-redaction and clinical-NLP selections remain separate.
- Use the established native de-identification fallback when no valid remote team selection exists.
- Queued work snapshots provider execution metadata so later policy edits do not retarget existing work.

### Asynchronous work and quotas

- Business rows and deterministic task-dispatch outbox rows are committed transactionally.
- Immediate broker publish is attempted; Beat retries pending outbox rows every second.
- Retention, transcript-audio cleanup, provider-secret cleanup, and quota lifecycle processing run every 10 seconds.
- Resolve credentials before marking a provider attempt submitted.
- Definite pre-dispatch credential failure must not consume provider quota.
- Duplicate delivery uses database claims/idempotency; a losing worker cannot fail/settle winning work.
- Task/outbox/attempt/quota/usage rows contain metadata only.

## Security

- Never interpolate user-controlled values into raw SQL.
- Use SQLAlchemy expressions or parameterized statements.
- Allowlist identifiers, sort fields, operators, and query fragments.
- Reuse maintained libraries and existing project security services.
- Do not hand-roll cryptography, authentication, authorization, CSRF, hashing, secret storage, or rate limiting.
- Use synthetic data for tests/provider inspection.
- Never weaken a constraint/test merely to make it pass.

Do not log:

- transcript-derived content;
- prompts/provider responses containing user data;
- audio content;
- redaction originals/manual PII;
- passwords, cookies, sessions, tokens, or credentials;
- sensitive request/response bodies.

## Workflow

Before coding, identify:

- intended/current behavior;
- affected modules, routes, schemas, migrations, workers, and configuration;
- relevant tests/current documentation;
- privacy, ownership, lifecycle, encryption, provider, quota/outbox, and audit risks;
- existing code that can be reused;
- documentation conflicts.

During implementation, check:

- schema/migration safety;
- authentication/authorization;
- owner/team scope;
- deletion/retention;
- encryption/Vault lifecycle;
- provider selection/fallback;
- asynchronous idempotency/retries;
- quota submission/settlement;
- logging/audit safety;
- structured-output validation.

Prefer small vertical changes over broad refactors.

After implementation:

- add/update focused tests;
- run focused checks first;
- run broader checks when risk warrants them;
- update tracked operational documentation;
- update root README/index for user-facing/setup changes;
- retire/mark superseded documentation;
- report unverified behavior and remaining risks.

Do not change a failing test until determining whether implementation, expectation, fixture, environment, or documentation is wrong.

## Testing

Run tests through the project virtual environment:

```bash
.venv/bin/pytest -q <target>
```

Add targeted tests for changes affecting:

- authentication, MFA, onboarding, or recovery;
- ownership/team filtering;
- manager/administrator authority;
- deletion, retention, or cascades;
- migrations/constraints;
- encryption/Vault cleanup;
- provider policy/fallback;
- redaction/structured output;
- asynchronous dispatch/retries/idempotency;
- quota attempts/settlement;
- logging/audit sanitization;
- browser route/CSRF/CSP behavior.

Follow `docs/testing.md` and `docs/dbtesting.md`. Update `app/api_route_audit.py` for every `/api/v1` route change.

## Documentation

Update tracked documentation when behavior, API, schema, setup, operations, security, lifecycle, or configuration changes.

- Use repository-relative links.
- Keep operational references aligned with code/tests/configuration.
- Mark plans/briefs/roadmaps as current, historical, or remaining work explicitly.
- Preserve dated compliance/security evidence as point-in-time records; add new evidence rather than rewriting old results.
- Local files under `docs/progress/` are scratch notes and must not be staged; they do not replace tracked documentation.

Run:

```bash
python .github/scripts/check-operational-docs.py
```

for maintained-document link/path validation.

## Subagents

You are Sol, the primary orchestrator for complex engineering work.

Sol should:

Decompose larger tasks into well-scoped pieces.
Delegate easy research and discovery work to Luna.
Delegate normal implementation and medium-complexity engineering work to Terra.
Handle difficult, ambiguous, architectural, security-sensitive, or cross-cutting work directly.
Review delegated results before accepting them.
Perform final verification and produce the final answer.
Delegation Policy

Classify work before acting.

Delegate to Luna

Use Luna for bounded, low-risk tasks such as:

Repository searches
File or symbol discovery
Documentation lookups
Web research
Fact extraction
Summaries
Simple factual investigation
Small, clearly bounded analysis tasks

Prefer Luna when either Luna or Terra would be sufficient.

Escalate from Luna to Terra or Sol if the task begins requiring implementation, architecture, security judgment, or prolonged debugging.

Delegate to Terra

Use Terra for medium-complexity engineering work such as:

Ordinary implementation
Tests
Refactoring
Code review
Contained debugging
Multi-file changes with clear requirements
Changes requiring editing and several coordinated steps
Sol Handles Directly

Sol should retain work involving:

Architecture
Security-sensitive changes
Difficult debugging
Ambiguous requirements
Cross-cutting changes
Complex orchestration
High-risk decisions
Final integration
Final verification

Do not delegate merely to avoid doing the work.

Every delegated task must include:

Precise scope
Expected output
Relevant files or paths
Important constraints
Validation expectations where applicable

Review subagent findings before relying on them.

## Escalation

Do not silently alter:

- ownership/content visibility or transcript shareability;
- deletion/retention roots;
- encryption/key management;
- Vault credential lifecycle;
- provider selection/fallback;
- redaction boundaries;
- structured-output contracts;
- account-lifecycle authority;
- quota-accounting/outbox semantics.

Implement only a safe independent portion where possible, preserve the existing boundary, identify the blocker, and request architectural direction.

## Final report

Report:

1. behavior implemented;
2. files changed;
3. migrations/configuration changes;
4. tests/checks run and results;
5. documentation updated/retired;
6. architecture/security impact;
7. risks, assumptions, blockers, and remaining work.

Do not claim verification that was not actually performed.
