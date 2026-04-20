# AGENTS.md

## Purpose

This repository implements the Ambient Scribing system.

All coding agents must follow the architecture and process rules in this file. This project is privacy-sensitive and architecture-sensitive. Do not improvise around ownership, content access, deletion semantics, or encryption.

---

## Core architecture rules

### Privacy and content visibility
- All transcript-derived content is private and non-shareable.
- Only the owning user may access transcript-derived content.
- Team leaders and system admins may manage accounts, providers, templates, and metadata, but may not read transcript/note content by default.
- Metadata access is not content access.
- Security is paramount. Never allow user entered data to be used in SQL queries

### Team and role model
- Each normal user belongs to exactly one team.
- `users.team_id` and `users.team_role` define team scope.
- `users.is_system_admin` is separate from `team_role`.
- System admin accounts are admin-only in MVP and do not own transcript-derived content.

### Transcript model
- Transcript row is created when recording starts.
- Realtime partial transcript updates write into `transcripts.current_draft_text_encrypted`.
- Committed transcript versions are created only on blur, explicit save, or action execution.
- Redaction is lazy and only occurs when an action requires it.
- Transcript root is the retention root.

### Generated documents
- Multiple outputs are allowed for the same transcript version/template/action.
- Generated documents are editable by the owner user.
- Generated documents support:
  - full document text
  - optional structured sections
- Structured notes use JSON output with `title` and `content`.
- Quick actions/follow-ups are freeform text in MVP.

### Structured note rules
- Initial structured profile is EMIS.
- Allowed EMIS section keys:
  - `problem`
  - `history`
  - `family_history`
  - `social_history`
  - `examination`
  - `comment`
  - `tasks`
  - `investigations`
- EMIS templates may remove/reorder allowed sections.
- Structured templates support global + per-section instructions.
- Empty sections are omitted.

### Asset sharing model
- Templates and quick actions are normal configuration unless they intentionally contain transcript-derived text.
- Team assets are available in team scope; not auto-added to user libraries.
- Personal user-shared assets are same-team only, discoverable, and must be explicitly watched.
- Watching is a live reference.
- Forking creates ownership.
- Renaming/customization of shared assets requires a fork.
- Deleting a watched original removes watcher access immediately; existing forks survive.

### Deletion rules
- Deletion means deletion.
- Manual deletion is immediate once confirmed.
- No undo grace period in MVP.
- User may delete:
  - own generated documents
  - own transcript roots
- Transcript-root deletion cascades to all transcript-derived children.
- Retention expiry is fixed once and does not extend on later edits.
- Expired transcript-derived content is hard-deleted immediately.
- Team leaders can lock/deactivate users but cannot fully delete them.
- Locking revokes sessions immediately but does not alter content state.
- System-level user deletion immediately deletes:
  - transcript-derived content
  - personal templates/actions
- Team deletion requires explicit system-admin confirmation and explicit cleanup.
- Team hard-delete may proceed only when the cleanup path enumerates and removes team users, transcript-derived content, team-scoped assets, provider config/selection rows, usage metadata, linked account requests, and provider credential references.
- Team deletion must block rather than silently skip unresolved blockers, including any system-admin account still linked to the team.

### Encryption and secrets
- HashiCorp Vault is the KEK/master-key layer.
- One DEK per user, created at account creation.
- User-owned confidential content is encrypted with the user DEK.
- Provider credentials are stored as Vault references in DB, not raw secrets.
- Provider credential cleanup must not delete Vault secrets before the DB commit that removes the corresponding references unless compensation or retry cleanup is implemented.
- Do not log or expose confidential fields.

### Provider rules
- System admin provisions provider credentials per team.
- Multiple LLM providers/models may be allowed per team.
- User chooses one active LLM for all LLM actions until changed.
- If invalid, fallback is the team default.
- Transcription provider is fixed per team in MVP.
- De-identification/pseudonymisation providers are system-admin provisioned. Team leaders may select an assigned active provider for their own team.
- If no valid team de-identification selection exists, use the built-in legacy/native Presidio provider.
- Remote de-identification endpoints must use HTTPS unless the endpoint is localhost, LAN/private, or link-local. Raw provider secrets must use Vault-backed bearer-token storage, not arbitrary headers or DB fields.

---

## Required engineering workflow

Every change must include:

1. **Checklist before coding**
   - target behavior
   - affected schema/modules/endpoints
   - affected tests
   - architecture risks
   - refer to docs/ .md files

2. **Checkpoint updates during coding**
   - schema checkpoint
   - auth/ownership checkpoint
   - lifecycle/deletion checkpoint
   - docs/tests checkpoint

3. **Checklist completion after coding**
   - code complete
   - tests added/updated
   - docs added/updated
   - open issues noted

Do not skip the checklist/checkpoint workflow.

---

## Mandatory tests for every change

Every meaningful change must include relevant tests.

### Add/update as applicable
- unit tests
- integration/API tests
- authorization tests
- migration/schema tests
- deletion/cascade tests
- structured-output validation tests
- provider-resolution tests

### Especially important
Changes affecting any of the following require targeted tests:
- ownership filtering
- transcript deletion
- user deletion
- watcher/fork behavior
- provider fallback
- structured-note JSON validation
- encryption/decryption paths
- MFA/auth flows

---

## Mandatory documentation for every change

Every change must update documentation as needed.

Update one or more of:
- README
- architecture notes
- migration notes
- API docs
- developer setup docs
- feature-specific docs/checklists

No feature is complete without docs.

---

## Database and schema guidance

### Prefer database constraints for structural truths
Use the DB to enforce:
- foreign keys
- uniqueness
- scope invariants
- cascade relationships
- required ownership/team references

### Keep workflow nuance in application logic
Use service logic for:
- provider fallback
- same-team discoverability checks
- admin-only account behavior
- “one successful reusable redaction run” behavior
- forced MFA setup flow
- team deletion eligibility

### Do not weaken these invariants
- transcript-derived records must carry `owner_user_id`
- transcript-derived records must carry `team_id` where architecturally defined
- transcript root cascades must remain intact
- template/action scope rules must remain valid
- generated document provenance must not break if source templates/actions are hard-deleted

---

## Logging and observability rules

Allowed in logs:
- event types
- IDs
- statuses
- timestamps
- token counts
- provider/model names
- error codes
- durations
- counts
- cost estimates

Forbidden in logs:
- transcript text
- note text
- prompts
- model responses containing user data
- redaction original values
- provider secrets
- invite/reset tokens
- plaintext session identifiers

---

## Change review expectations

Your final change summary must include:

### 1. Scope
What was implemented.

### 2. Checklist
Completed and remaining items.

### 3. Files changed
With brief purpose.

### 4. Tests
What was added/updated and what it verifies.

### 5. Documentation
What was added/updated.

### 6. Risks / assumptions
Anything needing architect review.

### 7. Architecture checkpoint summary
Explain how the implementation preserved:
- privacy boundaries
- ownership rules
- deletion semantics
- provider rules
- structured-note contract

Add this to the daily note in docs/progress
---

## Escalation rule

If a requested change would alter any of these, do not silently redesign it:
- ownership model
- privacy model
- deletion model
- encryption/key model
- provider resolution model
- structured-note JSON contract
- shareability of transcript-derived content

Instead:
- implement only the safe portion if possible
- clearly note the blocker
- ask for architectural direction in the change summary

---

## Practical bias

Prefer:
- small vertical slices
- explicit code over clever code
- Reusable code vs novel code
- stable interfaces
- deterministic behavior
- synthetic test data
- migrations that are easy to review
- strict validation on structured LLM output

Avoid:
- speculative abstraction
- hidden side effects
- broad refactors without tests
- content-bearing debug logs
- weakening constraints for convenience
