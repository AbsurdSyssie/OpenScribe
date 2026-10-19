# Generation stages: logging, efficiency and token review

Status: dated source assessment and remediation record, 16 October 2026. This record covers the local working tree, including earlier uncommitted fixes. It does not supersede maintained runtime contracts or activate older plans.

Three Luna agents reviewed STT/source preparation, split workflows, and ordinary generation/checking. The parent reviewed the findings, selected bounded fixes, and checked integration. All examples and regression inputs use synthetic data. No live provider calls, production logs, latency benchmark, or measured token-saving experiment formed part of this review.

## Stage assessment

| Stage | Error logging and failure handling | Efficiency and token/cost behavior |
| --- | --- | --- |
| Audio preparation and STT | Transport errors use safe metadata. Added ingestion stage diagnostics for source read, normalization, duration validation, credential resolution, dispatch and result persistence. Task failures use a content-free exception at the Celery boundary. | Audio quotas apply, rather than LLM tokens. Normalization precedes credential resolution and the dispatch claim. Existing claims prevent duplicate provider dispatch; established transient retries can repeat billable audio work. |
| Required redaction and manual PII protection | Required provider boundaries fail closed. Preview logs contain IDs and controlled errors. | Successful transcript-version redaction runs are reused. Failed runs may be attempted again on later preparation; preserving that recovery behavior avoids introducing a persistent failure cache. External redaction cost depends on the configured provider. |
| Optional clinical NLP | Existing logs contain provider/run/version IDs and bounded failure metadata. The saved source and provider eligibility boundary remain authoritative. | Successful enrichment is reused while provider configuration remains current. Later ensure calls can retry failed enrichment. This review found no basis for removing that recovery behavior. |
| Optional template suggestion | Existing lifecycle events cover preparation, dispatch and completion. Task boundary failures receive the same content-free handling as ingestion and generation. | One queued suggestion per transcript; excerpt threshold 1,200 characters and maximum 4,500. Nominal output cap is 220 tokens; Gemini uses its existing 30,000-token provider ceiling. Fixed queue reservation for the actual cap and dispatch expansion for the final redacted input; pre-submit rejection cannot cancel a concurrent winner. |
| Split analysis | Safe execution/usage metadata and split-worker failure diagnostics already exist. Responses undergo bounded schema and identity validation. | One analysis call per winning claim. Nominal output cap 512; reservation already uses the actual adapter cap, including Gemini's 30,000. Sources are bounded at 100,000 characters each and prompt at 256,000. |
| Ordinary Template generation | Added generator/stage categories to operational usage logs. Local validation and existing completion checks reject invalid or incomplete results. | Saved length caps are 800/1,600/3,200 tokens. Ordinary dispatch reserves against the built request; Gemini retains its fixed 30,000 cap. Saved source/request snapshots prevent later edits from changing submitted work. |
| Follow-up and Quick Action | Share ordinary transport, completion checks, usage logging and sanitized task failure handling. | OpenAI-compatible requests use the default cap and Gemini uses 30,000. Ollama still omits `num_predict` when no explicit cap is supplied; the finite reservation does not establish a matching provider ceiling. This remains an explicit contract decision. |
| Bundled split generation | Existing encrypted snapshots, strict topic/mode/section validation and safe failure codes remain. | One bundle shares source/context across topics. Nominal cap sums 800/1,600/3,200 per requested note plus 32 title tokens. Fixed reservations to use the actual adapter request cap, including Gemini's 30,000. |
| Targeted split recovery | Retains accepted siblings and never replays uncertain work without a durable response. | One targeted recovery execution for failed topics. Fixed the same reservation mismatch. Preparation still builds the initial payload before narrowing it; this repeats bounded local serialization, not provider work. Profiling should precede a refactor. |
| Ordinary hallucination check | Added separate checker start/outcome/skip operational events. Invalid output retains the original validated draft as unchecked under the existing policy. | At most two checker attempts, with the second for validation/patch failure; independent quota reservations use the built request. Nominal output cap follows note length; GPT-OSS requests use at least 4,000 and low reasoning, while Gemini uses 30,000 per call. A validation retry resubmits the bounded source/note context. Both calls share a 30-second budget within the main submitted-attempt deadline. |
| Bundled split verification | Existing exact-topic-set, all-or-none patch validation and unchecked fallback remain. Fixed a Gemini queue rejection where the secret-name scan mistook the canonical JSON Schema property `section_key` for a credential. Only its exact non-secret string definition inside schema properties is exempt; credential fields still fail the scan. | One independent bundle call after recovery finishes. Nominal cap is `max(512, min(8192, topics * 1024))`; fixed reservation to use the actual adapter request, including Gemini's 30,000, and the established byte-based input estimator. Insufficient checker quota still leaves drafts unchecked. |
| Validation, encryption and materialization | Provider output remains validated before display/persistence as ready content. Added a bounded split-finalization warning so errors consumed by runtime terminalization still leave stage evidence. Owner encryption and transcript-root retention/deletion still apply. | No provider call is needed to materialize validated split survivors. Duplicate delivery and materialization retain existing database-backed idempotency. Checker diagnostics use immutable identifiers rather than refreshing ORM state after transactions. |

Output caps describe submitted maximum capacity, not measured consumption. Token reservations use conservative estimates; reported usage and established unknown-outcome settlement remain authoritative. SDK retries remain disabled; shared LLM transport retries only classified temporary-capacity 429 responses within its bounded request/time budget.

## Changed implementation and documentation

- Ingestion and suggestions: [transcripts.py](../../app/services/transcripts.py), [template_suggestions.py](../../app/services/template_suggestions.py), and [tasks.py](../../app/tasks.py).
- Ordinary generation/checker diagnostics: [templates.py](../../app/services/templates.py).
- Split request preparation and reservations: [generation](../../app/services/consultation_split_generation.py), [initial queue](../../app/services/consultation_split_generation_queue.py), [recovery](../../app/services/consultation_split_recovery.py), [verification](../../app/services/consultation_split_verification.py), and [verification queue](../../app/services/consultation_split_verification_queue.py).
- Split request secret validation and finalization diagnostics: [consultation_splits.py](../../app/services/consultation_splits.py) and [generation runtime](../../app/services/consultation_split_generation_runtime.py).
- Maintained references: [transcript capture](../transcript-capture.md), [LLM providers](../llm-providers.md), [splitting](../consultation-splitting-preference.md), [hallucination check](../hallucination-check-design.md), [testing](../testing.md), and [documentation index](../README.md). Resolved findings and follow-ups appear in [DISCOVERED_ISSUES.md](../../DISCOVERED_ISSUES.md).

The change adds focused regressions and extends existing API, suggestion, split and logging tests. It adds no migration, database field, route, runtime setting or provider call. Ownership, encryption, retention, redaction boundaries, clinician draft review and structured response shapes remain unchanged.

## Remaining decisions and limits

- Choose an explicit Ollama follow-up/Quick Action output-cap contract before changing its preserved request shape or quota policy. See `NOTE-OLLAMA-OUTPUT-RESERVATION-PARITY` in [the issue log](../../DISCOVERED_ISSUES.md).
- OpenAI-compatible and Gemini SDKs can materialize a response before local validation; they lack Ollama's application-level stream byte ceiling. A post-read size check alone would not prove bounded transport memory.
- Gemini's 30,000-token ceiling supports reasoning plus response and is an existing provider contract. Lowering it or shortening clinical prompts requires separate quality and compatibility assessment. These changes align quota accounting and improve diagnosis; they do not claim token savings.
- Deployment log formatting, access controls, retention and SDK debug logging require deployment inspection. Source-level log assertions do not establish those controls.
- Redaction/NLP failure reuse and recovery-payload serialization are follow-up investigations, without a demonstrated need to alter current recovery contracts.
- Targeted recovery reuses the initial topic-aware response schema, which can permit sibling UUIDs that the narrowed payload does not request. Local validation still enforces the requested failed-topic set. Narrowing the provider schema is a possible later efficiency/reliability improvement; this review preserves the established request snapshot contract.

## Verification

The final combined run passed **360 tests** in 40.18 seconds, with one existing Starlette/httpx deprecation warning. All provider boundaries used synthetic mocks. The initial sandbox attempt could not connect to PostgreSQL; sanitized host/port checks showed `PermissionError` for the local PostgreSQL and Redis sockets. The supported elevated run succeeded without changing service addresses, isolation guards or test expectations to bypass infrastructure.

Exact final selection:

```bash
APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app .venv/bin/pytest -q \
  tests/test_llm_runtime.py tests/test_consultation_splits.py \
  tests/test_consultation_split_generation.py tests/test_consultation_split_verification.py \
  tests/test_consultation_split_generation_runtime.py tests/test_consultation_split_analysis.py \
  tests/test_transcript_ingestion_diagnostics.py tests/test_template_suggestions.py \
  tests/test_task_outbox.py tests/test_quota_lifecycle.py \
  tests/test_generation_usage_logging.py tests/test_generation_task_diagnostics.py \
  tests/test_consultation_split_worker_diagnostics.py tests/test_generation_unsafe_failures.py \
  tests/test_generation_ollama_unsafe.py tests/test_generation_json_validation.py \
  tests/test_api.py::test_legacy_transcript_ingestion_task_payload_is_accepted \
  tests/test_api.py::test_process_generated_document_logs_usage_metadata \
  tests/test_api.py::test_structured_hallucination_check_applies_exact_patch_and_records_debug \
  tests/test_api.py::test_structured_hallucination_check_invalid_response_retries_and_settles_each_attempt \
  tests/test_api.py::test_structured_hallucination_check_quota_failure_skips_provider_and_preserves_note
```

Delegated compilation checks and `git diff --check` passed. Maintained-document validation passed with 45 references checked (`.venv/bin/python .github/scripts/check-operational-docs.py`). No full-suite, browser, live-provider, production-log or deployment-capacity run was performed. No routes changed, so the API route manifest and authorization audit were outside this change's verification scope.
