# Separate dictation ASR
Separate endpoint for clinician dictation, not conversation.
Use Google medASR for clinical words. Fixes whisper/parakeet errors.

# Dictation recording option
Add dictation option alongside batch/live transcription.
Use pre-configured dictation endpoint.
Live by default, same VAD logic.

Follow-up tuning:
- tune shared browser VAD thresholds after real mic testing
- likely first knobs:
  - live pre-roll / overlap
  - live silence threshold
  - whole-file voice-only dictation pre-roll
  - whole-file voice-only dictation trailing buffer
  - minimum speech duration before segment accepted
- test against:
  - short drug names
  - dosage strings
  - fast stop/start speech
  - background room noise
  - quiet speaker / laptop mic

# Hazard log
Make a silence detected for thirty seconds have you finished your consultation? Prompt
In training, include warning if file uploaded it is clinicians responsibility to ensure patient info is correct

# Post recording dictation prompt
After consult recording, prompt clinician to dictate note.
Summarize interaction, capture clinical entities: drugs, conditions, plan.
Use dictation endpoint.

## Post consultation dictation plan

Goal:
- after consultation transcript capture ends, let clinician record short note-style dictation that improves downstream note/follow-up generation without replacing transcript ownership/privacy model

Recommended first slice:
- owner finishes consultation recording/upload flow
- workspace shows clear CTA: `Add post-consultation dictation`
- creates separate dictation capture attached to same transcript root
- capture uses dictation STT endpoint, not conversation STT endpoint
- dictation capture defaults to live VAD-gated mic flow, but uploads into one logical dictation artifact/result
- recognized dictation text stored as transcript-derived owner-only content
- clinician may dictate multiple times; new dictated ASR text appends into same dictation artifact
- original ASR text remains immutable even if clinician edits combined dictation text later
- note generation can include:
- base consultation transcript
- optional post-consultation dictation text
- UI shows dictation as separate source from consultation transcript, but as one combined dictation surface

Why separate storage:
- preserves provenance
- keeps conversation transcript distinct from clinician summary/dictation
- lets generation include/exclude dictation explicitly
- avoids confusing later audit/review of what patient said vs clinician added later

Recommended data model direction:
- do not append dictation text into `transcripts.current_draft_text_encrypted`
- add transcript-derived child record for post-consultation dictation under same transcript root
- suggested first-pass shape:
  - `post_consultation_dictations`
  - `id`
  - `transcript_id` FK cascade delete
  - `owner_user_id`
  - `team_id`
  - `status`
  - `source_kind` (`live_mic` first, maybe file later)
  - `input_started_at`, `input_completed_at`
  - `combined_edited_text_encrypted`
  - `latest_appended_at`
  - `stt_provider_id` / provenance metadata as needed
  - timestamps
- add append-only child rows behind it, not separate user-facing dictation entities:
  - `post_consultation_dictation_segments`
  - `post_consultation_dictation_id` FK cascade delete
  - `owner_user_id`
  - `team_id`
  - `sequence_no`
  - `source_kind`
  - `asr_text_encrypted`
  - provider/provenance metadata
  - timestamps
- generation uses `combined_edited_text_encrypted` when present, else concatenated segment ASR text in sequence order

Backend/service plan:
1. add separate dictation STT provider resolution path
2. add owner-only start/upload/finalize API for post-consultation dictation
3. persist dictation result encrypted with user DEK
4. append new ASR pass into existing dictation artifact for transcript
5. preserve immutable raw ASR segment text when combined editable text changes
6. expose combined dictation state in workspace read model
7. update generation services so note/follow-up prompts auto-include dictation text
8. weight dictation stronger than transcript in prompt assembly
9. preserve transcript-root cascade delete and user-delete cascade behavior

Frontend plan:
1. add CTA only when consultation exists and main consultation capture is not actively recording
2. add dictation panel/modal with mic record control, status, timer, VAD visualizer
3. reuse shared browser VAD package and current mic UI patterns where safe
4. allow dictation start immediately after consultation recording stops, even if consultation STT still queued/processing
5. show one combined editable dictation field plus append action for further dictation passes
6. no separate history UI first pass; provenance stays internal

Validation/tests needed:
- unit tests for dictation provider resolution and fallback
- API auth tests: owner only, no leader/admin content read
- encryption tests for stored dictation text
- append-order tests for multi-pass dictation on same transcript
- edit tests proving raw ASR segments unchanged after combined text edit
- deletion cascade tests from transcript root and user deletion
- generation tests proving dictation auto-included when present
- generation tests proving edited combined dictation preferred over raw concatenated text
- prompt assembly tests proving dictation framed as stronger signal than transcript
- structured/freeform generation tests for provenance-aware prompt assembly

Docs needed:
- API docs for dictation routes
- architecture note for transcript-root child model and provenance
- workspace UX doc for post-consultation flow

Settled decisions:
- one dictation artifact per transcript
- clinician may dictate multiple times; each pass appends into same dictation artifact
- clinician may edit combined dictation text
- raw ASR output stays immutable; edits do not overwrite original segment text
- note generation auto-includes dictation once present
- dictation may start once consultation recording stops; no need to wait for consultation transcript `ready`
- structured note generation should treat dictation as stronger signal than consultation transcript when conflict exists
- no separate dictation history UI first pass; keep internal provenance, inherit transcript-root lifecycle silently

## Post consultation dictation phased checklist

Phase 0: design lock
- done: reuse `team_stt_configs` for both conversation and dictation endpoints; split active team policy by purpose instead of creating second config store
- done: Phase 2 should extend team STT selection model to carry explicit purpose (`conversation`, `post_consultation_dictation`) with no silent cross-purpose fallback
- done: prompt assembly rule = consultation transcript remains base factual source; post-consultation dictation is clinician-authored stronger signal for summary, assessment, terminology, and plan framing; when sources conflict, prefer dictation for clinician intent/plan wording but do not invent facts absent from both
- done: generation source rule = if `is_combined_text_user_edited` is true, use `combined_edited_text_encrypted` exactly as saved, including intentional empty string; otherwise concatenate immutable segment ASR text in append order

### Phase 0 locked decisions

- provider config shape:
  - keep one admin-provisioned STT config store: `team_stt_configs`
  - do not add dictation-specific secret/config table
  - same config row shape already supports both workloads: endpoint metadata, model defaults, available models, Vault-backed secret ref
  - separate runtime policy at team-selection layer, not config layer
  - intended Phase 2 DB direction: add purpose-aware selection shape so team can hold one active conversation STT selection and one active dictation STT selection at same time
  - no runtime fallback may jump from dictation selection to conversation selection implicitly; missing dictation selection must fail explicitly
- generation source contract:
  - one `post_consultation_dictations` row per transcript
  - many append-only `post_consultation_dictation_segments` rows per dictation
  - clinician edits only parent combined text field
  - raw segment ASR text remains immutable provenance record
  - generation chooses source by explicit edit-state, not by non-empty string test alone
  - if user has edited combined text, use combined text exactly
  - if user has never edited combined text, use concatenated segment text in append order
  - if user edited combined text down to empty, treat that as intentional removal of dictation influence for generation
- prompt weighting rule:
  - transcript remains consultation record and chronology anchor
  - dictation is post-hoc clinician summary and should be framed as higher-priority interpretive guidance
  - generation prompt should label sources separately, with dictation block after transcript block under explicit instruction that dictation should guide note wording, assessment, and plan emphasis
  - if transcript and dictation disagree, prefer dictation for clinician-authored interpretation, diagnosis wording, medication names, and plan, unless dictation clearly contradicts hard transcript facts in a way that would require invention
  - model must not merge two conflicting facts into new unsupported claim; if conflict cannot be reconciled safely, keep output conservative and source-grounded

Phase 1: schema
- done: added `post_consultation_dictations` table with owner/team/transcript FK, encrypted combined text field, explicit `is_combined_text_user_edited`, and one-row-per-transcript constraint
- done: added `post_consultation_dictation_segments` table with append-only raw ASR rows and per-dictation sequence uniqueness
- done: enforced transcript-root cascade delete via `transcript_id -> transcripts.id ON DELETE CASCADE` and segment cascade via `post_consultation_dictation_id -> post_consultation_dictations.id ON DELETE CASCADE`
- done: added migration tests / schema assertions

Phase 2: provider resolution
- done: added purpose-aware STT selection path with explicit `conversation` and `post_consultation_dictation` selection purposes on `team_stt_selections`
- done: dictation resolution now requires explicit dictation-purpose selection and does not silently fall back to conversation selection
- done: added provider-resolution tests and migration assertions for purpose-aware selection behavior
- done: exposed separate conversation vs dictation STT team selection controls in admin and leader browser flows

Phase 3: API/service
- done: added owner-only get/update dictation endpoints with lazy create on first update/upload
- done: added owner-only append/upload dictation audio endpoint using dictation STT provider purpose
- pending: explicit finalize-pass endpoint not added; append is immediate for first cut
- done: encrypt combined text and raw segment text with user DEK
- done: preserve immutable segment text on edit by keeping raw append-only segment rows separate from editable combined text
- done: added auth/provider/encryption/workspace tests for dictation flow

Phase 4: transcribe UI
- done: refreshed workspace payload/rendering to include dictation data and dictation STT availability
- done: transcript tab now renders split-pane consultation transcript left + dictation pane right
- done: allow append pass after prior dictation exists via append audio upload form
- done: added combined editable dictation field
- done: dedicated real-mic dictation recorder UI with reused VAD visualizer/timer/status for voice-only microphone capture
- pending: explicit `Add post-consultation dictation` CTA polish; current first cut uses always-visible dictation pane

Phase 5: generation integration
- done: auto-include dictation in note/follow-up/quick action generation
- done: prefer combined edited dictation text when present
- done: otherwise concatenate immutable segment text in append order
- done: updated prompt assembly so dictation is stronger clinician-authored signal while transcript stays chronology/fact anchor
- pending: add targeted generation tests for freeform + structured note flows

Phase 6: docs and polish
- update API docs
- update architecture docs for provenance/storage model
- update transcribe UX docs/checklists
- add real-mic VAD tuning pass for dictation workflow 

## Workspace preference polish

- done: transcribe workspace now remembers last selected note template in existing user app preferences `default_template_id`
- done: transcribe workspace now remembers last selected recording mode in existing user app preferences `preferred_recording_mode`
- done: transcribe page applies those saved values as defaults before dedicated preferences page exists

# Account recovery
Need recovery flow for lost password and lost TOTP that preserves current privacy/encryption model.

Recommended order:
- instance-level outbound email transport for reset delivery
- self-service password reset with single-use hashed email token
- recovery-code MFA fallback with forced TOTP re-enrollment
- manager-assisted `reset MFA` and `reset password + MFA`
- optional Auth0 auth mode, but only behind an explicit `auth_provider` model

Rules:
- reset email transport should be system-admin/platform configured, not team-leader managed
- production mail secrets should follow the existing Vault-backed secret pattern
- password reset must not affect wrapped DEK or historical content access
- leaders/admins may manage recovery metadata only, not read transcript content
- recovery actions must revoke sessions and trusted devices
- public reset request must not leak whether an email exists
- if Auth0 is added, Auth0-managed accounts should use Auth0-owned password/MFA recovery rather than a competing local reset path

## Resend transactional email plan

Goal:
- use Resend for security-critical transactional email: account activation/setup, password reset, manager-issued recovery, and later MFA/security notifications
- keep email delivery as platform infrastructure, not team/provider configuration
- preserve current privacy model: email flows carry auth metadata only and never include transcript-derived content

Checklist before coding:
- target behavior:
  - managed user creation and approved account requests can send an activation/setup email instead of relying only on out-of-band temporary passwords
  - forgot-password flow sends a generic reset email for local-auth users
  - manager recovery actions can issue setup links or generate one-time visible temporary passwords without exposing tokens, MFA secrets, recovery codes, or content
  - public reset request response remains generic for existing and missing emails
- affected schema/modules/endpoints:
  - auth token table, mail outbox table, settings/config, auth services, admin/account-request services, Celery worker, browser pages, API route audit manifest
  - likely endpoints: `POST /api/v1/auth/password-reset/request`, `POST /api/v1/auth/password-reset/confirm`, activation/setup confirm route, manager recovery routes
- affected tests:
  - migration/schema tests, API auth tests, route-audit tests, mailer unit tests, account-request/managed-user integration tests, manager authorization tests
- architecture risks:
  - token leakage in logs, email enumeration, team leaders gaining platform mail control, Resend API key stored outside Vault, reset accidentally rotating/destroying user DEK, activation semantics weakening MFA onboarding
- docs to refer/update:
  - `docs/account_recovery_brief.md`
  - `docs/auth.md`
  - `docs/security.md`
  - `docs/api.md`
  - `docs/setup.md`
  - `docs/testing.md`

### Phase 0: design lock

- decide whether to add a generic `auth_email_tokens` table or implement `password_reset_tokens` first and add activation later
- recommended: generic `auth_email_tokens` so password reset, account activation, manager reset, and future email-change confirmation share one hashed-token lifecycle
- token purposes:
  - `account_activation`
  - `password_reset`
  - `manager_password_reset`
  - `manager_account_recovery`
- token rules:
  - store only token hash
  - short expiry
  - single use
  - revoke previous live tokens for same user and purpose when issuing a new one
  - never log plaintext token or full reset URL
  - token use must revoke active sessions and trusted devices where recovery changes auth material
- activation rule:
  - activation/setup link may let user set first real password
  - TOTP onboarding still required before full access
  - activation must not create transcript ownership for system admins or change user DEK rules
- password reset rule:
  - reset changes password hash only
  - reset must not rotate, delete, or rewrap the user DEK
  - reset completion revokes sessions and trusted devices

### Phase 1: platform Resend configuration

- done: added `disabled`, `stdout`, and `resend` mail transport modes
- done: added environment config loading/validation for mail transport, public URL, sender identity, reply-to, and Resend API key / Vault ref
- done: added stdout local sender so repo users can keep current no-Resend setup
- done: added direct Resend Email API sender behind the same mail service interface
- done: added operator test script for configured mail transport
- pending: admin/system setup page that shows mail config status
- pending: production UI/path for writing Resend API key into Vault instead of env
- add instance-level mail config, not team scoped:
  - `MAIL_TRANSPORT=disabled|stdout|resend`
  - `APP_PUBLIC_URL`
  - `MAIL_FROM_ADDRESS`
  - `MAIL_FROM_NAME`
  - `MAIL_REPLY_TO` optional
  - Resend API key Vault reference for production
- keep current no-email setup:
  - `disabled` preserves manual/temporary-password onboarding
  - self-service password reset should be unavailable or should direct users to contact a manager
  - manager-created temporary passwords remain the fallback path until email is configured
- setup modes:
  - `disabled`: no outbound mail, current manual setup behavior
  - `stdout`: local/dev outbox printed to server output, no external account needed
  - `resend`: production transactional email through operator-owned Resend account/domain
- dev fallback:
  - `stdout` outbox mode for tests/local runs
  - optional raw env API key only for local development
- production rule:
  - store Resend API key in Vault or equivalent secret store
  - database/env may hold only non-secret metadata and Vault reference
  - leaders cannot view, edit, or test platform mail credentials
- Resend setup notes:
  - verify sending domain before production use
  - use a dedicated transactional subdomain if available
  - create sending-restricted API key where Resend allows it

### Phase 2: mail outbox and sender service

- add `email_outbox` table:
  - `id`
  - `purpose`
  - `recipient_email`
  - `subject`
  - `body_text`
  - `body_html`
  - `status`
  - `provider`
  - `provider_message_id`
  - `idempotency_key`
  - `attempt_count`
  - `last_error_code`
  - `last_error_at`
  - timestamps
- do not store auth token plaintext separately from rendered email unless the row is deleted immediately after send; preferred path is render at enqueue time and keep only normal email body plus hashed token in auth-token table
- sender service:
  - resolve platform mail config
  - send via Resend `/emails`
  - include both HTML and plaintext bodies
  - use Resend idempotency key per outbox row
  - record provider message id and delivery attempt metadata
  - log only event type, outbox id, recipient hash or normalized email if current logging policy allows email metadata, provider id, status, duration, and error code
- worker:
  - send asynchronously through Celery
  - retry transient errors
  - mark permanent failures without exposing secrets
  - keep request path generic even when enqueue/send fails; surface operational failure only to admins through metadata

### Phase 3: account activation/setup email

- done: added `auth_email_tokens` table with hashed token, purpose, expiry, used marker, and optional manager actor
- done: added account setup email generation through configured mail transport
- done: added browser route `/activate-account` and API route `POST /api/v1/auth/account-activation/confirm`
- done: setup link sets first real password, creates onboarding session, and still forces TOTP enrollment before full access
- done: manager can send setup link from user-management UI/API
- manager creates user or approves request
- system creates user with password setup required and MFA onboarding required
- system issues `account_activation` token and enqueues setup email
- user opens setup link:
  - token validated by hash, expiry, purpose, unused state
  - user sets password
  - token marked used
  - onboarding session created or user redirected to login then onboarding
  - TOTP enrollment remains mandatory before full access
- keep temporary-password path as fallback until email delivery is proven in production
- tests:
  - leader can trigger activation only for same-team non-system-admin users
  - system admin can trigger activation for allowed accounts
  - activation token single-use
  - expired token rejected
  - activation does not grant full access before TOTP setup
  - activation email body contains no team secrets, provider secrets, transcript text, note text, or recovery codes

### Phase 4: self-service password reset

- done: added browser request/confirm routes `/forgot-password` and `/reset-password`
- done: added API routes `POST /api/v1/auth/password-reset/request` and `POST /api/v1/auth/password-reset/confirm`
- done: public request response is generic for existing and missing emails
- done: disabled mail hides browser self-service reset and returns `503 mail_transport_disabled` before user lookup/token creation
- done: reset tokens are hashed, short-lived, and single-use
- done: successful reset revokes sessions and trusted devices without touching user DEK
- add public forgot-password page and API
- request route:
  - normalize email
  - always return generic success
  - rate limit by IP and normalized email hash
  - create token/outbox only for existing eligible local-auth accounts
  - do not reveal locked/deleted/nonexistent status
- confirm route:
  - validate token hash
  - set new password
  - mark token used
  - revoke all sessions and trusted devices
  - revoke other live reset tokens for that user
  - keep MFA requirement unchanged; user still completes TOTP/recovery flow after login
- tests:
  - existing and missing email responses match
  - no plaintext token stored
  - reset preserves DEK and owner can still decrypt prior content
  - reset revokes sessions/trusted devices
  - pending/onboarding sessions cannot use normal routes after reset

### Phase 5: manager-assisted recovery emails

- done: added manager API routes for setup link, password reset, MFA reset, and combined account recovery
- done: added leader/system-admin browser actions in user-management UI
- done: manager non-email recovery generates one-time visible temporary passwords, stores only hashes, and forces password change
- done: MFA reset deletes TOTP/recovery-code state, revokes sessions/trusted devices, and forces TOTP reenrollment
- manager actions:
  - resend activation/setup email
  - generate temporary password for non-email password reset
  - reset MFA only
  - reset password + MFA with temporary password
- authority:
  - leaders: same-team non-system-admin users only
  - system admins: allowed accounts, but never silently delete/alter transcript-derived content
  - no self-reset through manager routes unless explicitly allowed later
- behavior:
  - manager sees delivery status metadata for setup links, or one-time temporary password for non-email recovery
  - manager cannot view token, URL, TOTP secret, recovery codes, mail provider secret, or transcript-derived content
  - all recovery actions revoke sessions/trusted devices as appropriate
- tests:
  - leader cross-team denial
  - leader system-admin target denial
  - manager metadata response contains no token/secret
  - route-audit manifest updated

### Phase 6: Resend webhooks and delivery status

- optional after basic send works
- add webhook endpoint only if delivery/bounce status is needed in-product
- verify webhook signatures using provider-recommended mechanism before trusting payload
- store event metadata only:
  - provider message id
  - event type
  - timestamp
  - delivery status
  - error code/reason category
- do not store provider payloads if they can include message body or headers with secrets
- tests:
  - unsigned webhook rejected
  - unknown message id handled safely
  - webhook update cannot expose email body/token content

### Checkpoints during coding

- schema checkpoint:
  - auth-token rows have hashed token only, expiry, purpose, used marker, and user FK
  - outbox rows hold delivery metadata and rendered transactional copy only; no provider secrets or transcript content
- auth/ownership checkpoint:
  - public reset route is enumeration-safe
  - manager routes are metadata-only and same-team scoped
  - activation/recovery sessions cannot access transcript routes before full auth
- lifecycle/deletion checkpoint:
  - user deletion removes auth tokens and outbox rows or anonymizes delivery metadata according to retention decision
  - token use/recovery revokes sessions and trusted devices
  - reset does not alter wrapped DEK or transcript retention
- docs/tests checkpoint:
  - docs and route-audit manifest updated with each route
  - tests cover auth boundaries, token lifecycle, mail failure, and DEK preservation

### Implementation order

1. platform mail config plus `stdout` sender and tests
2. Resend sender adapter behind same interface
3. mail outbox and Celery delivery worker
4. account activation/setup email for manager-created/approved users
5. self-service password reset
6. manager recovery emails
7. optional Resend webhooks

Open questions:
- whether activation email fully replaces temporary passwords or ships as preferred path with temporary-password fallback
- whether outbox retention stores rendered email bodies after send or purges bodies and keeps metadata only
- whether Resend webhook status is needed for MVP or can stay in Resend dashboard until operational demand exists
- exact public URL/source of truth for hosted deployments

# UI improvement
Revamp clinical notes/follow up area. Current UI ugly, not great. 

Status:
- Follow-ups panel now has quick-pick favourites for up to four quick actions.
- Icons for common SMS/referral/call/results actions.
- Follow-up generation supports optional extra guidance text.
- Duplicate template names now blocked within same owner/team scope.
- Duplicate quick action names now blocked within same owner/team scope.
- User-facing `Note layouts` copy now renamed to `Templates`.
- Blank note editor stays editable with structured headings / freeform plain rows.
- Follow-ups now unlock from transcript text or note content.
- Structured and freeform editors no longer auto-spawn trailing blank row while typing.
- Recording timer now persists per consultation across stop/start in browser state.
- Transcribe transient success/error messages now render as toasts instead of banners.
- Ready note documents now autosave through owner-only generated-document PATCH with `updated_at` conflict checks.
- Still open: broader workspace revamp for note/follow-up composition.
- Persisted favourite template / quick-action ids now drive transcribe ordering.
- Still open: dedicated favourites-management UI beyond API-backed ordering.
- Should be able to create a freeform note from a structured context. Just send the transcription and not the statement text.



## Preference plan
Need one harmonious preference model, not ad-hoc fields everywhere.

### Preference buckets

1. UI-only local preferences
- transcribe pane open/closed state
- split ratio
- dismissed tours / helper banners
- default active transcribe tab
- compact vs roomy list density
- preferred note/follow-up panel layout

Use browser `localStorage` only for these.

Rules:
- no transcript-derived content in browser storage
- no secrets in browser storage
- safe if cleared without breaking account behavior

2. Durable per-user workflow preferences
- favourite quick actions
- favourite templates
- default quick action for common tasks
- default note template
- LLM detail level
- freeform-generation custom style/system prompt
- preferred recording mode (`whole_file` vs `live_chunked`) if product wants it
- preferred copy/export format
- preferred home/transcribe landing tab
- future notification / reminder preferences
- existing active LLM preference

Use DB-backed owner-scoped preferences for these.

Rules:
- only current user may read/write own preferences
- metadata only, never transcript/note content
- deleting template/quick-action removes that favourite reference immediately
- invalid references should be dropped lazily or ignored safely

3. Team-level policy defaults, not user preferences
- active STT selection
- allowed LLM models
- retention defaults
- team templates / quick actions availability

Keep these in existing team-policy tables, not user-preference storage.

### Lowest-disruption implementation path
Prefer one generic table for durable user preferences rather than one new table per feature.

Suggested shape:
- `user_app_preferences`
- `user_id` unique FK to `users`
- `preferences_json` JSON/JSONB
- timestamps

Initial JSON keys could include:
- `favorite_quick_action_ids`
- `favorite_template_ids`
- `default_quick_action_id`
- `default_template_id`
- `llm_detail_level`
- `llm_custom_freeform_system_prompt`
- `preferred_recording_mode`
- `preferred_transcribe_tab`

Why this shape:
- small migration
- one service layer
- one owner-only API surface
- easy additive keys later
- avoids schema churn for every new preference

Guardrails:
- store only metadata ids / enums / booleans / small strings
- cap list sizes, e.g. max 8 favourites
- validate ids against same-team visible assets at write time
- re-check visibility at read/use time
- never infer content access from favourite status

### LLM generation settings tab
Add user-facing settings area for LLM generation preferences.

Candidate controls:
- active LLM model selector
- detail level
- default note template
- default quick action
- freeform output tone/style hints

Recommended first-pass behavior:
- `detail_level` should be enum-backed, not free text
- map enum to backend-owned prompt fragments such as:
  - `concise`
  - `balanced`
  - `detailed`
- apply those fragments after provider resolution and before request dispatch

### Custom system prompt guardrails
User-owned custom prompt possible, but needs limits.

Safe first version:
- allow only for freeform note generation
- allow only for follow-ups and quick actions if product wants same behavior there
- do not allow it to replace backend safety/privacy instructions
- append it after fixed backend rules, not before
- length cap it tightly
- treat it as user-owned confidential content if persisted

Do not allow first:
- raw custom system prompt for structured EMIS JSON generation
- raw custom system prompt that can weaken JSON-only contract
- raw custom system prompt that can override placeholder/privacy instructions

Reason:
- structured note generation has hard backend contract (`title` + `content` object with allowed EMIS keys)
- freeform verbosity/style controls low risk
- structured output controls should stay enum/template driven unless later architecture says otherwise

### Out-of-box options
Yes, but only partly:
- browser `localStorage`: good for harmless UI state, already used in transcribe shell
- single JSON/JSONB column in one DB table: closest out-of-box durable option with little code
- server-side session storage: poor fit for durable preferences because it expires and is session-scoped

Do not use:
- cookies for larger preference payloads
- `localStorage` for durable cross-device workflow preferences
- separate preference columns/tables for every tiny feature unless rule complexity demands it

### Next preference slice recommendation
1. keep UI layout preferences in `localStorage`
2. add dedicated favourites-management UI on top of existing `user_app_preferences`
3. later add default template/quick action into same table
4. add LLM generation settings tab backed by same preference row
5. start with `detail_level` enum before any custom freeform system prompt



# PHI Plan
Allow for a generic PHI endpoint to be configured so that all transcript derived content can be sent through.
Also need to add regex for UK phone numbers and NHS numbers. ideally we would be able to do something to counteract uneven spacing of numbers, numbers being spelled out or characters etc.

# Onboarding UI
- Needs to be updated to look like current main UI, colours, spacing, typeface etc
