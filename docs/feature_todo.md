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
- confirm provider config shape for dictation STT separate from conversation STT
- confirm exact prompt rule for stronger dictation weighting
- confirm whether generation uses all dictation segments or only combined edited text once non-empty

Phase 1: schema
- add `post_consultation_dictations` table with owner/team/transcript FK and encrypted combined text field
- add `post_consultation_dictation_segments` table with append-only raw ASR rows
- enforce transcript-root cascade delete
- add migration tests / schema assertions

Phase 2: provider resolution
- add dictation-specific provider selection path
- ensure no fallback crosses into wrong provider type silently
- add provider-resolution tests

Phase 3: API/service
- add owner-only create/get/update dictation endpoints
- add owner-only append/upload/finalize dictation pass endpoints
- encrypt combined text and raw segment text with user DEK
- preserve immutable segment text on edit
- add auth/encryption/deletion tests

Phase 4: transcribe UI
- add `Add post-consultation dictation` CTA
- add dictation recorder UI with reused VAD visualizer/timer/status
- allow append pass after prior dictation exists
- add combined editable dictation field
- refresh workspace payload/rendering

Phase 5: generation integration
- auto-include dictation in note/follow-up generation
- prefer combined edited dictation text when present
- otherwise concatenate immutable segment text in append order
- update prompt assembly so dictation stronger than consultation transcript
- add generation tests for freeform + structured note flows

Phase 6: docs and polish
- update API docs
- update architecture docs for provenance/storage model
- update transcribe UX docs/checklists
- add real-mic VAD tuning pass for dictation workflow

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
