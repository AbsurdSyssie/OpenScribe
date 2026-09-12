# JSON API Behavior

OpenScribe's canonical programmatic interface is versioned under `/api/v1`. The generated OpenAPI document at `/openapi.json` is the authoritative request/response schema for the running build; this document records access tiers, route groups, cross-cutting behavior, and lifecycle contracts that are easy to lose in generated schemas.

In production, `/docs`, `/redoc`, and `/openapi.json` default to full system-administrator authentication unless `PUBLIC_API_DOCS=true` is explicitly configured.

## Maintenance rule

Every new or removed `/api/v1` route must update:

- the FastAPI route/schema implementation;
- `app/api_route_audit.py`;
- focused authorization/behavior tests;
- this route-group index when the public surface changes;
- the relevant feature documentation and root README when user-facing entry points change.

Run:

```bash
./.venv/bin/python scripts/audit_api_auth.py
```

The audit compares the live FastAPI route inventory with its manifest and probes negative access scenarios. It exits non-zero for missing manifest entries or incorrect auth behavior.

## Error envelope

Non-2xx JSON responses use:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed",
    "details": {
      "issues": []
    }
  }
}
```

`details` is optional and must remain bounded and non-sensitive. Raw provider responses, credentials, cookies, transcript text, prompts, dictation, generated content, PII values, and uploaded audio do not belong in error payloads.

Common authorization responses:

- `401 unauthorized`: no valid session;
- `403 onboarding_incomplete`: onboarding session used on a full-access route;
- `403 mfa_required`: pending-MFA session used on a full-access route;
- `403 forbidden`: valid full session without the required role/scope;
- `404 not_found`: missing object and many cross-owner lookups where existence should not be disclosed.

Rate limiting uses `429 rate_limited`, `Too many requests`, and `Retry-After`. Provider quota denial is not a route-rate-limit condition: internal `quota_disabled`/`quota_exceeded` outcomes are returned to owners as the bounded public code `quota_exceeded` without allowance/usage/reset metadata and should not be automatically retried.

## Authentication and CSRF

Browser/API authentication uses the opaque `openscribe_session` cookie. The database stores only its hash and explicit auth/session state.

Access tiers used by the route audit:

- `public`: no session required;
- `authenticated`: any valid session, including onboarding or pending MFA where explicitly allowed;
- `full`: completed onboarding and MFA/trusted-device requirements;
- `manager`: full system administrator or own-team leader;
- `system_admin`: full system-administrator session;
- `local_debug`: localhost seeded development account plus owner restrictions.

Unsafe `/api/v1` requests carrying session or trusted-device cookies require:

- a session-bound `X-CSRF-Token`;
- a matching `Origin` or `Referer`;
- the normal authentication/authorization dependency.

Safe `GET`, `HEAD`, and `OPTIONS` requests do not require CSRF. Public unsafe auth/account-request endpoints remain callable without CSRF only when no cookie-backed authority is present.

See [auth.md](auth.md) and [security.md](security.md).

## Public and partial-session routes

### Public auth/account routes

- `POST /api/v1/auth/login`
- `POST /api/v1/auth/logout`
- `POST /api/v1/auth/password-reset/request`
- `POST /api/v1/auth/password-reset/confirm`
- `POST /api/v1/auth/account-activation/confirm`
- `POST /api/v1/account-requests`

Password-reset request is generic for existing/missing users when mail is enabled. When mail is disabled it returns `503 mail_transport_disabled` so clients can direct the user to manager-assisted recovery.

Public account requests are deduplicated by normalized email plus normalized requested-team name while pending, with a database partial unique index protecting concurrent submissions. A request for an existing normalized user email is not created. All three outcomes return `202 Accepted` with `{"message":"If the request is eligible, it has been submitted for review."}` so status and response content do not disclose account or request existence. This privacy change replaces the earlier `201 AccountRequestDetail` public response; clients must not depend on a public request ID or echo of submitted fields.

### Valid-session auth routes

- `POST /api/v1/auth/mfa/totp`
- `GET /api/v1/auth/me`
- `GET /api/v1/auth/trusted-device`

Pending-MFA sessions can use the TOTP/current-user/logout/trusted-device subset. Trusted devices never authenticate independently; they only allow a correct password login to skip TOTP while the server-side record remains valid and within the 24-hour MFA freshness window.

Authentication responses expose the next browser destination in `redirect_to`: onboarding sessions use `/onboarding`, pending-MFA sessions use `/mfa/challenge`, full system-administrator sessions use `/admin`, and full normal-user/team-leader sessions use `/workspace`.

### Onboarding routes

- `POST /api/v1/onboarding/password`
- `POST /api/v1/onboarding/totp/start`
- `POST /api/v1/onboarding/totp/verify`
- `POST /api/v1/onboarding/recovery-codes`
- `POST /api/v1/onboarding/skip-recovery-codes`

Onboarding sessions cannot use normal content/provider/management routes.

## Management routes

### Account-request review

Manager routes:

- `GET /api/v1/account-requests`
- `POST /api/v1/account-requests/{request_id}/approve`
- `POST /api/v1/account-requests/{request_id}/reject`

Leaders are restricted to matching requests for their own team. System administrators can review across teams.

### User management

Manager routes:

- `POST /api/v1/users`
- `GET /api/v1/users`
- `POST /api/v1/users/{user_id}/send-activation`
- `POST /api/v1/users/{user_id}/send-password-reset`
- `POST /api/v1/users/{user_id}/send-account-recovery`
- `POST /api/v1/users/{user_id}/break-glass-password-reset`
- `POST /api/v1/users/{user_id}/break-glass-account-recovery`
- `POST /api/v1/users/{user_id}/reset-mfa`
- `POST /api/v1/users/{user_id}/suspend`
- `POST /api/v1/users/{user_id}/reactivate`
- `DELETE /api/v1/users/{user_id}`

Deprecated `recover-password` and `recover-account` routes return `410 deprecated_recovery_endpoint`.

Leader scope:

- own team only;
- non-system-admin targets only;
- no manager self-suspend/reactivate/delete;
- current protected-account checks remain authoritative.

Suspension is reversible and blocks access. Reactivation currently forces password-change onboarding and re-establishment of MFA trust. Deletion is immediate hard delete with implemented cascades/cleanup and no undo path.

Break-glass routes require policy eligibility, the manager's current TOTP code, a reason, confirmation that email is unavailable, and metadata-only security audit recording. The returned temporary password is one-time display material and only its hash is persisted.

### Teams

System-admin-only:

- `POST /api/v1/teams`
- `GET /api/v1/teams`

`default_retention_days` is system-admin-managed policy constrained to `1..MAX_RETENTION_DAYS` (default maximum 90). Transcript creation snapshots server-owned team retention; transcript payloads cannot extend it.

Quota administration is currently browser-only under CSRF-protected `/admin` member forms. There is no JSON quota-management API under `/api/v1`. Quota policy/usage remains system-admin-only metadata.

## STT configuration and selection

System-admin provisioning:

- `GET /api/v1/stt-configs`
- `GET /api/v1/stt-configs/{config_id}`
- `POST /api/v1/stt-configs/inspect`
- `POST /api/v1/stt-configs/{config_id}/inspect`
- `POST /api/v1/stt-configs/drafts`
- `POST /api/v1/stt-configs/{config_id}/finalize`
- `POST /api/v1/stt-configs/{config_id}/replace-credential`
- `POST /api/v1/stt-configs`
- `DELETE /api/v1/stt-configs/{config_id}`

Manager selection:

- `GET /api/v1/stt-selection`
- `GET /api/v1/stt-selection/options`
- `POST /api/v1/stt-selection`
- `DELETE /api/v1/stt-selection`

Selection purpose supports at least `conversation` and `post_consultation_dictation`. Leaders can select/clear ready active options only for their own team and cannot provision/reveal/replace credentials.

Current adapter families include `openai_cloud`, `openai_compatible_rest`, `elevenlabs_speech_to_text`, and `generic_rest`, with provider-specific behavior described in [stt-config.md](stt-config.md).

Credential rules:

- raw credentials are written to Vault, not provider rows;
- responses expose bounded status/`has_secret`, never raw credentials or unrestricted Vault references;
- create/update supports explicit keep/replace/remove semantics subject to provider auth requirements;
- required-auth draft/revision inheritance copies the credential to a draft-owned versioned Vault path before the draft commit—it does not alias the active config's secret reference;
- replacement/removal/deletion/revision cleanup uses durable cleanup intents and live-reference guards;
- provider credential fingerprints are server-side non-reversible HMAC values used for duplicate warning, not authentication.

Queued ingestion snapshots the resolved provider/config/model/contract metadata so later team edits do not retarget existing jobs.

## LLM configuration, selection, and preferences

System-admin provisioning:

- `GET /api/v1/llm-configs`
- `POST /api/v1/llm-configs/inspect`
- `POST /api/v1/llm-configs/{config_id}/inspect`
- `POST /api/v1/llm-configs/drafts`
- `POST /api/v1/llm-configs/{config_id}/finalize`
- `POST /api/v1/llm-configs/{config_id}/replace-credential`
- `POST /api/v1/llm-configs`
- `DELETE /api/v1/llm-configs/{config_id}`

Manager team policy:

- `GET /api/v1/llm-selection`
- `GET /api/v1/llm-selection/options`
- `POST /api/v1/llm-selection`
- `DELETE /api/v1/llm-selection`

System-admin hallucination-check policy:

- `GET /api/v1/hallucination-check-selection`
- `POST /api/v1/hallucination-check-selection`
- `DELETE /api/v1/hallucination-check-selection`

Full-user preferences:

- `GET /api/v1/llm-preference`
- `POST /api/v1/llm-preference`
- `DELETE /api/v1/llm-preference`
- `GET /api/v1/app-preferences`
- `POST /api/v1/app-preferences`
- `DELETE /api/v1/app-preferences`

`template_suggestions_enabled` is an owner-scoped, persisted setting. It defaults to `true`; only an explicit `false` disables it. It controls whether the browser or API may create a template-suggestion job. See [template-suggestion-preference.md](template-suggestion-preference.md).

`split_consultations_into_separate_notes` is an owner-scoped, persisted setting. It defaults to `false`; only an explicit `true` enables it. `CONSULTATION_SPLITTING_ENABLED` controls whether the workspace shows the setting. The owner transcribe workspace REST/SSE response includes the server-derived `consultation_splitting_enabled` boolean; the initial browser bootstrap uses `consultationSplittingEnabled`. Both are true for an owning team user or team leader when the deployment gate and persisted preference are true. Leadership grants no access to another user's transcript-derived content, and system administrators remain excluded. With both gates enabled, the owner-only `POST /api/v1/transcripts/{transcript_id}/consultation-split-analysis` queues or reuses the source-bound analysis and returns only bounded lifecycle state and validated proposal topics.

`POST /api/v1/transcripts/{transcript_id}/consultation-split-intents` starts the verified durable Generate intent. Its strict JSON body requires UUID `client_idempotency_key` and UUID `selected_template_id`; malformed UUIDs and unknown fields return `422`, even when the key names an existing intent. The safe response contains only nullable `intent_id`, `idempotency_replayed`, and the bounded analysis projection. It returns `202` while analysis is queued or processing, otherwise `200`. For a syntactically valid same-owner key, replay happens before the current gate or changed payload is considered: it returns the original operation and does not retarget it. A new key remains subject to the service gate. The route is owner-only, CSRF/origin protected, no-store, and uses both existing LLM generation rate limits.

`POST /api/v1/transcripts/{transcript_id}/consultation-split-intents/{intent_id}/continue-as-one-note` has no request body. It consumes that exact transcript-bound intent as one ordinary queued note. A new consume returns `202`; a replay returns `200`. Its dedicated response contains only `intent_id`, `idempotency_replayed`, nullable `document`, and `consumed_document_deleted`. A replay after the linked document was deleted returns `document: null` and `consumed_document_deleted: true`; it never creates a replacement. The service validates the nested transcript and intent under its canonical locks. Owner/path/expiry failures remain non-disclosing, while leaders and system administrators remain forbidden. The route is owner-only, CSRF/origin protected, no-store, and uses both existing LLM generation transport limits; provider quota remains the business limit.

When the effective split gate is on, the browser intercepts **Create**: it captures the transcript/template, saves the Working note and dictation, rechecks the active transcript, and calls this route with a browser UUID. It does not call `/generate-output`. Queued and processing work uses the existing accessible review status plus the existing SSE-first restoration poller. After split confirmation, the Output selector shows client-only topic placeholder pills and the existing generation screen; SSE workspace snapshots replace stable slots with owner-projected documents as they become ready. No placeholder creates a database row or a second LLM call. For the matching browser-started ready analysis, the browser initializes or reuses the draft through the existing draft `POST`, then refreshes the guarded workspace and opens the matching modal once. Restored workspace drafts remain passive. A `not_required` result means the analysis found fewer than two topics, so the browser immediately consumes its own durable intent through `continue-as-one-note` with the template frozen when Generate was pressed. `failed`, `stale`, `incomplete`, and draft-initialization errors still end without automatic generation and show safe guidance. A deliberate Create can retry a failed draft initialization; a changed template starts a new intent.

`POST`, `GET`, and `PUT /api/v1/transcripts/{transcript_id}/consultation-split-draft` initialise/reuse, read, and fully replace the review draft. A draft can be made only from the current encrypted `ready` proposal. Every non-null proposal template ID must appear in that analysis's encrypted frozen candidate snapshot; a missing or malformed snapshot makes the proposal unavailable. `GET` never creates or changes it; it reports a stale source without writing. `PUT` requires the exact `expected_updated_at`, replaces zero to six ordered topics atomically, and rejects conflicting two-tab saves. Topic titles are owner-encrypted. The API returns only draft/analysis IDs, status, timestamps, and topic UUID/title/order/primary/disposition/template IDs.

After the latest batch reaches `ready`, `completed_partial`, or `failed`, a deliberate new Generate action may reopen the same review draft. Reopening changes only that mutable draft and advances its edit timestamp; every earlier confirmed batch and generated note remains immutable. Confirming the reopened draft uses the new intent and creates another independent batch.

`POST /api/v1/transcripts/{transcript_id}/consultation-split-draft/confirm` accepts only `{intent_id, expected_updated_at}`. It is owner-only and CSRF-protected. A new confirmation returns `202`; a same-intent replay returns `200` before it rechecks the timestamp or draft. Confirmation requires a current ready analysis, an active draft, two to six `separate_note` topics with exactly one primary, current redacted sources, active accessible template parents at the exact selected versions, and an eligible selected LLM configuration/model. In one transaction it creates the encrypted immutable batch/topic/outcome tree, encrypted canonical adapter request, write-once provider snapshot, generation execution/reservation, and deterministic outbox row, then marks the draft and intent confirmed. Queue construction failure rolls that transaction back. Its `no-store` response contains only batch ID, queued status, counts, timestamps, and replay state; it creates no document or provider call. A trustworthy mixed provider response retains only independently validated encrypted outcomes; an ambiguous envelope fails the full batch without salvage. At most one automatic recovery follows a durable parsed response, using the frozen original provider snapshot. Submitted work with no durable response never auto-retries. `POST /api/v1/transcripts/{transcript_id}/consultation-split-batches/{batch_id}/regenerate` takes only a UUID idempotency key. For a terminal batch it creates a fresh immutable batch from its frozen plan, clinical/source/template/PII/options snapshots and topics; it does not reopen analysis or review. It resolves the current eligible provider at queue time and returns only new batch/execution IDs and replay state. `POST /api/v1/transcripts/{transcript_id}/consultation-split-batches/{batch_id}/retry-missing-notes` uses the generation transport limit, creates one fresh current-provider execution/reservation/outbox winner, requests failed topics only, and retains accepted siblings as immutable context. `POST .../keep-available-notes` is owner-only, no-store, database-only, and idempotent: it materializes validated survivors once and sets `completed_partial`. It is rejected while a recovery is active, does not use an LLM limiter, and does not submit provider work. Workspace REST/SSE exposes only content-safe batch status, counts, `primary_failed`, server action flags, and safe document/execution IDs. When the primary failed, the browser warns before Keep and, after `completed_partial`, states that it selected a surviving secondary note. All resulting notes remain clinician-review drafts.

Automatic recovery requires a durably persisted provider response, including malformed durable response; it uses the frozen original provider snapshot. Submitted work with no durable response never auto-retries.

New manual Retry missing notes and Keep available notes enforce the deployment and owner gate before transcript or batch lookup. When either gate is off they return `403 consultation_split_disabled` without creating a quota reservation, outbox row, provider call, or document. They do not stop already durable worker or lifecycle work.

Before materializing a complete structured survivor set, the optional independently selected team hallucination checker may make one durable verification call. It receives only immutable encrypted batch snapshots and validated outputs, uses the existing exact-substring patch format, and stores a corrected candidate separately from accepted output. Missing selection, mixed/freeform output, quota/credential/expiry/provider failure, timeout, or malformed patches fail open: originals remain usable clinician-review drafts. Workspace REST/SSE exposes only verification lifecycle status and correction count; it never exposes verifier text, patches, or reasoning. Keep/retry actions are disabled while verification is active.

A trusted partial initial response decides and queues automatic recovery before verification. Verification runs only after that recovery completes or fails definitively, then uses the complete recovered set or stable survivors. It never queues while recovery is active.

The workspace REST/SSE payload may include the same read-only draft state. The browser permits Confirm only for the browser-owned intent from the matching Generate action; a restored draft without that unambiguous intent is review-only. Continue-as-one-note remains separate. See [consultation-splitting-preference.md](consultation-splitting-preference.md).

Current adapters include `openai_chat`, `ollama_chat`, `bedrock_chat`, and `gemini_enterprise`. Model discovery has no generic built-in LLM fallback list: a non-auth discovery failure can require a manually entered model, while definitive credential failures create neither draft nor secret. Provider-specific discovery/finalization behavior is in [llm-providers.md](llm-providers.md).

Required-token revisions copy inherited credentials to draft-owned versioned Vault paths. They do not share the active root reference. Credential removal/replacement and retired references use the durable cleanup path rather than relying on a delete-before-database-commit sequence.

Ready/active/default-model rules are enforced server-side. Team policy carries the allowed model subset/default; user preference is validated against that policy and falls back to the team default when stale/invalid.

Gemini Enterprise uses project/location/capacity plus ADC or Vault-backed service-account JSON. It rejects bearer-token/base-URL semantics and never exposes credential JSON/access tokens. See [gemini-enterprise-setup.md](gemini-enterprise-setup.md).

## De-identification and clinical NLP

System-admin provider/assignment routes:

- `GET /api/v1/deidentification-providers`
- `POST /api/v1/deidentification-providers`
- `POST /api/v1/deidentification-providers/inspect`
- `DELETE /api/v1/deidentification-providers/{provider_id}`
- `GET /api/v1/deidentification-provider-assignments`
- `POST /api/v1/deidentification-provider-assignments`
- `DELETE /api/v1/deidentification-provider-assignments`

Manager own-team selection:

- `GET /api/v1/deidentification-selection`
- `GET /api/v1/deidentification-selection/options`
- `POST /api/v1/deidentification-selection`
- `DELETE /api/v1/deidentification-selection`
- `GET /api/v1/clinical-nlp-selection`
- `GET /api/v1/clinical-nlp-selection/options`
- `POST /api/v1/clinical-nlp-selection`
- `DELETE /api/v1/clinical-nlp-selection`

The historical `deidentification` provider object can advertise PII-redaction, clinical-NLP, or both capabilities. Remote clinical endpoints receive redacted source by default; unredacted submission is restricted to explicitly configured local/private endpoint behavior. The built-in native Presidio path remains the PII-redaction fallback; clinical NLP has no built-in fallback.

Admin inspection uses synthetic caller-supplied test text and can return raw synthetic provider JSON for contract testing. Runtime patient-content provider responses are not exposed through admin routes or persisted raw.

## Templates, Quick Actions, and Smart Phrases

### Templates

- `GET /api/v1/templates/available`
- `GET /api/v1/templates/team`
- `POST /api/v1/templates/team`
- `DELETE /api/v1/templates/team/{template_id}`
- `GET /api/v1/templates/personal`
- `POST /api/v1/templates/personal`
- `DELETE /api/v1/templates/personal/{template_id}`
- `POST /api/v1/templates/export`
- `POST /api/v1/templates/import/preflight`
- `POST /api/v1/templates/import`

### Quick Actions

- `GET /api/v1/quick-actions/available`
- `GET /api/v1/quick-actions/team`
- `POST /api/v1/quick-actions/team`
- `DELETE /api/v1/quick-actions/team/{quick_action_id}`
- `GET /api/v1/quick-actions/personal`
- `POST /api/v1/quick-actions/personal`
- `DELETE /api/v1/quick-actions/personal/{quick_action_id}`
- `POST /api/v1/quick-actions/export`
- `POST /api/v1/quick-actions/import/preflight`
- `POST /api/v1/quick-actions/import`

### Smart Phrases

- `GET /api/v1/smart-phrases/available`
- `GET /api/v1/smart-phrases/personal`
- `POST /api/v1/smart-phrases/personal`
- `PATCH /api/v1/smart-phrases/personal/{smart_phrase_id}`
- `DELETE /api/v1/smart-phrases/personal/{smart_phrase_id}`
- `POST /api/v1/smart-phrases/personal/{smart_phrase_id}/used`
- `POST /api/v1/smart-phrases/export`
- `POST /api/v1/smart-phrases/import/preflight`
- `POST /api/v1/smart-phrases/import`

Scope:

- normal users manage caller-owned personal assets;
- leaders manage authorized team Templates/Quick Actions in their current team;
- Smart Phrases are personal only;
- system administrators do not own normal user/team generation assets.

Bundle contracts are published under `app/static/schemas/`. Bundles carry portable content, never ownership/team/creator/UUID/version/active/usage authority. Limits are 1 MiB and 100 entries. Preflight is read-only; commit reparses/re-authorizes/revalidates the original file and creates the selected subset atomically.

Structured EMIS templates use only: `problem`, `history`, `family_history`, `social_history`, `examination`, `comment`, `tasks`, `investigations`.

## Transcript, workspace, and generated-content routes

Full-user owner-scoped routes include:

- `POST /api/v1/transcripts`
- `POST /api/v1/transcripts/start`
- `GET /api/v1/transcripts`
- `GET /api/v1/transcripts/{transcript_id}`
- `PATCH /api/v1/transcripts/{transcript_id}`
- `DELETE /api/v1/transcripts/{transcript_id}`
- `POST /api/v1/transcripts/{transcript_id}/commit`
- `POST /api/v1/transcripts/{transcript_id}/audio-chunks`
- `POST /api/v1/transcripts/{transcript_id}/finalize-live-capture`
- `POST /api/v1/transcripts/{transcript_id}/audio-file`
- `POST /api/v1/transcripts/{transcript_id}/retry-audio-file`
- `GET /api/v1/transcribe/workspace`
- `GET /api/v1/transcribe/workspace/stream`
- `POST /api/v1/transcribe/stt-health/recheck`

Owner working-note/dictation/context routes:

- `GET|PATCH|DELETE /api/v1/transcripts/{transcript_id}/working-note`
- `GET|PATCH /api/v1/transcripts/{transcript_id}/post-consultation-dictation`
- `POST /api/v1/transcripts/{transcript_id}/post-consultation-dictation/preview-audio-file`
- `POST /api/v1/transcripts/{transcript_id}/post-consultation-dictation/audio-file`
- `POST /api/v1/transcripts/{transcript_id}/quick-action-context/preview-audio-file`

Owner PII/redaction routes:

- `POST /api/v1/transcripts/{transcript_id}/manual-pii`
- `DELETE /api/v1/transcripts/{transcript_id}/manual-pii/{entity_id}`
- `POST /api/v1/transcripts/{transcript_id}/pii-entities/reveal`

Owner generation/document routes:

- `POST|GET /api/v1/transcripts/{transcript_id}/template-suggestion`
- `POST /api/v1/transcripts/{transcript_id}/generate-output`
- `POST /api/v1/transcripts/{transcript_id}/generate-followup`
- `POST /api/v1/transcripts/{transcript_id}/run-quick-action`
- `GET /api/v1/transcripts/{transcript_id}/generated-documents`
- `PATCH /api/v1/generated-documents/{generated_document_id}`
- `DELETE /api/v1/generated-documents/{generated_document_id}`
- `POST /api/v1/generated-documents/{generated_document_id}/regenerate`
- `GET /api/v1/generated-documents/{generated_document_id}/redaction-debug` (localhost seeded-development owner only)

Cross-cutting transcript rules:

- system administrators cannot own transcripts;
- owner/team are derived/validated server-side;
- only `whole_file` and `live_chunked` are persisted ingestion modes;
- team retention is snapshotted server-side;
- expired roots are unavailable before asynchronous physical cleanup;
- cross-owner access fails without content disclosure;
- transcript titles remain plaintext metadata; designated content fields are encrypted at rest;
- owner API responses return authorized plaintext fields and use `Cache-Control: no-store`;
- deletion is immediate and cascades through transcript-derived children/queued cleanup.

See [transcript-capture.md](transcript-capture.md), [live_stt.md](live_stt.md), and [workspace.md](workspace.md).

## Audio ingestion

Whole-file defaults:

- individual raw upload: 200 MiB;
- individual normalized duration: four hours;
- burst: one request per five seconds;
- daily: 100 uploads;
- hourly aggregate: 200 MiB and four hours.

Live-chunk uploads are limited to 24 MiB each. Whole-file, dictation, and live-chunk routes use bounded readers that reject the first byte over their applicable limit before queueing or transcription. These application limits do not stop a reverse proxy from accepting a large request body first; set matching request-body limits at the public proxy/CDN.

Live defaults:

- one chunk request per second;
- one hour aggregate duration per rolling hour;
- measured chunk maximum around 30 seconds.

The server measures/probes audio rather than trusting declared duration for enforcement/accounting. Accepted jobs snapshot STT execution metadata and create task-dispatch/quota metadata transactionally.

Whole-file source audio required for asynchronous processing/retry is stored under a bounded Vault reference, not a new PostgreSQL audio blob. Its 24-hour expiry starts at the original Vault write and is preserved across retries. Successful processing and transcript deletion clear or durably queue cleanup sooner. At or after the deadline, transcript detail returns `latest_ingestion_retry_expired=true`, `latest_ingestion_retry_available=false`, and retry requires a fresh upload without exposing storage details.

Workers normalize to 16 kHz mono PCM WAV, resolve the snapshotted credential, mark the provider attempt submitted only at dispatch, call the adapter under configured timeouts, encrypt result text, settle usage, and reconcile transcript/job state.

## Working note and dictation

Working note:

- one owner transcript note in `freeform` or `structured` mode;
- mode locks on first non-empty save and unlocks when cleared;
- optimistic concurrency uses `expected_updated_at`;
- generation snapshots the saved note used for the request;
- source is redacted before LLM dispatch.

Post-consultation dictation:

- preview audio returns editable text without persistence;
- saved audio adds immutable segments to a transcript-owned aggregate;
- edited combined text is the authoritative generation source when present;
- an intentionally empty edited value suppresses dictation fallback;
- quick-action context preview is transient and populates the ordinary context field rather than a separate stored dictation row.

## Generation lifecycle

Generation endpoints return `202 Accepted` with a queued `generated_documents` row. Creation commits:

- source/generated-document metadata;
- encrypted source/request snapshots as applicable;
- provider quota reservation;
- deterministic durable task-dispatch outbox row.

The worker claims the document/attempt, resolves the credential before marking submission, dispatches once, validates/parses provider output, reidentifies allowed placeholders after redaction, encrypts stored output, records safe metadata, and transitions to `ready` or `failed`.

Template, follow-up, and Quick Action requests are owner-only. Saved transcript, working note, and dictation sources are redacted before provider dispatch. Static reusable asset instructions are treated as configuration and must not contain patient content.

Generation limits default to `20/3 minutes` and `200/day` per authenticated owner bucket. Provider quotas are separate authoritative accounting controls.

Generated-document edits use optimistic concurrency (`expected_updated_at`). Deleting an originating Template/Quick Action does not invalidate already queued/generated work because required snapshots are retained and source references can be cleared.

Regenerating a template note creates a new immutable child revision. It uses the original document's frozen transcript, template, Working-note, and dictation snapshots, plus the latest saved clinician-edited note. It resolves the user's current LLM policy for the new attempt. Regenerating a split note uses its confirmed batch/topic snapshots, never the current transcript or dictation. A split topic may therefore have several generated-document revisions; each keeps the stored title `Consultation split note`, and the owner-only projection supplies the topic title. Only one queued or processing revision may exist per lineage. Optional `steering_text` is limited to 4,000 characters. `steering_preset` accepts `more_detail` or `less_detail`; the server converts it to its fixed instruction and encrypts the combined steering with the owner's content key. Follow-up and Quick Action regeneration retain their existing current-source behavior.

Bundled split generation also returns one short title for the overall consultation. If the transcript still has the default `Untitled session` title, the owner-facing session title is filled from that overall result; an existing custom session title is preserved. The split documents retain their generic stored title and topic-title projection.

## Template-suggestion lifecycle

When the owner has not explicitly disabled suggestions and their transcript reaches 1,200 characters, `POST .../template-suggestion` atomically creates or returns its sole suggestion job. Disabled preferences return `not_eligible` without creating a job, redaction run, quota reservation, outbox row, or provider call. Turning the setting off cancels queued jobs, their pending dispatches, and reserved quota in the same transaction; a request already submitted to a provider cannot be recalled. The job stores the first eligible excerpt in an owner-encrypted field. It snapshots accessible template metadata and provider execution metadata, then commits its quota reservation, provider attempt, and deterministic outbox row together. The endpoint returns without waiting for the provider. `GET .../template-suggestion` returns `not_eligible`, `queued`, `processing`, `completed`, or `failed`; completed responses contain either one currently accessible template or no suggestion.

The worker applies the normal de-identification path and saved manual PII rules before provider dispatch. It validates the model response, rejects inaccessible or invented template IDs, and returns the current database template name. Low confidence and malformed or failed provider responses produce no suggestion. Accepting a suggestion only changes the existing browser template selection; it never starts note generation.

## Security and caching

- `/api` responses are no-store/no-cache.
- API cookies remain `HttpOnly`; browser JavaScript receives CSRF state only through server-rendered data.
- Provider credentials and Vault references are never returned through normal config/content APIs.
- Provider inspection and model discovery bound untrusted response bodies. OpenAPI inspection is limited to 2 MiB and model discovery to 1 MiB. Ollama generation streams are limited to 8 MiB and 10,000 fragments.
- Provider base URLs reject known cloud metadata hostnames and addresses. `localhost` and private/loopback provider addresses remain supported for local providers; see [security.md](security.md).
- Audit/usage/attempt/outbox rows contain metadata only.
- Sensitive values must not be added to validation details or support diagnostics.
- Browser invalid non-API routes redirect by current auth state; invalid `/api/*` routes remain JSON `404` and are never redirected to HTML.

## Related references

- [auth.md](auth.md)
- [security.md](security.md)
- [environment.md](environment.md)
- [stt-config.md](stt-config.md)
- [llm-providers.md](llm-providers.md)
- [transcript-capture.md](transcript-capture.md)
- [workspace.md](workspace.md)
- [testing.md](testing.md)
