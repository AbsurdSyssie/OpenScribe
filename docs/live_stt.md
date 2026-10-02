# Live STT

This document describes the implemented browser and backend behavior for `live_chunked` transcription. General ingestion, retention, encryption, and queue contracts are in [transcript-capture.md](transcript-capture.md).

## Implemented behavior

While live recording is active:

- the browser captures microphone audio through the pinned, same-origin `@ricky0123/vad-web` runtime;
- Silero VAD model `v5` detects speech boundaries;
- `preSpeechPadMs=800` retains speech onset;
- `redemptionMs=2000` waits through short pauses before ending a segment;
- OpenScribe trims approximately the final `1000ms` of a normal pause-ended segment before upload;
- `submitUserSpeechOnPause=true` flushes current speech when recording stops/pauses;
- continuous speech is force-flushed around 30 seconds;
- a forced flush uploads at most the latest 30 seconds and carries approximately 0.8 seconds of overlap into the next segment;
- every upload uses a strictly increasing `chunk_sequence_no` and declared duration;
- chunks are converted to mono 16 kHz PCM WAV and sent to the owner-only audio-chunk route;
- the workspace polls while capture/processing is active so applied text appears without a manual refresh.

The exact boundaries are a practical VAD approximation, not a lossless continuous streaming protocol.

## Browser states

The controller moves through these conceptual states:

1. `idle`: no microphone capture;
2. `arming`: permission, local VAD assets, and `MicVAD` initialize;
3. `listening`: microphone/VAD active, no current speech segment;
4. `speech_active`: VAD buffers the current segment and the forced-flush timer runs;
5. `speech_tail`: the VAD waits through the configured silence/redemption window;
6. `flushing`: audio is normalized in-browser to the expected WAV shape and queued for upload;
7. `stopped`: local capture ends and the backend is finalized/reconciled.

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

When the document is backgrounded, OpenScribe pauses/flushes local VAD before browser timer throttling can delay chunking. On unload, it stops local microphone state and sends a best-effort keepalive finalize request. This improves reconciliation but is not a guarantee against abrupt browser/process/network loss.

The inactivity prompt does not upload/finalize/stop capture. Dismissal suppresses only the current silent interval; later detected speech re-arms the timer.

## Whole-file microphone batch

The whole-file recording mode also uses local VAD gating, but it collects voiced segments into a local WAV batch and uploads through the whole-file path on stop/rollover. It does not use live chunk sequence numbers.

Do not conflate whole-file microphone batch with `live_chunked`; they have different queue, retry, and rate-limit behavior.

## Failure handling

On live upload failure, the open tab retains the unaccepted chunk in JavaScript memory. Network failures, HTTP 429, and HTTP 5xx receive two browser retries, using `Retry-After` capped at ten seconds when provided or bounded two/four-second fallback delays. On exhaustion, and after a non-retryable upload response, the workspace offers a retry for that audio while the originating transcript is active. The same stable idempotency key is used throughout.

The pending chunk is not stored in Web Storage, IndexedDB, a URL, logs, or audit metadata. Refresh, sign-out, tab/browser closure, or a browser/process crash loses it and can leave a gap. Server-retained source for an accepted failed job is separate, lasts no longer than 24 hours from its original write, and enables owner manual retry only until that deadline. The UI must not imply guaranteed lossless capture.

## Privacy and security

- Only the transcript owner can submit/view live chunks/results.
- Team leader/system-admin metadata authority does not grant content access.
- Provider credentials remain Vault-backed and never enter browser payloads.
- Provider/task/audit metadata excludes transcript text and raw audio.
- Result text is encrypted before persistence under the owner's content key.
- Transcript-root retention/deletion owns the chunk jobs and derived content.
- Runtime assets remain pinned and same-origin under CSP.

## Tradeoffs and remaining improvements

Known tradeoffs:

- a two-second silence window can feel slow in rapid conversation;
- forced 30-second boundaries can cut a sentence and introduce overlap duplication;
- VAD accuracy depends on microphone/environment/model behavior;
- background/unload finalization is best-effort;
- pending unaccepted audio is limited to current-tab memory and does not survive refresh or browser loss.

Potential follow-up work:

- tune timing/overlap with controlled clinical-environment testing;
- select lower-energy cut points near the forced boundary;
- improve reconnect/multi-device session handling;
- expose safer diagnostics for missing sequence/gap conditions without logging content.

The activity visualizer is implemented and is not future work.
