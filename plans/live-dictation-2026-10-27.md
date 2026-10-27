# Live dictation implementation — 27 October 2026

Status: implemented and checked on 27 October 2026. This plan records the approved work; maintained behavior is documented in docs/workspace.md, docs/live_stt.md and docs/api.md.

## Approved behavior

- Reuse consultation recording/capture and streaming primitives in dictation.
- Live is initially available only for eligible Deepgram dictation selections.
- Dictation remains a separate owner-only transcript-owned source, independent of consultation capture mode.
- Text and audio remain transient until explicit Save; durable usage and stream lifecycle metadata is permitted.
- Provisional words appear grey; provider-final words appear black. Confirmation is transcription finality, not clinician approval or persistence.
- Preserve ordinary recording/upload, Stop, optional pause/resume, preview retry, editable saved text, Cancel, Save, and Save & Generate.

## Boundaries

- Add an explicit server-owned stream-session purpose. Existing sessions default to conversation.
- Reuse authenticated relay, PCM transport, provider protocol, claims, and accounting. Keep the dictation provider purpose and credential snapshot separate.
- Dictation checkpoints settle permitted usage metadata and deliver text to the modal; they never write consultation text, turns, dictation source rows, or Vault recovery audio.
- Failures terminalize transient dictation sessions with accounted usage. Previously received text stays in the tab; incomplete output requires explicit user action, with no automatic replay.
- Keep one open stream per root. Exclude dictation metadata from consultation gap, retry, recovery, and source-readiness projections.
- Grey/black live tail stays inside the existing editor; ordered checkpoints append once without replacing clinician edits. Save waits for final drain and excludes provisional text.

## Ownership

- Backend agent: stream purpose migration/model, relay/services and backend regressions, API/live lifecycle docs.
- Frontend agent: shared capture helper, transport integration, modal UI/state, frontend regressions and workspace docs.
- Parent: capability projection/schema and focused tests, integration review, serialized test execution, browser/layout verification, authorization audit and documentation checks.

## Verification

Run focused tests with the project virtual environment and APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app. Pytest and API authorization audit run sequentially under the established infrastructure lock. Verify synthetic provider interims/finals, duplicate/out-of-order results, final drain/save, cancellation/startup races, provider eligibility, ownership, retained-root denial, absent persisted preview content/audio, quota settlement, migration compatibility, main recorder regression, and desktop/mobile layout.

## Verification completed

- Backend/provider capability, WebSocket authorization/lifecycle and migration upgrade checks: 70 passed in the focused backend run.
- Additional dictation lifecycle and generation barrier checks: 11 passed; recovery service purpose guards and duplicate checkpoint assertions also passed in the frontend/integration selection.
- Frontend capture, live transport, dictation actions, layout contracts and source-isolation checks: 51 passed in the focused run.
- Final dictation actions and workspace contracts: 32 passed; the separate PCM recovery regression passed after adding the new shared module to its isolated fixture.
- API authorization audit, Python compilation, JavaScript syntax checks and maintained-document validation passed.
- Synthetic browser checks covered eight sizes, eight states, preview visibility, editor sizing, footer access and hidden controls. No real provider or microphone was exercised.
- Deployment requires the stream-purpose migration and coordinated application/worker update. The working tree contained unrelated pre-existing changes, which remain separate from this task.

## Pre-release regression review

Independent backend and frontend reviews found one further issue: a second live
capture in the same modal reused the preceding stream's checkpoint deduplication
set. Reset the set at each capture start. A real-handler regression verifies
that each stream's window zero is appended once and both captures are saved.
The regression failed without the reset and passed with it. The follow-up
review found no remaining concrete defects in the dictation scope.

The combined pre-release selection passed 145 tests. Source deployment scope
remains to be selected because this working tree also contains independent
admin/quota work and the dictation migration currently depends on its quota
migration. The remote checkout was clean and its runtime healthy during the
read-only preflight; no release was pushed or deployed at that point.

The user subsequently authorized review and release of all needed pending
changes, including the admin work. The expanded review identified an existing
pending quota-accounting policy change: unlimited-to-finite activation would
count earlier current-period usage instead of starting at activation. The user
confirmed the established prospective accounting should be preserved, and the
activation boundary was restored. Independent
admin and quota reviews otherwise found no concrete runtime regression.

Admin browser accessibility verification passed 14 tests across desktop and
mobile. A focused no-screenshot run passed after using the existing optional
evidence helper. Quota checks passed 32 tests; charts passed one; the focused
quota migration selection passed two; network guard/harness checks passed 15.
Six stale workspace/cache-key assertions were reconciled and passed. The
broader admin regression expectations are being reconciled with the approved
canonical Usage and sidebar behavior before release.

The expanded admin review completed with 315 tests passing (two dependency
deprecation warnings). All 22 initial failures were stale route, markup, asset
version or source-string expectations. Their replacements preserve the
approved flows, metadata-only diagnostics, authorization/CSRF, team isolation,
provider actions and secret-reference checks. An independent re-review found
no coverage regression. The diagnostics test counts visible cells separately
from sorting attributes and still verifies the 25-row service bound. No further
runtime changes were needed. Diff checks and 54-document validation passed.
The final quota-policy restoration passed all 27 user-quota tests. The service
activation logic matches the released implementation; only the display helper
remains changed in that service. Push and deployment follow this verification.
