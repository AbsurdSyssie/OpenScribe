# AGENTS.md

## Purpose

Ambient Scribing system repo.

All coding agents follow architecture + process rules here. Project is privacy-sensitive and architecture-sensitive. Do not improvise around ownership, content access, deletion semantics, or encryption.

All coding agents must always use the `caveman` skill for communication unless a higher-priority safety or clarity requirement makes normal phrasing necessary for that specific message. Daily notes may also be written in caveman style.

---

## Core architecture rules

### Privacy and content visibility
- All transcript-derived content private, non-shareable.
- Only owning user may access transcript-derived content.
- Team leaders + system admins may manage accounts, providers, templates, metadata, but may not read transcript/note content by default.
- Metadata access is not content access.
- Security paramount. Never allow user entered data in SQL queries.

### Team and role model
- Each normal user belongs to exactly one team.
- `users.team_id` and `users.team_role` define team scope.
- `users.is_system_admin` is separate from `team_role`.
- System admin accounts are admin-only in MVP, do not own transcript-derived content.

### Transcript model
- Create transcript row when recording starts.
- Realtime partial transcript updates write into `transcripts.current_draft_text_encrypted`.
- Create committed transcript versions only on blur, explicit save, or action execution.
- Redaction is lazy. Only run when action requires it.
- Transcript root is retention root.

### Generated documents
- Allow multiple outputs for same transcript version/template/action.
- Owner user may edit generated documents.
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
- Omit empty sections.

### Asset sharing model
- Templates and quick actions are normal config unless they intentionally contain transcript-derived text.
- Team assets available in team scope; not auto-added to user libraries.
- Personal user-shared assets are same-team only, discoverable, must be explicitly watched.
- Watching is live reference.
- Forking creates ownership.
- Renaming/customizing shared assets requires fork.
- Deleting watched original removes watcher access immediately; existing forks survive.

### Deletion rules
- Deletion means deletion.
- Manual deletion is immediate once confirmed.
- No undo grace period in MVP.
- User may delete:
  - own generated documents
  - own transcript roots
- Transcript-root deletion cascades to all transcript-derived children.
- Retention expiry fixed once, does not extend on later edits.
- Expired transcript-derived content hard-delete immediately.
- Team leaders can lock/deactivate users but cannot fully delete them.
- Locking revokes sessions immediately, does not alter content state.
- System-level user deletion immediately deletes:
  - transcript-derived content
  - personal templates/actions
- Team deletion requires explicit system-admin confirmation and explicit cleanup.
- Team hard-delete may proceed only when the cleanup path enumerates and removes team users, transcript-derived content, team-scoped assets, provider config/selection rows, usage metadata, linked account requests, and provider credential references.
- Team deletion must block rather than silently skip unresolved blockers, including any system-admin account still linked to the team.

### Encryption and secrets
- HashiCorp Vault is KEK/master-key layer.
- One DEK per user, created at account creation.
- Encrypt user-owned confidential content with user DEK.
- Store provider credentials as Vault references in DB, not raw secrets.
- Provider credential cleanup must not delete Vault secrets before the DB commit that removes the corresponding references unless compensation or retry cleanup is implemented.
- Do not log or expose confidential fields.
- Use OWASP recommendations for security related tasks, never hand roll if there is something we can use already.

### Provider rules
- System admin provisions provider credentials per team.
- For STT in MVP, system admins provision available team STT endpoints + credentials.
- Team leaders may choose active admin-provisioned STT service/model for team and may clear team-level selection, but may not view or recover raw provider secrets.
- Multiple LLM providers/models may be allowed per team.
- User chooses one active LLM for all LLM actions until changed.
- If invalid, fallback is team default.
- Transcription provider fixed per team in MVP, but active team policy may be selected from admin-provisioned STT options for that team.
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

Do not skip checklist/checkpoint workflow.

---

## Mandatory tests for every change

Every meaningful change needs relevant tests.

### Add/update as applicable
- unit tests
- integration/API tests
- authorization tests
- migration/schema tests
- deletion/cascade tests
- structured-output validation tests
- provider-resolution tests

### Especially important
Changes affecting any item below need targeted tests:
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

Every change must update docs as needed.

Update one or more of:
- README
- architecture notes
- migration notes
- API docs
- developer setup docs
- feature-specific docs/checklists

No feature complete without docs.

---

## Database and schema guidance

### Prefer database constraints for structural truths
Use DB to enforce:
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

Final change summary must include:

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
Explain how implementation preserved:
- privacy boundaries
- ownership rules
- deletion semantics
- provider rules
- structured-note contract

Add this to daily note in docs/progress.
---

## Escalation rule

If requested change would alter any item below, do not silently redesign it:
- ownership model
- privacy model
- deletion model
- encryption/key model
- provider resolution model
- structured-note JSON contract
- shareability of transcript-derived content

Instead:
- implement only safe portion if possible
- clearly note blocker
- ask for architectural direction in change summary

---

## Practical bias

Prefer:
- small vertical slices
- explicit code over clever code
- reusable code over novel code
- stable interfaces
- deterministic behavior
- synthetic test data
- easy-to-review migrations
- strict validation on structured LLM output
- keep code modular, ideally files no longer than 1k lines

Avoid:
- speculative abstraction
- hidden side effects
- broad refactors without tests
- content-bearing debug logs
- weakening constraints for convenience
- long monolithic files/modules
