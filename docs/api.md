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

Public account requests are deduplicated by normalized email plus normalized requested-team name while pending. A request for an existing normalized user email is rejected.

### Valid-session auth routes

- `POST /api/v1/auth/mfa/totp`
- `GET /api/v1/auth/me`
- `GET /api/v1/auth/trusted-device`

Pending-MFA sessions can use the TOTP/current-user/logout/trusted-device subset. Trusted devices never authenticate independently; they only allow a correct password login to skip TOTP while the server-side record remains valid and within the 24-hour MFA freshness window.

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

- `POST /api/v1/transcripts/{transcript_id}/generate-output`
- `POST /api/v1/transcripts/{transcript_id}/generate-followup`
- `POST /api/v1/transcripts/{transcript_id}/run-quick-action`
- `GET /api/v1/transcripts/{transcript_id}/generated-documents`
- `PATCH /api/v1/generated-documents/{generated_document_id}`
- `DELETE /api/v1/generated-documents/{generated_document_id}`
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

Live defaults:

- one chunk request per second;
- one hour aggregate duration per rolling hour;
- measured chunk maximum around 30 seconds.

The server measures/probes audio rather than trusting declared duration for enforcement/accounting. Accepted jobs snapshot STT execution metadata and create task-dispatch/quota metadata transactionally.

Whole-file source audio required for asynchronous processing/retry is stored under a bounded Vault reference, not a new PostgreSQL audio blob. Successful/terminal/deletion paths clear or durably queue cleanup with live-reference guards. Retry transfers an existing source reference transactionally rather than duplicating it.

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

## Security and caching

- `/api` responses are no-store/no-cache.
- API cookies remain `HttpOnly`; browser JavaScript receives CSRF state only through server-rendered data.
- Provider credentials and Vault references are never returned through normal config/content APIs.
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
