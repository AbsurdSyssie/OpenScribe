# Structured EMIS Notes

## Status

The original persistence/editor roadmap is implemented and was subsequently generalized into the current **Working note** and generated-note editor contracts. The old statements that section context disappears between generations and that output line selection/editing remain future work are obsolete.

Current behavior is documented in:

- [working_note_implementation.md](working_note_implementation.md)
- [transcript-capture.md](transcript-capture.md)
- [workspace.md](workspace.md)
- [api.md](api.md)
- [tutorials/user.md](tutorials/user.md)

## Structured profile

Structured Templates and Working notes use the fixed EMIS profile with these allowed keys:

- `problem`
- `history`
- `family_history`
- `social_history`
- `examination`
- `comment`
- `tasks`
- `investigations`

The backend validates keys, drops/omits empty sections where appropriate, preserves canonical order, and rejects unknown/malformed structured configuration. UI needs must not relax this provider-independent JSON contract.

## Living structured source

The user-facing source is the transcript-owned Working note in `structured` mode.

- One living Working note exists per transcript.
- First non-empty save locks the mode.
- Clearing removes the living content and unlocks mode.
- Section lines persist between generations and page loads.
- Hidden sections remain persisted even when a selected output Template shows a narrower section subset.
- Saves/clears use optimistic concurrency.
- Content is owner-only transcript-derived data encrypted under the owner's DEK.
- Transcript-root retention/deletion owns the Working note.

Earlier implementation used transcript structured-context fields as the persistence foundation. Current models/migrations/services are authoritative for the physical field names; product documentation should describe Working note rather than exposing legacy “structured context” terminology.

## Generation snapshots

When structured generation is queued:

- dirty Working-note edits are saved first;
- the exact Working-note source used for the request is snapshotted encrypted on the generated document;
- later edits to the living Working note do not mutate historical generation snapshots;
- transcript, Working note, and dictation are labelled as separate sources;
- source text is redacted before provider dispatch;
- the provider returns the strict structured note shape;
- output is validated, reidentified where applicable, encrypted, and persisted.

A valid structured result has a short title and `content` object keyed only by allowed sections. Provider-specific response schema support does not replace backend validation.

## Structured editor

Implemented editor behavior includes:

- line-aware editing per section;
- Enter creates a new selectable statement/line;
- Shift+Enter creates a soft line break within the current line;
- blank rows do not become meaningful stored lines;
- generated section lines default to the current selection behavior and support section/statement selection for copying;
- generated section content can be edited and persisted through generated-document optimistic concurrency;
- statement reordering and keyboard/accessible controls where exposed by the current workspace;
- copy follows canonical EMIS section order and selected content;
- generated-note edits do not update the Working note.

The database does not need a dedicated line table merely because the browser presents line rows; current generated-document section storage remains the source of truth unless a future feature needs independently addressable line identity/history.

## Privacy and lifecycle

- Only the transcript owner can read/write Working-note or generated-section content.
- Team leaders/system administrators gain no content access.
- Working-note/output lines must not appear in logs, audit, usage, task payloads, or provider error metadata.
- Generated sections and snapshots follow generated-document/transcript-root deletion/retention.
- Reusable Template instructions/configuration must not contain patient content.

## Remaining roadmap

Possible future work, only when justified by a concrete user need:

- independently addressable line-level history/comments/provenance;
- additional structured profiles beyond EMIS;
- richer copy/export destination integrations;
- explicit owner-visible generation-source provenance UI;
- improved browser accessibility/visual regression coverage for large structured notes.

These require a focused design. Do not reintroduce the obsolete premise that structured session context is one-shot or unpersisted.
