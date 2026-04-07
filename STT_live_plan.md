# STT Live Plan

## Goal

Make live transcription support two distinct provider classes cleanly:

- batch-oriented HTTP STT endpoints that accept bounded audio uploads
- true websocket streaming STT providers that can handle continuous live audio transport

The current system is optimized for the first class. The next architecture step is to make that explicit instead of forcing every provider through the same chunking assumptions.

## Current situation

Today, live transcription is implemented as:

- browser microphone capture
- browser-side VAD chunking
- owner-only upload to `/api/v1/transcripts/{id}/audio-chunks`
- sequence-aware backend application into the transcript draft

This works for HTTP batch-style STT providers, but it has two important limitations:

1. The app currently treats live STT as if every provider wants the same bounded chunk model.
2. The server has a hardcoded live chunk duration guard in [app/services/transcripts.py](/home/oscar/Documents/Code_Projects/OpenScribe/app/services/transcripts.py), which is a policy choice rather than a universal technical requirement.

## Why the current 30-second server limit should change

The existing `declared_duration_seconds > 30` rejection is:

- an application-level guardrail
- not a browser/VAD requirement
- not necessarily a real provider limit

It was added to keep the current HTTP live chunk flow predictable, but it is too rigid as a general live STT contract.

The correct model is:

- keep a server-side notion of provider capabilities
- keep client behavior conservative for batch-style providers
- avoid enforcing one arbitrary chunk duration across all STT transports

## Provider transport split

We should explicitly model STT transport capability.

Recommended transport categories:

- `http_chunked`
- `websocket_streaming`

Optional future refinements:

- `http_chunked_batch`
- `http_chunked_longform`
- `websocket_streaming_partial`

## Recommended STT config fields

Add explicit non-secret transport metadata to STT configs, for example:

- `transport_kind`
- `max_chunk_duration_seconds`
- `supports_partial_results`
- `supports_continuous_stream`

These are capability fields, not secrets, so they are safe to expose to the owner browser where needed.

## Behavior by transport kind

### `http_chunked`

This is the right mode for custom STT services that behave like batch transcription APIs.

Expected behavior:

- browser uses VAD to create chunks
- browser keeps overlap across forced boundaries
- browser flushes before a provider/profile max, not at the exact limit
- browser uploads sequenced chunks over HTTP
- backend applies chunks in sequence to the draft

Recommended client policy for this mode:

- use a conservative hard cutoff such as `25s`
- keep overlap around `800ms`
- keep pre-roll around `800ms`
- keep the current `2s` silence endpointing unless product requirements change

### `websocket_streaming`

This is the right mode for providers that can handle genuine live transport.

Expected behavior:

- browser opens a websocket or equivalent continuous transport
- browser streams frames or micro-batches directly
- VAD may still be useful for UX state, local indicators, or optional utterance boundaries
- the transport should not rely on hard `25s` or `30s` chunk resets as a core mechanism

For this mode, forced chunking should be minimized or removed unless the provider protocol requires periodic restart.

## Near-term implementation plan

### Phase 1: Make transport explicit

Add STT capability metadata and keep all existing providers on `http_chunked`.

Deliverables:

- DB migration for STT transport metadata
- admin UI support for transport kind
- service-layer transport resolution
- docs/tests for provider capability handling

### Phase 2: Remove the hardcoded generic 30-second server rejection

Replace the current universal live chunk limit with capability-aware behavior.

Options:

- remove the generic rejection entirely
- or make it config-driven through `max_chunk_duration_seconds`

Preferred direction:

- no universal hardcoded `30s` rule
- use provider/profile-specific limits where needed

### Phase 3: Keep current live browser flow for `http_chunked`

Continue using:

- browser VAD
- overlap
- owner-only HTTP chunk upload
- sequence-aware backend application

But treat this as one transport profile, not the only live STT model.

### Phase 4: Add websocket streaming transport

Implement a separate live path for `websocket_streaming` providers.

Likely deliverables:

- websocket/browser transport client
- provider capability selection in workspace payload
- live partial/final result handling
- provider-specific reconnect/restart rules

## Browser UX principle

The UX should stay unified even if transport changes underneath.

Users should still see:

- `Start live`
- `Stop`
- listening / speech detected / sending / transcribing states

The transport implementation should vary by resolved provider capability, not by making the user choose a separate recording mode.

## Security and architecture constraints

This plan must preserve:

- transcript-derived content remains owner-only
- leaders/admins still do not gain transcript readability
- provider secrets remain server-side and Vault-backed
- transcript-root deletion still cascades through transcript-derived children
- only non-secret provider capability flags may reach the browser

## Tests required for implementation

When we implement this plan, we need targeted tests for:

- STT config capability persistence and validation
- provider-resolution behavior by transport kind
- authorization on live chunk and future websocket routes
- browser UI behavior for `http_chunked` vs `websocket_streaming`
- transcript sequencing and partial/final application rules
- deletion/cascade behavior for any new live transport records

## Recommended next slice

The next safe implementation slice is:

1. add `transport_kind` to STT configs
2. remove the hardcoded `30s` live server rejection
3. default existing providers to `http_chunked`
4. keep a conservative client cutoff, such as `25s`, only for `http_chunked`
5. leave websocket streaming support for the slice after that
