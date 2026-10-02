# Transcript Capture: Current Contracts and Remaining Roadmap

## Status

This document describes implemented transcript-ingestion behavior and clearly labelled remaining work. The previous opening that described capture as a pre-implementation MVP plan was obsolete: whole-file and live-chunked ingestion, queued worker processing, encrypted persistence, working notes, post-consultation dictation, and owner workspace behavior are implemented.

Detailed browser VAD behavior for the live path is in [live_stt.md](live_stt.md). Endpoint request/response details are in [api.md](api.md).

## Security and ownership invariants

- A transcript root belongs to one normal user and one team.
- System-administrator accounts cannot own transcript content.
- Transcript, working-note, dictation, generated-document, redaction, PII, and ingestion-result content remains owner-only.
- Leader or system-administrator provider-management authority does not grant content readability.
- The transcript root remains the retention and deletion root.
- Retention is snapshotted from server-owned team policy; user payloads cannot extend it.
- Expired roots are rejected by content services before asynchronous physical cleanup.
- Provider credentials remain Vault-backed and are never returned to the browser.
- Designated owner content is encrypted before PostgreSQL persistence under the owner's DEK.

Transcript and generated-document titles remain plaintext metadata. Owner-authorized API/browser responses return decrypted content as ordinary response fields and are `no-store`.

## Implemented ingestion modes

The persisted `TranscriptIngestionMode` values are:

- `whole_file`
- `live_chunked`

The browser presents several entry experiences on top of those two backend modes.

### Existing audio file

The user selects an audio file. The backend:

1. applies the configured request and aggregate upload limits;
2. creates or uses the owner transcript root;
3. stores the source audio under a bounded Vault reference for asynchronous processing/retry;
4. creates an ingestion job and durable task-dispatch outbox row in the same database transaction;
5. publishes work immediately when possible, with the one-second Beat publisher as fallback;
6. normalizes audio to 16 kHz mono PCM WAV;
7. enforces normalized duration limits;
8. resolves the snapshotted team STT configuration and credential;
9. calls the provider and appends encrypted result text to the transcript draft;
10. clears or durably queues cleanup for temporary source audio after successful terminal handling; a failed source remains retryable only until its fixed 24-hour deadline.

The browser retains an unaccepted selected file in JavaScript memory for the open tab. It retries a recoverable upload failure twice and then offers an explicit upload retry while that tab remains open. The stable request idempotency key is reused for those attempts, so a replay after a lost response returns the originally accepted job rather than creating another one. The local audio is cleared after acceptance. It is not browser-persisted and is lost on refresh, sign-out, or tab/browser/process closure.

### Microphone batch

The browser records locally and submits one or more whole-file parts. It rolls over before the local WAV approaches server upload/duration limits. Parts stay attached to the transcript UUID captured when recording began, so delayed uploads cannot drift to a newly selected consultation.

Only one whole-file ingestion job is actively processed for a transcript at a time; capture restarts for the same transcript only after that part is accepted. If a rollover part cannot be accepted, its local audio stays available in the originating open tab for automatic or user-triggered re-upload; capture stops instead of recording later audio and creating an undetectable transcript gap.

### Live chunked

The browser uses the pinned same-origin VAD runtime to create speech chunks. Pauses and a maximum chunk duration trigger upload. The backend validates sequence/duration metadata, stores a queued chunk job, and updates the encrypted current draft as worker results complete.

Stopping live capture calls a finalize route. Finalization:

- applies completed chunks;
- moves the transcript out of `recording`;
- leaves it `transcribing` while queued/processing chunks remain;
- reconciles it to `ready` when ingestion completes;
- creates or reuses the version-linked redaction run when the transcript becomes ready and no chunks remain pending.

After live capture stops, the user can create/open another consultation while the prior transcript continues processing. Jobs remain attached to their original transcript root.

## Core API lifecycle

The central routes include:

- `POST /api/v1/transcripts/start`
- owner transcript list/detail/workspace routes under `/api/v1/transcripts`
- whole-file audio upload for a transcript
- live audio-chunk upload for a transcript
- live-capture finalization
- ingestion job status/retry routes
- transcript draft/commit/version routes
- owner working-note, dictation, generated-document, PII, and redaction routes

Use [api.md](api.md) as the endpoint inventory. The route-audit manifest and focused tests must be updated whenever a new `/api/v1` route is introduced.

## Transcript state and job reconciliation

Transcript state is reconciled from durable ingestion work rather than trusted browser state.

- `recording` represents an active capture session.
- `transcribing` represents pending or processing ingestion after capture/upload.
- `ready` represents a transcript with no active ingestion work after reconciliation.
- terminal/failed job metadata remains distinct from transcript text and can be retried only while its bounded source-audio reference exists and the original 24-hour deadline has not passed;
- provider timeouts, connection failures, HTTP 429, and HTTP 5xx receive two durable automatic retries after the initial attempt, due after five then ten seconds; other provider/configuration, credential, quota, invalid-audio, and expiry failures terminalise without automatic retry;
- an owner can manually retry retained failed whole-file or live-chunk audio. A transcript-level retry selects the earliest retained live gap or a failed file; a job-level retry targets a retained failed job. Live retry reuses the original job and sequence so later chunks remain ordered;
- retry transfers the original source reference and deadline; it cannot extend the source-audio lifetime;
- queued, processing or failed work that reaches the deadline is terminalised safely, its dispatch/attempt state is reconciled, and Vault cleanup is durable and repeatable;
- the API and workspace report expiry in safe terms and require a fresh upload.

A processing live chunk older than `LIVE_CHUNK_PROCESSING_STALE_AFTER_SECONDS` can be reconciled as stale. Provider-attempt and quota lifecycle deadlines are separate from Celery task delivery and are documented in [environment.md](environment.md).

Duplicate worker delivery is expected and handled with database claims/idempotency. A worker that loses the claim cannot fail or settle the winning worker's submitted attempt.

## Audio limits and normalization

Default safeguards:

- individual whole-file upload: 200 MiB;
- individual normalized duration: 4 hours;
- whole-file burst: one request per 5 seconds;
- whole-file daily requests: 100;
- hourly whole-file aggregate: 200 MiB and 4 hours;
- live upload rate: one request per second;
- hourly live duration: one hour;
- ffprobe timeout: 15 seconds;
- ffmpeg normalization timeout: 1,800 seconds;
- synchronous STT timeout: 14,400 seconds.

The Docker image includes `ffmpeg` and `ffprobe`. Host development requires both on `PATH`. Exact variable names are in [environment.md](environment.md).

## Team STT policy

System administrators provision STT endpoint metadata and Vault-backed credentials. Team leaders select or clear active STT policies for their own team. Normal users consume only the resolved policy during capture.

OpenScribe snapshots the selected provider/config metadata onto queued work so later policy edits do not silently change an existing job. Runtime validates that the snapshotted config remains usable and resolves its credential before marking the provider attempt submitted.

A definite credential failure before dispatch cancels the quota reservation without audio usage. Provider-call outcomes and accounting remain metadata-only.

See [stt-config.md](stt-config.md).

## Encryption boundary

The following categories are encrypted at rest before PostgreSQL persistence where implemented:

- transcript current draft and committed versions;
- ingestion result text;
- working-note freeform/structured content;
- post-consultation dictation text and segments;
- generated-document request snapshots and output/edit fields;
- generated-document sections;
- redacted output and detected entity values;
- owner-entered manual PII values;
- other designated transcript-derived JSON/text fields.

Service code resolves ownership/retention before decryption. Worker code uses durable owner IDs, not a current browser user. Encryption or Vault failures fail closed rather than falling back to plaintext.

## Working notes

Working note is clinician-authored source content separate from transcript and generated output.

- One mode is active per transcript: `freeform` or `structured`.
- Mode locks on the first non-empty save.
- Clearing the working note removes content and unlocks the mode.
- Clearing it does not delete transcript text or generated outputs.
- It follows transcript-root retention/deletion.
- Generation snapshots the exact working-note input used.
- Working-note text is redacted before an LLM request; generation fails closed if redaction fails.
- Editing a generated note never writes back into the working note.

## Post-consultation dictation

Post-consultation dictation is a separate transcript-owned clinician source, not appended directly to the consultation draft.

- One transcript-owned aggregate can contain immutable STT segments plus an editable combined text.
- If combined text was edited, generation uses that text exactly.
- An intentionally empty edited value suppresses dictation influence rather than falling back to raw segments.
- Otherwise, generation concatenates immutable segments in append order.
- Prompt assembly labels consultation transcript and clinician dictation separately.
- Dictation is redacted before provider dispatch.
- Preview transcription does not create durable dictation rows until the user explicitly saves.
- Dictation-only sessions still use a transcript root and can support generation when consultation transcript text is empty.

## Browser workspace behavior

The canonical Scribe surface is `/workspace`. The browser stores only the active transcript UUID in `sessionStorage` as an untrusted navigation hint; the server repeats owner and retention checks.

Recording lifecycle events disable marked navigation controls and install an unload warning while microphone capture is active. Background transcription or generation does not by itself lock navigation.

Workspace refresh and polling reconcile server state, refresh owner transcript/history data, and enable generation controls when meaningful source content becomes available.

Pending selected-file, microphone-batch, rollover, and live-chunk audio exists only in JavaScript memory for its current tab. Recoverable upload failures (network failures, HTTP 429, and HTTP 5xx) are retried twice with bounded delay; after that, the workspace offers a retry for the pending audio while the originating transcript is active. It also retains the audio for an explicit retry after a non-retryable upload response. Switching away hides the action and switching back restores it. This is not durable recovery: refresh, sign-out, closing the tab/browser, or a browser/process crash loses the pending audio. The separate server-side retained source has the fixed 24-hour limit described below; expiry requires the user to supply audio again.

At 1,200 transcript characters, an owner who has not explicitly disabled template suggestions may claim the transcript's one durable template-suggestion job. The setting is on by default. Disabled users do not create a job or send transcript content to redaction or an LLM. Turning the setting off cancels queued work, its pending dispatch, and its reserved quota; it cannot recall a provider request that has already been submitted. The server stores the first eligible excerpt as owner-encrypted transcript content and dispatches classification through the quota-backed task outbox. The browser submits the selected template ID when it queues the job; the server snapshots its available metadata with the job. The worker redacts the excerpt, including saved manual PII, before sending it to the selected LLM along with the current-template snapshot. The prompt allows a null result when the current template is already a good fit. The browser polls the owner-only job and may offer one accessible template. Dismissal and acceptance remain browser state; accepting uses the ordinary template picker and does not generate a note. See [template-suggestion-preference.md](template-suggestion-preference.md).

## Redaction and PII

A transcript version can have a redaction run containing encrypted redacted text and encrypted detected values. The owner workspace exposes a minimized PII table. Original values require a separate owner-only POST+CSRF reveal path and sensitive responses are `no-store`.

Owners can add/delete manual PII values. Values are encrypted and duplicate detection uses an owner-scoped keyed digest rather than a plain hash. Manual values participate in pre-LLM redaction and post-generation reidentification.

An owner can dismiss a detected PII occurrence as a false positive. A dismissal is an encrypted, reversible owner decision; it never changes the source RedactionRun or RedactionEntity. It applies only when the current redaction can prove the same transcript version, source span, and source text. A changed transcript version never inherits it, and an uncertain rerun remains redacted pending review. Manual PII protections are applied after dismissals and always remain redacted.

The review table groups repeated detected values, including detections assigned different types, and shows their total occurrence count. Each occurrence keeps its own reversible decision and can be located in the current transcript using its verified source position. Detected highlighting uses those positions; it does not mark every matching word. Clinical NLP results have no saved source positions, so they are listed without transcript highlighting. New one-character Clinical NLP results are discarded; older one-character results are excluded from the workspace and optional clinical hints.

The effective redaction view is used when copying the redacted transcript and when assembling later generated-note and consultation-split provider sources. A dismissal changes the consultation-split source fingerprint, so an earlier unconfirmed analysis must be prepared again. It does not change already submitted provider requests, confirmed split batches, or existing generated drafts.

The Transcript panel can copy its current redacted text through an owner-only POST+CSRF response with `no-store`. It uses only the successful encrypted redaction run for the exact current saved transcript version, applies current manual PII protections, and fails closed when the transcript is empty or that run is missing, failed, or stale. It never initiates redaction or falls back to the raw transcript.

## Retention, deletion, and cleanup

- The transcript root owns versions, ingestion and template-suggestion jobs, generated documents, redaction/PII data, working notes, dictation, and related content.
- Content services reject expired roots before the 10-second retention worker physically deletes them.
- Transcript deletion is immediate from the user's perspective and uses relational cascades/service cleanup.
- Temporary source audio and provider secrets use durable cleanup queues with retries and live-reference guards. Failed whole-file and live-chunk retry audio has an absolute maximum lifetime of 24 hours from its original Vault write.
- Queue/outbox rows are terminalized or removed consistently with their source objects.

## Remaining roadmap

The following are not implied merely by the existing capture foundation and should be tracked as focused work when needed:

- additional persisted ingestion-mode values beyond `whole_file` and `live_chunked`;
- richer pause/resume/reconnect semantics across browser/device changes;
- multi-device live session coordination;
- external object-storage architecture for long-lived source audio (current temporary retry storage is Vault-backed and bounded);
- broader semantic quality controls for microphone-batch rollover and partial-provider results;
- any content-sharing/export workflow, which requires a separate authorization/privacy design.

Do not describe implemented whole-file/live capture, encryption, working notes, dictation, or queued processing as future work. Conversely, do not infer the roadmap items above from the current two-mode backend without code, migrations, tests, and operational documentation.
