# Scribe Workspace Brief

## Status

The canonical consultation workspace is `/workspace`, not `/transcribe`. `/transcribe` is a temporary compatibility redirect that preserves only a validated `transcript_id`. The former `/transcriber_col_changes`, `/transcribe-glm-2`, and `/transcribe-claude` prototype routes have been removed; they are not alternate content-access paths or compatibility routes.

Detailed behavior is maintained in [workspace.md](workspace.md), [transcript-capture.md](transcript-capture.md), [live_stt.md](live_stt.md), and [api.md](api.md).

## Audience and authorization

The Scribe workspace is owner-only. The owner can be a normal user or team leader acting as a clinician/content owner.

Role alone does not grant access to:

- another user's transcript;
- Working note;
- post-consultation dictation;
- generated notes/follow-ups;
- redaction/PII data;
- source audio.

System administrators are redirected to `/admin` and cannot own transcripts.

## Consultation navigation

Implemented behavior includes:

- create a new consultation;
- select/rename/delete owner consultations;
- open/create another consultation while previous stopped capture continues processing;
- independently scroll/toggle the Recent consultations rail;
- cursor pagination for older consultations;
- grouping by date and time of day;
- accessible status icons/tooltips;
- bulk deletion where offered by the current UI;
- preservation of loaded rail state during polling/selection;
- a remembered active transcript UUID in `sessionStorage` as an untrusted navigation hint only.

The server repeats owner and retention checks. A browser-stored UUID cannot grant access.

Before switching/creating consultations, dirty Working-note or generated-document edits are drained. A failed save blocks navigation rather than silently discarding edits.

## Audio capture

The workspace presents recording/upload modes on top of two persisted ingestion modes:

- `whole_file`;
- `live_chunked`.

User actions can include:

- upload an approved audio file;
- record a microphone batch with automatic rollover before server limits;
- record live VAD chunks;
- stop/finalize capture;
- retry whole-file ingestion only while a bounded retry audio reference exists;
- choose current versus new consultation when attempting to record into an older ready consultation with existing content.

Capture controls show safe provider/configuration status and bounded errors, never credentials or unrestricted provider metadata.

During microphone recording, marked workspace navigation is disabled and unload is warned. Background transcription/generation does not itself lock navigation.

See [transcript-capture.md](transcript-capture.md) and [live_stt.md](live_stt.md).

## Transcript

The transcript panel contains the owner-authorized current draft and status. Transcript text is populated by ingestion work and encrypted at rest. It is not directly edited through the current Scribe transcript panel.

Polling/SSE refresh reconciles status and content. Responses are `no-store`.

## Working note

The old “structured context” product language is superseded by **Working note**.

- One living Working note exists per transcript.
- Mode is `freeform` or `structured`.
- First non-empty save locks mode.
- Clear removes living content and unlocks mode without deleting transcript or generated outputs.
- Structured mode uses the fixed EMIS keys:
  - `problem`
  - `history`
  - `family_history`
  - `social_history`
  - `examination`
  - `comment`
  - `tasks`
  - `investigations`
- Saves/clears use optimistic concurrency.
- Dirty Working-note edits must save successfully before generation.
- Working note is redacted before provider dispatch and snapshotted encrypted per generated request.

Working note is distinct from transcript, dictation, and generated-note edits.

## Post-consultation dictation

The owner can preview microphone/file dictation without immediate persistence, edit the preview, and explicitly save it to the transcript-owned dictation aggregate.

- Saved segments are immutable source records.
- Editable combined text becomes authoritative after edit.
- Intentionally empty edited text suppresses segment fallback.
- Dictation is a separately labelled generation source and is redacted before dispatch.
- Quick Action context preview is transient and fills additional context rather than creating a separate dictation record.

## Templates and note generation

The workspace exposes authorized default/team/personal Templates and current team/user LLM policy.

Template generation:

- uses the selected template to control output shape;
- can use saved transcript, Working note, and/or dictation as separately labelled sources;
- blocks when all saved sources are empty;
- saves dirty Working note before enqueue;
- creates a durable queued generated-document row, quota reservation, and task-dispatch outbox row;
- refreshes status/result asynchronously;
- stores encrypted source/request/output snapshots;
- keeps every result as a draft requiring clinician review.

Structured output uses the fixed EMIS JSON contract. Freeform output does not show structured headings.

## Generated-note editing and copy

Owners can select/edit ready generated notes. Edits use optimistic concurrency and do not write back into Working note.

The note-selection rail and selected-note metadata show creation times in the browser's local timezone.

Structured note lines can be reordered and selected for copying. Freeform/structured copy controls can enforce the current review/scroll interaction and explain blocked copy through bounded UI feedback.

Copying is a convenience, not verification. The clinician remains responsible for reviewing the content and destination EPR field.

## Follow-ups and Quick Actions

Owners can:

- submit custom follow-up requests;
- run authorized Quick Actions;
- add transient context;
- review/edit ready follow-up documents;
- browse current-consultation history.

These requests use the current saved consultation sources, including Working note and dictation, without requiring an existing generated note. All-source-empty requests remain blocked. Results are queued owner-only generated documents and require review.

## Redaction and PII

The workspace exposes minimized owner-only PII/redaction state.

- Original values are not shown by default.
- Reveal is an explicit owner-only POST+CSRF action.
- Manual PII can be added/deleted by the owner.
- Detected/manual values are encrypted and participate in pre-LLM redaction/post-generation reidentification.
- Provider/audit/usage metadata excludes original values and content.

## Layout and refresh contract

- Scribe owns a bounded viewport-height shell.
- The active content panes and Recent rail own their overflow.
- Shared workspace navigation remains available.
- Mobile keeps the shared header/off-canvas navigation and a separate consultation overlay.
- Polling uses owner-authorized workspace payloads and should avoid unnecessary rerendering without retaining duplicate content-bearing client signatures.
- Hidden Scribe panels remain siblings so one tab cannot hide another accidentally.

## Compatibility and migration

- `/transcribe` redirects to `/workspace`.
- `/settings` redirects through a closed tab map to `/workspace/*`.
- `/home` remains the current normal-user post-login compatibility landing.
- New links/redirects should target canonical workspace routes.
- Preview routes should not be treated as production contracts without an explicit promotion decision and regression coverage.

## Non-negotiable behaviors

- Owner-only content and non-disclosing cross-owner failures.
- System administrators cannot own/read transcripts.
- Team policy/credential management never grants content access.
- Encrypted persistence and fail-closed decryption/redaction/provider behavior.
- Transcript-root retention/deletion and durable cleanup.
- Queue payloads/audit/usage contain metadata only.
- Unsafe browser requests require current CSRF/origin controls.
- All generated text remains draft requiring clinician review.
