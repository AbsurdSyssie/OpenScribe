# Owner-visible generation trace

**Status:** Implemented locally on 10 November 2026 with a 24-hour retention cap; deployment has not been performed. See delivered-scope and verification notes below.
**Date:** 10 November 2026

## Problem and intended outcome

Generation can involve a primary request, backoff, provider failover, JSON retry or repair, validation, and editor projection. Current persistence can expose the latest encrypted request to its owner and retain a failed redacted response, but it does not preserve a complete ordered record of each attempt. Replaced request snapshots and intermediate successful provider responses are therefore unavailable for comparison. The 11:07 generation cannot be reconstructed retrospectively from the retained data.

When an authorized account is enabled for tracing, its owner should be able to inspect future generation runs: the exact credential-free, provider-bound request body; the exact provider model text before validation or reidentification; attempt order and outcome; retry, backoff, failover and repair decisions; and the accepted parsed output and editor projection. The view should make whitespace visible and allow a direct comparison between raw model output and what the editor received.

## Proposed authorization and privacy boundary

- A system administrator may enable or disable tracing for an account using a dedicated metadata-only control. It defaults off. The control changes capture behavior only; it grants no content access.
- Only the owning user may read or clear trace content. System administrators, team leaders, and provider managers cannot read it, even when they enabled tracing.
- Show the owner a clear capture indicator while tracing is enabled, and identify that traces contain redacted prompts and model responses.
- Capture only provider-bound content after the established redaction boundary. Store placeholders as sent. Redaction can leave residual identifying information; these traces remain sensitive clinical content and must never be described as anonymous. Never capture credentials, authorization headers, Vault references, raw provider error bodies, cookies, tokens, or secrets.
- Audit admin enable/disable actions using only bounded metadata such as actor ID, target account ID, action, timestamp, and outcome. Audit records must not include trace content or generated-document identifiers unless an established safe audit contract requires them.
- Clinical trace content is encrypted with the owning user's established content key, bound to owner/table/field/row identity, and remains transcript-owned. It must not enter logs, audit/usage metadata, queue/outbox payloads, or provider-attempt rows.
- Keep existing `ProviderAttempt` and usage/transport records content-free. Link trace events to attempts by stable IDs and record only bounded controlled error codes and safe transport metadata.

## Capture and display contract

Capture future attempts while enabled, including successful attempts. Capturing successes is required to diagnose formatting changes that do not end in an error. Each event should record:

- run and attempt ordering, timestamp, workflow/phase, provider adapter and model, and links to the existing generated document/execution and provider attempt where applicable;
- exact provider-bound request body/messages after adapter envelope construction, excluding credentials and transport authentication data;
- exact returned model text before JSON parsing, validation, placeholder restoration, normalization, or editor assembly;
- controlled result/error code, HTTP status where already considered safe, token/timing metadata, and bounded backoff/retry/failover/repair decisions;
- whether capture is complete, omitted, or bounded/truncated. The UI must never label partial content as exact.

The owner view should present raw output with whitespace made visible and the validated/parsed output. Compare it with the final editor projection by reading the existing owner-authorized generated-document result; do not duplicate that result, original source, restored PHI, or PII mappings into trace storage. For structured notes, show section values and the rendered text the editor actually receives. Preserve exact captured strings; do not normalize them during persistence. If a secret-safety check requires omitting content, record an explicit omitted/incomplete status and reason; never silently redact or alter a string while labelling it exact. Escape content in the UI and apply the application's normal output-safety protections.

The repair prompt intentionally differs from the original generation prompt: it contains the expected schema and the malformed redacted response and asks for formatting-only repair. It does not replay the original transcript/source prompt. On successful repair, the response is validated, placeholders are restored, and structured sections are assembled before editor persistence. The trace must show those as distinct stages so clinicians can see whether formatting changed in the model response, validation/materialization, or editor projection. Generated output remains a draft for clinician review.

## Workflow coverage

The requested outcome is to see what was tried at each generation stage. The completed feature must cover all supported LLM workflows, not only ordinary Template notes:

- ordinary Template generation, regeneration, same-primary invalid-JSON retry, backup, and backup repair;
- follow-up and Quick Action generation;
- hallucination checks;
- template suggestions;
- consultation-split analysis, generation, recovery, and verification.

Before implementation, map every dispatch boundary to a shared trace abstraction or an explicit workflow adapter. A partial rollout is acceptable only if its UI clearly names uncovered workflow types and does not imply complete tracing. Avoid duplicating credential, redaction, encryption, or provider-dispatch logic in a local trace implementation.

## Storage, retention, and lifecycle

Use dedicated transcript-owned encrypted trace storage rather than expanding metadata-only preference, usage, audit, or provider-attempt records. Store bounded event metadata separately from encrypted request/response content. Create trace rows only after the transcript root exists and preserve the established owner and active-root checks before decrypting.

Recommended retention: each captured content event expires at the earlier of `captured_at + 24 hours` and its transcript root's server-owned `retention_expires_at`. It becomes unavailable at that deadline and is physically removed through the established durable deletion/cleanup path. The proposed admin control has no capture-expiry setting. Disabling capture stops new events but does not extend or silently shorten the retention of existing events. Give the owner an explicit clear-traces action if supported by the existing deletion abstractions.

**Retention decision:** use the 24-hour cap approved by the user. User and administrator preferences cannot extend that deadline or the transcript-root deadline.

Bound request/response sizes and event counts. If a bound is reached, persist a content-free reason/status that capture is incomplete; do not truncate and represent the result as exact. Trace-write failure must not change provider dispatch, quota settlement, validation, or generation success/failure. Use an isolated, well-defined persistence boundary so a diagnostic storage failure cannot roll back already-authorized business work or create an untracked provider retry. Do not silently swallow capture failures; surface a safe capture-incomplete state to the owner and content-free operational telemetry.

## Existing behavior and likely integration points

- `app/services/templates.py` builds and dispatches ordinary note requests; `generated_document_llm_request_payload` currently decrypts one latest request, while backup and repair replace that snapshot. Failed redacted output has one encrypted field.
- `app/services/llm_adapters/runtime.py` and adapter-specific request snapshot/build/invoke functions define provider-bound envelopes. Capture must occur at the dispatch boundary after the envelope is final and before invocation.
- Other workflow services have their own dispatch and durable execution paths; map them before choosing a shared abstraction.
- `app/services/content_crypto.py` supplies owner-bound encrypted content helpers. Reuse it and existing root/owner authorization services.
- `app/models.py` defines `GeneratedDocument`, `ProviderAttempt`, `UserAppPreference`, and transcript-owned execution records. `UserAppPreference` is currently user-managed preferences metadata and should not become an unguarded admin-authoritative control.
- `app/web/presentation.py`, `app/routes/api_routes.py`, and `app/schemas/templates.py` expose owner-generated-document detail and a localhost-dev-only redaction-debug route. Preserve the latter's restriction; it is not the production trace feature.
- Admin account management is in the admin routes/services and `app/templates/admin_mockup.html`. Add only a metadata toggle there; do not expose trace content in admin views.
- The route-audit manifest exists at `app/api_route_audit.py`; update it and perform the documented authorization audit for new `/api/v1` routes.

## Architecture authorization

The user authorized the narrow exception on 10 November 2026, with a 24-hour cap. Redacted provider-bound request snapshots and model text may be retained as owner-readable, owner-encrypted transcript content. Administrator or team content access, general-purpose diagnostics, unredacted content capture, and content logging remain prohibited. Provider attempt, usage, outbox, and audit rows remain metadata-only. `AGENTS.md` records the exception.

A syntactic JSON preview is not a record of the workflow's accepted parsed result. The editor comparison reads the current owner-authorized draft and may include later edits; it is not a historical snapshot. The delivered scope and its limits are recorded below.

## Acceptance criteria

1. Admin enable/disable is system-admin-only, changes metadata only, is default-off, and cannot grant content access. The owner sees the capture state.
2. Owner-only APIs and UI enforce active transcript ownership and expiry before decrypting. Admin/team-leader/provider-manager requests cannot read trace content.
3. Enabled future runs capture ordered primary/retry/backup/repair/checker/workflow stages with exact credential-free redacted request bodies and pre-validation model text, plus safe attempt/deadline/backoff/decision metadata.
4. The view shows captured model text, a syntactic JSON preview and separate workflow validation status, and compares them with the current editor content with whitespace controls. It does not preserve historical validated or placeholder-restored intermediate snapshots; current editor content can include later edits.
5. No trace content is written to logs, audit, usage, quota, outbox, or provider-attempt metadata. Credentials and unredacted source never enter the trace.
6. Trace content uses the owner DEK boundary, expires at the earlier of capture time plus the approved trace window and the transcript-root deadline, becomes unavailable at expiry, and is removed through durable deletion cleanup. Disabling does not extend retention.
7. Bounded capture failures do not alter provider calls, quota behavior, retries, validation, or generated drafts; failure is visible as an incomplete trace with content-free telemetry.
8. Existing structured-output validation and clinician-review draft behavior remain unchanged.

## Validation required for implementation

- Focused authorization tests for owner read, cross-owner denial, admin denial, leader denial, and expired-root unavailability.
- Encryption and lifecycle tests proving ciphertext-at-rest, owner/table/field/row binding, transcript expiry, user/team deletion cleanup, and no content in metadata records.
- Workflow tests proving ordered traces for success, provider error, 429 backoff, backup, invalid-JSON retry, repair, timeout, duplicate delivery, and capture-storage failure without extra dispatch or quota effects.
- Contract tests comparing exact raw response, validated result, and structured editor projection, including whitespace and placeholder restoration.
- Route audit and API authorization audit for any new API route; focused frontend tests for the owner indicator and trace view.
- Update maintained security, LLM provider, API, and testing documentation; run operational-document validation and relevant focused checks.


## Delivered scope and verification (10 November 2026)

The feature records credential-free redacted provider request snapshots, pre-normalization model text and safe attempt, transport, validation and decision metadata when capture is enabled. Recent content-free transport observations can indicate a missing capture; absence of an observation cannot prove no request occurred. Events can be incomplete because capture is bounded or failed. The Gemini request snapshot describes SDK arguments rather than literal HTTP bytes.

The JSON preview is a syntactic parse of captured text with duplicate keys rejected. It is not the actual validated intermediate, and workflow validation is reported separately. The editor comparison reads the current owner-authorized draft, which may have changed since generation. Placeholder-restored intermediate history is not retained.

Focused verification used the local PostgreSQL and Redis test services with `APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app`. Trace, adapter, outcome, runtime and migration checks passed 136 tests; affected workflow checks passed 144 tests; the follow-up suggestion and split-analysis/runtime checks passed 121 tests, with the separate browser test corrected and rerun successfully. The final browser/admin/UI checks passed four tests. The standalone API authorization audit passed, and operational documentation validation checked 54 files. Synthetic data was used. No deployment was performed.
