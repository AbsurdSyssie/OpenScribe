# Telehealth dual-channel capture — implementation and decision plan

**Status:** DRAFT — product and architecture decisions outstanding; do not treat this file as authorisation to implement.
**Branch:** feature/telehealth-dual-channel-capture (branched from master at bf63c2879dd8526db582a9598663db92df13642b on 2026-11-04).
**Owner:** OpenScribe maintainers. **Scope:** One clinician-owned consultation in OpenScribe Scribe, with local microphone and remote-call/tab audio kept separate for transcription and mapped into one ordered structured transcript.
**Plan authority:** This document was requested explicitly in the repository root. Repository AGENTS.md normally prefers plans/; the root placement is intentional.
**Source of truth:** Reconcile this draft with the current implementation, tests, migrations, and operational documentation before any code change. All TBD items below need explicit maintainer decisions. Do not infer approval from recommendations.

## 0. Established intent from maintainer discussion

The following are user-stated requirements, not open proposals:

- Capture two *independent* sources: clinician/local microphone and patient/remote tab or call audio, rather than mixing sources into one monophonic STT input.
- Send each source separately through the existing STT/ASR provider workflow, then merge recognized speech into a single chronological transcript.
- Give turns source-derived labels (initially "Clinician" and "Patient"/remote side) and reuse OpenScribe's structured speaker-turn presentation/editing rather than introducing a separate transcript editor.
- Use the existing recording-mode dropdown/split-button in the Scribe workspace.
- Maintain a single **original consultation timeline** even though each channel's VAD removes different amounts of silence. Timestamp frames **before VAD**; never use file creation time, VAD callback time, or concatenated voiced-audio duration as the shared time coordinate.
- Meter the two capture sources separately and preserve accurate STT audio usage/quota accounting.

**Not yet agreed:** exact v1 browser/OS support, whether to start with recorded batch or live incremental notes, source-role naming/verification, provider timestamp support strategy, partial failure policy, UI confirmation/recovery, speaker diarisation within each channel, quota semantics and retention choices. The answer to these questions materially affects architecture.

## 1. Baseline findings, verified against master at branch point

- **UI:** app/templates/transcribe/_workspace.html has the record split-button and dropdown with "Recorded upload" and "Live capture" options; app/static/js/transcribe/app.js switches ingestion mode. The backend enum TranscriptIngestionMode in app/models.py has only whole_file and live_chunked. Prefer adding a capture-source/workflow choice without silently repurposing ingestion-mode semantics.
- **Frontend microphone:** app/static/js/transcribe/media.js creates microphone streams with getUserMedia at createVadMicStream, buildLiveVadInstance and whole-file batch startup, and implements live/batch capture, pause, reset, retry, navigation and stop. app/static/js/transcribe/vadCapture.js centralises MicVAD options and owned stream startup/cancellation. app/static/js/transcribe/deepgramLiveStream.js assumes one mono PCM source with sample offsets for its existing streaming protocol.
- **Silence handling:** docs/live_stt.md specifies Silero VAD, pre-roll, trailing trim, continuous-speech splits, pause/restart, overlap, live-chunk ordering, and recovery. The existing callback audio (onSpeechEnd) alone does **not** reliably expose an origin frame index after VAD buffering, pre-roll and trimming. An explicit timing integration/spike is necessary.
- **Backend transcript lifecycle:** app/services/transcripts.py queue_audio_chunk_ingestion and _apply_completed_live_chunks process one sequence per transcript (Transcript.next_live_chunk_sequence_no_applied). app/models.py enforces unique (transcript_id, chunk_sequence_no) on TranscriptIngestionJob. A second independent channel cannot simply send the same sequence numbers to the existing route.
- **Deepgram relay:** app/models.py defines a partial unique active/unresolved TranscriptStreamSession per transcript. app/services/transcript_streaming.py and app/routes/deepgram_live.py use one conversation stream, own sample offsets, bounded checkpoints, quota reservations and specific failed-window recovery. Two simultaneous Deepgram conversation streams are **not currently supported** and cannot be enabled simply by opening two sockets.
- **ASR results:** app/services/stt.py commonly returns flat text. Structured timed SttSpeakerTurn results are currently coupled to configured diarisation/segment mapping in SttTranscriptResult. A channel-derived identity must work *without* provider diarisation, but accurate interleaving requires reliable time-aligned segment or word output, or an explicitly less accurate fallback. Validate each provider adapter independently.
- **Structured transcript:** app/services/structured_transcripts.py preserves encrypted turns with id, scope_id, speaker_id, speaker_number, start_ms, end_ms, text and edited. append_transcript_turns **appends in arrival order**, while current_turns_for_response renders "Speaker N" or "?". Clinical source labels, stable ordering across asynchronous channels, version integrity and redaction/generation projections require work; do not invent clinical identities from provider speaker numbers.
- **Owner/privacy/lifecycle:** docs/transcript-capture.md, docs/api.md, docs/security.md, SAFETY.md and AGENTS.md require owner-scoped content, per-owner encryption, Vault-backed bounded retry audio, deletion/retention, no PHI in logs, snapshotted team STT/provider policy, quota/outbox/idempotency and clinician-reviewed drafts. Treat telehealth as an extension of these contracts.
- **API constraints:** docs/api.md documents the existing /audio-chunks and /audio-file routes, route limits, measured server-side duration, 24-hour bounded source retention and retry. A second channel doubles submitted duration if two full-length tracks are sent; VAD upload can reduce submitted duration but needs exact original timing metadata.
- **Testing:** existing focused suites include tests/test_deepgram_live_route.py, tests/test_deepgram_streaming.py, tests/test_transcript_streaming.py, tests/test_deepgram_live_stream_frontend.py and tests/test_workspace_frontend_contract.py. Follow docs/testing.md and docs/dbtesting.md. Update app/api_route_audit.py if adding /api/v1 endpoints.

## 2. Candidate UX: existing dropdown (requires sign-off)

Add one user-facing option to the current recording-mode menu, provisionally **Telehealth (microphone + call audio)**, retaining "Recorded upload" and "Live capture" unchanged. Internal persisted configuration must distinguish *capture source* from *ingestion mode*. The selected consultation should not change underlying source type midway without an explicit transition/reset contract.

Proposed permission flow: after an explicit Start action, acquire the selected remote tab's shared audio via getDisplayMedia (requires video capture request; its video track must not be sent or saved), and the clinician microphone via getUserMedia. Validate that a remote audio track really exists; do not downgrade to mic-only without an explicit user decision. Require clear UI showing each channel's signal/availability and whether the system is recording, stopping, transcribing or blocked. User chooses the tab/window; OpenScribe does not connect to Teams/WhatsApp APIs and cannot programmatically choose an arbitrary app.

No claim of universal native desktop app/system audio compatibility. Start with a tested matrix for Chrome/Edge + tab audio; later native/window/system audio only if tested and separately authorised. OS-specific capture code is *not* the starting requirement.

Questions to resolve:
- Does "Telehealth" perform **live incremental transcription**, **recorded two-channel upload after stop**, or both?
- Is the v1 call source strictly a browser tab, or also desktop app/window/system audio when the browser offers it?
- Should both input permissions be reacquired per session; what if remote audio is missing or sharing is revoked?
- Should the dropdown be selectable mid-consultation, or only before recording starts?

## 3. Timing and audio capture invariant (mandatory)

**Do not stitch two separately VAD-compressed recordings together and compare their local ASR timestamps.**

Proposed capture model:

1. Request both tracks; connect both to a common Web Audio AudioContext (or another demonstrably equivalent sample-accurate common-clock mechanism).
2. Define a shared recording timeline at an agreed first audio-context frame. Read timestamps/sample-frame counters at the earliest reliable point, **before** VAD/silence suppression. Channel timestamps are relative to this common origin, not Date.now(), file creation times, chunk upload times, or independent VAD clocks.
3. Maintain independent per-channel sample data and VAD state; preserve original sample-frame spans even through silence, pre-roll, trailing trim, forced flush, overlap, resampling, pauses, buffering or network failure.
4. Each uploaded speech region needs a channel identifier, common-clock origin/span, actual sample mapping and deterministic idempotency identity. ASR-relative timings must map back to original consultation time. A simple start_ms + relative_word_ms transform is valid only if the submitted audio is a **single contiguous original interval**; for discontiguous concatenated voiced spans, preserve a piecewise sample-to-original-time map or do not concatenate them.
5. Distinguish *silence/no voiced sample* from *track unavailable/suspended* and *audio sent but not committed*. Missing audio must surface as an explicit gap; do not quietly advance its 'complete' watermark.
6. AudioWorklet/shared AudioContext is a candidate, not yet proven against the pinned MicVAD internals. Run an instrumented technical spike first to determine how to associate VAD's emitted samples with exact source frames. Consider processing timestamped blocks before VAD rather than deriving timestamps from its speech-end callbacks. Reuse current VAD runtime where possible without mutating its internal memory or weakening its bounds.
7. Capture both channels against one timeline even if each starts returning valid frames at different times; define whether late-starting channel is marked as missing or simply begins at its first valid frame. No fabricated 0-second offset.
8. Bound in-browser buffers, memory/CPU, pending chunks and cleanup. Video is not processed. No hidden recording after a source stops, permission is revoked, browser tab closes, user logs out, or consultation is deleted.
9. Consider hardware clock mismatch, resampling and remote-call playback latency. Common capture timeline reflects audio heard locally, not the patient's physical utterance time. Define accuracy target and report discontinuities.

### Illustrative metadata — NOT a final API contract

~~~json
{
  "capture_session_id": "<server-generated UUID>",
  "channel": "remote",
  "segment_sequence_no": 17,
  "timeline_start_frame": 1200000,
  "timeline_end_frame": 1440000,
  "timeline_sample_rate": 48000,
  "submitted_audio_sample_rate": 16000,
  "timeline_mapping": "contiguous",
  "idempotency_key": "<stable UUID>"
}
~~~

An audio payload with 16 kHz samples is not inherently 16 kHz in *timeline frame coordinates*. Either preserve a correctly defined mapping or normalize timestamps to common-millisecond units using integer/rational arithmetic. Require clock and duration validation server-side; client claims are not trust boundaries.

## 4. Backend architecture options — decision required

**Option A: two ordinary independently accounted STT jobs, coordinated by a single telehealth capture session.** Recommended conceptual architecture. Design a server-owned capture/session identity, per-channel ordered piece IDs and safe replay/idempotency; jobs may complete in any order. Introduce a **merge readiness/watermark** mechanism per channel. Do not apply a later result to canonical source text until both channels are complete through that timeline boundary, or the user has explicitly resolved a failed/gapped interval. An out-of-order result must not rewrite clinician edits or silently reorder committed text.

**Option B: synchronous whole-session two-file batch with common metadata.** Simpler v1 candidate if *recorded-after-stop* is acceptable. Persist the two independently submitted files/jobs; only commit ordered source after both have terminal results, with explicit partial-result recovery. Avoid assuming the existing whole-file jobs can be uploaded concurrently without lifecycle changes.

**Option C: two Deepgram live sockets/streams.** Requires deliberate redesign of TranscriptStreamSession uniqueness, source-sample accounting, server relay, checkpoint/commit order, per-channel recovery windows, browser reconnection and finalization. This is a separate work package, not an incidental enhancement to Option A.

**Option D: provider-native multichannel audio in one request.** Potential later optimization only; cannot be the generic solution while OpenScribe supports multiple STT providers and its current Deepgram protocol is mono. Must preserve source-channel separation and accounting if added.

**Strong constraints for all options:**
- No new broad admin/leader access to content; per-user DEK encryption for new text/turns and bounded Vault temporary audio.
- Server-authoritative capture mode, session ownership, provider snapshot, quota reservations/settlements and response validation; no trust in 'clinician' role field or client-supplied durations for billing.
- Idempotency and retry per *channel + original interval*; correct ordered application after retries; no duplicate turns; clear expiry/deletion.
- Maintain immutable transcript versions, saved generation snapshots, structured edits and per-version redaction offset mapping. Never overwrite edited turns when a late channel completes; define read-only capture interval, incremental draft vs committed versions and conflict resolution.
- Preserve provider costs/usage by job attempt, distinct metrics for session elapsed time, per-channel received/captured time, per-channel VAD speech duration, actual transmitted duration and provider-metered/billed duration. Do not infer billed duration from VAD speech alone.
- Missing provider timings require an explicit supported-provider capability rule or coarser, disclosed ordering guarantee. Never fabricate precise utterance/word timestamps from a flat result.
- Avoid conflict with current 'one stream per transcript' and one global next-live-sequence barriers without an approved migration. Any migration must handle old data and downgrade policy safely.
- For transcript display and generation, define source-derived display roles separate from provider diarisation; never claim the remote channel proves patient identity or only contains one person.

## 5. Decision register — BLOCKING QUESTIONS FOR MAINTAINER

Mark each with a decision, rationale, and sign-off date before implementing the dependent work.

| ID | Decision required | Proposal / tradeoff |
| --- | --- | --- |
| D1 | Is first release post-stop dual recordings, near-live VAD chunks, live Deepgram streams, or both? | Start with after-stop batch unless immediate live notes are essential; this changes backend design considerably. |
| D2 | What can be shared? Browser call tab only, or desktop app/window/system audio also? Which browsers/OS must pass launch tests? | Tab audio on desktop Chrome/Edge as v1 supported baseline. |
| D3 | May telehealth be used with any configured STT provider? What if provider returns flat text without segment timestamps? | Require verified timed segments for trustworthy sub-chunk interleaving, or clearly define coarse fallback / disable. |
| D4 | Exact meaning of Clinician/Patient labels and source trust? Can remote include family/interpreter/other clinician? | "Clinician (microphone)" / "Remote (call audio)" as provenance; optionally display Patient only after clinician confirms. |
| D5 | Do you want provider diarisation *within* each channel, or disable it and use solely source-derived role labels? | Source label by default; optional provider intra-channel diarisation later. |
| D6 | What if remote sharing is denied/ends mid-call, mic disconnects, or only one channel has audio? Stop entire capture, offer reacquire, or permit explicit one-sided continuation? | Fail visibly, mark gap, allow explicit recovery/reacquisition only under a defined policy. |
| D7 | How should partial failures affect transcript display and note generation? Allow provisional available turns or block until both channels complete / gaps acknowledged? | Block finalization/generation until unresolved channel intervals are retried or explicitly discarded, matching existing Deepgram gap safety. |
| D8 | Do channels both retain independent pre-roll/VAD and trimming? Do we need word-level alignment or speech-segment-level ordering? Target synchronization tolerance? | Preserve original frame mapping; word/phrase timings preferred; set synthetic test tolerance. |
| D9 | What should happen for overlapping speech and tie timestamps? | Keep both turns with deterministic stable ordering and genuine intervals; do not delete/merge one speaker. |
| D10 | Is it acceptable to meter two 30-minute channels as up to 60 STT minutes, with VAD silence reduction, and enforce provider quotas against actual submitted/billed durations? | Show combined provider-metered minutes plus per-channel breakdown; confirm policy. |
| D11 | Can the selected source mode change on an existing consultation; should telehealth capture append to mic-only recordings? | Allow new capture session on a consultation only after earlier one finalized, preserving provenance per source/scope. |
| D12 | UI: show individual input levels, source names and warnings, browser permission prompts, which active source indicators and persistent labels? | Both channels clearly visible; no silent downgrade from two to one. |
| D13 | Existing consultation audio may contain patient speech through clinician microphone (speaker/headset bleed). What approach to duplicates and clinical attribution? | Encourage headphones; document cross-talk; no automatic source override without validated audio evidence. |
| D14 | Governance: user acknowledgement, patient notice/objection, source-audio retention/export, provider approval and deployment rollout restrictions? | Reuse current privacy/retention boundaries and add telehealth-specific hazards and deployment guidance. |

**Responses belong in this register.** When decisions are resolved, change status from DRAFT to APPROVED with date, accepted scope and review notes. Never erase the rejected alternatives or unresolved risks.

## 6. Proposed agent work packages (not authorised until dependent decisions are resolved)

### WP0 — Evidence and timing prototype
- Examine pinned vad-web behavior, potential independent VAD instances fed from AudioContext/AudioWorklet tracks, original sample offset tracking, pause/restart, device changes, forced flush and overlap. Verify whether browser 'share audio' actually provides an audio track for supported sources.
- Produce synthetic timing probes: silence removed differently on each channel, concurrent speech, trim/pre-roll, long recordings and reconnect. Confirm whether provider output offers suitable timestamps under approved STT configurations.
- Deliver capability matrix and measured timing/CPU/memory findings before choosing a capture API design.

### WP1 — Capture UX and two-channel lifecycle
- In the existing record split-menu choose a telehealth workflow without breaking saved preferred recording mode semantics or existing ingestion-mode transitions.
- Build a small, well-owned capture module with two source streams, one common clock, per-channel VAD, explicit teardown, stop/restart/disconnect and reliable metadata. Keep native OS audio adapters out of scope unless authorised.
- Test permission revocation, hidden tab, interrupted media track, startup races, late async returns, stopping during prompts, browser refresh, navigation and sign-out. No user content in Web Storage or logs.

### WP2 — Secure server capture/session model and ingestion jobs
- Design schema/migrations to group channel jobs into the same consultation session, preserve per-source sample mappings, maintain per-channel stable ordering and finalization barriers.
- Reuse existing request validation, idempotency, team STT snapshots, Vault source-audio retention, retries, rate limits, owner authorization, quota/outbox, audit and expiry; do not silently loosen unique constraints.
- Enforce bounded metadata and safe per-source failure/recovery. Update route audit if routes added. Adapt source-duration rules to measured rather than client-declared audio.

### WP3 — ASR adapter timing and transcript merge
- Provide a normalized provider result with timed segments regardless of provider diarisation (where supported); validate that timings are bounded, monotonically meaningful within each chunk and consistent with returned text.
- Convert ASR-relative offsets through the exact speech-segment-to-consultation mapping. Deterministically stable-merge both channels, preserving overlaps, source labels, same-scope identities, turn UUID stability, version/PII/redaction/generation mapping and edits.
- Enforce channel watermarks: a quicker completed channel must not permanently commit newer text before an earlier region from the other channel is resolved. Retries must not duplicate turns.

### WP4 — Test, safety, operational docs and staged rollout
- Focused unit tests for source-frame timing, VAD trimming/pre-roll, ASR timing offset calculations, staggered completion, track loss, source mismatch, duplicate/idempotent submissions, retry barriers, provider/quota cases, retention, deletion, encryption and cross-owner authorization.
- Browser E2E tests of split-button, source consent/permissions, call-tab selector, recording indicators, recovery and a real two-track synthetic conversation. Test supported browser/OS combinations with speaker leakage/headphones and long-run timing.
- Update docs/transcript-capture.md, docs/live_stt.md, docs/api.md, docs/stt-config.md, docs/testing.md as relevant, SAFETY.md/hazard case, tests and docs index. Use synthetic data only. Run relevant automated tests plus operational-doc/route audits.

## 7. Acceptance criteria (draft)

1. Clinician selects Telehealth in existing Scribe recording dropdown and explicitly starts/stops capture; each valid source is clearly visible and controllable.
2. Local mic and remote tab audio stay independent through capture and ASR; no video or unintended system audio is sent/stored.
3. An original consultation-time speech map survives independent VAD silence suppression, pre-roll, trimming, resampling and overlap. Test evidence demonstrates deterministic chronological reconstruction.
4. Two ASR jobs may finish in reverse order; the merged owner-only structured transcript remains correctly ordered, displays trusted **source provenance**, retains both overlapping utterances and does not duplicate results on retry.
5. Any missing channel, source interruption, non-timestamped provider, gap or failure has an explicit safe outcome; no false "complete" or silent partial note generation.
6. Existing microphone-only whole-file, live-chunk and Deepgram stream/dictation paths continue to behave identically; all existing authorization, encrypted persistence, redaction, versioned edits, generation, quotas, retry, retention and deletion controls hold.
7. Usage shows or reliably accounts for actual captured/submitted/provider-metered seconds by channel and as aggregate provider usage; consultation wall-clock duration is not confused with billed channel-minutes.
8. No medical/patient details or raw audio in logs, provider metadata, browser storage or test fixtures. Review clinical safety and disclosure text before release.

## 8. Implementation guardrails and handoff

- **STOP:** No agent should implement an architectural choice covered by unresolved D1–D14 without specific maintainer approval. Purely read-only exploration and synthetic proof-of-concept branches are acceptable if requested separately; do not merge them into this feature branch as implicit policy.
- **NO code yet on this branch.** This initial commit contains only this planning Markdown file.
- Before implementing, re-fetch current code and tests and explicitly map each work package to symbols, migrations, API contracts, browser assets and regression tests. Keep changes focused and staged.
- Maintain AGENTS.md non-negotiables: user/owner content isolation, encryption under user DEK, Vault cleanup, provider secrets, redaction/generation snapshots, clinical safety, quota/outbox and audit metadata.
- Do not auto-merge or open a production rollout. Update this file with decisions and a dependency-ordered executable implementation plan before creating code work items.
