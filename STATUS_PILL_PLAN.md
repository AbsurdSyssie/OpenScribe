# Transcription Status Pill Plan

## Goal

Make the transcription page status pill a trustworthy at-a-glance health indicator for capture, transcription, speech-provider health, generation, redaction, and microphone problems.

The pill should not be a raw transcript status mirror. It should aggregate active local UI state, backend lifecycle state, provider availability, and current-session issues into one prioritized label with details available on hover, focus, and tap.

## Target Behavior Checklist

- [ ] Show one highest-priority pill label at all times.
- [ ] Show `+N` when there are additional active issues or meaningful details behind the top label.
- [ ] Show ordered details on hover, keyboard focus, and mobile tap.
- [ ] Include a manual `Recheck` action for speech-service health when speech health is unknown or degraded.
- [ ] Keep live capture labels stable while local capture is active; workspace refresh must not stomp `Listening`, `Speech detected`, or `Sending chunk` with backend `ready`.
- [ ] Surface microphone startup failures as explicit pill states.
- [ ] Treat STT health as warning-only, not recording-blocking.
- [ ] Avoid exposing transcript text, note text, prompts, provider responses, secrets, tokens, or raw audio details.

## Affected Areas Checklist

- [ ] Frontend status pill rendering in `app/static/js/transcribe/app.js`.
- [ ] Audio capture local phase/mic-error reporting in `app/static/js/transcribe/media.js`.
- [ ] Workspace STT availability payload in `app/web/transcribe_workspace.py`.
- [ ] Workspace API schema in `app/schemas/workspace.py` if new fields are returned.
- [ ] API route for manual STT health recheck in `app/routes/api_routes.py`.
- [ ] STT health helper/caching in `app/services/stt.py` or a small focused service module.
- [ ] Tests in `tests/test_api.py` or adjacent focused tests.
- [ ] Docs update in transcript/status documentation and progress note.

## Architecture Risk Checklist

- [ ] Privacy: status details must be operational metadata only.
- [ ] Ownership: workspace health details only for current user's team-selected STT config.
- [ ] Secrets: health checks may use Vault-backed bearer token server-side, but never return or log it.
- [ ] Provider rules: normal users consume selected provider indirectly; leaders/admins may see richer diagnostics but no secrets.
- [ ] Deletion: no persisted health rows in first slice, so no new cleanup path.
- [ ] Structured notes: no change to structured-note JSON contract.
- [ ] Logging: log only event type, config/team IDs, status code/error code, sanitized URL, durations.

## Status Hierarchy

Use this priority order for the pill label:

1. `Transcription failed`
2. `Generation failed`
3. `Mic not detected`
4. `Mic blocked`
5. `Mic unavailable`
6. `Recording blocked`
7. `Speech issue`
8. `Generation unavailable`
9. `Redaction issue`
10. `Clinical NLP issue`
11. `Finalizing`
12. `Sending chunk`
13. `Speech detected`
14. `Listening`
15. `Generating`
16. `Transcribing`
17. `Ready`
18. `Idle`

If several states are active, show the highest-priority label and a `+N` badge for extra active items.

Do not count normal `Ready`/`Idle` backend details as issues when warning/error details exist.

## Severity Mapping

- Error or blocked: coral/red.
- Warning: amber/gold.
- Active: teal with pulsing dot.
- Ready: teal, no urgent pulse.
- Idle/unknown: slate/white.

Ready should not pulse. Pulse only when work is actively happening.

## STT Health Behavior

STT health is warning-only. It should not prevent record/upload controls from being used.

Minimal first-slice provider health source:

- Infer health URL as `${base_url}/health`.
- Apply only to custom REST/openai-compatible REST style endpoints.
- Skip OpenAI Cloud, Deepgram, and ElevenLabs health for now unless they already have an obvious safe built-in check.
- No schema change for configurable `healthcheck_url` in this slice.

Health result mapping:

- `2xx`: healthy.
- `404`: health endpoint not reported, not provider-down.
- `401`/`403`: credential/config warning.
- timeout/connect/`5xx`: provider may be unavailable.
- no active selection/missing credential: recording-blocking/config issue, not health warning.

Normal-user copy:

- Healthy: `Speech service reachable.`
- `404`/no endpoint: `Speech service health is not reported.`
- `401`/`403`: `Speech service needs attention from your team lead.`
- timeout/connect/`5xx`: `Speech service may be unavailable; transcription may fail.`

Leader/admin details may include:

- sanitized health URL
- status code
- provider error code
- duration
- checked-at timestamp

Leader/admin details must not include:

- bearer token
- raw response body
- provider secret reference
- transcript or note content

## STT Health Caching

- [ ] Use in-memory TTL cache keyed by `team_id + stt_config_id + purpose`.
- [ ] Default TTL: 60 seconds.
- [ ] Workspace load/refresh may use cached result.
- [ ] Manual `Recheck` bypasses cache.
- [ ] Multi-worker deployments may have separate cache entries; acceptable for MVP.
- [ ] Avoid hammering provider with every SSE/poll refresh.

## Manual Recheck

- [ ] Normal users may recheck their own team's selected conversation STT.
- [ ] Leaders/admins may recheck and see diagnostic details.
- [ ] Recheck response must use same privacy/secrets boundaries as workspace payload.
- [ ] Frontend should disable or debounce `Recheck` briefly after click.
- [ ] Recheck updates pill details without needing full page reload.

## Live Capture State Rules

Current problem: workspace refresh can call backend status rendering and overwrite local live labels with backend `ready`, `recording`, or `transcribing`.

Required rule:

- While local live capture is active, local phase wins over backend lifecycle status.
- Backend hard failures still win.
- Backend state remains visible in details/tooltip.

Live local phase labels:

- Waiting for speech: `Listening`
- Speech started: `Speech detected`
- Chunk upload in progress: `Sending chunk`
- Stop/finalize requested: `Finalizing`
- Accepted upload while capture continues: return to `Listening`
- Backend failed: `Transcription failed`

Do not show `Transcribing` between chunks while mic/VAD is actively listening. Put backend chunk processing in details instead.

## Microphone Errors

Do not proactively probe mic on page load, because that can trigger browser permission prompts.

Map startup failures after user presses start:

- `NotFoundError`: `Mic not detected`
- `NotAllowedError` or `PermissionDeniedError`: `Mic blocked`
- other `getUserMedia` or VAD startup failure: `Mic unavailable`

Mic error lifecycle:

- [ ] Clear when capture starts successfully.
- [ ] Keep across workspace refreshes for current page session.
- [ ] Clear on transcript/session switch.
- [ ] Manual STT recheck does not clear mic error.

## Generation and Redaction Rules

Generation should be included only when it affects the current transcript.

Generation mapping:

- Latest current-transcript note/follow-up failed: `Generation failed`.
- Current-transcript note/follow-up queued or processing: `Generating`.
- No LLM selection while there is draft/note input available: `Generation unavailable`.

Priority rule:

- Generation status outranks plain transcript `Ready`.
- Generation status does not outrank active local recording/listening/sending chunk, except `Generation failed` outranks active states according to hierarchy.

Redaction/clinical mapping:

- Redaction run failed: `Redaction issue`.
- Clinical NLP run failed: `Clinical NLP issue`.
- Redaction `not_run`: no pill issue until an action requires redaction.
- PII count by itself is not an issue.

## Tooltip / Popover

Desktop:

- Hover shows details.
- Focus shows details for keyboard users.

Mobile:

- Tap pill toggles details.
- Tap outside closes details.

Details content:

- Ordered list of active issues/statuses by severity.
- Include local live phase and backend phase when useful.
- Include `Recheck` when speech health is unknown/degraded.
- Use plain operational messages for normal users.
- Add diagnostic metadata only for leaders/admins.

Example details:

- `Speech service may be unavailable; transcription may fail.`
- `Latest note generation failed.`
- `Redaction check failed: provider_unavailable.`
- `Live capture: listening for speech.`
- `Backend: transcribing latest chunk.`

## First Implementation Scope

Do now:

- [ ] Backend STT health result in workspace payload.
- [ ] Manual STT health recheck endpoint.
- [ ] Frontend status aggregator with hierarchy and `+N`.
- [ ] Hover/focus/tap details.
- [ ] Live status protection against backend refresh stomping.
- [ ] Mic error mapping and lifecycle.
- [ ] Tests and docs/progress update.

Do not do now:

- [ ] Persistent health history.
- [ ] Schema migration for configurable `healthcheck_url`.
- [ ] LLM provider health checks.
- [ ] Full popover/modal redesign.
- [ ] Provider-specific health contracts for OpenAI/Deepgram/ElevenLabs.

## Testing Checklist

- [ ] Workspace returns STT health warning when inferred `/health` times out or fails.
- [ ] Workspace treats `/health` 404 as health-not-reported warning, not hard provider-down.
- [ ] Normal user receives plain health message only.
- [ ] Leader/admin receives diagnostic details without secrets.
- [ ] Manual recheck bypasses cache.
- [ ] Missing STT selection remains a recording-blocking/config issue.
- [ ] Live capture local phase is not overwritten by workspace refresh.
- [ ] Mic startup error maps to `Mic not detected`, `Mic blocked`, or `Mic unavailable`.
- [ ] Generation failure/current processing contributes correct pill status.
- [ ] Redaction/clinical failure contributes warning status.

Run focused tests with:

```bash
.venv/bin/pytest -q tests/test_api.py -k "stt_health or transcribe_workspace"
```

Run broader relevant tests if shared workspace/status behavior changes:

```bash
.venv/bin/pytest -q tests/test_api.py
```

## Implementation Notes

Prefer a small status aggregation function on the frontend instead of spreading pill decisions through capture, workspace, generation, and redaction handlers.

Suggested model:

- Maintain local status facts: live phase, mic error, capture active flag.
- Maintain backend status facts from workspace: transcript status, latest ingestion job status, generation statuses, redaction status, clinical NLP status, STT health result, LLM selection.
- Convert facts into status items.
- Sort by hierarchy.
- Render top item into pill label/style.
- Render remaining items into details.

This avoids recurring bugs where one code path calls `setVisibleStatus()` and accidentally erases a more important state.

Longer term, deprecate direct `setVisibleStatus(label)` calls in favor of updating facts and re-rendering the aggregated pill.
