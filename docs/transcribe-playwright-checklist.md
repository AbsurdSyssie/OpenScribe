# Scribe Workspace Playwright Coverage Roadmap

## Status

This is a browser-test coverage roadmap, not proof that every item is automated. The canonical Scribe route is `/workspace`. `/transcribe` is a compatibility redirect. Retired prototype routes must not replace canonical coverage.

The existing optional browser regression in `tests/test_csrf_browser.py` verifies real login, compatibility redirect, canonical workspace loading, transcript creation, and session-bound CSRF behavior. Broader Scribe E2E coverage remains incremental.

Recommended disposable account for local browser tests:

- `dev.user@example.com`
- password from `DEV_TEST_USER_PASSWORD` (sample default `test1234`)

The seeded development account is localhost-only, has onboarding complete, and has MFA disabled for controlled local tooling. Never use seeded accounts or real patient content in a shared/production browser test.

## Route coverage

Required:

- `/workspace` canonical shell;
- full normal-user login redirects directly to `/workspace`;
- `/transcribe` temporary redirect preserving only validated `transcript_id`;
- `/settings` compatibility redirect through its closed tab/query map;
- legacy `GET /home` temporarily redirects allowlisted tabs/selected assets into canonical workspace sections and never renders the retired landing.

Retired-route coverage:

- `/transcribe-glm-2`, `/transcribe-claude`, and `/transcriber_col_changes` are removed prototype routes and should return the normal not-found response.

## Baseline workspace load

- log in as the seeded user;
- open `/workspace`;
- verify the shared workspace shell, Scribe section, and consultation rail render;
- verify workspace hydration does not blank the page after polling/refresh;
- verify owner transcript title/status/content and provider labels render for an existing fixture consultation;
- verify Account, Preferences, and Library pages do not load/decrypt Scribe consultation history;
- verify system-admin accounts redirect to `/admin` rather than rendering user workspace.

## Consultation lifecycle

- create a new consultation from Scribe;
- verify the browser selects the returned transcript root;
- rename the active consultation and verify persistence after refresh;
- select another owner consultation and verify transcript, working note, dictation, generated-document, follow-up, and history state all switch together;
- verify the remembered `sessionStorage` transcript UUID is only a navigation hint and cannot open another owner's transcript;
- multi-select/delete consultations where the current UI permits it;
- verify hard-deleted consultations disappear and the workspace chooses a valid remaining/new state.

## Working note

Freeform:

- select/open Working note;
- enter text and verify save-state transitions;
- refresh and verify encrypted server-backed content is restored;
- use a Smart Phrase and verify expansion/usage behavior;
- queue generation and verify dirty content is saved before enqueue;
- verify generation blocks after a failed or stale save.

Structured:

- choose structured mode before first non-empty save;
- enter multiple allowed EMIS sections;
- verify hidden sections survive template changes and refresh;
- verify the first non-empty save locks mode;
- verify mode switching is rejected until the note is cleared;
- clear with confirmation and verify mode unlocks without deleting transcript/generated output;
- verify optimistic-concurrency conflict handling across two browser contexts/tabs.

## Transcript and note editor

- verify transcript text/stats for an existing ready consultation;
- verify transcript copy writes the visible owner-authorized text;
- select freeform and structured output templates;
- verify output/editor controls match template mode;
- verify generated-note edits use optimistic concurrency and persist after refresh;
- verify generated-note edits do not modify Working note;
- verify structured section/line selection and copy behavior;
- verify editor reordering and keyboard controls where applicable.

## Generation

Template note:

- queue generation with transcript-only, Working-note-only, dictation-only, and combined saved sources;
- verify all-source-empty generation is blocked;
- verify one client-side in-flight guard prevents duplicate note enqueue;
- verify status transitions `queued` -> `processing` -> `ready` or controlled `failed`;
- verify the newest result refreshes without full page reload;
- verify source edits after enqueue do not change the stored generation snapshot.

Follow-ups and Quick Actions:

- verify they can queue independently of an in-flight template note where intended;
- verify saved Working note/dictation are included automatically as labelled sources;
- verify additional context and Quick Action context preview are transient until the normal generation request;
- verify result/history refresh and controlled failures;
- verify no existing generated note is required when another valid saved source exists.

## Post-consultation dictation

- opening the modal does not start recording automatically;
- preview audio returns editable text without durable dictation creation;
- Cancel discards unsaved preview state;
- Save creates/updates the transcript-owned dictation aggregate;
- subsequent segments append in order;
- editing combined text makes it authoritative for later generation;
- intentionally clearing edited combined text suppresses segment fallback;
- Save & generate uses the selected template and the shared generation guard.

## File upload and microphone batch

With a configured STT selection:

- upload the bundled synthetic audio fixture;
- verify accepted job metadata and status transitions;
- verify transcript text updates after worker processing;
- verify individual/hourly request, byte, and duration errors are controlled;
- verify source-audio retry is offered only when a bounded retry reference exists;
- record a microphone batch and verify local rollover before server caps;
- verify each rollover part stays attached to the transcript captured at recording start;
- verify a failed rollover stops capture rather than silently continuing with a gap.

Without a configured STT selection:

- verify recording/upload actions are disabled or return the documented bounded configuration error;
- verify no credential or unrestricted provider metadata is exposed.

## Live chunked capture

- start live recording and verify recording navigation lock/unload warning;
- verify VAD activity/speech-state UI;
- verify sequence numbers increase and refresh uses the server-advertised next sequence;
- verify route-rate-limit retry uses the same sequence and honors `Retry-After`;
- verify quota/validation/authorization failures stop automatic retry;
- verify forced-flush/pause chunks appear in order;
- verify Stop finalizes recording while queued chunks continue processing;
- open/create another consultation and verify prior chunks remain attached to the original root;
- verify the inactivity prompt does not stop/finalize/upload by itself;
- verify backgrounding/unload sends best-effort local pause/finalize behavior without claiming guaranteed delivery.

## Consultation rail and layout

- verify recent consultation rail open/close and `open_recent=1` URL cleanup;
- verify selection/focus survives ordinary polling;
- verify Scribe owns bounded internal scrolling while shared navigation remains visible;
- verify non-Scribe workspace pages use normal document scrolling;
- verify desktop collapse and mobile off-canvas navigation;
- verify the closed mobile drawer is inert;
- verify recording disables only `data-recording-navigation` controls.

## Account, Preferences, and Library integration

- Account sensitive changes require password/fresh TOTP where applicable and rotate/revoke authority correctly;
- recording/writing preferences persist and affect Create controls;
- personal/team asset authorization remains role-correct;
- Template/Quick Action import preflight is read-only and confirmation creates only selected authorized assets;
- Smart Phrases remain personal;
- non-Scribe pages never expose transcript-derived content through shell context.

## Security checks

- session/trusted-device/CSRF cookies remain `HttpOnly`;
- unsafe cookie-authorized API requests carry correct `Origin` and session-bound `X-CSRF-Token`;
- session rotation invalidates the previous CSRF token;
- CSP blocks unapproved inline/third-party runtime dependencies;
- transcript/generated/account pages remain `no-store`;
- cross-owner object addressing returns the documented non-disclosing response;
- localhost redaction debug is available only to the seeded owner under the local-debug dependency.

## Failure-state coverage

- ingestion provider/credential/deadline failure;
- queued work awaiting worker/Beat;
- generation provider failure, malformed JSON, and truncation;
- Working-note optimistic conflict/save failure;
- dictation preview/save failure;
- Vault/content-decryption failure produces a controlled error with no plaintext fallback;
- deleted/expired consultation becomes unavailable during polling/navigation;
- session expiry/partial-auth redirect from an open workspace.

Assertions must use safe metadata and synthetic fixture text. Do not snapshot credentials, patient content, raw provider responses, reset/setup tokens, or TOTP/recovery values.

## Suggested automation order

1. canonical login/workspace and compatibility redirects;
2. consultation create/rename/switch/delete;
3. Working-note save/mode-lock/clear/conflict;
4. freeform template generation;
5. follow-up and Quick Action generation;
6. post-consultation dictation preview/save/generate;
7. structured editor and Smart Phrase behavior;
8. whole-file upload with fake/local STT adapter;
9. live capture with controlled media/VAD stubs;
10. responsive navigation and failure-state matrix.

## Historical observation

The March 23, 2026 manual notes for `/transcribe-glm-2` were observations of a preview surface at that date. They are not current release evidence and have been removed from the operational checklist. Future manual/browser evidence should be stored under a dated evidence/test-result path with the commit/environment identified.
