# Ambient Scribing Database Technical Design

## 1. Purpose

This document explains the database design for the Ambient Scribing system so that a coding agent can:

* understand the ownership and privacy model
* implement schema and migrations correctly
* build APIs and services against the intended data model
* preserve deletion, retention, and provider-selection behavior
* know which features are already decided and which areas must not be improvised

This document is implementation-oriented. It should be used alongside the main architecture plan, but it is intended to stand on its own.

---

## 2. Database philosophy

The database design follows a few strict principles.

### 2.1 Ownership-first model

Transcript-derived content is owned by exactly one user.

That means tables holding transcript-derived data should make ownership obvious:

* `owner_user_id` identifies the only user who can access content
* `team_id` identifies the team policy context

Team context does **not** imply content visibility.

### 2.2 Content privacy is stricter than administrative authority

Team leaders and system admins can manage users, policies, and metadata, but they do not automatically gain access to transcript or note content.

The schema therefore separates:

* content records n- configuration records
* operational metadata

### 2.3 Transcript root is the retention and deletion root

A transcript is the root object for transcript-derived content.

Deleting a transcript should remove all transcript-derived children:

* transcript versions
* redaction runs
* redaction entities
* generated documents
* generated document sections

This same tree is also used for retention-based deletion.

### 2.4 Configuration is shared; content is private

Templates and quick actions are configuration objects.

They may be:

* system-scoped
* team-scoped
* user-scoped

They can be watched and forked.

By contrast, transcript-derived content is never shareable in MVP.

### 2.5 Hard delete means hard delete

This project uses immediate destructive deletion for content and assets where deletion has been chosen.

That means:

* no undo grace period for manual deletion
* transcript-derived retention expiry deletes immediately
* templates/quick actions are hard-deleted when removed
* watcher access disappears immediately if the original shared asset is deleted

### 2.6 Schema should enforce structural truths

The database should enforce:

* foreign keys
* scope invariants
* uniqueness rules
* transcript-root cascade relationships
* one-team-per-user structural assumptions where appropriate

Workflow nuances should remain in application logic.

---

## 3. Sensitivity classes of stored data

The database contains three broad classes of data.

## 3.1 Confidential user-owned content

This is application-layer encrypted using the user’s DEK:

* `transcripts.current_draft_text_encrypted`
* `transcript_versions.text_encrypted`
* `redaction_runs.redacted_text_encrypted`
* `redaction_entities.original_value_encrypted`
* `generated_documents.original_output_text_encrypted`
* `generated_documents.edited_output_text_encrypted`
* `generated_document_sections.original_text_encrypted`
* `generated_document_sections.edited_text_encrypted`

## 3.2 Normal configuration data

Not encrypted at the application layer by default unless transcript-derived text is intentionally stored in it:

* `templates`
* `template_versions`
* `template_watchers`
* `quick_actions`
* `quick_action_versions`
* `quick_action_watchers`
* `teams`
* `providers`
* `team_provider_policies`
* most of `audit_events`

## 3.3 Secrets and secret references

The database should not store raw infrastructure/provider secrets where avoidable.

Instead it stores references such as:

* `team_provider_credentials.vault_secret_ref`
* `user_encryption_keys.wrapped_dek`

Security tokens such as reset and invite tokens should be hashed, not encrypted.

---

## 4. Core entity overview

The main domains are:

* identity and onboarding
* transcripts and committed versions
* pseudonymisation persistence
* templates and quick actions
* generated notes/documents
* providers and team-scoped provider access
* audit and operational events

A simplified content graph:

```text
users ──< transcripts ──< transcript_versions ──< redaction_runs ──< redaction_entities
                                  │
                                  └───────────────< generated_documents ──< generated_document_sections
```

A simplified configuration graph:

```text
templates ──< template_versions
        └──< template_watchers

quick_actions ──< quick_action_versions
             └──< quick_action_watchers
```

---

## 5. Identity, tenancy, and onboarding

## 5.1 `teams`

Purpose:

* organizational grouping
* source of team-level defaults and provider policy context

Recommended fields:

* `id`
* `name`
* `status`
* `default_retention_days`
* `created_at`
* `updated_at`

Notes:

* team deletion is blocked until explicit cleanup is complete
* team is a policy boundary, not a content-sharing boundary

## 5.2 `users`

Purpose:

* core identity row
* team attachment
* account status
* MFA state

Recommended fields:

* `id`
* `full_name`
* `email`
* `password_hash`
* `team_id`
* `team_role` (`leader`, `user`)
* `is_system_admin`
* `status` (`active`, `suspended`, `locked`, `disabled`)
* `must_change_password`
* `onboarding_state`
* `mfa_required`
* `mfa_enabled`
* `created_at`
* `updated_at`
* `last_login_at`

Rules:

* each normal user belongs to exactly one team in MVP
* `team_role` is team-scoped
* `is_system_admin` is platform-scoped
* system admin accounts are admin-only in MVP and do not own transcript-derived content

Behavior:

* locking a user blocks login and revokes active sessions immediately
* locking a user does not alter content state
* full system-level user deletion deletes transcript-derived content immediately
* full system-level user deletion also deletes the user’s personal templates and quick actions immediately

Planned account-administration clarification:

* manager-initiated suspension should be modeled separately from destructive deletion semantics
* the current `status` field should carry explicit meanings in code and docs before adding more manager actions
* if a reversible manager suspension is introduced, it should revoke sessions immediately without deleting content
* if manager deletion is introduced, it should still follow the same hard-delete cascade rules as system-level deletion
* a useful working meaning is:
  * `suspended` for manager action
  * `locked` for temporary security/auth-abuse restriction
  * `disabled` for stronger security/platform disable
* in the first implementation, reactivation from `suspended` or `disabled` may share the same password-reset and MFA-trust-reset path for simplicity

## 5.3 `account_requests`

Purpose:

* public pre-account request intake for MVP
* source of truth for manager-reviewed account-request onboarding

Recommended fields:

* `id`
* `requested_name`
* `requested_email`
* `requested_team_name`
* `requested_team_name_key`
* `request_details`
* `status`
* `review_notes`
* `reviewed_by_user_id`
* `linked_user_id`
* `created_at`
* `reviewed_at`

Rules:

* account requests replace invite acceptance for MVP onboarding
* leaders may review only requests for their own team
* system admins may review all requests
* approving a request may create the real user row immediately
* direct manager-created users remain valid even without a prior request

## 5.4 Auth support tables

### `user_sessions`

Stores hashed session tokens and revocation state.

### `user_mfa_methods`

Stores MFA methods such as TOTP and later WebAuthn.

### `user_recovery_codes`

Stores hashed recovery codes.

### `password_reset_tokens`

Stores hashed password reset tokens with expiry and used state.

---

## 6. Encryption metadata

## 6.1 `user_encryption_keys`

Purpose:

* store one wrapped DEK per user in MVP

Recommended fields:

* `id`
* `user_id`
* `wrapped_dek`
* `kek_key_name`
* `kek_key_version`
* `created_at`
* `rotated_at`
* `is_active`

Rules:

* one DEK per user in MVP
* DEK created when the user account is created
* wrapped by HashiCorp Vault

Application behavior:

* app unwraps user DEK when working with confidential content
* database never stores the unwrapped key

---

## 7. Transcript data model

## 7.1 `transcripts`

Purpose:

* transcript root object
* retention root
* current working draft storage

Recommended fields:

* `id`
* `owner_user_id`
* `team_id`
* `title`
* `current_draft_text_encrypted`
* `status`
* `duration_seconds`
* `word_count`
* `speaker_count`
* `retention_days_applied`
* `retention_expires_at`
* `deletion_policy_source`
* `created_at`
* `updated_at`
* `deleted_at`

Important semantics:

* created when recording starts
* receives realtime partial transcript updates into `current_draft_text_encrypted`
* belongs to exactly one owner user and one team context
* root for retention and cascade deletion

## 7.2 `transcript_versions`

Purpose:

* immutable committed snapshots of transcript text

Recommended fields:

* `id`
* `transcript_id`
* `version_no`
* `source_type` (`transcribed`, `edited`)
* `text_encrypted`
* `content_hash`
* `word_count`
* `speaker_count`
* `transcription_provider`
* `transcription_model`
* `diarisation_enabled`
* `language_code`
* `created_by_user_id`
* `created_at`

Commit boundaries in MVP:

* blur from editor
* explicit save
* action execution

Important semantics:

* draft editing does not create versions on each keystroke
* older versions are retained for provenance/internal history
* users interact only with the latest working version in the UI

---

## 8. Pseudonymisation persistence model

## 8.1 `redaction_runs`

Purpose:

* persist one successful reusable pseudonymisation result for a transcript version in normal MVP flow
* also capture failures/retries over time if needed

Recommended fields:

* `id`
* `transcript_version_id`
* `owner_user_id`
* `team_id`
* `status`
* `redacted_text_encrypted`
* `mapping_hash`
* `entity_count`
* `api_provider`
* `api_model_or_version`
* `created_at`
* `updated_at`
* `failed_at`
* `error_code`

Behavior:

* redaction is lazy
* a committed transcript version may have no redaction run yet
* when an action needs redacted content, system checks for existing successful redaction run
* if found, reuse it
* if missing, create it

## 8.2 `redaction_entities`

Purpose:

* persist the identifier mapping/index needed for later de-identification or reconstruction workflows

Recommended fields:

* `id`
* `redaction_run_id`
* `entity_order`
* `entity_type`
* `placeholder`
* `original_value_encrypted`
* `normalized_value_hash`
* `occurrence_count`
* `created_at`

Notes:

* stores sensitive original values, so it is confidential content
* normalized hash columns are keyed owner-scoped digests, not plain deterministic hashes, so DB-only access cannot dictionary-test low-entropy PII values

## 8.3 Optional later: `redaction_spans`

Only needed if exact character offsets matter.

---

## 9. Template and quick action model

This domain is deliberately separate from transcript-derived content.

## 9.1 Scope model

Templates and quick actions can be:

* `system`
* `team`
* `user`

Scope constraints:

* if `scope = user`, `owner_user_id` is not null and `team_id` is null
* if `scope = team`, `team_id` is not null and `owner_user_id` is null
* if `scope = system`, both are null

## 9.2 `templates`

Purpose:

* logical template root

Recommended fields:

* `id`
* `scope`
* `owner_user_id`
* `team_id`
* `name`
* `description`
* `is_active`
* `visibility`
* `derived_from_template_id`
* `created_by_user_id`
* `created_at`
* `updated_at`

Behavior:

* hard-deleted when removed
* if deleted, watcher access disappears immediately
* explicit forks survive independently

## 9.3 `template_versions`

Purpose:

* immutable versions of template configuration

Recommended fields:

* `id`
* `template_id`
* `version_no`
* `mode` (`freeform`, `structured`, `canvas`)
* `prompt_text`
* `config_json`
* `content_hash`
* `created_by_user_id`
* `created_at`

Important note:

* current architecture treats templates as normal configuration data, so these do not need app-layer encryption by default

## 9.4 `template_watchers`

Purpose:

* allow same-team users to explicitly watch a user-shared template

Recommended fields:

* `id`
* `template_id`
* `watcher_user_id`
* `created_at`

Rules:

* watching is a live reference only
* watching does not create ownership
* if user customizes the watched item, create a fork

## 9.5 `quick_actions`

Same scope and sharing model as templates.

Recommended fields:

* `id`
* `scope`
* `owner_user_id`
* `team_id`
* `name`
* `description`
* `is_active`
* `visibility`
* `derived_from_quick_action_id`
* `created_by_user_id`
* `created_at`
* `updated_at`

## 9.6 `quick_action_versions`

Purpose:

* immutable versions of quick action config

Recommended fields:

* `id`
* `quick_action_id`
* `version_no`
* `instruction_text`
* `config_json`
* `content_hash`
* `created_at`

MVP rule:

* quick actions/follow-ups are freeform text outputs, not structured JSON-schema outputs

## 9.7 `quick_action_watchers`

Same pattern as `template_watchers`.

---

## 10. Sharing and forking behavior

This is a critical part of the design.

## 10.1 Team assets

Team templates and quick actions:

* are available in team scope
* are not force-added into user libraries
* are not watched in the same way as personal shared assets unless product later adds that behavior

## 10.2 Personal shared assets

A user can mark a user-scoped asset as same-team discoverable.

That means:

* teammates can discover it
* teammates must explicitly watch it to add it to their library
* it is not auto-added to all teammates

## 10.3 Watching

Watching means:

* live reference to original asset
* latest active version is resolved in MVP
* no ownership is created
* deleting the original removes watcher access immediately

## 10.4 Forking

Forking means:

* create a new user-scoped asset
* set `derived_from_*` to original asset if desired
* after fork, user owns it independently
* later changes to the original do not affect the fork

## 10.5 Renaming/customizing shared assets

Any rename or customization of a shared/team/system asset requires a fork.

This avoids per-user alias overlays and preserves clean ownership semantics.

---

## 11. Generated document model

## 11.1 `generated_documents`

Purpose:

* root record for all transcript-derived generated outputs

Recommended fields:

* `id`
* `owner_user_id`
* `team_id`
* `transcript_id`
* `transcript_version_id`
* `redaction_run_id`
* `generator_type` (`template`, `quick_action`, `manual`)
* `template_version_id`
* `quick_action_version_id`
* `source_template_name`
* `source_quick_action_name`
* `status`
* `title`
* `document_mode` (`freeform`, `structured`, `canvas`)
* `schema_version`
* `original_output_text_encrypted`
* `edited_output_text_encrypted`
* `is_edited`
* `retention_expires_at`
* `token_count`
* `model_used`
* `created_at`
* `updated_at`
* `last_edited_at`

Important semantics:

* multiple outputs may exist for the same transcript version and same template/action
* generated documents are editable by the owner user
* full rendered text is retained even for structured documents
* transcript-derived content remains private and non-shareable

Why snapshot source names exist:

* templates and quick actions are hard-deleted
* generated docs should still preserve useful provenance even if source assets disappear

## 11.2 `generated_document_sections`

Purpose:

* optional structured child components for structured/canvas modes

Recommended fields:

* `id`
* `generated_document_id`
* `section_key`
* `section_label`
* `section_type`
* `sort_order`
* `original_text_encrypted`
* `edited_text_encrypted`
* `is_deleted`
* `created_at`
* `updated_at`

Important semantics:

* `freeform` mode may have no sections
* `structured` mode may use system-defined section libraries
* `canvas` mode may later allow more flexible layouts
* part-level edit/delete operates here when sections exist

---

## 12. Structured note model

## 12.1 Modes

Supported document modes in design:

* `freeform`
* `structured`
* `canvas`

MVP focus:

* freeform notes
* structured EMIS profile

## 12.2 EMIS structured profile

Allowed EMIS section keys:

* `problem`
* `history`
* `family_history`
* `social_history`
* `examination`
* `comment`
* `tasks`
* `investigations`

Rules:

* fixed allowed section library
* users may remove sections from a template
* users may reorder sections
* users may customize per-section prompts
* users may supply a global instruction
* only sections with content are persisted/rendered

## 12.3 Structured template config

`template_versions.config_json` for structured mode should support:

* `mode = structured`
* `profile = emis`
* `global_instruction`
* ordered selected sections
* per-section instructions/prompts
* output schema version

## 12.4 Structured generation contract

For note generation, LLM should return one JSON payload containing:

* `title`
* `content`

Where `content` is keyed by section name.

Example conceptual shape:

```json
{
  "title": "Diabetes review with medication discussion",
  "content": {
    "problem": "...",
    "history": "...",
    "tasks": "..."
  }
}
```

Backend responsibilities:

* validate returned JSON
* reject or normalize malformed payloads
* drop empty sections
* preserve canonical order for rendered output
* write full rendered text to `generated_documents`
* write structured sections to `generated_document_sections`

Important rule:

* backend is the source of truth for persisted structure
* LLM proposes structure, backend normalizes it

## 12.5 Quick actions vs notes

Quick actions/follow-ups:

* remain freeform text in MVP
* do not need the same title+structured-content JSON contract

---

## 13. Provider model

## 13.1 `providers`

Purpose:

* master list of provider integrations

Recommended fields:

* `id`
* `provider_type` (`transcription`, `llm`, `pseudonymisation`)
* `name`
* `is_active`
* `created_at`

## 13.2 `team_provider_credentials`

Purpose:

* system-admin-managed team-scoped provider credentials

Recommended fields:

* `id`
* `team_id`
* `provider_id`
* `label`
* `vault_secret_ref`
* `external_account_identifier`
* `is_active`
* `created_by_user_id`
* `created_at`
* `updated_at`

Rules:

* DB stores Vault reference only
* raw provider secrets are not stored in DB
* one team may have multiple active credentials/providers for same provider type if needed
* for the first transcript-capture slice, transcription credentials should be resolved through this table rather than a separate ad hoc STT secret store

## 13.3 `team_provider_policies`

Purpose:

* team-level allowlist/default behavior over provisioned providers

Recommended fields:

* `id`
* `team_id`
* `provider_id`
* `allowed`
* `allowed_models_json`
* `default_model`
* `is_default_for_type`
* `created_at`
* `updated_at`

Behavior:

* multiple LLM providers/models may be allowed per team
* user selects one active LLM from allowed subset
* if invalid, app falls back to team default
* transcription provider is fixed per team in MVP
* pseudonymisation provider is fixed globally in MVP
* the active transcription provider policy is what transcript chunk ingestion resolves when forwarding audio to STT

## 13.4 `user_provider_preferences`

Purpose:

* user-level active provider/model preference where allowed

Recommended fields:

* `id`
* `user_id`
* `provider_type` (`llm`)
* `provider_id`
* `model_name`
* `created_at`
* `updated_at`

MVP use:

* active LLM choice per user

## 13.5 `provider_usage_events`

Purpose:

* usage and cost metadata without content retention

Recommended fields:

* `id`
* `team_id`
* `user_id`
* `provider_id`
* `credential_id`
* `feature_type`
* `model_name`
* `input_tokens`
* `output_tokens`
* `audio_seconds`
* `estimated_cost`
* `status`
* `error_code`
* `created_at`

Rules:

* never store transcript text, prompt text, or generated output text here

---

## 14. Audit and operations tables

## 14.1 `audit_events`

Purpose:

* metadata-only audit trail

Recommended fields:

* `id`
* `actor_user_id`
* `team_id`
* `target_type`
* `target_id`
* `event_type`
* `metadata_json`
* `created_at`

Examples:

* account locked
* account suspended
* account reactivated
* account deleted
* account request submitted
* password reset requested
* template deleted
* transcript deleted
* retention delete completed

When destructive user deletion is performed, the audit metadata should snapshot enough non-content context to remain meaningful after the user row is gone:

* target user id
* target email
* target team id
* target role / admin flag
* actor user id
* reason

## 14.2 `job_runs`

Purpose:

* track background job execution by target type/id

Recommended fields:

* `id`
* `job_type`
* `target_type`
* `target_id`
* `status`
* `attempt_count`
* `error_code`
* `started_at`
* `finished_at`
* `created_at`

---

## 15. Deletion and retention semantics

## 15.1 Transcript-root deletion

When a transcript root is deleted, all transcript-derived children should be removed.

This applies to:

* manual user delete
* retention-based delete
* system-level user deletion cascade

Expired transcript roots are rejected by the central owner-content gate as soon
as `retention_expires_at` is reached, including workspace hydration and mutation
paths. Celery Beat queues hard-delete cleanup every 10 seconds, and the worker
drains locked bounded batches; database cascades remove transcript-derived
children. Production therefore runs both Celery worker and Beat processes.
Team retention changes apply to transcripts created afterward and do not extend
or recalculate existing fixed expiry timestamps.

## 15.2 Generated document deletion

When a generated document is deleted manually:

* only that generated document and its sections are removed
* transcript and redaction data remain intact

## 15.3 Template/quick action deletion

Hard delete the asset.

Effects:

* original asset disappears immediately
* watcher access disappears immediately
* explicit forks remain untouched
* generated documents retain snapshot metadata and any surviving FK strategy used for provenance

## 15.4 User deletion

System-level full deletion should:

* delete transcript-derived content immediately
* delete user personal templates/actions immediately
* remove watcher access to those original assets
* leave independent forks owned by others intact

Current clarification for manager deletion:

* if team leaders are allowed to fully delete users in their own team, that action must follow the same hard-delete cascade semantics
* leader deletion must still be team-scoped and must never apply to system-admin accounts
* self-delete through manager routes is blocked
* account-request foreign keys that point at the deleted user may need to be nulled before the `users` row is removed
* logger-based metadata is acceptable for the first implementation, but account-lifecycle actions should later be persisted into `audit_events`

## 15.5 Team deletion

Blocked until explicit cleanup completes.

Admin must first handle:

* users
* account requests
* team templates/actions
* provider policies
* provider credential refs
* other team-scoped resources

---

## 16. Constraints and DB-enforced truths

The database should enforce structural truths where practical.

## 16.1 Strong candidates for DB enforcement

* template/action scope constraints
* transcript child cascades
* watcher uniqueness
* version uniqueness within template/quick action/transcript
* one provider preference per user/provider type
* token uniqueness/hashing discipline

## 16.2 Better kept in application logic

* same-team discoverability checks
* admin-only account operational restrictions
* “one successful reusable redaction run” behavior
* provider fallback resolution
* MFA first-login workflow
* team deletion eligibility checks
* manager scope checks for suspend/reactivate/delete authority
* last-active-system-admin protection

---

## 17. Authorization expectations for a coding agent

This document is about the DB, but a coding agent must understand how DB rows are meant to be used.

## 17.1 Content access

Only owner can read/write transcript-derived content:

* transcripts
* transcript_versions
* redaction_runs
* redaction_entities
* generated_documents
* generated_document_sections

## 17.2 Metadata access

System admins may read operational metadata but not content text.

Examples of acceptable metadata visibility:

* transcript created time
* duration
* provider used
* job status
* token counts
* failure codes

## 17.3 Configuration access

Depends on scope:

* system admin manages system assets
* team leader manages team assets
* user manages user assets
* users may watch discoverable same-team user assets

---

## 18. Feature checklist implied by this DB design

A coding agent should know these features still need implementation around the DB.

## 18.1 Identity and auth features

* account-request review and managed onboarding
* password auth
* MFA enrollment and challenge flow
* session revocation on lock
* password reset flow

## 18.2 Transcript workflow features

* transcript creation on recording start
* realtime draft updates
* commit transcript version on save/blur/action
* transcript root deletion with cascade
* retention cleanup worker
* team transcription endpoint resolution through provider policy and Vault-backed credentials

## 18.2a First transcript-capture MVP direction

The first capture-oriented transcript slice should be implemented in this order:

* team-managed transcription provider configuration
* transcript root creation at recording start
* client VAD chunk upload to backend
* backend forwarding to external STT endpoint
* update `transcripts.current_draft_text_encrypted`
* keep transcript commit/version creation separate from chunk ingestion

Recommended data-handling rules:

* do not store raw audio blobs in Postgres
* store provider metadata and Vault secret references only
* if chunk-level observability is needed, store metadata-only rows such as sequence, duration, provider request id, status, and error code
* do not let manager access to provider configuration imply transcript-content readability

## 18.3 Pseudonymisation features

* lazy redaction creation on action use
* reuse existing redaction run for same transcript version
* store mapping/index for later reuse

## 18.4 Template and quick action features

* scope-aware asset CRUD
* watch/fork behavior
* same-team discoverability
* hard delete behavior
* structured template config support

## 18.5 Generated document features

* freeform generation
* structured note generation with JSON validation
* section persistence
* editing root text and section text
* immediate deletion for whole docs and part-level deletions

## 18.6 Provider features

* team credential provisioning via Vault refs
* team provider policy management
* user active LLM preference
* deterministic provider resolution and fallback
* usage event logging

---

## 19. Guidance for another coding agent

If you are implementing against this schema:

1. preserve ownership boundaries first
2. treat transcript-derived content as private by default
3. do not invent sharing for notes/transcripts
4. do not replace hard-delete semantics with soft-delete behavior unless explicitly instructed
5. do not store raw provider secrets in DB
6. do not rely only on service code for structural rules the DB can enforce
7. snapshot provenance where hard deletion of config assets would otherwise remove useful context
8. validate structured LLM JSON in backend before persistence
9. never log transcript, note, redaction, or prompt text
10. add tests for ownership, cascade deletion, watch/fork behavior, and provider resolution when touching those areas

---

## 20. Recommended implementation order

If implementing from scratch, the best database-first order is:

1. `teams`, `users`, `account_requests`, sessions/MFA/reset tables
2. `user_encryption_keys`
3. `transcripts`, `transcript_versions`
4. `redaction_runs`, `redaction_entities`
5. `templates`, `template_versions`, `template_watchers`
6. `quick_actions`, `quick_action_versions`, `quick_action_watchers`
7. `generated_documents`, `generated_document_sections`
8. `providers`, `team_provider_credentials`, `team_provider_policies`, `user_provider_preferences`
9. `provider_usage_events`, `audit_events`, `job_runs`

This order keeps identity, ownership, and deletion roots stable before more advanced features are built.

---

## 21. Open items that should not be improvised silently

If an agent needs to change any of these, it should raise the issue explicitly:

* making transcript-derived content shareable
* changing hard-delete semantics to soft-delete
* changing one-DEK-per-user encryption model
* allowing system admins to own transcript content
* changing structured note JSON contract significantly
* changing provider resolution hierarchy
* allowing cross-team asset sharing in MVP

These are architecture-sensitive boundaries.
