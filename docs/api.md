# API Behavior

Canonical JSON API routes are versioned under `/api/v1`.

Browser navigation behavior:

- invalid non-API browser routes now redirect by session state:
  - unauthenticated users -> `/login`
  - authenticated users -> `/home`
- invalid `/api/*` routes still return JSON `404` responses and are not redirected

## Implemented endpoint groups

### Auth

- `POST /api/v1/auth/login`
- `POST /api/v1/auth/mfa/totp`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`
- `GET /api/v1/auth/trusted-device`

### Public account requests

- `POST /api/v1/account-requests`

### Manager review

- `GET /api/v1/account-requests`
- `POST /api/v1/account-requests/{request_id}/approve`
- `POST /api/v1/account-requests/{request_id}/reject`

### Onboarding

- `POST /api/v1/onboarding/password`
- `POST /api/v1/onboarding/totp/start`
- `POST /api/v1/onboarding/totp/verify`
- `POST /api/v1/onboarding/recovery-codes`
- `POST /api/v1/onboarding/skip-recovery-codes`

### Team management

- `POST /api/v1/teams`
- `GET /api/v1/teams`

### User management

- `POST /api/v1/users`
- `GET /api/v1/users`
- `POST /api/v1/users/{user_id}/suspend`
- `POST /api/v1/users/{user_id}/reactivate`
- `DELETE /api/v1/users/{user_id}`

### Transcripts

- `POST /api/v1/transcripts`
- `POST /api/v1/transcripts/start`
- `PATCH /api/v1/transcripts/{transcript_id}`
- `DELETE /api/v1/transcripts/{transcript_id}`
- `GET /api/v1/transcripts/{transcript_id}`
- `POST /api/v1/transcripts/{transcript_id}/commit`
- `POST /api/v1/transcripts/{transcript_id}/audio-chunks`
- `POST /api/v1/transcripts/{transcript_id}/audio-file`
- `POST /api/v1/transcripts/{transcript_id}/retry-audio-file`
- `GET /api/v1/transcripts/{transcript_id}/post-consultation-dictation`
- `PATCH /api/v1/transcripts/{transcript_id}/post-consultation-dictation`
- `POST /api/v1/transcripts/{transcript_id}/post-consultation-dictation/audio-file`
- `GET /api/v1/transcripts/{transcript_id}/generated-documents`
- `PATCH /api/v1/generated-documents/{generated_document_id}`
- `GET /api/v1/generated-documents/{generated_document_id}/redaction-debug`
- `POST /api/v1/transcripts/{transcript_id}/generate-output`
- `GET /api/v1/users/{user_id}/transcripts`
- whole-file upload rejects oversize payloads with:
  - status `413`
  - code `payload_too_large`
  - message `Audio file exceeds the current maximum upload size`

### Templates

- `GET /api/v1/templates/available`
- `GET /api/v1/templates/team`
- `POST /api/v1/templates/team`
- `DELETE /api/v1/templates/team/{template_id}`
- `GET /api/v1/templates/personal`
- `POST /api/v1/templates/personal`
- `DELETE /api/v1/templates/personal/{template_id}`

### Team transcription configuration

- `GET /api/v1/stt-configs`
- `GET /api/v1/stt-configs/{config_id}`
- `POST /api/v1/stt-configs/inspect`
- `POST /api/v1/stt-configs`
- `DELETE /api/v1/stt-configs/{config_id}`
- `GET /api/v1/stt-selection`
- `GET /api/v1/stt-selection/options`
- `POST /api/v1/stt-selection`
- `DELETE /api/v1/stt-selection`
- `GET /api/v1/stt-selection` now accepts optional `purpose` query param:
  - `conversation` default
  - `post_consultation_dictation`
- `DELETE /api/v1/stt-selection` now accepts same optional `purpose` query param
- `POST /api/v1/stt-selection` now accepts `purpose` in JSON body with same values
- these are metadata and secret-reference routes, not transcript-content routes

### Team LLM configuration

- `GET /api/v1/llm-configs`
- `POST /api/v1/llm-configs/inspect`
- `POST /api/v1/llm-configs`
- `DELETE /api/v1/llm-configs/{config_id}`
- `GET /api/v1/llm-selection`
- `GET /api/v1/llm-selection/options`
- `POST /api/v1/llm-selection`
- `DELETE /api/v1/llm-selection`
- `GET /api/v1/llm-preference`
- `POST /api/v1/llm-preference`
- `DELETE /api/v1/llm-preference`
- `GET /api/v1/app-preferences`
- `POST /api/v1/app-preferences`
- `DELETE /api/v1/app-preferences`
- these are metadata and secret-reference routes, not transcript-content routes

### De-identification provider configuration

- `GET /api/v1/deidentification-providers`
- `POST /api/v1/deidentification-providers`
- `DELETE /api/v1/deidentification-providers/{provider_id}`
- `GET /api/v1/deidentification-provider-assignments`
- `POST /api/v1/deidentification-provider-assignments`
- `DELETE /api/v1/deidentification-provider-assignments`
- `GET /api/v1/deidentification-selection`
- `GET /api/v1/deidentification-selection/options`
- `POST /api/v1/deidentification-selection`
- `DELETE /api/v1/deidentification-selection`
- built-in native provider remains selectable for every team by default
- external providers require explicit admin assignment before team selection
- these are metadata and secret-reference routes, not transcript-content routes

## Error envelope

All non-2xx JSON responses use:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed",
    "details": {
      "issues": []
    }
  }
}
```

Rate-limited requests return the same envelope with:

- status `429`
- code `rate_limited`
- message `Too many requests`

## Current auth and authorization rules

### Authentication

- protected JSON routes require a valid opaque session cookie
- unauthenticated access returns `401 unauthorized`
- invalid login credentials return the same `401 unauthorized` response shape
- the explicit session-public API route allowlist is currently:
  - `POST /api/v1/auth/login`
  - `POST /api/v1/auth/logout`
  - `POST /api/v1/account-requests`
- completed MFA-enabled users may receive `auth_level = pending_mfa` after password success
- login is rate-limited at `5 per 5 minutes` per client IP
- whole-file transcript uploads are rate-limited at:
  - `1 per 5 seconds`
  - `100 per day`
- whole-file upload throttling is shared across:
  - `POST /api/v1/transcripts/{transcript_id}/audio-file`
  - `POST /transcribe/upload`
- whole-file upload throttling keys to the authenticated user when a valid session resolves, with hashed-session/IP fallback only when user resolution is unavailable
- whole-file uploads are also capped by:
  - raw upload size: `25 MB`
  - normalized whole-file duration: `30 minutes`

### Pending-MFA sessions

- accounts with completed onboarding and active TOTP may still require a second step after password login
- the resulting session has `auth_level = pending_mfa`
- pending-MFA sessions may use only:
  - `auth/me`
  - `auth/mfa/totp`
  - `auth/logout`
  - `auth/trusted-device`
- normal JSON routes return:
  - `403`
  - code `mfa_required`

### Trusted-device freshness

- trusted-device cookies only influence post-password MFA skipping
- they do not replace password login
- a fresh trusted device currently means:
  - same browser still holds the opaque trusted-device cookie
  - the server-side record is not revoked or expired
  - the last MFA verification was within 24 hours

### Public account requests

- account-request submission is rate-limited at `3 per hour` per client IP

### MFA challenge

- TOTP challenge submission is rate-limited at `10 per 10 minutes` per client IP

### Onboarding-only sessions

- accounts with incomplete onboarding authenticate successfully
- the resulting session has `auth_level = onboarding`
- onboarding-only sessions may use only onboarding routes, `auth/me`, and logout
- normal JSON routes return:
  - `403`
  - code `onboarding_incomplete`

## Route auth audit

- run `./.venv/bin/python scripts/audit_api_auth.py` to probe every `/api/v1` route with:
  - no session cookie
  - an invalid session cookie
  - onboarding, pending-MFA, normal-user, and leader sessions where denial is expected
- the script exits non-zero if:
  - a protected route does not deny the expected scenario
  - a new `/api/v1` route exists without an audit manifest entry

### Manager routes

These routes require a full authenticated manager session:

- `GET /api/v1/account-requests`
- `POST /api/v1/account-requests/{request_id}/approve`
- `POST /api/v1/account-requests/{request_id}/reject`
- `POST /api/v1/users`
- `GET /api/v1/users`
- `POST /api/v1/users/{user_id}/suspend`
- `POST /api/v1/users/{user_id}/reactivate`
- `DELETE /api/v1/users/{user_id}`
- `GET /api/v1/stt-selection`
- `GET /api/v1/stt-selection/options`
- `POST /api/v1/stt-selection`
- `DELETE /api/v1/stt-selection`

Managers are:

- system admins
- team leaders

Leader scope is restricted to their own team.

Current account-administration behavior:

- leaders may suspend and reactivate non-system-admin users in their own team only
- leaders may delete non-system-admin users in their own team only
- system admins may suspend and reactivate other users across teams
- system admins may delete other users across teams
- no manager may suspend their own account through these routes
- no manager may delete their own account through these routes
- suspended users cannot log in
- reactivated users are forced back into password-change onboarding and must re-establish MFA setup
- delete is immediate hard delete and returns `204`
- deleting a user removes currently implemented user-owned transcript data immediately

Current STT-configuration behavior:

- system admins provision STT endpoint rows and Vault-backed secrets per team
- system admins may list, inspect, create, update, and delete provisioned STT configs, but must supply `team_id`
- leaders may not provision, rotate, or delete STT credentials
- leaders may read only their own team's selectable provisioned endpoints through the selection routes
- leaders may set or clear only their own team's active STT selection
- normal users may not access provisioning or selection routes
- onboarding-only and pending-MFA sessions may not access provisioning or selection routes
- `generic_rest` inspection fetches `base_url + openapi_path` and returns inferred fields without saving
- `openai_cloud` inspection uses the official OpenAI SDK server-side to return built-in contract defaults plus a filtered `available_models` list
- if OpenAI model discovery fails, `openai_cloud` inspection falls back to a built-in transcription-model allowlist and still returns `200`
- `openai_cloud` inspection also returns labeled model-option metadata so the UI can show whether each choice was `fetched` live or supplied from the built-in `default` list
- `openai_compatible_rest` inspection returns built-in known-contract defaults without OpenAPI fetch
- inspection also returns documented field descriptions and required flags when the provider's OpenAPI schema exposes them
- the admin HTML inspect flow preserves the just-entered token only for the current rendered page so the immediate save can reuse it without retyping
- saved STT config now carries an explicit `adapter_kind`
- currently supported adapter families are `generic_rest`, `openai_cloud`, and `openai_compatible_rest`
- the API never returns the bearer token
- the API currently returns metadata plus `has_secret`, not the raw Vault secret reference
- one team may have multiple provisioned STT config rows
- one team may have only one active STT selection row

Current LLM-configuration behavior:

- system admins provision LLM provider rows and Vault-backed secrets per team
- system admins may list, inspect, create, update, and delete provisioned LLM configs, but must supply `team_id`
- leaders may not provision, rotate, or delete LLM credentials
- leaders may read only their own team's selectable provisioned LLM providers through the selection routes
- leaders may set or clear only their own team's active LLM selection
- the active team selection stores both:
  - a team default model
  - an allowed-model subset that controls which models normal users can see and choose
- normal users may not access provisioning or team-selection routes
- normal users may set or clear only their own preferred default model through `/api/v1/llm-preference`
- normal team users may read, write, and clear only their own `/api/v1/app-preferences` row
- `user_app_preferences` currently stores validated workflow metadata only:
  - favourite quick action ids
  - favourite template ids
  - default quick action/template ids
  - `llm_detail_level`
  - preferred recording mode
  - preferred transcribe tab
- `user_app_preferences` rejects template/quick-action ids outside the caller's currently visible owner/team scope
- when referenced templates or quick actions are later deleted or hidden, `/api/v1/app-preferences` drops those stale ids lazily on read
- if the user's preferred model is no longer allowed for the active team provider, runtime resolution falls back to the team-selected default model
- the implemented LLM adapter families are `openai_chat`, `bedrock_chat`, and `ollama_chat`
- `openai_chat` inspection uses the official OpenAI SDK server-side to return built-in contract defaults plus a filtered `available_models` list
- if OpenAI model discovery fails, `openai_chat` inspection falls back to a built-in chat-model list and still returns `200`
- `bedrock_chat` uses Amazon Bedrock's OpenAI-compatible Bedrock Mantle endpoint and the existing OpenAI SDK integration for both `/models` discovery and Chat Completions generation
- `bedrock_chat` accepts an optional `bedrock_region`; when `base_url` is blank OpenScribe derives `https://bedrock-mantle.<region>.api.aws/v1`
- `bedrock_chat` does not use a built-in fallback model list because the available models are region- and account-specific; admins may still save a model manually if discovery is unavailable
- `ollama_chat` inspection calls `GET /api/tags` on the configured Ollama host and generation uses streaming `POST /api/chat`
- local Ollama may run without an API key; remote Ollama endpoints must still use `https`
- the admin HTML inspect flow preserves the just-entered API key only for the current rendered page so the immediate save can reuse it without retyping
- remote LLM endpoints must use `https`; `http` is accepted only for localhost/private-network hosts
- the API never returns the bearer token
- the API currently returns metadata plus `has_secret`, not the raw Vault secret reference
- normal team users may patch only their own ready note documents through `/api/v1/generated-documents/{generated_document_id}`
- note save requests must include `expected_updated_at`; stale revisions return `409 conflict`
- one team may have multiple provisioned LLM config rows
- one team may have only one active LLM selection row

Current template behavior:

- team templates are normal configuration data, not transcript-derived content
- leaders may create, update, list, and delete team templates for their own team
- normal users may create, update, list, and delete only their own personal templates
- system admins do not own or manage transcript-derived generation output through these routes
- template updates create a new immutable `template_versions` row while updating the logical template root metadata
- quick actions now follow the same team/personal scope model as templates:
  - leaders may create, update, list, and delete team quick actions for their own team
  - normal users may create, update, list, and delete only their own personal quick actions
  - quick action updates create a new immutable `quick_action_versions` row while updating the logical quick action root metadata

Current generation behavior:

- note generation is owner-only and runs against the selected transcript root
- follow-up generation is also owner-only and runs against the selected transcript root
- quick action generation is owner-only and runs against the selected transcript root
- generation snapshots the current transcript draft into a new `transcript_versions` row before calling the LLM
- queued generated-document rows now also snapshot:
  - resolved `llm_config_id`
  - resolved `model_used`
  - prompt text for template and quick-action runs
  - provider execution metadata needed to keep the worker stable if team defaults later change
- generation resolves the active team LLM provider plus the user's preferred/default model through the existing provider-selection path
- generation currently supports both OpenAI chat-style providers and Ollama chat hosts
- generation now applies native PHI pseudonymisation before outbound LLM calls:
  - a successful reusable `redaction_runs` row is created lazily per `transcript_versions` snapshot when first needed
  - `redaction_entities` persist the placeholder-to-original mapping for later reconstruction
  - generated-document rows keep the `redaction_run_id` used for that run
  - transcript text is sent to the external LLM only in redacted form
  - free-text follow-up/template/quick-action instructions are also redacted transiently before the provider call
  - generated output is validated so only well-formed known placeholders survive to re-identification
  - final stored output is re-identified before being written back into `generated_documents`
- a dev-only verification endpoint now exists for localhost seeded test accounts:
  - `GET /api/v1/generated-documents/{generated_document_id}/redaction-debug`
  - it remains owner-only
  - it returns the redacted transcript payload and placeholder inventory for the linked `redaction_run`
  - it does not return the original PHI values
- the implemented generators are:
  - template-based note output that now requires the LLM to return JSON with:
    - `title`: a short user-facing consultation summary
    - `content`: the full note body for `freeform` templates, or an object keyed by selected EMIS section names for `structured` templates
  - freeform follow-up output
  - quick action freeform output written back into the follow-up lane
- template mode now supports:
  - `freeform`
  - `structured`
- the first structured profile is EMIS with allowed section keys:
  - `problem`
  - `history`
  - `family_history`
  - `social_history`
  - `examination`
  - `comment`
  - `tasks`
  - `investigations`
- structured template versions store per-section instructions in `template_versions.config_json`
- structured generation may also include optional owner-provided `structured_context` keyed by selected EMIS sections so existing section text can be sent into the LLM as context
- the current transcript session now also stores EMIS working context in `transcripts.structured_context_json`
- `/transcribe` reloads EMIS context fields from that transcript-backed state
- when a structured note is queued, the current EMIS context is:
  - saved back onto the transcript root
  - snapshotted onto `generated_documents.structured_context_json`
- for structured notes, backend validation:
  - rejects user-submitted section keys outside the configured EMIS subset
  - filters transcript-persisted EMIS sections that are not present in the selected template
  - drops empty sections
  - preserves configured section order
  - renders full note text into `generated_documents`
  - persists section parts into `generated_document_sections`
- for template-generated notes, the returned JSON `title` is persisted into `generated_documents.title`
- if a template-generated note returns invalid JSON or omits `title`/`content`, generation fails with `llm_generation_invalid_json`
- template-note JSON parsing applies only mild coercion before failure:
  - strips markdown code fences
  - extracts the first balanced JSON object if the model wraps it in surrounding prose
- if note JSON still fails, the raw redacted provider output is retained on the generated document for localhost dev-account debugging only
- generation is now asynchronous:
  - `POST /api/v1/transcripts/{transcript_id}/generate-output` returns `202`
  - `POST /api/v1/transcripts/{transcript_id}/generate-followup` returns `202`
  - `POST /api/v1/transcripts/{transcript_id}/run-quick-action` returns `202`
  - the app creates a `generated_documents` row immediately with status `queued`
  - a Celery worker later moves it through `processing` to `ready` or `failed`
- follow-up generation stores the typed follow-up request on the queued generated-document row and uses the same worker, rate limits, and metadata-only usage logging as note generation
- quick action generation stores the selected `quick_action_version_id` plus the quick action name on the queued generated-document row and uses the same worker, rate limits, and metadata-only usage logging as notes/follow-ups
- generated output is persisted into `generated_documents` and remains private to the transcript owner
- template or quick-action deletion no longer breaks queued/generated output:
  - generated documents retain their prompt snapshot
  - source version references may be cleared when the source asset is deleted
  - already queued work still has enough context to run
- generation routes are throttled per authenticated user:
  - `1 per 5 seconds`
  - `100 per day`
- browser and JSON generation routes share the same authenticated limiter bucket
- generation workers now persist metadata-only usage events in `provider_usage_events` as well as emitting runtime usage logs
- generation metadata now carries team/user IDs, provider/model names, statuses, durations, input/output/total token counts, and safe provider error metadata when available
- generated-document rows now retain per-run input/output/total token counts, durations, provider HTTP status, and safe provider error codes for later debugging
- failed generations now keep a more specific safe reason where available, such as provider timeout, unreachable provider, rejected credentials, missing model, or provider-side rate limiting
- transcript deletion cascades to generated documents through the transcript-root delete path

### System-admin-only routes

These require a full authenticated system-admin session:

- `POST /api/v1/teams`
- `GET /api/v1/teams`

### Transcript routes

Transcript routes require a full authenticated user and remain owner-only:

- `POST /api/v1/transcripts/start` creates the transcript root for the current user
- `/api/v1/transcripts/start` records or implies `ingestion_mode`
- a user may create a transcript only for `owner_user_id == current_user.id`
- a user may commit only their own transcript
- a user may list only their own transcripts
- a user may upload audio chunks only for their own transcript

Current transcript-start behavior:

- the current user becomes `owner_user_id`
- `team_id` is derived from the current user
- `title` lives on the transcript root and is the current browser-level session title
- the transcript root remains the current session root for retention, versions, and derived-document lineage
- creating a new transcript root is rejected when the owner's latest transcript is still blank:
  - title-only does not count as content
  - a non-empty draft, a transcript version, or an ingestion job does count
- creating a new transcript root is also rejected while the owner's latest session is still `transcribing`
- `PATCH /api/v1/transcripts/{transcript_id}` currently supports owner-only title updates
- `PATCH /api/v1/transcripts/{transcript_id}` also supports owner-only `ingestion_mode` switching between `whole_file` and `live_chunked`
- mode switching is allowed only while the session is still blank and idle
- `DELETE /api/v1/transcripts/{transcript_id}` hard-deletes the owner transcript root immediately and cascades to transcript versions and ingestion jobs
- system-admin accounts are blocked from owning transcript content
- `ingestion_mode` is persisted on the transcript root and currently supports:
  - `whole_file`
  - `live_chunked`
- if the caller omits `ingestion_mode`, the route currently implies `whole_file`
- team retention defaults are applied when no explicit retention override is supplied
- transcript JSON responses remain owner-plaintext even though transcript drafts, transcript structured context, committed transcript versions, STT job result text, generated-document body fields, generated-document sections, follow-up prompts, redaction output text, and redaction entity values are now stored encrypted at rest per owner
- transcript and generated-document `title` fields remain plaintext metadata in this slice

Current live chunk-ingestion behavior:

- `POST /api/v1/transcripts/{transcript_id}/audio-chunks` accepts multipart audio upload for owner-only live chunked transcripts
- live chunk upload is rate-limited to `1 request/second` per authenticated user/session bucket
- live chunk queueing also enforces a rolling hourly audio budget per authenticated owner; the default ceiling is `3600` uploaded seconds per hour via `LIVE_CHUNK_HOURLY_DURATION_LIMIT_SECONDS`
- the route currently requires:
  - `audio`
  - `chunk_sequence_no`
- the route currently accepts:
  - `declared_duration_seconds`
- chunk uploads are rejected unless the transcript `ingestion_mode` is `live_chunked`
- the server measures the uploaded audio duration before queueing:
  - that measured duration is what counts toward the rolling hourly budget
  - client-supplied `declared_duration_seconds` is no longer trusted for budgeting
- live chunk uploads reject measured durations above the current 30-second maximum
- the route queues a transcript-ingestion job and returns `202 Accepted`
- the response includes both the transcript summary and the queued ingestion job
- queued live chunk jobs now persist both `source_audio_size_bytes` and the measured chunk duration in `declared_duration_seconds` so upload volume can be aggregated later for dashboarding or broader ingestion policy
- queued chunk jobs now snapshot the resolved STT provider execution settings at enqueue time:
  - selected STT config id
  - adapter kind
  - base URL and transcribe path
  - resolved model and language
  - file field name, response text path, and extra form fields
- the backend worker normalizes the uploaded audio to `16 kHz` mono PCM WAV with `ffmpeg`; ffprobe/ffmpeg calls have bounded timeouts so stuck media inspection/normalization fails cleanly
- the backend worker reads the queued STT snapshot plus the selected provider credentials from Vault
- the backend worker forwards the normalized chunk to the external STT service
- the backend worker encrypts the returned live-chunk text at rest before later owner-visible draft reconciliation
- live chunk application is sequence-aware:
  - duplicate `chunk_sequence_no` values are rejected at queue time
  - completed chunks are appended only in order using `next_live_chunk_sequence_no_applied`
- live chunk jobs left queued or processing beyond `LIVE_CHUNK_PROCESSING_STALE_AFTER_SECONDS` are marked `failed` with `ingestion_processing_stale` during transcript reconciliation, so later completed chunks can advance through the existing failed-gap path
- the transcript status remains `transcribing` while more live chunks may still arrive
- leaders/admins may configure team transcription metadata without gaining transcript readability

Current whole-file ingestion behavior:

- `POST /api/v1/transcripts/{transcript_id}/audio-file` accepts multipart audio upload for owner-only `whole_file` transcripts
- whole-file queueing now records both `source_audio_size_bytes` and `source_audio_duration_seconds` on the ingestion job for later upload reporting
- whole-file queueing enforces a rolling hourly upload budget per authenticated owner:
  - upload bytes via `WHOLE_FILE_HOURLY_UPLOAD_BYTES` (default `262144000`)
  - source audio duration via `WHOLE_FILE_HOURLY_DURATION_LIMIT_SECONDS` (default `7200`)
- whole-file ingestion no longer persists newly uploaded source audio blobs in Postgres while the owner-content at-rest encryption path is still pending
- newly uploaded whole-file source audio is retained for retry in Vault-backed secret storage, with only a Vault reference stored on the ingestion job row
- `POST /api/v1/transcripts/{transcript_id}/retry-audio-file` works when the latest failed whole-file job still has a stored retry source, either as a legacy DB blob or a Vault-backed source-audio ref
- transcript deletion and user deletion now attempt best-effort cleanup of any Vault-backed retry audio before the owning rows are removed, without blocking the hard-delete path on a transient Vault outage
- applied whole-file jobs now keep `source_audio_size_bytes` and `source_audio_duration_seconds` so rolling hourly budgets continue to count recently completed uploads
- file ingestion is rejected unless the transcript `ingestion_mode` is `whole_file`
- file ingestion is rejected while another `audio_file` ingestion job for that transcript is already `queued` or `processing`
- the route queues a transcript-ingestion job and returns `202 Accepted`
- queueing now fails early if no active team STT selection exists
- queueing now also fails early with `stt_config_secret_missing` if the selected STT config expects a saved credential and Vault no longer has it
- queued file jobs snapshot the resolved STT provider execution settings at enqueue time, so later team-setting changes do not alter where an already-uploaded file is sent
- the backend worker normalizes the uploaded audio to `16 kHz` mono PCM WAV with `ffmpeg`; ffprobe/ffmpeg calls have bounded timeouts so stuck media inspection/normalization fails cleanly
- the backend worker uses the queued STT snapshot plus the saved bearer credential when the selected adapter needs one
- the backend worker forwards the normalized audio file to the external STT service
- the backend worker appends the returned transcript text into `current_draft_text_encrypted`
- transcript drafts, committed transcript versions, and STT job result text now use one wrapped user DEK per normal content-owning user, with the DEK wrap/unwrap path handled through Vault Transit
- the transcript status moves to `ready` when the provider returns successfully
- if the queued STT config no longer has a readable saved credential, the job is marked `failed` with the same `stt_config_secret_missing` message the browser upload path uses
- generic REST STT failures now keep safer detail at the job level:
  - connect failure -> `stt_unavailable`
  - timeout -> `stt_timeout`
  - upstream non-2xx -> `stt_request_failed` with `status_code`
  - unreadable JSON or missing transcript text path -> `stt_response_invalid`
- `GET /api/v1/transcripts/{transcript_id}` now includes the latest ingestion failure metadata when present:
  - `next_live_chunk_sequence_no_upload`
  - `latest_ingestion_job_status`
  - `latest_ingestion_error_code`
  - `latest_ingestion_error_message`
  - `latest_ingestion_retry_available`
- `GET /api/v1/transcribe/workspace` now exposes the owner-facing read model for the `/transcribe` page:
  - `recent_transcripts`
  - `active_transcript`
  - `generated_documents`
  - `available_templates`
  - `available_quick_actions`
  - `active_structured_context`
  - current session-level capability flags like `can_create_new_session` and `can_switch_to_whole_file`
- `GET /api/v1/transcribe/workspace/stream` now exposes the same owner-facing workspace payload as an SSE stream for the `/transcribe` page.
- the SSE route validates auth using short-lived DB sessions and does not hold a request-scoped SQLAlchemy session open for the lifetime of the stream
  - emits `workspace` events
  - follows the same owner-only access rules as the JSON workspace endpoint
- the owner-facing `/transcribe` workspace now:
  - creates blank sessions from the session rail
  - blocks a second blank session until the latest session has draft content or descendant work, or is deleted
  - also blocks a new session while the latest session is still transcribing
  - requires an active selected session before upload
  - uses a single whole-file session type in the browser and lets the user choose file upload or microphone batch inside the session
  - queues file ingestion into the selected transcript root
  - records microphone batches locally in the browser with `MicVAD` voice-only gating plus short buffer and submits one captured WAV blob through the same `/transcribe/upload` file-ingestion path
  - supports bulk-delete of selected transcript sessions from the session rail
  - exposes `recent_transcripts[].has_transcript_content` as an owner-only boolean so the browser can require confirmation before deleting a non-empty session without exposing transcript text in the rail
  - hydrates the active workspace state from `GET /api/v1/transcribe/workspace`
  - keeps an owner-scoped SSE connection to `GET /api/v1/transcribe/workspace/stream` for pushed workspace updates
  - falls back to polling the same owner-only workspace read model only while a live session is actively recording or restarting if SSE is unavailable or disconnected
  - creates new sessions through `POST /api/v1/transcripts/start`
  - deletes selected sessions through owner-scoped `DELETE /api/v1/transcripts/{transcript_id}` calls
  - switches a blank session back to `whole_file` through `PATCH /api/v1/transcripts/{transcript_id}`
  - switches the active session in place by refetching `GET /api/v1/transcribe/workspace?transcript_id=...` instead of full-page navigation
  - patches transcript session title and EMIS working context through `PATCH /api/v1/transcripts/{transcript_id}`
  - queues whole-file upload directly through `POST /api/v1/transcripts/{transcript_id}/audio-file`
  - offers retry through the same workspace when `active_transcript.latest_ingestion_retry_available` is true
  - queues note/follow-up/quick-action generation directly through the corresponding `/api/v1/transcripts/{transcript_id}/...` JSON routes
  - enforces the same 4000-character limit for quick-action additional context on the API path as the browser textarea, trimming blank-only values to null server-side
  - the non-JS `/transcribe/run-quick-action` form path now enforces that same quick-action additional-context limit before queueing work
  - shows recent owner transcripts and current draft text on refresh or poll completion
  - preserves structured EMIS note section rendering and copy-selected-lines behavior during workspace refreshes by rebuilding the section view from generated-document section data
  - includes each structured generated note's snapshotted allowed section definitions in workspace/API note payloads so deleted template provenance does not expand the editable section set on refresh
  - silently saves dirty owner note edits before switching note-history versions; if save fails or conflicts, the browser keeps the current editor state selected
  - now shows explicit session progress copy in the header and active rail row for local recording, uploading, queued, transcribing, ready, and failed states
- if no active team STT selection exists, the browser flow fails early with:
  - `No STT configured, please ask your team leader {email}`
  - or a generic team-leader message when no active leader email is available

System-admin or leader authority does not grant transcript-content access.

### Provider model enforcement

- STT and LLM selection flows now enforce server-provided model lists server-side, not only in the UI
- leader/team LLM allowed-model subsets must be chosen from the provider-discovered model list
- user LLM preferences must be chosen from the leader-approved allowed-model subset
- STT team selection rejects model overrides outside the provider-discovered model list
- if a provider does not return a selectable model list, the selection APIs reject free-text overrides rather than silently accepting them

## Current uniqueness and onboarding rules

### User email

- emails are normalized before persistence
- uniqueness is enforced case-insensitively by a unique index on `lower(email)`

### Team name

- teams keep the display `name`
- teams also store a canonical `name_key`
- `name_key` is built from Unicode normalization + trim + collapsed whitespace + case-folding
- uniqueness is enforced on `name_key`

### Account requests

- account requests are deduplicated by normalized email + normalized requested team name while pending
- creating a request for an existing user email returns `409 conflict`

### Managed users

- manager-created users are active immediately
- they are created with a temporary password hash
- they start with:
  - `must_change_password = true`
  - `onboarding_state = pending_password_change`
