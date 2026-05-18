# OpenScribe Context

## Glossary

### Consultation Working Note

Clinician-authored note content captured during a consultation before LLM generation. It may be freeform text or structured section content, depending on selected note style. It is distinct from generated note output and must remain available after generation so user can review, edit, and regenerate without losing original clinician input.

There is one living consultation working note per transcript. Each generated note should retain its own immutable snapshot of the working-note input used for that generation.

Each transcript has one consultation working-note mode. Once a user creates working-note content as freeform or structured, that mode is the only working-note mode for that consult. Switching note style must not create a second parallel working note.

Working-note mode locks on first non-empty save. User may browse and switch templates before entering content. To switch mode after content exists, user must clear the working note with confirmation.

Selected template controls generated output shape. Working-note mode controls clinician-authored source shape. Generation may use a structured working note for a freeform template, or a freeform working note for a structured template, as a labelled source.

Structured output generation receives structured working-note content as labelled sectioned context. The prompt/model decides how to use it; application code does not hard-map working-note sections into generated output sections.

Clearing working note removes all working-note content and unlocks working-note mode. It leaves transcript text, generated outputs, and generated-output snapshots untouched. Clearing requires confirmation because deletion is immediate.

Working-note mode is explicit state on the transcript, nullable until first non-empty working-note save. It is not inferred only from content.

Structured working note supports EMIS profile in MVP. Storage and language may allow future structured profiles, but current validation uses allowed EMIS section keys.

Existing structured context storage can remain physically named as-is during MVP. Product/API/service language should expose it as structured working note where practical. Schema adds explicit working-note mode and freeform working-note text rather than renaming existing structured storage immediately.

Migration should backfill working-note mode to structured only when existing structured context has at least one non-empty allowed section. Empty or missing structured context keeps null mode.

Generated notes snapshot the working-note mode and the one working-note content source used for generation. Only the matching freeform or structured snapshot is populated.

Generated-note working-note snapshots do not need first-slice normal UI exposure. They may support future generation-input details, debugging, or provenance under owner-only access.

If working-note redaction fails, note generation fails closed. The system must not send unredacted working-note content to an LLM.

Working-note input has size limits. Freeform working note supports up to 20,000 characters. Structured working note should reuse existing structured validation where present, otherwise target 4,000 characters per section and 20,000 total.

Working-note autosave is conservative in the first slice. UI shows saving, saved, or error based on server response. Mode lock is final only after server confirms save.

Generation blocks when working-note editor has unsaved or failed-save state. User must save successfully before generation so LLM input matches visible working note.

Working-note content follows transcript-root retention. Living working note is deleted with transcript root. Generated-document working-note snapshots are deleted with generated documents or transcript cascade. No separate retention clock exists for working notes.

Working-note metadata and content are owner-facing in the first slice. Team leaders and system admins receive no new working-note visibility by default.

Working-note content must not appear in logs, analytics, provider usage events, or error details. Logs may include IDs, mode, status, counts, durations, and error codes only.

Structured consultation working-note content is global to the transcript by structured profile. EMIS content uses the allowed EMIS section keys. Templates decide which sections are visible or used, but hidden sections persist and can reappear when the selected template changes.

Generation treats transcript text and consultation working note as separate labelled sources. Transcript text remains factual patient-spoken anchor. Consultation working note is clinician-authored context and carries stronger signal for assessment, phrasing, and plan. The generated note must not invent facts absent from both sources.

Generated-note edits do not feed back into consultation working note automatically. Generated-note edits belong only to that generated document. Future generation uses edited working-note content only when user explicitly changes the working note.

After generation, workspace may focus generated output, but consultation working note must remain visible or easy to reopen as a clearly labelled editable source. Unsaved working-note edits must be protected from accidental loss.

Each regeneration creates a new generated note output. Existing generated outputs remain available until user deletes them or transcript-root deletion/retention removes them.

Generated outputs do not need stale/out-of-date indicators when consultation working note changes after generation.

Consultation working note uses autosave/on-blur persistence with visible saved state. Generation first saves current working-note editor content, then queues generation.

Consultation working note is transcript-derived content under transcript-root retention and deletion. Owner may clear working-note content independently without deleting transcript or generated outputs. Clearing working note does not alter generated-output snapshots.

Consultation working note is exposed under transcript owner authorization as its own concept. It should not be mixed into generated-document output, though owner-facing workspace payloads may include it for rendering.

Existing structured transcript context represents structured consultation working-note content. Freeform consultation working-note content may be added separately. Naming in product and code should converge on working-note language where practical.

Freeform consultation working-note content is one living encrypted text source on the transcript root. A separate working-note table is not needed for the MVP because each transcript has at most one working note, either freeform or structured.

Transcript draft text remains distinct from working-note text. Transcript draft text is consultation transcript/STT content. Working-note text is clinician-authored note context. Generation may use both, labelled separately.

Template note generation uses consultation working-note context. Quick actions and follow-ups do not automatically include consultation working note unless a user explicitly supplies context for that action. Consultation working-note content must pass through redaction before any LLM request.

Working-note redaction reuses the generation-time redaction boundary with one combined placeholder index across transcript, dictation, and working note. A separate user-visible redaction run is not required for working note.

Each generated note stores an encrypted plaintext snapshot of the working-note input used for that generation. Redacted prompt auditing can rely on the generated document's encrypted LLM request payload; separate redacted working-note fields are not required unless future needs emerge.

Structured generated-note snapshots store only the structured sections used for that generation. The living structured working note keeps all profile sections.

Template generation may proceed when at least one source has content: transcript text, selected-mode consultation working note, or saved dictation. Generation should block when all are empty.

Consultation working note has no standalone edit history in MVP. History is limited to living working-note state plus immutable per-generation snapshots.

No explicit copy-back flow from generated note to consultation working note is needed in MVP.

Default clinical copy/export actions use generated note output, not consultation working note. Working-note UI may provide its own explicit copy working note action when user is viewing the working note.

Freeform working note should support smart phrases when existing editor infrastructure can be reused without new smart-phrase architecture.

User-facing label is "Working note". Help text may describe it as "Your own notes used as context for generation."

Generated note history may show lightweight source metadata such as freeform working note or EMIS working note when it is easy to include, but this is not required for MVP usability.
