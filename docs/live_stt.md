# Live STT

This document describes the implemented browser and backend behavior for `live_chunked` transcription. General ingestion, retention, encryption, and queue contracts are in [transcript-capture.md](transcript-capture.md).

## Implemented behavior

The browser uses the pinned, same-origin `@ricky0123/vad-web` runtime with Silero VAD model `v5`. The capture behavior depends on the server-selected STT configuration:

- eligible Deepgram `live_chunked` sessions use the server-owned streaming path described below;
- other live STT selections continue to use the existing VAD chunk-upload path.

For the existing chunk-upload path:

- `preSpeechPadMs=800` retains speech onset;
- `redemptionMs=2000` waits through short pauses before ending a segment;
- OpenScribe trims approximately the final `1000ms` of a normal pause-ended segment before upload;
- `submitUserSpeechOnPause=true` flushes current speech when recording stops/pauses;
- continuous speech is force-flushed around 30 seconds;
- a forced flush uploads at most the latest 30 seconds and carries approximately 0.8 seconds of overlap into the next segment;
- every upload uses a strictly increasing `chunk_sequence_no` and declared duration;
- chunks are converted to mono 16 kHz PCM WAV and sent to the owner-only audio-chunk route;
- the workspace polls while capture/processing is active so applied text appears without a manual refresh.

VAD boundaries are practical approximations, not a lossless continuous stream.

## Deepgram live streaming

The workspace exposes streaming only when its server-derived capability says the active owner transcript is `live_chunked` and the saved eligible selection is Deepgram. The browser cannot grant itself this capability. The server independently verifies the owner, active transcript, frozen selection, Vault credential and same-origin/session/CSRF handshake before accepting audio. Credentials stay server-side.

The existing single microphone/VAD pipeline converts captured 16 kHz mono audio to signed 16-bit little-endian PCM. It initially listens locally and retains a five-second rolling pre-roll in JavaScript memory. On speech, it sends the unsent ring samples once and then new samples over the same-origin WebSocket. Frames carry monotonic source sample offsets; offsets can skip locally discarded audio, but sent intervals never overlap. The VAD speech-end copy is not uploaded a second time.

When the user starts a streaming capture, the browser prepares the local VAD model while the server WebSocket handshake runs. It requests microphone access only after the server reports the stream ready, and reports `Listening` or starts the recording timer after asynchronous microphone/VAD startup completes. Browser permission prompts, model cache state, and audio-device initialization affect startup time; the UI does not promise a fixed start latency. Canceling during initialization invalidates the pending capture, and a microphone stream returned after cancellation has its tracks stopped.

In this streaming branch, OpenScribe also bounds the pinned VAD runtime's internal speech-frame accumulator to approximately five seconds before each new frame is appended. The VAD's speech and silence counters continue across that trim; the existing chunk-upload branch is unchanged.

After 30 seconds without a voiced frame, the browser stops sending audio and continues microphone/VAD capture locally. Its bounded ten-second outbound queue remains available while an accounting checkpoint is in flight; the five-second pre-roll ring is unchanged. On speech resumption, only unsent ring samples are sent before new frames. The Deepgram provider socket stays open through silence, pause/resume, and ordinary accounting checkpoints; server-side KeepAlive maintains it while no audio is sent. A routine checkpoint uses `Finalize` and reuses the socket after a matching, positive-duration `from_finalize` final result; its text may be empty. If final results already cover the frozen audio interval before `Finalize`, that coverage proves completion without creating acknowledgement debt. If the flush cannot be proven within two seconds, the relay exceptionally sends `CloseStream`, drains through Metadata within two seconds, commits the exact window, and opens a fresh provider socket only when later authorized audio arrives. A failed drain uses the existing manual recovery path and never replays uncertain audio. KeepAlive does not consume audio quota; only unique forwarded samples do.

The relay permits at most one audio batch in flight. A `received` message grants transport credit after durable authorization and provider write; it does not mean transcript text is committed. `window_committed` follows the encrypted transcript transaction. Interim text is temporary UI state and is cleared at each committed checkpoint. Provider windows contain at most 30 seconds of forwarded audio. Provider result timestamps use one cumulative clock per socket; ordinary checkpoints do not reset it, while an exceptional fallback starts a new clock base at the cumulative sample count.

The browser keeps sent-but-uncommitted audio in bounded tab memory and maintains a separate five-second pre-roll ring. The server-retained source is preferred for recovery. A browser-held fallback is offered only when server-provided source bounds and sample count exactly match the sent-but-uncommitted samples in memory; the server also checks each recovered PCM batch against its persisted owner-keyed digest before accepting it. Unsent outbound and pre-roll samples are never included. Successful audio is discarded after final text commit and is not routinely written to Vault. Retry is never automatic: it uses the dedicated owner route, a fresh accounted ingestion attempt and existing durable outbox; acceptance returns a job to poll, not completed transcript text. If the exact interval is unavailable, the workspace offers explicit discard rather than silently truncating or substituting audio. Refresh, sign-out, tab/browser closure or process loss can remove uncommitted tab audio; the workspace exposes only safe gap identifiers, sample bounds and recovery state. Committed text remains. Unresolved intervals block later live ingestion until recovered or explicitly discarded.

Note generation stays blocked while a stream session is active, finalizing, failed, or recovering; it becomes available after stream completion, successful recovery, or explicit discard.

Stop waits for a bounded provider final drain and durable final-window commit. Backgrounding keeps the capture active while the tab remains open. Page unload performs a best-effort finalize and is not a guarantee against abrupt process/network loss.

All forwarded audio, including silence and resumed pre-roll, is quota-bearing. The server reserves against measured samples before dispatch and settles a successfully drained checkpoint using its exact sample count, rounded to audio-second units once per window. Explicit recovery creates a new paid provider attempt; the capture-hourly safeguard counts the original source interval once. Uncertain submitted audio settles conservatively against its durable authorization.

Crossing a UTC quota day/month while audio remains pending forces a drain and checkpoint before new-period audio is authorized. Completed reservations settle before new-period admission; the existing rule that unfinished reservations remain committed across quota resets is unchanged.

Each relay operation revalidates the full user session and owning transcript. Database transactions are short-lived. The relay reuses the established owning-user content-key cache only within one checkpoint. Cache use expires after 30 wall-clock seconds and is checked before each operation; checkpoint and connection cleanup clear it. This avoids a Vault key unwrap for every audio batch without introducing a global key cache.

## Speaker continuity

When enabled at capture start, Deepgram speaker metadata is retained as structured transcript turns. Ordinary checkpoints and pause/resume keep the same speaker scope while the provider connection remains open. An exceptional connection replacement starts a new scope; equal speaker numbers on either side do not establish the same person. The workspace and generation source retain these boundaries. Unknown speaker information preserves text as unattributed. Custom live chunking does not support diarisation; dictation never enables it.

## Live diagnostics

Each accepted Deepgram recording emits content-free JSON lifecycle records to the FastAPI stdout stream. The random `diagnostic_id` correlates one recording's connection, authorized media dispatch, provider receive, checkpoint, durable commit, browser acknowledgement, recovery, and terminal outcome. A `recording_failed` record reports the active phase, safe failure category, and last completed stage. Window counters are aggregated; the relay does not emit per-frame or per-result records. Successful KeepAlive counts appear with the next checkpoint or terminal summary, including after a long silent pause.

Under `start-dev.sh`, the FastAPI process also writes the same records to `.local/logs/deepgram-live.jsonl` by default. Use `tail -n 200 .local/logs/deepgram-live.jsonl` to inspect recent records, or search one recording with `rg '"diagnostic_id":"<id>"' .local/logs/deepgram-live.jsonl`. The owner-only sink retains a 5 MiB active file and two backups (15 MiB total). It is local troubleshooting metadata, not clinical record storage. If the file sink cannot be created or written, a fixed warning appears and stdout diagnostics continue without interrupting recording.

Docker emits these JSON records to the `openscribe` container's stdout. Retrieve them with `docker compose logs --since=1h --no-color openscribe`; use the deployment's Compose project and configured Docker logging retention. The application does not create a separate diagnostic file or configure Docker-wide log rotation. Share only the random diagnostic ID for support after applying normal local handling to logs.

## Browser states

The existing chunk-upload controller moves through these conceptual states:

1. `idle`: no microphone capture;
2. `arming`: permission, local VAD assets, and `MicVAD` initialize;
3. `listening`: microphone/VAD active, no current speech segment;
4. `speech_active`: VAD buffers the current segment and the forced-flush timer runs;
5. `speech_tail`: the VAD waits through the configured silence/redemption window;
6. `flushing`: audio is normalized in-browser to the expected WAV shape and queued for upload;
7. `stopped`: local capture ends and the backend is finalized/reconciled.

The Deepgram streaming branch additionally reports `connecting`, `streaming`, `listening` (local VAD with audio paused), `finalizing`, and `failed` states. A failed stream stops capture and requires an explicit recovery or discard action.

The current constants include:

- pre-roll: 800 ms;
- silence/redemption window: 2,000 ms;
- normal trailing-silence trim: 1,000 ms;
- forced continuous-speech flush: 30,000 ms;
- minimum speech before upload: 400 ms;
- minimum spacing between live upload attempts: 1,100 ms;
- route-level rate-limit retry fallback: 1,200 ms, preferring server `Retry-After`.

These browser constants are implementation details and should be changed only with focused browser/audio/provider testing.

## Vendored runtime assets

OpenScribe serves the pinned VAD/ONNX runtime from the application origin:

- `onnxruntime-web@1.22.0`;
- `@ricky0123/vad-web@0.0.29`;
- `ort.wasm.min.js`;
- matching threaded SIMD/JSEP WASM and module-loader files.

The files must remain version-matched and complete. Production must not switch them to public-CDN runtime loading because the application CSP and dependency policy require same-origin assets.

## Forced-flush behavior

`MicVAD` does not natively expose OpenScribe's exact forced mid-speech split. OpenScribe wraps it with a timer:

- pause the current VAD instance at the boundary;
- obtain/limit/upload the current buffered segment;
- preserve the configured overlap;
- reinitialize/resume VAD without dropping the overall recording UI to idle.

A hard boundary can still cut awkwardly or duplicate a small overlap. Provider/transcript review remains required.

## Sequencing and refresh safety

The backend is sequence-aware. Transcript workspace data exposes `next_live_chunk_sequence_no_upload`; the browser treats it as authoritative after hydration/refresh rather than relying only on local counters.

This supports:

- ordered append after refresh;
- safe continuation when the page reloads and the backend has accepted earlier chunks;
- duplicate-sequence rejection;
- owner/mode validation at every upload.

The sequence contract belongs to the transcript root. A chunk cannot be redirected to a new consultation merely because the UI selection changes later.

Each pending chunk keeps one idempotency key for its automatic and user-triggered replays. An exact replay returns the accepted job; reuse with a different chunk sequence, ingestion kind, or byte length is rejected. This resolves a lost response without applying the chunk twice.

## Routes and backend lifecycle

The browser uses owner-authorized routes including:

- `POST /api/v1/transcripts/start`;
- transcript update/workspace hydration routes;
- `POST /api/v1/transcripts/{transcript_id}/audio-chunks`;
- live-capture finalization;
- owner workspace polling/status routes.

Each accepted chunk creates durable ingestion metadata and queued work. The provider worker resolves the snapshotted STT configuration/credential, processes audio, and applies encrypted result text in sequence.

After an eligible provider failure, the initial attempt has two durable automatic retries, due after five then ten seconds. Eligible failures are provider timeout/connection failures and provider HTTP 429/5xx responses. Invalid audio, authorization/configuration/credential, quota, source-expiry, and other failures do not retry automatically. A failed chunk can be manually retried through the owner retry route while its retained source remains available; it reuses the original job and sequence number.

Finalization moves the transcript out of active recording. It remains transcribing while queued/processing chunks exist and reconciles to ready when work completes. Already uploaded chunks can continue processing after the user opens another consultation.

## Rate limits and quotas

Defaults:

- route rate limit: `LIVE_CHUNK_UPLOAD_RATE_LIMIT=1/second`;
- rolling hourly duration safeguard: `LIVE_CHUNK_HOURLY_DURATION_LIMIT_SECONDS=3600`.

The browser spaces upload starts by at least about 1.1 seconds. It retries only a structured route-level `rate_limited` response, uses the same sequence number, and honors `Retry-After` where present.

Quota decisions are authoritative and separate from the route limiter. Public quota failures use a bounded `quota_exceeded` response without exposing allowance/usage internals. They are not retried automatically.

Each job records bounded source byte/duration metadata for usage accounting. It does not persist raw audio in accounting/audit rows.

## Workspace behavior

The Scribe workspace provides:

- new consultation/live-session entry points;
- mode-aware Record/Stop controls;
- a microphone activity visualizer driven by current VAD frames;
- speech-state visual indication;
- live-specific status messages;
- polling while capture/processing is active;
- recording navigation lock and unload warning;
- a non-blocking inactivity prompt after prolonged VAD silence;
- a prompt to choose the current or a new consultation when recording into an older ready consultation with existing content.

For other-provider chunk capture, backgrounding flushes local VAD before browser timer throttling can delay chunking. For Deepgram, backgrounding leaves the local VAD and stream active while the tab remains open. On unload, both paths stop local microphone state and attempt best-effort finalization; this is not a guarantee against abrupt browser/process/network loss.

The inactivity prompt does not upload/finalize/stop capture. Dismissal suppresses only the current silent interval; later detected speech re-arms the timer.

## Whole-file microphone batch

The whole-file recording mode also uses local VAD gating, but it collects voiced segments into a local WAV batch and uploads through the whole-file path on stop/rollover. It does not use live chunk sequence numbers.

Do not conflate whole-file microphone batch with `live_chunked`; they have different queue, retry, and rate-limit behavior.

## Failure handling

On live upload failure, the open tab retains the unaccepted chunk in JavaScript memory. Network failures, HTTP 429, and HTTP 5xx receive two browser retries, using `Retry-After` capped at ten seconds when provided or bounded two/four-second fallback delays. On exhaustion, and after a non-retryable upload response, the workspace offers a retry for that audio while the originating transcript is active. The same stable idempotency key is used throughout.

Deepgram streaming failures use a different recovery contract: stop capture and require an explicit retry or discard. A no-audio retry uses the server-retained failed interval when available. Browser-held fallback audio is eligible only when it exactly matches the server-reported failed interval; unsent batches and pre-roll are excluded. A retry polls the returned ingestion job and does not automatically replay provider audio or reuse the stream's submitted attempt.

All allowlisted Deepgram live diagnostic records are emitted at INFO level as one-line JSON. Before the durable session starts, phase records can identify failures in origin, owner, handshake, and preflight checks without a recording ID. After session start, a `recording_failed` record includes the active phase, safe lifecycle failure, provider category, WebSocket close class, last completed stage, and bounded counters under the random `diagnostic_id`. Exception text, provider bodies, transcript text, and application identifiers are omitted. Existing WebSocket response codes and recovery behavior remain in place; an expired final-drain wait reports `stream_finalization_failed`.

The pending chunk is not stored in Web Storage, IndexedDB, a URL, logs, or audit metadata. Refresh, sign-out, tab/browser closure, or a browser/process crash loses it and can leave a gap. Server-retained source for an accepted failed job is separate, lasts no longer than 24 hours from its original write, and enables owner manual retry only until that deadline. The UI must not imply guaranteed lossless capture.

## Privacy and security

- Only the transcript owner can submit/view live chunks/results.
- Team leader/system-admin metadata authority does not grant content access.
- Provider credentials remain Vault-backed and never enter browser payloads.
- Provider/task/audit metadata excludes transcript text and raw audio.
- Successful Deepgram stream audio is not routinely retained; the browser's bounded recovery and pre-roll audio are volatile JavaScript memory, never Web Storage.
- Result text is encrypted before persistence under the owner's content key.
- Transcript-root retention/deletion owns the chunk jobs and derived content.
- Runtime assets remain pinned and same-origin under CSP.

## Tradeoffs and remaining improvements

Known tradeoffs:

- a two-second silence window can feel slow in rapid conversation;
- forced 30-second boundaries can cut a sentence and introduce overlap duplication;
- VAD accuracy depends on microphone/environment/model behavior;
- background/unload finalization is best-effort;
- pending upload/stream recovery audio is limited to current-tab memory and does not survive refresh or browser loss;
- routine Deepgram checkpoints and pauses keep the provider socket open; explicit Stop drains and closes it once, while an exceptional unproven checkpoint can require a fresh socket after a successful drain.

Potential follow-up work:

- tune timing/overlap with controlled clinical-environment testing;
- select lower-energy cut points near the forced boundary;
- improve reconnect/multi-device session handling;
- expose safer diagnostics for missing sequence/gap conditions without logging content.

The activity visualizer is implemented and is not future work.
