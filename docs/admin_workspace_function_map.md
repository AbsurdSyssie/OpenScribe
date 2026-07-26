# Admin Workspace Function Map

## Status

This document maps the implemented canonical system-administrator workspace. It replaces the earlier redesign checklist, which mixed completed migration tasks with current behavior and contained obsolete provider-secret revision rules.

Canonical route: `/admin`

The former `/legacy-admin` and `/admin2` compatibility/development routes have been removed. They are not alternate admin entry points; use `/admin`.

Access is system-admin-only and metadata-only on every admin surface.

## Navigation model

Global navigation areas:

- Admin home;
- Teams / Manage teams;
- Account requests;
- System administrators;
- Global defaults;
- De-identification providers;
- Usage;
- Audit;
- Log out.

Selected-team tabs:

- Overview;
- Members;
- Provider policy;
- STT;
- LLM;
- De-identification;
- Defaults;
- Usage;
- Security;
- Danger zone.

Selected team and tab are validated URL state:

`/admin?team_id=<team_uuid>&team_tab=<tab>`

`/admin` without a team shows a neutral global home/selection state. It does not silently remember or guess a team. Invalid team/tab values fall back to a safe global or selected-team state rather than authorizing a hidden scope.

## Non-negotiable boundary

The admin workspace may expose operational metadata, configuration, counts, statuses, and bounded audit/usage details. It must never expose transcript, working-note, dictation, generated-document, prompt, redaction/PII, or uploaded-audio content.

System-administrator authority does not create owner-content access and system-admin accounts do not own transcripts.

All mutation authority remains server-side. Visibility of a control is not authorization.

## Team overview

The selected-team Overview is read-only navigation and operational summary. It can show safe metadata such as:

- member/request counts and account states;
- active provider-selection labels/status;
- provider credential/setup health;
- retention default;
- links to dedicated management tabs.

It must not perform hidden mutations or display owner content.

## Members and account lifecycle

Selected-team Members supports eligible normal users/leaders in that team. Global System administrators handles admin-only accounts.

Current member actions can include:

- create member;
- send activation/setup link;
- send password-reset link;
- send account-recovery link;
- break-glass password/account recovery where policy permits;
- reset MFA;
- suspend;
- reactivate;
- hard delete;
- manage quotas for an eligible selected member.

Protected rules include:

- no manager self-suspend/reactivate/delete;
- leaders cannot manage system administrators or users outside their team;
- the last active system administrator cannot be removed/suspended through ordinary lifecycle actions;
- suspension revokes active sessions/trusted devices;
- reactivation currently forces password-change onboarding and clears previous MFA trust;
- deletion is immediate hard delete with implemented cascades/cleanup and no undo path.

High-risk/destructive operations use explicit POST forms, CSRF, state/role checks, and consequence-specific confirmation.

## Quota management

Quota detail is selected-member URL state under Members:

`/admin?team_id=<team_uuid>&team_tab=members&member_id=<member_uuid>`

It is system-admin-only and resolves only eligible normal members in the selected team. Invalid, cross-team, self, or system-admin targets do not load quota detail.

The current quota dimensions include daily/monthly tokens and daily/monthly audio with base limits, temporary grants, reservations/usage, effective/remaining values, reset boundaries, and revocation/reset operations.

Quota free-text reasons remain administrative ledger data and must not contain patient/clinical content. Security audit receives controlled reason/operation metadata, not the unrestricted free-text reason.

Quota reporting is distinct from Usage telemetry. See [usage_tab.md](usage_tab.md).

## Account requests

Global Account requests supports all-team review by system administrators.

- Approval selects/validates target team and allowed role.
- Rejection records a bounded reason.
- Created users enter the activation/password-change/onboarding lifecycle.
- Request metadata must remain minimal and non-clinical.

Team leaders use the separate own-team workspace route described in [workspace.md](workspace.md).

## Provider policy versus provisioning

Provider policy and provider provisioning are separate concerns.

### Provider policy

The selected-team Provider policy tab manages active team selections/defaults for supported purposes, such as:

- consultation STT;
- post-consultation dictation STT;
- LLM/default model policy;
- hallucination-check configuration;
- de-identification;
- clinical NLP.

Selections reference eligible provisioned configurations. Changing a selection does not reveal or mutate the credential.

### STT and LLM provisioning

The selected-team STT and LLM tabs manage credential-bearing provider configurations. System administrators can create/inspect/finalize/edit/reinspect/diagnose/activate/delete subject to current service constraints.

Provider setup/revision safety:

- incomplete drafts/revisions are not selectable by leaders/users;
- one pending revision can exist for a provider root where supported;
- material endpoint/adapter/model/credential changes are staged and inspected before promotion;
- required-auth drafts/revisions that inherit a saved credential copy it to a draft-owned unique versioned Vault path before committing the draft;
- a draft never persists an alias to the active root's Vault reference;
- promotion updates the stable root transactionally and retires superseded references through durable cleanup;
- cancellation deletes draft metadata then safely/durably cleans the draft-owned reference;
- replacement/removal/deletion uses cleanup intents, retries, and live-reference guards;
- API/browser responses expose bounded status/`has_secret`, never raw secrets or unrestricted references.

Queued/processing generated work can constrain provider edits/deletion so snapshotted runtime behavior stays stable. Credential correction is allowed only through the specific validated service path and does not silently change unrelated active config fields.

See [stt-config.md](stt-config.md), [llm-providers.md](llm-providers.md), and [security.md](security.md).

## De-identification providers

Global De-identification providers manages provider lifecycle. A provider can advertise de-identification, clinical NLP, or both capabilities.

The selected-team De-identification tab manages assignments to the team. Provider policy stores the active de-identification and clinical-NLP selections separately.

Terminology must remain precise:

- detach/remove from team: delete only the assignment;
- delete provider: global lifecycle operation that can affect multiple teams and requires impact checks.

Synthetic inspection content only belongs in admin provider testing. Runtime patient-content responses must not appear in admin pages/logs.

## Defaults and reusable assets

Global defaults manages platform-seed Templates and Quick Actions.

Selected-team Defaults is a metadata/read-only summary and navigation aid for team assets; team leaders manage team Templates/Quick Actions through the user workspace. System administrators do not gain ownership of normal team/personal generation assets.

Reusable configuration must not contain patient content.

## Usage and Audit

Usage provides global or validated selected-team aggregate operational telemetry. Per-user aggregates appear only under selected-team scope. See [usage_tab.md](usage_tab.md).

Audit supports validated filters for safe dimensions such as time range, team, actor ID, action, category, outcome, and sanitized request IP.

Neither surface may include request bodies, transcript-derived content, secrets, raw provider responses, tokens, or plaintext email subjects where hashing/minimization is required.

## Security tab

The selected-team Security tab is posture/navigation metadata, for example:

- member activation/suspension/MFA/recovery status;
- provider setup/credential health;
- links to the responsible member/provider controls.

Lifecycle mutations remain in Members/provider tabs, and security events remain in global Audit.

## Danger zone

The selected-team Danger zone contains team hard deletion and its preflight/blocker information. It is not a suspend/archive function.

Deletion must disclose the current cleanup scope, validate blockers such as attached system administrators, require explicit confirmation, and preserve current transcript-root/provider-secret/content-key cleanup rules. It is irreversible.

## Forms and browser behavior

- Server-rendered forms remain functional without JavaScript where practical.
- Progressive enhancement must preserve CSRF and server authorization.
- Validation errors render field-level and form-summary feedback while preserving only non-secret input.
- Submitted/saved provider credentials are never repopulated into HTML.
- Successful mutations use redirect-after-POST and preserve only validated safe workspace return state.
- Confirmation uses application-owned delegated handlers; inline event attributes remain forbidden under CSP.
- Admin JavaScript/assets are same-origin and compatible with the enforced CSP.

## Responsive and accessibility contract

The workspace must remain usable at laptop/tablet sizes and operable at mobile sizes through collapsed/off-canvas navigation and horizontal table handling where necessary.

Navigation, dialogs, forms, menus, validation summaries, charts, and destructive confirmations must retain keyboard and screen-reader semantics.

## Verification

Relevant checks include:

```bash
pytest -q tests/test_admin_ui.py tests/test_admin_workspace.py tests/test_auth.py
./.venv/bin/python scripts/audit_api_auth.py
```

Use actual collected file names where tests have been split/renamed. The repository contains optional focused browser coverage, including the CSRF browser regression, but does not yet have a comprehensive admin Playwright E2E suite.

## Remaining roadmap

Potential follow-up work:

- broader admin Playwright coverage;
- stale abandoned provider-draft cleanup/visibility improvements;
- team directory search/pagination only when expected deployment scale justifies it;
- additional reversible team-status lifecycle only after access/session/provider/retention effects are designed.

Future changes must preserve the metadata-only boundary, provider secret lifecycle, owner-only content model, quota/outbox semantics, and hard-delete/retention roots.
