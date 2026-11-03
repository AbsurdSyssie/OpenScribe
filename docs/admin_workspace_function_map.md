# Admin Workspace Function Map

## Status

This document maps the implemented canonical system-administrator workspace. It replaces the earlier redesign checklist, which mixed completed migration tasks with current behavior and contained obsolete provider-secret revision rules.

Canonical route: `/admin`

The former `/legacy-admin` and `/admin2` compatibility/development routes have been removed. They are not alternate admin entry points; use `/admin`.

Access is system-admin-only and metadata-only on every admin surface.

## Navigation model

The Admin landing destination is All teams Usage at
`/admin/usage?view=team&range=30d`. Bare `/admin` and the legacy Admin home
and Manage teams destinations redirect there. A compact Team picker lists
active teams before suspended teams; suspended teams remain searchable and
selectable. Create team opens the existing Name, Status and retention form in
a dialog. A successful creation selects the new team and opens its Overview;
validation errors keep the dialog and safe non-secret values visible.

The sidebar groups Usage & Quotas, Providers, Members, Security & Data, and Team
workspace under Team settings, using the same link spacing as Global
administration. Members opens the selected-team Members route and stays
selected when switching teams. Security & data contains account-security
status, transcript retention and a clearly separated Danger zone. Team
workspace opens the team overview. Providers opens the selected team's Provider
policy, STT, LLM and De-identification pages; without a selected team it
presents a team picker. Its four pages use a visible provider navigation row,
and the sidebar destination remains active across them. The global De-ID
providers page remains separate.
Global pages include Account requests, System admins, Global defaults, De-ID
providers, Audit and Legal content. Selecting a global page does not apply the
remembered team as a data filter. The sidebar keeps its team destinations
visible and uses the validated remembered team for their links; if no team is
remembered, those links open Providers so an administrator can choose a team.
The Usage & Quotas link restores the remembered team's Usage view. The global
team scope control remains unscoped. The bottom account disclosure retains the
existing POST logout action; the brand is not a link.

On desktop, the admin shell fills the viewport. The brand stays at the top of
the sidebar, account controls stay at the bottom, and only the navigation
middle scrolls when the viewport is short. Main content scrolls independently;
the centered legal links and browser-storage notice follow the page content at
the bottom of that scroll area. Short pages place them at the bottom of the
content viewport; long pages reveal them after scrolling to the end. At narrow
widths, the shell returns to one document scroll with the navigation
disclosure, avoiding nested scroll areas.

Selected-team pages do not repeat a second team-settings menu in the content
area. Usage & Quotas, Providers, Members, Security & Data, and Team workspace
are direct sidebar destinations. Quotas and Usage are grouped under Usage &
Quotas; team quota
defaults appear at the top of Usage → Quotas, ahead of member-specific quota
management. The Quotas list filters eligible members by name or email; each
row has a fixed-width **Usage** toggle and a separate **Edit quotas** action.
The toggle expands the member's usage below the row without changing its label
or column width. Editing opens a modal while the defaults and member list stay
in place. The list shows the selected Daily or Monthly UTC window for token and audio
quotas, with Monthly selected by default; its progress includes unsettled
accepted work and identifies pending units separately.
Provider policy, STT, LLM and De-identification are managed in the
dedicated Providers area. Old `team_tab=defaults` URLs redirect to Usage →
Quotas; old Security and Danger zone URLs redirect to Security & Data. These
links preserve validated team scope; switching teams keeps the current
destination, and All teams returns to the Providers team picker.

Within standalone Usage, a validated team scope exposes Team, Users and Quotas
tabs beneath an area-title header that uses the same summary bar as Members.
The title changes to Team usage, User usage or Quotas with the selected team
shown in supporting text. The Users table remains limited to that team, and
Quotas does not load reporting telemetry. Provider pages use the same summary
bar with Providers as the area title.

At narrow widths, team destinations remain in the Admin navigation disclosure
with their team scope preserved. Provider pages use a visible
four-link navigation row beneath the same selected-team area-title header; it
uses the shared Team settings tab style, wraps at narrow widths and remains
reachable without opening the team menu. At
desktop widths the navigation destinations are visible in their respective
sidebar groups.

Selected team and tab are validated URL state:

`/admin?team_id=<team_uuid>&team_tab=<tab>`

`/admin` with a selected team shows that team's validated workspace tab. A
missing team does not silently filter or guess a team for global administration;
the legacy bare route redirects to All teams Usage. Invalid team/tab values
fall back to a safe global or selected-team state rather than authorizing a
hidden scope. Explicit All teams navigation clears the remembered team.

The per-tab `sessionStorage` navigation hint is account-scoped and contains
only a validated team ID, an allowlisted section/Usage view, and an allowlisted
Usage range. It never stores member selection, quota editing state, monitoring
filters, form values, or credentials. Explicit URLs, All teams, browser history
and refresh remain authoritative. Unsaved POST-form edits prompt for Stay or
Discard before navigation; normal explicit form submission remains available.

On narrow screens, the Admin navigation disclosure exposes the existing global destinations and Log out. It does not change authorization or selected-team scope. Provider setup and member dialogs move keyboard focus inside while open, keep background controls inactive, and return focus to their opening control when dismissed. Provider choices and member-action disclosures expose their selected or expanded state to assistive technology.

Security & data presents retention guidance beside its numeric control and
Save action on desktop; on narrow screens the guidance sits above the input
and Save action. The setting retains its existing 1–90 day range and applies
only to transcript roots created after the update. Quota defaults retain their
existing form and validation in Usage → Quotas.

Wide data grids scroll within their own labelled regions while surrounding navigation and forms reflow. Required operator legal-setup information remains visible in the page; transient status messages use live-region semantics.

Usage reporting ranges apply through an explicit Apply button. Populated charts have keyboard controls for the displayed period range and visible series, plus one server-rendered data table containing every reporting bucket and all four metrics. The table includes the corresponding previous-period buckets when comparison is available, and remains available without chart JavaScript. These controls change presentation only; they do not change usage accounting or data scope.

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
- open Usage → Quotas for an eligible selected member.

Protected rules include:

- no manager self-suspend/reactivate/delete;
- leaders cannot manage system administrators or users outside their team;
- the last active system administrator cannot be removed/suspended through ordinary lifecycle actions;
- suspension revokes active sessions/trusted devices;
- reactivation currently forces password-change onboarding and clears previous MFA trust;
- deletion is immediate hard delete with implemented cascades/cleanup and no undo path.

High-risk/destructive operations use explicit POST forms, CSRF, state/role checks, and consequence-specific confirmation.

## Quota management

Quota detail is selected-member URL state in the standalone Usage Quotas view:

`/admin/usage?team_id=<team_uuid>&view=quotas&member_id=<member_uuid>`

The Members table links directly to this view. Older Quotas and
`team_tab=members&member_id=...` links redirect to the standalone route. A
searchable table lists only eligible members of the selected team. It is
system-admin-only and resolves only eligible normal members in the selected
team. Invalid, cross-team, self, or system-admin targets do not load quota
detail or fall back to another member.

Each row's **See usage** action expands read-only daily/monthly usage details,
reset dates, temporary allowances, remaining capacity, and lifetime accounted
usage in place. **Collapse usage** returns to the same list while preserving the
search, quota period, and reporting range. **Edit quotas** opens a focused modal
with the existing finite/Unlimited controls and required change reason. Zero
remains a finite disabled limit. A successful save returns to the table with
updated usage; validation errors reopen the modal with entered values. The
expanded usage links to the established selected-member overview for allowance,
reset, revoke, and history actions. Direct legacy member URLs continue to show
that full overview. Eligibility and team-scoped authorization apply to every
selected-member view.

The current quota dimensions include daily/monthly tokens and daily/monthly audio with base limits, temporary grants, reservations/usage, effective/remaining values, reset boundaries, and revocation/reset operations.

Usage → Quotas manages finite daily/monthly token and audio defaults for new
members alongside individual quota management. The team overview links to
Security & data for retention settings. Retention edits use the existing team
retention action and apply only to transcript roots created afterward. New
users, including account-request approvals, receive a snapshot of quota
defaults in their user quota fields; changing a team default does not change
existing members. Zero disables the relevant resource and period. Existing
members can still receive individual finite limits or Unlimited through Edit
quotas in the member table.

When an active team LLM selection, backup, or checker has an assigned model, the
member limit editor and team defaults show an advisory when a finite token
limit is at or below that model's known output cap. The message explains that
one generation or check can reserve that many output tokens plus variable
input tokens, so the limit may block generation or checks; longer and split
requests may need more. This warning is advisory and does not change quota
validation or enforcement. Zero and Unlimited do not trigger it, and global
defaults without a selected team do not infer a model.

Daily usage follows the current UTC calendar date; monthly usage follows the current UTC calendar month. Setting a previously unlimited window to a finite limit does not restart it: settled usage already recorded in the current natural period counts toward the new limit. An explicit reset starts usage from its reset time. Accepted work still in progress remains counted across a reset until it settles or its unused reservation expires. The quota cards show each calendar period's UTC dates and its next reset boundary.

The selected-member panel also shows lifetime accounted tokens and audio. For the calculation, settlement and estimate rules, and lifecycle limits, see [Lifetime accounted usage](usage_tab.md#lifetime-accounted-usage).

Quota free-text reasons remain administrative ledger data and must not contain patient/clinical content. Security audit receives controlled reason/operation metadata, not the unrestricted free-text reason.

Quota reporting is distinct from Usage telemetry. See [usage_tab.md](usage_tab.md).

## Account requests

Global Account requests supports all-team review by system administrators.

- The admin page lists pending requests in a table; approved or rejected requests leave the review list.
- Approval selects/validates target team and allowed role.
- Rejection records bounded review notes.
- Created users enter the activation/password-change/onboarding lifecycle.
- Request metadata must remain minimal and non-clinical.

Team leaders use the separate own-team workspace route described in [workspace.md](workspace.md).

## Provider policy versus provisioning

Provider policy and provider provisioning are separate concerns.

### Provider policy

The selected-team Provider policy tab manages active team selections/defaults for supported purposes, such as:

- consultation STT;
- post-consultation dictation STT;
- writing-provider selection and saved provider model policy;
- an optional system-admin-owned writing-assistant backup provider and model;
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

The STT and LLM setup dialogs show provider errors inside their own dialog. During
inspection, finalization, and draft cancellation, the dialog reports progress,
marks its active form area busy, and prevents edits or duplicate requests. Failed
requests keep entered non-secret settings available for recovery; supported
field errors focus a visible matching field, while other errors keep focus on
the dialog alert. A lost network response has an unconfirmed outcome, so the UI
asks administrators to reload the provider list before repeating the request.
When inspection returns a known upstream HTTP status or allowlisted provider
error code, the dialog includes that bounded diagnostic and a matching recovery
step. It never displays provider response bodies or arbitrary provider messages.
The Connection tab has an explicit **Test connection** action. It checks a
fresh draft and leaves the live provider unchanged. Connection fields remain
editable after the check; a changed connection causes the tested draft to be
durably discarded before Save. Test always performs a fresh check. Save reuses
an unchanged tested draft. Without a prior test, Save checks the connection and
can complete the revision automatically. Cancel discards any draft before the
modal closes. API keys use one password field; the page shows only whether a
key is saved, and replacing it requires pasting the new key. Optional saved
bearer keys are removed only with the explicit **Remove saved key** action.

If a connection check or save finds unfinished changes for the same provider,
the editor can show a read-only summary of that earlier revision in the current
settings dialog. The summary contains safe connection metadata and whether
authentication is configured; it never displays a secret or Vault reference.
Reviewing keeps the current form entries in place and does not test, save,
attach, or apply the earlier revision. The administrator may explicitly
discard the earlier changes. This removes only that pending revision; the
saved connection remains in use and the current editor keeps its entered
values. The administrator must choose to test or save those values afterward.
Closing the editor leaves the earlier revision untouched.

Ready STT and LLM providers have **Connection** and **Models** tabs under
**Edit settings**. Connection edits the provider name, team availability, and
connection details. Name and availability changes use the scoped details route
without inspecting the provider or changing its credential. A connection
change uses the existing revision flow; it saves directly when the saved model
remains available and asks for a deliberate model choice when it does not.
Optional authentication presets use no authentication when no key is
configured; keeping a saved optional key requires entering it again, and
removal is explicit. Models refreshes a provider catalog only when requested.
Opening a provider editor does not contact the provider. The LLM provider row
separates team availability from the last explicit connection check, with a
timestamp and bounded status wording.

Finalizing a new LLM connection creates an empty model policy and does not
select it for writing. The editor opens its **Models** tab and asks the
administrator to choose the models the team can use. After saving a nonempty
policy with a default, an administrator may assign that provider under
**Provider policy**.

For LLMs, **Models** edits that provider's saved enabled models and writing
default, even when it is not the active writing provider. Enabled models are
the overall allowance for writing, backup, and checking; an empty list means
none. A nonempty list needs an enabled default. Save models never changes the
active writing provider. Search and the All models / Enabled filters only
change the view. Refresh retains matching draft choices, leaves new models
unchecked, and marks removed saved IDs for repair without changing the saved
policy. Saving a restriction can make an assigned role unavailable; the editor
shows the affected roles before Save and leaves their provider/model choices
visible for repair. The Provider policy table shows role warnings with text
and an icon. System administrators edit model policies; team leaders can choose
an eligible writing provider only, which uses that provider's saved default.
STT models and language remain separate for consultation transcription and
post-consultation dictation.

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

Selected-team Defaults contains the system administrator's team retention and
new-member quota forms, alongside metadata summaries for reusable assets.
Team leaders manage team Templates/Quick Actions through the user workspace.
System administrators do not gain ownership of normal team/personal generation
assets.

Reusable configuration must not contain patient content.

## Usage and Audit

Usage provides global or validated selected-team aggregate operational telemetry, bounded recent failure/retry projections, and 15-minute health indicators. Per-user aggregates appear only under selected-team scope. The operational tables show safe metadata and never grant access to transcript-derived content. See [usage_tab.md](usage_tab.md).

Audit supports validated filters for safe dimensions such as time range, team, actor ID, action, category, outcome, and sanitized request IP. A fully authenticated system administrator can place, renew or release a retention hold on a visible event. Each approval has a controlled reason, bounded reference, owner, review date and expiry; one approval cannot exceed 90 days.

Neither surface may include request bodies, transcript-derived content, secrets, raw provider responses, tokens, or plaintext email subjects where hashing/minimization is required.

## Operator legal content

`/admin?tab=legal` manages the deployment-global operator profile and fixed privacy, cookie/browser-storage, terms, and accessibility documents inside the normal admin side-rail workspace. The former `/admin/legal-content` address redirects to this tab. Access is system-admin-only, requires a full session and CSRF, and must never contain patient, transcript, credential or team-specific content.

The full-width editor accepts a restricted Markdown subset: headings, paragraphs, flat bullet lists, bold and italic text, bounded tables, inline or standalone HTTPS links, and plain email links. The server parses Markdown into validated blocks. Each document remains bounded to 64 KiB and 1,000 blocks. Email links accept one address without query parameters. The parser removes harmless unsupported presentation marks, keeps their wording and warns the administrator. It rejects HTML, unsafe link schemes and structures that could change meaning. Administrators can open a server-generated preview, edit drafts with optimistic concurrency, publish one immutable current version, create a rollback draft from history, and place or release a retention hold. Legal changes and their metadata-only audit events commit together. Missing required profile, contact or notices create warnings; they do not block service use. Accessibility statements follow the same versioning and publishing flow and require no user acceptance step.

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

The workspace must remain usable at laptop/tablet sizes and operable at mobile
sizes. On narrow screens, member and provider-policy data reflows into labeled
stacked rows while wide operational tables remain in named scroll regions.

Navigation, dialogs, forms, menus, validation summaries, charts, and destructive confirmations must retain keyboard and screen-reader semantics.

Ready STT and LLM provider rows use a pencil button to open provider settings
and a separate “More actions” disclosure for the existing test/refresh and
delete forms. The action panel is positioned in the document top layer (with a
viewport-positioned fallback), preserves each form's team and return state, and
keeps deletion behind the existing confirmation handler. Incomplete setup
rows retain their existing secondary actions and omit the ready-only edit
button.

## Verification

Relevant checks include:

```bash
pytest -q tests/test_admin_ui.py tests/test_auth_service.py tests/test_cookie_csrf_security.py
./.venv/bin/python scripts/audit_api_auth.py
```

Use actual collected file names where tests have been split/renamed. The repository contains optional focused browser coverage, including the CSRF browser regression, but does not yet have a comprehensive admin Playwright E2E suite.

## Remaining roadmap

Potential follow-up work:

- broader admin Playwright coverage;
- stale abandoned provider-draft cleanup/visibility improvements;
- team-picker search performance improvements only when expected deployment scale justifies them;
- additional reversible team-status lifecycle only after access/session/provider/retention effects are designed.

Future changes must preserve the metadata-only boundary, provider secret lifecycle, owner-only content model, quota/outbox semantics, and hard-delete/retention roots.
