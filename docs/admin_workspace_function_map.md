# Admin workspace function-preservation map

## Resolved redesign questions

| Question | Decision |
|---|---|
| Migration routes? | New design owns `/admin`; current functional design moves to `/legacy-admin`; `/admin2` stays unchanged. |
| May internal `/admin` contain mock panels? | Yes on redesign branch; incomplete redesign never merges to user-facing `master`. |
| Primary organisation? | Mockup's team-first workspace; plug controls into extant functions before workflow redesign. |
| Team-list scaling? | Render full environment list; no search/pagination now. |
| Navigation state? | Validated `team_id` and `team_tab` URL state; links work without JavaScript. |
| No selected team? | Neutral Admin home with safe global counts and select-team prompt; never guess/remember team. |
| Default team tab? | Overview. |
| Overview mutations? | None; summary/navigation only. |
| Provider responsibility split? | Provider policy selects runtime providers; STT/LLM provision team configs; team De-ID attaches/detaches global providers. |
| Global De-ID lifecycle? | Separate sidebar registry; manages De-ID, Clinical NLP, or dual-capability providers. |
| Security tab? | Read-only posture/navigation; member recovery actions stay under Members; events stay in Audit. |
| Danger zone? | Existing team hard-delete only. |
| Retention edits? | System admin may edit team default for future transcript roots only; existing expiry remains fixed. |
| Team asset defaults? | Read-only team summary; leaders manage team assets; sidebar Global defaults manages platform seeds. |
| Account creation? | Team Members creates normal team users; separate System admins area manages admin-only accounts. |
| Member actions? | State-aware menu; consequence-specific confirmation for destructive/high-risk actions. |
| Account requests? | Global review; approval selects team/role; rejection requires reason. |
| Usage? | Global service view plus dedicated team Usage tab with scoped charts and user aggregate table; metadata only. |
| Audit? | Safe filters in URL; never sensitive values. |
| CSS/JS structure? | Jinja partials, progressive-enhancement JS, minimal workspace CSS, reuse site primitives; ask when reuse materially changes mockup. |
| Form feedback? | Inline field errors + summary; site toasts; preserve non-secret input only. |
| Provider wizard refresh/cancel? | Server-backed drafts restore non-secret state; cancel explicitly deletes pending row and safely cleans Vault reference. |
| Provider wizard complexity? | Simple preset path; custom providers reveal progressive Advanced configuration containing every existing expert field. Render only backend-registered provider presets, never mock-only brands. |
| Provider edit safety? | One Edit action reuses add wizard and stages inspected revision for all supported settings. Blank credential securely reuses active root's saved Vault reference; replacement input stages new reference. Active config remains usable until atomic promotion. |
| Sidebar? | Admin home, Teams, Manage teams, Account requests, System admins, Global defaults, De-ID providers, Usage, Audit, Log out. |
| Manage teams? | Metadata directory, create, open; deletion stays in team Danger zone; team status read-only. |
| Responsive target? | Laptop/tablet fully usable; mobile operable with collapsed/stacked/scrollable layout. |
| Development/release? | Long-lived redesign branch, small slice commits, sync master; full parity/security gate before merge to user-facing master. |
| Browser testing? | Semantic E2E for critical workflows; no pixel-perfect snapshots. |

## Purpose

Checklist for redesigning `app/templates/admin.html` around `app/templates/admin_mockup.html` without losing behavior. The mockup is a visual/design input, not a functional specification. A row is complete only when its control, validation, route, service behavior, authorization, feedback, and regression coverage survive.

Related sources: `docs/admin_brief.md`, `docs/usage_tab.md`, `docs/auth.md`, `docs/security.md`, `docs/testing.md`, `app/routes/web_admin.py`, and `tests/test_admin_ui.py`.

## Migration routing decision

- `/admin` hosts the new, incrementally completed design based on `admin_mockup.html`.
- `/legacy-admin` hosts the current functional `admin.html` workspace during migration.
- Existing POST action routes remain under `/admin/...`; `legacy-admin` names the browser page, not a duplicate backend API.
- Redirects after mutations must return to the initiating workspace during migration. New and legacy pages therefore need an explicit, validated return-workspace mechanism rather than blindly returning every action to `/admin`.
- `/legacy-admin` is temporary and may be removed only after every applicable preservation row in this document passes verification.
- Access control is identical for both browser pages: system-admin only, metadata only.
- Migration occurs in an internal development environment. `/admin` may show incomplete mock panels while work proceeds; production users will not receive the route switch until the whole redesign passes its release gate.
- Incomplete controls must be recognisable as mock/incomplete and must not invoke mutation routes accidentally. Functional status remains tracked by this checklist, not inferred from visual completeness.
- Mockup information architecture is the accepted starting structure: team selection in the sidebar; team-level tabs in the workspace; global requests, usage, and audit navigation outside those tabs.
- First implementation pass wires mockup buttons and controls to extant routes/services one-by-one. Backend behavior is reused before any workflow redesign is considered.
- Team sidebar renders the full environment team list as designed. No team search, filters, recency ranking, or pagination are required for the expected deployment scale.
- Selected team and team tab are canonical URL query state (`team_id=<uuid>&team_tab=<key>`). Sidebar/team-tab navigation must work as links; JavaScript may enhance it. Refresh, back/forward, bookmarks, validation failures, and POST redirects preserve valid state.
- `/admin` without `team_id` shows a neutral empty state asking the admin to select a team. It must not guess or remember a team, preventing accidental work in the wrong scope. If no teams exist, empty state instead offers team creation. Global admin areas remain navigable without team scope.
- Selecting a team without an explicit valid `team_tab` opens `overview`; canonical URL becomes `?team_id=<uuid>&team_tab=overview`.
- Team Overview is read-only. It shows operational summaries, warnings, and navigation into dedicated tabs; it performs no provider, member, security, default, or lifecycle mutation.
- Provider tab responsibilities are explicit:
  - `provider-policy`: active conversation/dictation STT, LLM/default model, hallucination checker, de-identification, and Clinical NLP selections.
  - `stt`: provision, inspect, test, edit, and delete STT endpoints.
  - `llm`: provision, inspect, edit, and delete LLM providers.
  - `deidentification`: view available/assigned providers and attach/detach them for selected team.
- Runtime selection is never hidden inside provider provisioning; “available/configured” and “actively used” remain distinct states.
- STT/LLM revision drafts self-link to a ready provider root. Root IDs and selection foreign keys remain stable at promotion; pending revisions never appear in normal provider lists, selection candidates, or runtime resolution.
- One pending revision is allowed per provider root. Revision labels may match their root because normalized label uniqueness applies only to root configs.
- Revision cancellation commits row deletion before cleaning its staged Vault secret. Promotion copies the staged secret reference to the stable root, deletes the revision atomically, then cleans the replaced root secret after commit.
- Downgrading across provider-revision support discards pending STT/LLM revision rows before restoring unconditional team-label uniqueness. Active provider roots remain; staged Vault references may require operator cleanup after rollback.
- De-identification provisioning remains global for this redesign even though team-scoped provisioning may be preferable later. Selected team's De-identification tab shows assigned providers first, then available global providers with attach action.
- Canonical language: **detach/remove from team** deletes only assignment; **delete provider** is global and may affect multiple teams. Global edit/delete controls must disclose cross-team scope and impact.
- Sidebar includes a separate global **De-ID providers** management area. It owns provider create, inspect/ping, edit, and global delete. Team rows never expose global deletion.
- Global De-ID provider registry also manages Clinical NLP-capable providers. Providers expose capability as De-identification, Clinical NLP, or both. Team assignment makes a capable provider available; Provider policy stores separate active De-identification and Clinical NLP selections.
- Team `security` tab is read-only posture: member activation/suspension/MFA/recovery states and provider credential health, with links to relevant member/provider controls. User recovery, MFA reset, and break-glass actions remain under Members; security audit events remain in global Audit.
- Team `danger` tab contains only existing team hard-delete. It must enumerate cleanup scope, surface blockers, and require explicit confirmation. No suspend/archive workflow is introduced without backend/domain design.
- Addition under team `defaults`: system admin can edit team default retention days. Change applies only when creating future transcript roots. Existing transcript expiry timestamps remain fixed and are never shortened or extended by later default edits.
- Team `defaults` also shows a read-only summary of team templates and quick actions, linking to team-leader management where appropriate. System admins do not edit existing team-owned assets there. Sidebar **Global defaults** owns platform default template/quick-action CRUD and does not overwrite existing team assets when changed.
- Team `members` provides **Add member** for a normal user in selected team; `team_id` is fixed by workspace and form chooses team role. Separate sidebar **System admins** area owns admin-only account creation/lifecycle. Normal member form never exposes an `is_system_admin` toggle.
- Each member row has a state-aware Actions menu for suspend/reactivate, activation/password/account recovery, MFA reset, break-glass recovery, and deletion. Only valid actions are shown. Destructive/high-risk actions require consequence-specific confirmation; email actions submit with explicit outcome feedback. Server-side authorization/state validation remains authoritative.
- Account-request approval/rejection lives entirely in global sidebar **Requests**. Approval requires target team and role, then links to created member in that team workspace. Rejection requires a reason and remains metadata-only.
- Global **Usage** defaults to all-team aggregates. Team rows/charts link to URL-scoped team usage, with user drill-down constrained to that team. Existing metadata-only boundary remains unchanged.
- Global **Audit** encodes lookback, team, actor, action, outcome, and resource filter state in validated URL query parameters. URLs may contain safe operational metadata only, never content, secrets, tokens, or plaintext session identifiers.
- Accepted sidebar inventory: Home; full Teams list; Manage teams; Account requests; System admins; Global defaults; De-ID providers; Usage; Audit; Log out. Team workspace tabs: Overview, Members, Provider policy, STT, LLM, De-identification, Defaults, Usage, Security, Danger zone.
- Sidebar **Admin home** links to neutral `/admin`, not `/home`. It shows safe global summary counts plus explicit team-selection prompt and never auto-selects team. Brand link uses same target.
- Sidebar **Manage teams** shows directory metadata (name, status, retention default, member count, provider-health summary), creates teams, and opens team workspaces. It does not hard-delete; deletion remains in selected team's Danger zone.
- Team status is read-only after creation in this redesign. No status mutation is added until session, recording, provider, retention, and lifecycle effects receive separate domain design.
- Production UI structure: Jinja templates/partials for semantic markup and server data, `static/js/admin_workspace.js` for progressive enhancement, and workspace-specific CSS only where existing site tokens/components cannot express design. Reuse shared typography, spacing, color, button, form, card, dialog, table, feedback, and CSRF behavior; do not fork a parallel admin component system.
- Shared-component reuse is not an automatic override of mockup styling. Reuse obvious matches; when reuse would materially change mockup appearance or interaction, pause for explicit user choice with a concrete comparison.
- Form feedback contract: field errors render beside fields plus accessible form-level summary; success and completed destructive outcomes use existing site toast behavior and refresh affected state. Preserve entered non-secret values after failure; never repopulate submitted or saved credentials.
- Provider add/edit wizards use existing server-side draft/finalize flows rather than browser-only state. URL identifies valid draft and step; refresh restores non-secret fields and inspection results. Credential material remains Vault-backed/write-only and is never placed in URL or HTML.
- Cancelling after draft creation uses explicit POST cleanup: delete pending DB draft, commit reference removal, then clean Vault credential according to existing safe ordering, and return to provider list. Browser abandonment may leave a pending draft; add auditable scheduled stale-draft cleanup separately. Cleanup logs contain metadata only.
- Existing provider edits split by risk. Label and availability are cosmetic and may update active config directly. Endpoint, credential, adapter, region, model-discovery, or request/response-contract changes must create a separate pending revision and pass inspection before atomic promotion replaces active configuration.
- Pending revisions must carry explicit lineage to their active STT/LLM config. Runtime resolution and team policy continue referencing active config ID while revision is pending. Finalization atomically applies inspected revision without exposing secrets; only after DB commit may superseded Vault credential cleanup run. Cancellation removes revision and its staged credential only.
- Provider revision lineage, atomic promotion, same-team authorization, post-commit credential cleanup, and focused regressions now exist. Redesign material-edit controls may replace legacy links once they submit `revision_of_config_id` and preserve the staged finalize/cancel flow.

## Non-negotiable boundaries

- System-admin workspace exposes operational metadata, never transcript-derived content.
- Existing server routes and service authorization remain source of truth; visual navigation does not grant access.
- Provider secrets remain write-only and Vault-backed. UI may show secret-present/health state, never raw saved values.
- Destructive actions remain explicit POST operations with CSRF protection and confirmation. Team deletion must preserve blocker preflight and full cleanup semantics.
- Admin workspace confirmations use nonce-backed delegated handlers through `data-confirm-submit`; inline event attributes are forbidden by CSP.
- Provider inspection/test uses synthetic content only. Runtime transcript content never appears here.
- User/team ownership, retention, encryption, structured-note contract, and transcript-root cascades are not redesigned by this work.

## Page shell and read behavior

| Preserve | Browser entry / backend | Redesign check |
|---|---|---|
| Load new admin workspace and selected tab/team/provider context | `GET /admin` -> new mockup-based template using existing presentation context | [ ] |
| Load temporary current workspace | `GET /legacy-admin` -> current `admin.html` and presentation context | [ ] |
| Preserve initiating workspace after POST redirect | validated new/legacy return context in `web_admin.py` | [ ] |
| Existing alternate workspace | `GET /admin2`; retained unchanged as secondary developer reference, not official fallback or source of truth | [ ] |
| Sign out | `POST /logout` | [ ] |
| Flash success/error and reopen originating tab/subtab | redirect/query state assembled in `web_admin.py` | [ ] |
| Team scope selection without mutation | `GET /admin?team_id=...&tab=...` | [ ] |
| System-admin-only authorization | route dependencies plus service guards | [ ] |

## Teams, people, recovery, and requests

| User function | Route | Primary service behavior | Preserve |
|---|---|---|---|
| Create team | `POST /admin/teams` | `admin.create_team` (includes default asset seeding) | [ ] |
| Hard-delete team | `POST /admin/teams/{team_id}/delete` | `admin.delete_team`; blocker preflight, complete cleanup, post-commit credential cleanup | [ ] |
| Create user/system admin | `POST /admin/users` | `admin.create_user`; team/role/system-admin invariants and user DEK creation | [x] Team-member creation; system-admin surface pending |
| Suspend user | `POST /admin/users/{user_id}/suspend` | `admin.suspend_user`; immediate session revocation | [x] |
| Reactivate user | `POST /admin/users/{user_id}/reactivate` | `admin.reactivate_user` | [x] |
| Delete user | `POST /admin/users/{user_id}/delete` | `admin.delete_user`; transcript-derived/personal asset cleanup and metadata reassignment | [x] |
| Send activation | `POST /admin/users/{user_id}/send-activation` | account activation/recovery service | [x] |
| Send password reset | `POST /admin/users/{user_id}/send-password-reset` | account recovery service | [ ] |
| Break-glass password reset | `POST /admin/users/{user_id}/break-glass-password-reset` | account recovery service; explicit high-risk flow | [ ] |
| Reset MFA | `POST /admin/users/{user_id}/reset-mfa` | account recovery service | [x] |
| Send account recovery | `POST /admin/users/{user_id}/send-account-recovery` | account recovery service | [x] |
| Break-glass account recovery | `POST /admin/users/{user_id}/break-glass-account-recovery` | account recovery service; explicit high-risk flow | [ ] |
| Approve account request and create account | `POST /admin/account-requests/{request_id}/approve` | `admin.approve_account_request` | [ ] |
| Reject account request | `POST /admin/account-requests/{request_id}/reject` | `admin.reject_account_request` | [ ] |

Legacy aliases `recover-password` and `recover-account` also exist in `web_admin.py`; confirm whether retained as compatibility routes before removing any links/tests.

## STT provider setup and team policy

| User function | Route | Primary service behavior | Preserve |
|---|---|---|---|
| Inspect unsaved endpoint | `POST /admin/stt-configs/inspect` | `stt.inspect_stt_contract` | [ ] |
| Create inspection draft | `POST /admin/stt-configs/drafts` | draft/Vault-backed credential staging | [x] |
| Inspect in provider wizard | `POST /api/v1/stt-configs/drafts` | server result drives inspection/defaults; cancel deletes draft | [x] |
| Finalize inspected draft | `POST /admin/stt-configs/{config_id}/finalize` | config finalization | [x] |
| Create/update config | `POST /admin/stt-configs` | STT config upsert and credential action | [ ] |
| Stage inspected edit revision | draft route with `revision_of_config_id` | active config remains runtime target until atomic promotion | [x] |
| Replace staged credential | `POST /admin/stt-configs/{config_id}/replace-credential` | credential replacement flow | [ ] |
| Reinspect saved config | `POST /admin/stt-configs/{config_id}/inspect` | saved Vault reference, no secret rendering | [ ] |
| Test with bundled synthetic WAV | `POST /admin/stt-configs/{config_id}/test` | saved contract test | [ ] |
| Delete config and saved secret reference | `POST /admin/stt-configs/{config_id}/delete` | `stt.delete_stt_config` | [ ] |
| Cancel pending setup draft | new explicit draft-cancel POST | pending-row and post-commit Vault-reference cleanup | [ ] |
| Set conversation or dictation policy/model | `POST /admin/stt-selection` | `stt.set_team_stt_selection` | [ ] |
| Clear conversation or dictation policy | `POST /admin/stt-selection/clear` | `stt.clear_team_stt_selection` | [ ] |

Preserve adapter-specific fields, inspection output, model discovery/override, language/default mappings, generic REST request/response mappings, active state, credential keep/replace/remove choice, duplicate warning confirmation, and invalid-config selection clearing.

## LLM providers and policies

| User function | Route | Primary service behavior | Preserve |
|---|---|---|---|
| Inspect/discover unsaved provider | `POST /admin/llm-configs/inspect` | `llm.inspect_llm_contract` | [ ] |
| Create inspection draft | `POST /admin/llm-configs/drafts` | draft/Vault-backed credential staging | [x] |
| Inspect in provider wizard | `POST /api/v1/llm-configs/drafts` | server result drives model/default controls; cancel deletes draft | [x] |
| Finalize inspected draft | `POST /admin/llm-configs/{config_id}/finalize` | config finalization | [x] |
| Create/update provider | `POST /admin/llm-configs` | LLM config upsert and credential action | [ ] |
| Stage inspected edit revision | draft route with `revision_of_config_id` | active config remains runtime target until atomic promotion | [x] |
| Replace staged credential | `POST /admin/llm-configs/{config_id}/replace-credential` | credential replacement flow | [ ] |
| Reinspect saved provider | `POST /admin/llm-configs/{config_id}/inspect` | `llm.inspect_saved_llm_config` | [ ] |
| Delete provider | `POST /admin/llm-configs/{config_id}/delete` | `llm.delete_llm_config` | [ ] |
| Cancel pending setup draft | new explicit draft-cancel POST | pending-row and post-commit Vault-reference cleanup | [ ] |
| Set allowed models and team default | `POST /admin/llm-selection` | `llm.set_team_llm_selection` | [ ] |
| Clear team LLM policy | `POST /admin/llm-selection/clear` | `llm.clear_team_llm_selection` | [ ] |
| Set hallucination-check provider/model | `POST /admin/hallucination-check-selection` | `llm.set_team_hallucination_check_selection` | [ ] |
| Clear hallucination-check policy | `POST /admin/hallucination-check-selection/clear` | `llm.clear_team_hallucination_check_selection` | [ ] |

Preserve provider presets, corrected preset base URLs, Bedrock region-derived endpoint, optional-token providers, credential keep/replace/remove, discovered visible-model subset, and rule that default/checker model must be discovered and selectable.

## De-identification and clinical NLP

| User function | Route | Primary service behavior | Preserve |
|---|---|---|---|
| Inspect OpenAPI/docs or synthetic ping | `POST /admin/deidentification-providers/inspect` | `deidentification.inspect_deidentification_provider` | [ ] |
| Create/update provider | `POST /admin/deidentification-providers` | `deidentification.upsert_deidentification_provider` | [ ] |
| Delete provider | `POST /admin/deidentification-providers/{provider_id}/delete` | `deidentification.delete_deidentification_provider` | [ ] |
| Assign provider to team | `POST /admin/deidentification-provider-assignments` | `deidentification.assign_deidentification_provider_to_team` | [x] |
| Remove assignment | `POST /admin/deidentification-provider-assignments/remove` | `deidentification.remove_deidentification_provider_assignment` | [x] |
| Select team redaction provider | `POST /admin/deidentification-selection` | `deidentification.set_team_deidentification_selection` | [ ] |
| Clear to native Presidio fallback | `POST /admin/deidentification-selection/clear` | `deidentification.clear_team_deidentification_selection` | [ ] |
| Select team clinical NLP provider | `POST /admin/clinical-nlp-selection` | `deidentification.set_team_clinical_nlp_selection` | [ ] |
| Clear clinical NLP selection | `POST /admin/clinical-nlp-selection/clear` | `deidentification.clear_team_clinical_nlp_selection` | [ ] |

Preserve HTTPS/private-network validation, write-only bearer token flow, discovery path versus runtime detect path distinction, request/response mappings, synthetic response inspection, assignment-before-selection, and native fallback.

## Defaults

| User function | Route | Preserve |
|---|---|---|
| Open default template editor | `GET /admin/templates/editor?scope=default` | [ ] |
| Create/update default template, global/per-section instructions, ordering, active state | `POST /admin/default-templates` | [ ] |
| Duplicate default template | `POST /admin/default-templates/{template_id}/duplicate` | [ ] |
| Delete default template | `POST /admin/default-templates/{template_id}/delete` | [ ] |
| Create/update default quick action | `POST /admin/default-quick-actions` | [ ] |
| Duplicate default quick action | `POST /admin/default-quick-actions/{quick_action_id}/duplicate` | [ ] |
| Delete default quick action | `POST /admin/default-quick-actions/{quick_action_id}/delete` | [ ] |

Preserve EMIS allowed-section validation, structured/freeform conditional fields, empty-section omission contract, and default-asset seeding behavior for new teams.

## Usage and audit

| Read function | Backend | Preserve |
|---|---|---|
| Usage overview KPIs, charts, team/provider/model/document/ingestion/failure aggregates | `GET /admin?tab=usage` -> `admin.admin_usage_overview` and presentation helpers | [ ] |
| Team/user scope and local usage subtabs/filters | same GET query context | [x] Dedicated team Usage tab, shared range controls/charts, and team-scoped user aggregate table wired. |
| Security audit metadata/signals, lookback, actor/action/outcome/resource filters, caps | `GET /admin?tab=audit` -> audit query/detection services and presentation helpers | [ ] |

Both areas must remain metadata-only: IDs, statuses, counts, durations, token/cost estimates, provider/model names, failure codes. No transcript, note, prompt, response, secret, token, or plaintext session identifier.

## Client-side behavior that can be lost without changing routes

- [ ] Main tab and provider subtab selection; URL/history state and post-action return target.
- [ ] Validate `team_id` and `team_tab` against accessible teams/known keys; never trust return-state input as authorization.
- [ ] Team selection updates all team-scoped forms and hidden `team_id` values.
- [ ] STT/LLM discovered-model controls remain synced to submitted fields.
- [ ] Adapter/provider-dependent fields, required state, presets, and credential defaults update correctly.
- [ ] Inspection wizard retains entered non-secret values while never replaying a secret.
- [ ] Confirmations cover provider deletion, selection clearing, asset deletion, user deletion, and team deletion.
- [ ] Protected/current system-admin and last-active-admin constraints remain understandable in UI and enforced server-side.
- [ ] Error summary and field errors are visible; focus and keyboard operation work in dialogs/tabs/dropdowns.
- [ ] Pending request count, statuses, credential health, active selections, and provider assignment/selection distinction remain visible.
- [ ] Responsive tables/cards do not hide actions or operational metadata.

## Mockup gap register (initial inspection)

`admin_mockup.html` currently demonstrates shell, team sidebar, team tabs, provider-policy cards, member rows, and STT/LLM add-provider wizards. Much content is static and several controls are client-only.

- [ ] Replace hard-coded teams, members, providers, counts, endpoints, and models with server context.
- [ ] Decide mapping between global navigation (`requests`, `usage`, `audit`) and team workspace tabs.
- [ ] Add complete team overview, members lifecycle/recovery, defaults, security, and danger-zone behavior.
- [ ] Add de-identification, clinical NLP, hallucination-check, audit, usage, request, and default-asset flows.
- [ ] Add De-identification team tab beside STT and LLM for attach/detach; keep active selections in Provider policy.
- [ ] De-identification tab distinguishes assigned, available-to-attach, active, detached, and globally deleted states.
- [ ] Add sidebar De-ID providers area for global registry lifecycle; deletion shows cross-team assignment impact and explicit confirmation.
- [ ] Expand STT/LLM wizards to every existing adapter field, inspect/test/edit/delete and credential lifecycle.
- [x] Add STT/LLM pending-revision lineage and atomic promotion before replacing legacy material-edit links; cosmetic-only edits may remain direct.
- [ ] Use POST + CSRF for logout and all mutations; mockup currently links logout with GET semantics.
- [ ] Preserve server-rendered fallback/progressive enhancement where practical.

## Mockup-first wiring sequence

1. [ ] Swap browser routing: new `/admin`, current `/legacy-admin`, unchanged `/admin2`.
2. [ ] Build shared shell, full team sidebar, no-team empty state, and URL-backed navigation.
3. [ ] Wire read-only Team Overview.
4. [ ] Wire Members and account lifecycle/recovery.
5. [x] Wire six Provider policy rows (consultation STT, dictation STT, writing assistant, hallucination checker, De-identification, Clinical NLP) with inline save/clear controls and discovered-model synchronization.
6. [ ] Wire STT, LLM, team De-ID assignment, and global De-ID registry.
7. [ ] Wire Defaults, Security, and Danger zone.
8. [ ] Wire global Requests, Usage, and Audit.
9. [ ] Complete parity and release gate; retire legacy only after every applicable check passes.

Across slices:

- [ ] Extract mockup inline CSS/JS into maintainable assets while replacing duplicate styles with existing site tokens/components.
- [ ] Map every visible mockup control to an existing function-map row or mark it explicitly mock-only.
- [ ] Wire mutations with existing CSRF, validation, confirmations, redirects, and error feedback intact.
- [ ] Add missing extant functions into mockup structure; evolve workflow only after parity is measurable.

## Verification gate for each migrated slice

- [ ] Compare rendered controls against this map and `docs/admin_brief.md`.
- [ ] Add/update focused route, authorization, validation, secret non-disclosure, destructive-action, and UI regression tests.
- [ ] Run `.venv/bin/pytest -q tests/test_admin_ui.py` plus affected API/service/migration tests.
- [ ] Update `docs/admin_brief.md`, `docs/testing.md`, and relevant feature/provider docs.
- [ ] Record scope, files, tests, docs, risks, and architecture checkpoints in `docs/progress` daily note.

## User-facing `master` release gate

`master` is user-facing. Incomplete redesign work must remain outside `master`, even though `/legacy-admin` exists. Before merging redesign to `master`:

- [ ] Every applicable preservation-map row is checked with test or direct evidence.
- [ ] Every migrated slice's focused tests pass.
- [ ] Full `tests/test_admin_ui.py` and affected API/provider/auth/deletion/migration suites pass.
- [ ] Side-by-side workflow comparison with `/legacy-admin` finds no missing behavior.
- [ ] Keyboard, focus, dialog, responsive, and supported-browser smoke checks pass.
- [ ] No mock-only mutation controls or misleading incomplete states remain.
- [ ] Security review confirms metadata-only admin visibility, write-only secrets, CSRF, authorization, safe redirects, and destructive-action semantics.
- [ ] Docs and final progress/change summary are complete.
- [ ] Semantic browser E2E tests cover team/URL navigation, member lifecycle, provider inspect/finalize/cancel, provider policy, team-deletion confirmation/blockers, and initiating-workspace redirect preservation. Avoid pixel-perfect screenshot assertions.

Development uses one long-lived redesign branch with small, reviewable commits per slice. Regularly incorporate current `master` and rerun affected parity checks so the final merge does not hide accumulated drift.

## Architecture checkpoints for redesign

- **Schema:** presentation redesign should require no schema change. Escalate any proposed persistence change.
- **Auth/ownership:** all content remains system-admin operational metadata; no owner content path added.
- **Lifecycle/deletion:** route/service semantics and immediate confirmation remain unchanged.
- **Providers/secrets:** current assignment, selection, fallback, Vault, inspection, and credential cleanup rules remain unchanged.
- **Structured notes:** EMIS template contract remains unchanged.
- **Docs/tests:** this map is living acceptance checklist; mark items only with test or direct verified evidence.
# Provider edit action

- One `Edit` button opens existing STT/LLM add wizard in edit mode with supported non-secret root settings populated.
- Credential input stays blank. Blank means keep active root credential; supplied value stages replacement. Shared reference survives revision cancellation and same-reference finalization.
- Draft redirect retains selected revision ID so finish/discard card remains visible. Normal provider lists continue excluding revisions.
