# Discovered issues

### ADMIN-WCAG22AA-REVIEW — 2026-10-24

- Status: Remediation implemented 2026-10-25; focused verification and remaining human assessment are recorded in [the follow-up](docs/accessibility/admin-verification-2026-10-25.md).
- Discovery baseline: `4603b1ec929fded6eb222a6e21878eca8a9fdfdb` plus the current working tree.
- Locations: `app/templates/admin_mockup.html` shared palette/focus styles, responsive navigation, provider wizards and member disclosures; `app/static/js/admin-usage-charts.js`.
- Exact examples: `--muted: #718096`, `.sidebar .nav-group { display: none; }` within the mobile media query, and the LLM wizard's unnamed `role="dialog"`.
- Impact: contrast, responsive access, dialog focus/naming, field associations and widget-state barriers affect administrators using low vision, keyboard and assistive technology.
- Original disposition: the 24 October request was for review only; remediation was subsequently authorized and implemented separately, preserving authorization, provider and quota contracts.
- Resolution: restored responsive navigation, strengthened contrast/focus, corrected dialog focus/labels and disclosure states, provided complete usage tables and keyboard chart controls, made reporting-range changes explicit, and contained wide data grids.
- Follow-up: complete the human checks listed in the dated verification. The original assessment is preserved as pre-remediation evidence; this entry does not establish full conformance.

### TEAM-QUOTA-DEFAULTS-WRONG-TAB — 2026-10-23

- Status: Resolved 2026-10-24; team Quotas tab added and quota defaults moved to Defaults.
- Resolution: added the system-admin-only team Quotas tab with an eligible-member selector, resource cards, on-demand quota actions, lifetime totals, allowance and history disclosures. Legacy selected-member links redirect to Quotas. Moved team quota defaults from Overview to Defaults and added a UI assertion for panel placement.
- Verification: focused quota UI tests and operational-document validation (see final implementation report).
- Discovery commit: `4603b1ec929fded6eb222a6e21878eca8a9fdfdb` plus the current working tree.
- Location: `app/templates/admin_mockup.html`, team workspace `data-panel="overview"` around line 3641; quota-defaults form around line 3711. The actual `data-panel="defaults"` starts around line 4107.
- Exact excerpt: `<header><h3>Quota defaults for new members</h3></header>` appears inside the Overview panel, while `docs/admin_workspace_function_map.md` says: “Each team's Defaults tab manages finite daily/monthly token and audio quota defaults for newly created members.”
- Observation: the form for editing team quota defaults is placed in Overview instead of the team's Defaults tab. The current markup-oriented test checks that quota-defaults content exists but does not verify which tab contains it.
- Impact: administrators looking for quota defaults in the documented Defaults tab will not find the form, and the maintained function map describes a different location from the rendered interface.

### ADMIN-LEGAL-SETUP-WARNING-OBSCURES-WORKSPACE — 2026-10-24

- Status: Resolved in the 2026-10-25 admin accessibility remediation; the required warning remains visible in page flow with a Review legal content link.
- Discovery baseline: `4603b1e` plus the current working tree; observed during quota-panel screenshot QA on 2026-10-24.
- Location: `app/templates/admin_mockup.html`, shared `.flash` styling around line 1420 and operator legal setup warning around line 2038.
- Exact excerpt: shared flash treatment and the warning container:

  ```css
  .operation-result,
  .flash {
    margin-bottom: 16px;
    padding: 14px 16px;
    border: 1px solid var(--border);
    border-left: 4px solid var(--success);
    border-radius: 8px;
    background: var(--card);
  }
  ```

  ```html
  <aside class="flash warning" role="status" aria-label="Operator legal setup warnings">
  ```
- Observation: when operator legal setup is incomplete, the long warning is rendered as a large shared flash that overlays a substantial part of the admin workspace, especially at a 390-pixel mobile viewport. Screenshot QA found it obscures quota controls and navigation.
- Impact: administrators may have difficulty reaching or reviewing workspace controls while the required operator setup warning is visible.
- Not fixed here: changing the warning layout is outside the quota-panel scope. Its required visibility must remain intact; do not weaken or hide the legal setup warning as a workaround.
- Follow-up: present the warning in a persistent in-flow or accessible collapsible layout that retains its status and review link, then verify visibility and workspace access on desktop and narrow mobile screens.
- Resolution: the later accessibility task removed warning-to-toast conversion, retained status semantics and the legal-content link, and placed the admin storage notice in flow. See [25 October verification](docs/accessibility/admin-verification-2026-10-25.md).

### LIVE-STT-FAILURE-DIAGNOSTICS — 2026-10-22

- Status: Open; diagnostic retention/correlation gap.
- Discovery baseline: `57af8486387ce2a4d4701c676c5f6cadfbfd4345` plus the existing working tree.
- Locations: `app/services/transcript_streaming.py::_settle_session_attempts`, `app/services/quotas.py::settle_provider_attempt_stream_unknown`, `app/routes/deepgram_live.py` failure warning, and `start-dev.sh`.
- Exact call: `settle_provider_attempt_stream_unknown(db, attempt_id=attempt.id, now=now)`.
- Observation: live failure settlement preserves an unknown outcome without copying the session's allowlisted failure code into the attempt. Session deletion clears the attempt's session reference. The runtime warning has safe failure/category fields but no attempt correlation key. The development launcher sends application and worker output to the terminal without configuring a retained log file.
- Impact: historical failures can retain an outcome without an actionable reason or a reliable link to their runtime warning. This does not establish the cause of a provider or retry failure.
- Not fixed here: this investigation is read-only operational diagnosis; changing diagnostic persistence or retention needs a focused implementation and tests.
- Follow-up: persist bounded failure codes on failed live attempts and define a privacy-safe correlation/retention policy for diagnostic events. Preserve unknown-outcome quota semantics and exclude clinical content, credentials, and raw exceptions.

### CHECKER-FAILURE-DEBUG-EXPECTATION — 2026-10-22

- Status: Resolved 2026-10-23; terminal failure diagnostics use content-free metadata.
- Resolution: restored the opt-in failure payload through the existing owner-encrypted storage, limited to allowlisted application/provider codes, HTTP status, and retry count. Provider and Vault failure API regressions pass, including checks that error-message sentinels and content-bearing fields are absent. The original discovery notes below are retained as history.
- Discovery baseline: `57af8486387ce2a4d4701c676c5f6cadfbfd4345` plus the existing working tree.
- Locations: `tests/test_api.py::test_structured_hallucination_check_provider_failure_records_safe_debug`, `test_structured_hallucination_check_vault_failure_does_not_fail_document`; `app/web/presentation.py::generated_document_response`.
- Exact expectation: `assert debug["failure_code"] == "vault_read_failed"` (and the equivalent provider-failure assertion).
- Observation: both tests enable the diagnostic flag but receive `hallucination_check_debug_json=None` after otherwise correct provider/credential failure handling, then fail while indexing it. The diarisation edits do not change checker-debug persistence or its presentation gate; the underlying reason for the missing debug payload still needs investigation.
- Impact: the selected checker regressions are not green. No change to checker execution correctness or privacy exposure was established by these assertions. Do not broaden debug content exposure merely to satisfy them.
- Not fixed here: diagnostic payload expectations are outside speaker-source handling. Follow up by tracing failure-debug persistence and reconciling the synthetic fixtures with the intended opt-in diagnostic contract, including default-off coverage.

### HALLUCINATION-DEBUG-CONTENT-BOUNDARY — 2026-10-22

- Status: Open; privacy-boundary follow-up.
- Discovery baseline: commit `445119e` plus the current working tree.
- Location: `app/services/templates.py::_run_hallucination_check`.
- Safe code excerpt: `"initial_note": _structured_note_debug_payload(title=title, sections=sections)` and `debug_payload["checker_edits"] = edits`.
- Observation: when the gated hallucination-check debug path is enabled, the initial note snapshot is retained on skipped and successful checks, and checker edits are retained on corrected checks. The record is encrypted on the owning generated document and remains owner-only; this is not evidence of cross-owner exposure. The terminal provider/credential failure paths added in this review use content-free failure metadata, but they do not change the older skipped and successful paths.
- Impact: opt-in diagnostic storage can retain transcript-derived note content, contrary to the project's content-free diagnostic boundary, even though the stored field is encrypted and owner-scoped.
- Not fixed here: changing the historical success/skipped debug contract is outside the four review fixes and needs a focused privacy review of the gate and presentation path.
- Follow-up: inspect the debug gate and every persistence/display path; remove note snapshots and checker edits from diagnostics or limit any required investigation to synthetic content. Keep owner and encryption boundaries intact, and test both default-off and explicitly enabled behavior.

Reconciled on 19 October 2026 against `97da191926db1da74fdf741e01f4f8490d60f6c8` and the remediation working tree; final verification was completed on 20 October 2026. This is a work log, not an architectural source of truth. Current status notes take precedence over the original dated observations retained beneath them. See the [remediation and regression review](docs/reviews/discovered-issues-remediation-2026-10-19.md) for changes, verification and limits.

| Disposition | Entries |
| --- | ---: |
| Open code defects | 0 |
| Resolved in this pass | 8 |
| Policy decision | 1 |
| Deployment follow-ups | 3 |
| Upgrade watchpoints | 2 |
| Resolved and superseded history | 25 |

## Resolved in this pass

### LLM-SDK-RESPONSE-SIZE-PARITY — 2026-10-16

- Current status: Resolved 2026-10-19; focused regression verification passed.
- Reconciliation/resolution (2026-10-19): OpenAI-compatible and Gemini SDK response hooks stop reads at 8 MiB and reject compressed bodies before decompression, while retaining proxy/TLS behavior. Real SDKs with synthetic mock streams, early Content-Length rejection, exact-boundary and closure tests pass.
- Verification and review: [19 October remediation record](docs/reviews/discovered-issues-remediation-2026-10-19.md). Prepared for commit; not deployed (status at final verification, 20 October 2026).

- Baseline commit: `717d2698a2ac268f830d99548068202a5de3f780` plus local uncommitted work.
- Locations: `app/services/llm_adapters/runtime.py::_invoke_openai_once`, `invoke_gemini`; `app/services/llm_adapters/gemini_enterprise.py::generate`.
- Observed problem: OpenAI-compatible and Gemini SDKs materialize responses without the application stream byte ceiling used by Ollama. Provider output-token caps and later validation do not establish an equivalent transport-memory bound.
- Impact: an oversized or malformed upstream response could consume memory before local parsing.
- Not fixed here: a post-read size check would not solve transport allocation; changing SDK transport/streaming needs a separate compatibility assessment. No memory exhaustion was reproduced in this source review.
- Follow-up: define response-byte limits and enforce them at the transport boundary with synthetic oversized-response tests, safe error codes and preserved usage/attempt settlement.

### SPLIT-RECOVERY-SCHEMA-SCOPE — 2026-10-16

- Current status: Resolved 2026-10-19; focused regression verification passed.
- Reconciliation/resolution (2026-10-19): New recovery provider schemas contain only failed UUIDs. Exact legacy queued requests are reconstructed from immutable snapshots and accepted only by full-request equality; arbitrary altered schemas fail before submission. Mixed-mode preparation and legacy/current/tampered runtime regressions pass.
- Verification and review: [19 October remediation record](docs/reviews/discovered-issues-remediation-2026-10-19.md). Prepared for commit; not deployed (status at final verification, 20 October 2026).

- Baseline commit: `717d2698a2ac268f830d99548068202a5de3f780` plus local uncommitted work.
- Location: `app/services/consultation_split_generation.py::prepare_split_generation_recovery_request`.
- Exact excerpt: `recovery_schema = prepared.response_json_schema`.
- Observed problem: a targeted recovery narrows payload topics and its nominal output cap but retains the initial provider response schema, which permits accepted sibling UUIDs. Runtime validation still enforces the requested failed-topic set.
- Likely impact: constrained decoding can permit irrelevant sibling output, wasting recovery capacity or requiring local rejection. No extra provider work or clinical validation bypass was demonstrated.
- Not fixed here: preserve the frozen-request reconstruction contract while addressing diagnostics and quota correctness; assess compatibility for already-queued recovery requests before changing schema reconstruction.
- Follow-up: build a failed-topic-only provider schema with matching snapshot/runtime reconstruction and synthetic Gemini/recovery regressions. Profile repeated initial-payload serialization before attempting a broader preparation refactor.

### GEMINI-400-ERROR-CLASSIFICATION — 2026-10-14

- Current status: Resolved 2026-10-19; focused regression verification passed.
- Reconciliation/resolution (2026-10-19): Only allowlisted structured model/location reasons map to those classifications. Other HTTP 400 failures use the content-free llm_provider_bad_request code. Structured-reason and fallback tests pass.
- Verification and review: [19 October remediation record](docs/reviews/discovered-issues-remediation-2026-10-19.md). Prepared for commit; not deployed (status at final verification, 20 October 2026).

- Discovery commits: working tree `dcdc3b7`; deployed `78ac327`.
- Location: `app/services/llm_adapters/gemini_enterprise.py`, `translate_gemini_error`.
- Exact excerpt: `if status_code == 400 and operation in {"model_validation", "generation"}: return AppError(422, "llm_model_unavailable", ...)`.
- Observed problem: every Gemini generation HTTP 400 is classified as `llm_model_unavailable`, including a valid model request rejected because its thinking level is unsupported.
- Likely impact: operators and clinicians can receive misleading model-availability guidance for malformed or model-option-specific requests, slowing diagnosis.
- Not fixed during the Gemini 3.8 Flash thinking-level repair because changing the error taxonomy requires a bounded, content-safe interpretation of Google's structured error reasons across validation and generation. Broadening this patch could alter existing provider failure contracts.
- Follow-up: inventory synthetic Gemini 400 response shapes, identify stable structured reason fields without persisting provider messages, and add focused translation tests before refining the classifier.

### LLM-UNSUPPORTED-WORKER-PREFLIGHT — 2026-10-14

- Current status: Resolved 2026-10-19; focused regression verification passed.
- Reconciliation/resolution (2026-10-19): All six main/checker/suggestion/split submission boundaries check worker capability before the submitted transition. Definite local failures cancel reservations; optional checkers preserve validated drafts as unchecked. Helper and workflow lifecycle regressions pass.
- Verification and review: [19 October remediation record](docs/reviews/discovered-issues-remediation-2026-10-19.md). Prepared for commit; not deployed (status at final verification, 20 October 2026).

- Discovery commit: `c712ba5b9db00e5008951bc3ff585a78e711c93f` (resilience working tree).
- Location: `app/services/llm_execution.py`, `bounded_request`; provider submission boundaries in ordinary, split and suggestion workers.
- Observed problem: an unsupported thread/eventlet/gevent worker or occupied Unix timer is rejected before HTTP dispatch, but its durable attempt may already have been marked submitted. Existing terminalization then settles conservatively as unknown.
- Safe example: a synthetic generation executed in a background thread reaches `llm_execution_context_unsupported` without calling the provider. It emits no HTTP transport event.
- Impact: misconfigured workers can reserve and conservatively settle quota without an HTTP request. Maintained launchers pin prefork and ordinary credential preflight remains unchanged.
- Not expanded now: moving capability checks across every durable submission boundary would broaden worker lifecycle contracts and concurrent fault-test setup beyond the maintained prefork runtime mode.
- Follow-up: add a shared capability preflight before submission with focused pre-dispatch cancellation, duplicate-claim and concurrency tests.

### SCRIBE-SIDEBAR-RESIZE-ALTERNATIVE — 2026-10-18

- Current status: Resolved 2026-10-19; focused regression verification passed.
- Reconciliation/resolution (2026-10-19): Added narrower/wider click buttons using existing resize bounds and persistence. Keyboard controls were already present; ArrowRight now expands correctly from collapsed width. Node behavioral tests cover pointer/keyboard/click bounds, collapse and persistence.
- Verification and review: [19 October remediation record](docs/reviews/discovered-issues-remediation-2026-10-19.md). Prepared for commit; not deployed (status at final verification, 20 October 2026).

- Discovery commit: `717d2698a2ac268f830d99548068202a5de3f780`.
- Locations: `app/templates/transcribe/_sidebar.html`, `data-sidebar-resize`; `app/templates/transcribe/_shell_extras.html`, sidebar resize handlers.
- Exact UI hint: `title="Drag to resize. Double-click to collapse or restore."`.
- Observed constraint: the Scribe-only resizer supports pointer dragging and a double-click collapse/restore action; its resize handler does not implement keyboard adjustment or click-based width steps. Help uses the separate non-Scribe workspace sizing handler, which now has both keyboard and click alternatives.
- Likely impact: users who cannot drag may not be able to choose an intermediate Scribe sidebar width. This is distinct from the Help sidebar and requires a focused Scribe resizing review.
- Not changed now: this task's Help accessibility scope did not authorize a broader Scribe layout interaction change.
- Follow-up: assess and add an equivalent single-pointer alternative and keyboard controls to the Scribe resizer, with focused regression tests; verify collapsed and intermediate-width behavior.

### CONSULTATION-DERIVED-CONTENT-DELETE-CONFIRMATION — 2026-10-18

- Current status: Resolved 2026-10-19; focused regression verification passed.
- Reconciliation/resolution (2026-10-19): Added the owner-scoped has_saved_content flag for deletion confirmation, covering transcript text plus Working-note, retained dictation and ready generated-note record presence. Derived-source checks are batched and metadata-only. Existing has_transcript_content status/readiness behavior is preserved. API/history and single/bulk cancel/proceed tests pass.
- Verification and review: [19 October remediation record](docs/reviews/discovered-issues-remediation-2026-10-19.md). Prepared for commit; not deployed (status at final verification, 20 October 2026).

- Discovery commit: `717d2698a2ac268f830d99548068202a5de3f780`.
- Locations: `app/static/js/transcribe/actions.js`, bulk delete form submit handler; `app/web/transcribe_workspace.py`, `_transcript_has_content` and `_transcript_content_by_id_for_history_page`.
- Exact excerpt: `selectedBoxes.some((checkbox) => checkbox.dataset.hasTranscriptContent === 'true')`; the flag is derived from transcript draft and version text.
- Evidence: static inspection only; this gap was not runtime-reproduced. The confirmation check covers transcript text but does not account for a consultation whose transcript is blank while its Working note, post-consultation dictation, or generated notes contain content.
- Likely impact: a user can delete such a consultation without the extra confirmation and may accidentally lose its consultation-owned derived content permanently.
- Not fixed during Help work: changing deletion confirmation and content-presence semantics is outside the Help scope and needs focused lifecycle/privacy review; no deletion or authorization contract was changed here.
- Follow-up: derive a metadata-only “has consultation content” flag covering transcript text/version and the relevant derived-content records, then add focused synthetic tests for history-page flags and single/bulk confirmation behavior, including blank-transcript Working-note/dictation/generated-note cases. Preserve existing ownership, authorization, retention, and cascade behavior.

### TEST-CONFIG-GUARD-SECRET-DIAGNOSTICS — 2026-10-18

- Current status: Resolved 2026-10-19; focused regression verification passed.
- Reconciliation/resolution (2026-10-19): Database/Redis isolation guard refusals omit connection URLs and credentials while retaining refusal checks. Synthetic credential regressions pass; infrastructure isolation was not weakened.
- Verification and review: [19 October remediation record](docs/reviews/discovered-issues-remediation-2026-10-19.md). Prepared for commit; not deployed (status at final verification, 20 October 2026).

- Discovery commit: unavailable (documentation review of the current working tree).
- Location: `tests/db_utils.py`, `ensure_safe_test_database_url` and `ensure_safe_test_rate_limit_storage_url`.
- Exact excerpt: `f"DATABASE_URL={app_url}\n"`; the Redis guard also interpolates its complete application and test URLs.
- Observed problem: when an isolation guard rejects equal application/test endpoints, its `RuntimeError` includes complete connection URLs. A URL can contain credentials, so a failed test startup can expose them through terminal or CI output. Static source inspection established the interpolation; no real credentials were printed to reproduce it.
- Likely impact: diagnostic output can violate the project rule against exposing credentials and DSNs. The guard correctly refuses unsafe infrastructure use; its message needs redaction.
- Not fixed in this documentation review because changing test infrastructure is separate executable work. The documentation continues to prohibit printing secrets or bypassing isolation guards.
- Suggested follow-up: preserve the equality checks and refusal, replace URL values with safe labels or bounded host/port metadata, and add synthetic-credential regression cases for both guards.

### TEMPLATE-SUGGESTION-DUPLICATE-JSON-KEY — 2026-10-19

- Current status: Resolved 2026-10-19; focused regression verification passed.
- Reconciliation/resolution (2026-10-19): Template suggestions now use the shared duplicate-key rejection hook at every object depth before validation. Duplicate and unambiguous JSON regressions pass.
- Verification and review: [19 October remediation record](docs/reviews/discovered-issues-remediation-2026-10-19.md). Prepared for commit; not deployed (status at final verification, 20 October 2026).

- Discovery commit: `717d269`.
- Location: `app/services/template_suggestions.py`, `_extract_json`.
- Exact excerpt: `value = json.loads(encoded)`.
- Observed problem: duplicate keys in provider JSON are accepted with the last value winning; for example, conflicting `template_id` fields can make the classifier select a candidate while hiding an earlier conflicting value.
- Likely impact: ambiguous provider output can produce an unreliable template recommendation. This affects reusable-template metadata only, not generated clinical documents.
- Not fixed now: the current regression fix targets stream readiness boundaries; changing this parser is a separate fail-closed classifier change.
- Follow-up: use the shared duplicate-key rejecting JSON hook and add synthetic duplicate-key cases for top-level and nested fields.

## Policy decision

### NOTE-OLLAMA-OUTPUT-RESERVATION-PARITY — 2026-10-16

- Current status: Decision required; existing output-ceiling policy is preserved.
- Reconciliation/resolution (2026-10-19): The proposed remedy is a 1,600-token Ollama num_predict ceiling for follow-ups/Quick Actions, matching the existing nominal reservation and retaining truncation rejection. The user has been asked for the policy choice required by AGENTS.md. No unanswered choice is treated as approval.

- Discovery commit: `717d2698a2ac268f830d99548068202a5de3f780`.
- Location: `app/services/llm_adapters/runtime.py:87`, `generation_request_snapshot`; `app/services/templates.py:3542`, `_flush_generated_document_with_quota`; `:4626`, `_claim_generated_document_for_dispatch`.
- Exact excerpt: `if output_token_cap is not None: request_body["options"] = {"num_predict": output_token_cap}`.
- Observed behavior: follow-ups and Quick Actions pass no output cap, so Ollama receives no `num_predict`, while reservation estimates use a finite nominal output allowance (initially 1600 tokens). A synthetic request assertion confirmed the missing cap; `tests/test_llm_runtime.py` explicitly preserves this existing shape.
- Impact: the application does not make the provider output ceiling match its reservation assumption. Actual completion length depends on provider configuration; this review did not demonstrate a quota overrun.
- Not fixed: changing the preserved request shape is a scoped contract decision, and the user requested assessment only.
- Follow-up: agree an explicit non-Template output ceiling or another reservation policy, then test provider request/reservation/actual usage/unknown settlement together. Keep Template length preferences separate from this decision.

## Deployment follow-ups

### ONE-WORKER-PROVIDER-STALL — 2026-10-14

- Current status: Operational follow-up; historical observations are not current deployment verification.
- Reconciliation/resolution (2026-10-19): Request deadlines/SDK retry controls and opt-in isolated prefork workers exist in current code. Deployment capacity and queue progress remain operational verification; preserve the original incident below.

- Discovery commit: deployed `78ac327`.
- Location: `docker/entrypoint.sh`, runtime Celery worker command; `app/services/llm_adapters/gemini_enterprise.py`, `build_gemini_client`.
- Exact excerpts: `-Q control,generation,ingestion` and `--concurrency "${CELERY_WORKER_CONCURRENCY:-1}"`; `types.HttpOptions(api_version=api_version, headers=headers or None)`.
- Observed problem: metadata-only production review showed one Gemini 3.5 consultation-split generation submitted at 14:06:07 UTC and terminalized at 14:16:05 with HTTP 503 / `provider_server_error` (about 598 seconds). A separate ordinary note had been queued and published at 14:07:43, but its worker had not received it until 14:16:05; it started at 14:16:08 and failed at 14:16:34 with HTTP 429. Afterward the worker was idle, queues were empty, and there were no active attempts, unfinished documents, or database blockers. The team had no checker selection and there were no checker or consultation-split verification attempts in the inspected 24-hour metadata window; the checker was not the observed cause.
- Likely impact: a slow synchronous provider call occupies the single worker slot and delays other queued generation. The Gemini client supplies no explicit HTTP timeout; the installed SDK's request timeout is unset by this configuration. Provider capacity/rate limits can also turn delayed requests into failed generations. The observation establishes queue starvation during a slow provider request, not a persistent provider outage or a database lock.
- Not fixed during production diagnosis because changing worker topology, request deadlines, and provider-capacity/429 policy together needs a scoped plan that preserves submitted-attempt idempotency, quota settlement, and durable outbox semantics. No database state, queue, worker, or deployment configuration was changed.
- Follow-up: isolate the control worker as recommended by [docker.md](docs/docker.md), give generation work sufficient isolated capacity, and define explicit provider request timeouts that fit the durable attempt deadlines. Review rate-limit/capacity handling separately; do not reset or replay submitted attempts to recover this incident. Validate queue progress under a synthetic slow-provider scenario, timeout-to-terminal-settlement behavior, duplicate delivery, and the provider 429 failure path.

### GEMINI-25-FLASH-RETIREMENT-AND-LONDON-CAPACITY — 2026-10-13

- Current status: Operational follow-up; historical observations are not current deployment verification.
- Reconciliation/resolution (2026-10-19): Google still lists 20 October 2026 retirement for Gemini 2.5 Flash; see the [current model lifecycle](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions). Approved deployment model/region selection and capacity/clinical validation remain required; no live provider/configuration change was made.

- Discovery commit: deployed `78ac327`.
- Location: `app/models.py`, `TeamLlmConfig.model_name`; Google Cloud's [Gemini 2.5 Flash lifecycle and availability page](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/2-5-flash).
- Observed problem: deployed metadata shows the active Gemini configuration defaults to `gemini-2.5-flash` in `europe-west2` (London), with no purchased Provisioned Throughput and PayGo-only capacity. The current Google model page lists an October 20, 2026 retirement date for Gemini 2.5 Flash. Recent attempts frequently override the default with `gemini-3.5-flash`; both synthetic London probes for 2.5 Flash and 3.5 Flash succeeded. Therefore, the retirement is approaching, but these observations do not establish a persistent London 3.5 Flash capacity outage or that the default is responsible for the reported 429.
- Likely impact: configurations that continue to select the retiring default may lose supported generation after retirement; London capacity behavior for PayGo remains subject to Google's model/location support and current shared capacity.
- Not changed during the capacity investigation because provider/model selection must account for approved London residency, capacity availability, and clinical review; changing the model or location would exceed a diagnostic metadata-only action.
- Follow-up: plan and validate migration to a model explicitly approved for the deployment's residency and capacity requirements before the published retirement date. Reconfirm Google's current lifecycle and London availability at that time; do not infer a persistent outage from isolated 429s or successful synthetic probes.

### OPENSCRIBE-CONTAINER-UNHEALTHY — 2026-10-12

- Current status: Operational follow-up; historical observations are not current deployment verification.
- Reconciliation/resolution (2026-10-19): The local Docker inventory has healthy OpenScribe PostgreSQL/Redis/Vault containers but no running OpenScribe application container. The dated deployed health failure cannot be reproduced or cleared from this environment. No services were started or restarted.

- Discovery commit: `2d7bfcb`.
- Location: deployed `openscribe-openscribe-1` container healthcheck.
- Exact observation: `openscribe-openscribe-1` was `Up 2h (unhealthy)` while PostgreSQL, Redis, and Vault were healthy.
- Observed problem: the deployed application container is reporting an unhealthy healthcheck state.
- Likely impact: orchestration or operational monitoring may treat the application as unavailable even while supporting services remain healthy.
- Not fixed during the consultation-split provider-binding repair because this is unrelated to the deterministic pre-submit request mismatch. No healthcheck logs, environment, or container state beyond the reported status were inspected.
- Follow-up: inspect the healthcheck command, recent healthcheck output, and relevant safe operational logs; then reproduce and repair the specific healthcheck failure without exposing sensitive configuration or content.

## Upgrade watchpoints

### DEEPGRAM-SDK-PRIVATE-WEBSOCKET-HOOK — 2026-10-17

- Current status: Compatibility watchpoint; pinned implementation and regression coverage remain.

- Discovery commit: `717d269` (working tree includes the current Deepgram live-stream adapter).
- Location: `app/services/deepgram_streaming.py::_install_scoped_sdk_websocket_logger`.
- Observed constraint: pinned `deepgram-sdk==7.13.0` does not expose public per-connection arguments for the underlying WebSocket logger or message/queue bounds. The adapter wraps only the generated Listen V1 connector and passes the scoped disabled logger, `max_size`, and `max_queue` to the pinned `websockets` legacy connector.
- Likely impact: an SDK upgrade or generated connector change can invalidate these privacy and memory bounds.
- Not changed now: the pinned connector supports these per-connection options, and a synthetic adapter test checks that the wrapper applies them while preserving other connection options. Replacing the hook would require an SDK-supported public equivalent.
- Follow-up: prefer a public SDK per-connection connector/logger/buffer configuration if Deepgram exposes one; update the compatibility test when upgrading the SDK.

### PINNED-VAD-STREAM-BUFFER-BOUND — 2026-10-17

- Current status: Compatibility watchpoint; pinned implementation and regression coverage remain.

- Discovery commit: unavailable (Deepgram live-stream implementation working tree).
- Location: `app/static/js/transcribe/media.js`, `buildLiveVadInstance`; pinned runtime `app/static/vendor/vad-web/0.0.29/bundle.min.js`, `FrameProcessor.process`.
- Observed constraint: the pinned `FrameProcessor` emits its `FrameProcessed` callback immediately before appending the frame to its speech accumulator. The streaming callback trims that accumulator to approximately five seconds without resetting VAD state; the implementation and synthetic runtime test depend on that event order and the exposed `frameProcessor.audioBuffer` array.
- Likely impact: an upstream VAD runtime upgrade that changes callback order or buffer shape could remove the bound or fail closed for streaming capture.
- Not changed now: the bounded adapter is limited to the Deepgram streaming path; the non-stream chunk behavior remains unchanged.
- Follow-up: when upgrading the pinned VAD runtime, rerun the synthetic test against the actual vendored bundle and update the adapter only if its memory and speech-boundary behavior remain correct.

## Resolved and superseded history

### GENERATION-ADAPTER-RESERVATION-PARITY — 2026-10-16

- Current status: Resolved as recorded below.

- Baseline commit: `717d2698a2ac268f830d99548068202a5de3f780`; local working tree includes earlier uncommitted work.
- Locations: `app/services/consultation_split_generation_queue.py::queue_confirmed_split_generation`, `consultation_split_recovery.py::_queue_recovery`, `consultation_split_verification_queue.py::queue_split_verification_or_mark_unchecked`, and `app/services/template_suggestions.py::queue_template_suggestion` / `process_template_suggestion`.
- Observed problem: queues reserved nominal completion caps while Gemini requests used the established 30,000-token ceiling. Split verification also used a local character-divided-by-four input estimate instead of the maintained quota estimator. Template suggestions estimated a placeholder excerpt before redaction and did not expand the reservation for a larger final request.
- Impact: reserved capacity could be below the submitted request estimate; reported/unknown usage settlement still applied.
- Resolution: derive queue reservations from adapter-specific output caps and the maintained UTF-8 estimator. Template suggestions also check the final redacted request before submission. Keep provider caps, clinical prompts, snapshots and quota settlement contracts. Optional verification remains unchecked when accurate reservation exceeds available quota.
- Validation: focused queue/attempt regressions cover Gemini, targeted recovery and quota rejection; final results appear in [the stage review](docs/reviews/generation-stage-cost-logging-review-2026-10-16.md).

### GENERATION-SAFE-STAGE-DIAGNOSTICS — 2026-10-16

- Current status: Resolved as recorded below.

- Baseline commit: `717d2698a2ac268f830d99548068202a5de3f780` plus local uncommitted work.
- Locations: `app/services/transcripts.py::process_transcript_ingestion_job`, `app/services/templates.py::_generation_usage_event` / `_run_hallucination_check`, `app/tasks.py` ingestion/generation/template-suggestion task boundaries.
- Observed problem: ingestion failures lacked operational stage evidence, ordinary checker events lacked separate operational logs, and exceptions outside service-owned catches could reach Celery with database/provider details.
- Resolution: controlled stage/status/error categories and UUID correlation, with default-message and structured fields; fresh content-free task exceptions suppress original exception rendering. Checker logs use captured immutable IDs to avoid ORM refreshes after commit/rollback. The ingestion task also supplies a fixed redacted Celery kwargs representation before handling legacy audio payloads. Preserve existing task failure state and generation wait/retry behavior. No new database rows, provider calls, or clinical-content logs.
- Validation: synthetic sensitive sentinels, formatted traceback assertions, checker outcomes and ingestion settlement tests; see [the stage review](docs/reviews/generation-stage-cost-logging-review-2026-10-16.md).

### SPLIT-GEMINI-CHECKER-SCHEMA-SECRET-SCAN — 2026-10-16

- Current status: Resolved as recorded below.

- Baseline commit: `717d2698a2ac268f830d99548068202a5de3f780` plus local uncommitted work.
- Locations: `app/services/consultation_splits.py::queue_split_execution`, `app/services/consultation_split_generation_runtime.py::_finalize`.
- Observed problem: Gemini embeds its generated response schema in the request. The recursive secret-name guard treated the canonical schema property `section_key` as a credential key, preventing bundled checker queueing. Finalization then mapped the consumed failure to a generic generation-invalid-output code without operational stage evidence.
- Resolution: exempt only the exact schema property `section_key: {"type": "string"}` inside response-schema properties; continue scanning other keys and values, including altered definitions and nested credentials. Add a fixed-phase, content-free finalization warning while preserving persisted failure/accounting contracts.
- Validation: Gemini queue and quota-fallback regressions plus negative schema/config credential-injection tests; final results appear in [the stage review](docs/reviews/generation-stage-cost-logging-review-2026-10-16.md).

### NOTE-OPENAI-INCOMPLETE-OUTPUT — 2026-10-16

- Current status: Resolved as recorded below.

- Discovery commit: `717d2698a2ac268f830d99548068202a5de3f780`.
- Location: `app/services/llm_adapters/runtime.py:300`, `_invoke_openai_compatible_once`; `app/services/templates.py:5689`, `_process_generated_document_impl`.
- Exact excerpt: `message = completion.choices[0].message if completion.choices else None`.
- Observed problem: the adapter extracts nonempty text without inspecting the choice's completion reason. A synthetic completion with `finish_reason="length"` returned successfully. Follow-up and Quick Action paths accept this text directly and can mark it ready; parseable Template output can also pass despite an incomplete completion reason.
- Impact: an incomplete clinical draft can look like an ordinarily completed generation. This is an adapter probe plus worker-path inspection, not an end-to-end patient-data reproduction.
- Not fixed: the requested work is a review and backlog for later fixes.
- Follow-up: reject known incomplete completion reasons before output acceptance, using bounded safe codes; define compatibility for gateways that omit the reason. Preserve quota settlement and prevent automatic replay of submitted work. Add synthetic adapter and document-lifecycle regressions for truncated text, parseable JSON, normal completion, and missing reason.
- Resolution note (2026-10-16; baseline `717d2698a2ac268f830d99548068202a5de3f780`; no commit made): `_invoke_openai_compatible_once` now checks `finish_reason` through `_validate_completion_finish_reason`; `length` maps to `llm_generation_truncated`, other explicit non-completion reasons fail with a bounded generic code, and absent/null reason remains accepted for compatibility. Consumer lifecycle tests confirm a parseable Template response and plain-text follow-up/Quick Action cannot become ready and duplicate task delivery does not make another provider call. Evidence: `tests/test_llm_runtime.py::test_openai_completion_accepts_complete_or_legacy_missing_finish_reason`, `::test_openai_completion_rejects_incomplete_or_malformed_finish_reason`, and `tests/test_generation_unsafe_failures.py::test_explicit_truncation_fails_followup_quick_action_and_parseable_template_without_replay`.

### NOTE-OLLAMA-INCOMPLETE-STREAM — 2026-10-16

- Current status: Resolved as recorded below.

- Discovery commit: `717d2698a2ac268f830d99548068202a5de3f780`.
- Location: `app/services/llm_adapters/runtime.py:401`, `_invoke_ollama_once`; `app/services/templates.py:5689`, `_process_generated_document_impl`.
- Exact excerpt: `payload = final_payload or {}`.
- Observed problem: a nonempty stream followed by EOF without a `done: true` frame returns successfully. A synthetic `done: false` content frame reproduced this. The adapter also ignores the terminal `done_reason`.
- Impact: partial follow-up/Quick Action text can become ready, and usage totals can be unavailable. Template JSON parsing catches malformed JSON but cannot prove transport completion.
- Not fixed: review-only scope.
- Follow-up: require terminal completion, classify bounded terminal reasons, and test EOF with/without a trailing newline, partial JSON and text, successful terminal frames, and rejected incomplete terminal reasons. Preserve response-byte limits, submitted-attempt accounting, and no ambiguous automatic replay.
- Resolution note (2026-10-16; baseline `717d2698a2ac268f830d99548068202a5de3f780`; no commit made): `_invoke_ollama_once` now requires `done: true`, rejects explicit length/other non-completion reasons with safe codes, and accepts absent/null `done_reason` only when a terminal frame exists. EOF without the terminal frame is a generic incomplete-response failure. Context cleanup, no-ready persistence, conservative settlement, and duplicate-delivery no-replay behavior are covered. Evidence: `tests/test_llm_runtime.py::test_ollama_accepts_terminal_stop_frame_with_optional_reason_and_no_final_newline`, `::test_ollama_rejects_incomplete_or_malformed_terminal_reason`, `::test_ollama_rejects_eof_without_terminal_frame`, and `tests/test_generation_ollama_unsafe.py::test_ollama_incomplete_response_fails_ordinary_generation_once`.

### NOTE-AMBIGUOUS-JSON-KEYS — 2026-10-16

- Current status: Resolved as recorded below.

- Discovery commit: `717d2698a2ac268f830d99548068202a5de3f780`.
- Location: `app/services/templates.py:2559`, `_parse_generated_note_json`; `:2603`, `_parse_generated_structured_note_json`; `:2778`, `_parse_hallucination_check_response`; `app/services/consultation_split_verification.py:129`, `apply_split_verification_response`.
- Exact excerpt: `raw = json.loads(payload_text)`.
- Observed problem: these paths accept duplicate JSON keys with last-value-wins semantics. Synthetic assertions confirmed duplicate ordinary content, checker status, and split-verifier topic keys pass. Split analysis and generation already reject duplicates.
- Impact: contradictory provider output can be accepted as an unambiguous note/check result. Existing value, key-set and exact-patch checks still apply; this is a validation gap, not evidence of cross-owner access.
- Not fixed: review-only scope; preserve the current response shapes.
- Follow-up: consistently reject duplicate keys at every nesting level without exposing offending keys/values. Add nested/top-level regressions and ensure checker rejection leaves the original validated draft available under the existing unchecked contract.
- Resolution note (2026-10-16; baseline `717d2698a2ac268f830d99548068202a5de3f780`; no commit made): the shared `app/services/json_validation.py::reject_duplicate_json_keys` hook now rejects duplicate fields at every object depth in ordinary freeform/structured note parsing, checker response parsing, and split-verifier outer parsing. Ordinary fenced and balanced-object compatibility, exact response shapes, optional sections, and existing safe error codes remain. Duplicate checker output continues through the existing failed/unchecked path and leaves the validated encrypted draft ready. Evidence: `tests/test_generation_json_validation.py`, `tests/test_consultation_split_verification.py`, and `tests/test_generation_unsafe_failures.py::test_invalid_checker_response_keeps_original_structured_note_ready_unchecked`. Existing split analysis/generation duplicate-key validation was not changed.

### NOTE-CHECKER-DEEP-JSON — 2026-10-16

- Current status: Resolved as recorded below.

- Discovery commit: `717d2698a2ac268f830d99548068202a5de3f780`.
- Location: `app/services/templates.py::_parse_hallucination_check_response`.
- Exact excerpt before fix: `payload = json.loads(payload_text.strip())`.
- Observed problem: a deeply nested JSON response could raise `RecursionError` outside the checker's invalid-response mapping. Since the structured draft is already validated at this point, the uncaught exception could fail the whole generated-document worker rather than leave the original note available as unchecked.
- Impact: optional checker parsing could interrupt successful generation after a valid draft checkpoint; this did not accept an invalid patch or expose data across owners.
- Resolution (2026-10-16; no commit made): recursion overflow now maps to the existing content-free `hallucination_check_invalid_response` with `failure_code=invalid_json` and no raw exception cause. The checker retries/falls back under its existing fail-open behavior; the validated draft remains ready and unchecked.
- Evidence: pure regression `tests/test_generation_json_validation.py::test_hallucination_checker_parser_maps_deep_json_recursion_to_safe_invalid_response`; worker regression `tests/test_generation_unsafe_failures.py::test_invalid_checker_response_keeps_original_structured_note_ready_unchecked[deeply-nested]`. The parent’s final selected lifecycle/API-control run passed 16 tests, including both checker fallback cases.
- Follow-up: none for this bounded parser failure; preserve the regression tests and current checker fail-open contract.

### NOTE-SPLIT-UNEXPECTED-FAILURE-TELEMETRY — 2026-10-16

- Current status: Resolved as recorded below.

- Discovery commit: `717d2698a2ac268f830d99548068202a5de3f780`.
- Location: `app/tasks.py:94`, `process_consultation_split_execution_task`.
- Exact excerpt: `except Exception:` followed by `db.rollback()`.
- Observed problem: unexpected task errors are deliberately swallowed to prevent sensitive exception text reaching Celery. This boundary emits no content-free diagnostic event; lifecycle reconciliation later terminalizes abandoned work.
- Impact: a local worker defect can look like a delayed timeout, making stage attribution and incident investigation difficult.
- Resolution (2026-10-16; no commit made): `process_consultation_split_execution_task` now emits the ERROR event `consultation_split_worker_failed` through `openscribe.tasks` with only `execution_id` (parsed UUID), allowlisted `phase` (`session`, `lookup`, `analysis`, `generation`, `verification`, `cleanup`), allowlisted `reason_code` (`unexpected_worker_error`, `session_error`, `rollback_error`), and UTC ISO `occurred_at`. The fields are included in both structured log extras and the normal formatted message, so diagnosis does not depend on database availability. The initial failure is logged before rollback; session factory/enter/exit failures and rollback failures are contained and safely classified. No exception text/type, traceback, provider/source/response content, credentials, or database event row is recorded. The task does not replay provider work or change attempt, quota, queue, or lifecycle state; submitted uncertainty remains with existing reconciliation.
- Evidence: `tests/test_consultation_split_worker_diagnostics.py` covers synthetic sensitive exceptions, session boundary and rollback failures, success/no-op silence, and absence of retries or mutations. Parent focused run: `APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app .venv/bin/pytest -q tests/test_consultation_split_worker_diagnostics.py tests/test_consultation_split_runtime.py tests/test_consultation_split_generation_runtime.py tests/test_consultation_split_intent_progress.py tests/test_task_outbox.py` — 142 passed, 1 Starlette/httpx warning.

### NOTE-PIPELINE-REASSESSMENT — 2026-10-16

- Current status: Superseded or reconciled with current source; see dated notes.

- Review commit: `717d2698a2ac268f830d99548068202a5de3f780`; full assessment: [note generation pipeline review](docs/reviews/note-generation-pipeline-review-2026-10-16.md).
- `ONE-WORKER-PROVIDER-STALL`: preserve the dated incident. Current `llm_execution.py` and Gemini client configuration now bound provider calls and disable SDK retries. The old missing-timeout observation does not describe current source. Worker isolation/capacity still requires deployment-specific verification; none was performed here.
- `SPLIT-CREATE-PENDING-TRANSCRIPTION`: current `consultation_split_intents.py`, `_require_source_ready_for_analysis`, rejects recording/transcribing/pending ingestion before preparation and repeats the check afterward. The earlier immediate-incomplete-snapshot concern is now guarded in this path. Split rejects/waits through client behavior rather than accepting ordinary generation's server-side waiting contract; browser timing was not tested in this review.
- `LLM-UNSUPPORTED-WORKER-PREFLIGHT` and `GEMINI-400-ERROR-CLASSIFICATION` remain relevant source-level follow-ups; no live provider or deployment conclusions were drawn.
- Additional decisions and opportunities, including empty structured results, response-size parity and safe stage diagnostics, are in the dated review. No runtime fixes, migrations or configuration changes were made.

### SPLIT-DELETED-SLOTS-MERGE — 2026-10-15

- Current status: Superseded or reconciled with current source; see dated notes.
- Reconciliation/resolution (2026-10-19): Current 97da191 includes the deleted-topic projection and browser filtering, with API/runtime/JavaScript regressions. The old branch-integration instruction is superseded; deployment state is not inferred from this checkout.

- Discovery commit: `78ac327d45a59a34293d3771619f2f267e2cea4e` (`fix(split): hide deleted note slots`), currently on `fix/deleted-split-slots-release`.
- Location: `app/services/consultation_split_api.py`, `read_workspace_split_batch`; projection field in `app/schemas/consultation_split.py`, `ConsultationSplitBatchDetail`; rendering in `app/static/js/transcribe/app.js`, `buildSplitDisplayDocuments` and `reconcileSplitPlaceholderState`.
- Exact excerpt: `deleted_topic_uuids=deleted_topic_uuids,`
- Observed problem: this release-branch change projects independently deleted generated-note topics so the browser omits their old split placeholders. The production checkout was on `fix/deleted-split-slots-release`, while the requested deployment target is `master`; switching to and building `master` leaves this behavior out until commit `78ac327` is integrated.
- Likely impact: after a clinician independently deletes a generated note, refreshing the workspace on code without this change can display that topic as a failed or phantom split slot, even though generation completed and the note was deliberately deleted.
- Not merged during the branch switch and build because the requested immediate operation is to deploy `master`; integrating this separate release-branch change requires reconciling its API, browser, tests, and maintained split documentation with current `master`.
- Follow-up: merge or cherry-pick `78ac327d45a59a34293d3771619f2f267e2cea4e` into `master`, resolve any drift, and run focused consultation-split API, generation-runtime, browser, and JavaScript tests before deployment.

### XDIST-RANDOM-PARAM-ID — 2026-10-14

- Current status: Resolved as recorded below.

- Discovery commit: `c712`.
- Location: `tests/test_consultation_split_runtime.py`, parameterization for `test_runtime_rejects_invalid_or_unavailable_provider_output_once`.
- Observed problem: a synthetic unknown-template UUID was embedded in the parameter value, making pytest derive different collection IDs in xdist workers.
- Resolution: assigned stable explicit IDs to all three cases while retaining the original generated synthetic values and assertions.
- Verification: rerun the full xdist suite after the metadata-only fix.

### GEMINI-3-THINKING-FALLBACK — 2026-10-14

- Current status: Resolved as recorded below.

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

### API-AUDIT-SHARED-INFRASTRUCTURE-LOCK — 2026-10-05

- Current status: Resolved as recorded below.

- Discovery commit: unavailable (working tree investigation).
- Location: `scripts/audit_api_auth.py`, `main` and `reset_public_schema`.
- Exact excerpt: `reset_public_schema()` followed by `rate_limit_redis.flushdb()`.
- Observed problem: the standalone API audit resets the canonical test database and limiter store without acquiring the `/tmp/openscribe_pytest.lock` used by pytest.
- Likely impact: running the audit beside an active pytest process can reset that process's shared test infrastructure. Static tracing established the gap; no concurrent destructive run was attempted.
- Not fixed during the Multiple problems toggle because changing audit infrastructure is separate work. Audit verification remains pending while the shared lock is occupied.
- Follow-up: share the existing test-run lock with the audit before any infrastructure initialization or reset, then add an isolated contention regression.
- Resolved 2026-10-07: pytest and the API audit share the nonblocking lock helpers in `tests/db_utils.py`. Both acquire the lock before database creation; the audit holds it through final Redis cleanup and exits with code `2` when busy. An isolated subprocess regression verifies that contention causes no database initialization, schema reset, or Redis flush.

### SPLIT-GENERATION-STATUS-HIDDEN — 2026-10-05

- Current status: Resolved as recorded below.

- Discovery commit: unavailable (working tree investigation).
- Location: `app/static/js/transcribe/app.js`, `createSplitGenerateController` setup.
- Exact excerpt: `setStatus: () => {},`
- Observed problem: terminal split-generation status messages, including a rejected or failed one-note continuation, are discarded by the workspace integration.
- Likely impact: a clinician may not receive the controller's safe actionable failure message after the dictation modal closes.
- Not fixed during the queued-intent restoration repair because presenting and deduplicating split lifecycle feedback is a separate workspace feedback contract.
- Follow-up: define a visible, accessible, non-noisy status surface for terminal split-generation failures and add browser coverage for rejected continuation feedback.
- Resolved 2026-10-07: the workspace now forwards split-generation `warning` and `error` statuses to the existing accessible flash banner via `showFlash`; controller coverage continues to assert safe terminal guidance and rejected continuation feedback.

### SPLIT-PREFERENCE-BROWSER-CHECKBOX-CLICK — 2026-10-01

- Current status: Resolved as recorded below.

- Discovery commit: `32eaf7caf2f6a26929195ee1d06e8f9b2dab97d7`.
- Location: `tests/test_consultation_split_browser.py:252`, `test_browser_team_leader_split_preference_persists_across_workspace_navigation`; `app/static/css/components.css:208-209`, compact switch styling.
- Exact excerpt: `split_toggle.check()` targets the 1px checkbox while `.switch-compact__track` occupies the visible switch area.
- Observed problem: Playwright times out because the track intercepts pointer events. The test failed the same way on its own rerun; the other four browser tests passed.
- Likely impact: this browser regression cannot verify preference persistence; the styled label may still respond to ordinary user clicks.
- Not fixed during the note-split confirmation change because the preference control and its test are outside the affected flow.
- Follow-up: test the accessible switch through its visible label or adjust the control's hit target, then rerun this browser regression.
- Resolved 2026-10-07: inspection at `fd599225` confirms the browser regression now clicks the visible preference label instead of calling `.check()` on the hidden checkbox. Browser execution is recorded separately from this code verification.

### Stale Scribe stylesheet cache key assertion — 2026-09-24

- Current status: Resolved as recorded below.

- Discovery commit: `118ed37`.
- Location: `tests/test_workspace_scribe_layout_contract.py`, `test_scribe_mobile_dictation_modal_owns_the_top_layer_and_safe_viewport`.
- Assertion: `assert 'transcribe.css?v=20260911-note-regeneration-layer-fix' in read("app/templates/transcribe/_head_assets.html")`.
- Observed problem: the template at HEAD uses `transcribe.css?v=20260920-session-sidebar-timestamp-skeleton`, so this unrelated contract test fails despite no change to that stylesheet link in the recording-lock work.
- Impact: the broader workspace layout contract run reports a failure unrelated to the behavior under change.
- Not fixed during the recording navigation lock change because stylesheet cache-key maintenance was outside its scope.
- Resolved by updating the assertion to match the maintained asset key in `tests/test_workspace_scribe_layout_contract.py`.

### SPLIT-SOURCE-ONLY-MATERIALIZATION — 2026-09-26

- Current status: Resolved as recorded below.

- Discovery commit: `118ed375caefb73e42617412b00a8e001622602c` (inspected current working tree).
- Location: `app/services/consultation_split_partial.py:167-169`, `_materialize_available_split_notes_locked`.
- Exact excerpt: `transcript_version_id=batch.analysis.transcript_version_id`.
- Observed problem: Working-note-only and dictation-only analyses intentionally have a null transcript version. Confirmation binds an encrypted empty version to the batch instead. Ordinary split generation uses that materialization version, but Keep Available and verification finalization use the analysis version through this shared materializer.
- Likely impact: inserting a source-only child document violates the non-null `GeneratedDocument.transcript_version_id` constraint and rolls back materialization. Established by static tracing; not reproduced in a test during this inspection.
- Resolved by `consultation_split_materialization.require_batch_materialization_version`, used before all split document materialization. Verification now terminalizes a missing or wrong-root binding with existing failed/unchecked states after settling or cancelling its attempt, instead of rolling back and retrying finalization. Working-note-only and dictation-only Keep Available and verification-finalization regressions prove the confirmation-bound version is used.

### SPLIT-INTENT-PREFERENCE-OPTOUT-VISIBILITY — 2026-10-06

- Current status: Resolved as recorded below.

- Discovery commit: unavailable (working tree review).
- Location: `app/services/consultation_split_gates.py`, `analysis_split_enabled` and `intent_split_enabled`; `app/services/consultation_split_api.py`, `read_workspace_split_analysis`/`read_workspace_split_intent`; `app/services/consultation_split_drafts.py`, `read_split_draft`; `app/services/consultation_split_confirmation.py`, `confirm_split_draft`.
- Observed problem: an already accepted automatic-preference intent continues server-side after the owner opts out, but current analysis/draft projection and explicit confirmation still use the existing preference gates. With the automatic preference off, `analysis_split_enabled` permits only an accepted manual-review intent; otherwise workspace analysis is omitted, an active fresh draft can be projected stale, and `intent_split_enabled` rejects confirmation.
- Likely impact: a ready draft created by durable server progress can be hidden/unreviewable after opt-out while the deployment gate remains available. This is an existing gate boundary, separate from Create navigation durability.
- Not changed during durable Create continuation because this task preserves current preference/deployment authorization for workspace and clinician review actions; broadening accepted-work authorization needs an explicit product/security decision.
- Follow-up: decide whether an exact accepted intent should retain workspace visibility and clinician confirmation authority after preference opt-out, then update the gate helpers and focused authorization/workspace tests together.
- Resolved 2026-10-07 under the supplied plan: preference opt-out blocks new unmarked automatic intents but preserves the exact accepted intent through analysis, draft review, confirmation, and batch access. Deployment availability, owner/team scope, source freshness, and retention still apply. A real-runtime regression reproduces the old rejection and verifies the accepted-work flow after opt-out; focused progress, runtime, and Multiple problems tests pass.

### TRANSCRIPT-COMBINED-PATCH-DROPS-EARLY-FIELDS — 2026-10-07

- Current status: Resolved as recorded below.

- Discovery commit: `88e487718e627643d9c3f8a99609f137bacd0a54`.
- Location: `app/services/transcripts.py`, `update_transcript`.
- Exact excerpt: title and ingestion-mode assignments occur before `_lock_split_source_writer_transcript(...)`, whose query reloads the same row with `populate_existing=True` while autoflush is disabled.
- Observed problem: when one transcript PATCH combines `structured_context_json` with a title or ingestion-mode change, the source-writer reload replaces those earlier in-memory assignments with database values before commit.
- Likely impact: the request can succeed while silently dropping its title or ingestion-mode update. The Multiple problems marker had the same reload ordering risk, but this change now assigns that new marker after the reload and covers the combined marker/structured-context case.
- Not fixed during the Multiple problems toggle because title and ingestion-mode update ordering is older, unrelated behavior and broadening the patch would expand scope beyond the new marker contract.
- Follow-up: preserve validated title and ingestion-mode changes across the source-writer reload, then add focused combined-PATCH regressions for each field.
- Resolved 2026-10-07: `update_transcript` acquires the source-writer lock before applying metadata for structured-context requests. Independent title and ingestion-mode PATCH regressions reproduce the dropped fields on the old ordering and pass with the fix; metadata-only requests retain the lighter owner lookup.

### SPLIT-VERIFIER-PREFERENCE-RECOVERY-EXPECTATION — 2026-10-08

- Current status: Resolved as recorded below.

- Discovery commit: `23fa441591064d7d2b4d47c8acfd51d86992a4cf`.
- Location: `tests/test_consultation_split_generation_runtime.py`, preference cases in `test_submitted_verifier_finishes_after_gate_flip_without_reopening_manual_actions`, `test_submitted_verifier_fail_open_materializes_originals_after_gate_flip`, and `test_verifier_deadline_wins_over_late_provider_response_without_a_second_call_or_settlement`; `app/services/consultation_split_gates.py`, `batch_split_enabled`.
- Exact expectation: `assert raised.value.code == "consultation_split_disabled"` (or matching the disabled message).
- Observed problem: four preference-opt-out cases expect disabled recovery actions. The accepted confirmed intent keeps its exact batch eligible after preference opt-out; completed/actively verifying batches instead reject recovery with `consultation_split_batch_unavailable`. Deployment-off cases still reject as disabled.
- Evidence: runtime file run reported 57 passed and these four failures during diagnostics work. A separate isolated pytest run loaded the unchanged commit's generation-runtime source without modifying the checkout or test isolation, reproducing the same four failures; all four corresponding deployment cases passed.
- Likely impact: focused runtime verification reports failures unrelated to provider diagnostics; the tests appear stale against the accepted-intent opt-out contract implemented on 7 October.
- Not fixed during split diagnostics because changing recovery authorization or its test contract would expand scope beyond messages and safe operational metadata.
- Follow-up: reconcile these assertions with the maintained accepted-intent/batch opt-out contract, retaining deployment-off, owner, retention, and exact-binding protection tests.

- Resolution — 2026-10-14, working tree based on `c712ba5b9db00e5008951bc3ff585a78e711c93f`: corrected only the four stale preference error expectations to `consultation_split_batch_unavailable`. The unchanged committed `batch_split_enabled` and maintained split documentation both preserve exact confirmed-intent eligibility after opt-out. Deployment-off assertions and verifier success/fail-open/late-result protections remain unchanged. Focused verification is reported separately.

### SPLIT-CREATE-PENDING-TRANSCRIPTION — 2026-10-08

- Current status: Superseded or reconciled with current source; see dated notes.
- Reconciliation/resolution (2026-10-19): _require_source_ready_for_analysis checks before preparation and again under the source lock. The 16 October reassessment supersedes the original missing-readiness-guard claim. A unified server-side waiting workflow remains a separate product design, not an open snapshot-safety defect.

- Discovery commit: unavailable (consultation-flow review of current working tree).
- Location: `app/services/consultation_split_intents.py`, `create_or_replay_consultation_split_intent`; `app/services/consultation_split_sources.py`, `prepare_source_bound_consultation_split_analysis`; contrast `app/services/templates.py`, `_ensure_generation_can_be_queued` and transcript-wait handling.
- Exact call: `prepared: PreparedConsultationSplitAnalysis = prepare_source_bound_consultation_split_analysis(db, actor, transcript_id=transcript_id)`.
- Observed problem: ordinary generation records pending ingestion and waits before resolving its transcript input. Split Create prepares a source snapshot immediately without an equivalent pending-ingestion wait. The browser permits Create while transcript text is queued/processing, so splitting can analyse an incomplete draft or only currently saved Working note/dictation.
- Likely impact: later transcript arrivals can invalidate analysis/continuation as stale and consume unnecessary analysis work. A clinician can see a split failure after a normal stop-and-Create action. Exact output/timing consequences require a synthetic regression; static inspection alone does not prove that every ordering rejects incomplete input.
- Evidence: read-only service/UI trace; no timing reproduction, provider calls, or live clinical data used.
- Not fixed during consultation-flow analysis because the task requests assessment of current versus ideal behavior. Binding an accepted split intent to pending ingestion and changing when its source snapshot is created needs a coherent lifecycle design and focused tests.
- Follow-up: define a shared server-owned readiness contract for ordinary and split Create, show a persistent waiting-for-transcription stage, and test pending/failing/final live chunks, whole-file ingestion, Working-note/dictation-only sources, duplicate delivery, source freshness, and one-click continuation.

### STALE-ACCESSIBLE-GUIDE-UI-EXPECTATIONS — 2026-10-18

- Current status: Resolved as recorded below.
- Reconciliation/resolution (2026-10-19): The current baseline already uses the Scribe guide wording and accessible-guide asset expectations. This pass updated app-module cache expectations for deletion confirmation; focused UI checks pass.
- Reverification (2026-10-27): The workspace-shell check confirms the Scribe guide markup and the mobile asset check confirms the current stylesheet cache key. Both passed in the six-test transcribe assertion subset after updating cache-key expectations for the live-dictation assets.

- Discovery commit: `717d2698a2ac268f830d99548068202a5de3f780`.
- Locations: `tests/test_split_review_js.py::test_split_review_workspace_accessibility_hooks_and_cachebusters`; `tests/test_admin_ui.py::test_transcribe_page_contains_accessible_ui_controls`; source `app/templates/transcribe/_head_assets.html` and `app/templates/transcribe/_shell_extras.html`.
- Evidence: the test expects `transcribe.css?v=20261012-review-problems-layout`, while the template serves `transcribe.css?v=20261016-accessible-manual-guide`. The UI test expects `Quick guide`, while the template now uses `<div class="tour-overlay__eyebrow">Scribe guide</div>` in an accessible dialog.
- Impact: these two UI expectation failures remain in the broader focused run after dictation work; the source changes are part of the existing accessible-guide worktree diff, not the dictation implementation.
- Not changed here: changing guide markup or stylesheet versions, or rewriting unrelated UI expectations, would exceed this task’s scope. The dictation-related cache-key assertions were updated separately.
- Follow-up: have the accessible-guide change owner reconcile the two expectations with the final guide design and rerun the focused UI tests.

### QUICK-ACTION-UTILITY-ATTRIBUTE-ORDER — 2026-10-18

- Current status: Resolved as recorded below.
- Reconciliation/resolution (2026-10-19): The current baseline already checks class, role and accessible label independently. Focused settings tests pass; no further markup change was needed.

- Discovery commit: `717d269` (working tree includes the current Help page work).
- Location: `tests/test_settings_quick_actions.py::test_quick_action_io_configures_shared_controller_and_wires_help_adapter`; `app/templates/settings/_quick_action_library.html`, import/export utility group.
- Exact assertion: `class="template-library-utilities" aria-label="Quick action import and export"`.
- Observed problem: the current template emits `<div class="template-library-utilities" role="group" aria-label="Quick action import and export">`. The test's exact substring fails because the valid `role="group"` attribute appears between the class and label.
- Likely impact: the focused settings test fails despite the accessible group label being present; this assertion does not indicate a user-facing defect.
- Not fixed now: Help work does not change Quick Actions markup, and changing unrelated code would widen scope.
- Follow-up: make the test assert the class, role, and accessible label independently, then rerun `tests/test_settings_quick_actions.py`.

### DSPT-RETRY-UI-EXPIRY — 2026-10-14

- Current status: Resolved as recorded below.

- Discovery commit: `c712ba5`.
- Location: `app/web/transcribe_workspace.py`, `_earliest_retryable_ingestion_job`.
- Previous selection returned `db.scalar(statement.limit(1))` without checking source expiry. A retained expired failed upload appeared retryable, suppressing API/workspace expiry guidance.
- Fixed during resilience validation because the correction is bounded and uses the established `ingestion_retry_source_expired` service. The selected earliest failed source now returns no retry when expired; live ingestion never skips that gap to offer a later job.
- Validation: focused DSPT API/workspace expiry regression passed; existing live-gap retry coverage remains required. No retention or content-access contract changed.

### SPLIT-QUOTA-LIFECYCLE-REFRESH-ORDER — 2026-10-19

- Current status: Resolved as recorded below.

- Discovery commit: `717d269` (review of the pending changes on this baseline).
- Location: `app/services/quota_lifecycle.py`, `process_quota_lifecycle`.
- Exact sequence: `terminal = _terminalize_locked_attempts(expired, now)`; `db.flush()`; `changed = terminal.cancelled + terminal.settled_unknown + _reconcile_sources(db, expired, now)`.
- Observed problem: quota cleanup marked expired reservations cancelled or submitted attempts settled in memory, then reconciled their split source. Verification fail-open reacquired execution and attempt rows with `populate_existing=True`, which could reload the old active attempt state before the terminal change flushed.
- Likely impact: UUID ordering could leave an expired verification reservation active or lose its conservative settlement, producing incorrect quota availability/accounting.
- Resolution: flush terminal attempt changes before reconciling source rows. A deterministic verification-first expired-reservation regression failed before the flush and passed afterward; a verification-first submitted-timeout regression verifies persisted unknown settlement.
- Validation: both focused lifecycle regressions passed after the change; the broader focused suite is recorded in [regression review](docs/reviews/regression-review-2026-10-19.md).

### LIVE-STREAM-FAILURE-DIAGNOSTIC-COLLAPSE — 2026-10-20

- Current status: Corrected locally; deployment and the original incident's trigger remain unverified.
- Discovery commit: unavailable (live incident investigation; deployed route and streaming-service fingerprints matched the inspected local files).
- Locations: `app/routes/deepgram_live.py::deepgram_live`, `_Relay.checkpoint`; `app/services/transcript_streaming.py::_SAFE_STREAM_FAILURE_CODES`.
- Observed problem: typed provider failures and browser disconnects become `stream_failed`. The route also passes result-validation and finalization codes absent from the lifecycle allowlist, which normalizes them to `stream_failed`. A checkpoint `asyncio.wait_for` timeout bypasses the route's specific finalization code. The fixed close message uses INFO, which the current default web-server logging does not emit.
- Impact: durable failure metadata and available operational logs cannot distinguish several materially different causes of an interrupted recording. Successful recovery of the accepted interval does not establish why capture stopped or whether unsent browser audio existed.
- Follow-up: preserve the established safe lifecycle categories, retain only allowlisted provider categories in visible content-free failure logs, and test synthetic route failures and finalization timeouts. These changes cannot retrospectively recover the original exception category.
- Resolution: the route now maps failures to existing lifecycle categories and emits established-stream failures at warning level with only bounded codes. Final-drain wait expiry uses the existing finalization error. The owning-user, retained-source, explicit-retry, quota and cleanup contracts are unchanged. Luna implemented and tested the correction; the parent reviewed it. Synthetic regressions failed before the fix and passed afterward; the complete focused route, lifecycle and frontend protocol files passed 45 tests. The API authorization audit passed, with its existing HTTP route manifest unchanged. Tests and audit ran sequentially and needed the supported elevated retry for local test-service access. Maintained-document validation passed for 54 files. The original recording failure was not reproduced.

### LIVE-STREAM-UNSENT-FAILURE-TAIL — 2026-10-20

- Current status: Open; architectural recovery decision required before changing the contract.
- Discovery commit: unavailable (live incident investigation).
- Locations: `app/static/js/transcribe/deepgramLiveStream.js::_queueAudio`, `_fail`, `pendingRecoverySamples`; `app/static/js/transcribe/media.js::failDeepgramCapture`; `app/services/transcript_streaming.py::_recovery_wav_matches_deliveries`.
- Exact boundary: `if (this.outboundSamples > RING_LIMIT_SAMPLES)` stops the stream after the unsent queue exceeds five seconds. Recovery accepts only the exact server-authorized interval and verifies persisted batch digests; unsent outbound and pre-roll are excluded.
- Observed problem: stopping on a connection/backpressure failure does not provide a recovery path for captured audio that the server never authorized. Local failure callbacks can also lack the server interval needed to expose the exact pending recovery buffer. The media integration releases its active stream reference after failure.
- Likely impact: server-accepted audio can be recovered while additional browser-captured audio remains unavailable. The inspected incident recovered its entire accepted interval; the available metadata cannot establish whether an unsent tail existed in that incident.
- Not fixed now: treating an unacknowledged captured tail as a new source, or persisting capture through refresh/process loss, requires an explicit recovery/retention/encryption design. Extending the existing digest-bound recovery interval would weaken its exact-source contract.
- Follow-up: design a bounded owner-only recovery path for unsent capture, define retention and browser-loss limits, and test stalled acknowledgements, network loss, capture stop, source sequencing, duplicate delivery, quota, encryption, and transcript-root cleanup with synthetic audio. Keep current exact-interval recovery and explicit user replay intact.

### GEMINI-LATE-CAPACITY-RETRY-BUDGET — 2026-10-20

- Current status: Open; provider retry-admission policy decision deferred.
- Discovery commit: unavailable (synthetic reproduction during live HTTP 504 investigation).
- Location: `app/services/llm_execution.py`, `bounded_request`.
- Exact guard: `if waited + wait > 30 or wait >= remaining: wait = 0.0`.
- Observed behavior: after a long-running request returns an explicitly temporary HTTP 429, the retry loop checks that the selected wait fits the remaining shared stage budget but does not reserve a minimum duration for the next request. Each Gemini attempt builds a fresh client, so its request timeout reflects the reduced remaining budget.
- Likely impact: a late retry can start with only a few seconds remaining and return an upstream HTTP 504 before the local stage deadline. A synthetic full-wrapper reproduction used durations of 83.954 seconds and 3.151 seconds with a 1.650-second retry wait; the second client's SDK timeout was 4,396 ms. It emitted a first `rate_limited`/HTTP 429 event and then a terminal `timeout`/HTTP 504 event, with no third request. This demonstrates the timing path, not the cause of any individual provider 504.
- Not fixed now: choosing a minimum residual budget changes which explicitly temporary 429s are eligible for replay; extending or refreshing a stage deadline would change provider-execution bounds. These are provider execution policy decisions. The current stage deadline and rule against automatically retrying 504 remain in force.
- Follow-up: decide whether to require a minimum remaining budget before retry admission, then add deterministic bounded-request and Gemini-wrapper tests for late 429, shrinking per-attempt client timeouts, terminal upstream 504, and local timeout with null HTTP status. Keep tests synthetic and verify no 504 replay.
- Reassessment — 2026-10-20: safe metadata from a separate ordinary Gemini generation showed a first and only transport event with upstream HTTP 504 after 87.266 seconds, no preceding 429 or retry, inside the original 150-second durable attempt. This means the late-retry guard alone would not prevent that incident; it does not establish the provider-side cause. The 90-second Gemini generation-stage cap is separate from the 150-second ordinary attempt deadline. A Gemini-only 120-second generation cap is a bounded trial proposal requiring approval, not a confirmed fix. The checker would remain capped at 30 seconds within the same 150-second attempt and may have less than 30 seconds after generation and intervening work. Split generation has its own 90-second durable deadline, which would also need alignment if that path were included. No runtime policy was changed. The deployment's combined worker concurrency is 1, so any deadline increase also increases the time one worker may remain occupied; host inspection confirmed one CPU and approximately 2 GB RAM. Capacity must be assessed before enabling additional isolated workers.
- Monitoring reassessment — 2026-10-20: read-only Google Cloud Monitoring for the EU Gemini 3.8 Flash PublisherModel resource, from 09:00 UTC, showed 19 invocations: 13 HTTP 200, 2 HTTP 429, and 4 HTTP 504. All reported requests were `shared` / `standard`; no `dedicated` requests were observed. The `error_category` label was absent. Twelve model-latency observations had a mean of 30.277 seconds, but their latency-kind label was absent; no first-token latency series was returned. The fixed-quota `serviceruntime.googleapis.com/quota/exceeded` query returned no time series. That absence cannot rule out a fixed quota or dynamic shared-capacity contention. The project-metadata lookup returned 403, preventing completion of the configured-quota check; the billing tier and account/org limits were not established. An initial Monitoring read failed because of an OAuth scope mismatch; retry with the exact `monitoring.read` scope succeeded, so this was not evidence of an IAM denial. No project identifiers, provider payloads, prompts, responses, or credentials were recorded here.

### LIVE-HOST-WORKER-CAPACITY-HEADROOM — 2026-10-20

- Current status: Open; host sizing and workload-isolation assessment pending.
- Discovery commit: unavailable (read-only live host capacity inspection).
- Location: deployment worker topology and host capacity; source configuration in `app/celery_app.py` routes control, generation, and ingestion queues, but does not establish the live process topology.
- Observed state: the live host is an AWS `t2.small` with one vCPU and 2 GiB RAM. The combined worker concurrency is one. At inspection, approximately 514 MiB RAM was available and 1,038 MiB of swap was in use. CPU credit balance remained positive but fell from 11.90 to 9.63 over about 25 minutes. AWS documents the `t2.small` baseline as 20% CPU utilization and 12 credits earned per hour ([AWS burstable performance concepts](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/burstable-credits-baseline-concepts.html)).
- Likely impact: one combined worker limits parallelism, while adding worker processes on the same small host could increase memory pressure and allow generation backlog to starve control or ingestion work. The observed positive credit balance does not establish CPU-credit throttling, and these host observations do not establish that AWS capacity caused any Google HTTP 429 or 504.
- Not changed: no worker count, queue routing, host size, deadline, or provider policy was modified. Increasing worker count without sizing and load testing could worsen memory pressure and queue starvation.
- Follow-up: assess a host with at least 2 vCPUs and 8 GiB RAM as an initial sizing proposal, not a benchmark guarantee. After load testing, consider isolated pools with one control worker, two generation workers, and one ingestion worker. Profile resident memory, queue-delay p95, CPU utilization, and CPU-credit balance under representative synthetic load before changing deployment configuration.

### SCRIBE-SIDEBAR-RESIZE-CONTRACT-DRIFT — 2026-10-21

- Current status: Open; maintained UI contract needs reconciliation.
- Discovery commit: unavailable.
- Locations: `tests/test_workspace_navigation.py::test_workspace_scribe_and_personal_routes_load`; unchanged `app/templates/workspace/_sidebar.html` lines 11–13.
- Exact test assertion: `assert 'data-sidebar-step=' not in scribe.text, "Scribe must not render non-Scribe sidebar width buttons"`.
- Exact template snippet: `<button type="button" data-sidebar-full data-sidebar-step="-16" ...>` and `<button type="button" data-sidebar-full data-sidebar-step="16" ...>`.
- Observed problem: the focused workspace navigation test fails because the current shared sidebar template renders resize controls in Scribe, contrary to the test assertion. The template was not changed for the example-consultation work.
- Likely impact: the test may encode a stale expectation or the controls may need route-specific rendering; current UI behavior and maintained test contract disagree.
- Not fixed now: this is unrelated to the example consultation and requires reconciling the current Scribe resize controls with the test/accessibility contract.
- Follow-up: confirm whether Scribe should retain the width controls, then update the route-specific template or the focused test and its accessibility coverage to match.

### VAD-INACTIVITY-TEST-IMPORT-REPLACEMENT — 2026-10-22

- Current status: Open; the test harness does not reach its assertions.
- Discovery commit: `7a1baec560c9c8cf4f357006dba28a46ed7bd921`.
- Location: `tests/test_vad_inactivity_js.py::test_vad_inactivity_prompt_dismiss_rearms_and_resets`; source `app/static/js/transcribe/media.js`.
- Exact obsolete replacement: `.replace("import { csrfFetch } from '../csrf.js';", ...)`.
- Current source import: `import { csrfFetch, csrfToken } from '../csrf.js';`.
- Observed problem: the harness leaves the ES module import in the source before passing it to `vm.runInContext`, which raises `SyntaxError: Cannot use import statement outside a module` before the test exercises VAD inactivity behavior.
- Likely impact: this test cannot currently validate inactivity prompt dismissal or reset behavior; the failure does not establish a runtime defect.
- Not fixed now: repairing this unrelated harness is outside the live startup change.
- Follow-up: update the source replacement to match the current imports, then rerun `tests/test_vad_inactivity_js.py` and confirm its behavioral assertions execute.
