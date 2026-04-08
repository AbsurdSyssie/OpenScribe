# Main Refactor Plan

## Why this refactor is worth doing

`app/main.py` is currently doing too many jobs at once:

- FastAPI app/bootstrap and middleware
- auth and role dependencies
- HTML page rendering
- transcribe workspace assembly and SSE streaming
- API response serialization
- browser form handlers
- JSON API handlers

At more than 5k lines, that is no longer maintainable. It makes review harder, raises the cost of safe changes, and increases the chance of accidental auth or privacy regressions because unrelated concerns live in one file.

This refactor should keep behavior stable while making the code easier to reason about.

## Refactor goals

1. Keep `app/main.py` focused on:
   - app/bootstrap
   - middleware and exception wiring
   - shared auth/dependency functions
   - router registration

2. Move presentation and workspace code into explicit modules.

3. Preserve all existing contracts:
   - ownership filtering
   - transcript privacy
   - deletion semantics
   - provider resolution
   - encryption/decryption behavior
   - structured-note JSON shape

## Proposed module boundaries

### Phase 1: Safe extraction

- `app/web/templates.py`
  - shared Jinja template setup

- `app/web/presentation.py`
  - page renderers
  - HTML form defaults/helpers
  - response serializers for template/quick-action/generated-document/STT/LLM view models

- `app/web/transcribe_workspace.py`
  - transcribe workspace assembly
  - transcript detail serialization
  - transcribe workspace serialization
  - SSE payload/event helpers

This phase keeps route decorators in `app/main.py` and only extracts logic.

### Phase 2: Router extraction

- `app/routes/web_pages.py`
- `app/routes/web_home_transcribe.py`
- `app/routes/web_admin.py`
- `app/routes/api_auth.py`
- `app/routes/api_transcripts.py`
- `app/routes/api_admin.py`

This phase is now implemented for the browser routes. `app/main.py` keeps the bootstrap, shared auth/dependency helpers, and router registration, while the HTML/browser handlers live in route modules.

### Phase 3: Dependency and policy cleanup

- `app/web/deps.py`
  - route auth dependencies
  - CSRF helpers
  - role gates

- `app/web/rate_limits.py`
  - shared limiter config and key functions

## Implementation order

1. Extract presentation/workspace helpers without changing routes.
2. Run focused UI/transcribe/admin tests plus full suite.
3. Extract HTML routers.
4. Extract API routers.
5. Do one final cleanup pass on naming/import structure.

## Risks

- page rendering drift, especially around `home` and `transcribe`
- owner-only content serialization drift
- SSE workspace behavior drift
- subtle import cycles if helpers still depend on `main.py`

## Guardrails

- no schema changes
- no route path changes
- no auth dependency changes in this slice
- no provider or encryption redesign
- no deletion/lifecycle changes

## Implemented status

- `app/web/templates.py` is the shared Jinja setup
- `app/web/presentation.py` holds page renderers and view serializers
- `app/web/transcribe_workspace.py` holds workspace assembly and SSE helpers
- `app/routes/web_pages.py` holds login/request-access/onboarding routes
- `app/routes/web_home_transcribe.py` holds home and transcribe browser routes
- `app/routes/web_admin.py` holds admin browser routes
- `app/main.py` is down from the original 5k+ monolith to app/bootstrap and shared helpers only

The remaining cleanup work, if wanted later, is import hygiene inside the extracted route modules. `web_home_transcribe.py` has now been split further so that:

- `app/routes/web_home_transcribe.py` holds the home/settings browser routes
- `app/routes/web_transcribe.py` holds the transcribe browser routes
- `app/routes/web_team_management.py` holds leader user/account-request management routes

## Frontend follow-on

The next maintainability seam after the browser-route split was the transcribe template script. That follow-on is now started with:

- `transcribe_frontend_refactor_plan.md`
- `app/static/js/transcribe/bootstrap.js`
- `app/static/js/transcribe/documents.js`
- `app/static/js/transcribe/tour.js`
- `app/static/js/transcribe/app.js`

The transcribe page now reads a JSON bootstrap payload from Jinja, loads its behavior from static JS modules, and uses Jinja includes for the large shell sections instead of one giant all-in-one template.
