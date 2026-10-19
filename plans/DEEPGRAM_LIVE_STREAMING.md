# Deepgram live streaming

Status: implemented and synthetically verified, 17 October 2026. The approved design includes recovery-only audio retention and a 30-second silence pause with a five-second rolling buffer. Maintained current behavior is documented in [live_stt.md](../docs/live_stt.md).

## Outcome

When the selected live STT provider is eligible Deepgram, the owner can record through a server-side Deepgram Listen V1 WebSocket and see interim transcription while speaking. The server saves only finalized segments. File uploads retain the prerecorded REST path. Other providers retain their existing capture and ingestion behavior.

Use the maintained Deepgram Python SDK behind an application adapter. Preserve the saved provider host, model, language and supported options, Vault credential resolution and mandatory `mip_opt_out=true`. No provider credential or unrestricted Vault reference enters the browser. No regional/model fallback occurs without explicit selection.

## Why the existing job cannot be relabelled as a stream

Current admission measures a completed audio clip, stores retry audio in Vault and commits the ingestion job, exact audio-second reservation and outbox together. The worker claims that job and marks its attempt submitted before the provider request.

Executable boundaries:

- `app/services/transcripts.py::queue_audio_chunk_ingestion`: server measurement, 30-second clip bound, hourly cap, Vault source, selection snapshot and durable queue.
- `app/services/quotas.py::reserve_provider_attempt`: audio requires positive measured duration and `reserved_units == ceil(measured_audio_seconds)`.
- `app/services/quotas.py::increase_provider_attempt_reservation`: existing growth is for token reservations; audio growth is rejected.
- `app/models.py::ProviderAttempt`: resource/state/settlement constraints enforce the current measured-audio contract.
- `app/services/quota_lifecycle.py` and `quotas.py::reconcile_provider_attempts`: recovery settles submitted audio against its persisted measured duration.
- `app/services/task_outbox.py`: existing dispatch maps durable identifiers to queued tasks.

A stream receives more audio after its first provider submission. A maximum future duration must not be written into a field representing already measured audio. A stream also needs a distinct rule for a crashed connection: ordinary ingestion can retry a complete saved clip, while replaying an uncertain live interval could duplicate content and provider cost.

## Recommended architecture decision

Implement the approved Deepgram-only durable stream-session lifecycle with **incremental server-measured audio admission and bounded recovery windows**, while preserving existing clip-job and token-attempt behavior. The direct ASGI relay owns provider connections. Its new durable delivery ledger is an explicit extension of the dispatch architecture; existing Celery outbox dispatch remains unchanged.

1. Create the transcript root first. Create one owner/transcript-bound stream session with a frozen eligible Deepgram configuration and an exclusive connection lease. A second connection cannot forward concurrently for that session/transcript.
2. Resolve the required credential before the submitted boundary. Authenticate the owner and validate the active, retained transcript before any audio goes to Deepgram.
3. Receive bounded mono 16 kHz, signed 16-bit little-endian PCM frames. Assemble small bounded dispatch batches; a target of at most one second avoids per-sample database/Vault work while retaining near-live updates. Derive duration from validated sample counts, not a browser duration claim.
4. Use ordered recovery windows of at most 30 seconds of forwarded media. The browser and relay hold only the current bounded window's audio until sealing. At sealing, use bounded `CloseStream` final draining through summary `Metadata`, then commit encrypted final text. Discard successful audio after the commit acknowledgement: the normal path writes no audio to Vault. Open a fresh provider connection only for subsequent new audio, within the same logical browser session and fenced accounting lifecycle. Never replay old audio automatically. A transport-received acknowledgement is not durable audio acceptance. On failure only, retain available bounded recovery audio through the established Vault/cleanup service. Keep its original server-owned deadline, capped at 24 hours and by transcript retention; never reset it on retry. A lost tab/process may lose uncommitted audio; expose the unresolved interval explicitly.
5. Before each dispatch batch can reach the provider, serialize owner/root/session/window/attempt accounting, check daily/monthly and rolling-hourly limits, and commit its sample-count authorization plus durable delivery claim. Store authorization/received/dispatch-authorized sample counts in explicit stream fields; do not repurpose the existing clip `measured_audio_seconds` field as a future allowance. A scoped stream-attempt kind and additive state/constraint changes permit incremental authorization while submitted. Existing clip reservation APIs and reconciliation retain their contract.
6. The direct relay uses a committed per-batch ledger and an exclusive, fenced connection lease before forwarding. Reuse existing locking, owner validation, quota and cleanup services; the existing task outbox does not publish WebSocket frames. Ledger states distinguish admitted, dispatch-authorized, local-write-completed, and checkpoint-committed data; a completed local write does not prove provider receipt. A process restart reconciles the ledger and closes the session without silently recreating a provider connection or replaying uncertain audio.
7. Record actual received and dispatch-authorized sample counts durably and separately. Normal checkpoint settlement uses measured audio; ambiguous dispatch settles conservatively against the durable dispatch-authorized count with an explicit streaming settlement basis. Never assume an unacknowledged network write proves that Deepgram received no audio. Hourly capture allowance counts each unique admitted source interval once, including active windows, while explicit provider replay consumes a new daily/monthly provider attempt. KeepAlive messages do not count as audio. Forwarded silence is audio and participates in accounting.
8. Assemble validated provider-final segments with stable session/window-local identities. Show them while recording, but commit canonical text atomically per sealed recovery window so an interrupted window cannot partly replace already committed text. Require the `CloseStream` final drain before sealing. Provider timing values must be finite and bounded by the authorized media range, with each provider connection's time origin mapped to its logical window. Persist neither interim hypotheses nor provider debug payloads in usage, attempt, audit or delivery metadata.
9. Display interim text only as replaceable, owner-visible, volatile UI state. It never becomes a source for note generation until finalized and committed into the transcript.
10. Stop local capture on connection failure. Preserve committed text. For the current uncommitted window, use the browser's bounded copy or available retained failed source for an explicit owner retry through the existing prerecorded recovery path, with a fresh accounted attempt; never replay it automatically. If the tab/browser is lost, its uncommitted audio may be lost too, and the durable failed-window record must expose that gap. An unresolved window is a sequence barrier until explicit recovery or acknowledgement/discard. A retry must not replace an already committed window.
11. On Stop, stop capture, drain authorized audio, send `CloseStream`, and wait for final results/metadata within a bounded deadline before sealing the last window. Do not treat a local socket close or `speech_final` alone as complete stream finalization. Keep generation blocked while stream finalization or an unresolved interval remains active.
12. Deletion, retention expiry, sign-out/session revocation and loss of authorized owner/team scope prevent further provider audio and content delivery. Close resources promptly and retain durable cleanup/reconciliation for outstanding records.

No global “infinite” reservation, fabricated duration, raw key in the browser, silent model/region substitution, or memory-only authoritative dispatch state is acceptable.

### Silence pause and pre-roll

Use the existing single browser microphone/VAD pipeline. Initially listen locally with a five-second circular PCM buffer. On detected speech, send its unsent pre-roll once, then each newly captured frame. After 30 seconds since the last voiced frame, pause binary audio transmission and seal any pending window. The microphone and VAD remain active; the UI must say it is listening locally rather than muted. Resume by flushing only unsent samples from the latest five seconds into a fresh provider connection, then continue live frames. KeepAlive messages every four seconds cover temporary inactivity within an open window; sealed pauses hold no provider connection. Never also send VAD's accumulated `onSpeechEnd` copy.

Protocol reconciliation during implementation: official documentation does not guarantee an empty acknowledgement for `Finalize`, and silent audio need not produce a textual interval covering its duration. Requiring textual coverage would falsely fail silent windows; assuming a timeout means success could lose speech. The documented `CloseStream` final drain and summary provide the checkpoint boundary. Connection rotation is therefore a provider lifecycle detail of these bounded windows, preserving live result delivery and avoiding uncertain replay.

Track monotonic source sample offsets separately from contiguous provider-media offsets. Source offsets can skip locally discarded silence; already forwarded samples cannot overlap or replay. Cached-but-unsent audio and KeepAlive messages consume no provider audio quota. Forwarded silence, including the 30-second tail and resumed pre-roll, does. Five seconds of 16 kHz mono PCM is 160,000 bytes. The five-second pause ring is separate from the bounded uncommitted recovery window. Neither buffer uses browser-persistent storage.

## Authentication and browser contract

Use a same-origin browser-to-application connection and a server-to-Deepgram connection. Reuse existing session resolution/full-auth and CSRF services. The browser WebSocket API cannot set `X-CSRF-Token`; adapt the handshake explicitly with exact Origin validation and verification of the existing session-bound CSRF token in the initial bounded message before accepting audio. Keep tokens out of URLs, subprotocol names, logs and diagnostics. Revalidate session/root authority during a long connection using short-lived DB sessions.

Expose a server-derived non-secret streaming capability to the workspace only for the eligible Deepgram live selection. The server re-resolves selection and enforces eligibility; the browser flag grants no authority. Do not infer Deepgram from its generic REST adapter name.

Browser states: connecting, streaming, finalizing, complete and failed. Bind callbacks and queued frames to the originating transcript/session. A transcript switch cannot redirect audio. Keep recording controls, status announcements and failure/retry controls accessible. Background/unload remains best-effort; do not imply that unsent browser-memory audio survives tab loss.

## Implementation partitions

- Provider adapter and synthetic protocol tests: new `app/services/deepgram_streaming.py`, dependency pin in `requirements.txt`, configured EU/global environment, raw PCM, safe error translation, KeepAlive/CloseStream and bounded cancellation.
- Durable lifecycle and accounting: transcript-owned stream/session/window/delivery persistence, additive Alembic migration and an explicit stream-attempt kind/basis, narrowly scoped streaming extensions to `quotas.py`, `quota_lifecycle.py` and transcript cleanup/status services. Existing `TaskDispatchOutbox` remains for Celery tasks; the direct relay gets a distinct committed delivery ledger and fencing epoch. Preserve existing clip jobs and token quotas.
- Owner transport and authorization: a focused route/service module, full-auth/session/Origin/CSRF checks, bounded input/output, regular authority revalidation, safe metadata-only diagnostics. Update the HTTP API audit manifest and add dedicated WebSocket authorization probes because the existing manifest inventories HTTP routes.
- Frontend: a Deepgram branch in `app/static/js/transcribe/media.js`, capability serialization/bootstrap in `app/web/transcribe_workspace.py`, `app/schemas/workspace.py`, `bootstrap.js`/`app.js`, and a volatile interim-transcript view in the existing workspace. Use existing audio primitives; do not add a second microphone capture session.
- Generation/lifecycle integration: ready/transcribing state, generation waiting, split-source readiness, finalization, retained-audio retry and deletion/expiry must account for active or unresolved stream intervals.
- Operations: same-origin WebSocket proxy support, connection/idle/handshake/finalization limits and worker ownership; update executable configuration and closest maintained operational docs together when settings change. Do not deploy as part of implementation.

Luna should own most implementation and focused tests, with partitioned file ownership. The parent reviews schema, authorization, quota/outbox and failure semantics before integration. Run shared-infrastructure tests sequentially.

## Acceptance and verification

Use synthetic PCM and mocked SDK connections only. No real provider call, microphone recording or private clinical data is required for automated checks.

- Region/model/language and opt-out match the frozen configuration; rejected/incompatible selection does not fallback or dispatch.
- No credential, session/CSRF token, audio, transcript, raw provider response or exception string appears in logs, ordinary metadata or task payloads.
- Anonymous, partial/onboarding, cross-owner, leader/admin metadata-only, wrong-Origin, missing/incorrect CSRF and revoked/expired session connections cannot send audio or read results. An expired/deleted root closes existing connections.
- Provider output can arrive before microphone Stop. Interim updates replace prior interim text; only valid final results change the encrypted transcript. Duplicate final delivery appends once; conflicting final intervals fail safely.
- No audio precedes durable quota authorization and delivery claim. Concurrent streams serialize limits and include active windows in hourly admission; replay counts once for unique capture allowance but creates new provider usage. Quota exhaustion stops forwarding; credential preflight failure consumes no quota; measured and unknown settlement follow the approved streaming policy.
- Committed text does not require retained successful audio. Failed retained sources have an original fixed cleanup deadline. Provisional audio and text are labelled as such. Replayed browser acknowledgements cannot create duplicate dispatch or content. Abrupt disconnect/process loss creates a visible recoverable or failed interval, not an automatic provider replay. Test the explicit loss case when an uncommitted browser buffer disappears.
- Silence gates at 30 seconds from the last voiced frame; the five-second ring remains bounded, resumes in source order without duplicate audio and consumes quota only when sent. KeepAlive does not count as audio. The UI distinguishes local listening from a stopped microphone.
- Graceful CloseStream drains final results. Timeouts, SDK errors, malformed messages, cancellation and empty/no-audio sessions release connections and reconcile durable state without hanging recording or enabling premature generation.
- Existing uploaded-file, other-provider live chunk, ordering/gap/retry, generation, ownership/encryption and transcript deletion/retention tests remain passing.
- Run migration/DB-constraint checks, focused quota/outbox/lifecycle/API tests, WebSocket auth tests and browser capture tests. Use `APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app`; follow `docs/testing.md` and `docs/dbtesting.md` and run API audit sequentially after pytest. Run maintained-doc validation.

## Official protocol references

- [Deepgram Listen V1 streaming](https://developers.deepgram.com/reference/speech-to-text/listen-streaming): raw media, interim/final events, opt-out, KeepAlive, Finalize and CloseStream.
- [Official Python SDK](https://github.com/deepgram/deepgram-python-sdk): maintained async Listen V1 connection API. Select a pinned compatible release and verify its exact API before implementation.
- [Endpointing and interim results](https://developers.deepgram.com/docs/understand-endpointing-interim-results): `is_final` segment completion versus `speech_final` utterance boundaries.
- [EU endpoint notice](https://developers.deepgram.com/changelog/2025/12/3): regional live endpoint. Retain the saved regional host.

## Implementation and verification

Three Luna agents implemented the provider adapter, durable accounting/recovery, browser capture, documentation and most tests. The parent integrated the authenticated relay and reviewed authorization, quota, retention, cleanup and failure semantics. Review corrected quota-period checkpointing, orphan audio cleanup, retry deadlines, teardown replay, and the pinned VAD's otherwise unbounded internal speech accumulator.

The implementation adds migration `d3e4f5a6b7c8`, pins `deepgram-sdk==7.13.0`, and bounds WebSocket messages/queues in the SDK adapter and persistent runtime entrypoint. Existing clip and token quota contracts remain intact. Successful audio is not written to Vault; only exact failed-window recovery audio is eligible for retention. A pinned-runtime adapter bounds VAD history without resetting speech detection. The relay reuses the existing owning-user crypto cache within a checkpoint, expiring its use after 30 wall-clock seconds and clearing it on checkpoint/close.

Final targeted verification: **297 passed**, three third-party deprecation warnings, in 35.54 seconds. This included new streaming/SDK/authentication/browser/VAD/teardown tests, quota/outbox/ingestion/provider-bound/runtime/workspace regressions, focused existing API capture tests, and migration-head checks. The standalone API authorization audit passed, including both new HTTP recovery routes. Dependency compatibility (`pip check`), JavaScript/Python syntax, shell syntax, diff whitespace and maintained-document validation also passed.

No external Deepgram call, real microphone test or deployment was performed. The SDK connector hook and pinned VAD buffer integration have compatibility follow-ups in `DISCOVERED_ISSUES.md`.
