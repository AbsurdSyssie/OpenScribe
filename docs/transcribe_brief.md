# Transcribe Brief

## Purpose

This document inventories what `/transcribe` contains today, without tying future design work to the current arrangement of panes, tabs, controls, or sections.

Goal:
- describe what information exists
- describe what can be changed
- describe who can change it
- describe what behavior must survive redesign

This page is owner-only. Team leaders and system admins do not gain transcript readability from role alone.

## Audience and access

### Primary user

- the owning clinician
- this may be a normal user or a leader acting as a normal content owner

### Not granted access here by default

- other normal users
- team leaders looking at someone else’s content
- system admins

## Information Inventory

### Consultation/session context

Contained information:
- current active consultation identity
- consultation title
- consultation status
- consultation ingestion mode
- recent consultation list
- created timestamps
- consultation readiness or failure messaging

User can change:
- create a consultation
- switch active consultation
- rename consultation
- delete consultation
- create/open another consultation while a previous stopped recording is still queued or transcribing

How:
- start/open another consultation
- edit title inline
- remove one or more consultations

Access:
- owner only

### Audio capture and ingestion state

Contained information:
- whether speech-to-text is configured and available
- current recording mode
- upload availability
- microphone/live capture availability
- status-pill ingestion progress and retry status
- duration timer
- ingest failure messages
- toast-only blocked navigation/session creation feedback

User can change:
- choose recording mode
- upload audio file
- record audio
- retry failed file ingestion when retry audio still exists
- choose whether a new microphone recording should continue the current ready consultation or start a fresh consultation when the previous transcript completed more than 30 seconds ago

How:
- choose available recording mode
- start/stop recording
- upload file
- trigger retry
- try blocked navigation/session creation and receive a toast without sidebar layout changes
- if recording into a stale ready consultation with transcript content, answer the local confirmation prompt; choosing a new consultation creates a fresh transcript root and starts recording there

Access:
- owner only

### Transcript draft content

Contained information:
- current draft transcript text
- live updates from polling/SSE
- empty-state guidance when no transcript exists

User can change:
- indirectly, by recording or uploading audio

How:
- capture or submit audio and wait for transcript updates

Access:
- owner only

Notes:
- transcript text is not directly editable here
- transcript content is private and non-shareable

### Note generation context

Contained information:
- available note templates
- template names
- template scope visibility
- note mode per template
  - freeform
  - structured
- current selected template
- latest note output state
- note generation status
- note history
- in-panel loading animation while note generation is queued or processing

User can change:
- choose template
- generate note
- switch between available outputs/history

How:
- select template
- trigger generation against current consultation content

Access:
- owner only

### Editable note content

Contained information:
- empty editable note state before generation
- structured note sections when template mode is structured
- freeform note lines when template mode is freeform
- saved generated note content
- per-note autosave/conflict state

User can change:
- type note content directly
- add/remove/revise structured statements
- add/remove/revise freeform lines
- reorder non-empty structured/freeform lines by drag handle or keyboard shortcut
- continue editing generated notes after creation

How:
- edit note lines in place
- autosave on debounce/blur
- hide the editable-note empty guidance as soon as structured or freeform note rows contain text
- blank placeholder lines cannot be moved; blocked keyboard reorder shortcuts are still consumed so browser history navigation does not fire
- explicit note switching changes which note is being edited

Access:
- owner only

Notes:
- structured templates use EMIS section keys only
- freeform notes must not show structured headings
- changing template while editing an existing structured note must not silently drop saved sections

### Structured-note selection and copy state

Contained information:
- selected vs unselected structured lines
- copy-ready structured output subset
- section-level structured output text

User can change:
- select or deselect structured note statements for copy
- copy a single structured section from its header without changing line selection

How:
- toggle line selection in structured output
- use a section header copy button; this copies only that section body, not the section heading
- for LLM-generated notes, scroll to the bottom of a structured section before copying that section
- for LLM-generated freeform notes, scroll to the bottom of the generated note before copying selected note lines
- if the copyable generated-note text changes after review, scroll to the updated bottom again before copying
- hidden output panes do not count as reviewed for generated-note copy
- blocked generated-note copy controls remain clickable and explain the block with a toast
- blocked copy attempts surface as a toast instead of an inline alert

Access:
- owner only

### Structured EMIS context

Contained information:
- transcript-backed structured context keyed by allowed EMIS section keys
- saved section guidance for the active consultation

User can change:
- structured context content for the active consultation

How:
- edit structured context lines while working
- autosave context back to the consultation

Access:
- owner only

Allowed EMIS section keys:
- `problem`
- `history`
- `family_history`
- `social_history`
- `examination`
- `comment`
- `tasks`
- `investigations`

### Follow-up generation

Contained information:
- current follow-up prompt text
- quick-action guidance text
- latest follow-up output
- follow-up generation status
- in-panel loading animation while follow-up generation is queued or processing
- current-transcript follow-up recents
- empty-state guidance when nothing exists yet

User can change:
- enter a custom follow-up request
- generate follow-up output
- edit the selected ready follow-up title and body
- select a recent output for the current consultation

How:
- select a quick action and optionally add context
- type secondary free-text request text
- trigger follow-up generation
- type directly into the selected generated follow-up title/body; autosave uses the owner-only generated-document edit route
- click current-transcript recents to load the selected output
- if a ready follow-up has unsaved edits, switching or workspace refresh saves/preserves the current follow-up before another output is rendered

Access:
- owner only

Notes:
- the Follow Ups tab is a two-panel workspace: selected generated follow-up on the left, context/quick-action controls on the right
- current-transcript recents sit under the selected follow-up in the left output panel
- the generated follow-up panel title is the selected generated document name, with generated text emphasized inside the scrollable output card
- ready follow-up and quick-action outputs expose editable title and body fields; queued, processing, failed, and empty states remain read-only
- the right builder uses one follow-up context textarea; if no quick action is selected, Generate creates a free-text follow-up, and if a quick action is selected, Generate applies that saved format to the same context
- all available quick actions are visible in the right builder step 2, with search, selected-card highlight, and a remove control
- selecting a quick-action card changes selection before generation; it does not auto-run
- the right builder keeps the LLM request hidden until the step 3 `Show request` toggle is used; the same toggle changes to `Hide request` while open

### Quick actions

Contained information:
- available quick actions
- quick-action scope visibility
- quick-action search and card list
- optional one-off guidance text
- latest quick-action output
- quick-action history mixed into follow-up-type output history

User can change:
- choose which quick action to run
- use quick-pick favourites
- add one-off extra guidance
- trigger quick action generation

How:
- select a quick-action card
- search visible quick actions
- add optional follow-up context in the combined builder
- run generation

Access:
- owner only

Notes:
- quick actions and follow-ups should unlock when transcript text or note content exists
- visible follow-up/quick-action content must refresh when switching consultations
- quick actions are ordered by default preference, then favourites, then name when usage counts are not available

### Service/model context

Contained information:
- active team STT selection
- active team LLM selection
- resolved user LLM model

User can change:
- nothing directly from this page except following links back to broader settings/configuration pages

Access:
- owner only for visibility of resolved state

### Debug and developer metadata

Contained information:
- dev-only redaction/debug details in local development contexts

User can change:
- nothing meaningful in normal workflow

Access:
- owner only, and only in development/debug conditions

## Capability Summary

### Owner can

- create, switch, rename, and delete consultations
- upload audio
- record audio
- choose recording mode
- retry failed transcription when retry source exists
- review live transcript updates
- choose note template
- edit note content before and after generation
- edit structured context for structured workflows
- generate note
- select structured lines for copy
- write custom follow-up requests
- run quick actions
- review note/follow-up/quick-action history

### Owner cannot

- view another user’s transcript-derived content
- use this page to alter team provider policy directly
- use this page to expose transcript content to leaders or admins

### Leader role does not add here

- no extra readability into other users’ consultations
- no implicit access to transcript/note content just because the owner is on the same team

## Redesign constraints

The redesign must preserve:
- owner-only access
- active-consultation orientation
- consultation switching
- consultation creation and deletion
- editable consultation title
- upload and recording flows
- transcript live updates without manual refresh
- freeform vs structured note distinction
- EMIS context only for structured workflows
- editable empty note state
- note autosave with conflict safety
- follow-up generation
- quick action generation
- output history
- visible error and retry states

## Design implications

- this is a long-duration working screen, not a simple dashboard
- it needs to hold:
  - live capture state
  - transcript state
  - editable note state
  - generated-output state
  - historical output state
- some information is transient and live
- some is editable draft state
- some is generated artifact history
- the UI should make those differences obvious without losing flow
- on narrow screens, the recent-consultation rail may collapse into an off-canvas drawer so the owner workspace remains usable without changing any content access or transcript-root behavior

## Recommended use of this brief

Use this document as a capability map:
- not “what current layout looks like”
- but “what `/transcribe` must contain and protect”

That should let the UI evolve more freely without dropping core behavior or privacy boundaries.
