# Deepgram live connection continuity plan

Status: implemented on 22 October 2026; independent review and verification recorded below.

## Goal

Keep one Deepgram Listen V1 WebSocket open for the lifetime of an active live recording, including while the browser is paused or no audio is being sent. Keep the existing bounded transcript, quota, and recovery checkpoint contracts. Close the provider connection on explicit Stop or when the stream fails. This work is limited to Deepgram live connection lifecycle; diarisation configuration, speaker parsing, transcript presentation, and generation prompting remain separate work.

The user approved KeepAlive through silence while recording is active and approved an exceptional CloseStream/drain followed by a fresh connection when a checkpoint cannot be proven finalized. This fallback can reset speaker identity in that exceptional case. It is an exception path, not a scheduled 30-second rotation.

## Baseline current behavior before implementation

- `app/routes/deepgram_live.py` calls `CloseStream`, waits for Deepgram Metadata, exits the provider context, commits the current window, and opens a new provider context for the next audio. The same checkpoint runs on pause, Stop, a 30-second window, and a quota-bucket boundary.
- `app/services/transcript_streaming.py` caps each window at 30 seconds of forwarded PCM. Durable delivery claims, the current window, and provider quota attempts are recorded before media is sent. `commit_stream_window` settles the submitted attempts and appends the finalized text under the owning user's encryption boundary.
- On failure, only the unresolved bounded window can be recovered. Recovery audio must match the exact durable delivery digests. No audio is automatically replayed.
- `DeepgramLiveStream` has automatic SDK reconnect disabled. It supports KeepAlive, Finalize, and CloseStream. Receive timeouts are re-armed by the relay.
- A persistent provider connection has one audio-relative clock. Deepgram `start` and `duration` must therefore be interpreted against cumulative forwarded samples, not offset by the already committed sample count. Browser source offsets can have gaps; provider sample time counts only media actually sent.

Relevant maintained contracts are in `docs/live_stt.md`, `docs/testing.md`, `docs/dbtesting.md`, and `docs/transcribe-playwright-checklist.md`.

## Intended design

1. Keep the adapter context and socket alive across successful 30-second, pause/resume, and quota-boundary checkpoints. Keep sending KeepAlive while a recording remains active and no audio is sent. Avoid opening a provider socket before the first authorized audio batch, as today.
2. Retain the 30-second maximum unresolved-audio window and the current durable delivery/idempotency, quota, and encrypted-text commit services. A routine checkpoint freezes authorization/sending at the current window boundary, sends Deepgram `Finalize`, and drains results without closing the socket.
3. Freeze `connection_base_samples` when a provider socket opens. Convert every Deepgram `start`/`duration` to the connection-global clock as `connection_base_samples + timestamp_samples`. Advance the base only when an exceptional fallback closes that socket and a later socket opens; never advance it at an ordinary checkpoint. Keep durable cumulative provider sample counters unchanged. Browser source offsets may contain gaps and remain separate from the provider clock.
4. Before sending `Finalize`, a validated final-interval coverage proof may complete a checkpoint without creating acknowledgement debt. If coverage is incomplete, send `Finalize` and then require a valid positive-duration `is_final` Results event with `from_finalize=true` before reusing that same socket; its text may be empty. Coverage arriving after `Finalize` does not discharge the acknowledgement debt. Do not clear or reset an outstanding-finalize flag until the corresponding acknowledgement is consumed. A stale/already-committed event cannot acknowledge a later window; a zero-length marker is ambiguous and must fail closed.
5. Keep provider control writes bounded to 1 second each, routine `Finalize` acknowledgement/drain to 2 seconds, exceptional CloseStream drain to 2 seconds, and adapter connection/HTTP cleanup to one shared 2-second budget. Send KeepAlive at least once per second while waiting and cap each KeepAlive write at the remaining checkpoint deadline. Provider signaling/draining is bounded to under 6 seconds and socket/client cleanup to at most 2 additional seconds; established owner rechecks and durable commit/ack work remain separate. Raise only the browser outbound queue cap from 5 to 10 seconds to absorb audio during provider checkpoint work; preserve the five-second pre-roll ring and retain a hard outbound bound. The browser still has its 30-second finalization deadline. A timeout, silence, or unproven interval is never success; an empty-text positive-duration final marker can prove completion, but a zero-length marker cannot. If CloseStream drain fails, retain existing manual failed-window recovery; never replay uncertain audio.
6. On successful routine checkpoint proof, commit and settle the whole window through the existing transaction, then send `window_committed`, clear only that window's audio/results, and resume on the same socket. Preserve browser in-flight/`window_committed` semantics. A `paused` control is a non-closing checkpoint; keep the same socket active with KeepAlive during pause. Stop drains and closes the existing context once. If Stop arrives while a socket exists but no audio is active, close it once without a phantom commit.
7. On the exceptional fallback, send CloseStream and drain through Metadata within 2 seconds, commit the exact frozen window, close that context, and reopen on demand for subsequent authorized media. The next socket's connection base is the current cumulative provider sample count. Never replay audio. The existing 30-second accounting window remains; quota rollover must checkpoint and settle the prior day's actual work before authorization for the new period. Do not introduce variable-prefix quota accounting.
8. Do not change transcript ownership, encryption, the root retention lifecycle, credential lookup, durable dispatch ordering, quota reservation/settlement semantics, Vault recovery cleanup, or diagnostics. No transcript text, audio, raw provider response, or credentials enter logs. No migration is expected if current contracts suffice; reassess only if implementation demonstrates a schema requirement. The WebSocket route is not an `/api/v1` HTTP route declaration; run the API audit if route declaration or authorization behavior changes.

## User-visible behavior and limits

Routine checkpointing must not close the Deepgram socket, including during a pause or silence. An exceptional unprovable checkpoint may close and reopen it under the user's decision. Speaker continuity is expected across normal checkpoints and silence, but cannot be promised across that exceptional fallback or an upstream/network failure. This task does not add diarisation or persist speaker labels.

Deepgram's official documentation describes `is_final` as a finalized transcript segment and `Finalize` as a flush control whose completion marker is not guaranteed for insignificant buffered audio. The design must not treat no response as proof. See [Finalize](https://developers.deepgram.com/docs/finalize), [endpointing and interim results](https://developers.deepgram.com/docs/understand-endpointing-interim-results/), and [KeepAlive](https://developers.deepgram.com/docs/audio-keep-alive).

## Implementation boundaries

- Route lifecycle and sample-clock/result bookkeeping: `app/routes/deepgram_live.py`.
- Adapter control behavior only if needed: `app/services/deepgram_streaming.py`.
- Durable accounting/lifecycle service should remain unchanged unless code proves it cannot represent a valid bounded checkpoint: `app/services/transcript_streaming.py`.
- Replace route tests that assert routine connection rotation with tests for same-socket checkpointing, KeepAlive during silence/pause, cumulative provider timestamps, quota-boundary checkpoints, explicit Stop drain, uncertain-finalization fallback, and no replay. Extend adapter fakes/tests for Finalize and CloseStream ordering.
- Update maintained live-STT behavior and focused verification instructions/checklist. Do not edit diarisation-owned files.
- Use synthetic provider events and audio only. Do not make real provider calls.

## Acceptance criteria

- A long synthetic recording crosses multiple accounting windows and continues on one provider socket; the socket receives each authorized PCM interval once.
- Pause/resume and silent periods keep the same socket alive through KeepAlive while the session is active.
- Deepgram timestamps remain correctly ordered across windows, including ignored already-committed results and source-offset gaps.
- A routine checkpoint persists only finalized text, settles quota in the existing durable transaction, and acknowledges only after commit; a `window_committed` message follows that commit.
- Explicit Stop performs final drain, commits before `finalized`, and closes the provider context once.
- Pre-Finalize validated coverage can prove completion. After Finalize is sent, no coverage-only result, stale marker, zero-length marker, timeout, or silence is misclassified as successful acknowledgement; an empty-text marker is accepted only with a positive-duration interval inside the frozen window.
- The chosen exceptional-checkpoint behavior is covered and documented; errors preserve manual recovery and never trigger automatic audio replay.
- Existing exact recovery matching, owner-only access, encryption, retention, duplicate delivery claims, quota, and sanitized logging contracts remain intact.
- Focused sequential tests from `docs/testing.md` pass. Run the API authorization audit if route declarations or authorization behavior change.

## Risks and review points

- The checkpoint proof rule must match the SDK's event ordering and `from_finalize` behavior. Deterministic fakes verify repository contracts; they do not replace deployed-provider acceptance testing.
- Long-lived connections can fail independently of our checkpoints. The existing no-auto-reconnect policy and manual recovery behavior must remain clear.
- Receive-side silence is expected; timeout handling must continue to re-arm reads without treating timeout as provider failure or checkpoint proof.
- A separate Sol 6.1 review should check timestamp mapping, checkpoint proof, failure/recovery behavior, and quota-boundary handling before implementation is considered complete.

## Implementation and verification record

Implemented on 22 October 2026. No migration or runtime configuration change was needed. The HTTP API route manifest was not changed because this work did not change route declarations or authorization behavior.

- Focused sequential command from `docs/testing.md`: 103 passed, 3 dependency deprecation warnings, including the UTC timestamp regression check.
- Independent Sol 6.1 review: approved with no blockers on 22 October 2026 after the logging timestamp and plan/documentation corrections.
- `APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app .venv/bin/python scripts/audit_api_auth.py`: exit 0.
- `.venv/bin/python .github/scripts/check-operational-docs.py`: checked 54 maintained documentation files successfully.
- `git diff --check`: passed.
Repository tests use synthetic provider events and do not replace an acceptance check against the deployed Deepgram service. An exceptional unproven checkpoint may reset provider speaker identities; this work does not add diarisation.
