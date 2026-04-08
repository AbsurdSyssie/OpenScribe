# Transcribe Frontend Refactor Plan

## Goal

Break the transcribe page into maintainable frontend modules without changing the ownership, privacy, deletion, provider, or encryption model.

## Why

- `app/templates/transcribe.html` grew into a large mixed template with a multi-thousand-line inline script.
- The backend browser routes are already extracted, so the main maintainability problem has moved to the client-side transcribe shell.
- The page needs cleaner seams for:
  - workspace transport
  - live capture
  - generated document switching
  - guided onboarding

## Constraints

- Keep transcript-derived content owner-only.
- Do not move content-bearing logic into the browser beyond what the page already renders for the owner.
- Do not change API routes or response shape just to support the refactor.
- Keep the page working with the current SSE transport and live-capture model.

## Target module shape

1. `app/static/js/transcribe/bootstrap.js`
   - Reads a JSON bootstrap payload rendered by Jinja.
   - Removes Jinja state from executable JavaScript.

2. `app/static/js/transcribe/app.js`
   - Main coordinator for cross-module state and startup only.
   - Keeps the page-level state machine readable instead of owning every feature directly.

3. `app/static/js/transcribe/documents.js`
   - Handles note/message version selection and history rendering.

4. `app/static/js/transcribe/tour.js`
   - Handles the guide overlay and step navigation.

5. `app/static/js/transcribe/structured.js`
   - Handles sectioned note editing, structured draft rendering, and EMIS context syncing.

6. `app/static/js/transcribe/media.js`
   - Handles microphone recording, live capture, VAD chunking, and timer state.

7. `app/static/js/transcribe/actions.js`
   - Handles document/session/form event wiring.

8. `app/static/js/transcribe/layout.js`
   - Handles split-pane sizing, tab state, and related settings-link updates.

## Implementation order

1. Mount `/static` in FastAPI.
2. Replace the inline transcribe script with:
   - a JSON bootstrap script tag
   - a `type="module"` loader for `app.js`
3. Move the current inline script body into `app.js`.
4. Remove Jinja expressions from JS and read them from the bootstrap payload instead.
5. Extract the document-switching and guide overlay logic into dedicated modules.
6. Run syntax checks and a focused browser smoke pass when practical.

## Implemented status

This refactor is now materially complete for the transcribe shell:

- `transcribe.html` is now a small wrapper template with Jinja includes
- the inline client app is gone
- `app.js` is down below 1k lines and acts as the coordinator only
- structured editing, media capture, document switching, tour flow, layout state, and form wiring all live in dedicated modules

## Follow-on cleanup

- split workspace transport/SSE handling out of `app.js` if we want an even thinner coordinator
- add a focused browser smoke pass for the include-based template layout
- continue the same pattern on any other page that starts accumulating large inline behavior
