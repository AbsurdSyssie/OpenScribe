# Transcribe Playwright Checklist

This checklist turns the current manual transcribe workspace checks into candidate Playwright coverage.

Primary target routes:

- `/transcribe`
- `/transcribe-glm-2`

Recommended seeded account:

- `dev.user@example.com` / `test1234`

## Baseline workspace load

- login as `dev.user@example.com`
- open `/transcribe`
- open `/transcribe-glm-2`
- verify the workspace loads without server errors
- verify recent sessions are visible in the session rail
- verify the active session title, status, transcript text, and model labels render
- verify the owner-only workspace API hydration does not blank the page after the first refresh

## Session management

- create a new session from the session rail
- verify the browser lands on the new active transcript
- edit the active session title
- blur or press Enter
- verify the new title persists after workspace refresh
- select a different recent session
- verify transcript, latest note, latest follow-up, and histories all change with the active session
- multi-select sessions in the rail and delete them
- verify the deleted sessions disappear and the app remains on a valid active transcript

## Pane layout behavior

- on `/transcribe-glm-2`, switch the assistant pane between:
  - `Hide`
  - `Split`
  - `Expand`
- verify:
  - `Hide` removes the assistant pane and leaves the transcript visible
  - `Split` shows transcript and assistant pane together
  - `Expand` gives the assistant pane the full workspace width
- switch assistant tabs:
  - `Clinical note`
  - `Follow-ups`
  - `History`
- verify tab state survives ordinary workspace polling and refresh

## Transcript actions

- verify transcript text renders in the transcript pane for an existing ready session
- use `Copy transcript`
- verify clipboard contents match the visible transcript text
- verify transcript stats update when switching to a session with different transcript text

## Note generation

- pick a freeform template
- verify EMIS context inputs are hidden
- generate a note
- verify:
  - success flash appears
  - latest note moves through `queued`/`processing`/`ready`
  - latest note content updates without a full page reload
  - session title updates from the generated note title when the session title was previously default/untitled
- switch to a structured template
- verify EMIS context inputs appear
- add text in multiple EMIS sections
- verify the EMIS context autosaves and survives a workspace refresh
- generate a structured note
- verify:
  - structured sections render
  - line checkboxes render
  - `Copy selected lines` copies only checked lines
  - `Clear selection` unchecks all lines

## Follow-ups and quick actions

- open the `Follow-ups` tab
- run a quick action
- verify latest follow-up moves through `queued`/`processing`/`ready`
- submit a custom follow-up request
- verify the latest follow-up content updates in place
- verify the follow-up request text is reflected in the latest follow-up metadata

## History

- open the `History` tab
- verify note history shows the generated notes for the active transcript
- verify follow-up history shows both freeform follow-ups and quick actions
- verify the history tab updates after new note or follow-up generation completes

## Upload and STT behavior

- if STT is configured for the seeded team:
  - upload an audio file
  - verify transcript status moves through `uploading`/`queued`/`transcribing`/`ready`
  - verify transcript text updates after processing
- if STT is not configured for the seeded team:
  - verify the UI clearly blocks or explains audio upload/microphone actions
  - verify the user-facing message explains the missing configuration state cleanly
- record a microphone batch
- verify:
  - timer starts
  - stop button becomes enabled while recording
  - upload queues when recording stops

## Redaction debug

- for localhost seeded dev accounts only
- open the latest note redaction debug panel
- verify the debug payload loads
- verify placeholder inventory and redacted text render
- if a note fails on invalid JSON, verify the debug panel can show the raw failed redacted provider output

## Failure-state checks

- use a transcript with a failed ingestion job
- verify the transcript workspace shows the latest ingestion error message
- verify failed note generation shows the note failure message
- verify failed follow-up generation shows the follow-up failure message

## Suggested first Playwright suite order

1. login and workspace load
2. session create / rename / switch
3. freeform note generation happy path
4. follow-up generation happy path
5. quick action happy path
6. structured EMIS happy path
7. history refresh
8. pane hide / split / expand behavior
9. upload happy path
10. failure-state coverage

## Manual observations from 2026-03-23

- `/transcribe-glm-2` loaded successfully for `dev.user@example.com`
- session switching and active-session rendering worked
- follow-up generation queued and refreshed to `ready` in place
- note generation queued and refreshed to `ready` in place
- the GLM 2 assistant pane hide / split / expand controls worked
- the rebuilt GLM 2 route kept the restored shell while using real session, transcript, note, and follow-up data
- the GLM 2 STT label now shows the configured team STT label instead of a misleading model-only fallback
