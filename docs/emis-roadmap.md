# EMIS Structured Note Roadmap

## Scope

This note captures the next planned work for the structured EMIS note flow.

Current behavior:

- structured EMIS templates exist
- per-section EMIS prompts exist on `template_versions.config_json`
- users can prefill section context before generation
- structured generation validates section keys and persists `generated_document_sections`

Current gap:

- user-entered section context in `/transcribe` does **not** persist between generations
- the next page load shows empty EMIS context fields again

That is not the intended UX.

## Target behavior

The EMIS flow should behave like an in-progress working note, not a one-shot prompt form.

Desired behavior:

1. user enters or edits section text in the EMIS context area
2. that text persists on the transcript session
3. future generations reuse that text automatically unless the user changes it
4. generated section output is clearly separated line-by-line
5. users can select or deselect individual lines for clipboard copy
6. Enter creates a new selectable line
7. Shift+Enter creates a soft line break inside the current line

## Recommended persistence model

Persist EMIS working context at the transcript root, not only on individual generated documents.

Why:

- the user expectation is that the session remembers their working EMIS fields
- multiple generations for the same transcript should share the same working section state
- generated documents are outputs, not the editable session workspace source of truth

Recommended DB addition:

- `transcripts.structured_context_json`

Shape:

```json
{
  "profile": "emis",
  "sections": {
    "problem": ["Known asthma", "Worse cough for 2 weeks"],
    "history": ["Symptoms improved after doxycycline", "Green sputum returned today"],
    "tasks": ["Peak flow diary requested"]
  }
}
```

Notes:

- store lines as arrays, not one large blob
- this matches the intended selectable-line UX
- blank sections can be omitted
- this stays owner-only transcript-derived content

## Why not keep using `generated_documents.structured_context_json`

That field is still useful as a snapshot of what a specific generation run used.

But it is the wrong source of truth for the workspace because:

- it exists only when a generation is queued
- it does not represent the current working state of the session
- it makes the form feel stateless between generations

Recommended rule:

- `transcripts.structured_context_json` = current working session state
- `generated_documents.structured_context_json` = immutable generation snapshot

## UX stages

### Stage 1: Persist section text between generations

Status: implemented

Add:

- transcript-backed EMIS working context
- preload `/transcribe` EMIS fields from transcript working context
- save updated context whenever the user runs generation

Expected outcome:

- the user does not lose section text between runs
- each generation snapshots the current EMIS context into the generated document

Current implementation notes:

- transcript-backed working state now lives in `transcripts.structured_context_json`
- `/transcribe` EMIS context fields are hydrated from that transcript-backed state
- queued structured note generation updates the transcript-backed state and snapshots it into `generated_documents.structured_context_json`
- the transcript-backed state now persists per-section line arrays

### Stage 2: Line-based section editor

Status: implemented in first browser form

The workspace now uses a line-aware browser editor per section.

Current interaction:

- each section displays one or more line rows
- Enter creates a new line row
- Shift+Enter inserts a newline inside the current line row
- blank rows are ignored when syncing back into the hidden JSON payload

### Stage 3: Output-side line selection parity

The generated section display should mirror the editor model.

Add:

- default all generated lines to selected
- allow section-level select all / deselect all
- copy selected lines in EMIS order

### Stage 4: Editable generated sections

Allow users to edit generated section lines directly and persist them.

Recommended model:

- keep `generated_document_sections` as the saved output state
- split section text into line arrays in UI only unless line-level persistence becomes necessary

Only add a dedicated `generated_document_section_lines` table if real per-line persistence becomes necessary.

## Prompting contract

Structured EMIS generation should continue to require:

```json
{
  "title": "Two to three word summary",
  "content": {
    "problem": "...",
    "history": "..."
  }
}
```

Backend remains responsible for:

- validating allowed section keys
- dropping empty sections
- preserving canonical order
- rendering full note text
- persisting structured sections

Do not relax this contract just to compensate for UI/editor needs.

## Near-term implementation order

1. add `transcripts.structured_context_json`
2. load it into `/transcribe`
3. save it when generation is queued
4. snapshot it into `generated_documents.structured_context_json`
5. replace EMIS context textareas with line-aware section editors
6. add section-level select all / deselect all in output

Completed:

- 1 through 5 are now in place

Remaining:

- 6 section-level select all / deselect all in output

## Risks / assumptions

- transcript-backed EMIS working context is transcript-derived content and must remain owner-only
- transcript deletion must cascade through all transcript-derived EMIS outputs as it already does
- no leader/admin content access should be introduced by this feature
- do not log EMIS working context lines

## Architecture checkpoint summary

- Privacy boundaries: preserved if EMIS working context stays transcript-owned and owner-only
- Ownership rules: preserved if only the transcript owner can read/write the working context
- Deletion semantics: preserved if transcript-root deletion removes the working context and all derived outputs
- Provider rules: preserved if the LLM still receives only the resolved provider/model plus redacted transcript/context
- Structured-note contract: preserved if backend remains strict about allowed EMIS section keys and JSON shape
