# Discovered issues

## XDIST-RANDOM-PARAM-ID — 2026-10-14

- Discovery commit: `c712`.
- Location: `tests/test_consultation_split_runtime.py`, parameterization for `test_runtime_rejects_invalid_or_unavailable_provider_output_once`.
- Observed problem: a synthetic unknown-template UUID was embedded in the parameter value, making pytest derive different collection IDs in xdist workers.
- Resolution: assigned stable explicit IDs to all three cases while retaining the original generated synthetic values and assertions.
- Verification: rerun the full xdist suite after the metadata-only fix.

## ONE-WORKER-PROVIDER-STALL — 2026-10-14

- Discovery commit: deployed `78ac327`.
- Location: `docker/entrypoint.sh`, runtime Celery worker command; `app/services/llm_adapters/gemini_enterprise.py`, `build_gemini_client`.
- Exact excerpts: `-Q control,generation,ingestion` and `--concurrency "${CELERY_WORKER_CONCURRENCY:-1}"`; `types.HttpOptions(api_version=api_version, headers=headers or None)`.
- Observed problem: metadata-only production review showed one Gemini 3.5 consultation-split generation submitted at 14:06:07 UTC and terminalized at 14:16:05 with HTTP 503 / `provider_server_error` (about 598 seconds). A separate ordinary note had been queued and published at 14:07:43, but its worker had not received it until 14:16:05; it started at 14:16:08 and failed at 14:16:34 with HTTP 429. Afterward the worker was idle, queues were empty, and there were no active attempts, unfinished documents, or database blockers. The team had no checker selection and there were no checker or consultation-split verification attempts in the inspected 24-hour metadata window; the checker was not the observed cause.
- Likely impact: a slow synchronous provider call occupies the single worker slot and delays other queued generation. The Gemini client supplies no explicit HTTP timeout; the installed SDK's request timeout is unset by this configuration. Provider capacity/rate limits can also turn delayed requests into failed generations. The observation establishes queue starvation during a slow provider request, not a persistent provider outage or a database lock.
- Not fixed during production diagnosis because changing worker topology, request deadlines, and provider-capacity/429 policy together needs a scoped plan that preserves submitted-attempt idempotency, quota settlement, and durable outbox semantics. No database state, queue, worker, or deployment configuration was changed.
- Follow-up: isolate the control worker as recommended by [docker.md](docs/docker.md), give generation work sufficient isolated capacity, and define explicit provider request timeouts that fit the durable attempt deadlines. Review rate-limit/capacity handling separately; do not reset or replay submitted attempts to recover this incident. Validate queue progress under a synthetic slow-provider scenario, timeout-to-terminal-settlement behavior, duplicate delivery, and the provider 429 failure path.

## GEMINI-400-ERROR-CLASSIFICATION — 2026-10-14

- Discovery commits: working tree `dcdc3b7`; deployed `78ac327`.
- Location: `app/services/llm_adapters/gemini_enterprise.py`, `translate_gemini_error`.
- Exact excerpt: `if status_code == 400 and operation in {"model_validation", "generation"}: return AppError(422, "llm_model_unavailable", ...)`.
- Observed problem: every Gemini generation HTTP 400 is classified as `llm_model_unavailable`, including a valid model request rejected because its thinking level is unsupported.
- Likely impact: operators and clinicians can receive misleading model-availability guidance for malformed or model-option-specific requests, slowing diagnosis.
- Not fixed during the Gemini 3.8 Flash thinking-level repair because changing the error taxonomy requires a bounded, content-safe interpretation of Google's structured error reasons across validation and generation. Broadening this patch could alter existing provider failure contracts.
- Follow-up: inventory synthetic Gemini 400 response shapes, identify stable structured reason fields without persisting provider messages, and add focused translation tests before refining the classifier.

## GEMINI-3-THINKING-FALLBACK — 2026-10-14

- Discovery commit: `c712ba5`.
- Location: `app/services/llm_adapters/gemini_enterprise.py`, `_gemini_thinking_config`.
- Exact excerpt:
  ```python
  if model_id.startswith("gemini-3"):
      return types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL)
  ```
- Observed problem: Google's [Gemini thinking-level matrix](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/thinking), last updated 2026-10-13, lists only `LOW`, `MEDIUM`, and `HIGH` for Gemini 3.7 Flash and Gemini 3.8 Flash Cyber. The generic Gemini 3 fallback sends `MINIMAL` to both. The base Gemini 3.8 Flash model already has a specific `LOW` branch. This is a documentation/code comparison; no live provider probe was made during this audit.
- Likely impact: generation requests using either affected model may receive HTTP 400 and OpenScribe may report the model as unavailable because the current generation error mapper classifies 400 as `llm_model_unavailable`.
- Not fixed during the check-only audit because the requested scope was assessment, not adapter changes.
- Follow-up: add model-specific `LOW` handling for Gemini 3.7 Flash and 3.8 Flash Cyber with adapter request regressions. Preserve supported `MINIMAL` for Gemini 3.5 Flash-Lite and the existing Gemini 2.5 `thinking_budget` behavior, and verify structured-output and quota behavior remain intact.
- Resolved 2026-10-14: `_gemini_thinking_config` now assigns `LOW` only to the exact normalized Gemini 3.7 Flash and Gemini 3.8 Flash Cyber IDs alongside the existing Gemini 3.8 Flash rule. Request-seam regressions cover bare and publisher paths, structured JSON configuration, prior Gemini 3 thinking levels, Gemini 2.5 budgets, and unknown-model fallback behavior.

## GEMINI-25-FLASH-RETIREMENT-AND-LONDON-CAPACITY — 2026-10-13

- Discovery commit: deployed `78ac327`.
- Location: `app/models.py`, `TeamLlmConfig.model_name`; Google Cloud's [Gemini 2.5 Flash lifecycle and availability page](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/2-5-flash).
- Observed problem: deployed metadata shows the active Gemini configuration defaults to `gemini-2.5-flash` in `europe-west2` (London), with no purchased Provisioned Throughput and PayGo-only capacity. The current Google model page lists an October 20, 2026 retirement date for Gemini 2.5 Flash. Recent attempts frequently override the default with `gemini-3.5-flash`; both synthetic London probes for 2.5 Flash and 3.5 Flash succeeded. Therefore, the retirement is approaching, but these observations do not establish a persistent London 3.5 Flash capacity outage or that the default is responsible for the reported 429.
- Likely impact: configurations that continue to select the retiring default may lose supported generation after retirement; London capacity behavior for PayGo remains subject to Google's model/location support and current shared capacity.
- Not changed during the capacity investigation because provider/model selection must account for approved London residency, capacity availability, and clinical review; changing the model or location would exceed a diagnostic metadata-only action.
- Follow-up: plan and validate migration to a model explicitly approved for the deployment's residency and capacity requirements before the published retirement date. Reconfirm Google's current lifecycle and London availability at that time; do not infer a persistent outage from isolated 429s or successful synthetic probes.

## OPENSCRIBE-CONTAINER-UNHEALTHY — 2026-10-12

- Discovery commit: `2d7bfcb`.
- Location: deployed `openscribe-openscribe-1` container healthcheck.
- Exact observation: `openscribe-openscribe-1` was `Up 2h (unhealthy)` while PostgreSQL, Redis, and Vault were healthy.
- Observed problem: the deployed application container is reporting an unhealthy healthcheck state.
- Likely impact: orchestration or operational monitoring may treat the application as unavailable even while supporting services remain healthy.
- Not fixed during the consultation-split provider-binding repair because this is unrelated to the deterministic pre-submit request mismatch. No healthcheck logs, environment, or container state beyond the reported status were inspected.
- Follow-up: inspect the healthcheck command, recent healthcheck output, and relevant safe operational logs; then reproduce and repair the specific healthcheck failure without exposing sensitive configuration or content.

## API-AUDIT-SHARED-INFRASTRUCTURE-LOCK — 2026-10-05

- Discovery commit: unavailable (working tree investigation).
- Location: `scripts/audit_api_auth.py`, `main` and `reset_public_schema`.
- Exact excerpt: `reset_public_schema()` followed by `rate_limit_redis.flushdb()`.
- Observed problem: the standalone API audit resets the canonical test database and limiter store without acquiring the `/tmp/openscribe_pytest.lock` used by pytest.
- Likely impact: running the audit beside an active pytest process can reset that process's shared test infrastructure. Static tracing established the gap; no concurrent destructive run was attempted.
- Not fixed during the Multiple problems toggle because changing audit infrastructure is separate work. Audit verification remains pending while the shared lock is occupied.
- Follow-up: share the existing test-run lock with the audit before any infrastructure initialization or reset, then add an isolated contention regression.
- Resolved 2026-10-07: pytest and the API audit share the nonblocking lock helpers in `tests/db_utils.py`. Both acquire the lock before database creation; the audit holds it through final Redis cleanup and exits with code `2` when busy. An isolated subprocess regression verifies that contention causes no database initialization, schema reset, or Redis flush.

## SPLIT-GENERATION-STATUS-HIDDEN — 2026-10-05

- Discovery commit: unavailable (working tree investigation).
- Location: `app/static/js/transcribe/app.js`, `createSplitGenerateController` setup.
- Exact excerpt: `setStatus: () => {},`
- Observed problem: terminal split-generation status messages, including a rejected or failed one-note continuation, are discarded by the workspace integration.
- Likely impact: a clinician may not receive the controller's safe actionable failure message after the dictation modal closes.
- Not fixed during the queued-intent restoration repair because presenting and deduplicating split lifecycle feedback is a separate workspace feedback contract.
- Follow-up: define a visible, accessible, non-noisy status surface for terminal split-generation failures and add browser coverage for rejected continuation feedback.
- Resolved 2026-10-07: the workspace now forwards split-generation `warning` and `error` statuses to the existing accessible flash banner via `showFlash`; controller coverage continues to assert safe terminal guidance and rejected continuation feedback.

## SPLIT-PREFERENCE-BROWSER-CHECKBOX-CLICK — 2026-10-01

- Discovery commit: `32eaf7caf2f6a26929195ee1d06e8f9b2dab97d7`.
- Location: `tests/test_consultation_split_browser.py:252`, `test_browser_team_leader_split_preference_persists_across_workspace_navigation`; `app/static/css/components.css:208-209`, compact switch styling.
- Exact excerpt: `split_toggle.check()` targets the 1px checkbox while `.switch-compact__track` occupies the visible switch area.
- Observed problem: Playwright times out because the track intercepts pointer events. The test failed the same way on its own rerun; the other four browser tests passed.
- Likely impact: this browser regression cannot verify preference persistence; the styled label may still respond to ordinary user clicks.
- Not fixed during the note-split confirmation change because the preference control and its test are outside the affected flow.
- Follow-up: test the accessible switch through its visible label or adjust the control's hit target, then rerun this browser regression.
- Resolved 2026-10-07: inspection at `fd599225` confirms the browser regression now clicks the visible preference label instead of calling `.check()` on the hidden checkbox. Browser execution is recorded separately from this code verification.

## Stale Scribe stylesheet cache key assertion — 2026-09-24 (resolved 2026-09-29)

- Discovery commit: `118ed37`.
- Location: `tests/test_workspace_scribe_layout_contract.py`, `test_scribe_mobile_dictation_modal_owns_the_top_layer_and_safe_viewport`.
- Assertion: `assert 'transcribe.css?v=20260911-note-regeneration-layer-fix' in read("app/templates/transcribe/_head_assets.html")`.
- Observed problem: the template at HEAD uses `transcribe.css?v=20260920-session-sidebar-timestamp-skeleton`, so this unrelated contract test fails despite no change to that stylesheet link in the recording-lock work.
- Impact: the broader workspace layout contract run reports a failure unrelated to the behavior under change.
- Not fixed during the recording navigation lock change because stylesheet cache-key maintenance was outside its scope.
- Resolved by updating the assertion to match the maintained asset key in `tests/test_workspace_scribe_layout_contract.py`.

## SPLIT-SOURCE-ONLY-MATERIALIZATION — 2026-09-26 (resolved 2026-09-27)

- Discovery commit: `118ed375caefb73e42617412b00a8e001622602c` (inspected current working tree).
- Location: `app/services/consultation_split_partial.py:167-169`, `_materialize_available_split_notes_locked`.
- Exact excerpt: `transcript_version_id=batch.analysis.transcript_version_id`.
- Observed problem: Working-note-only and dictation-only analyses intentionally have a null transcript version. Confirmation binds an encrypted empty version to the batch instead. Ordinary split generation uses that materialization version, but Keep Available and verification finalization use the analysis version through this shared materializer.
- Likely impact: inserting a source-only child document violates the non-null `GeneratedDocument.transcript_version_id` constraint and rolls back materialization. Established by static tracing; not reproduced in a test during this inspection.
- Resolved by `consultation_split_materialization.require_batch_materialization_version`, used before all split document materialization. Verification now terminalizes a missing or wrong-root binding with existing failed/unchecked states after settling or cancelling its attempt, instead of rolling back and retrying finalization. Working-note-only and dictation-only Keep Available and verification-finalization regressions prove the confirmation-bound version is used.

## SPLIT-INTENT-PREFERENCE-OPTOUT-VISIBILITY — 2026-10-06

- Discovery commit: unavailable (working tree review).
- Location: `app/services/consultation_split_gates.py`, `analysis_split_enabled` and `intent_split_enabled`; `app/services/consultation_split_api.py`, `read_workspace_split_analysis`/`read_workspace_split_intent`; `app/services/consultation_split_drafts.py`, `read_split_draft`; `app/services/consultation_split_confirmation.py`, `confirm_split_draft`.
- Observed problem: an already accepted automatic-preference intent continues server-side after the owner opts out, but current analysis/draft projection and explicit confirmation still use the existing preference gates. With the automatic preference off, `analysis_split_enabled` permits only an accepted manual-review intent; otherwise workspace analysis is omitted, an active fresh draft can be projected stale, and `intent_split_enabled` rejects confirmation.
- Likely impact: a ready draft created by durable server progress can be hidden/unreviewable after opt-out while the deployment gate remains available. This is an existing gate boundary, separate from Create navigation durability.
- Not changed during durable Create continuation because this task preserves current preference/deployment authorization for workspace and clinician review actions; broadening accepted-work authorization needs an explicit product/security decision.
- Follow-up: decide whether an exact accepted intent should retain workspace visibility and clinician confirmation authority after preference opt-out, then update the gate helpers and focused authorization/workspace tests together.
- Resolved 2026-10-07 under the supplied plan: preference opt-out blocks new unmarked automatic intents but preserves the exact accepted intent through analysis, draft review, confirmation, and batch access. Deployment availability, owner/team scope, source freshness, and retention still apply. A real-runtime regression reproduces the old rejection and verifies the accepted-work flow after opt-out; focused progress, runtime, and Multiple problems tests pass.

## TRANSCRIPT-COMBINED-PATCH-DROPS-EARLY-FIELDS — 2026-10-07

- Discovery commit: `88e487718e627643d9c3f8a99609f137bacd0a54`.
- Location: `app/services/transcripts.py`, `update_transcript`.
- Exact excerpt: title and ingestion-mode assignments occur before `_lock_split_source_writer_transcript(...)`, whose query reloads the same row with `populate_existing=True` while autoflush is disabled.
- Observed problem: when one transcript PATCH combines `structured_context_json` with a title or ingestion-mode change, the source-writer reload replaces those earlier in-memory assignments with database values before commit.
- Likely impact: the request can succeed while silently dropping its title or ingestion-mode update. The Multiple problems marker had the same reload ordering risk, but this change now assigns that new marker after the reload and covers the combined marker/structured-context case.
- Not fixed during the Multiple problems toggle because title and ingestion-mode update ordering is older, unrelated behavior and broadening the patch would expand scope beyond the new marker contract.
- Follow-up: preserve validated title and ingestion-mode changes across the source-writer reload, then add focused combined-PATCH regressions for each field.
- Resolved 2026-10-07: `update_transcript` acquires the source-writer lock before applying metadata for structured-context requests. Independent title and ingestion-mode PATCH regressions reproduce the dropped fields on the old ordering and pass with the fix; metadata-only requests retain the lighter owner lookup.

## SPLIT-VERIFIER-PREFERENCE-RECOVERY-EXPECTATION — 2026-10-08

- Discovery commit: `23fa441591064d7d2b4d47c8acfd51d86992a4cf`.
- Location: `tests/test_consultation_split_generation_runtime.py`, preference cases in `test_submitted_verifier_finishes_after_gate_flip_without_reopening_manual_actions`, `test_submitted_verifier_fail_open_materializes_originals_after_gate_flip`, and `test_verifier_deadline_wins_over_late_provider_response_without_a_second_call_or_settlement`; `app/services/consultation_split_gates.py`, `batch_split_enabled`.
- Exact expectation: `assert raised.value.code == "consultation_split_disabled"` (or matching the disabled message).
- Observed problem: four preference-opt-out cases expect disabled recovery actions. The accepted confirmed intent keeps its exact batch eligible after preference opt-out; completed/actively verifying batches instead reject recovery with `consultation_split_batch_unavailable`. Deployment-off cases still reject as disabled.
- Evidence: runtime file run reported 57 passed and these four failures during diagnostics work. A separate isolated pytest run loaded the unchanged commit's generation-runtime source without modifying the checkout or test isolation, reproducing the same four failures; all four corresponding deployment cases passed.
- Likely impact: focused runtime verification reports failures unrelated to provider diagnostics; the tests appear stale against the accepted-intent opt-out contract implemented on 7 October.
- Not fixed during split diagnostics because changing recovery authorization or its test contract would expand scope beyond messages and safe operational metadata.
- Follow-up: reconcile these assertions with the maintained accepted-intent/batch opt-out contract, retaining deployment-off, owner, retention, and exact-binding protection tests.

- Resolution — 2026-10-14, working tree based on `c712ba5b9db00e5008951bc3ff585a78e711c93f`: corrected only the four stale preference error expectations to `consultation_split_batch_unavailable`. The unchanged committed `batch_split_enabled` and maintained split documentation both preserve exact confirmed-intent eligibility after opt-out. Deployment-off assertions and verifier success/fail-open/late-result protections remain unchanged. Focused verification is reported separately.

## LLM-UNSUPPORTED-WORKER-PREFLIGHT — 2026-10-14

- Discovery commit: `c712ba5b9db00e5008951bc3ff585a78e711c93f` (resilience working tree).
- Location: `app/services/llm_execution.py`, `bounded_request`; provider submission boundaries in ordinary, split and suggestion workers.
- Observed problem: an unsupported thread/eventlet/gevent worker or occupied Unix timer is rejected before HTTP dispatch, but its durable attempt may already have been marked submitted. Existing terminalization then settles conservatively as unknown.
- Safe example: a synthetic generation executed in a background thread reaches `llm_execution_context_unsupported` without calling the provider. It emits no HTTP transport event.
- Impact: misconfigured workers can reserve and conservatively settle quota without an HTTP request. Maintained launchers pin prefork and ordinary credential preflight remains unchanged.
- Not expanded now: moving capability checks across every durable submission boundary would broaden worker lifecycle contracts and concurrent fault-test setup beyond the maintained prefork runtime mode.
- Follow-up: add a shared capability preflight before submission with focused pre-dispatch cancellation, duplicate-claim and concurrency tests.

## SPLIT-CREATE-PENDING-TRANSCRIPTION — 2026-10-08

- Discovery commit: unavailable (consultation-flow review of current working tree).
- Location: `app/services/consultation_split_intents.py`, `create_or_replay_consultation_split_intent`; `app/services/consultation_split_sources.py`, `prepare_source_bound_consultation_split_analysis`; contrast `app/services/templates.py`, `_ensure_generation_can_be_queued` and transcript-wait handling.
- Exact call: `prepared: PreparedConsultationSplitAnalysis = prepare_source_bound_consultation_split_analysis(db, actor, transcript_id=transcript_id)`.
- Observed problem: ordinary generation records pending ingestion and waits before resolving its transcript input. Split Create prepares a source snapshot immediately without an equivalent pending-ingestion wait. The browser permits Create while transcript text is queued/processing, so splitting can analyse an incomplete draft or only currently saved Working note/dictation.
- Likely impact: later transcript arrivals can invalidate analysis/continuation as stale and consume unnecessary analysis work. A clinician can see a split failure after a normal stop-and-Create action. Exact output/timing consequences require a synthetic regression; static inspection alone does not prove that every ordering rejects incomplete input.
- Evidence: read-only service/UI trace; no timing reproduction, provider calls, or live clinical data used.
- Not fixed during consultation-flow analysis because the task requests assessment of current versus ideal behavior. Binding an accepted split intent to pending ingestion and changing when its source snapshot is created needs a coherent lifecycle design and focused tests.
- Follow-up: define a shared server-owned readiness contract for ordinary and split Create, show a persistent waiting-for-transcription stage, and test pending/failing/final live chunks, whole-file ingestion, Working-note/dictation-only sources, duplicate delivery, source freshness, and one-click continuation.


## DSPT-RETRY-UI-EXPIRY — 2026-10-14 — resolved

- Discovery commit: `c712ba5`.
- Location: `app/web/transcribe_workspace.py`, `_earliest_retryable_ingestion_job`.
- Previous selection returned `db.scalar(statement.limit(1))` without checking source expiry. A retained expired failed upload appeared retryable, suppressing API/workspace expiry guidance.
- Fixed during resilience validation because the correction is bounded and uses the established `ingestion_retry_source_expired` service. The selected earliest failed source now returns no retry when expired; live ingestion never skips that gap to offer a later job.
- Validation: focused DSPT API/workspace expiry regression passed; existing live-gap retry coverage remains required. No retention or content-access contract changed.
