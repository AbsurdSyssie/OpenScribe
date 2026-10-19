# Note generation unsafe-failure fixes

Status: implemented and reviewed; parent final signoff complete, 16 October 2026. Authorized by the user's request to prioritize unsafe failures from the pipeline review, delegate fixes and perform final review. Implementation and final verification are recorded below.

## Outcome and boundaries

Prevent known-incomplete provider responses from becoming ready clinical drafts, and reject ambiguous duplicate-key provider JSON before output or checker patches are accepted. The dated assessment is [the pipeline review](../docs/reviews/note-generation-pipeline-review-2026-10-16.md); executable source and focused tests take precedence.

The reviewed working tree contains unrelated local changes. Preserve them. No API route, schema, migration, ownership, encryption, redaction, retention, provider-selection, outbox or quota redesign is required. Keep submitted-attempt accounting conservative and do not replay ambiguous calls. Keep optional checker failure fail-open to the already validated encrypted draft. Preserve valid response shapes and optional structured sections.

## Priorities and delegation

1. Luna transport owner: `app/services/llm_adapters/runtime.py` and adapter regression tests. Reject OpenAI-compatible explicit non-completion reasons; classify length as existing `llm_generation_truncated`. Require Ollama's `done: true` terminal frame and inspect its reason. Preserve legacy missing reasons where a complete response/terminal frame otherwise exists. Never echo arbitrary provider reason strings or response content. Cover chunk boundaries, terminal frame without final newline, normal stop, missing reason, incomplete/length output, and cleanup. Do not change request caps or retry policy.
2. Luna JSON owner: ordinary note/checker parsing in `app/services/templates.py`, split verification parser and focused JSON tests. Reject duplicate object keys at every depth with a content-free exception, following the established split parser pattern. Preserve ordinary fenced/balanced-object compatibility, existing safe invalid-output codes, and checker fallback. Avoid broad refactoring of existing split contracts.
3. Luna lifecycle/communication owner: focused worker regression tests, failure presentation, and closest maintained documentation. Prove incomplete output cannot become ready and that submitted attempts settle without a replay; prove invalid checker output leaves the validated original as unchecked. Ensure existing safe presentation can describe truncation/incomplete responses. Update provider/security documentation only for implemented changes.
4. Parent: integrate changes; run tests sequentially against shared test infrastructure; review source, test adequacy, sensitive error handling and accounting; resolve issue-log entries only when evidence supports it.

Write ownership is partitioned. Agents must coordinate before changing another owner's files. Only the parent runs pytest by default; agents supply selectors and may use isolated synthetic probes. Parent may hand off the test lock explicitly.

## Acceptance and verification

- Adapter-level regression signals reproduce the prior unsafe acceptance before the fix and reject it afterward; normal completions and legacy omitted reason remain compatible.
- Plain-text follow-up/Quick Action and parseable Template JSON cannot bypass rejection. No unvalidated output becomes ready; duplicate delivery cannot submit again; reservations/attempts reach the established terminal state.
- Duplicate top-level and nested fields fail for ordinary freeform/structured notes, checker status/edit fields, and split-verifier topic UUIDs. Error metadata and logs contain no source, response, credentials or arbitrary reason text.
- Existing split analysis/generation duplicate-key rejection, checker exact-patch validation, deadline/transport tests and provider response bounds continue to pass.
- Use `.venv/bin/pytest` with `APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app`; obey documented sandbox/service retry and test-lock rules. No live-provider calls or real clinical data.
- Run maintained-document validation and whitespace/diff review. Final review must distinguish tested behavior from untested deployment assumptions.

## Deferred decisions

Keep `NOTE-OLLAMA-OUTPUT-RESERVATION-PARITY`, unexpected split-worker telemetry, all-empty ordinary structured output, adapter byte-limit parity, and provider-specific length policy as explicit follow-ups. They need their own bounded contract/operational work and are not prerequisites for these safety fixes.

## Implementation record

Implementation was reviewed against baseline commit `717d2698a2ac268f830d99548068202a5de3f780`; no commit was made. Changes were limited to these areas:

- `app/services/llm_adapters/runtime.py`: inspect OpenAI-compatible finish reasons, classify `length` as `llm_generation_truncated`, reject other explicit non-completion reasons with a content-free error, require Ollama `done: true`, and inspect its optional `done_reason`. An absent OpenAI reason or Ollama reason remains accepted for compatibility when the returned completion/terminal frame otherwise exists.
- `app/services/templates.py`: reject duplicate JSON keys in freeform/structured note output and hallucination-check output; map checker parser recursion overflow to the existing safe `invalid_json` response so the checker retains its fail-open/unchecked behavior for the already validated draft.
- `app/services/consultation_split_verification.py` and new `app/services/json_validation.py`: reject duplicate keys at every object depth in bundled verifier output using a shared helper with no source key/value in its exception.
- `app/services/generation_presentation.py` recognizes the existing truncation/bad-response codes in the safe validation category. Current behavior is documented in `docs/llm-providers.md` and `docs/hallucination-check-design.md`.
- Focused regressions were added/updated in `tests/test_llm_runtime.py`, `tests/test_generation_json_validation.py`, `tests/test_generation_presentation.py`, `tests/test_generation_unsafe_failures.py`, and `tests/test_generation_ollama_unsafe.py`. Existing split-verification tests were run without modification.

Delegated boundaries remained separate: transport implementation/tests, JSON parser implementation/tests, and worker lifecycle/failure-presentation coverage. The parent integrated and reviewed the slices. Ownership, encryption, redaction, transcript lifecycle, provider selection, schema, routes, configuration, outbox semantics, quota policy, retry policy, and checker fail-open behavior did not change. No schema, route, or configuration edits were made.

## Verification record

The parent ran these focused pytest groups sequentially with `APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app` and `.venv/bin/pytest -q`:

| Files | Result |
| --- | --- |
| `tests/test_llm_runtime.py`, `tests/test_provider_response_bounds.py` | 57 passed |
| `tests/test_generation_json_validation.py`, `tests/test_consultation_split_verification.py`, `tests/test_consultation_split_generation.py`, `tests/test_consultation_split_analysis.py` | 99 passed |
| `tests/test_llm_execution.py`, `tests/test_consultation_split_generation_runtime.py`, `tests/test_generated_document_regeneration.py`, `tests/test_generation_presentation.py`, `tests/test_gemini_enterprise_llm.py`, `tests/test_task_outbox.py`, `tests/test_quota_lifecycle.py` | 178 passed |
| `tests/test_generation_unsafe_failures.py`, `tests/test_generation_ollama_unsafe.py`, `tests/test_generation_json_validation.py`, `tests/test_generation_presentation.py` | 32 passed |
| `tests/test_generation_unsafe_failures.py`, `tests/test_api.py` selected controls with `-k 'explicit_truncation or invalid_checker_response_keeps or parse_generated_note_json_coerces or template_generation_supports or structured_emis_template_generation_persists_sections or structured_hallucination_check or process_generated_document_fails_on_invalid or generation_invalid_json_and_provider_errors_settle_authoritative_attempt'` | 16 passed, 470 deselected, 1 Starlette/httpx deprecation warning |

The groups overlap; do not add their counts as unique tests. One initial checker expectation failed because the renderer includes the `Problem` section label; the fixture was corrected to that established rendering contract, with no runtime workaround. An initial sandbox precollection attempt could not connect to local PostgreSQL; test-service host/port were checked without printing secrets, then the identical command succeeded through the supported elevated retry. Synthetic probes reproduced the incomplete-response and duplicate-key acceptances before the fix and confirmed rejection afterward. The checker deep-JSON probe confirms recursion maps to the safe invalid-response path. No live provider or clinical data was used.

Maintained-document validation passed with 45 files. Scoped diff checks and Python compilation passed for changed/new runtime and test modules. Parent source review and independent lifecycle, JSON-parser, and transport reviews found no blockers. No full suite, browser, live-provider, or deployment test was run. Final signoff is complete. Deferred cap parity, split telemetry, empty ordinary structured output policy, response-byte parity, and provider-specific length policy remain follow-up decisions.
