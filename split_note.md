# Separate consultation notes — historical implementation plan

## Current completion summary (11 September 2026)

All implementation phases recorded below, including bundled generation, partial recovery, optional structured verification, browser restoration safeguards, and source-only materialization, are complete in the current working tree and covered by synthetic focused tests. The final source-only defect is fixed: confirmation binds an encrypted empty transcript version to Working-note-only and dictation-only batches, and generation uses that batch binding for every child document. Owning team users and team leaders can use the workflow; leadership grants no access to another user's transcript-derived content, and system administrators remain excluded. The feature remains **deployment-disabled** by default: `CONSULTATION_SPLITTING_ENABLED=false`. Do not enable it for deployment until a release reviewer accepts the dated Phase 10 evidence and completes the rollout-only checks.

The maintained current contract is [docs/consultation-splitting-preference.md](docs/consultation-splitting-preference.md). The dated validation record is [docs/consultation-splitting-release-evidence-2026-09-10.md](docs/consultation-splitting-release-evidence-2026-09-10.md). The rest of this file preserves sequencing and design rationale. Its future-tense, partial, and pending statements are historical and superseded by this summary.

### Remaining rollout-only items

- Keep the deployment gate false.
- Obtain independent release-review sign-off for the dated evidence.
- Exercise the gated workflow in a deployment-like environment with configured provider credentials, worker/Beat, Vault, and production migration backup/rollback procedures. Use synthetic data only.
- Perform the manual browser checklist items that cannot be proved by the local Playwright and JavaScript harnesses.

## Historical status record

**Phase 10 status, 9 September 2026:** recovery and verification ordering is implemented behind the disabled deployment gate. A trusted partial response queues its sole automatic recovery before any optional verification. Recovery parses only its failed-topic response set; retained siblings stay immutable. Verification waits for the recovery to complete or fail definitively, then runs once against the stable complete set or stable survivors. It never queues beside active recovery. Manual Retry missing and Keep enforce the deployment and owner gate before transcript lookup, return `403 consultation_split_disabled` without side effects when disabled, and do not strand already durable worker or lifecycle work. Split notes remain clinician-review drafts; rollout evaluation remains pending.

Current, ratified implementation plan. Slice 1 (gate and preference), Slice 2A (passive schema/model), Slice 2B (passive lifecycle boundary), the Slice 3 dispatch/quota safety foundation, Slice 4 (provider-runtime extraction plus redaction-source primitives), Slice 5A (server-derived source-state fingerprints), Slice 5B (bounded analysis contract, prompt, and parser), Slice 5C (encrypted redacted-source snapshot preparation with a required current-transcript redaction boundary), Slice 5D0 (durable split-execution LLM-config retention plus canonical snapshot/config binding), Slice 5D1a (pure analysis request preparation), Slice 5D1b (source-preparation/cache foundation), Slice 5D1c (atomic initial queue/cache), Slice 5D2a (claim-free pre-submit preparation), and Slice 5D2b (one-shot provider analysis runtime) are implemented and focused-verified. Slice 5D2c (source-writer and preference serialization), Slice 6A (owner-only analysis queue route, safe projection, and read-only workspace REST/SSE state), API-only Slice 6B (owner review-draft initialise/read/replace routes and read-only workspace projection), Slice 6C1 (passive existing-draft review/edit modal), Slice 6C2a (effective capability gate shared by bootstrap, REST, and SSE), and accepted persistence-only Slice 6C2b (durable logical-Generate intent persistence) are implemented. The later service-only atomic intent-start slice is **PARTIAL / UNVERIFIED**: interrupted work left scratch files in the worktree, but it is not implemented or accepted. Slice 5D1c commits initial encrypted analysis/execution snapshots, a reservation, and an outbox row before best-effort publish. Slice 5D2a resolves the bound credential outside a database transaction, then re-locks and proves the current source and stored request. Slice 5D2b permits one provider call only after a received dispatch and durable submitted attempt; it accepts either pending or published outbox state because Celery publish necessarily precedes the durable published marker. It commits an encrypted response before parsing, settles one usage event, and recovers that response without a second call. It suppresses queued or submitted work when the clinician turns splitting off, and terminalizes expired or stale source work safely. Slice 5D2c serializes mutable preference and transcript-owned source writes with the runtime's final proof. Slice 6C1 edits only an existing draft already projected by workspace state; it does not intercept **Generate**, initiate analysis, initialise a draft, or start generation. Slice 6C2a exposes only a server-derived boolean: it is true for a normal owning user only when the deployment gate and stored preference are both enabled; it is false for missing users, team leaders, system administrators, missing preferences, or a disabled deployment. It also suppresses analysis/draft projections when the effective gate is false. Slice 6C2b adds no route or workflow: it persists the future owner-scoped request identity and encrypted fallback snapshot only. Analysis initiation and restoration polling, the **Continue as one note** flow, batches, bundled generation, and retry remain outstanding. Consultation splitting remains disabled by default; the API route can enqueue only when both explicit gates are enabled.

**Status correction, 6 September 2026:** Slice 6C2c (service-only atomic intent start) is implemented and focused-verified. This supersedes the earlier partial/unverified wording in the long historical status summary above. Keep the deployment flag off until the final evaluation passes.

**Status correction, 11 September 2026:** the end-to-end **Continue as one note** slice is complete. A browser-started intent with a `not_required` analysis is consumed automatically because the model found fewer than two topics; the clinician does not need a second click. Failed, stale, and incomplete analysis remain non-generating recovery states. Active split review retains its explicit **Continue as one note** choice. Consumption never calls ordinary `/generate-output` or sends template, source, or content payloads. Deleted-child replay stays consumed and reports no recreation. Restored drafts remain passive. Bundled generation and partial recovery are implemented; verification and rollout evaluation remain pending. Provider quota is the business limiter; database-only draft writes have no LLM limiter, while intent start and consume retain transport limits.

**Historical status correction, 8 September 2026:** split-draft **Confirm** originally froze owner-DEK encrypted redacted source, manual-PII, plan, exact template-version, and note-option snapshots into an immutable batch/topic/pending-outcome tree. This was superseded later that day by atomic generation queue construction; see the current correction below.

**Status correction, 8 September 2026:** Phase 7's pure bundled-generation contract is complete and focused-verified. `app/services/consultation_split_generation.py` validates decrypted confirmation snapshots in memory, builds one provider-neutral request with shared redacted sources once, returns the exact no-title response schema, calculates deterministic caps and a one-copy input estimate, and authoritatively parses the complete mixed-mode envelope. It has no database, provider, task, queue, quota-reservation, or document changes. Later queue/runtime execution, persistence, and recovery are implemented; verification and rollout evaluation remain pending.

**Status correction, 8 September 2026:** Phase 8 recovery is implemented behind the deployment gate. A trustworthy mixed response retains valid encrypted outcomes and marks only missing or invalid requested topics failed; an ambiguous outer envelope remains a total failure. One automatic recovery may follow only that durable parsed response and uses the original frozen provider; submitted/no-response and timeout states never qualify. Clinician-directed retry queues one current-provider generation execution for failed topics only, with immutable full boundaries and accepted sibling context. Keep Available is database-only, idempotent, and blocked while a recovery is active. Workspace/SSE projects owner-safe partial state and server-derived action flags only. Browser controls warn about a failed primary and confirm selection of a surviving secondary after `completed_partial`. Verification and rollout evaluation remain outstanding; keep the deployment flag off.

**Status correction, 8 September 2026:** confirmation now requires an eligible selected LLM configuration and model. It builds the initial bundled-generation execution, encrypted canonical adapter request, write-once provider snapshot, quota reservation, and durable outbox row in its transaction; no selection rolls the entire confirmation tree back. The runtime resolves credentials before its final locks, submits once, saves an encrypted recoverable provider response, and creates all split documents only after the complete response validates. It does not retry, verify, or reidentify split output. Provider snapshots, requests, outputs, and topic titles remain owner-encrypted; generated document titles use the fixed generic projection.

**Phase 9 implementation note, 9 September 2026:** an independently selected team hallucination checker may verify a complete structured survivor set before split-note drafts materialize. It receives only immutable encrypted batch snapshots and validated outputs, uses the existing exact-substring section patch contract, stores a corrected candidate separately from the accepted output, and revalidates it before use. It has one durable execution, quota reservation, outbox task, submitted-before-call boundary, and no retry. Checker credential, quota, expiry, transport, or patch failure is fail-open: accepted outputs remain available for clinician-review drafts and no provider reasoning, patch, or response enters metadata or workspace state. Mixed and freeform sets remain unchecked; no freeform patch protocol exists. Keep remains disabled while verification is active.

This plan was checked against the models, migrations, services, routes, workspace client, focused tests, and maintained documentation on 3 September 2026. Implement it through the safe slices below. Keep the deployment flag off until the final evaluation passes.

## Goal and language

When a clinician presses **Generate**, OpenScribe may analyse the saved consultation sources, propose distinct topics, let the clinician review them, and generate a coordinated set of notes.

Use **topic** or **issue**, not disease or condition. A meaningful topic can support its own note through a distinct assessment, management decision, investigation, medication decision, safety-net, or follow-up. Do not split background conditions, incidental mentions, or each clinical NLP entity into separate notes. Keep related symptoms with one assessment and plan together.

## Ratified product rules

- Add an independent user preference. It defaults off.
- Add `CONSULTATION_SPLITTING_ENABLED`, default false. Hide the preference and reject split endpoints while disabled.
- When splitting applies, suppress ordinary template suggestion for that flow. Do not make two classification calls or show competing choices.
- Start analysis only after **Generate** and after the Working note and dictation save successfully. Do not start it on transcript readiness or workspace open.
- Accept any non-empty combination of transcript, Working note, and saved dictation.
- Send redacted sources only. Apply manual PII protection before dispatch.
- Clinical NLP may add hints, but its absence or failure must not fail splitting.
- With fewer than two meaningful topics, continue through ordinary generation using the template selected when Generate was pressed.
- With at least two meaningful topics, open the review modal.
- Limit both proposals and confirmed separate notes to six.
- Initial coordinated generation uses one provider call. Recovery calls may target failed topics.
- All outputs remain drafts requiring clinician review.
- Bundled generation does not change the consultation or transcript title.
- Preserve normal `/generate-output`, Quick Action, and follow-up behaviour.
- Quick Actions and follow-ups continue to use their established consultation-source snapshots. Split children never become implicit sources.

## Review semantics

### Primary topic

Analysis proposes exactly one **primary topic**. The clinician may change it. A clinically relevant fact that cannot be assigned confidently elsewhere goes into the primary note only. Shared facts may appear in several notes when independently relevant. Omit irrelevant or unsupported material.

Analysis may surface borderline items with `include_in_primary` preselected, but only when at least two separate meaningful topics already require review. If there is only one meaningful topic, incidental material flows into that note and no modal opens.

### Topic card

Each card shows only:

- an editable topic title, which is also its scope instruction;
- a primary selector;
- a mutually exclusive disposition;
- a template selector.

Do not add a separate scope field, confidence score, or model rationale.

Each non-primary topic has one disposition:

- `separate_note` — create its own note;
- `include_in_primary` — retain relevant facts in the primary note;
- `exclude_from_notes` — omit its issue-specific facts from this batch.

The primary topic is always `separate_note`.

Combining topics creates a new stable topic UUID and editable title. A combined group remains primary if one member was primary. Keep its template only if all members used the same template; otherwise require a new choice. Removing the primary requires an explicit replacement.

Allow clinician-added topics with a non-empty title and available template. Generated facts must still be supported by the sources.

If review leaves one separate note, use ordinary generation with that remaining template. If none remain, disable split confirmation and leave **Continue as one note** available.

The current confirmation acknowledgement exposes only safe separate-note and total-topic counts. Included/excluded detail remains in encrypted plan data and is not returned by this persistence slice.

### Actions

- **Generate N notes** confirms an immutable batch.
- **Continue as one note** bypasses splitting for that request and starts ordinary generation. It does not destroy the analysis.
- Closing means **Review later** and preserves the draft.
- **Review note split** reopens a valid draft.
- Seed a new draft from the most recently confirmed clinician grouping, or the model proposal if none exists.
- Do not offer reanalysis for unchanged sources. Offer **Retry analysis** only after failure.

## Source and template snapshots

Bind analysis to the exact transcript version, Working-note version or hash, dictation version or hash, owner, team, redaction run, candidate-template metadata, and provider execution snapshot.

If a source changes during analysis, discard the stale result. Do not loop while the clinician edits; analyse again on the next Generate. Any source change before confirmation makes the proposal and draft stale. Changes after confirmation do not alter that batch.

At confirmation, resolve every template through normal access rules and snapshot its exact version, name, mode, prompt, and structured sections. Deleted, inactive, or inaccessible templates require a new choice. Never substitute after confirmation.

Apply monotonic manual-PII protection: use the confirmed protection plus anything added before dispatch. Removing an entry after confirmation must not weaken the batch.

Apply the user's length and detail preference to each note independently. Reject a bundle that cannot fit the provider's safe cap.

## Domain model

Do not force analysis, review, generation, verification, and retries into one status-heavy coordinator.

### `ConsultationSplitAnalysis`

The source binding and proposal are immutable; lifecycle status, safe error, and execution references may change. Store owner/team/transcript/version/redaction IDs, source fingerprint, encrypted proposal, safe error code, and timestamps. Enforce one reusable analysis per owner, transcript, and source fingerprint. Cache `not_required` for unchanged sources. Each retry is a new execution for that analysis, not a second analysis row.

### `ConsultationSplitIntent`

One durable, owner-scoped logical **Generate** action. Store its UUID idempotency key, owner/team/transcript IDs, transcript retention deadline, and one owner-DEK encrypted JSON snapshot of the selected template/version and ordinary-generation configuration. Do not store a live template or template-version foreign key, template name, prompt, configuration, source content, or patient content in plaintext.

The future atomic Create service binds an intent to the reusable analysis. The nullable analysis reference uses `SET NULL`: deleting an analysis independently preserves the request identity and encrypted selected-at-submit snapshot until the transcript root deletes it. The analysis remains the canonical source-fingerprint binding, so the intent does not duplicate a fingerprint. Multiple deliberate intent keys may bind to one analysis. This persistence slice adds no route, worker, provider call, quota reservation, retry, or browser behaviour.

The one-note foundation adds `bypassed` for logical consumption, not provider success, plus a nullable unique `generated_document_id` link to one ordinary `GeneratedDocument`. Confirmation adds `confirmed` and a nullable unique batch link. These terminal paths are mutually exclusive. It stores no document content. The consume operation validates the expected transcript plus matching owner/team/transcript/retention and analysis ancestry under its locks before it binds the document. It compares saved clinical state while excluding candidate-template metadata; it requires a live active accessible parent template but uses the immutable submitted version, prompt, sections, and options. It creates normal document/quota/attempt/outbox rows atomically, commits before best-effort publish, and makes no credential or provider call. Deleting the document sets the link to null and leaves the intent bypassed; it does not make the intent reusable. Transcript deletion remains the root cascade. The API/browser actions are complete; bundled generation remains pending.

### `ConsultationSplitDraft`

Editable owner-encrypted titles, ordering, stable topic UUIDs, primary choice, dispositions, and template choices. Bind the draft to the analysis/source fingerprint and mark it stale when sources change. Use `expected_updated_at`; never merge two-tab changes silently. Persist draft topics as child rows rather than a status-heavy JSON coordinator.

### `ConsultationSplitBatch`

The confirmed plan and source/template/PII snapshots are immutable; lifecycle status and per-topic outcomes may change. One analysis may have many batches. Suggested statuses are `generation_queued`, `generating`, `verifying`, `ready`, `partially_ready`, `completed_partial`, and `failed`. Store the confirmed plan encrypted and snapshot sources, PII protection, templates, provider metadata, note options, and order. Persist confirmed topics separately from mutable per-topic outcomes.

### `ConsultationSplitExecution`

One provider-bearing run for `analysis`, `generation`, or `verification`. Each automatic or manual retry creates a new execution UUID. Use it as the outbox source and provider-attempt correlation identity, avoiding existing outbox and attempt uniqueness collisions.

Snapshot adapter, base URL, provider configuration, model, encrypted request and recoverable response, safe errors, usage, and timestamps. Copy the server-owned transcript retention deadline onto each split row; never accept a client deadline. Resolve credentials before marking an attempt submitted.

Clear recoverable successful output after documents commit. Retain bounded encrypted failed or invalid output only until a successful retry or transcript deletion. Never expose it through normal APIs.

### Generated documents

Add nullable batch-topic FK and stable topic UUID to `GeneratedDocument` and owner-only response schemas. Unchanged confirmed topics keep their UUID across batches; combined and new topics receive new UUIDs. The FK proves membership in the confirmed batch while the UUID preserves continuity across batches.

Topic titles are patient-derived. Do not put them in plaintext `generation_snapshot_json`. Because `GeneratedDocument.title` is currently plaintext, store a safe generic title for split children and derive the owner-visible title from encrypted split data, unless a separately reviewed migration encrypts all generated-document titles.

Keep only safe IDs and non-content state in plaintext JSON, outbox, quota, usage, audit, and logs.

All denormalized owner/team/transcript fields are service-validated against the transcript and owner. System administrators never own split content; team membership does not grant content access.

## Dispatch, quota, and usage

Current enums already include generation, ingestion, and template suggestion. Preserve them.

Add `consultation_split_execution` as a dispatch source and distinct analysis, generation, and verification dispatch kinds. Extend `ProviderAttempt` with a split-execution FK and validate owner/team/transcript scope. Add matching attempt and usage feature kinds.

Link one provider call's usage to its execution. Never duplicate bundle usage across child documents.

Reserve separately for analysis, confirmed generation, verification, and each retry. Definite pre-dispatch failures consume no provider quota. Automatic retry uses the original provider snapshot; a clinician retry may resolve the current eligible provider while retaining immutable clinical snapshots.

Do not impose a separate lifetime cap on clinician retries. Apply normal rate limits and quota to every submitted retry, and disable retry until deterministic configuration, authorization, quota, redaction, or stale-source failures are resolved.

Estimate one copy of shared input, every template instruction, and each note's output allowance. Do not multiply transcript input by N. Reject oversized work before submission; never truncate or silently turn the initial generation into several calls.

Extend quota expiry, submitted timeouts, failed dispatch, cancellation, transcript expiry, and user/team deletion for split executions.

## Analysis contract

Input is redacted transcript, redacted Working note, redacted saved dictation, optional successful NLP hints, and available template metadata. Send only template ID, name, description, and actual mode, not full prompts.

Validate bounded JSON, zero to six topics, non-empty bounded titles, one primary when topics exist, valid dispositions, UUID-or-null template IDs, no duplicates, and normal template access.

Do not truncate invalid output. Retry once for transient provider errors, timeouts, malformed JSON, or invalid envelopes. Do not retry quota, credential, configuration, authorization, redaction, or stale-source failures. After failure show **Retry analysis** and **Continue as one note**; never fall back silently.

## Coordinated generation

Use one stable provider-neutral envelope for freeform and structured notes. Each note echoes its topic UUID and mode. Validate identity, mode, content, structured keys, duplicate sections, string types, and size bounds independently.

The prompt includes the redacted sources once; all separate topics and templates; primary, included, and excluded dispositions; sibling boundaries; rules against invention and unrelated cross-contamination; permission for clinically relevant shared facts; and placeholder-preservation rules.

Use the confirmed title as owner-visible title and scope. Do not use prior generated notes as source material.

## Retry and partial success

Allow one automatic generation retry for transient provider failure, malformed output, or invalid note output.

Salvage only when the outer response is trustworthy and outputs map uniquely to requested topic UUIDs. Unknown or duplicate IDs, or an unparseable outer response, invalidate the whole response.

When some notes validate:

1. retain them encrypted on the batch;
2. retry only missing or invalid topics, with all boundaries and immutable valid sibling output as context;
3. if the combined set validates, create all documents together;
4. if it remains partial, verify and persist the valid subset in one transaction;
5. mark `partially_ready`, identify failed topics in the owner UI, and offer **Retry missing notes** and **Keep available notes**.

Never regenerate or overwrite a successful note during recovery. Empty or placeholder-only notes are invalid. If the primary fails, valid secondary notes may remain after verification, but the UI must state the primary failure and must not move its material elsewhere.

**Keep available notes** sets `completed_partial`. Clinician edits to successful notes never feed retries.

## Verification

Use the team's hallucination-check provider when configured; do not require one. Verify after automatic recovery. For a partial batch, verify survivors against the full source, all boundaries and dispositions, and missing topics before exposure.

Verification is fail-open. On provider, quota, timeout, or invalid-output failure, keep the validated generation result as drafts. Use a corrected envelope only after complete validation; otherwise keep the pre-verification result. Never persist part of a verifier envelope. Show that corrections were applied without exposing raw reasoning.

## Workspace and API

Add owner-only operations to queue/reuse analysis, read workspace state, save a draft, confirm a batch, bypass to ordinary generation, retry analysis or missing topics, and accept partial output.

Add every route to `app/api_route_audit.py`; apply owner checks, CSRF, rate limits, safe errors, and `Cache-Control: no-store`.

Put split state in `TranscribeWorkspaceDetail`, shared by REST and SSE. Define one source for the split preference; do not let bootstrap and workspace state disagree.

Create a focused JS controller for analysis, modal state, drafts, confirmation, retry, partial outcomes, and restoration. Add fallback polling because current polling stops outside live capture.

Allow navigation away. Restore state on return and show one in-app completion/attention notification. Send no external notification.

Group repeated batches in the existing navigator, primary first and then confirmed order. Keep the existing editor and per-note copy/export. Do not add batch export in v1.

Select the primary note when a batch completes, or the first surviving note when the primary failed. Allow each generated child to be deleted through the existing generated-document path without deleting its siblings or provenance batch.

Duplicate Generate requests return the existing in-flight operation. After a split confirmation consumes that operation, the browser retires it; a later **Regenerate** creates a new intent and immutable batch. While analysis or review is active, ordinary generation is available only through **Continue as one note**.

## Preference transitions

Turning the preference off prevents new analysis, cancels reserved/unpublished analysis where safe, suppresses submitted results and unconfirmed review, and never cancels confirmed generation. Turning it on may reuse a matching completed analysis and draft. Continue as one note is a per-request bypass, not permanent dismissal.

## Retention, deletion, and privacy

The transcript remains the retention and deletion root. Analyses, drafts, batches, executions, recoverable output, and children inherit its deadline.

Extend all transcript, expiry, user, and team deletion services. Cancel or settle attempts and remove outbox metadata in established lock order. Keep ORM relationships and migration FKs consistent.

Encrypt proposals, drafts, confirmed plans, titles, requests, responses, and recoverable output under the owner's DEK. Never expose their content through logs, audit/usage metadata, or manager/admin views.

## Historical implementation slices (complete; retained for rationale)

Keep the deployment flag off through all slices.

### 1. Gate and preference

Add the deployment flag and typed default-off preference. Update both web preference setters, JS whole-payload persistence, response presentation, `.env.example`, Compose mapping, and operational docs.

Verify defaults, legacy normalization, API round trips, every setter preserving the field, and disabled behaviour.

### 2. Persistence and deletion skeleton

Keep this slice off until both sub-slices pass.

#### 2A. Passive schema and model

Add analysis, draft, batch, draft-topic, batch-topic, outcome, and execution persistence; generated-document links; encrypted field slots; constraints; indexes; ORM relationships; and migration introspection tests. Do not add routes, workers, outbox, quota, provider behavior, or encryption writes.

#### 2B. Lifecycle integration

Add owner/team/transcript consistency checks, source-bound draft staleness, server-copied retention deadlines, tested encryption writes and encrypted persistence tests through the existing content-crypto helpers, generated-child deletion behavior, and all transcript expiry plus user/team deletion paths before routes can create rows. Dispatch, provider attempts, quota, and usage remain in Slice 3.

Verify migration shape, uniqueness, FKs, owner/team scope, encryption, expiry, and deletion.

### 3. Async, quota, and usage foundation

Add dispatch/task mappings, execution-linked attempts and usage, lifecycle reconciliation, cancellation, retry identities, and failure transitions without calling a provider.

Verify two phases and repeated executions do not collide; failed publish, expiry, timeout, and deletion settle correctly; usage is not duplicated.

### 4. Shared provider and redaction runtime

Extract only the safe primitives needed from existing generation. Isolate optional clinical NLP failure from successful redaction without weakening required redaction. Do not copy `_process_generated_document_impl()`.

Run existing generation, suggestion, redaction, manual-PII, credential-before-submit, quota, and provider-snapshot regressions.

### 5. Source-bound analysis backend

Implement fingerprints, encrypted proposals, parsing, primary/dispositions, template validation, caching, stale suppression, and one auto retry behind the disabled flag.

Verify every source combination, empty input, PHI boundary, optional NLP, topic counts/caps, malformed output, templates, concurrent create, duplicate delivery, stale sources, quota, and provider failure.

Slice 5B (pure analysis contract, parser, and prompt builder) is complete. It has no persistence, route, worker, quota, or provider-call behavior; the remaining backend work stays in Slice 5A and the later analysis execution slices.

#### 5D analysis request boundary

Slice 5D0 is implemented. Split executions retain the durable provider configuration snapshot, and the validator binds that snapshot to the config canonically. Its default validator currently binds `config.model_name`.

Slice 5D1a is implemented as a pure boundary. It accepts strict, already-redacted source and candidate snapshots, preserves the canonical EMIS Working-note shape, builds the provider-neutral request and JSON schema, applies the 512-token nominal analysis cap, and performs no persistence or provider call. It also returns a conservative reservation from the exact system/user messages and the request's actual output cap. The current Gemini adapter uses its 30,000-token provider ceiling; 512 is nominal only, so parser and response-size validation remain authoritative.

Slice 5D1b's source-preparation/cache foundation is implemented and independently reviewed. It provides non-persisting redacted current-source preparation, an explicit empty-current/no-historical fallback, a server retention snapshot, an owner/root/current-fingerprint read-only cache lookup, and an existing constructor that preserves encrypted persistence. Slice 5D2c closes the earlier writer-serialization gap; the final pre-submit fingerprint recheck remains mandatory as defence in depth.

Slice 5D1c is implemented as a service-only initial queue/cache boundary. It checks the opt-in before any transcript lookup, redaction, decryption, or provider selection; then it prepares and cache-proves the current source. It reuses ready, not-required, failed, and complete queued/processing rows without retrying. A queued passive row without a valid execution, reserved attempt, and durable outbox fails closed; this slice never attaches work to it. For a new source, one savepoint creates the encrypted analysis source/candidate/provider snapshots, encrypted request/provider execution snapshots, reservation, and outbox. It uses the user-resolved model only to build the canonical config snapshot, commits before best-effort publish, and leaves a failed fast publish pending for the dispatcher. A duplicate insert rolls back only that savepoint, rereads the canonical row, and makes no second reservation or outbox; a stale historical unique conflict remains a safe terminal outcome rather than being revived. It does not resolve credentials, invoke an LLM, claim a task, retry, parse results, create drafts, finalise status, add routes, or add UI.

Slice 5D2a is implemented as a service-only, claim-free pre-submit boundary. It reads the execution's fixed LLM config in a short transaction, closes that transaction before Vault credential resolution, then locks the owner, transcript, ordered dictation rows, analysis execution, parent analysis, reserved attempt, and pending or published dispatch. It binds the encrypted provider snapshots and request to `execution.llm_config_id`, rebuilds the canonical request from the encrypted source and candidate snapshots, and recomputes the complete current source fingerprint from existing rows only without creating an owner key. It repeats the expiry, retention, and reservation checks after that proof. Source, retention, reservation, config, snapshot, request, and credential failures cancel the unsubmitted reservation with safe metadata-only codes; only a pending dispatch is cancelled, while a published row remains as history. A changed source marks the analysis stale. Lost or duplicate workers do not alter work already changed by a winner. A successful result remains queued and reserved and returns with the final transaction and locks active for an immediate later submit transition.

Slice 5D2b is implemented as the one-shot analysis task runtime. It accepts either pending or published outbox state because the task publisher sends to Celery before committing the published marker; a received task is the delivery proof, while cancelled and failed rows remain invalid. It repeats the split preference and source/retention checks under the final queued locks, commits `processing/submitted` before the provider call, and proves the durable submitted attempt again before invoking. A returned response is bounded, encrypted in the execution before parsing, and then finalized into an encrypted proposal and exactly one settled usage event. Invalid output, provider errors, source changes, expiry, and retention-binding failures terminalize safely. Missing, malformed, or incoherent total usage uses conservative settlement. A persisted response recovers without a second provider call; a submitted attempt with no durable response never invokes again. Turning the preference off cancels reserved queued work without credential or provider use, and settles submitted work while suppressing an unconfirmed proposal. The non-atomic provider/database boundary remains deliberate: an uncertain response commit leaves submitted/no-response work in flight for conservative lifecycle handling rather than risking another call. Automatic retry, drafts, batches, generation, verification, routes, and UI remain outstanding.

Slice 5D2c closes the preference-off and transcript-source writer races. The canonical lock order is `User(owner) -> Transcript(root) -> extant PostConsultationDictation rows ordered by id`, followed by any execution/analysis/attempt/outbox row that the worker needs. Split runtime, pre-submit preparation, source preparation/cache, Working-note save/clear/structured update, transcript commit/version snapshot and capture application, post-consultation dictation update/append/create, manual-PII create/update/delete, and successful optional clinical-NLP result commits use that source order. The owner row also serializes every app-preference mutation, including first upsert, clear, normalization repair, and user deletion cascade, with the runtime's final preference check through durable submission. This makes an opt-out wait until that submission commits; it cannot land in the proof-to-submit gap.

Candidate templates remain deliberately outside this transcript-owned lock chain. Their candidate metadata/access snapshot is re-fingerprinted at each final source proof and frozen in the encrypted request. A template change that commits after that proof is a later reusable-configuration change and cannot alter an already frozen request; it does not create a new transcript-content boundary. Failed or absent optional clinical-NLP runs do not enter the fingerprint. Successful redacted runs take the source lock immediately before their durable commit, so a committed hint identity is either visible to the proof or follows the submission boundary. No template-wide or team-wide lock is introduced.

### 6. Workspace review flow

#### 6C2a implemented: effective capability gate

Slice 6C2a exposes one server-derived capability in the initial bootstrap and workspace REST/SSE responses. The shared helper requires the deployment gate, an explicit owner preference, and a normal owning team user; leaders, system administrators, missing users/preferences, and disabled deployments receive `false`. When the effective gate is false, the workspace does not project split analysis or draft state. This slice does not intercept **Generate**, initiate analysis, or add generation behavior.

#### 6C2b accepted: persistence-only logical-Generate intent

Slice 6C2b adds the `ConsultationSplitIntent` model, migration, encrypted selected-template/configuration snapshot, owner idempotency key, retention binding, and deletion/lifecycle wiring. It adds no route, queueing, provider, quota, draft, generation, or browser behavior. This accepted slice is the handoff baseline. Do not treat any later service file as evidence that this slice's workflow exists.

#### 6C2c implemented: atomic intent start

The service-only atomic start is implemented and focused-verified. It creates or replays one owner-scoped logical Generate intent, binds it to the exact reusable analysis, and publishes only a newly created durable dispatch after commit. It has no route, browser, credential-resolution, provider-call, draft, generated-document, generation, retry, or result-consumption behavior.

The intended slice is narrow: for one normal owner, validate the effective split gate and the selected template; prepare the current redacted source through the established boundary; atomically create or replay the owner intent and bind it to reusable analysis; and, for new analysis work, commit the analysis, encrypted execution snapshots, reserved attempt, and deterministic outbox row together. Publish only after commit. A same-key replay must return the winner without new work. Different deliberate keys may share one source analysis and its one analysis execution. This slice must not resolve credentials, call a provider, initialise a draft, create a generated document, consume a proposal, or add routes/browser behavior.

The implementation is in:

- `app/services/consultation_split_intents.py`: `create_or_replay_consultation_split_intent`, `_insert_or_replay_intent`, `_selected_template_generation_snapshot`, and replay helpers.
- `app/services/consultation_split_queue.py`: `_queue_or_reuse_prepared_split_analysis`, an uncommitted queue decision intended to be composed by the atomic service; the existing public `queue_or_reuse_split_analysis` remains a separate commit boundary.
- `tests/test_consultation_split_intents.py`: focused tests for gating, atomic rows, replay, rollback, concurrent same/different-key sharing, publish failure, and template access.

Focused tests prove all of the following:

- one transaction contains the new intent, analysis, execution, reserved provider attempt, and outbox row, with no partial rows after rollback;
- same-key replay returns the original intent and snapshot, even with changed payload, source, template, or preference, and fails closed when its transcript or linked analysis has unavailable or broken scope;
- different keys for the same current source create separate intents but one reusable analysis/execution/attempt/outbox, including concurrent requests;
- concurrent same-key and same-source calls have one winner, no duplicate reservation, and no duplicate outbox;
- a real separate-session preference opt-out committed while preparation is at its unlocked redaction boundary blocks the final atomic write;
- redaction/source preparation commits only its established boundary, while the intent and new analysis work roll back together on later failure;
- no credential resolution, Vault access, provider invocation, draft creation, generation, or retry occurs;
- the existing `queue_or_reuse_split_analysis` and queue/runtime regression suites still pass, including post-commit best-effort publish and failed-publish recovery;
- ownership, retention, deletion, encryption, safe metadata, route-audit implications, and documentation remain consistent with the accepted 6C2b contract.

The implementation decision still needed for this slice is whether an ordinary one-note fallback may use the encrypted selected-template snapshot after that template is deleted or disabled, or must ask for a new choice. Other unresolved product decisions are the rate limit for database-only draft `POST`/`PUT` routes and whether a provider transport/read timeout is eligible for automatic analysis retry; the at-most-once provider boundary currently argues against retrying an uncertain submission.

Documentation audit (6 September 2026): `docs/consultation-splitting-preference.md` and `docs/DatabasePlan.md` describe the verified service-only atomic start, its transaction and replay rules, and its absence of route, worker consumption, credential, provider, draft, and browser behavior. The maintained documents remain the release contract; no Generate interception exists.

#### 6C2d implemented: API-only owner intent start

Slice 6C2d adds `POST /api/v1/transcripts/{transcript_id}/consultation-split-intents` over the verified 6C2c service. Its strict JSON body takes UUID `client_idempotency_key` and UUID `selected_template_id`; malformed UUIDs and extra fields return `422` before the service, including for a known key. The endpoint calls the atomic service once and returns only nullable intent ID, replay flag, and bounded analysis state. It returns `202` while analysis is queued or processing and `200` for terminal, stale, or incomplete state. A syntactically valid same-key request reaches the service before any new gate check and returns the original operation without retargeting it; new-key gate enforcement remains there.

This is API-only. It adds no browser interception, draft or document behavior, credential resolution, provider call, generation, retry, or result consumption.

#### 6C1 implemented: passive existing-draft review/edit modal

Slice 6C1 adds the owner-facing **Review note split** modal for an existing draft already present in the workspace REST/SSE projection. It supports editing topic titles, primary choice, dispositions, and templates, validates titles and primary rules locally, saves through the existing optimistic `PUT` route, and fails closed on conflicts, stale sources, unavailable drafts, malformed proposals, and changed workspace targets. It preserves focus and unsaved-edit safety while the save is in flight. The modal is passive: it does not intercept **Generate**, initiate analysis, initialise a draft, or start generation.

Browser-only restoration polling for an already queued or processing analysis is implemented. It uses the existing workspace GET only while SSE is unavailable, has bounded backoff and retry cycles, and never opens the modal, initializes a draft, or changes ordinary **Generate**. A transcript switch stops polling for the old transcript; polling may restart only for an eligible analysis on the newly active transcript. Analysis initiation from **Generate**, draft initialization from **Generate**, **Continue as one note**, batches, bundled generation, and retry remain outstanding. Bundle generation remains unavailable.

The remaining workflow slices are separate: route/API wiring and the route-audit manifest; browser **Generate** interception and draft initialization; **Continue as one note** ordinary generation; confirmation and immutable batch creation; one-call bundled generation; recovery, partial success, and retries; and verification. None is implemented by 6C2c.

Verify reload, reconnect, two-tab conflict, accessibility, saves, one-topic fallback, preference transitions, and suggestion suppression.

### 7. Historical bundled-generation slice (complete)

Add confirmation snapshots, stable envelope, prompts, mixed-mode validation, reidentification, safe display titles, and transactional complete-batch persistence.

Verify exact IDs, modes and sections, one-copy input, caps, dispositions, primary allocation, templates, no plaintext content, one usage record, and duplicate delivery.

### 8. Historical recovery and partial-success slice (complete)

Add recoverable output, automatic and manual retries, targeted recovery, partial statuses/actions, and provider-success/DB-failure resume.

Verify missing/invalid/empty notes, primary failure, unsafe envelopes, immutable siblings, current-provider manual retry, crash points, settlement, and no duplicate children.

### 9. Historical bundle-verification slice (complete)

Add the split verifier using team checker selection and the same output validator. Support complete and partial batches and all fail-open outcomes.

Verify allocation, unsupported facts, dispositions, contradictions, corrections, invalid fallback, configuration/provider/quota failure, and no raw reasoning exposure.

### 10. Historical hardening and rollout-evidence slice (implementation complete)

The implementation checks and synthetic evaluation are recorded in the dated Phase 10 evidence. Keep the flag off until an independent release reviewer accepts that record and the rollout-only checks above are complete.

Update `app/api_route_audit.py`, `docs/api.md`, `docs/DatabasePlan.md`, `docs/transcript-capture.md`, `docs/transcribe_brief.md`, `docs/security.md`, `docs/testing.md`, root README where needed, and this status. Run:

```bash
python .github/scripts/check-operational-docs.py
.venv/bin/pytest -q <focused targets>
```

## First-release non-goals

- No extra transcripts or disease entities.
- No physical transcript slicing.
- No mandatory clinical NLP.
- No trust in provider-returned template IDs.
- No full template prompts during analysis.
- No change to normal `/generate-output` semantics.
- No ordinary template-note or split-child regeneration.
- No batch copy/export.
- No generated-note edits as source.
- No manager or administrator content access.

## Handoff: remaining work (10 September 2026; source-only fix verified 11 September 2026)

The feature is code-complete. The worktree holds all changes uncommitted; nothing is staged. Keep the deployment gate false until rollout-only checks and independent review finish.

### Resolved source-only materialization defect

Working-note-only and dictation-only consultations are ratified inputs (this plan: "Accept any non-empty combination of transcript, Working note, and saved dictation"). Before this fix, bundled materialization had no transcript version for these sources. `GeneratedDocument.transcript_version_id` is non-nullable, while the truthful analysis keeps `transcript_version_id` and `redaction_run_id` null.

The design below is now implemented and verified:

1. Added nullable `consultation_split_batches.materialization_transcript_version_id`, with a PostgreSQL-safe FK name, in a migration after `q6r7s8t9u0v1`; the ORM relationship is explicit.
2. In `consultation_split_confirmation`, while source locks are held and after freshness checks, the existing transcript-version snapshot helper runs with `allow_empty=True, mark_transcript_ready=False`, and the returned ID is bound to the batch. Historical rows remain nullable, and `analysis.transcript_version_id`/`redaction_run_id` retain their truthful null semantics for source-only inputs.
3. In the generation runtime, the batch-bound version is required and validated against the same root, owner, and live retention, then used on every child `GeneratedDocument`. The worker never snapshots late; the encrypted batch snapshot remains the provider source of truth.

Verified coverage includes the Working-note-only and dictation-only service paths and the Playwright harness at `tests/test_consultation_split_browser.py`; the analysis version and redaction run stay null while the batch gains an empty encrypted version; post-confirmation source changes cannot retarget the batch; missing or wrong-root bindings fail before provider submission and document creation; transcript-root deletion cascades; and the isolated migration roundtrip passes. The migration, API, and maintained consultation-splitting documentation record the binding. Final checks are recorded in [docs/consultation-splitting-release-evidence-2026-09-10.md](docs/consultation-splitting-release-evidence-2026-09-10.md).

### Validation state at handoff

- Focused source-only checks: 6 passed.
- Full split suite: 415 passed (7 known deprecation warnings).
- Full suite: 2104 passed (25 known deprecation warnings).
- Operational documentation check: 45 files checked successfully; `git diff --check` is clean.
- An attempted debugging hypothesis list for an early E2E confirmation 409 was rejected; real cause was null topic template IDs in the harness, since fixed.
- Broad templates.py refactor was audited as required and regression-tested.
- Remaining rollout-only checks: real Vault/broker/Beat/deployment migration, real provider calls, manual checkbox flows not covered by Playwright, and independent release-review sign-off.

## Definition of done

With both gates enabled, a clinician can press Generate, receive a redacted source-bound proposal, review at most six topics, choose one primary topic and each disposition/template, and confirm a reproducible batch.

One initial provider call produces the set. Safe validation, recovery, optional verification, and owner encryption protect complete or safely partial results. Notes appear as ordinary editable documents grouped by batch. Existing generation, Quick Actions, follow-ups, ownership, retention, deletion, provider, quota, outbox, redaction, and structured-output contracts continue to pass.
