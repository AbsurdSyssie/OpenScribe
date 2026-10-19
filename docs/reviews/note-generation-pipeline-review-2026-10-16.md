# Note generation pipeline review — 16 October 2026

This dated assessment records current behavior and a prioritized fix backlog for ordinary note generation, regeneration, and consultation splitting. It inspects the working tree based on commit `717d2698a2ac268f830d99548068202a5de3f780`, which also contains pre-existing local changes. It is a review record, not a new runtime contract or an authorized implementation plan. Preserve it as a point-in-time assessment; later reviews should add a newer record.

The pipeline handles transcript-derived clinical material, so ownership, transcript-root retention, encryption, redaction, provider-attempt accounting, and clinician review remain hard boundaries. Generated notes are drafts and require clinician review. This review proposes contained fixes and test work only; it does not authorize new content sharing, weaker validation, new structured-output contracts, or a change to checker failure policy.

## Ordinary generation and regeneration

1. The Scribe workspace submits a selected template to `POST /transcripts/{transcript_id}/generate-output`. The route requires a fully authenticated context and applies daily and burst LLM-generation limits. The service checks team membership, owner-scoped active transcript, recording/ingestion state, template availability, and the user's resolved provider/model. See [`api_routes.py`](../../app/routes/api_routes.py#L2460) and [`templates.py`](../../app/services/templates.py#L3752).

2. Before dispatch, the service creates or selects an immutable transcript version, snapshots the current Working note and post-consultation dictation, and records the selected template version/config plus generation options. A generated-document row is created under the transcript root with the same owner, team, and retention expiry. The content snapshots use the owner's encryption boundary. See [`templates.py`](../../app/services/templates.py#L3615) and [`templates.py`](../../app/services/templates.py#L3652).

3. Quota reservation, generated-document state, and durable task-dispatch state are committed before the outbox publisher is tried. A broker publication problem therefore does not erase committed queued work. The worker waits for eligible transcription work when the job was queued in wait mode; it then snapshots the final transcript version before provider work. See [`templates.py`](../../app/services/templates.py#L3594) and [`templates.py`](../../app/services/templates.py#L1771).

4. The worker resolves the snapshotted provider configuration, creates or reuses the redaction run for that exact transcript version, and obtains its effective redacted text and placeholder map. It also redacts saved dictation, Working note, manual PII, structured context when supplied, previous output during regeneration, and freeform steering. Redaction or decryption errors fail before provider dispatch. See [`templates.py`](../../app/services/templates.py#L5364), [`templates.py`](../../app/services/templates.py#L5392), and [`redaction.py`](../../app/services/redaction.py).

5. The selected template prompt and section instructions are reusable configuration and are passed directly into the request; they are not dynamically redacted. This follows the rule that reusable configuration must not contain patient or transcript content. Structured context is separately redacted before being appended as context. See [`templates.py`](../../app/services/templates.py#L5433) and [`templates.py`](../../app/services/templates.py#L5445).

6. The worker builds an adapter-specific request and local expected-output contract. Freeform template notes expect `{title, content}` strings. Structured templates expect a title and allowed configured sections; the local parser checks JSON shape, title, allowed section keys, and string section values. Provider response-format/schema support is an additional aid; local validation remains authoritative. See [`templates.py`](../../app/services/templates.py#L2393), [`templates.py`](../../app/services/templates.py#L2405), and [`templates.py`](../../app/services/templates.py#L2559).

7. Credentials are resolved before the submitted provider-attempt boundary. The worker atomically claims work, freezes the request, marks the attempt submitted, then calls the provider under the execution budget. Provider failures and malformed output are terminalized with safe codes and quota settlement. See [`templates.py`](../../app/services/templates.py#L5582), [`templates.py`](../../app/services/templates.py#L5593), and [`templates.py`](../../app/services/templates.py#L4783).

8. For structured template notes only, generation first commits a validated encrypted draft checkpoint. An optional separately configured checker then sees the redacted transcript, Working note, dictation, and generated note. It may make exact-substring edits under a bounded policy. Checker failures leave an unchecked draft; this is deliberate fail-open behavior and status is exposed in the workspace. Lifecycle reconciliation can promote a live validated checkpoint if checking expires, and late responses cannot overwrite a ready/edited/terminal result. Freeform notes and follow-ups/Quick Actions are not checked by this structured checker. See [`templates.py`](../../app/services/templates.py#L4751), [`templates.py`](../../app/services/templates.py#L2936), and [`hallucination-check-design.md`](../hallucination-check-design.md).

9. After local validation and optional checking, placeholders are restored in memory and output/sections are encrypted for the owner. Titles remain plaintext under the documented metadata exception. The original and editable draft begin with the same text. Structured sections are stored individually as well as rendered for display. Ready state and quota settlement commit together. Follow-ups and Quick Actions use the direct text path rather than Template JSON parsing. See [`templates.py`](../../app/services/templates.py#L5689) and [`templates.py`](../../app/services/templates.py#L5747).

10. Regeneration makes a new immutable revision in the same lineage. It uses saved source/template snapshots, the prior note output, and newly submitted steering. It checks owner, transcript/retention scope, terminal source status, and lineage before queueing. It does not overwrite the earlier draft. See [`api_routes.py`](../../app/routes/api_routes.py#L2432) and [`templates.py`](../../app/services/templates.py#L4031).

## Consultation-splitting flow

1. The owner queues analysis for an eligible transcript. The service checks preference and source state, prepares the transcript/Working note/dictation and clinical hints, applies the established redaction boundary, resolves the selected provider, and records an idempotent execution, quota reservation, and outbox dispatch. Analysis prompts are bounded and contain only redacted source text, hints, and available template metadata. See [`api_routes.py`](../../app/routes/api_routes.py#L2093), [`consultation_split_analysis.py`](../../app/services/consultation_split_analysis.py#L333), and [`consultation_split_queue.py`](../../app/services/consultation_split_queue.py#L382).

2. The analysis response is bounded and parsed as one JSON envelope. Duplicate keys are rejected; shape, topic limit, disposition, primary-topic rules, UUID-independent template selection, and known template IDs are validated before an owner-visible proposal is saved. Invalid analysis does not become a draft. See [`consultation_split_analysis.py`](../../app/services/consultation_split_analysis.py#L675).

3. The clinician reviews/edits the proposal and confirms the split. Confirmation rechecks the transcript root, owner, retention, exact analysis/source lineage, selected templates, and source version. It freezes source/configuration snapshots and atomically creates the bundled generation execution, quota, and durable dispatch state. Confirmed content remains owned by the same user. See [`consultation_split_confirmation.py`](../../app/services/consultation_split_confirmation.py#L270).

4. The bundled worker sends one bounded request over the reviewed topics and validates the response against the confirmed topic UUIDs, modes, section keys, and content limits. Structured split notes require the configured keys and at least one populated section; freeform results must be nonempty. Independently validated outcomes are retained encrypted. One automatic targeted recovery is permitted after a trustworthy durable mixed response, using frozen provider settings; submitted work with no durable response is not automatically replayed. See [`consultation_split_generation.py`](../../app/services/consultation_split_generation.py#L417) and [`consultation_split_generation_runtime.py`](../../app/services/consultation_split_generation_runtime.py#L329).

5. After recovery has reached a terminal decision, optional verification checks a complete structured survivor set against frozen redacted sources and clinical hints. Mixed/freeform batches and no-checker cases bypass this patch protocol. Patches are applied all-or-none and structured output is revalidated. Materialization follows the verification/no-checker decision: it restores frozen placeholders and creates encrypted transcript-owned generated drafts. Manual Retry and Keep Available remain gated and idempotent. See [`consultation_split_verification.py`](../../app/services/consultation_split_verification.py#L96), [`consultation_split_verification_queue.py`](../../app/services/consultation_split_verification_queue.py), and [`consultation_split_materialization.py`](../../app/services/consultation_split_materialization.py).

## Information, persistence, and observability

| Data | Provider use | Persistence and logging |
| --- | --- | --- |
| Transcript version and redaction map | Redacted version text plus pseudonym placeholders; original values are restored only after validation | Transcript-owned encrypted source/redaction data; attempt and usage rows hold metadata |
| Working note and dictation | Redacted saved snapshots; these remain distinct source types | Encrypted generated-document snapshots under the transcript root |
| Template prompt/config | Reusable instructions supplied as saved configuration; must contain no patient/transcript content | Versioned reusable asset; copied by reference/snapshot to the document as appropriate |
| Structured context / steering / prior output | Redacted dynamic inputs when included in the relevant generation request | Encrypted generated-document snapshots; not ordinary usage/audit log fields |
| Exact outbound generation request | Contains redacted clinical sources and saved instructions, never raw provider credentials | Encrypted request snapshot on the generated document; owner-only detail API may decrypt it |
| Generated and edited output | Redacted output is validated before reidentification and encrypted persistence | Encrypted output and section rows; transcript root controls retention/deletion |
| Failed output / recoverable split responses | Invalid output is never displayed as a ready note | Ordinary parse-failure output and split recoverable responses can be retained in owner-encrypted content fields; not in usage/audit rows |
| Usage and attempt events | No clinical prompt or completion content | IDs, provider/model, status, token counts, timing, cost, controlled error codes/status, and bounded transport metadata |
| Security/audit events | No transcript-derived text | Actor/object/team identifiers and safe action metadata |
| Celery/outbox dispatch | Provider work is identified by document/execution UUID | Note-generation task payloads contain identifiers, not source or output content |

The encrypted request snapshot can contain clinical content, so access must remain owner-scoped and transcript-root lifecycle-bound. Titles remain plaintext intentionally; this is documented as an explicit metadata exception in [`dek-kek-production-plan.md`](../dek-kek-production-plan.md#L43). Current provider request snapshot and logging boundaries are also described in [`llm-providers.md`](../llm-providers.md#generated-document-request-payloads) and [`security.md`](../security.md).

Ordinary `openscribe.usage` events cover queued, started, completed, failed and enqueue-failed transitions. Transport events contain UTC time, request ordinal, duration, controlled result code, HTTP status and retry wait, with at most four events per attempt. They commit independently before retry waits. The reviewed note paths do not intentionally log prompts, content or credentials. Deployment log formatting, effective log levels, access controls, retention and third-party SDK debug logging were not inspected; a call supplying Python `extra` fields does not establish which fields reach an operational log sink.

## Checks and failure handling

Provider transport uses a monotonic budget plus a Unix main-thread wall timer. Connection setup is limited to 10 seconds; generation and split analysis stages have 90 seconds, checking/verification at most 30 seconds, and ordinary generation plus checking share a 150-second submitted-attempt deadline. Queue waiting is separate. SDK retries are disabled. Only a classified temporary-capacity HTTP 429 may retry the same frozen request/credential: at most four requests, bounded jitter/Retry-After waits and a 30-second cumulative wait within the stage deadline. Timeouts, ambiguous connection failures, HTTP 503 and malformed output do not automatically replay. Required credentials are resolved before submission; submitted uncertain work settles conservatively. Unsupported worker contexts currently fail only at the transport boundary, as recorded in `LLM-UNSUPPORTED-WORKER-PREFLIGHT`.

| Boundary | Current checks | Failure behavior / remaining concern |
| --- | --- | --- |
| Request admission | Authentication, owner scope, team membership, active/retained transcript, state, selection, configured provider, rate limits | Rejects before queuing; verify current source guards before carrying older split-create timing concerns forward |
| Queue and quota | DB-backed reservation, dispatch claim/idempotency, transactional outbox, durable cleanup | Prevents double submission and stranded publication state; retain existing accounting semantics |
| Before provider dispatch | Transcript version and required snapshots, redaction, structured config, provider/model, credential resolution, request snapshot | Fail closed for redaction/config errors; pre-dispatch credential failure must not consume provider quota |
| Provider response | Adapter transport/status handling, execution time budget, output nonempty checks, provider-specific finish handling | See high-priority finish/stream issues below; output-size parity between adapters remains a boundedness question |
| Ordinary freeform output | JSON object, nonempty title/content strings | Rejects malformed shape; `json.loads` currently accepts duplicate keys, so ambiguous repeated fields may resolve silently |
| Ordinary structured output | JSON object, title, content object, allowed configured keys, string values | Allows omission/blank values per prompt/schema; an all-empty note is accepted. Decide/test the intended ordinary contract before tightening |
| Hallucination checker | Structured-note-only; bounded retries/edits; exact unique substring patches; separate quota/attempt accounting | Failure remains unchecked and draft remains ready by design; workspace exposes status. Do not treat this as verified when failed or skipped |
| Persistence | Encryption helpers, owner-bound generated document, transcript retention expiry, ready/failed terminal state, quota settlement | Clinician edits are separate from source and original generated content; preserve encrypted source snapshots on revision |
| Split response | Bounded output, duplicate-key rejection, strict envelope and topic identity/mode/content validation, survivor recovery | Stronger-than-ordinary local checks; retain transactional confirmation, recovery, and verification rules |

## Prioritized backlog

### P1 — Provider completion semantics can make truncated drafts look complete

**Confirmed adapter behavior; ready-state impact established by code tracing.** The OpenAI-compatible path reads message content but does not inspect the completion's `finish_reason` ([`runtime.py`](../../app/services/llm_adapters/runtime.py#L300)). A synthetic completion marked `length` returned successfully. Template parsing may accept truncated content if it remains valid JSON. The Ollama stream accepts EOF with accumulated text even without a final done event and ignores `done_reason` ([`runtime.py`](../../app/services/llm_adapters/runtime.py#L401)); a synthetic `done:false` frame followed by EOF returned successfully. Follow-up and Quick Action text enters the direct persistence branch and can become `ready` ([`templates.py`](../../app/services/templates.py#L5689)). Gemini already rejects `MAX_TOKENS` ([`gemini_enterprise.py`](../../app/services/llm_adapters/gemini_enterprise.py#L208)). Tracked separately as `NOTE-OPENAI-INCOMPLETE-OUTPUT` and `NOTE-OLLAMA-INCOMPLETE-STREAM`.

**Acceptance criteria.** Classify OpenAI-compatible length/truncation finish reasons and Ollama missing/incomplete terminal frames or length stop reasons as controlled truncation failures before persistence. Do not persist them as ready drafts. Preserve valid stop/completion results, safe provider metadata, attempt settlement, and content-free error reporting. Add adapter tests and worker-level persistence tests for freeform, structured, follow-up, and Quick Action outputs.

### P2 — Align Ollama cap and quota semantics for follow-up/Quick Action

**Bounded contract issue.** Ordinary template notes set `output_token_cap`; follow-ups and Quick Actions leave it `None`. The Ollama snapshot builder only includes `options.num_predict` when a cap is supplied ([`templates.py`](../../app/services/templates.py#L5408), [`runtime.py`](../../app/services/llm_adapters/runtime.py#L87)). Queue reservation starts from a nominal estimate, while dispatch claim expands against the request's resolved cap/default. Existing runtime tests intentionally assert the current no-options request shape. Decide whether uncapped follow-up/Quick Action behavior is supported, then make submitted limits, reservation, and usage settlement agree. Do not silently change that established request shape.

**Acceptance criteria.** The selected contract is documented and tested for both operations and adapter kinds. The exact request snapshot expresses the intended cap; quota reservation covers the maximum submitted request; successful and failed attempts settle consistently; existing provider compatibility tests pass.

### P2 — Add safe operational stage evidence for unexpected split worker errors

**Operational gap.** The split task catches unexpected exceptions to prevent raw exception/provider text from reaching Celery, rolls back, then returns without recording a safe stage/error event ([`tasks.py`](../../app/tasks.py#L94)). This protects privacy but makes silent terminalization/reconciliation harder to diagnose.

**Acceptance criteria.** Record a bounded, content-free event with execution ID, controlled stage, safe error category/code, and timestamp before or alongside rollback/terminalization. Never persist exception text, provider payloads, prompts, transcript/output text, or credentials. Verify the existing terminalization and quota lifecycle remain authoritative.

### P2 — Reject ambiguous duplicate JSON keys in note and checker parsing

**Confirmed validation gap.** Ordinary note parsers, the ordinary checker and the split verifier's outer envelope use standard `json.loads`; duplicates use deterministic last-value-wins behavior. Synthetic assertions confirmed duplicate ordinary content, checker status and split-verifier topic keys pass. Split analysis and generation already reject duplicate keys. Apply the same rule without changing response shapes. See [`templates.py`](../../app/services/templates.py#L2559), [`templates.py`](../../app/services/templates.py#L2778), and [`consultation_split_verification.py`](../../app/services/consultation_split_verification.py#L129). Tracked as `NOTE-AMBIGUOUS-JSON-KEYS`.

**Acceptance criteria.** Duplicate top-level or nested keys fail with existing safe invalid-output errors; error messages/details do not echo provider content. Cover note, checker status/edit fields, structured sections and split-verifier topic UUIDs. Preserve valid JSON and existing unchecked fallback behavior.

### P2 — Decide how to handle all-empty ordinary structured responses

**Contract question / test gap, not a confirmed defect.** The parser accepts an empty `content` object or all-blank sections and returns an empty section list. A synthetic parser probe confirmed both cases. Existing behavior deliberately permits individual empty optional sections (`tests/test_consultation_split_generation.py:206`) and ordinary prompt/schema language tells the model to omit sections with no relevant content. Split generation is stricter and requires at least one populated section. Decide whether an ordinary structured note with no relevant sections is a valid titled empty draft or invalid output.

**Acceptance criteria.** Add a focused ordinary-generation parser/worker test for `content: {}` and all-empty configured sections, documenting either accepted behavior and UI presentation or controlled invalid-output failure. If stricter behavior is chosen, do not require every section; preserve optional omission and the current section-key contract.

### P3 — Bound and harmonize response size across adapters

**Improvement opportunity.** Ollama streaming enforces an 8 MiB response ceiling and fragment limit; OpenAI-compatible and Gemini paths do not have an equivalent explicit application-level byte ceiling at the shared runtime boundary. Large or malformed responses could consume excess memory before local validation.

**Acceptance criteria.** Define a documented response limit and reject over-limit payloads before parsing/persistence across all adapters. Keep provider responses out of exception messages and logs. Test limits with synthetic payloads and preserve allowed stream/provider semantics.

### P3 — Improve safe generation observability without storing content

**Improvement opportunity.** Current generation usage events provide IDs, provider/model, status, token counts, timing, estimated cost, and safe error codes. Stage-level observability could make redaction, preflight, dispatch, parsing, checking, and persistence failures easier to distinguish without exposing clinical text.

**Acceptance criteria.** Add controlled stage/error categories and correlation identifiers only where needed. Keep out prompts, provider responses, audio, PII, credentials, raw exception strings, and arbitrary URLs. Confirm owner-facing error presentation remains useful and logs remain metadata-only.

### P3 — Clarify provider-specific structured output and detail controls

**Improvement opportunity.** Gemini note-generation length is snapshotted metadata but does not alter the fixed provider cap or add a semantic prompt guard; this is already documented ([`llm-providers.md`](../../docs/llm-providers.md#L134)). JSON schema support is adapter-specific, with local parsing the final contract.

**Acceptance criteria.** Keep documentation and UI claims aligned with actual provider behavior. Any later semantic length/detail change must preserve local schema validation, redaction, request snapshots, quota reservations, and adapter compatibility; do not introduce a new response shape/profile without design authorization.

## Invariants for later fixes

- Transcript is the retention/deletion root. Create it before transcript-derived content; expired roots are unavailable before asynchronous cleanup finishes.
- Content remains readable only by its owning user. Team/admin/provider authority is not transcript-content access.
- Reusable templates/Quick Actions contain portable reusable instructions, not patient or transcript data. The provider request snapshot is encrypted and owner-scoped.
- Redact provider-bound clinical/user content at established workflow boundaries and fail closed when required redaction fails. Restore placeholders only after output validation.
- Generated output and clinician edits remain distinct. Regeneration creates a new revision and must not mutate transcript, Working note, dictation, templates, or Quick Actions.
- Preserve adapter/provider selection, credential preflight, attempt boundary, quota settlement, outbox dispatch, and duplicate-delivery idempotency.
- Keep queue, attempt, usage, audit, and diagnostic metadata content-free. Do not weaken local structured-output validation or clinician review requirements.
- Treat checker failure as unchecked under the current deliberate fail-open policy; communicate status clearly rather than presenting it as verification.

## Existing issues and verification status

Track adjacent existing issues in [`DISCOVERED_ISSUES.md`](../../DISCOVERED_ISSUES.md); this review does not duplicate them: `LLM-UNSUPPORTED-WORKER-PREFLIGHT`, `GEMINI-400-ERROR-CLASSIFICATION`, and `ONE-WORKER-PROVIDER-STALL`. The timeout portion of the last issue has since been addressed; isolated worker topology remains deployment-dependent.

The previous `SPLIT-CREATE-PENDING-TRANSCRIPTION` concern is guarded in current code: source readiness is checked before preparation/claim and again after preparation in [`consultation_split_intents.py`](../../app/services/consultation_split_intents.py#L111). Do not carry it forward without evidence of a path that bypasses those checks.

Verification reported for this assessment: the focused ordinary LLM runtime, execution, provider-response-bounds, and generation-presentation modules passed, 68 tests total. Four focused split tests also passed, 72 tests total. The sandbox precollection attempt could not connect to local PostgreSQL; the same focused run succeeded through the supported elevated retry. Synthetic adapter probes and an empty-structured-output parser probe also succeeded. No real patient/provider data or web research was used. These checks were run by the parent/review agents; this document author made no code changes and did not rerun them.

Suggested regression coverage for the backlog:

- Adapter-level tests for OpenAI-compatible length finish reasons and Ollama missing final frame / length completion.
- Worker-level tests proving truncated follow-up and Quick Action output cannot be persisted as a ready draft.
- Ollama request and quota tests for whichever follow-up/Quick Action output-cap contract is selected.
- Task-boundary tests that unexpected split exceptions create only safe stage metadata and preserve terminalization semantics.
- Synthetic duplicate-key JSON tests for ordinary note and hallucination-check responses; compare against split analysis/generation boundaries.
- Ordinary structured parser and UI tests that document the chosen all-empty response behavior while preserving optional sections.
- Synthetic oversized response tests per adapter, including safe error presentation without retaining the payload in logs.
- Documentation checks for Gemini length/detail claims and the explicit title metadata exception.

Exact focused runs (both used `APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app .venv/bin/pytest -q`):

```text
tests/test_llm_runtime.py tests/test_llm_execution.py tests/test_provider_response_bounds.py tests/test_generation_presentation.py
# 68 passed, 1 Starlette/httpx deprecation warning

tests/test_consultation_split_analysis.py tests/test_consultation_split_generation.py tests/test_consultation_split_verification.py tests/test_consultation_split_generation_runtime.py -k 'prepares_one_mixed_mode_request or parser_accepts_exact_mixed_modes or bundled_verification_uses_exact_uuid_set or generation_submits_before_one_provider_call_and_commits_exact_mixed_documents'
# 4 passed, 142 deselected, 1 Starlette/httpx deprecation warning
```

Direct synthetic probes additionally demonstrated empty ordinary structured output, incomplete OpenAI/Ollama adapter acceptance, duplicate-key acceptance and absent non-Template Ollama caps. Existing passing tests do not establish coverage of these gaps. No full suite, browser run, production-log inspection or live-provider call was performed.

Luna agents traced ordinary generation, split generation and asynchronous transport independently; the parent checked findings against source, corrected overstatements and integrated this report. Five issue entries and a reassessment were added to [`DISCOVERED_ISSUES.md`](../../DISCOVERED_ISSUES.md), and [`docs/README.md`](../README.md) links this record. Maintained-document validation passed (`.venv/bin/python .github/scripts/check-operational-docs.py`: 45 files). No runtime, schema, migration or configuration changes were made for this review. Follow-up priorities are recommendations, not authorization to alter architectural contracts.
