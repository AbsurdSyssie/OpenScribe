# Deepgram live diagnostics plan

Status: implemented on 22 October 2026; independent review and verification recorded below.

## Goal

Make failures in Deepgram live transcription diagnosable in host development and Docker deployments, while keeping clinical content, credentials, and provider payloads out of logs. Scope is the Deepgram live route, its provider adapter, the `start-dev.sh` launch path, bounded local log retrieval, and the applicable operational documentation. This is an observability change; it must not alter stream, transcript, quota, recovery, or ownership behavior.

The user confirmed that failures occur under `start-dev.sh` and similar errors have occurred in production running Docker.

## Current behavior

- `app/routes/deepgram_live.py` logs only the terminal outcome after a started session fails, plus a fixed reconciliation-pending message. It maps errors to safe lifecycle codes and deliberately drops exception strings and tracebacks. Most useful context such as phase, elapsed time, recording correlation, and checkpoint outcome is not logged.
- Some event fields are put in `logging.extra`. The default visible message includes only the format string, so these fields do not appear in ordinary terminal or Docker output.
- `app/services/deepgram_streaming.py` maps SDK and websocket failures to `DeepgramStreamError` categories without logging provider messages. Deepgram SDK logging is silent, and the adapter's protocol-frame logger is disabled and non-propagating to prevent audio/transcript frames from entering the shared logging pipeline. These safeguards are mandatory.
- `start-dev.sh` starts FastAPI, Celery, and Beat in the foreground process group. Their output appears in the launch terminal, but the script does not persist it. Capturing all launcher output would also capture unrelated services and free-form bootstrap/readiness errors, so it is outside this plan.
- The documented Docker retrieval path is `docker compose logs`; `docker-compose.yml` does not set a per-service rotation policy. Docker daemon logging policy may vary by installation.
- `docs/live_stt.md`, `docs/setup.md`, and `docs/docker.md` are maintained current references. Live STT documentation currently describes sanitized terminal failure records but not a diagnostic correlation ID or a local file sink.

## Intended design

1. Add a small Deepgram-live diagnostic emitter scoped to `openscribe.deepgram_live`. It serializes only a fixed event name and a fixed allowlist of primitive fields to one JSON object per line. Emit the serialized record as the actual log message so existing stdout and Docker formatters show the useful fields. Do not attach raw objects, exception details, tracebacks, arbitrary `extra`, URLs, provider request IDs, or database identifiers.
2. Generate one cryptographically random opaque diagnostic ID per accepted recording, independent of `TranscriptStreamSession.id`, user/team IDs, and transcript ID. Include it on every event after creation. For failures before that point, emit only a fixed phase/outcome and bounded error category without an identifier.
3. Record bounded lifecycle events at useful transitions: handshake/preflight phase outcome, provider connect start/success/failure, first authorized media dispatch (distinct from connection success), silence/pause KeepAlive summary, checkpoint/Finalize start and result, exceptional CloseStream/drain fallback, durable window commit result, browser `window_committed` acknowledgement-send result, explicit Stop/final drain, provider close, client disconnect/close classification, recovery reconciliation result, and recording failure/termination. Record elapsed milliseconds and bounded counters/samples/bytes where useful. Avoid per-frame and per-interim-result logs; aggregate counts and timings at checkpoints and terminal transitions.
4. Extend the adapter-to-route error contract only as needed to expose safe typed failure categories and a close-code classification. If a websocket close code is available, map it through a finite enum; never include the close reason, raw exception, status body, request ID, or arbitrary numeric/provider value. Preserve the current adapter silence, the dedicated disabled/non-propagating protocol logger, no-autoreconnect behavior, and typed error mapping.
5. Add an optional rotating local sink for only the Deepgram live diagnostic records. Under `start-dev.sh`, enable it by default at `.local/logs/deepgram-live.jsonl`; create only the dedicated logs directory with mode `0700` and newly created log/backup files with mode `0600`. Do not chmod or otherwise change unrelated parent directories. Reject a symlink, non-directory logs path, non-regular/symlink file, file owned by another user, or an existing file with group/world access; do not silently follow or broaden an unsafe target. Rotation must retain these permissions for backups. Bounds are a 5 MiB active file plus 2 backups (15 MiB total). Only records emitted through the allowlisted diagnostic emitter may reach the file sink. The file sink is best-effort: initialization/write/rotation failure must not affect recording, checkpointing, or cleanup; emit one fixed warning at most once to stdout and continue with stdout diagnostics. Do not tee or redirect FastAPI/Celery/Beat/bootstrap/browser output. Keep the same safe JSON records on stdout for immediate inspection. Docker deployments use stdout and `docker compose logs`; do not add a second file volume or alter container filesystem retention in this plan.
6. Explicitly configure and verify INFO visibility for `openscribe.deepgram_live` in both `start-dev.sh`'s FastAPI CLI and the Docker Uvicorn command. Ensure emitted structured records reach stdout once, with no duplicate propagation/handlers. Document exact local retrieval (`tail`/`grep` of `.local/logs/deepgram-live.jsonl`) and Docker retrieval (`docker compose logs --since/--tail openscribe`, adapted to the deployment's Compose project). Explain that the diagnostic ID links one recording's events and can be shared for support only after normal local handling. State retention/rotation bounds and that restarting the app may rotate the local file.
7. Keep the prior continuous-connection implementation and behavior intact. In particular, ordinary checkpoints, silence, and pause retain the provider connection; an unproven Finalize follows the existing bounded CloseStream/drain fallback. Durable quota settlement, exact recovery interval rules, encrypted transcript writes, and ownership checks remain in their current services.

## Data allowed in diagnostics

- A random per-recording diagnostic ID that is not an application or owner identifier.
- Fixed event names, phase names, safe error categories, and finite close classifications.
- Bounded duration values, counters, window number (a sequence number only), booleans, and fixed outcomes.
- Provider event counts by fixed event type; no event text or payload.

Do not log transcript text, interim/final results, prompts, provider responses or request IDs, audio or audio hashes, credentials/tokens/cookies, request/response bodies, exception strings, tracebacks, host/user/team/transcript/session identifiers, raw URLs, or raw websocket close reasons. Do not add these values to audit/usage metadata or persistence.

## Candidate files and seams

- `app/routes/deepgram_live.py`: create/store the opaque diagnostic ID, emit route lifecycle/checkpoint/recovery events and safe timing/counter summaries; remove misleading reliance on invisible `extra` fields for the live route.
- `app/services/deepgram_streaming.py`: preserve SDK logging suppression and typed adapter contract; expose only additional safe close classification if the SDK exception gives a reliable code.
- `app/main.py` or a small focused logging utility: attach a bounded rotating handler to the named live logger only, without changing root logging or other handlers. The handler must be idempotent across FastAPI reloads and must not duplicate records. Rotation must be safe for the single active FastAPI app process used by `start-dev.sh`; do not claim multi-worker/process safety for the local file sink.
- `start-dev.sh` and `docker/entrypoint.sh`: explicitly ensure INFO visibility for the named live logger and its stdout path; set the optional diagnostic file path only for the host FastAPI process in `start-dev.sh`. Do not pipe the full script or child process output through `tee`.
- `docker-compose.yml`: no change planned unless the reviewer determines Docker's existing logs are insufficient for retrieval. Any proposed logging-driver rotation affects all OpenScribe stdout/stderr and must be explicitly assessed before inclusion.
- `tests/test_deepgram_live_route.py`, `tests/test_deepgram_streaming.py`, and a focused logging-configuration test: verify useful events and field bounds under the actual development and Docker log levels, separate provider drain, durable commit, and browser acknowledgement outcomes, no duplicate handlers, rotation/permissions/rejected unsafe paths, graceful file-sink failure, and that synthetic sentinels in provider errors/close reasons/transcript events never appear in captured logs or local file output.
- `docs/live_stt.md`, `docs/setup.md`, and `docs/docker.md`: update current diagnostics behavior, local log location/retrieval, and Docker retrieval/retention guidance as applicable.

No schema or migration is expected. No `/api/v1` route contract changes are intended.

## Acceptance criteria

- A synthetic successful recording emits correlated connection, checkpoint, commit, and Stop lifecycle records with durations and bounded counters visible in stdout and, when enabled, the local JSONL sink.
- Synthetic provider connect, send, receive, timeout, close, Finalize, drain, quota, transport, and reconciliation failures can be distinguished by safe phase/category fields without logging exception or provider contents.
- Provider drain success, database commit success, and delivery of the browser `window_committed` message are recorded as separate stages. A browser send failure after a successful commit must not be logged as if the durable transcript commit failed; no additional database read or retry is introduced for diagnostics.
- A random diagnostic ID correlates all events for one recording but cannot be derived from transcript/session/owner identifiers and is not persisted or sent to clients.
- Tests inject recognizable synthetic sentinels into transcript text, provider error text/body, credentials, and websocket close reason and prove those sentinels do not appear in either sink; tests also prove SDK frame logging remains suppressed after root logger configuration changes.
- Local log creation uses owner-only permissions, remains bounded after repeated writes, does not duplicate records after app reload/reconfiguration, and a failed file sink never interrupts the live session.
- Actual `start-dev.sh` and Docker server logging configurations emit INFO diagnostics to stdout exactly once. The local rotating handler is scoped to one active FastAPI process; Docker file capture remains disabled.
- No generic process output is captured by the new local sink. No migrations, provider calls, quota/recovery contract changes, content logging, or broad root logging changes are introduced.
- Focused tests and maintained-document validation pass after implementation. A separate Sol 6.1 review checks privacy leakage, correlation-ID unlinkability, logger propagation/duplication, lifecycle coverage, local rotation, and unchanged quota/recovery behavior.

## Implementation status

1. Use the optional environment-controlled file path, enabled by `start-dev.sh` for the FastAPI process only. Path is `.local/logs/deepgram-live.jsonl`, rotation is a 5 MiB active file plus 2 backups (15 MiB total), and no file capture is enabled in Docker.
2. Leave Docker logging-driver rotation operator-owned because it applies to all OpenScribe stdout/stderr; document `docker compose logs` retrieval and the deployment's retention dependency.
3. Include finite close classifications when reliably available from the installed SDK/websocket version; exclude raw close code and reason.
4. The file sink fails open for recording behavior, warns at most once with fixed content, and is scoped to the single active FastAPI app process under `start-dev.sh`; generic Docker file capture and multiprocess file rotation remain outside scope.

The initial implementation was approved by a separate Sol 6.1 review on 2026-10-22. A follow-up Sol 6.1 review approved the UTC timestamp and documentation corrections on the same date. The route emits bounded, allowlisted JSON records with ephemeral correlation and phase snapshots; provider drain, durable commit and browser acknowledgement are separate outcomes. The local sink is scoped to the named diagnostic logger, uses an owner-only directory and files, keeps a 5 MiB active file plus two backups (15 MiB total), and disables itself on file I/O failure while stdout continues. `start-dev.sh` defaults to `.local/logs/deepgram-live.jsonl` for the FastAPI process only; set `DEEPGRAM_LIVE_LOG_PATH=` in `.env` to disable the file sink. Rotation is intended for one active host FastAPI process. Docker uses stdout retrieval and deployment/operator-owned log retention, with no dedicated file sink or rotation changes. No schema, API response, persistence, quota, recovery, or client contract changed.

Verification on 2026-10-22: focused isolated live-only Deepgram suite **103 passed**, API authorization audit **exit 0**, maintained-document validation **54 files checked**, and `bash -n start-dev.sh docker/entrypoint.sh` passed. The initial sandboxed pytest/audit attempts failed before collection/startup because the configured PostgreSQL test connection was unavailable to the sandbox; retrying the same commands through the supported elevated path passed. The focused suite reported three dependency deprecation warnings. No provider calls or runtime logs were used.
