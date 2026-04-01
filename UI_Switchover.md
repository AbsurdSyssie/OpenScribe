# UI Switchover

This document tracks the server-rendered pages that had restyled replacements and the parity checks required to move them onto the live template filenames.

Current state:

- all in-scope live routes now render the new UI through the original template filenames
- preview aliases that remain (`/home-restyled`, `/admin-restyled`, `/transcribe-glm-2`) are still available for QA
- `/home-restyled` now renders the live [app/templates/home.html](/home/oscar/Documents/Code_Projects/OpenScribe/app/templates/home.html) template with alias-aware form context
- `/admin-restyled` now renders the live [app/templates/admin.html](/home/oscar/Documents/Code_Projects/OpenScribe/app/templates/admin.html) template with alias-aware form context
- transcribe-to-home template and quick-action editor flows now return to `/transcribe` instead of leaving the user on `/home`
- the remaining work is cleanup of preview aliases once they are no longer needed

## Pages In Scope

Restyled routes currently tracked in the repo:

- `login`:
  - old: [app/templates/login.html](/home/oscar/Documents/Code_Projects/OpenScribe/app/templates/login.html)
  - restyled UI now lives in the same file
- `request-access`:
  - old: [app/templates/request_access.html](/home/oscar/Documents/Code_Projects/OpenScribe/app/templates/request_access.html)
  - restyled UI now lives in the same file
- `mfa-challenge`:
  - old: [app/templates/mfa_challenge.html](/home/oscar/Documents/Code_Projects/OpenScribe/app/templates/mfa_challenge.html)
  - restyled UI now lives in the same file
- `home`:
  - old: [app/templates/home.html](/home/oscar/Documents/Code_Projects/OpenScribe/app/templates/home.html)
  - preview alias already exists: [app/main.py](/home/oscar/Documents/Code_Projects/OpenScribe/app/main.py) `GET /home-restyled`
- `admin`:
  - old: [app/templates/admin.html](/home/oscar/Documents/Code_Projects/OpenScribe/app/templates/admin.html)
  - preview alias already exists: [app/main.py](/home/oscar/Documents/Code_Projects/OpenScribe/app/main.py) `GET /admin-restyled`
- `transcribe`:
  - old: [app/templates/transcribe.html](/home/oscar/Documents/Code_Projects/OpenScribe/app/templates/transcribe.html)
  - new live target now moved into: [app/templates/transcribe.html](/home/oscar/Documents/Code_Projects/OpenScribe/app/templates/transcribe.html)
  - current preview route already exists: [app/main.py](/home/oscar/Documents/Code_Projects/OpenScribe/app/main.py) `GET /transcribe-glm-2`

Not in scope for this switchover document:

- onboarding: no restyled template exists yet

## Cross-Cutting Switchover Rules

Every switchover must preserve:

- existing route auth and redirect behavior from [app/main.py](/home/oscar/Documents/Code_Projects/OpenScribe/app/main.py)
- browser CSRF protection via `{% include "_csrf_script.html" %}`
- all existing form field names and action URLs
- existing role-gated sections and server-rendered message blocks
- destructive-action confirmation prompts
- no reduction in visibility of security-relevant actions, status, or error states

## `login`

### Existing live page

- route: `GET /login`, `POST /login`, `POST /bootstrap/system-admin`
- render helper: `render_auth_page(...)` in [app/main.py](/home/oscar/Documents/Code_Projects/OpenScribe/app/main.py)
- security behavior:
  - redirects authenticated users away from `/login`
  - browser CSRF required on POST
  - rate limit on login POST
  - supports bootstrap admin creation while user count is zero
  - surfaces server-rendered error messages

### Restyled page status

- the live [app/templates/login.html](/home/oscar/Documents/Code_Projects/OpenScribe/app/templates/login.html) now carries the restyled login UI
- the login UI preserves:
  - request-access link
  - login form
  - bootstrap form
  - message rendering
  - `{% include "_csrf_script.html" %}`

### Completion notes

- completed: moved the restyled login UI into [app/templates/login.html](/home/oscar/Documents/Code_Projects/OpenScribe/app/templates/login.html)
- completed: kept bootstrap visibility keyed off `bootstrap_allowed`
- add/keep tests for:
  - authenticated redirect away from `/login`
  - login form error rendering
  - bootstrap form visibility and POST flow
  - CSRF rejection on browser POST without token

## `request-access`

### Existing live page

- route: `GET /request-access`, `POST /request-access`
- render helper: `render_request_access_page(...)`
- security behavior:
  - public but rate-limited
  - browser CSRF required on POST
  - server-rendered success and error messages
  - exact field names must match `requested_name`, `requested_email`, `requested_team_name`, `request_details`

### Restyled page status

- the live [app/templates/request_access.html](/home/oscar/Documents/Code_Projects/OpenScribe/app/templates/request_access.html) now carries the restyled public request form
- it preserves:
  - same form action and field names
  - message rendering
  - back-to-login link
  - `{% include "_csrf_script.html" %}`

### Completion notes

- completed: moved the restyled request-access UI into [app/templates/request_access.html](/home/oscar/Documents/Code_Projects/OpenScribe/app/templates/request_access.html)
- completed: kept the public field names and message flows unchanged
- add/keep tests for:
  - successful submission
  - duplicate request error
  - rate-limit error
  - CSRF rejection on browser POST without token

## `mfa-challenge`

### Existing live page

- route: `GET /mfa/challenge`, `POST /mfa/challenge`, `POST /logout`
- render helper: `render_mfa_challenge(...)`
- security behavior:
  - pending-MFA-only route
  - redirects unauthenticated users to `/login`
  - redirects fully authenticated users away from challenge
  - browser CSRF required on challenge submit and logout
  - rate limit on MFA POST
  - remember-device checkbox preserved

### Restyled page status

- the live [app/templates/mfa_challenge.html](/home/oscar/Documents/Code_Projects/OpenScribe/app/templates/mfa_challenge.html) now carries the restyled MFA challenge UI
- it preserves:
  - code field
  - remember-device checkbox
  - logout form
  - message rendering
  - `{% include "_csrf_script.html" %}`

### Completion notes

- completed: moved the restyled MFA challenge UI into [app/templates/mfa_challenge.html](/home/oscar/Documents/Code_Projects/OpenScribe/app/templates/mfa_challenge.html)
- completed: preserved current-user email context and remember-device controls
- add/keep tests for:
  - pending-MFA access
  - redirect behavior for non-pending sessions
  - invalid code error rendering
  - remember-device checkbox path
  - CSRF rejection on browser POST

## `home`

### Existing live page

- route: `GET /home`
- many browser POST handlers under `/home/...`
- render helper: `render_home(...)`
- security behavior:
  - requires full authenticated context
  - redirects system admins to `/admin`
  - role-gates manager sections, team asset editors, user actions, and account-request review
  - browser CSRF required on all POST actions
  - current live page includes both save and delete flows for templates and quick actions
  - current live page includes both save and clear flows for personal LLM preference
  - current live page includes STT/LLM team selection, managed-user actions, and account-request review forms

### Restyled page status

- the live [app/templates/home.html](/home/oscar/Documents/Code_Projects/OpenScribe/app/templates/home.html) now carries the restyled home UI
- `GET /home-restyled` now renders the same live template with preview-route links preserved through route context
- it preserves:
  - role-aware sections via the same server context
  - template and quick-action forms and delete actions
  - managed-user suspend/reactivate/delete actions
  - STT/LLM team selection forms
  - account-request approve/reject forms
  - `{% include "_csrf_script.html" %}`
  - `return_view=restyled` and `return_tab` hooks already supported in [app/main.py](/home/oscar/Documents/Code_Projects/OpenScribe/app/main.py)

### Completion notes

- completed: added the missing clear personal LLM preference control back onto the live home UI
- completed: moved the restyled home UI into [app/templates/home.html](/home/oscar/Documents/Code_Projects/OpenScribe/app/templates/home.html)
- completed: kept `/home-restyled` as an alias to the same live template to avoid drift during cleanup
- expanded tests from [tests/test_admin_ui.py](/home/oscar/Documents/Code_Projects/OpenScribe/tests/test_admin_ui.py) to cover:
  - all current manager actions on the restyled route
  - clear personal LLM preference on the restyled route
  - role-gated visibility for user vs leader
  - CSRF rejection for restyled POST flows
  - redirect targets after save/delete actions

## `admin`

### Existing live page

- route: `GET /admin`
- browser POST handlers under `/admin/...`
- render helper: `render_admin(...)`
- security behavior:
  - full-auth required, then explicit system-admin-only check
  - browser CSRF required on all POST actions
  - team-scoped STT selection and provisioning
  - team-scoped LLM selection and provisioning
  - team creation
  - user create/suspend/reactivate/delete
  - account-request approve/reject
  - STT inspection and saved-config test flow
  - STT test result rendering via `stt_test_result`

### Restyled page status

- the live [app/templates/admin.html](/home/oscar/Documents/Code_Projects/OpenScribe/app/templates/admin.html) now carries the restyled admin UI
- `GET /admin-restyled` now exists as a system-admin-only alias to the same live template
- it preserves:
  - logout form
  - team selection
  - STT and LLM selection/provision/delete forms
  - team and user creation forms
  - user lifecycle forms
  - account-request review forms
  - message rendering
  - `{% include "_csrf_script.html" %}`

### Completion notes

- completed: added the missing STT test action to the provisioned-endpoint list and edit form
- completed: restored `stt_test_result` rendering on the live admin UI
- completed: added `/admin-restyled` as a system-admin-only alias to the same live template
- verified selected team/config edit state still survives validation failures and inspection flows through the existing `render_admin(...)` path
- add tests for:
  - admin-only access on preview route
  - STT test button and result block
  - STT/LLM inspect and save validation errors
  - account-request approve/reject
  - CSRF rejection on restyled admin POST routes

## `transcribe` -> GLM-2

### Existing live page

- route: `GET /transcribe`
- alternate preview route: `GET /transcribe-glm-2`
- shared render helper: `render_transcribe(...)`
- browser POST handlers under `/transcribe/...`
- security behavior:
  - requires full authenticated context
  - redirects system admins to `/admin`
  - browser CSRF required on all browser POST forms
  - owner-only session, title, upload, generation, and delete flows
  - client-side fetches hit owner-scoped `/api/v1/transcribe/workspace` and related transcript/generation routes
  - localhost-only seeded-dev redaction debug remains conditional on `show_redaction_debug`

### GLM-2 page status

- the live [app/templates/transcribe.html](/home/oscar/Documents/Code_Projects/OpenScribe/app/templates/transcribe.html) now carries the GLM-2 implementation
- `GET /transcribe-glm-2` now renders the same live template so preview and production do not drift
- the GLM-2 implementation preserves:
  - workspace endpoint hydration
  - owner session title editing
  - upload form and generation forms
  - follow-up and quick-action forms
  - `show_redaction_debug` support
  - `{% include "_csrf_script.html" %}`
  - existing full-auth route protection by reuse of the same page route wrapper

### Patch list before switch

- completed: moved the GLM-2 markup into [app/templates/transcribe.html](/home/oscar/Documents/Code_Projects/OpenScribe/app/templates/transcribe.html) so git history stays on the live template
- completed: fixed the bulk delete form action to `POST /transcribe/sessions/delete`
- completed: pointed `GET /transcribe-glm-2` at the same live template to avoid preview drift
- verify every GLM-2 browser form action matches a real server route before cutover:
  - `/logout`
  - `/transcribe/sessions`
  - `/transcribe/sessions/{id}/title`
  - `/transcribe/sessions/{id}/mode`
  - `/transcribe/sessions/delete`
  - `/transcribe/upload`
  - `/transcribe/generate-output`
  - `/transcribe/generate-followup`
  - `/transcribe/run-quick-action`
- decide whether `/transcribe-glm-2` remains as a preview alias or is retired after cutover
- remove the remaining preview aliases once the extra QA entry points are no longer needed
- add/keep tests for:
  - full-auth access and system-admin redirect behavior
  - bulk delete action
  - upload/generation forms on the live route
  - workspace endpoint hooks and pane controls
  - localhost-only redaction debug visibility
  - CSRF rejection on browser POST flows

## Recommended Switchover Order

1. `login`
2. `request-access`
3. `mfa-challenge`
4. `home`
5. `transcribe` -> GLM-2
6. `admin`

Status:

- completed for all six in-scope pages
- remaining cleanup is limited to snapshot-template removal and preview-alias retirement once no longer needed

## Minimum Done Definition Per Page

Before any page is switched live:

- old and new routes use the same auth/redirect model
- all old form actions and fields exist in the new page
- all security controls remain:
  - CSRF
  - role gating
  - confirmation prompts
  - server-rendered error/success states
- targeted UI tests exist for the new page
- preview-only routes are removed or intentionally retained with a clear purpose
