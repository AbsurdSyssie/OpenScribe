# Provider Credential Save, Inspection, and Cleanup

## Status

**Implemented design history.** The original goal—enter a provider credential once, inspect it server-side, persist only a Vault reference and safe metadata, and allow later reinspection without redisplaying the credential—is implemented across current provider flows.

Current contracts are maintained in:

- [stt-config.md](stt-config.md)
- [llm-providers.md](llm-providers.md)
- [admin_workspace_function_map.md](admin_workspace_function_map.md)
- [api.md](api.md)
- [security.md](security.md)

This file records the principal lifecycle decisions and corrects early proposed generic endpoint/status details that are not the current API schema.

## Implemented authority and privacy

- System administrators provision credential-bearing providers.
- Team leaders select eligible provisioned policies for their own team but cannot create/reveal/replace/delete raw credentials.
- Provider inspection uses metadata or synthetic fixture content, never transcript, Working-note, dictation, generated-document, or patient prompt content.
- PostgreSQL stores configuration metadata, bounded inspection status, fingerprints, and Vault references—not raw credentials.
- Browser/API responses expose bounded status and `has_secret`, never the raw value or unrestricted Vault reference.
- Logs/audit/usage contain IDs, type/adapter/preset, team, status, safe error code, duration, and counts only.

## Current create/draft flow

Provider families have adapter-specific setup, but the common safety pattern is:

1. Validate actor/team/provider metadata.
2. Reject malformed/unsafe endpoint/auth shapes before a provider call.
3. For credential-bearing setup, write the submitted credential to a unique versioned Vault path only when the flow reaches the persistence stage.
4. Inspect/validate with the cheapest safe provider-specific operation.
5. On definitive credential rejection, create neither a usable config nor a retained secret; any external write that cannot be deleted immediately is durably queued for cleanup.
6. Save only sanitized model/contract/status metadata.
7. Keep incomplete setup in a non-selectable pending/draft state until required model/contract choices are finalized.
8. Promote finalized metadata to a ready active/available root according to the provider service contract.

The concrete API uses provider-specific routes (`/api/v1/stt-configs/*`, `/api/v1/llm-configs/*`, and de-identification routes), not the proposed generic `/admin/provider-credentials` endpoint.

## Duplicate credential handling

STT credential duplicate detection uses a non-reversible HMAC fingerprint with `PROVIDER_CREDENTIAL_FINGERPRINT_SECRET` (and controlled fallback behavior documented in [environment.md](environment.md)).

A fingerprint is:

- duplicate-warning metadata only;
- not authentication material;
- not reversible credential storage;
- scoped with provider/team/endpoint metadata as required by the service;
- stable only when the deployment uses a stable dedicated production secret.

Duplicate handling remains provider/service specific; do not assume every provider family implements the exact early-plan warning status set.

## Saved provider reinspection

Authorized reinspection:

- reads the saved Vault reference/deployment identity server-side;
- never requests/renders the saved secret in the browser;
- performs adapter-specific metadata/model/health checks;
- persists bounded inspection metadata/time/status;
- does not auto-delete the provider merely because a remote call is temporarily unavailable;
- can make a rejected/incomplete provider non-selectable according to current service/policy rules;
- never exposes raw provider response bodies from runtime patient-content calls.

STT saved diagnostics can use the bundled synthetic audio fixture. LLM discovery/finalization behavior is provider-specific; Gemini Enterprise uses Google identity/project/location rather than bearer-token/base-URL semantics.

## Revisions and credential inheritance

The current revision flow is stronger than the early proposal:

- A required-auth draft/revision that inherits a saved credential reads it only inside the authorized service.
- It immediately copies the credential to a draft-owned unique versioned Vault path.
- The draft does **not** persist an alias to the active root's Vault reference.
- Promotion transactionally updates the stable root and retires superseded references through durable cleanup.
- Cancelling a draft removes its metadata and safely/durably cleans its draft-owned reference.
- A revision changing to no-auth/ADC can explicitly remove the previous credential through the same retirement path.

This isolates pending work from concurrent active-root credential replacement/deletion and preserves rollback safety.

## Replacement, removal, deletion, and rollback

External Vault writes/deletes cannot be atomically committed with PostgreSQL. Current services use DB-first durable intent/compensation patterns:

- replacement writes a new unique reference before switching database metadata;
- the old reference is retired only after database commit;
- removal/deletion/promotion/cancellation records exact FK-free cleanup intent where needed;
- cleanup workers retry and verify a reference is not live before deletion;
- if a Vault write succeeds but the database transaction fails, rollback compensation queues the orphan reference;
- team/provider deletion clears or blocks dependent assignments/selections according to the current service contract;
- queued/processing generated work can block ordinary provider edits/deletion to preserve runtime snapshots.

Credential correction during in-flight LLM work is a narrow validated exception described in [llm-providers.md](llm-providers.md); it cannot silently alter unrelated endpoint/model/availability fields.

## Status terminology

Do not use the original generic `verified`/`partial`/`pending_inspection` proposal as a universal enum. Current STT and LLM models have provider-specific setup and credential-inspection status fields such as pending model selection, ready, and bounded credential/discovery states.

The operational rule is stable:

- incomplete/invalid configs are not newly selectable;
- ready/active/complete configs can be selected subject to team policy;
- temporary inspection failure does not disclose secrets or patient content;
- definitive invalid identity/credential fails safely and updates availability according to that provider service.

## Testing requirements

Provider credential lifecycle tests should cover:

- system-admin-only provisioning/reinspection/replacement/deletion;
- leader/user denial and no-secret response shape;
- invalid credential creates neither retained usable config nor untracked Vault secret;
- saved reinspection uses the saved reference without browser key input;
- draft inheritance creates a distinct reference;
- rollback compensation after external write + DB failure;
- durable cleanup and live-reference guard;
- selection/assignment behavior after provider invalidation/deletion;
- queued-work edit/delete blockers;
- no raw secret/provider body/content in responses, logs, audit, usage, or cleanup rows.

## Remaining improvements

Possible focused follow-up work includes:

- stale abandoned draft visibility/cleanup policy;
- richer operator health/cleanup metrics;
- stronger production Vault identity/rotation controls;
- consistent user-facing status vocabulary where provider-specific semantics permit it.

Implement these through the current provider-specific services and update the operational references above. Do not revive the generic endpoint/model as if it were the existing API.
