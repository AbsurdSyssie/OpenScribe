# PHI / De-identification Plan

## Goal

Rework the current PHI redaction flow so system admins can configure a de-identification API while OpenScribe keeps ownership of:

- placeholder numbering and format
- PHI index construction
- `redaction_runs` reuse
- `redaction_entities` persistence
- placeholder validation
- re-identification before storing generated output

The intended outcome is a provider-agnostic de-identification layer that preserves the current indexing and re-identification pipeline.

## Current Pipeline

### Runtime flow

Current implementation is centered in `app/services/redaction.py` and `app/services/templates.py`.

1. `process_generated_document()` resolves the queued transcript version.
2. `ensure_redaction_run_for_transcript_version()` lazily creates or reuses one successful `redaction_runs` snapshot per transcript version.
3. The transcript snapshot is decrypted and passed into native Presidio detection.
4. OpenScribe converts detections into internal placeholders using the `[PHI-N]` format.
5. The redacted transcript text is encrypted and stored in `redaction_runs.redacted_text_encrypted`.
6. Original PHI values are encrypted and stored in `redaction_entities.original_value_encrypted`.
7. Prompt text, follow-up text, quick-action text, structured context, and optional dictation text are redacted transiently using the same placeholder numbering space.
8. Only redacted text is sent to the external LLM.
9. Returned output is validated so only known well-formed placeholders survive.
10. OpenScribe re-identifies the output internally before persisting the final generated document.

### Persisted artifacts

Current schema stores:

- `redaction_runs`
  - transcript root/version linkage
  - owner and team linkage
  - encrypted redacted text snapshot
  - `mapping_hash`
  - `entity_count`
  - provider metadata
- `redaction_entities`
  - stable placeholder order
  - placeholder string
  - entity type
  - encrypted original value
  - normalized hash and occurrence count

### Current provider-specific coupling

Most of the pipeline is already generic. The provider-specific logic is concentrated in:

- Presidio runtime/bootstrap in `app/services/redaction.py`
- the `analyzer.analyze(...)` call
- Presidio-specific config in `app/redaction/presidio_config.yaml`

Everything after "provider returned candidate PHI spans" is OpenScribe logic and should remain OpenScribe-owned.

## Design Principles

### Must remain true

- Transcript-derived content stays private and owner-only.
- Admin config access must not imply transcript readability.
- Transcript deletion semantics must remain unchanged.
- Existing `redaction_runs` and `redaction_entities` stay the retention/deletion children of transcript roots.
- OpenScribe must continue validating placeholders before re-identification.
- Final generated documents remain re-identified before storage.
- Provider secrets must stay in Vault, not Postgres.

### What we should not outsource

Do not delegate these to an external de-identification API:

- placeholder format generation
- placeholder numbering
- transcript-version redaction snapshot reuse
- PHI index persistence
- placeholder validation rules
- re-identification rules

If the provider returns pre-redacted text instead of detections/spans, OpenScribe loses control of the placeholder contract and makes cross-source numbering much harder.

## Recommended Architecture

### High-level shape

Split the current redaction service into two layers.

1. Provider adapter layer
2. OpenScribe PHI mapping layer

### Provider adapter contract

All adapters should return detected entities, not final redacted text.

Suggested internal contract:

```python
@dataclass
class DetectedEntity:
    start: int
    end: int
    entity_type: str
    score: float | None = None


@dataclass
class DeidentificationDetectionResult:
    entities: list[DetectedEntity]
    api_provider: str
    api_model_or_version: str | None = None
```

OpenScribe then performs:

- span normalization
- false-positive filtering
- overlap resolution
- `[PHI-N]` assignment
- `phi_index` creation
- `mapping_hash` generation
- persistence into `redaction_runs` and `redaction_entities`

### Initial adapters

Phase 1 should support:

- `native_presidio`
- `generic_rest`

`native_presidio` becomes an adapter implementation behind the new contract.

`generic_rest` calls an admin-configured external API, parses returned entities, maps them into OpenScribe canonical entity types, then hands them to the existing internal mapping flow.

## Why Span-Based Detection Is The Right Boundary

Returning spans instead of provider-generated placeholder text lets OpenScribe preserve:

- deterministic placeholder numbering
- one numbering space across transcript plus transient prompt text plus dictation text
- the existing dictation split-marker path
- stable validation and re-identification semantics
- debug tooling that shows the exact OpenScribe redacted payload sent to the LLM

This is the minimal architecture change that introduces external de-identification without redesigning the rest of the generation pipeline.

## Recommended Authority Model

Recommended model:

- keep the current native Presidio implementation as the built-in default provider
- expose that built-in provider as selectable by any team
- let system admins provision additional de-identification providers
- assign external providers to teams
- let teams select from their assigned providers, following the same policy pattern as STT and LLM

### Why this is a good fit

- preserves today's behavior as the default path
- gives every team a working PHI de-identification option even with no external setup
- matches the existing mental model for STT and LLM provider management
- keeps secrets and provider metadata under admin control
- lets different teams use different de-identification providers without changing the internal PHI index and re-identification pipeline

### Architecture note

This is broader than the earlier MVP assumption that pseudonymisation is globally fixed. If implemented, we should treat that as an explicit architecture update:

- de-identification execution remains backend-only
- transcript-derived content remains owner-private
- provider selection becomes team policy, not a global singleton

## Proposed Provider / Policy Shape

Mirror the STT / LLM split:

- provider config rows are admin-provisioned
- teams only consume providers assigned to them
- active team selection is separate from provider definition

Suggested shape:

- `deidentification_providers`
  - provider metadata and runtime contract
  - may be built-in or external
- `team_deidentification_provider_assignments`
  - which providers are available to a team
- `team_deidentification_selections`
  - the active provider for the team

### Built-in default provider

The current native Presidio path should become a first-class built-in provider row or built-in provider identity.

Requirements:

- no secret required
- available to all teams by default
- selectable without any extra provisioning step
- remains the fallback implementation for local development and minimal deployments

### Additional admin-provisioned providers

System admins should be able to add additional providers such as:

- generic REST de-identification APIs
- future known-contract adapters if needed

Admins control:

- provider metadata
- endpoint contract settings
- Vault-backed secrets
- which teams may use the provider

Team policy then controls:

- which assigned provider is active for that team

### Team leader authority

Recommended authority split, matching STT:

- system admins provision provider definitions and secrets
- system admins assign external providers to teams
- team leaders may choose the active provider from the providers assigned to their own team
- team leaders may not view or recover raw provider secrets
- team leaders do not gain transcript-derived content visibility through provider policy screens

### Selection behavior

Recommended resolution order:

1. active team de-identification selection if one exists
2. otherwise the built-in native Presidio provider

This avoids teams becoming unusable when no external provider has been assigned yet.

## Proposed Generic REST Config Shape

If we add an admin-configured generic REST detector, the config should describe detection contract details rather than let runtime become fully arbitrary.

Suggested metadata fields:

- `label`
- `adapter_kind`
- `base_url`
- `detect_path`
- `auth_mode`
- `vault_secret_ref`
- `request_text_field`
- `request_language_field` optional
- `extra_headers_json`
- `extra_body_json`
- `response_entities_path`
- `response_start_field`
- `response_end_field`
- `response_type_field`
- `response_score_field` optional
- `response_model_version_path` optional
- `entity_type_map_json`
- `is_active`

Guardrails:

- no arbitrary code execution
- no user-defined placeholder templates
- no user-defined re-identification logic
- no content-bearing provider debug logs

## Canonical Entity Taxonomy

To preserve current filtering logic in `app/services/redaction_policy.py`, provider-specific labels should map into a canonical internal taxonomy.

Initial canonical types should include at least:

- `PERSON`
- `DATE_TIME`
- `PHONE_NUMBER`
- `EMAIL_ADDRESS`
- `LOCATION`
- `UK_POSTCODE`
- `UK_NHS_NUMBER`
- `STREET_ADDRESS_PHRASE`

This keeps existing normalization and false-positive filtering reusable.

## Refactor Plan

### Slice 1: provider-agnostic internal pipeline

Goal:

- no behavior change
- keep native Presidio as the only runtime implementation
- isolate provider calls behind an adapter contract

Work:

- extract native Presidio detection into its own adapter implementation
- refactor `redact_text_with_mapping()` into:
  - resolve active adapter
  - fetch detections
  - run OpenScribe mapping and persistence logic
- keep existing schema unchanged
- keep existing `redaction_runs` and `redaction_entities` behavior unchanged

### Slice 2: admin-configured generic REST adapter and team selection

Goal:

- allow admins to configure an external de-identification API
- continue using the same OpenScribe-owned mapping/re-identification pipeline

Work:

- add provider config storage and Vault-backed secret handling
- add admin UI/API for inspection/save/edit/delete
- add team assignment and active selection support
- add generic REST detection adapter
- parse provider entity payload into canonical detections
- use same `ensure_redaction_run_for_transcript_version()` and `redact_transient_text()` flow

### Slice 3: policy hardening and diagnostics

Only if needed:

- prevent editing/deleting configs referenced by in-flight work if snapshotting is required
- diagnostics and health-check path
- richer assignment controls or provider capability validation

## Suggested Module Shape

Possible target structure:

- `app/services/redaction.py`
  - orchestration and persistence
- `app/services/deidentification.py`
  - adapter resolution
- `app/services/deidentification_adapters/native_presidio.py`
- `app/services/deidentification_adapters/generic_rest.py`
- `app/services/redaction_policy.py`
  - keep current normalization/filter rules

This keeps the change small without broad refactoring.

## Data / Snapshot Behavior

Current run reuse behavior should stay unchanged.

Important rule:

- a successful `redaction_runs` row remains reusable for the same `transcript_versions` snapshot

Open question:

- if a team's active de-identification provider changes later, should existing queued documents continue using the redaction run already linked to the transcript version, or should they re-run under the new provider?

Recommendation:

- preserve current snapshot semantics
- if a successful `redaction_run` already exists for a transcript version, reuse it regardless of later team-provider selection changes

This matches the current immutable snapshot approach already used for generation inputs.

## Privacy / Ownership / Deletion Checkpoints

### Privacy boundary checkpoint

- provider config management must not expose transcript-derived content
- admin users may manage metadata and secrets but not inspect PHI values
- debug routes must remain owner-only and must not reveal original PHI values

### Auth and ownership checkpoint

- only the transcript owner may trigger generation or read generated output
- admin-configured de-identification providers operate only as backend infrastructure
- leaders should not gain content access through provider policy screens

### Lifecycle and deletion checkpoint

- `redaction_runs` and `redaction_entities` remain transcript-derived children
- transcript-root deletion continues to cascade through redaction artifacts and generated docs
- provider config deletion must not orphan transcript-derived data or break already-completed redaction snapshots

### Docs and tests checkpoint

- update runtime docs
- add config-management docs if external adapters are introduced
- expand tests around provider resolution and unchanged re-identification behavior

## Checklist Before Coding

### Target behavior

- admins can configure a de-identification provider
- OpenScribe can call that provider to detect PHI
- OpenScribe still builds and stores its own placeholder index and mappings
- OpenScribe still validates placeholders and re-identifies before persistence

### Affected schema / modules / endpoints

Likely affected modules:

- `app/services/redaction.py`
- `app/services/redaction_policy.py`
- new de-identification adapter module(s)
- `app/models.py`
- admin schemas/routes/presentation if config UI is added
- `app/services/vault.py`

Possible schema additions:

- de-identification provider table(s)
- team assignment table
- team active selection table

### Affected tests

- redaction unit tests
- generated-document integration tests
- admin config authorization tests
- migration/schema tests
- provider-resolution tests

### Architecture risks

- accidentally moving placeholder ownership outside OpenScribe
- introducing team-scoped policy without clearly documenting the architecture change from the earlier global-MVP assumption
- breaking run reuse semantics for transcript-version snapshots
- logging or surfacing content-bearing provider payloads

### Docs to update

- `docs/api.md`
- `docs/transcript-capture.md`
- `docs/testing.md`
- add dedicated de-identification config doc if external provider support lands

## Tests Required

### Unit tests

- provider adapter parsing for generic REST responses
- canonical entity mapping
- span normalization and overlap resolution still behaving correctly
- placeholder validation unchanged

### Integration / API tests

- transcript version redaction run creation using selected adapter
- generic adapter preserving `redaction_runs` and `redaction_entities`
- transient prompt/dictation redaction sharing one placeholder numbering space
- failed malformed provider response closing safely

### Authorization tests

- admin-only de-identification config management
- no transcript readability granted through config management
- owner-only generated-document redaction debug remains intact

### Migration / schema tests

- new config table presence and constraints
- Vault ref and metadata fields present as intended
- selection uniqueness if policy table exists

### Deletion / lifecycle tests

- transcript deletion still cascades to redaction artifacts
- config deletion behavior does not break existing stored runs

## Documentation Updates Required

When implementation lands, update:

- runtime generation/redaction docs
- admin/provider-management docs
- testing docs
- architecture notes describing the external adapter boundary

## Locked Decisions

1. Generic API returns spans/entities only. No provider-supplied replacement values.
2. No diagnostics/inspection flow in first slice. Add later only if needed.
3. External providers require explicit team assignment.
4. Built-in native provider is implicitly available to all teams.

## Recommended Decision Set

For the smallest safe implementation:

1. Keep OpenScribe-owned placeholders and re-identification.
2. Keep native Presidio as the built-in default provider available to every team.
3. Require external de-identification APIs to return spans/entities, not prebuilt placeholder text.
4. First refactor native Presidio behind an adapter with no behavior change.
5. Add admin-configured generic REST providers plus team assignment and team-leader-controlled active selection.
6. Fall back to the built-in native provider when a team has no explicit external selection.

## Definition Of Done

This plan is complete when implementation delivers:

- provider-agnostic de-identification adapter boundary
- unchanged `redaction_runs` / `redaction_entities` semantics
- unchanged placeholder validation and re-identification semantics
- admin-configurable external de-identification support
- relevant tests added or updated
- docs updated to match the new provider model
