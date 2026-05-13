# Transcript Capture and Team STT Planning

This document defines the next transcript-facing MVP slice before implementation starts.

The detailed browser live-VAD concept for the implemented `live_chunked` path, now based on pinned `@ricky0123/vad-web` browser assets, lives in [live_stt.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/live_stt.md).

The goal is to let a user create a transcript by recording audio locally, chunking it on the client, and sending chunks to a team-managed speech-to-text endpoint without weakening the existing ownership and privacy model.

This planning doc now explicitly covers three future ingestion modes so the backend foundation does not overfit the first one we implement.

## Objective

Add a first transcript capture flow with:

- transcript root creation when recording starts
- client-side chunking with VAD
- backend forwarding of audio chunks to the team transcription endpoint
- backend-owned draft text updates into `transcripts.current_draft_text_encrypted`
- transcript draft text, transcript structured context, committed transcript versions, STT job result text, generated-document body fields, generated-document section text, follow-up prompt text, redaction output text, detected redaction entity values, and owner-entered manual PII values encrypted at rest before Postgres persistence
- existing commit/version behavior preserved

Longer-term capture modes to support on the same foundation:

- file upload transcription
- microphone batch transcription
- live chunked transcription driven by VAD and maximum chunk length

This slice is about transcript draft ingestion on top of admin-provisioned STT endpoints and team-selected STT policy. It is not about note generation, sharing, or admin visibility into content.

Current metadata boundary:

- transcript and generated-document `title` fields remain plaintext metadata in this slice
- owner-facing API and browser responses still return plaintext content after normal owner authz

## Core constraints

- transcript-derived content remains owner-only
- system admins provision STT endpoints and credentials for teams
- team leaders choose the active STT service/model from the admin-provisioned options for their team and may clear that selection
- neither leaders nor system admins may read transcript content by virtue of STT management
- provider secrets must not be stored raw in the database
- Vault remains the secret layer for provider credentials
- Vault Transit is now also the KEK layer for owner DEKs
- transcript roots remain the deletion and retention root
- committed transcript versions are still created only on blur, save, or explicit action

## End-to-end flow

## Planned ingestion modes

The product should eventually support three distinct user entry points:

### 1. File upload

User selects an existing audio file and asks OpenScribe to transcribe it.

Characteristics:

- single uploaded file
- backend may chunk it internally later if needed
- transcript root still belongs to the current user
- best fit for retrospective dictation or imported recordings

### 2. Microphone batch transcription

User records from the microphone locally. The browser normally submits captured voice audio when the user stops, but it rolls over before the local WAV approaches the whole-file upload limits: the current part is sent to the normal whole-file transcription endpoint and capture restarts for the same transcript only after that part is accepted. Each queued part carries the transcript id captured at recording time, so delayed uploads cannot drift to a newly selected consultation.

Characteristics:

- one logical recording session with one or more uploaded recording parts
- upload occurs after stop, or automatically during recording when a part approaches size/duration limits
- backend still normalizes audio before STT submission
- useful when low latency is not required
- if the server still has a whole-file job in progress, the browser keeps the next part in memory and retries so the existing one-job-at-a-time backend rule is preserved
- if a rollover part cannot upload, capture stops instead of recording later audio that would create a silent transcript gap after the failed part
- workspace refreshes must keep the local microphone control in the active `Stop` state while batch capture or rollover restart is still running, even though this mode is not live chunked capture

### 3. Live chunked transcription

User records from the microphone while the client sends short chunks during the session.

Characteristics:

- client-side VAD prunes silence
- pauses in speech and a configured max chunk length trigger chunk submission
- backend updates the current transcript draft incrementally
- stopping live capture calls a server-side finalize step that applies completed chunks, moves the transcript out of `recording`, and creates/reuses a version-linked redaction run once no chunks remain pending
- if queued or processing chunks still exist, finalize leaves the transcript `transcribing`; completion/reconciliation creates the redaction preview once the transcript becomes `ready`
- this is the first live-oriented mode we expect to implement

## Shared backend foundation

These three modes should share one backend model rather than three unrelated code paths.

Shared invariants:

- transcript root is created before content ingestion starts
- transcript root remains the retention and deletion root
- transcript root is also the current browser/session root for the workspace
- all transcript-derived content remains owner-only
- backend normalizes audio before provider submission
- backend resolves the active team STT selection and Vault-backed secret
- backend owns persisted draft text updates
- commit/version semantics remain separate from ingestion semantics

## Post-consultation dictation Phase 0 lock

Post-consultation dictation is separate transcript-derived child source, not extension of consultation draft itself.

Locked direction:

- do not write dictation text into `transcripts.current_draft_text_encrypted`
- do not attach dictation text to `generated_documents`; dictation is generation input, not one document-specific artifact
- create one transcript-owned dictation aggregate per transcript root
- append later dictation passes as immutable child segment rows under same dictation aggregate
- keep user-editable combined dictation text on parent dictation row

Generation contract:

- transcript remains base source for chronology and patient-spoken content
- dictation remains separate clinician-authored source for summary/assessment/plan emphasis
- if combined dictation text was user-edited, generation uses that combined text exactly
- otherwise generation concatenates immutable dictation segment ASR text in append order
- empty edited combined text means clinician intentionally removed dictation influence, so generation should omit dictation rather than falling back to raw segments

Prompt contract:

- prompt assembly should label consultation transcript and post-consultation dictation as separate sources
- prompt should instruct model to treat dictation as stronger clinician-authored signal for interpretation, terminology, and plan wording
- prompt should still keep transcript as factual consultation anchor and should not permit invention of facts absent from both sources

Workspace UX:

- the post-consultation dictation CTA appears beside the main consultation transcription controls so it is reachable from any workspace tab
- if dictation STT is not configured or unavailable, the CTA remains available for manual typed dictation, but recording/upload controls are disabled with an unavailable message
- clicking the CTA opens a modal recording/editing surface and focuses/highlights the dictation record button when recording is available
- clicking the CTA does not start recording automatically
- the modal records locally with record/pause/resume/stop controls, then sends audio to a preview STT endpoint that returns text without creating dictation rows or segments
- saving is explicit; cancel discards unsaved modal text/audio without DB writes
- saved dictation remains visible in a compact separate-source block in the transcript/history area
- dictation-only sessions are still backed by a transcript root; generation may proceed from saved dictation even when the consultation transcript draft is empty
- live dictation is the primary path; audio upload remains a secondary fallback for separately recorded dictation audio and follows the same preview-before-save modal flow
- after the first successful consultation recording stop for a transcript with no generated documents and no saved dictation, the browser may show a one-time localStorage-backed nudge toast and CTA flash encouraging a short clinician summary
- quick-action context recording is a transient prompt-context helper: it sends short audio through the post-consultation dictation STT selection, inserts returned text into the existing quick-action context field, and relies on the current quick-action redaction boundary before any LLM request.

Shared backend concepts:

- transcript start
- ingestion mode
- optional recording session identifier
- normalized audio handoff to provider adapters
- metadata-only chunk records for observability and idempotency later
- whole-file upload records may retain the owner upload blob temporarily so failed STT attempts can be retried without asking the user to upload the same file again

Recommended ingestion-mode values:

- `whole_file`
- `live_chunked`

### 1. System admin provisions team transcription endpoints and credentials

System admins provision the STT endpoints and credentials available to a team in MVP.

This means:

- provider type is `transcription`
- the team has one active transcription provider policy for normal use
- the credential material is stored in Vault and referenced from the database
- the endpoint configuration is metadata only and does not expose content

### 2. Leader chooses the active team STT policy

Team leaders configure which provisioned STT service/model their team actively uses.

This means:

- leaders do not handle raw secrets directly
- leaders choose policy from admin-provisioned options
- leaders may clear that team-level STT selection
- users later consume only the resolved active team STT policy indirectly through transcript capture

### 3. User starts recording

When recording starts, or when the user begins a file/batch transcription flow, the client calls a transcript-start route.

The backend:

- creates the transcript root immediately
- sets `owner_user_id`
- sets `team_id`
- applies retention once
- initializes the transcript in recording state
- records the intended ingestion mode

Implemented now:

- `POST /api/v1/transcripts/start`
- current-user-derived `owner_user_id`
- current-user-derived `team_id`
- persisted `ingestion_mode`
- team-default retention when no explicit override is provided
- system-admin accounts remain blocked from owning transcript content

### 4. Client performs VAD and chunking

The client performs local VAD and produces uploadable chunks.

Current MVP direction:

- minimum chunk duration: 10 seconds
- maximum chunk duration: 30 seconds
- chunking is client-driven
- backend validates the declared chunk metadata and rejects obviously invalid uploads

The backend must not assume the client’s duration or sequence metadata is trustworthy without validation.

This step applies only to `live_chunked`.

For the other planned modes:

- `whole_file` with file-upload source: the client uploads one file without live chunking
- `whole_file` with microphone-batch source: the client keeps local voice-only segments with VAD pre/post buffer and uploads one batch at the end

Lifecycle handling for `live_chunked`:

- backgrounding the browser tab pauses `MicVAD` and flushes live capture before timer throttling can delay chunk upload
- unloading the browser tab stops local mic state and sends a best-effort keepalive finalize request
- finalize reconciles already uploaded chunks and prevents sessions from remaining in `recording` when no chunks are pending

### 5. User uploads a chunk

The client sends the audio blob plus chunk metadata to the backend.

The backend:

- verifies the current user owns the transcript
- creates a queued transcript-ingestion job and returns `202 Accepted`

The worker path then:

- normalizes the uploaded audio to a canonical backend format before provider submission
- resolves the active team transcription selection and Vault-backed credential reference
- fetches the secret from Vault
- forwards the chunk to the external STT endpoint
- receives transcript text from the provider
- applies completed live chunks in sequence order
- updates `transcripts.current_draft_text_encrypted`
- encrypts transcript draft/version/job text at rest before persistence and decrypts only after owner authz has already resolved the transcript
- records operational metadata without storing raw secrets in the database
- whole-file uploads now keep the raw upload blob only until one ingestion attempt succeeds, then that stored blob is cleared

Development note:

- the transcript-ingestion worker loads Python models and enum definitions at process start
- after enum/schema-adjacent code changes, restart the Celery worker along with the FastAPI app
- otherwise new jobs can fail under stale worker code and leave sessions appearing stuck in `queued` or `transcribing`
- `./start-dev.sh` now kills any existing OpenScribe FastAPI dev server and Celery worker processes before starting a fresh stack, to avoid mixed-code workers consuming the same queue during development

Implemented now for manual browser testing:

- `/transcribe` is the dedicated owner-facing transcription workspace
- the workspace now uses a warmer, more polished clinical shell with a persistent session rail, a dominant session header, and a cleaner tabbed main canvas
- the workspace now creates explicit transcript-root sessions from the session rail rather than treating upload as the only session-creation path
- session title currently maps directly to `transcripts.title`
- the session rail now supports multi-select and bulk-delete for owner transcript roots
- deleting selected sessions with non-empty transcript content now requires a browser confirmation; the rail uses an owner-only boolean content flag and does not render transcript text for inactive sessions
- the upload form targets the currently selected transcript session and queues whole-file ingestion
- the workspace presents file upload and microphone batch as options inside the active `whole_file` session instead of as separate session-creation paths
- the browser workspace currently creates `whole_file` sessions by default and can switch a blank `live_chunked` session back to `whole_file`
- the workspace blocks a second blank session until the latest session has actual content or descendant work, or is deleted
- the workspace also blocks new-session creation while the latest session is still transcribing in the backend
- the browser shell now surfaces blocked new-session attempts as toasts instead of a persistent sidebar warning, so the session rail does not shift during recording state changes
- whole-file upload routes are throttled to reduce authenticated abuse:
  - `1 per 5 seconds`
  - `100 per day`
- the browser and JSON whole-file upload routes share the same authenticated limiter bucket
- browser state-changing routes now require a CSRF token alongside the normal session cookie
- the workspace shows recent owner transcripts in the sidebar and opens the latest or explicitly selected transcript
- the upload flow is post/redirect/get, so page refresh does not re-upload the file
- the workspace now has a dedicated owner-only read model at `GET /api/v1/transcribe/workspace`
- the transcribe page now keeps an owner-only SSE subscription to `GET /api/v1/transcribe/workspace/stream` so draft/workspace updates can arrive without constant request polling when the stream is healthy
- if SSE is unavailable or disconnected, fallback polling is now limited to active live-recording/restart windows instead of continuing for the broader workspace
- the browser shell hydrates active transcript state, generated documents, available template/action lists, and EMIS working context from that workspace API
- the transcript history pane now shows owner-visible detected PII from the latest successful redaction run in a bounded right-side table beside the transcript text
- the browser highlights matching PII values in the transcript and lets the owner add/remove missed PII values that persist as owner-only encrypted transcript children
- the workspace polls the same owner-only workspace read model while the active transcript or generated documents remain pending
- failed whole-file STT attempts now expose a retry control in the workspace when stored retry audio is still available and the team still has a usable STT selection
- failed live chunks no longer permanently block later successful chunks; the owner workspace reconciles completed live chunks past failed sequence gaps as soon as later results exist
- `/transcribe-glm-2` now reuses that same workspace API while preserving the restored GLM 2 shell as the visual source of truth
- the GLM 2 workspace now also consumes STT availability metadata from that workspace API so it can visibly show when the selected STT provider is unavailable and disable whole-file recording/upload controls
- Parakeet testing showed that `timestamps=segment` returns richer sentence-like segment boundaries than `timestamps=word`, so paragraph formatting can start from segment mode and merge adjacent segments by pause/length/speaker heuristics
- The first paragraph heuristic was too loose for Parakeet’s relatively long sentence-sized segments; it now uses a tighter default paragraph length cap so whole-file uploads produce multiple readable blocks instead of one long slab
- whole-file HTTP STT responses that include a top-level `segments` array now paragraphize the stored transcript draft by joining grouped segments with blank lines; providers without `segments` still fall back to the configured response text path unchanged
- the GLM 2 route keeps note output, follow-ups, and history inside a right-side assistant pane that can be hidden, shown in split mode, or expanded to take over the main workspace
- the GLM 2 route now wires real session switching, title editing, upload, microphone batching, note generation, follow-up generation, quick actions, EMIS autosave, structured-line copy, and history refresh into that shell
- new-session creation now submits through `POST /api/v1/transcripts/start` from the browser shell
- selected-session deletion now submits through owner-scoped transcript `DELETE` API calls from the browser shell
- blank-session mode switching now patches `ingestion_mode` through the transcript JSON API
- session-rail transcript selection now refreshes the active workspace in place from the workspace API and updates the URL with the selected `transcript_id`
- session-rail transcript selection is blocked while local/live recording is active and asks the user to stop recording before switching
- session title changes now patch the transcript directly through the transcript JSON API instead of relying on a redirect round-trip
- the large session title in the workspace header is now the primary editable title control; pressing Enter or blurring the field saves it back to `transcripts.title`
- whole-file upload and microphone batch controls now live in the header bar instead of a separate audio-input panel
- the current HTML workspace styling has been tightened to use denser spacing and smaller radii so more of the clinical workspace fits on screen at once
- the EMIS section context editor is only shown when the selected note template is `structured`; freeform templates keep the output flow freeform-only
- EMIS working context now autosaves back onto the transcript session through the transcript JSON API
- whole-file upload and note/follow-up/quick-action queue actions now submit directly to the owner-only JSON APIs from the browser shell
- the session header reports the resolved user LLM model, not just the team default, so the displayed model matches the model the generation path will actually use
- the workspace now distinguishes local browser progress from backend queue status:
  - compact status-pill labels such as `listening`, `speech detected`, `sending chunk`, `stopping`, and `uploading` while MediaRecorder/live VAD is active
  - backend `queued`/`transcribing`/`ready`/`failed` once the transcript row reflects worker state
- when the active transcript is `failed`, the workspace now shows the latest owner-visible ingestion error message instead of only a generic failed-state banner
- missing active team STT selection is surfaced before queueing work rather than as a later failed worker job
- the Output tab now supports the first template-driven note generation action for the active session
- generation uses the current transcript draft plus a selected team/personal template and the resolved active LLM provider/model
- template note generation now requires the model to return only a JSON object with:
  - `title`
  - `content`
- the returned note `title` is persisted into the generated document title shown to the user
- when the session title is still blank or the default `Untitled session`, the first successful note title also fills the transcript session title
- the Follow-ups tab now supports freeform follow-up generation from the active session
- the Follow-ups tab now also supports running a selected team/personal quick action from a dropdown in the active session
- the Follow-ups tab now also exposes up to four quick-pick buttons for common quick actions so repeat actions can start without opening the dropdown first
- follow-up generation sends:
  - a fixed system instruction that the model is acting as a medical secretary in British English
  - the current transcript draft
  - the user-entered follow-up request text
- quick action generation sends:
  - a fixed system instruction that the model is acting as a medical secretary in British English and writing from the doctor's perspective
  - the current transcript draft
  - optional user-entered quick-action guidance text
  - the saved quick action instruction text from the selected quick action version
- generation now queues work asynchronously instead of blocking the browser request:
  - the browser route returns immediately after creating a queued `generated_documents` row
  - a Celery worker performs the LLM call in the background
  - the latest output later becomes `ready` or `failed` under the same transcript root
  - Ollama-backed generation now consumes streamed `/api/chat` chunks so long-running local generations can keep progressing before the final `done: true` usage chunk arrives
- template-generated notes now support two note modes:
  - `freeform`
  - `structured` EMIS
- structured EMIS templates let the user define per-section prompts for the allowed EMIS sections
- the Output tab now includes optional structured EMIS context fields so the user can pre-fill section content before generation
- that structured EMIS context now persists on the transcript session and reloads on the next `/transcribe` visit
- transcript-backed EMIS working context is now stored as per-section line arrays, not just one string per section
- when a selected EMIS template omits some sections, transcript-persisted context for those omitted sections is ignored instead of blocking generation
- when a structured EMIS note returns successfully:
  - the backend validates the returned section keys
  - the generated document now snapshots its allowed structured section definitions so later owner edits still honor the original template section set even if that template version is deleted
  - the snapshot migration also backfills existing structured generated notes from either the linked template version or persisted section rows so older notes keep the same edit guardrails
  - generated-note edits are also constrained to the source template's allowed section keys when the template version still exists, so removed sections cannot be reintroduced through the edit API
  - the owner edit API also rejects duplicate structured `section_key` values in one save payload so rendered note text and persisted section rows cannot diverge
  - renders the full note text onto the generated document
  - persists individual section rows for section-by-section display
  - the workspace shows line-selectable EMIS section output with clearer row separation and a copy-selected-lines action, including after workspace API refreshes and polling
  - generated-note copy controls now require the owner to view the relevant generated content first: structured section copy unlocks per section after its bottom is visible, reaching a later structured section bottom marks earlier sections reviewed, and generated freeform copy unlocks after the note bottom is visible; changing the copyable note text revokes the review state until the updated bottom is visible; hidden output panes do not count as reviewed; blocked copy controls remain clickable and surface a toast instead of an inline alert
- when the owner edits a generated note in the browser and switches to another note version before autosave fires, the UI now saves the dirty note first and only switches versions if that save succeeds
- before the worker sends transcript-derived text to an external LLM:
  - it lazily creates or reuses a `redaction_runs` snapshot for the queued `transcript_versions` row
  - it sends the redacted transcript text, not the raw transcript text
  - it tells the model that `[PHI-N]` placeholders are deliberate and must be preserved exactly
  - it validates returned placeholders before re-identification
  - it re-identifies the finished output before saving it in `generated_documents`
- localhost-only seeded dev accounts now get a dev redaction debug panel in `/transcribe` for the latest note/follow-up
  - this fetches the linked `redaction_run`
  - shows the redacted transcript payload and placeholder inventory that reached the outbound LLM path
  - if a note fails JSON parsing, the same dev-only debug payload now includes the raw redacted provider output for diagnosis
  - does not expose the original PHI values
- follow-up and quick-action free-text instructions are also redacted transiently before the provider call so outbound user-entered text follows the same PHI rule
- successful generation persists a `generated_documents` row under the current transcript root and renders the latest output back into the Output tab
- selected generated notes expose a permanent-delete control that confirms before calling the owner-only generated-document delete API
- successful follow-up generation persists a second `generated_documents` type under the same transcript root and renders it back into the Follow-ups tab
- successful quick action generation persists a third `generated_documents` type under the same transcript root and also renders it back into the Follow-ups tab/history
- the initial server-rendered follow-up history now uses the same copy/delete action hooks as the JS-refreshed cards, so those controls still work before the first workspace refresh
- template and quick-action save paths now translate raced unique-index collisions back into targeted 409 conflict responses, including concurrent version-number races during save
- generation routes are rate-limited per authenticated user:
  - `1 per 5 seconds`
  - `100 per day`
- metadata-only usage logging now records generation event type, IDs, provider/model names, durations, and separate input/output/total token counts when the provider returns them
- the same metadata is now persisted into `provider_usage_events` for later per-user and per-team usage analysis
- generated-document rows now also retain per-run input/output/total token counts, durations, and safe provider error metadata for owner-visible debugging without storing prompts or note text
- failed generations should now surface a safer, more specific reason when available, such as timeout, unreachable provider, credential rejection, or missing model
- important current caveat:
  - redacted transcript text and original PHI mappings are persisted in columns still named `*_encrypted`
  - until the encryption-at-rest slice lands, those fields are plaintext at rest even though only the redacted text is sent externally

## Session-root decision

For now, do not introduce a second `sessions` table.

Reason:

- `transcripts` already carries `title`
- `transcripts` is already the retention and deletion root
- committed versions already hang from the transcript root
- generated documents are planned to derive from transcript versions, not from an unrelated parallel root

Current decision:

- a browser "session" is the transcript root plus its derived versions/documents
- session title is stored in `transcripts.title`
- if we later need ingestion-session metadata, that should remain a child concept such as `transcript_ingestion_sessions`, not a replacement for the transcript root
- generated note output also hangs from this transcript root, rather than inventing a separate note-session root

## Current race-condition notes

Current behavior is intentionally simple:

- deleting multiple selected transcript roots is immediate and relies on the existing transcript-root cascade
- live chunk ordering is protected by `chunk_sequence_no` and ordered worker application
- switching between `whole_file` and `live_chunked` is allowed only before the session has transcript content or descendant work

Current session-creation guard:

- a second blank transcript root is rejected while the latest transcript is still empty
- a second transcript root is also rejected while the latest transcript is still transcribing
- title alone does not count as content
- a draft, a committed transcript version, or any queued/processing/completed ingestion job does count

Near-term hardening direction:

- disable browser submit buttons on form submit
- keep transcript root creation and title updates owner-only
- avoid introducing a second root table just to hide workflow races
- keep note generation owner-only and transcript-root-attached for the same reason
- add structured transcription-job logging later:
  - queued
  - started
  - completed
  - failed
  - without logging transcript text, prompts, or audio payloads

### 6. User commits a version

The existing commit flow remains:

- the user commits on blur, save, or explicit action
- a `transcript_versions` row is created
- the transcript root remains the deletion and retention root

## MVP backend shape

### Team-managed STT configuration

The MVP now supports one active transcription selection per team chosen from one or more admin-provisioned STT endpoint rows.

Recommended model direction:

- reuse the provider domain rather than inventing a second provider system
- use `providers`, `team_provider_credentials`, and `team_provider_policies`
- store endpoint and authentication material as Vault-backed credential data
- keep the database limited to metadata and Vault references

Likely manager-facing fields:

- provider label
- base URL
- model name
- active flag
- optional external account identifier
- Vault secret reference

Authentication material should be treated as secret even if it is just an API key.

### Transcript draft ingestion route

Planned API shape:

- `POST /api/v1/transcripts/start`
- `POST /api/v1/transcripts/{transcript_id}/audio-chunks`
- `POST /api/v1/transcripts/{transcript_id}/audio-file`
- existing `POST /api/v1/transcripts/{transcript_id}/commit`

Planned chunk request shape:

- transcript identifier in path
- audio blob in multipart form data
- chunk sequence number
- declared duration
- optional client timestamps/session ids needed for idempotency later

The backend should be the source of truth for any persisted draft text.

Implemented now for `live_chunked`:

- `POST /api/v1/transcripts/{transcript_id}/audio-chunks`
- multipart `audio`
- `chunk_sequence_no`
- optional `declared_duration_seconds`
- `1 request/second` rate limiting per authenticated user/session bucket
- browser-side live upload pacing at `1100ms` between request starts plus short same-sequence retry on route-level `429`
- rolling hourly audio budgeting per authenticated owner, default `3600` uploaded seconds/hour
- owner-only enforcement
- rejection when the transcript ingestion mode is not `live_chunked`
- queued ingestion job response
- persisted live upload telemetry on the queued ingestion job:
  - `source_audio_size_bytes`
  - measured audio duration stored in `declared_duration_seconds`
- queue-time snapshot of the resolved STT provider execution settings so later team-provider changes do not retarget already-submitted audio
- backend worker audio normalization before provider submission
- provider text append into `current_draft_text_encrypted` only when completed chunks can be applied in order
- live hourly budgeting is now based on server-inspected audio duration, not trusted client form input
- live finalize is used as the unload recovery path so already uploaded chunks are reconciled before the tab exits

Planned start request additions:

- `ingestion_mode`
- optional client recording session id

Implemented now:

- `ingestion_mode`

Still planned:

- optional client recording session id

Planned file/batch upload request shape:

- transcript identifier in path
- audio file in multipart form data
- ingestion source metadata where useful
- no transcript text in the request

Implemented now for `whole_file`:

- `POST /api/v1/transcripts/{transcript_id}/audio-file`
- multipart `audio`
- owner-only enforcement
- rejection when the transcript ingestion mode is not `whole_file`
- queued ingestion job response
- queued whole-file jobs now persist:
  - `source_audio_size_bytes`
  - `source_audio_duration_seconds`
- rolling hourly whole-file upload budgets per authenticated owner:
  - upload bytes via `WHOLE_FILE_HOURLY_UPLOAD_BYTES`
  - source duration via `WHOLE_FILE_HOURLY_DURATION_LIMIT_SECONDS`
- rejection at queue time when no active team STT selection exists
- queue-time snapshot of the resolved STT provider execution settings so later team-provider changes do not retarget already-submitted audio
- New Celery task payloads contain only `job_id`; workers read queued source audio from the stored Vault ref, while legacy `audio_b64` messages are accepted during rollout and moved into Vault-backed source storage before processing
- backend worker audio normalization before provider submission
- provider transcript text appended into `current_draft_text_encrypted`
- transcript status set to `ready` after successful provider completion
- newly queued whole-file jobs no longer persist raw source audio blobs in Postgres while the owner-content encryption layer is still pending
- newly uploaded whole-file retry audio is stored behind a Vault ref on the ingestion job rather than as a raw DB blob
- whole-file retry remains available when the failed job still has stored retry audio, either through the legacy blob column or the Vault-backed source-audio ref
- transcript-root deletion and user deletion attempt best-effort cleanup of any Vault-backed retry audio before the owning transcript rows are deleted, without blocking the delete path on a transient Vault outage
- applied whole-file jobs keep byte and duration telemetry so rolling hourly budgets continue to count recently completed uploads
- the browser microphone-batch UX now uses browser `MicVAD` locally to keep voiced segments plus short buffer, then posts one captured WAV blob into the same file-ingestion route rather than introducing a separate STT processing path

### Backend audio normalization

Before a chunk is sent to the STT provider, the backend should normalize it to:

- mono
- `16 kHz`
- PCM WAV

Practical target:

```bash
ffmpeg -i input.ext -ac 1 -ar 16000 -c:a pcm_s16le output.wav
```

Why this is the preferred default:

- it gives the backend one canonical provider-input format
- it reduces input variability across browsers and devices
- it makes duration and validation behavior more predictable

Rules:

- normalization is transient and request-scoped
- normalized audio is not stored in Postgres
- raw provider uploads should come from the normalized backend representation, not directly from the browser blob
- if a provider later supports a better direct format path, that can be an adapter-specific optimization, not the MVP default

Implemented now:

- live chunk uploads normalize audio through `ffmpeg` before STT submission
- whole-file uploads normalize audio through `ffmpeg` before STT submission
- whole-file uploads are bounded by:
  - raw upload size cap: `200 MB`
  - normalized duration cap: `4 hours`
- long whole-file processing is bounded by:
  - ffmpeg normalization timeout: `AUDIO_FFMPEG_TIMEOUT_SECONDS` default `1800`
  - STT provider request timeout: `STT_TRANSCRIPTION_TIMEOUT_SECONDS` default `14400`
- normalization failures surface as provider-path errors without storing newly uploaded raw audio in Postgres
- queued/generated LLM output now snapshots prompt/provider execution context as well:
  - transcript draft is committed into a `transcript_versions` row before generation
  - template and quick-action prompt text is copied onto the queued generated-document row
  - queued work can still complete even if the source template or quick action is later deleted

## Data model direction

### Needed in this slice

- team-scoped transcription provider configuration using the existing provider model
- transcript start endpoint that creates the root row at recording start
- a way to ingest chunk metadata and provider results safely

### Needed for a good long-term foundation

- transcript start should carry ingestion mode now, even if only one mode is implemented first
- ingestion services should normalize file upload, microphone batch, and live chunks into one internal provider-submission path
- provider adapters should accept normalized audio input rather than browser-specific upload assumptions

### Probably needed soon

A dedicated chunk metadata table is likely warranted once the first draft-ingestion path exists.

Reasons:

- idempotency for repeated uploads
- observability for provider failures and retries
- duration/ordering validation
- metadata-only auditability without storing audio payloads

Possible future table:

- `transcript_audio_chunks`

Likely fields:

- `id`
- `transcript_id`
- `owner_user_id`
- `team_id`
- `chunk_sequence`
- `declared_duration_seconds`
- `provider_request_id`
- `provider_id`
- `credential_id`
- `status`
- `error_code`
- `created_at`

This should remain metadata-only. Raw audio does not belong in Postgres for this slice.

Possible future companion table:

- `transcript_ingestion_sessions`

Likely fields:

- `id`
- `transcript_id`
- `owner_user_id`
- `team_id`
- `ingestion_mode`
- `client_session_id`
- `status`
- `started_at`
- `ended_at`

This is not required for the very first route, but the concept should shape the service boundaries now.

## Explicit non-goals for the first STT slice

- no realtime websocket transcription
- no raw audio storage in Postgres
- no admin or leader transcript readability
- no transcript sharing
- no provider-specific abstraction explosion beyond what the existing provider model already supports
- no note-generation coupling in the first chunk-ingestion slice
- no attempt to make the first route support all three ingestion modes at once

## Security and secret-handling rules

- raw STT credentials are stored in Vault, not in Postgres
- database rows store Vault references only
- logs may contain provider metadata and error codes, but not transcript text, prompt text, secrets, or audio payloads
- leaders may manage team STT configuration only within their team scope
- system admins may manage team STT configuration platform-wide
- content access remains based on `owner_user_id`, not manager role

## Planned implementation order

1. Define the team transcription provider config UI and API contract.
2. Add the backend service that resolves the active team transcription endpoint and Vault secret reference.
3. Add transcript-start route and tests.
4. Add live audio-chunk ingestion route and tests.
5. Add file-upload and microphone-batch planning hooks on the same service boundary.
6. Preserve existing commit/version behavior while routing draft updates through the new service layer.
7. Add docs and DB-test coverage for provider config, ownership, and retention invariants.

## Testable checkpoints

- leader can configure transcription metadata only for their own team
- system admin can configure transcription metadata for any team
- credentials are stored as Vault references, not raw secrets in DB
- user can create a transcript root on recording start
- transcript start records or implies the intended ingestion mode
- user can upload a valid chunk to their own transcript
- invalid chunk duration or malformed upload is rejected
- missing team transcription configuration fails deterministically
- repeated or conflicting chunk submissions have deterministic handling
- leader/admin authority does not grant transcript text visibility
- transcript commit/version behavior remains owner-only
