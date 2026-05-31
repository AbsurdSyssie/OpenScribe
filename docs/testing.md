# Testing

This document covers non-database testing. Database-specific behavior, safety rules, and persistence-level checks belong in [dbtesting.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/dbtesting.md).

Documentation convention:

- split test docs by concern
- explain the behavior or contract in plain language first
- show the test shape briefly after the behavior description

## Run the suite

```bash
source .venv/bin/activate
export $(grep -v '^#' .env | xargs)
pytest
```

The test harness now takes a session-level file lock on `/tmp/openscribe_pytest.lock`.

Why:

- the test suite resets the shared test database schema
- concurrent pytest runs against the same `TEST_DATABASE_URL` can deadlock or drop tables out from under each other

Current behavior:

- the first pytest run acquires the lock and proceeds
- a second concurrent run exits immediately with a clear message instead of colliding with the shared test DB
- the browser-style `client` fixture also auto-injects the CSRF token for non-API state-changing routes so existing UI tests behave like a rendered browser page
- admin UI regression tests verify the redesigned sidebar workspace, provider subtabs, card-style provider metadata, and de-identification management controls render without exposing transcript-derived content
- admin UI regression tests verify hallucination checker provider selection uses discovered model dropdowns instead of free-text checker model entry
- transcribe UI static regressions verify generated notes expose hallucination check status/debug panel and refresh the document navigator cache-bust token
- mail-service tests verify disabled/stdout/resend configuration validation, stdout local delivery, skipped delivery when mail is disabled, hidden Resend API key repr behavior, Resend API payload/header construction, provider-error mapping, and Vault-ref API key resolution
- auth-email tests verify generic password reset request responses when mail is enabled, non-enumerable password reset behavior during mail misconfiguration/send failures, disabled-mail reset gating in API and browser pages, current browser shell styling for reset pages, hashed setup/reset tokens, invalid confirm tokens being rejected before password hashing, password reset session/trusted-device revocation, first-time-only activation into TOTP onboarding, manager recovery same-team authorization, one-time temporary password generation, password-only recovery preserving TOTP/recovery codes, MFA-only reset preserving pending password changes, persistent copy-modal browser display, and MFA/recovery-code clearing for full recovery
- auth-service tests verify Argon2id password hashing, non-Argon2id hash rejection, and forced dev password rotation
- cookie/CSRF security tests verify production startup guards, HTTPS-only HSTS emission, anonymous pre-login CSRF, same-origin unsafe request checks, signed session-bound CSRF acceptance, and stale CSRF rejection after session rotation

## API auth route audit

Use `./.venv/bin/python scripts/audit_api_auth.py` for a route-level auth sweep across `/api/v1`.

The audit:

- verifies the manifest matches the live FastAPI route inventory
- probes protected routes with no session cookie and an invalid session cookie
- probes higher-trust routes with onboarding, pending-MFA, normal-user, and leader sessions where denial is expected
- exits with status `1` on auth mismatches and `2` when a new API route lacks an audit entry

## Browser CSRF regression

`tests/test_csrf_browser.py` is an optional Playwright-backed browser test for rendered CSRF behavior.

It starts the FastAPI app on a localhost port, logs in through the browser form, opens `/transcribe`, clicks “Create new consultation”, and verifies the browser sends `X-CSRF-Token` on `POST /api/v1/transcripts/start`.

Run it with:

```bash
source .venv/bin/activate
pip install playwright
python -m playwright install chromium
pytest -q tests/test_csrf_browser.py
```

If Playwright or browser binaries are unavailable, the test is skipped rather than failing the normal suite.

## Manual file-ingestion smoke test

For a real end-to-end file upload against a running local app, use:

```bash
OPENSCRIBE_EMAIL='user@example.com' \
OPENSCRIBE_PASSWORD='password-1' \
./scripts/test_file_ingestion.sh tests/MoreOrLess.wav
```

What it does:

- logs in through the real auth flow
- prompts for TOTP only if the login comes back as `pending_mfa`
- starts a `whole_file` transcript
- uploads the provided audio file through `/api/v1/transcripts/{id}/audio-file`
- prints the start and upload JSON responses

## Transcribe workspace regression highlights

- the workspace API reports whether a new session may be created from the owner’s latest transcript state
- the GLM 2 transcribe workspace keeps the `New Consultation` control in sync with that API state so it re-enables once the latest session has meaningful transcript content
- the GLM 2 transcribe workspace also re-enables note, quick-action, and follow-up controls after polling brings transcript draft text in, so a completed transcription no longer needs a manual page refresh before generation
- the GLM 2 note pane prioritises the latest generated note above the note-input area and allows structured note generation from EMIS context alone when the selected template is structured
- the GLM 2 structured-note output exposes both the `Copy Selected` control and its status hook so line-selection copy remains wired after live workspace refreshes
- GLM 2 structured-note copy groups selected lines by section so the section heading is emitted once per section in clipboard output, with a trailing `:`
- structured-note section headers expose individual copy buttons for copying a whole section without changing selected-line state
- generated-note copy actions expose a review gate: users can copy generated structured sections only after viewing that section bottom, and can copy generated freeform notes only after viewing the note bottom; review-required state follows the rendered generated draft and is revoked when the copyable note text changes; hidden output panes, pre-layout render geometry, and setup-time sentinels do not count as reviewed; blocked copy attempts now surface as toasts rather than inline alerts; manual pre-generation note input remains unrestricted
- generation queue tests verify users can queue multiple follow-ups for the same transcript while recording-active generation remains blocked
- transcript history shows an owner-only right-side PII table sourced from the latest successful redaction run without changing transcript ownership rules
- note switching refreshes the right-side PII table from the selected note's redaction entities without a full page reload
- transcript text highlights selected-note PII matches and persisted owner-created manual PII values in the owner workspace
- workspace refreshes re-render the right-side PII table and transcript highlights from `active_transcript_pii_entities`, including newly detected clinical NLP entities, without requiring a full page reload
- workspace PII minimisation tests verify default PII rows omit original values, owner-only reveal returns values through POST+CSRF, non-owners receive `404`, sensitive APIs are `no-store`, and plaintext response fields no longer use `_encrypted` names
- manual PII API coverage verifies owner-only add/delete, encrypted-at-rest storage, duplicate collapse, workspace hydration, and transcript-root cascade cleanup
- manual PII dedupe coverage verifies normalized value hashes are keyed owner-scoped digests rather than plain SHA-256 of low-entropy PII
- manual PII generation coverage verifies owner-entered missed PII is redacted before the LLM provider call, including transcript whitespace variants, and reidentified after output validation

## Main.py refactor guardrails

- the first `app/main.py` refactor slice keeps route decorators in place and extracts rendering/workspace helpers into `app/web/*`
- `tests/test_web_refactor.py` locks in the compatibility aliases used during that transition and verifies the extracted `render_transcribe(...)` helper still supports the legacy route call shape

## What the tests currently cover

### API contract

- public account-request submission
- duplicate account-request conflict behavior
- direct manager-created user onboarding state
- leader review scope limited to their own team
- leader suspend/reactivate scope limited to their own non-system-admin team users
- leader delete scope limited to their own non-system-admin team users
- unauthenticated suspend/reactivate/delete attempts returning `401`
- ordinary users being blocked from manager account routes with `403`
- onboarding and pending-MFA sessions being blocked from manager account routes with `403`
- onboarding-only sessions blocking normal routes
- completed-user login producing `pending_mfa` until TOTP challenge succeeds
- remembered-browser login skipping TOTP only within the freshness window
- expired remembered-browser login requiring TOTP again
- repeated bad login attempts returning `429 rate_limited`
- repeated bad TOTP challenge attempts returning `429 rate_limited`
- repeated public account-request submissions returning `429 rate_limited`
- repeated live chunk transcript uploads returning `429 rate_limited`
- live chunk uploads exceeding the rolling hourly declared-audio budget returning `429 rate_limited`
- repeated whole-file transcript uploads returning `429 rate_limited`
- whole-file uploads exceeding the rolling hourly upload-size budget returning `429 rate_limited`
- whole-file uploads exceeding the rolling hourly audio-duration budget returning `429 rate_limited`
- browser and JSON whole-file upload routes sharing the same authenticated rate-limit bucket
- live chunk upload rate limiting being isolated per authenticated user instead of globally by shared test-client IP
- live chunk hourly duration budgeting being isolated per authenticated owner
- whole-file upload rate limiting being isolated per authenticated user instead of globally by shared test-client IP
- whole-file hourly byte/duration budgeting being isolated per authenticated owner
- browser state-changing routes rejecting missing CSRF tokens
- cookie-authenticated unsafe API requests rejecting missing CSRF, mismatched CSRF, cross-origin origins, and stale session-bound CSRF after session rotation
- whole-file uploads rejecting oversize payloads before queueing
- whole-file ingestion jobs failing when normalized duration exceeds the configured maximum
- server-side security log emission on rate-limit hits
- password change + TOTP + QR-assisted setup + recovery-code completion flow
- session revocation when a user is locked
- session revocation when a user is suspended
- trusted-device revocation when a user is locked
- reactivation resetting a user into password-change onboarding
- hard-delete user removal with transcript/version cascade
- system-admin STT config provisioning, fetch, inspection, and delete for a selected team
- STT save-and-inspect duplicate warning before Vault write/provider inspection, confirmed duplicate override, invalid first-add cleanup, partial discovery status, saved-provider re-inspection via Vault reference, and invalid re-inspection clearing active selections
- explicit provider `credential_action` behavior for keeping and removing STT/LLM Vault-backed credentials, including STT credential-derived metadata cleanup, LLM fail-closed Vault delete ordering, and stale LLM Vault refs that remain clearable when the old token cannot be read
- manual generic STT save-and-inspect testing the saved runtime contract with bundled synthetic audio instead of default OpenAPI discovery
- generic REST and OpenAI-compatible REST STT bad replacement token rejection preserving the existing config row, team selection, and saved credential without writing the rejected token to Vault
- STT OpenAPI inspection inferring provider-specific `model_field_name`/`language_field_name` and runtime sending those saved field names instead of hard-coded `model`/`language`
- STT OpenAPI inspection validating/dereferencing provider documents through the shared inspection libraries
- STT response extraction accepting `jsonpath-ng` expressions as well as legacy dot paths
- STT runtime paragraphization using configured segment path/text/start/end/speaker mappings, including queued ingestion snapshots
- saved STT provider tests covering both OpenAI Cloud and generic REST dynamic-field branches
- LLM inspection exposing machine-readable discovery status, default model source, warning, and manual-required states
- LLM provider presets covering branded provider catalog/inference, live-discovery-only manual fallback, manual model selectability after failed discovery, service-owned inspection metadata, stale model clearing/rediscovery on provider endpoint changes, OpenAI-only prefix filtering, base URL override reclassification to custom OpenAI-compatible, saved inspection metadata, and migration backfill
- saved LLM provider re-inspection using the Vault-backed API key to refresh provider model metadata without key exposure
- hallucination-check selection API coverage for system-admin-only set/read/clear using ready active team LLM configs
- structured hallucination-check generation coverage for redacted-only checker prompt shape, exact-substring patch application, checked bucket, applied edit count, encrypted dev debug payload, and provider usage metadata
- structured hallucination-check provider/Vault-failure coverage verifies notes still save ready/unchecked and owner-only debug includes safe failure metadata
- dictation-only follow-up generation coverage verifies saved post-consultation dictation counts as a valid source when transcript text is empty
- OpenAI-compatible LLM generation coverage verifies content-part dictionary responses are extracted as note text instead of being treated as empty provider output
- gpt-oss hallucination-check request coverage verifies the checker uses low reasoning effort and a larger completion cap so reasoning tokens do not consume the final JSON answer
- leader team STT selection and clear flow using admin-provisioned options
- STT browser model selection using provider-populated dropdowns instead of free-text overrides
- STT provisioning/selection route blocking for unauthenticated, ordinary-user, onboarding, and pending-MFA callers
- STT config validation for remote HTTPS-only and leader team-selection scope
- transcript owner-only access and version history
- transcript start creating the root for the current user and persisting `ingestion_mode`
- transcript create/start/update retention coverage verifies public payloads cannot extend retention and new roots use the owning team's default retention snapshot
- transcript start provisioning an owner DEK and storing the initial draft encrypted at rest
- transcript structured context persisting encrypted at rest while owner-facing responses still expose plaintext JSON
- transcript list responses including the persisted `ingestion_mode`
- transcript list responses use self-scoped keyset pagination, stay owner-only, and return metadata without transcript or note text
- owner-only transcript detail fetch for browser polling
- transcript detail and workspace reads still returning plaintext draft/context data to the owner while the DB stores ciphertext
- generated-document queue/process flows storing note bodies, follow-up prompts, structured section text, and structured context encrypted at rest while the owner-facing APIs still return plaintext
- redaction run creation storing redacted transcript text and placeholder original values encrypted at rest
- owner-only live audio chunk queueing
- live audio chunk upload rejecting non-`live_chunked` transcripts
- duplicate live chunk sequence rejection
- sequence-aware live chunk worker application
- sequence-aware live chunk reconciliation advancing past failed live-chunk gaps once later completed chunks are available
- stale live-chunk queued/processing jobs being marked failed during reconciliation so later completed chunks can apply
- late Celery delivery not reviving ingestion jobs already marked failed
- live chunk worker failure when no active team STT selection exists
- live chunk worker encrypting provider result text at rest before owner-visible reconciliation
- ffprobe/ffmpeg timeouts surfacing as clean audio inspection/normalization errors instead of hanging workers
- owner-only whole-file ingestion queueing
- whole-file ingestion retaining retryable source audio outside Postgres, with the ingestion job carrying either a legacy blob or a Vault-backed source-audio ref
- whole-file ingestion rejecting transcripts in the wrong ingestion mode
- whole-file queueing failing early when no active team STT selection exists
- whole-file ingestion moving the transcript to `ready` after successful provider completion
- whole-file ingestion appending plaintext transcript content into an encrypted-at-rest transcript draft
- STT provider execution using the team config, Vault secret, and configured response text path
- queued STT jobs snapshotting the resolved provider/model so later team STT selection changes do not retarget already-uploaded audio
- queued transcript-ingestion Celery tasks carrying only `job_id`, with worker audio loaded from Vault-backed `source_audio_vault_ref`; legacy `audio_b64` task messages are accepted during rollout and immediately moved into Vault-backed source storage
- handled STT worker failures now marking the job/transcript failed without re-raising expected AppError paths into noisy Celery tracebacks
- selected STT configs with missing saved credentials failing immediately with `stt_config_secret_missing` instead of queueing a doomed job
- already-running STT jobs surfacing the same `stt_config_secret_missing` message if the saved credential disappears before execution
- self-hosted `generic_rest` and `openai_compatible_rest` STT configs working without a bearer token when the endpoint does not require auth
- admin browser defaults keeping optional-token STT and Ollama credential actions away from blank replacement submissions
- generic REST STT transport failures surfacing distinct connect/timeout/upstream-status error codes instead of flattening them all to `stt_unavailable`
- timestamped-segment paragraph grouping heuristics for Parakeet-style STT responses
- whole-file STT responses preferring paragraphized `segments` over flat `text` when timestamped segment data is present
- transcript detail and the `/transcribe` workspace surfacing the latest owner-visible ingestion failure message instead of only a generic failed-state banner
- transcript detail and the `/transcribe` workspace surfacing `latest_ingestion_retry_available` so the owner UI can show a retry control only when stored retry audio still exists
- STT config edits/deletes being blocked while queued or processing ingestion jobs still reference that config
- leader team-template create/update/delete scope
- user personal-template create/update/delete scope
- owner-only generated-document listing per transcript
- owner-only template-based note generation creating a transcript-version snapshot and generated-document row
- owner-only template generation using either OpenAI chat or Ollama chat provider adapters
- Ollama chat generation streaming partial `/api/chat` chunks and collecting final usage metrics from the terminal `done: true` chunk
- owner-only template generation now queues a generated-document job instead of blocking inline
- template note generation now requires valid JSON `title`/`content` output and fails cleanly on invalid JSON
- template note JSON parsing tolerating markdown fences and surrounding prose without weakening the required `title`/`content` object contract
- structured EMIS template generation validating selected section keys, rendering full note text, and persisting `generated_document_sections`
- transcript-backed EMIS working context persisting between structured generations and hydrating the `/transcribe` EMIS fields on reload
- the first successful note title auto-filling the transcript session title only when the session is still blank or `Untitled session`
- owner-only follow-up generation now queues a generated-document job using the same async worker path, including Working-note-only sessions with redacted Working-note context
- generated-document prompt snapshots surviving later template or quick-action deletion
- transcript commit and completed whole-file ingestion proactively creating owner-scoped redaction runs for review before generation
- live-capture finalize applying completed chunks, deferring preview redaction while chunks are still pending, and creating/reusing owner-scoped redaction once the transcript is ready
- workspace redaction preview status distinguishing not-run, succeeded, and failed checks so an empty PII table is not ambiguous
- workspace PII coverage verifies detected rows are hidden when the latest redaction run failed rather than falling back to stale older successful runs
- clinical NLP coverage verifies admin provider flags, team assignment plus leader enablement, remote providers receiving redacted transcript text, local/private providers receiving unredacted transcript text only when allowed, encrypted owner-scoped clinical entity rows, stale zero-result runs rerunning after provider config changes, transcript deletion cascade cleanup, and the `/transcribe` PII panel rendering disease/symptom rows and clinical NLP status with a separate highlight class
- generated-document worker lazily creating or reusing a `redaction_runs` snapshot for the queued transcript version, including reuse of an existing matching transcript version
- generated-document worker waiting up to 120 seconds for pre-click transcription jobs to finish before refreshing the transcript snapshot and building the LLM request, with no second user click needed
- generated-document worker preserving click-time transcript snapshots for queued docs that were not waiting on pre-click transcription, even if live draft changes before worker start
- generation routes blocking active recording while allowing multiple queued follow-ups for the same transcript
- generated-document worker sending only redacted transcript text to the LLM and re-identifying the finished output before persistence
- generated-document worker failing closed when the LLM returns malformed or unknown PHI placeholders
- system-admin de-identification provider provisioning and team assignment
- leader de-identification provider selection using assigned providers plus built-in fallback
- redaction runtime resolving team-selected de-identification provider and falling back to built-in native provider when no explicit selection exists
- LLM config edits/deletes being blocked while queued or processing generated documents still reference that config
- server-side STT/LLM model validation rejecting API-submitted model names outside the provider-discovered list
- leader team-quick-action create/update/delete scope
- user personal-quick-action create/update/delete scope
- one-off import of team-scoped templates and quick actions into admin-managed default assets, including editor-equivalent name normalization, idempotent skip-existing behavior, and freeform quick-action normalization
- owner-only quick action generation now queues a generated-document job using the same async worker path and persists quick action provenance
- generated-document worker processing updates queued documents to `ready`, persists `provider_usage_events`, and logs metadata-only usage counts
- provider failure tests now verify sanitized provider HTTP/error metadata is persisted without logging prompts or output text
- generated-document generation route rate limiting per authenticated user
- transcript delete cascade removing generated documents

### Admin and browser UI

- bootstrap flow when the database is empty
- public `/request-access` form
- bootstrap redirect to onboarding
- onboarding QR code rendering for TOTP setup
- leader home page with request-review and direct-user-create tools
- leader home page suspend/reactivate controls for manageable users
- leader home page delete control for manageable users
- leader home page STT selection form
- leader home page STT selection clear flow
- leader home page LLM selection using provider-backed allowed-model controls
- user home page LLM preference using a populated dropdown instead of free-text
- owner transcription workspace at `/transcribe`
- owner transcription workspace file-upload form
- owner transcription workspace missing-STT error that names the team leader email when available
- owner transcription workspace showing a `Retry transcription` control only when a failed whole-file job still has stored retry audio available
- owner transcription workspace hiding the retry control when a failed whole-file job has no stored retry audio available
- owner transcription workspace sidebar session list and redesigned tabbed transcript shell
- owner transcription workspace exposing and hydrating from `GET /api/v1/transcribe/workspace`
- owner transcription workspace SSE updates from `GET /api/v1/transcribe/workspace/stream`
- `/transcribe` header-only audio controls still exposing the upload form hook, the large editable session title control, and the split `New consultation` mode chooser
- owner transcription workspace exposing API-driven session-title, upload, and generation form hooks
- owner transcription workspace exposing API-driven new-session and selected-session delete hooks
- owner transcription workspace marking non-empty transcript sessions for client-side delete confirmation without rendering transcript text in the session rail
- owner transcription workspace rendering detected and owner-created manual PII in a bounded right-side table next to the transcript content
- owner transcription workspace rendering post-consultation dictation as a modal recording/editing flow plus compact separate-source history block
- owner transcription workspace dictation modal making the primary recording button stop active capture, moving pause/resume into a small icon button, automatically preview-transcribing recorded dictation on stop, and keeping failed preview audio retryable locally
- post-consultation dictation preview STT returning text without persisting dictation rows/segments, while preserving owner-only auth, size/duration limits, and dictation STT selection requirements
- dictation-only note generation allowing an empty transcript snapshot when saved dictation exists and redacting dictation before the LLM provider call
- dictation-only follow-up UI coverage verifies saved dictation enables follow-up prompt and Generate controls without transcript text
- working-note-only sessions counting as non-empty for new-session lifecycle checks, follow-up/quick-action generation sending the saved Working note only in redacted form after saving dirty Working-note edits, transcript list responses reporting saved Working notes, and legacy transcript structured-context PATCH rejecting invalid/empty payloads without clearing saved Working notes
- note/follow-up/quick-action generation drains queued Working-note saves before enqueueing, so the worker snapshots the latest saved Working note instead of a stale in-flight version
- transcript create/start routes reject invalid or empty legacy `structured_context_json` Working-note payloads instead of silently dropping submitted note content
- owner transcription workspace exposing both `whole_file` and `live_chunked` new-session entry points
- owner transcription workspace exposing client-side session-rail links for workspace refresh without full-page navigation
- owner transcription workspace exposes an infinite-scroll sentinel for metadata-only consultation history pages and preserves loaded older rows across workspace refreshes
- owner transcription workspace keeping blocked new-session feedback out of the sidebar and blocking session switches with toasts while recording is active
- owner transcription workspace preserving structured EMIS output hooks during workspace refresh and poll-driven rerender
- owner transcription workspace keeping the redesigned clinical shell copy and core controls while preserving current browser hooks
- GLM 2 transcribe route exposing the same owner-only workspace endpoint and pane controls for hide, split, and expand states
- `live_chunked` sessions rendering live-specific controls, pinned `vad-web` runtime hooks, and Silero/VAD status copy in the transcribe workspace
- `/transcribe` rendering user-facing copy for messages/notes plus the transcribe guide overlay and generated-document switchers for regenerated notes and follow-up outputs
- `live_chunked` sessions surfacing the latest live-chunk STT failure message instead of immediately reverting to generic ready copy
- GLM 2 transcribe route rendering the full EMIS section editor surface and the output/follow-up/history assistant pane against real workspace data
- GLM 2 transcribe route keeping the restored GLM shell while wiring real session switching, title editing, and provider labels through the existing runtime
- owner transcription workspace post/redirect/get upload flow so refresh does not resubmit the form
- owner transcription workspace session header showing the resolved user LLM model instead of the raw team default when a user preference is active
- leader home page team-template management form
- user home page personal-template management form
- leader home page team-quick-action management form
- user home page personal-quick-action management form
- owner transcription workspace output-tab note generation flow
- owner transcription workspace follow-ups tab queueing a follow-up request into the same async generated-document pipeline
- owner transcription workspace follow-ups tab quick-action dropdown queueing a quick action into the same async generated-document pipeline
- owner transcription workspace rendering a selected-note delete control wired through a permanent-delete browser confirmation
- system-admin usage tab rendering provider usage telemetry plus transcript ingestion bytes/duration by team and selected-team user scope
- system-admin usage tab rendering KPI cards, split input/output token metrics and charts, provider/model mix, generated-document mix, ingestion mix, failure hotspots, and selected-team user activity share without exposing content
- `/home` rendering the lighter user/leader guide overlay and the renamed `Saved prompts` navigation copy
- localhost-only seeded dev-account access to generated-document redaction debug for manual verification that the outbound LLM path used the redacted transcript payload
- localhost-only seeded dev-account redaction debug exposing the raw redacted failed provider output for malformed note JSON diagnosis
- home and transcribe UI showing structured EMIS template authoring, transcript-backed EMIS context reload, and line-array context inputs
- template editor hiding EMIS section prompts until `structured` mode is selected and ignoring posted section fields for freeform template saves
- `/transcribe` hiding the EMIS context editor when the selected note template is freeform
- structured EMIS generation filtering transcript-persisted sections that are removed by the selected template
- template API responses preserving `latest_version.config_json` for structured template round-tripping
- OpenAI Cloud STT inspection loading a server-side filtered model list into the browser form
- Ollama LLM inspection and save flow without a required API key for local hosts
- STT inspect pages rendering inferred values into the save form, not just the API inspection result
- admin page provisioned-endpoint add/edit/delete flow
- admin page active STT selection flow for the selected team
- admin page saved STT `Test STT` action using the bundled `tests/MoreOrLess.wav` sample and rendering the outcome block
- GLM 2 workspace showing `idle` instead of backend `recording` for untouched whole-file sessions while leaving whole-file controls available when a team STT selection exists
- browser manager-account routes redirecting unauthenticated requests to `/login`
- admin page showing teams, users, and account requests
- admin page flat sidebar workspace layout for admin areas
- home page flat sidebar workspace layout for settings/admin-like areas while keeping overview as dashboard cards
- admin page protected-account marker for the current system-admin account
- admin page team-scoped STT config form
- admin page team-scoped STT inspection flow
- admin page LLM team policy rendering visible-model tiles with default model dropdown limited to visible models
- admin/admin2 LLM provider forms syncing branded default base URLs, rendering Bedrock regions from backend preset metadata, hiding the editable Bedrock base URL in favor of a derived endpoint label, and correcting stale localhost base URL submission server-side
- admin page default template management and default quick-action management
- admin team creation seeding active default templates and quick actions into team-owned assets
- admin team hard delete removing team users, team-owned configs, team assets, transcript-derived rows, account requests, and team usage metadata
- admin team hard delete preflighting system-admin membership before deleting Vault-backed provider secrets
- admin team hard delete deferring Vault-backed provider secret deletion until after DB cleanup commits
- system-admin user hard delete reassigning admin-managed metadata FK references before removing the user
- de-identification provider validation rejecting secret-bearing extra headers/body fields, including nested body JSON keys, and bearer-auth providers without a Vault-backed token
- shared NLP endpoint inspection ping using synthetic sample text, bearer auth, response path parsing, entity mapping, clinical-NLP `label`/`confidence` response adjustment, and no token echo
- admin de-identification inspection UI using entered bearer tokens for the ping only and requiring re-entry for save instead of rendering tokens into hidden fields
- de-identification provider OpenAPI/docs inspection inferring REST detect path, request fields, response entity fields including top-level array responses, typed extra body defaults, automatic synthetic ping, and raw response display
- de-identification provider inspection separating docs discovery path from selected runtime endpoint and allowing candidate endpoint re-ping with the same OpenAPI document
- de-identification provider synthetic ping pruning provider-rejected extra body fields and ignoring language values accidentally entered as field names
- generic REST de-identification parsing supports provider entities with explicit start/end offsets or value-only text plus label, deriving spans from the source text when needed
- admin providers UI selecting an assigned de-identification provider for the team so runtime redaction uses the saved endpoint rather than an older selection
- de-identification provider Vault lifecycle keeping old secrets until DB commits and cleaning pending replacement secrets on failed commits
- built-in de-identification provider fallback helper preserving caller transaction boundaries
- generic REST de-identification span normalization/filtering before placeholder replacement
- de-identification runtime fallback to the built-in global provider when a selected team provider is inactive or unavailable
- admin providers UI exposing de-identification provider provisioning, edit/delete, and per-team assignment without revealing Vault-backed bearer tokens
- leader home AI-services UI exposing team de-identification selection and clear-to-built-in-fallback behavior
- leader home AI-services UI exposing clinical NLP enable/disable separately from PII redaction selection
- home tabs initializing after the navigation moved above the tab shell
- transcribe structured and freeform statement editors autosizing correctly on first render, even when their panels were hidden during mount
- transcribe editable-note empty guidance hiding on initial render and generated-output refresh once structured/freeform note rows contain content
- transcribe structured/freeform line reordering blocking blank placeholder rows by mouse drag and consuming blocked keyboard shortcuts so browser history navigation does not fire
- transcribe working-note debt coverage checking shared target IDs, virtual working-note document shaping, serializer ownership in `structured.js`, and generation blocking on failed dirty working-note saves
- working-note API concurrency coverage for clear version tokens, stale save-after-clear rejection, and unsupported EMIS section-key rejection
- working-note migration coverage for encrypted legacy `structured_context_json` backfill into structured mode locks
- transcribe history tab keeping the transcript pane independently scrollable inside the split workspace
- transcribe session delete confirmation marking only meaningful transcript draft/version text as content
- MFA challenge page and remember-browser option for completed users
- login form rate-limiting returning `429`
- login form rate-limiting returning a generic wait-and-retry page
- seeded dev-account login is allowed from localhost but rejected for non-local browser requests
- seeded localhost dev accounts can inspect a dev-only redaction debug panel in `/transcribe` for the latest note/follow-up without exposing original PHI values
- seeded dev-account API sessions are revoked if reused from a non-local request
- invalid browser route navigation redirecting unauthenticated users to `/login` and authenticated users to `/home`
- invalid `/api/*` routes still returning JSON `404` instead of redirecting

### Auth unit tests

- password verification success
- password verification failure
- malformed stored hash rejection
- session/recovery-code hashing behavior

### Migrations

- `alembic upgrade head` builds the expected schema from scratch
- head schema includes account-request, session, trusted-device, MFA, and recovery-code tables
- migration behavior and database safety rules are documented in [dbtesting.md](/home/oscar/Documents/Code_Projects/OpenScribe/docs/dbtesting.md)

## Current notes

- Postgres-backed tests need real socket access to the local test database. In this environment that means running them outside the restricted sandbox.
- The STT and LLM browser forms use `provider_model` for HTML form posts, while the JSON API and persisted field remain `model_name` / `model_name_override`. That keeps the API stable while avoiding FastAPI/Pydantic protected-namespace warnings from generated form models.
