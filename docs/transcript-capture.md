# Transcript Capture: Current Contracts and Remaining Roadmap

## Status

This document describes implemented transcript-ingestion behavior and clearly labelled remaining work. The previous opening that described capture as a pre-implementation MVP plan was obsolete: whole-file and live-chunked ingestion, queued worker processing, encrypted persistence, working notes, post-consultation dictation, and owner workspace behavior are implemented.

Detailed browser VAD behavior for the live path is in [live_stt.md](live_stt.md). Endpoint request/response details are in [api.md](api.md). For Telehealth's current limitations and remaining release validation, see [the capture backlog](feature_todo.md#telehealth-release-validation-and-deferred-scope); the step-by-step synthetic device and crash-recovery procedure is [the physical walkthrough](telehealth-crash-walkthrough.md).

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

### Telehealth dual-source capture

Browser capture has an explicit source choice in the Telehealth consent dialog. The default requests `getDisplayMedia({video:true,audio:true,windowAudio:'window',systemAudio:'exclude',surfaceSwitching:'exclude'})`; experimental system-audio mode requests `video:{displaySurface:'window'}` and `windowAudio:'system'` while keeping `systemAudio:'exclude'` and `monitorTypeSurfaces:'exclude'`. The latter `systemAudio` hint controls *entire-monitor* sharing, not window audio. System capture may include unrelated applications or monitored microphone audio; neither the selected video window nor a returned live audio track proves which process generated the audio. The requested audio scope (`call` or `system`) is stored as metadata, never as proof of isolation. The server-owned `patient` channel remains a source label, not verified speaker identity. Browser and operating-system support must be validated before clinical rollout. Video is required to access the browser picker, but no video frames are processed, uploaded or stored by OpenScribe; the capture video track stays live until Stop. The browser must return a live audio track and a video track reporting `displaySurface: browser` or `window`; monitor capture is rejected. Window selection requires an additional explicit acknowledgement because the browser may fall back to system-wide audio even with the `windowAudio` hint. Window audio is a **test-only preview**, not proof of WhatsApp-only playback isolation, and native application capture remains subject to platform validation and clinical/privacy release gates. Unavailable audio, denied permissions and ended/muted sources fail or interrupt the capture as before.

The Telehealth receipt shows one Patient audio row regardless of whether the patient-channel audio was obtained from a browser tab, a desktop window, or the experimental system-audio option. The system-audio privacy disclosure is shown during initial source selection and confirmation rather than permanently in the recording or recovery receipt. The server retains the requested capture scope for provenance, but it does not change the clinical channel's Patient label. Clinician RMS activity feeds the existing microphone visualizer, while separate Silero v5 VAD instances continue checking both cloned patient and clinician tracks. Patient speech is shown as detected after 3 seconds of accumulated voice activity; this receipt is informational and does not gate, mute or trim recorder-owned PCM streams. The paragraph below the meter is reserved for warnings and recovery instructions; routine progress stays on the recording control. Meter movement is RMS activity, not evidence of speech.

Browser capture events are sent in bounded batches through the authenticated, owner-scoped, CSRF-protected and per-user-rate-limited `/api/v1/transcripts/{transcript_id}/telehealth/diagnostics` endpoint. The backend validates a closed event schema and writes only controlled stage names, source types, fixed error codes, part indexes, server timestamps and a random diagnostic reference to a dedicated private rotating file (5 MiB active + two backups). No audio, transcript text, user identifiers, root IDs, URLs, window titles or raw errors are stored; the browser does not print this log to its console or offer a file download. A server-configured `TELEHEALTH_DIAGNOSTICS_DEBUG_UI=true` developer view shows recent sanitized events and the correlation reference. It is hidden by default. If file logging is unavailable, diagnostics return zero accepted records and recording remains unaffected; failed deliveries are retried with a bounded browser queue. The Docker file sink is on a dedicated named volume; operational retention/deletion of that volume must be managed separately.

Telehealth is exclusive while selected or while its capture remains unresolved: ordinary recording-mode changes and whole-file upload controls stay unavailable until Telehealth is turned off and recovery is complete. The backend rejects an ordinary file upload when an unresolved Telehealth capture exists for that transcript. Telehealth records the clinician microphone and audio from a user-selected browser tab or (experimental) desktop application window as separate, owner-bound source parts on one transcript root. Stop uploads buffered audio, seals the exact part set, and queues one independently accounted ingestion job per source part. Stop interruption metadata is serialized in the API's channel/reason fields. If a capture track ends or becomes muted, recording follows the same flush-and-stop path and retains accepted audio; the incomplete warning remains until the clinician acknowledges it. Individual results remain encrypted until the complete capture can be assembled, or the owner explicitly accepts available results, with server-owned source labels and validated consultation timing. For Telehealth, the provider's canonical full response text is saved as the source; optional word timing is retained only when it aligns with that complete text. Missing or invalid canonical text fails closed, while unusable timing falls back to the complete untimed text. A Deepgram result with an empty canonical transcript and explicit empty word list is accepted as silence only when configured segments are absent or empty; contradictory or malformed configured segment data fails closed. Clinical turns preserve spoken text; clinician/patient labels are encrypted turn metadata and are added after redaction for display and generation. Coverage notices are separate protected source turns and pass through the same saved-source redaction boundary. Generic microphone speaker IDs do not establish those roles. Older telehealth turns retain their saved source text and version; a role is restored only when the turn scope matches a capture session for the same owner and transcript and a server-owned part confirms the channel. An exact old `Clinician: ` or `Patient: ` prefix is marked for presentation cleanup without rewriting saved text. Provider word ends may extend up to 2 seconds beyond the inspected source-file duration; the mapper clips those ends at the file boundary, while starts must remain inside the file and words crossing disjoint VAD spans remain unmappable. For timed results, adjacent words from the same source are first grouped into readable turns when their gap is at most 1.5 seconds, their timing does not overlap or rewind, and the turn stays within its size bound. The completed source turns are then ordered by consultation start, retaining real overlap between clinician and patient turns. If any part lacks usable timing, the transcript retains source-labelled blocks with words joined into readable text and part order preserved; the system does not infer cross-source chronology. If every part is silent, the capture reports `no_transcribed_speech` without inventing a transcript. The owner can explicitly finish using available results; earlier content remains intact and an empty root does not become eligible for generation.

If stop and sealing succeed but submission to `/transcribe` fails or its response is uncertain, consultation controls can retry submission with the same capture ID. This is separate from retrying a failed audio-part upload. Failed retained source jobs can also be retried without detaching their source role. Before partial acceptance, an expired source fails its linked part and capture while preserving any earlier interruption and acknowledgement; a late result cannot revive an expired attempt. After explicit partial acceptance, successful siblings and later retries are incorporated under the partial recovery rules below.

#### Using available results after a source failure

The owner can confirm **Use available transcript** for a failed submitted capture, or **Resolve without transcript** when the captured audio contains no speech. The server records a durable `partial` outcome and applies only successful, unapplied speech. This is separate from recovering browser windows before submission. It does not discard failed source audio, cancel provider work, refund usage, or extend retention. Per-window retry controls use the same ingestion job, provider snapshot, and original source deadline.

An accepted partial capture no longer blocks editing, generation, another recording, or navigation. Unrelated unresolved work still blocks. The receipt shows current missing clinician/patient intervals. Saved source versions include protected coverage notices before redaction; those notices cannot be edited or deleted as clinical speech. Notices alone do not count as usable clinical content.

Discarded unsubmitted captures do not add coverage notices to later transcript updates. Refreshing current turns removes any notice previously added for a discarded capture; historical versions remain unchanged. Crash recovery does not detect recording in another tab or device. Stop recording in all other tabs and devices before recovery: continuing stops and finalizes the capture, and unflushed speech may be lost.

Successful in-flight siblings and later retries automatically produce a new transcript version exactly once, without another acceptance step. Existing turn text, clinician edits, and deletions remain intact. New timed turns use only evidenced ordering within their capture; untimed recovery remains explicitly identified without invented chronology. Automatic insertion waits while another unresolved source holds the transcript writer boundary, retaining encrypted results for reconciliation. Already generated notes and immutable queued generation snapshots are unchanged; generating from the expanded source is a separate action.

Only the system updates current coverage notices as gaps change. Historical versions retain their original notices and content. Failed or expired intervals remain visible; retry is offered only while the original source is available. No clinical speech is invented when no result is usable.

Before deploying this code, apply all branch migrations with `alembic upgrade head`, including the capture-session, discard-status, partial-status (`a1b2c3d4e5f6`), and requested-audio-scope (`eafc081108b1`) revisions. Downgrading the capture-session schema refuses while any capture-session rows remain; the discard-status revision refuses while discarded rows remain, and the partial-status revision refuses while accepted partial rows remain. Do not rewrite capture states, delete retained audio, or remove provenance to bypass these guards. The feature remains default-off.

The encrypted browser recovery journal coordinates key and window updates across tabs with the Web Locks API. Web Locks support is required: capture fails closed when safe cross-tab coordination is unavailable. The journal keeps its existing limit of eight windows and six hours, including expiry cleanup during the startup readiness check. Recovery resubmits each retained window with its original bytes, timing and idempotency key before deleting it locally, even when matching timing already appears in the server receipt. Conflicting or rejected replays retain the encrypted window. Decrypted replay buffers are cleared on success and failure, including malformed receipts and partial decryption failures.

### Live chunked

For selections other than eligible Deepgram streaming, the browser uses the pinned same-origin VAD runtime to create speech chunks. Pauses and a maximum chunk duration trigger upload. The backend validates sequence/duration metadata, stores a queued chunk job, and updates the encrypted current draft as worker results complete.

Stopping live capture calls a finalize route. Finalization:

- applies completed chunks;
- moves the transcript out of `recording`;
- leaves it `transcribing` while queued/processing chunks remain;
- reconciles it to `ready` when ingestion completes;
- creates or reuses the version-linked redaction run when the transcript becomes ready and no chunks remain pending.

After live capture stops, the user can create/open another consultation while the prior transcript continues processing. Jobs remain attached to their original transcript root.

Eligible Deepgram live capture uses an owner-bound durable stream session and server-side WebSocket relay instead of the prerecorded chunk route. The relay resolves the saved credential before audio dispatch, authorizes unique PCM sample batches before provider writes, and commits encrypted final text by bounded window. Successful-path audio is not routinely stored in Vault. A failed unresolved interval may use the established bounded Vault recovery path; browser-held fallback is offered only when exact server-reported sample bounds/count match sent-but-uncommitted data in tab memory. Unsent audio and pre-roll are excluded. A retry creates a fresh accounted ingestion job/outbox dispatch and retains the gap barrier until applied or discarded. See [live_stt.md](live_stt.md) for the browser pause, pre-roll, checkpoint, and failure states.

## Core API lifecycle

The central routes include:

- `POST /api/v1/transcripts/start`
- owner transcript list/detail/workspace routes under `/api/v1/transcripts`
- whole-file audio upload for a transcript
- live audio-chunk upload for a transcript
- eligible Deepgram live WebSocket stream and explicit stream-recovery/discard routes
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
- Crash-recovery reconciliation selects bounded ready Telehealth captures in oldest-first order; pending candidates are excluded, and candidates that repeatedly fail application are moved behind their eligible peers.
- terminal/failed job metadata remains distinct from transcript text and can be retried only while its bounded source-audio reference exists and the original 24-hour deadline has not passed;
- provider timeouts, connection failures, HTTP 429, and HTTP 5xx receive two durable automatic retries after the initial attempt, due after five then ten seconds; other provider/configuration, credential, quota, invalid-audio, and expiry failures terminalise without automatic retry;
- an owner can manually retry retained failed whole-file or live-chunk audio. A transcript-level retry selects the earliest retained live gap or a failed file; a job-level retry targets a retained failed job. Live retry reuses the original job and sequence so later chunks remain ordered;
- retry transfers the original source reference and deadline; it cannot extend the source-audio lifetime;
- queued, processing or failed work that reaches the deadline is terminalised safely, its dispatch/attempt state is reconciled, and Vault cleanup is durable and repeatable;
- when a handed-off Telehealth source expires before partial acceptance, its ingestion job and linked source part fail together, and the capture/transcript become failed; an earlier interruption and its acknowledgement remain visible, while a clean capture records `source_audio_expired`; the expired source cannot be retried, and a late provider result cannot revive that attempt;
- expiry after partial acceptance preserves the missing interval and durable cleanup without reinstating a global consultation failure; other unresolved recording or ingestion still blocks as usual;
- ordinary ingestion reports expiry in safe terms and requires a fresh upload. Telehealth can instead explicitly use available results, preserving successful source without extending the missing audio's lifetime.

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

Ingestion failures emit `transcript_ingestion_failure` through `openscribe.transcript_ingestion` with a job UUID, controlled stage/error/action and timestamp. An observed failure precedes cleanup; `retry_scheduled` or `terminalized` reports the returned lifecycle state. Ingestion, note-generation and template-suggestion task boundaries log `generation_task_boundary_failed` through `openscribe.tasks` and raise a content-free task error for unexpected failures. The existing note-generation wait/retry path remains intact. The ingestion task replaces Celery's diagnostic kwargs representation before decoding any legacy audio payload; actual task arguments remain available to execution. Current durable dispatch sends identifiers only. These diagnostics exclude exception details, audio, clinical text, provider bodies and credentials.

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

## Diarised transcript sources

Diarised consultation transcripts retain ordered speaker turns as owner-encrypted structured source data associated with immutable transcript versions. Speaker identity is scoped to a provider connection or individual batch recording/upload. Ordinary Deepgram accounting checkpoints preserve that scope; an actual new connection starts another. Speaker numbers remain stable when earlier turns are deleted. They do not establish clinical roles or identify the same person across independent scopes.

The canonical transcript text contains turn text only. Speaker labels, recording headings, timings, and editor controls are rendered separately. Flat-only legacy transcripts retain their existing display. Mixed transcripts preserve earlier and later unattributed text alongside attributed turns. Unknown attribution displays `?`; missing attribution must not discard usable transcription or trigger an extra provider request.

Turn text can be edited after recording, ingestion, and unresolved recovery have finished. Inline edits use the shared note autosave mechanism with revision conflicts rather than overwriting newer source. New text outside a turn is unattributed. Emptying text does not implicitly delete a turn; whole-turn deletion is a separate action. Edited turns retain their original audio interval, which no longer claims exact alignment to the revised words.

Each accepted mutation creates a new immutable source revision. Historical versions and already queued generation retain their existing snapshots and transcript-root lifecycle. Source changes use the existing owning-user encryption, authorization, retention, deletion, redaction, and source-writer boundaries.

Redaction operates on the exact canonical version text. Speaker boundaries passed to generation must be mapped through redaction substitutions; original offsets cannot be applied directly to redacted text. Generation uses saved telehealth channel roles where available. Ordinary diarisation includes anonymous speaker/session context and cautions against confident clinical-role assignment when attribution is uncertain. Working note and dictation remain distinct sources.

## Working notes

Working note is clinician-authored source content separate from transcript and generated output.

- One mode is active per transcript: `freeform` or `structured`.
- Mode locks on the first non-empty save.
- Clearing the working note removes content and unlocks the mode.
- Owner Working note projections include a monotonic revision, including when empty. PATCH/DELETE accept `expected_revision` and `operation_id` as a pair; legacy timestamp-only clients remain supported.
- The server acknowledges an exact replay only for the latest operation while its result remains current. A superseded retry conflicts. This is bounded retry protection, not wall-clock last-write-wins or note history.
- The browser retains an uncertain frozen attempt in page memory and replays it once; an actual conflict preserves edits and blocks dependent actions. An acknowledged clear resets the editor to the returned revision.
- Clearing it does not delete transcript text or generated outputs.
- It follows transcript-root retention/deletion.
- Generation snapshots the exact working-note input used.
- Working-note text is redacted before an LLM request; generation fails closed if redaction fails.
- Editing a generated note never writes back into the working note.
- A combined owner transcript update retains its title or permitted ingestion-mode change while saving a structured Working note.

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
- A saved non-blank dictation, or retained dictation segment, keeps a session non-empty for the New session guard. An explicitly cleared combined value still suppresses generation input, but does not make retained segments disposable.

## Browser workspace behavior

The canonical Scribe surface is `/workspace`. The browser stores only the active transcript UUID in `sessionStorage` as an untrusted navigation hint; the server repeats owner and retention checks. A session-link workspace response is checked again immediately before applying a different consultation, so a recording that starts while navigation is pending keeps the current root selected; blocked full-page fallbacks repeat the same recording check.

Recording lifecycle events disable marked navigation controls and install an unload warning while microphone capture is active. Telehealth source selection happens after its privacy guidance when the user enables Telehealth. Before Start, the browser sharing connection is held only to the selected tab/window: OpenScribe does not request microphone permission, create an audio context/worklet/VAD/timeline, process or retain audio, create a capture session, or consume audio quota. The Patient row shows the connected source. Start transfers that source into recording; if it ends while waiting, its tracks are released and the user must turn Telehealth off and on to select again. Turning Telehealth off, cancelling consent/permission, a failed acknowledgement, changing consultation, or leaving the page releases an unused prepared source. Start does not open another picker. Once recording starts, the established stop, upload, recovery and session lifecycle applies.

Workspace refresh and polling reconcile server state, refresh owner transcript/history data, and enable generation controls when meaningful source content becomes available.

Pending selected-file, microphone-batch, rollover, and live-chunk audio exists only in JavaScript memory for its current tab. Recoverable upload failures (network failures, HTTP 429, and HTTP 5xx) are retried twice with bounded delay; after that, the workspace offers a retry for the pending audio while the originating transcript is active. It also retains the audio for an explicit retry after a non-retryable upload response. Switching away hides the action and switching back restores it. This is not durable recovery: refresh, sign-out, closing the tab/browser, or a browser/process crash loses the pending audio. The separate server-side retained source has the fixed 24-hour limit described below; expiry requires the user to supply audio again.

At 1,200 transcript characters, an owner who has not explicitly disabled template suggestions may claim the transcript's one durable template-suggestion job. The setting is on by default. Disabled users do not create a job or send transcript content to redaction or an LLM. Turning the setting off cancels queued work, its pending dispatch, and its reserved quota; it cannot recall a provider request that has already been submitted. The server stores the first eligible excerpt as owner-encrypted transcript content and dispatches classification through the quota-backed task outbox. The browser submits the selected template ID when it queues the job; the server snapshots its available metadata with the job. The worker redacts the excerpt, including saved manual PII, before sending it to the selected LLM along with the current-template snapshot. The prompt allows a null result when the current template is already a good fit. The browser polls the owner-only job and may offer one accessible template. Dismissal and acceptance remain browser state; accepting uses the ordinary template picker and does not generate a note. See [template-suggestion-preference.md](template-suggestion-preference.md).

## Redaction and PII

A transcript version can have a redaction run containing encrypted redacted text and encrypted detected values. The owner workspace exposes a minimized PII table. Original values require a separate owner-only POST+CSRF reveal path and sensitive responses are `no-store`.

Owners can add/delete manual PII values. Values are encrypted and duplicate detection uses an owner-scoped keyed digest rather than a plain hash. Manual values participate in pre-LLM redaction and post-generation reidentification.

An owner can dismiss a detected PII occurrence as a false positive. A dismissal is an encrypted, reversible owner decision; it never changes the source RedactionRun or RedactionEntity. It applies only when the current redaction can prove the same transcript version, source span, and source text. A changed transcript version never inherits it, and an uncertain rerun remains redacted pending review. Manual PII protections are applied after dismissals and always remain redacted.

The review table groups repeated detected values, including detections assigned different types, and shows their total occurrence count. Each occurrence keeps its own reversible decision and can be located in the current transcript using its verified source position. Detected highlighting uses those positions; it does not mark every matching word. Clinical NLP results have no saved source positions, so they are listed without transcript highlighting. New one-character Clinical NLP results are discarded; older one-character results are excluded from the workspace and optional clinical hints.

The effective redaction view is used when copying the redacted transcript and when assembling later generated-note and consultation-split provider sources. A dismissal changes the current source fingerprint, so a pending suggestion or not-yet-submitted Create must use a fresh source snapshot. It does not clear or rewrite the living problem plan, change an already submitted provider request or confirmed batch, or change existing generated drafts.

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

Live post-consultation dictation uses its own selected Deepgram provider and a transient browser preview. It does not write to the conversation transcript or persist dictation until the user saves it. Audio usage is metered even if the user discards the preview.
