# Separate dictation ASR
Separate endpoint for clinician dictation, not conversation.
Use Google medASR for clinical words. Fixes whisper/parakeet errors.

# Dictation recording option
Add dictation option alongside batch/live transcription.
Use pre-configured dictation endpoint.
Live by default, same VAD logic.

# Post recording dictation prompt
After consult recording, prompt clinician to dictate note.
Summarize interaction, capture clinical entities: drugs, conditions, plan.
Use dictation endpoint.

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
