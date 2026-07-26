# Working Note Implementation Notes

## Status

**Implemented design history.** The working-note slice described by the original plan is active. Current user, API, generation, retention, and security contracts are maintained in:

- [transcript-capture.md](transcript-capture.md)
- [api.md](api.md)
- [workspace.md](workspace.md)
- [tutorials/user.md](tutorials/user.md)
- [../CONTEXT.md](../CONTEXT.md)

This file retains the principal design decisions without presenting completed work as future tasks.

## Domain decisions

- User-facing label: **Working note**.
- One living working note exists per transcript.
- Working note is clinician-authored owner content distinct from transcript/STT text, post-consultation dictation, and generated output.
- Mode is `freeform` or `structured`, nullable until first non-empty save.
- First non-empty save locks mode.
- Clearing removes living content immediately and unlocks mode without deleting transcript text or existing generated documents/snapshots.
- Structured mode uses the EMIS profile and allowed section keys.
- Working-note content and generated-document working-note snapshots are encrypted under the owner's DEK.
- Retention/deletion follows the transcript root.

## Owner API

- `GET /api/v1/transcripts/{transcript_id}/working-note`
- `PATCH /api/v1/transcripts/{transcript_id}/working-note`
- `DELETE /api/v1/transcripts/{transcript_id}/working-note`

Important behavior:

- non-empty saves require the matching mode/content shape;
- whitespace-only or entirely empty saves are rejected and should use `DELETE`;
- unsupported structured keys are rejected rather than silently discarded;
- `expected_updated_at` provides optimistic concurrency for saves and clears;
- switching a locked mode returns a conflict until the note is cleared;
- generic transcript metadata updates are not a substitute for the working-note routes.

## Generation contract

Template generation, follow-ups, and Quick Actions use the saved working note automatically as a labelled clinician-authored source.

- Dirty edits are saved before enqueue.
- Generation can proceed with any saved consultation source: transcript, working note, or saved dictation.
- All-source-empty requests are blocked.
- Transcript, working note, and dictation remain separately labelled; application code does not hard-map working-note sections into output sections.
- Working note is redacted before provider dispatch and generation fails closed if redaction fails.
- Each generated document stores an encrypted snapshot of the working-note mode/content used for that request.
- Generated-note edits never write back into the living working note.

## Workspace/editor contract

- Working note is a virtual note target in the shared editor.
- Freeform and structured modes use the shared save/conflict machinery but the dedicated working-note endpoint/payload.
- Unsaved or failed-save working-note state blocks generation.
- Template/target switching protects dirty edits and optimistic-lock baselines.
- The newest generated note may take focus after generation; Working note remains selectable.
- Default copy/export actions use generated output, not Working note.
- Freeform Working note supports personal Smart Phrases through shared editor behavior.

## Privacy

- Owner authorization is required before reading or changing Working note.
- Team leaders and system administrators gain no content visibility.
- Working-note text is excluded from logs, audit details, provider usage events, task payloads, and error details.
- Safe metadata may include IDs, mode, status, counts, durations, and bounded error codes.

## Historical implementation choices

The first implementation reused the existing structured-context storage shape while adding explicit mode and encrypted freeform storage rather than introducing a separate working-note table. Generated-document snapshots use separate encrypted fields. These physical choices are migration/service details; current migrations/models/services are authoritative if they evolve.

Future changes such as standalone history, source-provenance UI, new structured profiles, or explicit generated-note-to-working-note copy-back require a focused design and updates to the operational references above.
