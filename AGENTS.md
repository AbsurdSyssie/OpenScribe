# AGENTS.md

## Purpose

OpenScribe is privacy-sensitive and architecture-sensitive.

Preserve established ownership, content-access, deletion, retention, encryption, provider, authentication, and structured-output contracts. Do not silently redesign them.

## Sources of truth

Read only the documentation relevant to the change.

Not every Markdown file describes current behaviour. Files named `plan`, `brief`, `refactor`, or similar may be historical or superseded.

Use these as evidence of current behaviour, in this order:

1. database migrations and constraints
2. implemented service and route behaviour
3. focused passing tests
4. current runtime configuration
5. documentation explicitly describing current behaviour

When these disagree:

* do not choose silently
* preserve the stricter privacy and security boundary
* identify the conflict
* establish the implemented behaviour from code, migrations, and tests
* update or retire stale documentation
* escalate if an architectural invariant is affected

Relevant references include:

* `docs/security.md`
* `docs/auth.md`
* `docs/api.md`
* `docs/setup.md`
* `docs/testing.md`
* `docs/DatabasePlan.md`
* `docs/transcript-capture.md`
* `docs/stt-config.md`
* `docs/llm-providers.md`

## Architecture invariants

### Privacy and ownership

* Transcript-derived content belongs only to its owning user.
* Transcript-derived content is not team-shareable.
* Administrative or team-leader authority does not grant access to transcripts, audio, dictation, working notes, generated notes, prompts, or redaction originals.
* Metadata access is not content access.
* System-administrator accounts must not own transcript-derived content.
* Each normal user belongs to one team.
* Team leaders may act only within their own team.

### Transcript lifecycle

* Create the transcript root before ingesting transcript-derived content.
* The transcript root is the retention and deletion root.
* Persist draft and committed transcript content through the established encrypted fields and version boundaries.
* Retention expiry is fixed when assigned and must not be extended by later edits.
* Transcript deletion must remove all transcript-derived children through established cascades and cleanup paths.
* Working notes and post-consultation dictation remain separate transcript-owned sources.

### Redaction and generation

* Run redaction only at defined workflow boundaries.
* Capture finalisation or ingestion reconciliation may create or reuse a redaction preview.
* Provider-bound workflows must use the appropriate source snapshot and redaction boundary.
* Fail closed when required redaction fails.
* Generated-document edits must not mutate transcripts, working notes, dictation, templates, or other source material.
* Preserve generated-document provenance when source assets are deleted.

### Structured output

* Treat `app/schemas/templates.py` and `app/schemas/transcripts.py` as the structured-output contracts.
* Validate provider output before persistence or display.
* Do not add profiles, section keys, or incompatible response shapes without architectural approval.
* Do not weaken validation to accept malformed model output.

### Shared assets

* Team assets remain team-scoped.
* Watching is a live reference; forking creates ownership.
* Customising a shared asset requires a fork.
* Deleting an original removes watcher access; existing forks survive.
* Never convert a reference into ownership implicitly.

### Account lifecycle and deletion

* Suspension is reversible; deletion is immediate and destructive.
* Suspension, locking, or disabling must revoke sessions and trusted-device authority as required by the existing lifecycle.
* Team leaders may suspend, reactivate, and hard-delete non-system-administrator users in their own team.
* System administrators may perform those actions across teams, subject to protected-account safeguards.
* Managers may not suspend or delete themselves through manager routes.
* Do not remove the final active system-administrator account.
* User and team deletion must use the established deletion services.
* Block deletion rather than silently skipping unresolved cleanup.

### Encryption and provider secrets

* Vault is the KEK and provider-secret layer.
* Encrypt confidential user-owned content with the owning user’s DEK.
* Store provider credentials in Vault; store only references and non-secret metadata in the database.
* Never expose raw credentials or Vault references through normal responses.
* Never delete a live Vault secret before the database transaction removing or replacing its reference commits.
* Record retired-reference cleanup durably with the database change.
* Cleanup must retry failures and verify that a reference is no longer live.
* Use existing encryption, Vault, and cleanup services.

### Provider policy

* System administrators provision providers and credentials.
* Team leaders select only providers assigned to their team.
* STT selection is purpose-specific, including conversation transcription and post-consultation dictation.
* Provider configuration never grants access to transcript-derived content.
* Preserve team LLM policy, user preference fallback, provider setup state, credential status, and selection rules.
* PII-redaction and clinical-NLP selections remain separate.
* Use the established native de-identification fallback when no valid team selection exists.

## Security

* Never interpolate user-controlled values into raw SQL.
* Use SQLAlchemy expressions or parameterised statements.
* Allowlist identifiers, sort fields, operators, and query fragments.
* Reuse maintained libraries and existing project security services.
* Do not hand-roll cryptography, authentication, authorisation, CSRF, hashing, secret storage, or rate limiting.
* Use synthetic data for tests and provider inspection.
* Never weaken a constraint or test merely to make it pass.

Do not log:

* transcript-derived content
* prompts or provider responses containing user data
* audio content
* redaction originals or manual PII
* passwords, cookies, sessions, tokens, or credentials
* sensitive request or response bodies

## Workflow

Before coding, identify:

* intended behaviour
* affected modules, routes, schemas, migrations, workers, and configuration
* relevant tests and current documentation
* privacy, ownership, lifecycle, encryption, and provider risks
* existing code that can be reused
* documentation conflicts
* In Code Mode, within each bounded stage, run independent, functions.exec-available tool calls concurrently in one functions.exec call. Use await Promise.allSettled([...]) when partial results are useful, and inspect every result; use await Promise.all([...]) only when any failure should abort the batch. Keep dependencies, waits/resumes, approvals, conflicting or interdependent mutations, and adaptive investigations where each result may change the next step sequential. Do not split otherwise batchable inspections across outer tool calls.


During implementation, check:

* schema and migration safety
* authentication and authorisation
* owner and team scope
* deletion and retention
* encryption and Vault lifecycle
* provider selection and fallback
* asynchronous idempotency and retries
* logging and audit safety
* structured-output validation

Prefer small vertical changes over broad refactors.

After implementation:

* add or update focused tests
* run focused checks first
* run broader checks when risk warrants them
* update tracked documentation
* retire or mark superseded documentation
* report unverified behaviour and remaining risks

Do not change a failing test until determining whether the implementation, expectation, fixture, environment, or documentation is wrong.

## Testing

Run tests through the project virtual environment:

```bash
.venv/bin/pytest -q <target>
```

Add targeted tests for changes affecting:

* authentication, MFA, onboarding, or recovery
* ownership or team filtering
* manager or administrator authority
* deletion, retention, or cascades
* migrations and constraints
* encryption or Vault cleanup
* provider policy and fallback
* redaction or structured output
* asynchronous dispatch, retries, or idempotency
* logging and audit sanitisation

Follow `docs/testing.md` for shared infrastructure and environment requirements.

## Documentation

Update tracked documentation when behaviour, API, schema, setup, operations, security, or lifecycle contracts change.

Local files under `docs/progress/` are scratch notes and must not be staged. They do not replace tracked documentation.

## Subagents

Delegate only bounded work where isolated context or parallel execution helps.

Every delegation must define the objective, permitted scope, constraints, required evidence, tests, and escalation condition.

* **Luna:** Narrow, low-risk, mechanically verifiable work. Prefer read-only searches, inventories, extraction, triage, formatting, and source-grounded documentation. Never assign architecture-sensitive decisions.
* **Terra:** Default for bounded implementation, fixes, tests, refactors, and documentation when behaviour and acceptance criteria are defined. Escalate ambiguity or architectural consequences.
* **Sol:** Ambiguous, cross-cutting, or high-consequence work; architecture, privacy, security, deletion, encryption, provider-secret lifecycle, migrations, complex debugging, and final sensitive review.

Prefer parallel read-heavy tasks. Avoid overlapping writes unless isolated workspaces and an integration plan are used.

The parent remains responsible for correctness. Review delegated changes, verify claims, resolve conflicts, and run relevant tests.

Require subagents to report:

* files reviewed or changed
* commands and tests with results
* assumptions and decisions
* limitations, risks, and blockers

Subagents must not delegate further unless explicitly authorised.

## Escalation

Do not silently alter:

* ownership or content visibility
* transcript shareability
* deletion or retention
* encryption or key management
* Vault credential lifecycle
* provider selection or fallback
* redaction boundaries
* structured-output contracts
* account-lifecycle authority
* quota-accounting semantics

Implement only a safe independent portion when possible, preserve the existing boundary, identify the blocker, and request architectural direction.

## Final report

Report:

1. behaviour implemented
2. files changed
3. migrations or configuration changes
4. tests run and results
5. documentation updated or retired
6. architecture and security impact
7. risks, assumptions, blockers, and remaining work

Do not claim anything was verified unless it was actually checked.
