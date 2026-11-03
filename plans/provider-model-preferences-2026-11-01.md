# Provider model preferences and role health

Status: implementation and focused verification completed on 2 November 2026, following the 1 November user interview. The application database migration has not been applied. Maintained documentation describes the implemented behavior; the baseline below records the pre-change behavior.

Completion evidence: 285 focused runtime tests passed; 60 API/provider/migration tests passed; all 170 API authorization route checks passed. Focused browser checks and desktop/mobile screenshot review passed. Operational documentation validation checked 54 documents successfully, and `git diff --check` passed. These checks used test infrastructure and synthetic browser data, without live provider calls or application database migration.

## Agreed behavior

- Each team provider has its own saved enabled models and default. Saving these preferences never changes the active writing provider.
- Provider policy selects the writing provider using its saved preferences. Writing follows that provider's default, subject to existing enabled personal model preferences. Changing the default never switches a role's provider.
- Enabled models are an overall provider allowance for writing, backup and hallucination checking. Backup and checker retain explicit pinned models when the provider default changes.
- Empty enabled models means none, never all. Require an enabled default when saving a nonempty list; an empty list has no default.
- Newly configured providers start with no enabled models. Connection setup remains distinct from model policy and role activation.
- Refresh preserves matching selections, leaves newly discovered models unchecked, and flags missing defaults for replacement. Refresh alone does not save a new model policy or silently substitute a role's model. A saved policy can become invalid when its catalog changes; derive warnings rather than destroying the user's assignment.
- Saving a restriction that invalidates an assigned role is allowed with a clear warning. Keep the role/provider/model assignment visible for repair. Show a red textual warning beside the affected service in the provider table and Provider policy.
- An unavailable writing provider/model uses an eligible backup. Provider, credential and model/setup failures should reach backup; authorization, ownership, redaction, retention, corrupt snapshots, internal errors, quota accounting and deadlines remain enforced.
- An unavailable checker does not block generation. Preserve existing fail-open behavior, with an admin warning and safe generation checking status. A disabled backup cannot be used for new work.
- Changes apply only to newly accepted work. Already queued/running work retains frozen provider/model choices, including backup and checker. Preserve existing lifecycle/credential/snapshot validation and durable dispatch/attempt/quota semantics.
- Connection status is separate from role validity and enabled/disabled status. Display the last explicit check's success/failure, timestamp and safe failure reason; never call providers merely to render/open the UI. Validate key and Refresh models update relevant check status. Failed tests of a replacement credential must not mark the unchanged saved credential broken.
- System admins manage overall allowances/defaults. Team leaders retain own-team writing-provider selection among configured eligible providers, but cannot mutate overall allowances affecting admin-owned backup/checker roles.

## Pre-implementation executable behavior and drift

- `TeamLlmConfig` is already team-scoped and has stable root IDs plus temporary connection revisions. Its `model_name` serves setup/discovery as well as an old default.
- `TeamLlmSelection` has unique team ID and stores the only enabled list/default override; switching overwrites it. Its old empty list means all catalog models.
- Backup has a pinned model; checker overrides may be NULL and then dynamically follow config.model_name. Pin existing resolved checker models during migration.
- `_reconcile_llm_config_model_selections` currently rewrites missing-model role choices. Replace that behavior with derived invalid-role warnings, retaining assignments.
- Ordinary documents freeze writing and backup choices, but read checker selection live during execution. A checker acceptance-time snapshot is necessary for the new-work-only guarantee. Split verification already snapshots provider/request when queued; carry its accepted checker choice forward rather than rechecking later admin policy.
- Existing backup policy excludes security/lifecycle/local-quota/deadline/snapshot failures. Preserve those exclusions instead of catching arbitrary errors. It also excludes most provider-returned HTTP 429/account-quota errors; the user's instruction to use backup regardless of provider error authorizes removing that provider-response exclusion, while still reserving and enforcing local application quotas before backup dispatch.
- LLM rows' Available status currently means is_active; failed refresh does not persist a failed check. Do not mistake it for credential health.
- Selection writes also exist in admin and home HTML form routes; update these alongside JSON routes and forms, not just the new modal.

## Persistence and API design

1. Add a one-to-one root-provider model-policy record, team-scoped and keyed by root config ID, with explicit enabled-model IDs, nullable default, actor and timestamps. Use existing SQLAlchemy/Alembic patterns and DB cleanup paths. Root connection revisions must preserve the saved policy.
2. Backfill existing root providers: active writing provider gets its current effective allowance/default (expand legacy empty/all); other providers get their current catalog/default. Union existing backup/checker assigned models so rollout does not disable current roles. Pin nullable checker choices to their pre-migration effective model. Preserve unavailable historical assignments as warnings rather than silently choosing unrelated replacements.
3. New roots get an explicit empty policy. Do not infer a missing persisted policy as unrestricted for new providers. Compatibility for legacy in-memory fixtures must be deliberate and covered, not an implicit empty-means-all fallback in production.
4. Keep active writing selection separate from saved policy. Remove old columns if coherent, or retain only clearly documented compatibility projections with one authoritative source. No competing persisted defaults/allowances.
5. Add system-admin scoped GET/PUT `/api/v1/llm-configs/{config_id}/model-policy`, team-scoped and root-only. PUT saves enabled IDs/default and returns safe policy data and affected-role warnings. GET supplies catalog, saved policy, effective validity and warnings. Selection endpoints choose a provider without rewriting its policy; legacy allowlist/default mutation must not let leaders alter global allowance.
6. Expose safe model policy, role validity and last-check metadata through existing detail/presentation contracts. Explicit role warning fields should identify writing/backup/checker and machine-readable safe reason codes. Never return credentials or unrestricted Vault references.
7. Store bounded service-owned connection-check metadata using current persistence patterns; only allowlisted error codes/statuses and safe generated wording. Persist failure despite the inspection operation returning an error, without committing unrelated writes. Never store provider response bodies or arbitrary provider messages.

Implementation contract: `TeamLlmProviderModelPolicy` is exposed as `config.model_policy`. Policy responses include `provider_available_models_json`, `enabled_models_json`, `default_model_name`, `policy_valid`, `invalid_enabled_models_json`, `default_model_valid` and `role_warnings` entries (`role`, `reason`, `model_name`). Config details expose `last_connection_check_status`, `last_connection_check_at`, `last_connection_check_error_code` and `last_connection_check_http_status`. Checker assignments use owner-encrypted `hallucination_check_selection_snapshot_encrypted` fields on generated documents and split batches; the runtime-owned helper freezes/reads/copies explicit configured, not-configured or invalid states. Core owns the schema/migration, runtime owns the helper and integration.

Snapshot lifecycle reconciliation: pair checker ciphertext with nullable `hallucination_check_selection_config_id` safe metadata/FK on both sources. Freeze/copy them together; config edit/deletion guards must detect pending checker-only references. Retire terminal snapshot pairs using established transactional reference removal before secret cleanup. For legacy queued records whose checker choice was never snapshotted, preserve the old live-checker path and narrowly prevent relevant checker/policy/catalog changes until they drain. Newly accepted work always freezes an explicit state and does not require that transitional guard.

## Runtime integration

- Central helpers resolve saved provider policy, effective enabled catalog intersection, default validity and role warnings. Check role model eligibility when accepting new work; do not apply changed allowances to already frozen work.
- Writing may use an eligible personal preference; disallowed personal choices fall back to the provider default. If the writing assignment itself has no usable model/default, use the eligible configured backup without silently changing the policy table's writing-provider pointer.
- Backup snapshot freezing must validate overall allowance. Existing frozen snapshots remain valid after allowance edits, subject to existing lifecycle/config/credential validation.
- Checker selection persists an explicit model. Freeze checker eligibility/provider/model when accepting ordinary generation, including distinguishable not-configured and configured-invalid states. Use established non-secret metadata or owner-encrypted snapshot conventions; no new raw secret/reference exposure.
- Carry checker selection through child/retry/split paths at the established accepted-work boundary. Do not resurrect retired/deleted provider snapshots or weaken live-root checks.
- Do not change clinical structured output, redaction, result validation, execution claims, submitted-attempt accounting or quota boundaries.

## UI

- Retain the existing Connection/Models modal design, scrolling, search, All/Enabled views and pinned actions. Models loads/saves the viewed provider's policy even when inactive.
- Use a neutral Save models action; remove activation side effects and setup/task-shaped copy. State that enabled models apply across writing, backup and checking and changes apply to new generations.
- Preserve drafts/search, match selections on refresh, show removed saved/default IDs as unresolved, and require replacement before saving a nonempty list. Allow clear-all with no default.
- Show the affected-role warning before Save without forcing a replacement. After Save refresh table/policy role warnings and effective default without switching providers.
- Provider policy writing selection uses saved provider defaults; backup/checker offer allowed models and retain visibly invalid assigned choices for repair. Leaders can switch providers but do not get allowance/default editing controls.
- Provider table shows enabled status, last explicit connection check and per-role warnings with icons plus text, accessible names and no color-only meaning. Use the established Lucide bundle and design tokens.
- No automatic provider calls on open/page load, no raw errors, and preserve existing key replacement/unsaved-change/revision conflict recovery behavior.

## Delegation and ownership

- Core Luna: models, migration, schemas, llm service/policy helpers, API routes/audit, presentation and focused core tests. Own core docs (`llm-providers.md`, API/database docs). Publish helper/payload contracts to runtime/UI before they integrate.
- Runtime Luna: backup/checker and accepted-work integration in generation/suggestion/split services, new runtime helper modules and focused runtime tests. Coordinate any model/migration additions with core Luna; never concurrently edit llm.py/models/routes/presentation.
- UI Luna: admin template/web routes, affected home form route/template integration, browser/static UI tests, admin function map. Do not edit core/runtime-owned services. Use Impeccable reference already established in this conversation. Capture synthetic desktop/mobile screenshots.
- Parent: integration/architecture review, resolve scope conflicts, serialize DB-dependent verification and audit, review screenshots and final findings. Do not overwrite unrelated dirty work; no commits or production/application DB migrations are requested.

## Acceptance and verification

- Save distinct policies for A/B, switch A→B→A, retain exact lists/defaults, and prove saving B never activates B.
- Empty means none, first setup empty, nonempty requires enabled default, unknown IDs rejected, team/root scoping enforced.
- Writing follows default; pinned backup/checker stay unchanged. Invalidated assignments stay visible; disabled primary uses eligible backup; checker failure leaves generation usable; disabled backup is never newly frozen.
- Refresh does not expand or save allowances, missing default stays unresolved, role assignments are not silently replaced.
- Migration preserves existing effective writing policy and role models, including legacy empty/all semantics; upgrade/downgrade checks do not touch application data.
- Queued primary/backup/checker work retains earlier accepted choices after edits; new work uses new policy. Include split/retry paths that carry existing accepted choices.
- Admin-only allowance mutation, leader own-team provider switching, cross-team denial, and no secret fields. Update manifest and run API authorization audit.
- Successful/failed explicit checks update safe connection status; rejected replacement key does not poison saved connection status; opening UI performs no inspection.
- Regression checks cover prior revision conflict recovery, busy-label restoration, preset controls, empty selection and mobile/desktop modal behavior.
- Run focused tests with `APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app .venv/bin/pytest`; all DB pytest/audit processes sequential. If sandbox connectivity blocks collection, follow AGENTS host/port check and exact elevated retry. No DSN output or isolation changes.
- Run `.venv/bin/python .github/scripts/check-operational-docs.py`, syntax/diff checks and focused migration/runtime/browser tests. Broaden only for affected contracts or unresolved failures. Update maintained docs only for behavior actually implemented.

## Boundaries

No STT allowance redesign, no new live-provider diagnostics, no content access changes, no arbitrary-error failover, no deployment or application database changes. Existing dirty changes belong to other work and must be preserved. Record unrelated discoveries rather than fixing them opportunistically.
