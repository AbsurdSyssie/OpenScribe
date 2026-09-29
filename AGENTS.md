Purpose

OpenScribe is privacy-sensitive, security-sensitive, and clinically safety-sensitive.

When priorities conflict:

preserve privacy, security, clinical-safety, and architectural invariants;

implement correct behavior completely;

make the smallest coherent change;

preserve maintainability and existing abstractions;

minimize unnecessary work and expensive reasoning.

Proceed autonomously for ordinary engineering work. Do not silently redesign established ownership, content-access, deletion, retention, encryption, provider, authentication, quota/outbox, redaction, or structured-output contracts.

Autonomy and plans

Do not stop for routine, reversible implementation choices that fit the existing architecture.

Stop and request direction when the correct solution appears to require a philosophy-level or architectural change, unless that change is already authorized by a plan the user explicitly supplied or referenced.

A referenced plan defines intended work; its presence in the repository alone does not make it active or authoritative.

When a plan is supplied:

read it before implementation;

reconcile it with current code, migrations, tests, configuration, and maintained documentation;

identify material drift, obsolete assumptions, or conflicts;

do not recreate a clear plan from scratch;

execute it autonomously through implementation, testing, and documentation;

stop only for a material conflict with a non-negotiable privacy, security, clinical-safety, or unaddressed architecture boundary.

Prefer storing implementation plans under plans/. Plans describe intended work, not current behavior.

Delegation

Preserve Sol capacity. Sol plans and orchestrates; Luna and Terra should perform almost all executable work.

For substantial work, Sol should establish the intended outcome, constraints, acceptance criteria, and delegation boundaries, then delegate execution wherever practical.

Luna

Use Luna by default for bounded, low-risk, read-heavy work:

repository exploration and symbol/file discovery;

documentation lookup and web research;

fact extraction and summaries;

test execution and failure/log triage;

bounded review and verification;

small factual investigations.

Prefer Luna when either Luna or Terra would be sufficient.

Terra

Use Terra by default for normal engineering execution:

implementation;

tests;

necessary refactoring;

contained debugging;

documentation updates;

code review;

clear multi-file or coordinated changes.

Sol should normally avoid implementation that Terra can safely perform.

Sol

Reserve Sol for work that materially benefits from the strongest reasoning or central orchestration:

complex planning;

architecture and security judgment;

ambiguous or conflicting requirements;

philosophy-level changes;

difficult cross-cutting debugging;

integration conflicts;

high-risk decisions;

final verification when risk warrants it.

Parallelism

Parallelize independent exploration, research, tests, review, and other read-heavy work when useful.

Avoid overlapping write ownership. Do not assign multiple agents concurrent edits to the same area unless the work is cleanly partitioned and integration ownership is explicit.

Every delegated task must state its scope, expected output, relevant files/paths, important constraints, and validation expectations. Review delegated findings before relying on them.

Sources of truth

Distinguish current behavior from intended behavior.

Current behavior

Use the closest executable source of truth:

schema/persistence: Alembic migrations, database constraints, current models;

runtime/domain behavior: services, routes, dependencies, workers, runtime configuration;

API/structured output: schemas, routes, validation code, focused tests;

authorization/privacy: authorization/service code and focused security/authorization tests;

operations: maintained documents listed in docs/README.md.

Focused passing tests corroborate behavior but can be stale or incomplete.

Historical plans, briefs, roadmaps, TODOs, design notes, and dated evidence do not override implemented behavior.

Intended behavior

The user's task and any plan explicitly referenced for that task define the intended change.

Use docs/README.md to identify maintained operational documentation and distinguish it from history, roadmap material, and point-in-time evidence.

When sources disagree:

do not choose silently;

do not broaden access or weaken a privacy/security boundary;

establish implemented behavior from executable sources;

identify the conflict;

update or retire stale maintained documentation when appropriate;

request direction if resolution would change an architectural invariant.

Non-negotiable architecture

These rules remain in root instructions because missing them can cause serious privacy, security, lifecycle, or clinical-safety errors. Detailed behavior belongs in maintained documentation and executable contracts.

Privacy and ownership

Transcript-derived clinical content belongs to its owning user and is not team-shareable.

Administrative, team-leader, provider-management, or metadata authority does not grant content readability.

Metadata access is not content access.

System-administrator accounts must not own transcript-derived content.

Team leaders act only within their authorized team scope.

Do not add transcript-derived sharing or cross-owner content access without explicit architectural authorization.

Transcript lifecycle

The transcript root is the retention and deletion root for transcript-derived content.

Create the transcript root before ingesting transcript-derived content.

Team retention is server-owned and snapshotted; later user input must not extend it.

Expired roots become unavailable before asynchronous physical cleanup completes.

Use established cascades and durable cleanup paths for deletion.

Working note and post-consultation dictation remain distinct transcript-owned generation sources.

Encryption and secrets

Use established user-content encryption, DEK/KEK, Vault, and cleanup services.

Confidential user-owned/authentication content must remain within the established owning-user encryption boundary.

Provider credentials belong in Vault/deployment identity; PostgreSQL stores only permitted references and non-secret metadata.

Never expose raw credentials or unrestricted Vault references through normal responses.

Never delete a live Vault secret before the database change removing/replacing its live reference commits.

External-secret cleanup must remain durable and retryable.

Do not couple password recovery to content-key deletion or rotation.

Redaction, generation, and structured output

Run redaction only at established workflow boundaries and fail closed when required redaction fails.

Provider-bound clinical/user content must use the appropriate saved source snapshot and redaction boundary.

Generated-document edits must not mutate source transcript, Working note, dictation, Templates, Quick Actions, or other source material.

Generated results remain drafts requiring clinician review.

Treat the current schema/validation implementation as the structured-output contract.

Validate provider output before persistence/display.

Do not add profiles, section keys, incompatible response shapes, or weaker validation without explicit design authorization.

Providers, asynchronous work, and quotas

Preserve established provider eligibility, selection, fallback, credential, and execution-snapshot semantics.

Provider-management authority does not grant transcript-derived content access.

Preserve the established transactional relationship between business state and durable task-dispatch state.

Preserve database-backed claims/idempotency for duplicate delivery.

Resolve required credentials before the submitted provider-attempt boundary.

Definite pre-dispatch credential failure must not consume provider quota.

Queue, outbox, attempt, quota, usage, and audit rows contain permitted metadata only.

Worker schedules are implementation/configuration details; when changing them, update executable configuration, focused tests, and relevant operational docs together.

Reusable assets

Preserve established Template and Quick Action platform/team/personal scope and personal-only Smart Phrase scope.

Team reusable assets are not transcript-derived sharing.

Import/export transfers portable content only, never ownership/team/creator/version/active/usage authority.

Reusable configuration must not contain patient/transcript content.

Do not reintroduce historical watcher/fork-reference behavior unless explicitly designed.

Security and data handling

Use maintained project services and libraries rather than creating local alternatives for authentication, authorization, cryptography, CSRF, hashing, secret storage, rate limiting, or similar controls.

Use parameterized/structured database access. Never interpolate user-controlled values into raw SQL.

Use synthetic data for tests, diagnostics, documentation, examples, and provider inspection.

Do not log, place in audit/usage metadata, or expose through diagnostics:

transcript-derived content, Working notes, or dictation;

prompts or provider responses containing user data;

audio, redaction originals, or manual PII;

passwords, cookies, sessions, tokens, or credentials;

sensitive request or response bodies.

Never weaken a security constraint or test merely to make a change pass.

Scope discipline and discovered issues

Prefer the smallest coherent change that fully solves the requested problem and respects existing abstractions.

Do not perform unrelated cleanup, speculative refactors, or broad rewrites merely because nearby code could be improved. Expand the change when necessary for correctness, schema consistency, privacy/authorization, lifecycle/cleanup, tests, maintained documentation, or to avoid a workaround that fights an established abstraction.

Fix an incidental issue only when it is necessary for the requested work or is trivial, clearly correct, low-risk, and does not materially expand scope.

Otherwise record it in DISCOVERED_ISSUES.md and continue the requested work. Record:

short identifier/title and discovery date;

commit SHA when available;

file path and relevant symbol;

a small exact code excerpt when useful;

observed problem and likely impact;

why it was not fixed now;

suggested follow-up.

DISCOVERED_ISSUES.md is a work log, not a source of truth for current architecture or intended behavior.

Workflow

Before changing code:

determine intended behavior;

establish current behavior from the appropriate sources;

identify affected code, schema, workers, configuration, tests, and maintained docs;

identify the relevant non-negotiable invariants;

reuse existing services and abstractions;

reconcile any supplied plan with the repository rather than replanning it from scratch;

delegate executable work to Luna/Terra wherever practical.

During implementation, apply only the checks relevant to the affected subsystem. Preserve applicable ownership, auth, lifecycle, encryption, provider, idempotency/quota, logging/audit, and structured-output boundaries without turning a trivial change into a repository-wide audit.

If the correct implementation requires an unapproved architecture/philosophy change, stop after gathering enough evidence to explain the conflict and decision required. Implement any safe independent portion that does not prejudge that decision.

Verification

Run focused checks first using the project virtual environment.

Follow:

docs/testing.md for general, API, UI, security, provider, and lifecycle verification;

docs/dbtesting.md for database isolation, migrations, and committed-connection behavior.

For /api/v1 route changes, update the route-audit manifest and run the documented API authorization audit.

When behavior, API, schema, setup, operations, security, lifecycle, or configuration changes, update the closest maintained operational documentation listed by docs/README.md.

Run maintained-document validation when applicable:

python .github/scripts/check-operational-docs.py

Do not change a failing test until you determine whether the defect is in implementation, expectation, fixture, environment, or documentation/contract.

Run broader checks when the affected subsystem or docs/testing.md requires them. Do not automatically run an expensive full suite when focused verification is sufficient.

Never claim verification that was not actually performed.

Documentation and writing

Keep maintained documentation aligned with implemented behavior, tests, and configuration.

Preserve dated compliance/security evidence as point-in-time records; add newer evidence rather than rewriting historical results.

For documentation and user-facing prose, use concise, concrete, plain English. Prefer active voice and remove unnecessary words without sacrificing technical, clinical, security, or legal precision.

Final report

Report concisely:

behavior implemented;

files changed;

migrations/configuration changes;

tests/checks run and results;

documentation updated;

architecture/security/clinical-safety impact where relevant;

material delegated work;

discovered issues recorded;

remaining risks, assumptions, blockers, or follow-up work.

Do not claim verification that was not performed