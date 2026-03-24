# Transcribe UI Brief

## Purpose

Design and implement a polished `/transcribe` screen for a clinical ambient voice scribing app.

This is the main clinician workspace for:
- capturing or uploading consultation audio
- reviewing the transcript
- generating notes from templates
- generating follow-ups
- running quick actions

The UI should feel calm, efficient, trustworthy, and clinically appropriate. It should feel like a serious working screen for a clinician, not a generic dashboard or admin page.

You have freedom over:
- layout
- structure
- visual language
- component hierarchy
- interaction patterns

But the screen must support the data, workflows, and constraints below.

## Product framing

- This is a clinician-facing ambient voice documentation workspace.
- It should be desktop-first.
- It should support long transcript and note content comfortably.
- It should reduce the feeling of page refreshes and broken flow.
- It should keep the user oriented around one active transcript session.
- It should feel modern and product-quality without becoming noisy or playful.

## Backend data the screen receives

The transcribe screen is driven by an owner-only workspace payload and related API responses.

### Active transcript/session

- `id`
- `title`
- `status`
- `ingestion_mode`
- `current_draft_text_encrypted`
- latest ingestion error message if present

### Recent transcript/session list

For each session:
- `id`
- `title`
- created timestamp
- `status`
- `ingestion_mode`

### Available note templates

For each template:
- `id`
- `name`
- `scope`
- latest version mode:
  - `freeform`
  - `structured`

Structured templates may also carry EMIS section configuration.

### Available quick actions

For each quick action:
- `id`
- `name`
- `scope`

### Active structured context for the transcript

Transcript-backed EMIS context already saved on the session, keyed by section.

### Generated documents for the transcript

This includes:
- note outputs
- follow-up outputs
- quick-action outputs

Available fields can include:
- `id`
- `title`
- `status`
- `generator_type`
- `document_mode`
- `model_used`
- `created_at`
- full generated text
- structured sections, if applicable
- error message, if failed

### Provider/model state

- active STT selection
- active LLM selection
- resolved user LLM model

### Dev-only debug metadata

For localhost seeded dev accounts only, a dev redaction debug view may be available.

## User inputs into the screen

The user must be able to:

- create a new session
- select an existing session
- delete one or more sessions
- edit the active session title
- upload an audio file for transcription
- record a microphone batch for transcription
- choose a note template
- enter structured EMIS context when the selected template is structured
- generate a note
- type a freeform follow-up request
- generate a follow-up
- choose and run a quick action
- select and deselect structured output lines before copying

## What the screen sends back to the backend

The UI must support these backend mutations:

### Session mutations

- create session
- delete session(s)
- update transcript/session title
- update transcript structured EMIS context

### Transcription mutations

- queue audio-file transcription
- queue microphone-batch transcription

### Note generation mutation

Send:
- `transcript_id`
- `template_id`
- `structured_context` only when the selected template is `structured`

Do not send structured context for freeform templates.

### Follow-up generation mutation

Send:
- `transcript_id`
- follow-up prompt text

### Quick action mutation

Send:
- `transcript_id`
- `quick_action_id`

## What the user needs to get out of the screen

The screen should make these things clear and usable:

- what the active session is
- whether transcription is idle, queued, transcribing, ready, or failed
- what transcript text currently exists
- whether note generation is queued, processing, ready, or failed
- the latest note output
- the latest follow-up or quick-action output
- history of generated notes
- history of follow-up and quick-action outputs
- visible error states and useful failure messages
- which lines of a structured note are selected for copy

## Required functional distinctions

### Template mode matters

#### Freeform template

- no EMIS section context editor should be shown
- generation should be freeform
- output should render as freeform text

#### Structured template

- show EMIS section context editor
- generation uses structured note JSON
- output should render as EMIS-style structured sections

### Structured EMIS section keys

Allowed EMIS sections:
- `problem`
- `history`
- `family_history`
- `social_history`
- `examination`
- `comment`
- `tasks`
- `investigations`

Templates may remove or reorder sections.

## Current client-side behavior that should be understood

These behaviors exist in the current HTML transcriber and should be preserved or consciously redesigned rather than accidentally dropped.

### Workspace/session behavior

- The UI is centered around one active transcript session.
- Session switching refreshes the active workspace without relying entirely on a full reload.
- The main session title in the workspace is editable.
- Title save currently happens on:
  - blur
  - Enter

### Transcription behavior

- Whole-file upload is supported.
- Microphone batch recording is supported.
- Upload/mic controls live in the header bar in the current HTML version.
- Transcription status is surfaced to the user.
- Ingestion failures can be shown as owner-visible error messages.

### Note-generation behavior

- Template selection drives whether the output is freeform or structured.
- Structured EMIS context is transcript-backed and can persist between generations.
- EMIS context is autosaved to the transcript session.
- Freeform templates should not show the EMIS editor or send structured context.

### Structured note behavior

- Structured notes render by section.
- Individual structured lines can be selected or deselected.
- Copy action should copy only the selected lines.

### Follow-up and quick action behavior

- Follow-up generation uses freeform user prompt text.
- Quick actions run from a dropdown of configured actions.
- Both are tied to the active transcript.

### Refresh/progress behavior

- The workspace refreshes/polls to keep transcript and generated-document state current.
- The UI should minimize the feeling of disruptive refreshes.

## UX expectations

- The user should always know which session they are in.
- The active session title should be editable in the main workspace.
- The title should not be redundantly repeated throughout the main canvas.
- Transcript, note generation, and follow-up work should be easy to understand as related but distinct activities.
- Long text should remain readable and scannable.
- Queued/processing/failed states should be obvious.
- Structured notes should be especially easy to review line by line.

## Clinical design expectations

- The screen should feel suitable for clinical work.
- It should feel focused, efficient, and low-friction.
- It should not feel like a chat app, social feed, or consumer content tool.
- The output area should support close reading and copying into a clinical record system.

## Constraints

- Desktop-first
- Long text support
- Must support both freeform and structured templates cleanly
- Must preserve session switching, upload, microphone batch, generation, history, and copy behavior
- Must preserve the distinction between structured and freeform note generation

## Design freedom

You can choose:
- overall information architecture
- whether transcript/output/follow-ups are tabs, panes, or another pattern
- session rail design
- action placement
- visual hierarchy
- spacing and typography
- color system
- status presentation
- history presentation

You do not need to keep the current HTML structure if you can produce a better screen.

## Must-not-lose capabilities

- session switching
- session creation
- session deletion
- editable session title
- file upload transcription
- microphone batch transcription
- transcript display
- note template selection
- freeform vs structured template distinction
- EMIS context editor only for structured templates
- note generation
- follow-up generation
- quick action execution
- structured line selection and copy
- generated output history
- visible and useful error states

## Success criteria

A successful transcribe UI should make it easy for a clinician to:
- understand which consultation/session they are working in
- capture or upload audio
- review the transcript
- generate a note
- distinguish structured and freeform workflows
- review structured notes line by line
- copy the right output into an external record system

The end result should feel meaningfully more polished and product-quality than a basic CRUD/admin interface.

## Instruction to the implementing agent

Use the product, data, and behavior requirements above, but choose the UI structure and appearance yourself.
