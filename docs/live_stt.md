# Live STT Concept

This document defines the browser and backend concept for OpenScribe live chunked transcription.

The goal is to make live transcription feel responsive without weakening the current privacy, ownership, and transcript-root rules.

## Target behavior

For a `live_chunked` session:

- the browser captures microphone audio continuously while live recording is active
- client-side voice activity detection decides chunk boundaries using `@ricky0123/vad-web`
- the browser waits for `2.0s` of silence before ending a speech chunk
- the browser trims the final `1.0s` of silence from a normal speech-ended chunk before upload
- the browser keeps `0.8s` of pre-roll before detected speech so the beginning of utterances is not clipped
- if speech continues without a long enough silence, the browser forces a chunk flush at `30s`
- after a forced `30s` flush, the browser uploads the latest `30s` of buffered speech and carries a `0.8s` overlap into the next segment
- each uploaded chunk gets a strictly increasing `chunk_sequence_no`

This produces a practical approximation of:

- `2s` silence endpointing
- `1s` post-roll after speech ends
- `0.8s` pre-roll before speech restarts
- `30s` maximum continuous chunk length

## Why this design

The design balances four competing needs:

- responsiveness: chunks should appear quickly enough to feel live
- STT quality: chunk starts and stops should not clip words
- bandwidth: silence should not dominate uploads
- sequencing safety: the backend must still apply chunks in order

## Non-goals

This slice does not try to:

- do diarisation in the browser
- stream token-by-token STT results from the provider
- redesign transcript ownership or admin visibility
- persist raw live audio after successful chunk application
- provide a perfect acoustic VAD under all room-noise conditions

## Browser state machine

The browser should treat live recording as a state machine:

1. `idle`
   - no active microphone capture
   - no active speech segment

2. `arming`
   - microphone permission granted
   - the Silero browser VAD runtime is initialized
   - the microphone stream is claimed by `MicVAD`

3. `listening`
   - `MicVAD` listens for speech onset
   - no chunk upload is attempted yet

4. `speech_active`
   - once speech is detected, `MicVAD` starts buffering a speech segment
   - `preSpeechPadMs=800` keeps the start of the utterance intact
   - the browser arms a `30s` forced-flush timer

5. `speech_tail`
   - after speech stops, `MicVAD` waits for `redemptionMs=2000`
   - when the callback fires, OpenScribe trims the last `1000ms` before upload so the chunk does not carry a full `2s` silence tail
   - if speech resumes before the silence threshold completes, the chunk remains open

6. `flushing`
   - the browser converts the `Float32Array` speech segment returned by `MicVAD` into mono `16kHz` PCM WAV
   - if the forced-flush segment runs slightly beyond `30s`, OpenScribe caps the upload to the latest `30s` so the API contract is still valid
   - OpenScribe keeps the final `0.8s` of that forced-flush segment and prepends it to the next live segment as overlap
   - it uploads to `/api/v1/transcripts/{transcript_id}/audio-chunks`
   - it includes:
     - `chunk_sequence_no`
     - `declared_duration_seconds`
     - the chunk audio file

7. `stopped`
   - live capture ends cleanly
   - `submitUserSpeechOnPause=true` flushes the current speech segment when the user presses stop

## Timing and buffering

Recommended initial constants:

- `MicVAD` model: `v5`
- speech pre-roll: `800ms`
- silence threshold to end chunk: `2000ms`
- trailing silence trimmed after a normal pause: `1000ms`
- forced chunk flush: `30000ms`
- minimum speech duration before sending: `400ms`
- minimum spacing between live chunk upload attempts: `1100ms`
- retry delay after a live chunk `429`: `1200ms`

These values are intentionally conservative and easy to adjust after real clinical testing.

## VAD approach

The browser implementation should use:

- `@ricky0123/vad-web`
- `MicVAD.new(...)`
- `preSpeechPadMs=800`
- `redemptionMs=2000`
- `submitUserSpeechOnPause=true`
- the vendor's Silero `v5` model

Current asset loading is the pinned browser quick-start path from the official docs:

- `onnxruntime-web@1.22.0`
- `@ricky0123/vad-web@0.0.29`

This keeps the page server-rendered while still moving VAD quality above the hand-rolled RMS implementation.

The pinned self-hosted `onnxruntime-web` asset set must include both threaded WASM binaries and matching threaded module loaders so `MicVAD.new(...)` can initialize across browser worker/thread paths:

- `ort.wasm.min.js`
- `ort-wasm-simd-threaded.wasm`
- `ort-wasm-simd-threaded.jsep.wasm`
- `ort-wasm-simd-threaded.mjs`
- `ort-wasm-simd-threaded.jsep.mjs`

Known weakness:

- `MicVAD` does not natively provide the exact OpenScribe `30s` forced mid-speech split behavior

Mitigation:

- OpenScribe wraps `MicVAD` with a `30s` timer
- when the timer trips, the page pauses the current `MicVAD` instance, uploads the current segment, then reinitializes `MicVAD` and resumes listening
- the restart path keeps live capture active instead of dropping back to idle/stop mode
- this is a practical approximation, but it can still cut awkwardly at the forced boundary

## Chunk sequencing and resume safety

The backend is already sequence-aware for `live_chunked` jobs.

The browser therefore needs the current next upload sequence number when a workspace hydrates or refreshes. The active transcript detail now exposes:

- `next_live_chunk_sequence_no_upload`

The browser uses that value as the authoritative next `chunk_sequence_no` instead of guessing from local state.

This preserves:

- ordered application after refresh
- safe continuation after page reload
- duplicate rejection when the client misbehaves

## Backend interaction

The live browser path uses existing owner-only routes:

- `POST /api/v1/transcripts/start`
- `PATCH /api/v1/transcripts/{transcript_id}`
- `POST /api/v1/transcripts/{transcript_id}/audio-chunks`
- `GET /api/v1/transcribe/workspace`

The live chunk upload route is rate-limited to `1 request/second` per authenticated user/session bucket.
Live chunk queueing also enforces a rolling hourly declared-audio budget per authenticated owner, defaulting to `3600` uploaded seconds per hour via `LIVE_CHUNK_HOURLY_DURATION_LIMIT_SECONDS`.
Each queued live chunk now persists `source_audio_size_bytes` and `declared_duration_seconds` for later usage reporting.
The browser paces live uploads so request starts are at least `1100ms` apart and retries a `429` response with the same `chunk_sequence_no` before surfacing failure.

No new transcript-content visibility is introduced.

## UI behavior

The transcribe workspace should expose:

- `New Consultation`
- `New Live Session`
- `Record` / `Stop` control that changes behavior based on ingestion mode

For `whole_file` sessions:

- file upload remains available
- microphone batch capture now uses local `MicVAD` gating so browser keeps voiced segments with short pre/post buffer, then uploads one WAV batch on stop

For `live_chunked` sessions:

- the same primary record control becomes live capture
- mic activity visualizer beside record control uses current `MicVAD` frame stream for bar motion and flips red/green from current VAD speech state
- the workspace keeps polling while live capture is active so newly applied transcript text appears without a manual refresh
- when the tab is backgrounded during active speech, the browser pauses `MicVAD` to flush the current segment before background timer throttling can delay the forced split
- status copy changes to live-specific text:
  - `Listening for speech...`
  - `Speech detected. Building live chunk...`
  - `Sending live chunk 3...`
  - `Thirty seconds of speech reached. Sending the current live chunk...`
  - `Thirty second speech window reached. Sending the latest 30 seconds and keeping a 0.8 second overlap for the next live chunk...`
  - `Background transcription is in progress.`

## Failure handling

If a live chunk upload fails:

- transient route-level `429` responses are retried briefly with the same sequence number
- the browser should stop active capture
- the UI should surface the error clearly
- the transcript remains owner-only and in its existing backend state

This slice does not add per-chunk retry persistence like whole-file upload retry. For live mode, the practical fallback is:

- show the failure
- let the user restart live capture

The backend already protects against:

- duplicate sequence numbers
- wrong ingestion mode
- non-owner access

## Privacy and security requirements

Must remain true:

- only the owning user may upload live chunks for the transcript
- team leaders and system admins do not gain transcript readability from live STT
- provider secrets remain Vault-backed and are never exposed to the browser
- transcript-root deletion still deletes transcript-derived children
- no transcript text or provider secret may be logged

## Criticisms and tradeoffs

This exact design is reasonable, but there are caveats:

- `2s` silence can feel a bit slow for quick back-and-forth speech
- a hard `30s` flush can cut mid-sentence
- the current implementation depends on pinned vendored browser assets staying complete and version-matched

Why still use it:

- it is predictable
- it is implementable in a server-rendered app without a frontend bundler migration
- it replaces the weaker custom RMS loop with a maintained browser VAD package
- it matches the existing sequence-aware ingestion backend

## Future improvements

Potential follow-ups after this first live slice:

- continue tuning the forced-flush overlap window against real microphone and STT behavior
- smarter low-energy cut-point search near the `30s` cap
- visual live waveform / speech activity indicator
- per-chunk retry queue if live reliability becomes a problem
