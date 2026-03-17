# Transcript Capture and Team STT Planning

This document defines the next transcript-facing MVP slice before implementation starts.

The goal is to let a user create a transcript by recording audio locally, chunking it on the client, and sending chunks to a team-managed speech-to-text endpoint without weakening the existing ownership and privacy model.

This planning doc now explicitly covers three future ingestion modes so the backend foundation does not overfit the first one we implement.

## Objective

Add a first transcript capture flow with:

- transcript root creation when recording starts
- client-side chunking with VAD
- backend forwarding of audio chunks to the team transcription endpoint
- backend-owned draft text updates into `transcripts.current_draft_text_encrypted`
- existing commit/version behavior preserved

Longer-term capture modes to support on the same foundation:

- file upload transcription
- microphone batch transcription
- live chunked transcription driven by VAD and maximum chunk length

This slice is about transcript draft ingestion on top of admin-provisioned STT endpoints and team-selected STT policy. It is not about note generation, sharing, or admin visibility into content.

## Core constraints

- transcript-derived content remains owner-only
- system admins provision STT endpoints and credentials for teams
- team leaders choose the active STT service/model from the admin-provisioned options for their team and may clear that selection
- neither leaders nor system admins may read transcript content by virtue of STT management
- provider secrets must not be stored raw in the database
- Vault remains the secret layer for provider credentials
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

User records from the microphone locally, then submits the whole captured recording as one batch at the end.

Characteristics:

- one logical recording session
- upload occurs after stop rather than during recording
- backend still normalizes audio before STT submission
- useful when low latency is not required

### 3. Live chunked transcription

User records from the microphone while the client sends short chunks during the session.

Characteristics:

- client-side VAD prunes silence
- pauses in speech and a configured max chunk length trigger chunk submission
- backend updates the current transcript draft incrementally
- this is the first live-oriented mode we expect to implement

## Shared backend foundation

These three modes should share one backend model rather than three unrelated code paths.

Shared invariants:

- transcript root is created before content ingestion starts
- transcript root remains the retention and deletion root
- all transcript-derived content remains owner-only
- backend normalizes audio before provider submission
- backend resolves the active team STT selection and Vault-backed secret
- backend owns persisted draft text updates
- commit/version semantics remain separate from ingestion semantics

Shared backend concepts:

- transcript start
- ingestion mode
- optional recording session identifier
- normalized audio handoff to provider adapters
- metadata-only chunk or upload records for observability and idempotency later

Recommended ingestion-mode values:

- `file_upload`
- `microphone_batch`
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

- `file_upload`: the client uploads one file without live chunking
- `microphone_batch`: the client records locally and uploads one batch at the end

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
- records operational metadata without storing raw audio or raw secrets in the database

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
- owner-only enforcement
- rejection when the transcript ingestion mode is not `live_chunked`
- queued ingestion job response
- backend worker audio normalization before provider submission
- provider text append into `current_draft_text_encrypted` only when completed chunks can be applied in order

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

Implemented now for `file_upload` and `microphone_batch`:

- `POST /api/v1/transcripts/{transcript_id}/audio-file`
- multipart `audio`
- owner-only enforcement
- rejection when the transcript ingestion mode is neither `file_upload` nor `microphone_batch`
- queued ingestion job response
- backend worker audio normalization before provider submission
- provider transcript text written into `current_draft_text_encrypted`
- transcript status set to `ready` after successful provider completion

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
- normalization failures surface as provider-path errors without storing raw audio in Postgres

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
