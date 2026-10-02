# Audio ingestion recovery

Status: bounded recovery implemented on 2 October 2026; durable recovery during an unfinished browser capture awaits an architecture decision.

## Outcome

An audio capture must remain retryable when an upload or transcription fails. Make two automatic attempts after the first attempt for recoverable failures. After exhaustion, keep a visible manual retry action while the source still exists. A successful retry must apply transcription text once.

## Current contracts and incident

- On 1 October 2026, an audio-file upload returned HTTP 500 after a PostgreSQL deadlock while inserting the ingestion job. The transaction rolled back, leaving no job for the existing retry button. The microphone UI had already cleared its recording state.
- Whole-file source audio is stored in Vault with a maximum 24-hour deadline. Current manual retry transfers that source and deadline from a failed job. It must not extend retention.
- Live chunks have unique sequence numbers and no manual retry control. Provider attempts, quota, and outbox dispatch are durable and idempotent per attempt/job.
- The browser currently does not persist audio. Recovery after a refresh or browser crash requires a separate browser-storage design decision because audio is sensitive content and the maintained browser-storage inventory records no such storage.

## Work

1. Keep unsent file, microphone-batch, rollover, and live-chunk audio available in the open tab after a failed upload. Retry transient network/server/rate-limit failures twice with bounded backoff. On exhaustion, show an explicit retry action tied to the pending audio. Resolve an ambiguous server response before resubmitting to avoid duplicate jobs or text.
2. Classify provider failures at the STT boundary. Automatically retry timeouts, connection failures, 429, and 5xx twice. Do not automatically retry invalid audio, authorization, provider configuration/credentials, quota, or expired source. Persist the attempt number and next dispatch so process restarts and duplicate task deliveries cannot add attempts.
3. Expose manual retry for failed whole-file and live-chunk jobs while their original source remains available. Preserve live sequence/order, owner-only access, original source deadline, attempt accounting, transactional outbox, and durable cleanup. Give an actionable expired-source message.
4. Keep retry state visible during automatic attempts. Update maintained capture, live-STT, API, security/browser-storage documentation to match implemented behavior. Update the route-audit manifest if `/api/v1` changes.

## Acceptance checks

- Synthetic tests cover the observed upload deadlock, network/5xx before acceptance, response loss after acceptance, two extra recoverable attempts, permanent failure, exhausted retry with manual action, duplicate worker delivery, live sequence/order, source expiry/deletion, quota settlement, and owner authorization.
- No raw audio, clinical content, credentials, provider responses, or sensitive bodies enter logs, audit/usage metadata, or the outbox.
- Focused tests use the project virtual environment and isolated test database. Run the documented API authorization audit for route changes and maintained-document validation.

## Delegation

- Terra owns server lifecycle, persistence, routes, migration, and focused server tests.
- Terra owns browser capture/upload and retry UI, plus focused UI tests, without changing browser persistence policy.
- Sol owns architecture/integration review, this plan, maintained documentation integration, and final verification. Luna mapped existing retry, retention, and idempotency contracts.

## Explicit boundary

The open-tab fix cannot guarantee recovery after the page or browser process is destroyed. Persistent browser recovery is pending a privacy/security decision and must be documented honestly. Existing 24-hour server audio expiry remains an upper bound; after expiry the user must provide audio again.
