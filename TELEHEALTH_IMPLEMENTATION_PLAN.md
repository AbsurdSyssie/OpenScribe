# Telehealth dual-channel capture — implementation and decision plan

**Status:** DRAFT — product and architecture decisions outstanding; do not treat this file as authorisation to implement.
**Branch:** feature/telehealth-dual-channel-capture (branched from master at bf63c2879dd8526db582a9598663db92df13642b on 2026-11-04).
**Owner:** OpenScribe maintainers. **Scope:** One clinician-owned consultation in OpenScribe Scribe, with local microphone and remote-call/tab audio kept separate for transcription and mapped into one ordered structured transcript.
**Plan authority:** This document was requested explicitly in the repository root. Repository AGENTS.md normally prefers plans/; the root placement is intentional.
**Source of truth:** Reconcile this draft with the current implementation, tests, migrations, and operational documentation before any code change. All TBD items below need explicit maintainer decisions. Do not infer approval from recommendations.

## Maintainer decisions — round 1 (2026-11-04)

These decisions supersede any earlier "candidate" recommendations in this draft. Preserve their rationale during the next review.

1. **V1 is recorded capture with transcription after the consultation**. No simultaneous Deepgram live ASR sessions in the first release. Bounded uploads *during* recording remain a technical option if needed for memory safety, but no live result display is required.
2. **Browser-tab audio comes first.** Support for **WhatsApp native desktop application audio remains a required later phase**; it must not delay initial tab support, and does require a separate supported platform approach.
3. **Do not exclude STT configurations lacking timestamps.** Prefer real per-word/segment timestamps for chronological mixing. Without them, preserve source-attributed text using best-effort grouping; a transcript consisting of two blocks titled **Clinician:** and **Patient:** is acceptable. Never invent fine-grained timestamps/chronology or assume an LLM's inferred sequence is fact.
4. **Fixed v1 speaker labels:** microphone **Clinician**, remote call audio **Patient**. Manual reassignment is optional future work. These labels describe expected source roles, not guaranteed real-person identity.
5. **Capture-source loss must produce an unmistakable warning directing the clinician to restart.** Exactly what stops, what recorded content is retained and how to restart require further decisions; no silent one-source fallback.
6. **Separate, subtle Telehealth button/toggle near recording controls**, not a third option inside the Recorded upload / Live capture dropdown. A **per-user show/hide preference** is desired for consideration; exact default, UX and persistence require sign-off.
7. **Original shared sample clock remains mandatory before VAD**, with per-channel source offsets, independent VAD and independent ASR metering. Existing batch VAD concatenation loses silence offsets and cannot be reused unchanged.

**Still unresolved:** exact failure/restart state transitions, persistent interruption markers, incomplete-generation warning/acknowledgement contract, per-part mapping and flat-text assembly, provider diarisation, overlap handling, exact toggle/meter details, consent and release matrix. See round-2 decisions and the decision register below.

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

## 2. Telehealth control and capture UX — partially decided

**Confirmed:** Telehealth is a **separate subtle button/toggle** by the recording controls, NOT a third recording-mode dropdown option. The existing recorded upload / live capture selection and backend `whole_file` / `live_chunked` modes must not be silently redesigned. V1 telehealth is two-source recorded capture with **post-consultation ASR**, likely using whole-file ingestion plus a new server-owned channel/session contract; the concrete ingestion design is still open.

**Preference:** implement an **owner-scoped show/hide Telehealth control** setting, shown by default. Existing `app/static/js/transcribe/app.js` sends full saved preferences via `persistUserAppPreferences`; `app/services/preferences.py` and `app/schemas/preferences.py` whitelist fields. A new preference needs coordinated schema, normalisation, persistence and client changes; decide default visibility and discoverability before coding.

**Browser first:** after an explicit Start action, request remote tab audio via `getDisplayMedia` and local microphone via `getUserMedia`. Video is required as a browser capture-request parameter but must never be processed or stored. Verify an actual remote audio track exists, show each active channel and any dropout prominently, and direct the user to restart when capture is compromised. Exact recovery policy is still undecided.

**Required future phase:** WhatsApp **native desktop application audio**, with separately scoped platform capture and release testing. Do not promise OS-wide app capture in tab-only v1.

**Open questions:** final small phone-icon toggle appearance and tooltip, dual visualiser five-second speech milestone and collapse animation, which recording modes can coexist, startup permission handling, when source selection can change, exact partial-audio recovery and tested browser/OS matrix. See proposed UI concept in the round-2 decision record.

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

**Option B: coordinated recorded two-channel batch (v1 candidate).** Post-stop transcription is approved. Assess whether bounded parts still need uploading during recording as the current batch recorder rolls over before stop. Persist the two independently submitted files/jobs; only commit ordered source after both have terminal results, with explicit partial-result recovery. Avoid assuming the existing whole-file jobs can be uploaded concurrently without lifecycle changes.

**Option C: two Deepgram live sockets/streams (DEFERRED beyond v1).** Requires deliberate redesign of TranscriptStreamSession uniqueness, source-sample accounting, server relay, checkpoint/commit order, per-channel recovery windows, browser reconnection and finalization. This is a separate work package, not an incidental enhancement to Option A.

**Option D: provider-native multichannel audio in one request (DEFERRED).** Potential later optimization only; cannot be the generic solution while OpenScribe supports multiple STT providers and its current Deepgram protocol is mono. Must preserve source-channel separation and accounting if added.

**Strong constraints for all options:**
- No new broad admin/leader access to content; per-user DEK encryption for new text/turns and bounded Vault temporary audio.
- Server-authoritative capture mode, session ownership, provider snapshot, quota reservations/settlements and response validation; no trust in 'clinician' role field or client-supplied durations for billing.
- Idempotency and retry per *channel + original interval*; correct ordered application after retries; no duplicate turns; clear expiry/deletion.
- Maintain immutable transcript versions, saved generation snapshots, structured edits and per-version redaction offset mapping. Never overwrite edited turns when a late channel completes; define read-only capture interval, incremental draft vs committed versions and conflict resolution.
- Preserve provider costs/usage by job attempt, distinct metrics for session elapsed time, per-channel received/captured time, per-channel VAD speech duration, actual transmitted duration and provider-metered/billed duration. Do not infer billed duration from VAD speech alone.
- **Approved fallback:** an STT provider without timings is supported. Use evidenced chronology where available; otherwise keep separate `Clinician:` and `Patient:` source-labelled blocks and never fabricate precise timestamps or clinical conversation order.
- Avoid conflict with current 'one stream per transcript' and one global next-live-sequence barriers without an approved migration. Any migration must handle old data and downgrade policy safely.
- For transcript display and generation, fixed v1 source labels are **Clinician** (local microphone) and **Patient** (tab/call audio). Keep provider diarisation separate and do not claim the remote channel proves actual speaker identity.

## 5. Decision register (maintainer answers dated 2026-11-04)

**DECIDED** items are approved scope; **PARTIAL** means the product direction is known but agents must not assume implementation specifics; **OPEN** requires the next maintainer interview.

| ID | State | Decision / question |
| --- | --- | --- |
| D1 | **DECIDED (round 2)** | V1 two recorded sources and post-consultation ASR. **Bounded audio-part uploads during capture are approved** for memory safety. Live dual-ASR streaming is deferred. |
| D2 | **DECIDED, matrix OPEN** | Browser-call **tabs first**. **WhatsApp native desktop app audio is a required later development phase**. OPEN: supported v1 browser/OS matrix; future platform adapters. |
| D3 | **DECIDED (round 2), adapter mechanics OPEN** | Providers without timestamps remain eligible; merge only at evidenced chunk/segment granularity and otherwise show **Clinician:** / **Patient:** blocks. Generation must preserve role provenance and acknowledge unknown chronology. |
| D4 | **DECIDED** | Always display **Clinician** for mic and **Patient** for remote audio in v1; later manual relabeling optional. These are provenance assumptions, not verified speaker identity. |
| D5 | **OPEN** | Run provider diarisation within channels or rely solely on two fixed labels? How should multiple remote voices be displayed? |
| D6 | **DECIDED (round 2), technical details OPEN** | **Stop compromised dual-source capture, warn immediately, preserve all already captured/recoverable audio, and allow a new run in the same consultation.** No silent mono fallback. Define interruption markers and interrupted-upload/retry races; obey existing retention/deletion. |
| D7 | **DECIDED (round 2), warning UX OPEN** | **Allow generation from incomplete transcript after a prominent warning and acknowledgement.** Missing channel/segments must remain distinguishable from a merely untimed-but-complete result; no false full-coverage claim. |
| D8 | **OPEN** | Timing precision target, both VAD settings/pre-roll, original-frame mapping, trim and resampling drift. Ensure independent silence suppression doesn't destroy shared timeline. |
| D9 | **OPEN** | Timed overlapping utterances/ties and fallback presentation when only flat text is available. Proposal: preserve both with deterministic stable order when timing is known, no fabricated order otherwise. |
| D10 | **DECIDED (round 2)** | Meter each channel independently; **apply provider limits to aggregate submitted/metered channel-minutes**. Consultation wall-clock duration stays distinct. Exact usage presentation remains a UI choice. |
| D11 | **OPEN** | Telehealth toggle on existing consultation, coexistence with ordinary modes/recordings, repeated restarts and capture-session scopes. |
| D12 | **PARTIAL (round 2 + new concept)** | Subtle separate control **beside recording**, **shown by default**, **per-user hide preference**. Enabling recorded capture may require mode confirmation. Latest concept: **icon-only phone toggle** with tooltip; two temporary Clinician/Patient level bars, five seconds cumulative voiced activity per source, green feedback then one combined visualisation; retain separate health indicators and reopen on fault. Treat exact animation/status palette as unapproved prototype, not settled spec. |
| D13 | **DECIDED baseline, guidance details OPEN** | **No automatic cross-channel deduplication for v1.** Recommend headphones/echo cancellation; first-use modal/guidance for telehealth and optional per-user acknowledgment/preference. Still test for speaker bleed and duplicate clinician/patient turns. |
| D14 | **OPEN** | Recording notice, consent/objections, clinical safety review, deployment/provider policy, retention/export, staged rollout. |

**Next interview priorities:** D12 phone toggle and short dual-bar verification/collapse animation; D6 interrupted-track warning, preserved-audio restart and gap markers; D7 exact incomplete-note acknowledgement; D5 diarisation and multi-speaker cases; D8 timing accuracy; D14 clinical governance and rollout. Record answers with dates; request sign-off before proceeding to code. Unresolved details remain decision gates.

## 6. Proposed agent work packages (not authorised until dependent decisions are resolved)

### WP0 — Evidence and timing prototype
- Examine pinned vad-web behavior, independent VAD fed from AudioContext/AudioWorklet tracks, original sample offset tracking, pause/restart, device changes, forced flush and overlap. Audit **existing batchSpeechSegments concatenation and rollover during capture**: the current voice-only batch recorder drops silence offsets, and long sessions may upload parts before Stop. Establish a bounded-memory two-channel design even though STT results are post-consultation.
- Produce synthetic timing probes: silence removed differently on each channel, concurrent speech, trim/pre-roll, long recordings and reconnect. Confirm whether provider output offers suitable timestamps under approved STT configurations.
- Deliver capability matrix and measured timing/CPU/memory findings before choosing a capture API design.

### WP1 — Capture UX and two-channel lifecycle
- Add a **separate subtle Telehealth toggle/button near recording controls**, preserve the existing split-menu options and preferred recording mode semantics; implement optional owner-scoped show/hide preference only after D12 resolution.
- Build a small, well-owned capture module with two source streams, one common clock, per-channel VAD, explicit teardown, stop/restart/disconnect and reliable metadata. Keep native OS audio adapters out of scope unless authorised.
- Test permission revocation, hidden tab, interrupted media track, startup races, late async returns, stopping during prompts, browser refresh, navigation and sign-out. No user content in Web Storage or logs.

### WP2 — Secure server capture/session model and ingestion jobs
- Design schema/migrations to group channel jobs into the same consultation session, preserve per-source sample mappings, maintain per-channel stable ordering and finalization barriers.
- Reuse existing request validation, idempotency, team STT snapshots, Vault source-audio retention, retries, rate limits, owner authorization, quota/outbox, audit and expiry; do not silently loosen unique constraints.
- Enforce bounded metadata and safe per-source failure/recovery. Update route audit if routes added. Adapt source-duration rules to measured rather than client-declared audio.

### WP3 — ASR adapter timing and transcript merge
- Provide normalized timed segments where the STT provider supports them, independently of diarisation. Also **support flat-text outputs** without inventing word timings: label one or more source-text blocks unambiguously as **Clinician:** / **Patient:** and ensure note-generation source text preserves both.
- Convert available ASR-relative offsets through the exact speech-segment-to-consultation mapping and stable-merge timed outputs, retaining overlapping words/turns, fixed source labels, turn UUID stability, version/PII/redaction/generation mapping and edits. For untimed flat results, output distinct **Clinician:** / **Patient:** blocks instead of false chronological merging.
- Enforce channel watermarks: a quicker completed channel must not permanently commit newer text before an earlier region from the other channel is resolved. Retries must not duplicate turns.

### WP4 — Test, safety, operational docs and staged rollout
- Focused unit tests for source-frame timing, VAD trimming/pre-roll, ASR timing offset calculations, staggered completion, track loss, source mismatch, duplicate/idempotent submissions, retry barriers, provider/quota cases, retention, deletion, encryption and cross-owner authorization.
- Browser E2E tests of split-button, source consent/permissions, call-tab selector, recording indicators, recovery and a real two-track synthetic conversation. Test supported browser/OS combinations with speaker leakage/headphones and long-run timing.
- Update docs/transcript-capture.md, docs/live_stt.md, docs/api.md, docs/stt-config.md, docs/testing.md as relevant, SAFETY.md/hazard case, tests and docs index. Use synthetic data only. Run relevant automated tests plus operational-doc/route audits.

## 7. Acceptance criteria (draft)

1. Clinician uses a **separate subtle Telehealth icon toggle beside recording**, shown by default and optionally hidden through an owner-scoped preference; existing mode dropdown remains unchanged. Both sources are visible during initial verification and their independent health remains observable after any meter collapse.
2. Local mic and remote tab audio stay independent through capture and ASR; no video or unintended system audio is sent/stored.
3. An original consultation-time speech map survives independent VAD silence suppression, pre-roll, trimming, resampling and overlap. Test evidence demonstrates deterministic chronological reconstruction.
4. Two ASR jobs may finish in reverse order; with supported timings, the merged owner-only structured transcript is correctly ordered and retains overlapping utterances, with source-derived **Clinician/Patient** labels and no duplicates on retry. Without timings, maintain two explicitly source-labelled blocks; do not invent chronology.
5. Non-timestamped provider results are permitted via `Clinician:` / `Patient:` fallback blocks. Missing audio, source interruption, gaps or ASR failures have explicit warnings and preserved recoverable audio; **incomplete-note generation is allowed only after an explicit warning/acknowledgement**, without claiming that capture was complete.
6. Existing microphone-only whole-file, live-chunk and Deepgram stream/dictation paths continue to behave identically; all existing authorization, encrypted persistence, redaction, versioned edits, generation, quotas, retry, retention and deletion controls hold.
7. Usage accounts for captured/submitted/provider-metered seconds independently by channel and combined for STT quota/billing; consultation wall-clock duration is distinct. Bounded uploads during recording must preserve sample-to-original-clock mapping.
8. No medical/patient details or raw audio in logs, provider metadata, browser storage or test fixtures. Review clinical safety and disclosure text before release.
9. V1 does not require dual Deepgram live sockets. WhatsApp **native app** capture is an explicit later-phase deliverable with separate platform feasibility/testing; it is not silently dropped.

## 8. Implementation guardrails and handoff

- **STOP:** No agent should implement an architectural choice covered by unresolved D1–D14 without specific maintainer approval. Purely read-only exploration and synthetic proof-of-concept branches are acceptable if requested separately; do not merge them into this feature branch as implicit policy.
- **NO code yet on this branch.** This initial commit contains only this planning Markdown file.
- Before implementing, re-fetch current code and tests and explicitly map each work package to symbols, migrations, API contracts, browser assets and regression tests. Keep changes focused and staged.
- Maintain AGENTS.md non-negotiables: user/owner content isolation, encryption under user DEK, Vault cleanup, provider secrets, redaction/generation snapshots, clinical safety, quota/outbox and audit metadata.
- Do not auto-merge or open a production rollout. Update this file with decisions and a dependency-ordered executable implementation plan before creating code work items.
