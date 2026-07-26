# OpenScribe Context

This file provides compact domain context for contributors. Operational contracts live in [`docs/README.md`](docs/README.md), especially [`docs/transcript-capture.md`](docs/transcript-capture.md), [`docs/api.md`](docs/api.md), and [`docs/security.md`](docs/security.md).

## Core content boundary

Transcript, working-note, post-consultation dictation, generated-document, redaction, and PII content belongs to one normal user and remains owner-only. Team-leader or system-administrator metadata/configuration authority never grants content readability. System-administrator accounts do not own transcripts.

The transcript root owns retention and deletion. Team retention is snapshotted from server-owned policy, and expired roots are unavailable before asynchronous cleanup.

Designated owner content is encrypted before PostgreSQL persistence under the owner's DEK; Vault Transit wraps DEKs under the deployment KEK.

## Consultation working note

The user-facing label is **Working note**. It is clinician-authored source content used as context for generation and is separate from:

- consultation transcript/STT draft;
- post-consultation dictation;
- generated note output and later generated-note edits.

There is one living working note per transcript.

### Mode and storage

- Mode is `freeform` or `structured` and is nullable until first non-empty save.
- First non-empty save locks the mode.
- Clearing the note immediately removes its living content and unlocks the mode.
- Clearing does not delete transcript text or existing generated documents/snapshots.
- Structured working notes use the EMIS profile and allowed section keys.
- Hidden structured sections persist even when the selected output template displays a narrower set.
- Freeform and structured content are encrypted under the transcript owner's key.
- Working-note state follows transcript-root retention/deletion and has no separate retention clock.

### API and concurrency

Owner-only routes:

- `GET /api/v1/transcripts/{transcript_id}/working-note`
- `PATCH /api/v1/transcripts/{transcript_id}/working-note`
- `DELETE /api/v1/transcripts/{transcript_id}/working-note`

Saves/clears use optimistic concurrency through `expected_updated_at`. Empty content is cleared through `DELETE`, not a whitespace-only `PATCH`. A stale edit cannot recreate a note cleared by another tab.

### Generation behavior

Template generation, follow-ups, and Quick Actions all use the saved working note automatically when it is one of the available consultation sources.

- Dirty working-note edits are saved before enqueue.
- Generation may proceed when at least one saved source exists: transcript text, working note, or saved dictation.
- Generation blocks when all saved consultation sources are empty.
- Transcript, working note, and dictation are labelled as distinct sources.
- Working note is redacted before provider dispatch; redaction failure fails closed.
- Quick Actions/follow-ups do not require an existing generated note.
- Generated-note edits never feed back into the working note automatically.

Each generated document stores an encrypted snapshot of the working-note input and mode used for that request. The living working note can change later without altering older snapshots. Normal generated-document detail does not need to expose snapshots unless an explicit owner-only provenance/debug feature does so safely.

### Editor behavior

- The workspace renders Working note as a virtual editor target alongside generated notes.
- Freeform and structured editors use the same save/conflict machinery as generated-note editing but a separate endpoint/payload.
- Generation waits for a successful working-note save.
- Unsaved/failed-save state blocks generation and is protected during target/template changes.
- After generation, the newest generated note may take focus; Working note remains available.
- Default clinical copy/export actions use generated output, not Working note.
- Freeform Working note supports personal Smart Phrases through the shared editor behavior.

### Privacy

Working-note text must not appear in logs, audit/usage metadata, task payloads, error details, provider diagnostics, or manager/admin views. Safe metadata can include IDs, mode, status, counts, durations, and bounded error codes.

## Post-consultation dictation

Post-consultation dictation is a separate transcript-owned clinician source.

- Preview transcription is transient until explicitly saved.
- Saved audio creates immutable segments plus an editable combined text.
- Edited combined text is authoritative; an intentionally empty edit suppresses dictation influence.
- Dictation is redacted before generation and snapshotted separately from transcript/working note.

## Generated documents

Every generation creates a new owner-only generated document. Queued creation uses a durable task-dispatch outbox and provider quota reservation. The worker resolves credentials before marking the attempt submitted, sends redacted sources, parses/validates output, reidentifies allowed placeholders, encrypts persistence fields, and records only safe metadata.

Generated documents remain drafts requiring clinician review. Existing outputs remain until explicit deletion or transcript-root deletion/retention cleanup.
