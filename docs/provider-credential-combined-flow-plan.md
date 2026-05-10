# Provider Credential Combined Save And Inspect Plan

## Target Behavior

System admins should enter provider API credentials once. Create flow should save the credential reference and validate/inspect it in one server-side pass, without returning raw secrets to the browser or requiring a second key entry.

Existing saved providers remain usable. Admins may re-inspect existing providers manually using saved Vault references.

## Affected Areas

- Admin provider UI for STT, LLM, de-identification, and future provider credential forms.
- Admin provider create endpoints that currently split inspect/discover and save.
- Saved provider credential models and provider metadata/status fields.
- Vault-backed provider secret lifecycle.
- Team provider selection rows when providers are deleted or marked invalid.
- Provider inspection services and tests.

## Architecture Constraints

- System admins may manage provider credentials, but raw secrets must never be shown back in HTML or JSON.
- Team leaders may select active providers/models where allowed, but must not view or recover raw credentials.
- Inspection must never send transcript text, note text, generated document text, prompts containing patient content, or real clinical content.
- Provider secrets stay in Vault. Postgres stores only Vault references and sanitized metadata.
- Do not delete Vault secrets before DB references are removed unless compensation/retry cleanup exists.
- Logs and audit events may include IDs, provider type, team ID, statuses, error codes, durations, and counts only.

## Proposed Flow

### Create Provider Credential

1. Admin submits provider metadata, secret/API key, and optional `confirm_duplicate` flag.
2. Server computes a safe non-reversible secret fingerprint/HMAC using server-side material.
3. If same team, provider type, endpoint/base URL, and fingerprint look duplicated, return a duplicate warning before any Vault write or provider call.
4. If admin confirms duplicate, continue.
5. Store secret in Vault.
6. Create DB provider row with Vault reference and `pending_inspection` status.
7. Validate credential with cheapest safe provider call.
8. If credential is invalid, remove DB row first, then delete Vault secret or enqueue cleanup/retry. Return sanitized error.
9. If credential is valid, run best-effort metadata/model discovery.
10. Save sanitized metadata and final status.

### Status Outcomes

- `verified`: credential valid and useful metadata/model discovery succeeded.
- `partial`: credential valid, but metadata/model discovery failed or timed out.
- `pending_inspection`: transient create state only.
- `unknown`: existing providers or providers not yet manually re-inspected.
- `degraded`: saved provider re-inspection failed or timed out without credential rejection.
- `invalid`: saved provider credential rejected by provider on re-inspection.

Runtime may use `unknown`, `verified`, `partial`, and `degraded`. New team selections should block `invalid`. If an active provider becomes `invalid`, clear or fallback according to existing provider resolution rules.

### Re-Inspect Existing Provider

- Re-inspect uses saved Vault reference and never asks admin to re-enter the key.
- If metadata discovery succeeds, update sanitized metadata.
- If provider is unreachable or times out, keep provider and mark `degraded`.
- If provider rejects credential, mark `invalid` and clear/fallback any active team selection using that provider.
- Do not auto-delete existing providers after re-inspect failure; admin delete remains explicit.

### Delete Provider

- Delete remains explicit admin action.
- If provider is active for any team, UI warns that deletion clears active selection for affected team count.
- In one DB transaction, clear selections referencing provider, then remove provider DB reference.
- After commit, delete Vault secret or enqueue cleanup/retry.
- Never silently leave selection rows pointing at deleted providers.

## Duplicate Warning

Duplicate detection should warn, not block.

Recommended matching inputs:

- team/scope
- provider type
- endpoint/base URL or region identity
- safe secret fingerprint/HMAC

First submit with likely duplicate returns warning and does not save or inspect. Confirmed submit proceeds with create flow.

## Inspection Calls

Credential validation should use the cheapest safe authenticated call available.

- LLM: model list, health, or equivalent authenticated metadata call.
- STT: health, model list, OpenAPI metadata, or equivalent authenticated provider call. Do not upload patient audio.
- De-identification: health/capabilities call. Do not send transcript text.

Model/capability discovery is best effort after credential validity is established. If discovery fails after credential validation succeeds, save provider as `partial`.

## API Shape

Create endpoint should accept provider metadata and secret once, for example:

```http
POST /admin/provider-credentials
```

Request fields:

- provider type
- team/scope
- endpoint/base URL or provider-specific location fields
- provider-specific config
- secret/API key
- `confirm_duplicate`

Response shapes:

- duplicate warning, no provider created
- `verified` with sanitized metadata
- `partial` with sanitized warning
- invalid credential error, no provider retained

Saved-provider re-inspect stays separate, for example:

```http
POST /admin/provider-credentials/{id}/inspect
```

## UI Requirements

- Replace inspect-then-save key re-entry with one `Save and inspect` action for new providers.
- Keep `Re-inspect` action on existing provider rows.
- Display duplicate warning before save and require explicit confirmation.
- Show `verified`, `partial`, `unknown`, `degraded`, and `invalid` status clearly.
- Show sanitized provider error code/message only. Do not show raw response bodies.
- Warn before deleting active provider that team selection will be cleared.

## Audit Events

Recommended events:

- `provider_credential_create_attempt`
- `provider_credential_duplicate_warning`
- `provider_credential_create_verified`
- `provider_credential_create_partial`
- `provider_credential_create_failed`
- `provider_credential_reinspect`
- `provider_credential_deleted`

Audit payloads should contain IDs, provider type, team ID, status, sanitized error code, duration, and affected selection counts only.

## Tests To Add

- Admin-only create authorization.
- Duplicate warning returns before Vault write and before provider inspect call.
- Confirmed duplicate can proceed.
- Valid credential creates Vault reference, DB provider row, sanitized metadata, and `verified` status.
- Valid credential with discovery failure saves provider as `partial`.
- Invalid first-add credential removes DB reference before Vault cleanup/retry.
- Re-inspect existing provider uses saved Vault reference and no key input.
- Re-inspect credential rejection marks provider `invalid` and clears/fallbacks active selection.
- Delete active provider clears affected team selection rows in same DB transaction.
- Responses/logs/audit payloads do not include raw secrets or raw provider bodies.

## Documentation To Update During Implementation

- `docs/admin_brief.md`: provider UI authority, new create/re-inspect actions, statuses.
- `docs/stt-config.md`: STT credential lifecycle and status behavior.
- `docs/api.md`: admin provider credential endpoints and response semantics.
- `docs/security.md`: Vault cleanup order and no-secret/no-raw-response logging rule.
- `docs/testing.md`: focused provider credential test commands.
- `docs/progress.md`: implementation checkpoint and final architecture summary.

## Checklist Before Coding

- Target behavior: admin enters provider secret once; create saves and validates/inspects in one server-side pass.
- Affected schema/modules/endpoints: provider credential models, admin provider routes, Vault secret service, provider inspection services, team selection rows.
- Affected tests: admin auth, Vault cleanup, duplicate warning, provider status, active-selection clearing, no-secret responses/logs.
- Architecture risks: Vault/DB transaction boundary, active-provider fallback behavior, duplicate fingerprint storage, raw provider error leakage.
- Docs referenced: `AGENTS.md`, `docs/admin_brief.md`, `docs/stt-config.md`, `docs/llm-providers.md`, `docs/security.md`.

## Coding Checkpoints

- Schema checkpoint: add status/metadata/fingerprint fields only where needed; preserve provider/team ownership constraints.
- Auth/ownership checkpoint: create/re-inspect/delete stays system-admin only; team leaders never see raw credentials.
- Lifecycle/deletion checkpoint: DB references removed or selections cleared before Vault cleanup; retry path exists for failed Vault deletion.
- Docs/tests checkpoint: update docs and run focused `.venv/bin/pytest -q` provider/admin tests, then full suite where practical.

## Completion Checklist

- Code complete: pending.
- Tests added/updated: pending.
- Docs added/updated: this plan created; implementation docs pending.
- Open issues: exact endpoint names and schema names should follow current code during implementation.

## Architecture Checkpoint Summary

- Privacy boundaries: inspection uses metadata/synthetic-only provider calls; no transcript-derived content added to admin flow.
- Ownership rules: provider provisioning remains system-admin scoped; team/user content ownership unchanged.
- Deletion semantics: invalid first-add cleanup and provider deletion preserve DB-first then Vault cleanup order.
- Provider rules: raw credentials remain Vault-backed; team selection/fallback remains policy-driven.
- Structured-note contract: no EMIS or generated-document JSON contract change.
