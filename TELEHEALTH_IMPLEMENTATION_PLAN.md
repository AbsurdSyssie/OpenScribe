# Telehealth dual-channel capture — implementation and decision plan

**Status:** PRODUCT SCOPE AGREED — implementation work packages ready for agents. Engineering feasibility, security, privacy and clinical-safety release gates remain mandatory; this status is **not** production deployment approval.
**Branch:** feature/telehealth-dual-channel-capture (branched from master at bf63c2879dd8526db582a9598663db92df13642b on 2026-11-04).
**Owner:** OpenScribe maintainers. **Scope:** One clinician-owned consultation in OpenScribe Scribe, with local microphone and remote-call/tab audio kept separate for transcription and mapped into one ordered structured transcript.
**Plan authority:** This document was requested explicitly in the repository root. Repository AGENTS.md normally prefers plans/; the root placement is intentional.
**Source of truth:** Maintainer decisions in rounds 1–3 define the product contract. The latest dated decision overrides earlier provisional wording. Reconcile the design against current implementation, tests, migrations, and operational documentation before coding. Agents may autonomously resolve ordinary engineering choices within the agreed behavior; escalate only architecture/safety conflicts or changes to the specified product scope.

## Maintainer decisions — round 1 (2026-11-04)

These decisions supersede any earlier "candidate" recommendations in this draft. Preserve their rationale during the next review.

1. **V1 is recorded capture with transcription after the consultation**. No simultaneous Deepgram live ASR sessions in the first release. Bounded uploads *during* recording remain a technical option if needed for memory safety, but no live result display is required.
2. **Browser-tab audio comes first.** Support for **WhatsApp native desktop application audio remains a required later phase**; it must not delay initial tab support, and does require a separate supported platform approach.
3. **Do not exclude STT configurations lacking timestamps.** Prefer real per-word/segment timestamps for chronological mixing. Without them, preserve source-attributed text using best-effort grouping; a transcript consisting of two blocks titled **Clinician:** and **Patient:** is acceptable. Never invent fine-grained timestamps/chronology or assume an LLM's inferred sequence is fact.
4. **Fixed v1 speaker labels:** microphone **Clinician**, remote call audio **Patient**. Manual reassignment is optional future work. These labels describe expected source roles, not guaranteed real-person identity.
5. **Capture-source loss must produce an unmistakable warning directing the clinician to restart.** Exactly what stops, what recorded content is retained and how to restart require further decisions; no silent one-source fallback.
6. **Separate, subtle Telehealth button/toggle near recording controls**, not a third option inside the Recorded upload / Live capture dropdown. A **per-user show/hide preference** is desired for consideration; exact default, UX and persistence require sign-off.
7. **Original shared sample clock remains mandatory before VAD**, with per-channel source offsets, independent VAD and independent ASR metering. Existing batch VAD concatenation loses silence offsets and cannot be reused unchanged.

**Engineering design remaining:** exact failure/restart state transitions, persistent interruption markers, warning implementation, per-part mapping and flat-text assembly, overlap handling, VAD timing validation and release-test evidence. See the final product decisions above and the implementation work packages below.

## Maintainer decisions — round 2 and visualiser refinement (2026-11-04)

**Confirmed on 2026-11-04:**

- **On unexpected source loss:** warn immediately, stop the compromised two-source capture and offer a restart as another capture run in the **same consultation**; **preserve already recorded audio**, not discard and restart from zero. Preserve it only within existing encrypted storage, retry and retention constraints — this is **not** permission to retain audio indefinitely or after explicit consultation deletion. Define exact stop/upload/error races and interruption markers before coding.
- **Incomplete note generation is allowed with a prominent warning and explicit acknowledgement**, rather than permanently blocking the clinician. Clearly identify missing/failed audio and avoid claiming the note is based on the full consultation. Do not suppress existing clinician review or safety protections.
- **Bounded uploads during recording are approved**, while **ASR/transcript assembly remains after Stop**. Current batch recorder already has rollover concepts; telehealth must preserve two-channel original-timeline mappings across all parts and avoid unbounded browser memory. Ensure source parts are safely retained and retryable on interruptions.
- **Best-effort timing/interleaving** is approved only at evidenced chunk/segment granularity. When timestamp accuracy is inadequate, show source-labelled `Clinician:` and `Patient:` blocks rather than invent event order; feed the LLM the same provenance and uncertainty.
- **UI placement**: subtle independent Telehealth control beside current recording area, **shown by default with a per-user hide preference**, and enabling Telehealth may switch to recorded mode with confirmation. Existing dropdown stays unchanged.
- **Usage/quota**: apply limits to **combined actual submitted/provider-metered audio minutes** from both channels, tracked independently by channel. A 30-minute session can consume up to 60 channel-minutes. Keep session duration distinct.
- **Echo/cross-talk**: recommend headphones and appropriate microphone echo cancellation; automated cross-channel duplicate removal is **not a v1 requirement**. First-use telehealth guidance/modal should include this recommendation, with an appropriate per-user preference/acknowledgement if approved. Do not claim laptop microphones are immune to remote audio bleed.

**UI exploration from latest feedback — concept, NOT final sign-off:**

- Previous illustrated two-row labelled controls were judged **too busy**. Telehealth toggle should be a **small phone icon only**, using an accessible name and hover/focus tooltip (e.g. "Telehealth recording"), with visible on/off state.
- The existing visualiser should initially expand into compact **Clinician** and **Patient** audio bars. Proposed colour progression **red → green** based on **five cumulative seconds of real voice activity on each source** (not five seconds of elapsed time); after both thresholds, show a brief green confirmation glow and **collapse into one animated combined activity bar** responding to either channel.
- **Required safety distinction:** five seconds of VAD-confirmed speech is a *signal-confidence milestone*, **not proof** the recording is reliable/complete. A connected but silent track is neither a failure nor a reason to block capture. Use an accessible waiting/amber state rather than indicating silence as broken; reserve true red/error for missing or disconnected tracks. Retain two small independent source-health indicators in collapsed state; automatically re-expand on source loss or degraded capture and announce the failure. Combined meter may use max/aggregate *visual* level but never mix the underlying ASR sources.
- Prefer a minimal CSS transition and existing level/VAD signals, avoiding expensive waveform reconstruction. Respect reduced-motion and non-colour status cues. Keep the split-button and dictation layout compact; test mobile/responsive width.
- **Open:** exact graphic, whether five seconds cumulative VAD is the right threshold, whether collapse is automatic and reversible, visual calibration and tooltip copy. Do not implement this as a fixed contract without maintainer approval.

## Maintainer decisions — final product confirmation (2026-11-04)

**This section supersedes earlier draft suggestions where they differ. The maintainer has answered all six final product questions.**

1. **Recoverable audio, not indefinite storage.** "Never discard audio" means **do not throw away captured/recoverable source audio because one channel fails, a call drops, or capture restarts before it has been transcribed**. Retain pending/failed source data through the established owner-encrypted Vault retry and expiry workflow; if the current 24-hour recovery window is insufficient for a specific supported interrupted-recording recovery path, identify and resolve that safely as an engineering/release gate. After successful transcription, normal source cleanup, deletion, GDPR/privacy and configured retention apply. Do **not** introduce permanent recording/replay storage.
2. **Recorded-mode switch accepted.** The independent phone-icon Telehealth toggle selects recorded/whole-file capture when enabled; if an existing live-mode consultation would be switched, use confirmation and the existing mode-change constraints, never changing mode during active recording or while unresolved jobs block switching. Turning telehealth off returns to ordinary recording controls but must not alter/corrupt already captured audio. Two-channel live ASR is deferred.
3. **Provider diarisation disabled/bypassed for telehealth v1** where supported. Speaker provenance is assigned deterministically from input source: **Clinician**=local microphone; **Patient**=call/tab audio. Additional people on either source are not individually separated in v1; no false claim of verified identity. Continue to respect existing provider configuration snapshots and avoid altering non-telehealth diarisation settings.
4. **Five-second audio check is informational only.** Recording starts as soon as both usable audio tracks and recording pipeline are ready, **not after speech-verification thresholds**. Show compact initial Clinician/Patient activity bars, independently accumulate approximately five seconds of valid detected speech on each (not elapsed time), display a brief positive visual confirmation and collapse to a combined visual **only** once both have been heard. A connected silent channel remains waiting/neutral, not failed. A user can re-expand the channels. After collapse, **continue monitoring both source tracks independently** and re-expand/show a clear error on dropout. Single visualizer is UI-only; never mix underlying ASR audio. The five-second threshold and exact animation are a visual-design target, not an audio integrity guarantee.
5. **One-time first-use modal with acknowledgement.** Before initiating the first Telehealth recording, show a short, accessible notice that microphone and call/tab audio will be captured; **headphones are recommended** to minimise echo/duplicate speech; the clinician must inform call participants and follow organisational recording/consent policy. Use an owner-scoped persisted acknowledgement and do not ask again under unchanged policy; require positive Continue. Existing policy, clinical-safety and privacy review still apply; this modal alone is not legal consent from remote participants.
6. **Browser v1 first; desktop WhatsApp is still required on roadmap.** Launch baseline: **Chrome/Edge on Windows and macOS with user-selected browser-tab audio** (Teams/WhatsApp Web or equivalent tested calls); validate actual browser support in WP0. Separate **Phase 2: WhatsApp native desktop application audio** with platform-specific feasibility, implementation and acceptance tests; it is not a v1 launch blocker or omitted TODO. Linux, Firefox, Safari and other native apps remain unpromised pending validation.

**Carry-forward decisions from round 2:** warn/stop/restart same consultation without discarding recoverable audio; generation from explicitly acknowledged incomplete source is allowed with a prominent warning; bounded uploads while recording are allowed; combine per-channel submitted/provider-metered audio for quota/billing; preserve provenance when timings are absent; no automatic echo/cross-channel transcript deduplication.

**No additional product interview is required for first-release coding.** Remaining unspecified details—backend session entities, endpoint shape, track acquisition races, VAD sample-to-timeline mapping, interrupted upload state machine, quota reservation and UI implementation—are engineering design work within these constraints. An agent must stop and escalate only if evidence proves a product contract cannot be satisfied safely or an existing ownership/privacy/clinical safety invariant conflicts with it.



## 0. Established intent from maintainer discussion

The following are user-stated requirements, not open proposals:

- Capture two *independent* sources: clinician/local microphone and patient/remote tab or call audio, rather than mixing sources into one monophonic STT input.
- Send each source separately through the existing STT/ASR provider workflow, then merge recognized speech into a single chronological transcript.
- Give turns source-derived labels (initially "Clinician" and "Patient"/remote side) and reuse OpenScribe's structured speaker-turn presentation/editing rather than introducing a separate transcript editor.
- Keep the existing recording-mode dropdown unchanged. Offer a subtle **separate Telehealth control**, with optional owner-scoped visibility preference subject to sign-off.
- Maintain a single **original consultation timeline** even though each channel's VAD removes different amounts of silence. Timestamp frames **before VAD**; never use file creation time, VAD callback time, or concatenated voiced-audio duration as the shared time coordinate.
- Meter the two capture sources separately and preserve accurate STT audio usage/quota accounting.

**Partially agreed:** post-stop transcription, browser-tab-first scope, fixed labels, no-timestamp fallback and independent Telehealth control. Outstanding questions are detailed in the maintainer decision register below.

## 1. Baseline findings, verified against master at branch point

- **UI:** app/templates/transcribe/_workspace.html has the record split-button and dropdown with "Recorded upload" and "Live capture" options; app/static/js/transcribe/app.js switches ingestion mode. The backend enum TranscriptIngestionMode in app/models.py has only whole_file and live_chunked. **Telehealth must be a separate control**, not another dropdown option; consider an owner-level visibility preference.
- **Frontend microphone:** app/static/js/transcribe/media.js creates microphone streams with getUserMedia at createVadMicStream, buildLiveVadInstance and whole-file batch startup; its batch path appends VAD segments in `batchSpeechSegments`, concatenates them without silence and sometimes uploads a rollover part *before* the recording stops. Thus post-stop **ASR** does not necessarily imply keeping all two-channel audio in browser memory. app/static/js/transcribe/vadCapture.js manages MicVAD ownership; app/static/js/transcribe/deepgramLiveStream.js is mono and is not in v1 scope.
- **Silence handling:** docs/live_stt.md specifies Silero VAD, pre-roll, trailing trim, continuous-speech splits, pause/restart, overlap, live-chunk ordering, and recovery. The existing callback audio (onSpeechEnd) alone does **not** reliably expose an origin frame index after VAD buffering, pre-roll and trimming. An explicit timing integration/spike is necessary.
- **Backend transcript lifecycle:** app/services/transcripts.py queue_audio_chunk_ingestion and _apply_completed_live_chunks process one sequence per transcript (Transcript.next_live_chunk_sequence_no_applied). app/models.py enforces unique (transcript_id, chunk_sequence_no) on TranscriptIngestionJob. A second independent channel cannot simply send the same sequence numbers to the existing route.
- **Deepgram relay:** app/models.py defines a partial unique active/unresolved TranscriptStreamSession per transcript. app/services/transcript_streaming.py and app/routes/deepgram_live.py use one conversation stream, own sample offsets, bounded checkpoints, quota reservations and specific failed-window recovery. Two simultaneous Deepgram conversation streams are **not currently supported** and cannot be enabled simply by opening two sockets.
- **ASR results:** app/services/stt.py commonly returns flat text, while structured timed SttSpeakerTurn results are currently coupled to configured diarisation/segment mapping. **V1 supports both**; with missing timings the approved fallback is clearly separated Clinician/Patient blocks. Validate each provider adapter and preserve the provenance of untimed results in generated-note inputs.
- **Structured transcript:** app/services/structured_transcripts.py preserves encrypted turns with id, scope_id, speaker_id, speaker_number, start_ms, end_ms, text and edited. append_transcript_turns **appends in arrival order**, while current_turns_for_response renders "Speaker N" or "?". Clinical source labels, stable ordering across asynchronous channels, version integrity and redaction/generation projections require work; do not invent clinical identities from provider speaker numbers.
- **Owner/privacy/lifecycle:** docs/transcript-capture.md, docs/api.md, docs/security.md, SAFETY.md and AGENTS.md require owner-scoped content, per-owner encryption, Vault-backed bounded retry audio, deletion/retention, no PHI in logs, snapshotted team STT/provider policy, quota/outbox/idempotency and clinician-reviewed drafts. Treat telehealth as an extension of these contracts.
- **API constraints:** docs/api.md documents the existing /audio-chunks and /audio-file routes, route limits, measured server-side duration, 24-hour bounded source retention and retry. A second channel doubles submitted duration if two full-length tracks are sent; VAD upload can reduce submitted duration but needs exact original timing metadata.
- **Testing:** existing focused suites include tests/test_deepgram_live_route.py, tests/test_deepgram_streaming.py, tests/test_transcript_streaming.py, tests/test_deepgram_live_stream_frontend.py and tests/test_workspace_frontend_contract.py. Follow docs/testing.md and docs/dbtesting.md. Update app/api_route_audit.py if adding /api/v1 endpoints.

## 2. Telehealth control and capture UX — product-agreed

**Confirmed:** Telehealth is a **separate subtle button/toggle** by the recording controls, NOT a third recording-mode dropdown option. The existing recorded upload / live capture selection and backend `whole_file` / `live_chunked` modes must not be silently redesigned. V1 telehealth is two-source recorded capture with **post-consultation ASR**, likely using whole-file ingestion plus a new server-owned channel/session contract; the concrete ingestion design is still open.

**Preference:** implement an **owner-scoped show/hide Telehealth control** setting, shown by default, with a discoverable control in user preferences so it can be re-enabled. Existing `app/static/js/transcribe/app.js` sends full saved preferences via `persistUserAppPreferences`; `app/services/preferences.py` and `app/schemas/preferences.py` whitelist fields. Coordinate schema, normalisation, persistence, bootstrap and client changes without clobbering unrelated user settings. Persist separate first-use modal acknowledgement.

**Browser first:** after an explicit Start action (and first-use notice if not previously acknowledged), request remote tab audio via `getDisplayMedia` and local microphone via `getUserMedia`. Video is required as a browser capture-request parameter but must never be processed or stored. Verify an actual remote audio track exists and recording starts without waiting for either participant to speak. On source loss, immediately warn, stop compromised two-channel capture, preserve recoverable data for transcription and offer another run in the same consultation. Keep missing-audio markers separate from ordinary silence.

**Required future phase:** WhatsApp **native desktop application audio**, with explicitly scoped Windows/macOS platform capture, permission, packaging, security and release testing. Phase 2 must have a concrete tracked agent work package (below). Do not promise native OS-wide app capture in tab-only v1.

**Agreed interaction:** icon-only phone toggle with tooltip and accessible name, default visible, switching safely to recorded mode; initial two source bars are a non-blocking audio-receipt check and collapse after each has accumulated about five seconds of detected voice. Combined visualizer continues to display either source's activity, with two individual health indicators and re-expand on demand or loss. First-use acknowledged modal recommends headphones and call-participant disclosure. Implementation details and supported browser tests belong to WP0/WP1.

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

## 4. Backend architecture — selected v1 and deferred alternatives

**Option A: two ordinary independently accounted STT jobs, coordinated by a single telehealth capture session.** **SELECTED for v1.** Design a server-owned capture/session identity, per-channel ordered piece IDs and safe replay/idempotency; jobs may complete in any order. Introduce a **merge readiness/watermark** mechanism per channel. Do not apply a later result to canonical source text until both channels are complete through that timeline boundary, or the user has explicitly resolved a failed/gapped interval. An out-of-order result must not rewrite clinician edits or silently reorder committed text.

**Option B: coordinated recorded two-channel batch (execution strategy for selected v1).** Post-stop transcription and bounded part uploads during capture are approved. Reuse the current batch rollover principles, but preserve per-channel original-time mappings. Persist two independently owned sets of parts/jobs; assemble source-labelled transcript only after safe finalization or explicitly acknowledged incomplete results. Avoid assuming the existing whole-file jobs can be uploaded concurrently without lifecycle changes.

**Option C: two Deepgram live sockets/streams (DEFERRED beyond v1).** Requires deliberate redesign of TranscriptStreamSession uniqueness, source-sample accounting, server relay, checkpoint/commit order, per-channel recovery windows, browser reconnection and finalization. This is a separate work package, not an incidental enhancement to Option A.

**Option D: provider-native multichannel audio in one request (DEFERRED).** Potential later optimization only; cannot be the generic solution while OpenScribe supports multiple STT providers and its current Deepgram protocol is mono. Must preserve source-channel separation and accounting if added.

**Strong constraints for selected v1 and later options:**
- No new broad admin/leader access to content; per-user DEK encryption for new text/turns and bounded Vault temporary audio. **Do not lose recoverable audio before transcription due to interruption, but do not introduce permanent post-transcription source-audio retention.**
- Server-authoritative capture mode, session ownership, provider snapshot, quota reservations/settlements and response validation; no trust in 'clinician' role field or client-supplied durations for billing.
- Idempotency and retry per *channel + original interval*; correct ordered application after retries; no duplicate turns; clear expiry/deletion.
- Maintain immutable transcript versions, saved generation snapshots, structured edits and per-version redaction offset mapping. Never overwrite edited turns when a late channel completes; define read-only capture interval, incremental draft vs committed versions and conflict resolution.
- Preserve provider costs/usage by job attempt, distinct metrics for session elapsed time, per-channel received/captured time, per-channel VAD speech duration, actual transmitted duration and provider-metered/billed duration. Do not infer billed duration from VAD speech alone.
- **Approved fallback:** an STT provider without timings is supported. Use evidenced chronology where available; otherwise keep separate `Clinician:` and `Patient:` source-labelled blocks and never fabricate precise timestamps or clinical conversation order.
- Avoid conflict with current 'one stream per transcript' and one global next-live-sequence barriers without an approved migration. Any migration must handle old data and downgrade policy safely.
- For transcript display and generation, fixed v1 source labels are **Clinician** (local microphone) and **Patient** (tab/call audio). **Bypass provider diarisation for telehealth v1**, independent of ordinary non-telehealth preferences. Do not claim remote-channel provenance verifies personal identity.

## 5. Product decision register — closed 2026-11-04

All product-level D1–D14 decisions are resolved sufficiently to begin implementation. **Engineering design details and formal release review are still required**, not additional product-question rounds.

| ID | Resolution for first release |
| --- | --- |
| D1 | Record two independent audio sources; post-stop transcription only. Bounded audio-part uploads *during* the consultation are allowed and should be used to constrain memory. |
| D2 | Tested Chrome/Edge **browser tabs** on Windows/macOS first; **Phase 2 must implement native WhatsApp desktop audio** after a feasibility spike. |
| D3 | Use provider timestamps if available; otherwise use original chunk boundaries where meaningful, or separate complete source-labelled **Clinician:** / **Patient:** blocks with no invented chronology. LLM note input retains provenance and uncertainty. |
| D4 | Fixed v1 speaker display: mic=**Clinician**, remote audio=**Patient**. They are assigned source roles, not proven individual identity. |
| D5 | **Disable/bypass provider speaker diarisation for telehealth v1**. Multiple participants within one source receive its source label; role-editing and within-source diarisation are later scope. |
| D6 | On source loss, immediately warn and stop the compromised capture; **retain recoverable recorded/queued audio**, offer a new capture run within the same consultation and mark actual gaps. Existing encryption, retry expiry, cleanup and user deletion remain. |
| D7 | Allow clinical-note generation from incomplete capture **after an explicit prominent warning and acknowledgement**, preserving missing-audio context and clinician review. Do not confuse untimed-but-complete ASR with dropped source data. |
| D8 | Shared pre-VAD sample timeline on both channels, with exact retained-sample mapping through VAD/pre-roll/trim/resample/rollovers. Agents must establish accuracy tolerances and test them using synthetic audio; silence never compresses consultation time. |
| D9 | Preserve real simultaneous overlapping speech and use deterministic tie order when timing exists. No fabricated ordering if text-only ASR lacks sufficient provenance. |
| D10 | Meter captured, submitted and provider-accounted time independently for both channels; enforce combined submitted/metered channel-minute quota while separating elapsed consultation duration. |
| D11 | Enabling separate Telehealth toggle switches to recorded mode under existing safe mode-transition/confirmation rules; disabling restores ordinary mic-only recording controls and preserves prior transcript content. Repeated interrupted runs remain attributable within one consultation. |
| D12 | Separate **subtle phone-icon** control by recording bar, tooltip + accessible label, **shown by default** with per-user show/hide preference. Temporary two-channel audio check, ~5s cumulative speech per source, non-blocking confirmation/brief glow, then combined meter; manual expand and auto-expand on fault with individual source status always available. |
| D13 | No auto-deduplication in v1; use mic echo-cancellation as appropriate, recommend headphones in **first-use acknowledged modal** stored per user and test for bleed. |
| D14 | One-time modal also states microphone and call audio capture and clinician responsibility to inform participants/observe organisational policy; it is not remote participant consent. Preserve owner access, source retention/deletion and explicit DCB0129/privacy/security release review. |

**Engineering authority:** Implement ordinary details autonomously if consistent with this table. If a technical spike finds a platform/browser inability or safety conflict, document the evidence and escalate **only the conflicting contract**. Do not silently reduce supported sources, drop audio, fabricate timestamps, store recordings indefinitely or weaken existing backend invariants.

## 6. Implementation work packages — authorised product scope, sequential dependencies

### WP0 — Evidence and timing prototype
- Examine pinned vad-web behavior, independent VAD fed from AudioContext/AudioWorklet tracks, original sample offset tracking, pause/restart, device changes, forced flush and overlap. Audit **existing batchSpeechSegments concatenation and rollover during capture**: the current voice-only batch recorder drops silence offsets, and long sessions may upload parts before Stop. Establish a bounded-memory two-channel design even though STT results are post-consultation.
- Produce synthetic timing probes: silence removed differently on each channel, concurrent speech, trim/pre-roll, long recordings and reconnect. Confirm whether provider output offers suitable timestamps under approved STT configurations.
- Deliver capability matrix and measured timing/CPU/memory findings before choosing a capture API design.

### WP1 — Capture UX and two-channel lifecycle
- Add an **icon-only phone Telehealth toggle near recording controls**, tooltip/accessible name, shown by default with owner-scoped show/hide preference. Preserve existing split-menu options, automatically select recorded mode with safe confirmation, and add one-time persisted user acknowledgement with headset/participant notice guidance.
- Build a small, well-owned capture module with two source streams, one common clock, per-channel VAD, explicit teardown, stop/restart/disconnect and reliable metadata. Add two initially expanded source activity meters, non-blocking ~5-second per-channel speech confirmation, brief green feedback, collapse to one combined level meter, separate persistent source-health checks, user-expand and auto-expand on drop; do not mix recorded channels. Use minimal accessible CSS and existing VAD level signals. Native OS audio adapters belong to Phase 2.
- Test permission revocation, hidden tab, interrupted media track, startup races, late async returns, stopping during prompts, browser refresh, navigation and sign-out. No user content in Web Storage or logs.

### WP2 — Secure server capture/session model and ingestion jobs
- Design schema/migrations to group channel jobs and bounded upload parts into one consultation capture session, preserve per-source sample mappings, stable source ordering, interruption and actual missing-audio gap metadata, and finalization barriers. Ensure no source-audio buffer or pending upload is discarded on a source failure before safe transcription or explicit configured expiry/deletion.
- Reuse existing request validation, idempotency, team STT snapshots, Vault source-audio retention, retries, rate limits, owner authorization, quota/outbox, audit and expiry; do not silently loosen unique constraints.
- Enforce bounded metadata and safe per-source failure/recovery; allow a new run in the same consultation with preserved prior parts, idempotent retry and visible incomplete status. Support explicit warned/acknowledged incomplete-note generation. Update route audit if routes added. Adapt quota rules to aggregate measured provider-submitted audio durations, independent of consultation elapsed time.

### WP3 — ASR adapter timing and transcript merge
- Provide normalized timed segments where the STT provider supports them **without relying on speaker diarisation**, which is bypassed for telehealth. Also support flat-text outputs without inventing word timings: label source-text blocks **Clinician:** / **Patient:**, expose uncertainty and ensure note-generation inputs preserve both. Existing diarisation remains unchanged for ordinary capture.
- Convert available ASR-relative offsets through the exact speech-segment-to-consultation mapping and stable-merge timed outputs, retaining overlapping words/turns, fixed source labels, turn UUID stability, version/PII/redaction/generation mapping and edits. For untimed flat results, output distinct **Clinician:** / **Patient:** blocks instead of false chronological merging.
- Enforce channel watermarks: a quicker completed channel must not permanently commit newer text before an earlier region from the other channel is resolved. Retries must not duplicate turns.

### WP4 — Test, safety, operational docs and staged rollout
- Focused unit tests for source-frame timing, VAD trimming/pre-roll, ASR timing offset calculations, staggered completion, track loss, source mismatch, duplicate/idempotent submissions, retry barriers, provider/quota cases, retention, deletion, encryption and cross-owner authorization.
- Browser E2E tests of icon toggle and preference, safe recorded-mode switch, first-use persisted acknowledgement, source consent/permissions, call-tab selector, initial two VAD meters and collapse/re-expand with real source-health diagnostics, recoverable audio on dropout, incomplete-generation warning, and synthetic dual-track consultation. Test Chrome and Edge on Windows and macOS with web-call sources, headset/speaker leakage and long-running capture.
- Update docs/transcript-capture.md, docs/live_stt.md, docs/api.md, docs/stt-config.md, docs/testing.md as relevant, SAFETY.md/hazard case, tests and docs index. Use synthetic data only. Run relevant automated tests plus operational-doc/route audits.

## 7. Acceptance criteria — agreed v1 product scope; verify engineering implementation

1. Clinician uses a **separate subtle Telehealth icon toggle beside recording**, shown by default and optionally hidden through an owner-scoped preference; existing mode dropdown remains unchanged. Both sources are visible during initial verification and their independent health remains observable after any meter collapse.
2. Local mic and remote tab audio stay independent through capture and ASR; no video or unintended system audio is sent/stored.
3. An original consultation-time speech map survives independent VAD silence suppression, pre-roll, trimming, resampling and overlap. Test evidence demonstrates deterministic chronological reconstruction.
4. Two ASR jobs may finish in reverse order; with supported timings, the merged owner-only structured transcript is correctly ordered and retains overlapping utterances, with source-derived **Clinician/Patient** labels and no duplicates on retry. Without timings, maintain two explicitly source-labelled blocks; do not invent chronology.
5. Non-timestamped provider results are permitted via `Clinician:` / `Patient:` fallback blocks. Missing audio, source interruption, gaps or ASR failures have explicit warnings and preserved recoverable audio; **incomplete-note generation is allowed only after an explicit warning/acknowledgement**, without claiming that capture was complete.
6. Existing microphone-only whole-file, live-chunk and Deepgram stream/dictation paths continue to behave identically; all existing authorization, encrypted persistence, redaction, versioned edits, generation, quotas, retry, retention and deletion controls hold.
7. Usage accounts for captured/submitted/provider-metered seconds independently by channel and combined for STT quota/billing; consultation wall-clock duration is distinct. Bounded uploads during recording must preserve sample-to-original-clock mapping.
8. No medical/patient details or raw audio in logs, provider metadata, browser storage or test fixtures. One-time owner-persisted guidance covers microphone/call audio, headphones and clinician participant-notification obligations. Privacy/security and DCB0129 clinical-safety review are mandatory before release.
9. V1 does not require dual Deepgram live sockets. **WhatsApp native desktop audio is an explicit Phase 2 deliverable**, with a separate tracked feasibility and Windows/macOS platform-capture work package; it is not silently dropped.
10. Existing successful-transcription source cleanup/retention continues unchanged. Failure/restart never silently destroys recoverable untranscribed audio, while deletion/expiry still apply.
11. The initial visualiser does not delay recording while waiting for ~5s cumulative speech; a quiet connected patient channel is not reported as a failure. Any later track loss produces persistent warning and visible source status.

## 7a. Required Phase 2 — WhatsApp native desktop application audio (post-v1)

This is **in scope for the overall feature roadmap**, but not required for browser-tab v1 release.

1. **Platform feasibility:** inventory actual supported Windows and macOS mechanisms for extracting *application/window-specific playback* (e.g. Windows WASAPI process/loopback capture and macOS ScreenCaptureKit/Core Audio where permitted), permissions and whether WhatsApp desktop audio can be isolated without incidental system/patient audio. Do not assume current browser getDisplayMedia universally captures native WhatsApp; validate concrete desktop app versions.
2. **Architecture:** design the smallest secure OS-specific capture bridge, helper or packaging integration that emits the *same* remote source samples/time mapping/capture health contract as the browser tab adapter. Define authenticated handshake and least-privilege local interface; no broad OS recording or new content-sharing endpoint by default.
3. **Privacy/safety:** user-initiated selection/consent, persistent recording indicator, source verification, no accidental unrelated app audio, encrypted transport to existing user session, no raw audio on logs/disk outside approved ephemeral storage, signed/updated binaries where required, trustworthy process/device selection and teardown.
4. **Compatibility/E2E:** test WhatsApp desktop microphone and far-end audio with headphones and speakers on Windows/macOS, app restart, permission loss, notifications, conferencing changes and long-running drift; run the same v1 offset/retention/quota/clinical-safety suite.
5. **Deliverable:** a separate detailed plan/PR chain with platform test results and acceptance criteria, not an untracked footnote; if a platform cannot reliably isolate app audio, do not ship an unbounded system-audio substitute without maintainer approval.



## 8. Implementation guardrails and handoff

- **START:** Product behavior is agreed. Agents may implement the scoped WP0→WP4 work, beginning with timing/platform feasibility and an API/data-contract design checkpoint. Stop and escalate only an evidenced technical impossibility, user-visible scope change, privacy/security/clinical-safety conflict or deviation from the confirmed product contracts.
- **Planning-only branch at handoff.** No application code has been changed in preparing this plan. Agents should create focused commits/PRs according to team workflow, using this document as the explicitly requested implementation specification.
- Before implementing, re-fetch current code and tests and explicitly map each work package to symbols, migrations, API contracts, browser assets and regression tests. Keep changes focused and staged.
- Maintain AGENTS.md non-negotiables: user/owner content isolation, encryption under user DEK, Vault cleanup, provider secrets, redaction/generation snapshots, clinical safety, quota/outbox and audit metadata.
- Do not auto-merge or open a production rollout. Keep this document current as WP0's timing findings, final API/schema design, actual tested OS/browser support, test results and clinical-safety release evidence are produced. Phase 2 WhatsApp native audio remains a required separate workstream.
