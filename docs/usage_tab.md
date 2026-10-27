# Admin Usage Reporting

## Status

This document describes the implemented system-administrator Usage surfaces and the metadata boundary they must preserve. It is not a quota-policy contract; quota behavior is documented in [api.md](api.md), [security.md](security.md), and [admin_workspace_function_map.md](admin_workspace_function_map.md).

## Routes and scope

Usage has a dedicated system-administrator route in the `/admin` shell:

- global Team view: `/admin/usage`;
- selected-team Team view: `/admin/usage?team_id=<team_uuid>&view=team`;
- selected-team Users view: `/admin/usage?team_id=<team_uuid>&view=users`;
- selected-team Quotas view: `/admin/usage?team_id=<team_uuid>&view=quotas`;
- per-user aggregate rows appear only inside a validated selected-team scope.

The sidebar Usage destination opens this standalone surface without the
selected-team workspace header or its primary tabs. Use the top team picker to
change scope; its Usage links preserve the current view and reporting range
while clearing member and editor context. **All teams**
returns to the global Team view. With a team selected, **Team** shows charts and
operational reports, **Users** shows that team's aggregate user table, and
**Quotas** opens the quota panel without loading usage telemetry. The Usage
range is selected from a compact inline disclosure with direct links, and is
preserved when navigating among these views. Quotas does not show a reporting
range control because quota windows use their own daily and monthly boundaries;
its navigation links retain the last report range. The Users view remains
constrained to users belonging to the validated team. With no team selected,
Usage stays in the global Team view;
Users and Quotas are not available globally and no per-user rows are rendered.

The view defaults to `team`. An unknown view also resolves to Team. Invalid or
unknown team identifiers resolve to the global Team view; requesting Users or
Quotas without a valid team does the same and clears member/editor state. In a
valid team Quotas view, malformed, cross-team, system-admin, or otherwise
ineligible member IDs show a selection error without loading quota detail.

Older Usage URLs remain compatible and redirect to the dedicated route after
the usual authorization and team validation: `/admin?tab=usage`,
`/admin?team_id=<team_uuid>&team_tab=usage`, and the latter with
`usage_view=users`. Legacy selected-team Quotas and member-detail URLs also
redirect to the standalone Quotas view.

Only full system-administrator sessions can access these surfaces. Invalid or missing team identifiers do not create a team scope.

The page is metadata-only. It must not expose:

- transcript, working-note, dictation, generated-document, or prompt text;
- detected/manual PII values;
- uploaded audio;
- provider credentials, unrestricted Vault references, or raw provider responses;
- cookies, setup/reset tokens, TOTP values, recovery codes, or plaintext session identifiers.

## Reporting versus quota enforcement

Usage reporting summarizes retained operational telemetry. It does not reconstruct or control provider quotas.

Quota enforcement uses `provider_attempts`, reservations, grants, limits, and reset boundaries. A quota reset changes the authoritative usage start for its selected window; it does not delete or rewrite reporting telemetry. Therefore a Usage total can legitimately differ from a user's current post-reset quota consumption.

Daily quota windows follow the UTC calendar date, and monthly windows follow the UTC calendar month. Setting an unlimited window to a finite limit counts settled usage from the activation time. Finite limit changes do not restart that period; an explicit reset does. Accepted work already in progress remains counted across a reset until it settles or an unused reservation expires. The quota table shows the inclusive UTC calendar dates and the next reset boundary separately.

Quota management is in the standalone Usage Quotas view. The Members table deep-links to the selected person, and older member-detail links redirect to this route:

`/admin/usage?team_id=<team_uuid>&view=quotas&member_id=<member_uuid>`

### Lifetime accounted usage

The selected-member quota panel shows lifetime accounted tokens and audio from
available `provider_attempts` records. It sums `settled_units` for rows whose
`owner_user_id` matches the selected member, grouped by resource (`tokens` or
`audio_seconds`). The sum spans all dates and does not apply quota windows,
reset boundaries, grants, limit changes, or month rollovers. A row contributes
when settlement is committed. Repeated settlement of the same attempt does
not add a second row to the total.

For tokens, settlement uses the provider-reported total when available. If
usage is unknown, it accounts the attempt's full reservation, which is a
conservative estimate based on the input allowance and output cap and may not
match tokens actually billed. For submitted audio-file and chunk attempts,
settlement uses the measured submitted audio duration rounded up to whole
seconds, including when the provider attempt fails after dispatch. Live-stream
audio uses authorized PCM samples divided by 16,000 and rounded up to whole
seconds; if the final stream usage is uncertain, it uses the full reserved
audio units instead.

Only settled attempts contribute. Reserved work that has not been submitted is
excluded and is cancelled when its reservation expires. Submitted work remains
unresolved until it settles or its deadline passes; after the deadline,
reconciliation settles it conservatively using the full token reservation,
the measured audio duration, or the full live-stream reservation as applicable.
Therefore a submitted attempt can be included after reconciliation even when
the operation did not complete successfully. A failed attempt after
submission still contributes according to its settlement. Missing credentials
or other preparation failures before provider dispatch do not add usage;
pre-dispatch reservations are cancelled. Provider fallback or retry work can
create another attempt and add its own settled units. This describes distinct
provider attempts, not individual network retry counts.

The displayed audio hours are the total settled audio seconds divided by 3,600
and shown to two decimal places alongside the exact duration. Small totals can
therefore display as `0.00 hours` while the duration shows seconds.

These totals cover available records only. Historical activity from before
provider-attempt accounting cannot be reconstructed. Transcript deletion
preserves attempt metadata and its owner attribution under the existing
lifecycle rules. Account deletion follows the existing ownership cleanup;
ownerless records are no longer included in a user's totals. This is an
accounting summary, not a claim of exact historical provider billing.

There is no normal-user or team-leader quota dashboard. Public quota rejection remains a bounded `quota_exceeded` response without allowance, usage, or reset details.

## Data sources

The reporting service aggregates metadata from current persistence models, including:

### Provider usage events

- team and owner identifiers;
- generated-document and transcript references;
- provider/config/adapter/model metadata;
- generation event/status type;
- prompt, completion, and total token counts;
- estimated cost where available;
- total/provider duration;
- bounded error/provider status metadata;
- timestamps.

### Provider attempts and dispatch timing

- provider attempt correlation and attempt kind;
- submitted, settled, and deadline timestamps;
- bounded transport request events: UTC timestamp, ordinal, duration, controlled outcome/status, and numeric retry wait;
- outcome and allowlisted application/provider error metadata.

Each transport request event is counted once. Admin projections select scalar fields and only the fixed transport-event keys; they do not render arbitrary JSON or ORM objects. Older attempts without transport events use their one logical attempt and terminal safe status as a fallback.

### Generated documents

- generator type (`template`, `followup`, `quick_action`);
- status;
- token/duration/cost metadata;
- bounded provider error metadata;
- created, started, and completed timestamps.

### Consultation-split executions

Failure hotspots distinguish **Split analysis** from **Split generation**. For
those stages, a hotspot retains the controlled application failure code and,
when available, the controlled provider code and HTTP status.

The Usage page also has a **Recent split failures** table. It is a bounded
projection of at most 25 retained terminal failed executions within the
selected reporting range and scope. Its range and ordering use the terminal
timestamp, falling back to execution creation time for older rows. Each row
contains only that timestamp, stage, execution UUID, parent operation UUID
(analysis for analysis; batch for generation), controlled application code,
controlled provider code, and HTTP status. It does not serialize an ORM object
or event row to the browser. The all-available range includes retained failed
split executions even when no provider usage event was recorded.

The page also has **Operational health · last 15 minutes** and **Recent failures
and retries** panels. Health counts observed failed provider requests once, including authentication responses, connection errors, and timeouts. Preflight configuration failures that make no request do not count. It retains separate HTTP 429/503 and timeout counts,
including recovered rate-limit retries, reports mean recorded request duration
and queue wait where timestamps exist, and shows the oldest queued work and
currently overdue provider attempts. It warns when queued work is over 60
seconds old, when any provider attempt is overdue, or when at least five
observed requests and at least 20% of observed requests are degraded. These
thresholds concern transport failures; invalid generated output is reported separately in the failure table. They are page indicators, not external notifications. Queue age and
overdue counts describe current open work; request/error and latency summaries
cover the trailing 15 minutes.

The bounded **Recent failures and retries** table covers generation, note
checking, template suggestions, speech ingestion, split failures, and recovered retries.
It groups provider attempts by correlation and operation kind so a failed check
does not hide a generation failure. It shows time, stage, operation reference,
final outcome, retry count, allowlisted error category, safe provider status,
provider adapter, model metadata, and duration. A controlled terminal transport failure appears as Failed even when quota settlement remains conservative. A successful transport with uncertain usage appears as Usage unknown; legacy attempts without evidence retain Unknown outcome. Search accepts a UUID operation reference and/or
a UTC start time within the selected reporting range. It is capped at 50 rows.
The separate detailed split table also shows terminal split failures, including
failures recorded without a provider attempt.

### Transcript ingestion jobs

- team and owner identifiers;
- transcript/job/config references;
- job kind (`audio_file`, `live_chunk`);
- chunk sequence where applicable;
- snapshotted STT adapter/config/model metadata;
- status;
- source byte and duration metadata;
- bounded error code/message;
- created, started, completed, and applied timestamps.

The reporting service returns aggregates to chart code. The recent split and
operational failure tables receive bounded metadata-only projections described
above, never raw event rows.

## Reporting ranges

Supported URL range values:

- `30d`: rolling 30 days, daily buckets, selected by default;
- `90d`: rolling 90 days, daily buckets;
- `1y`: rolling year, weekly buckets;
- `all`: all retained telemetry, monthly buckets.

Invalid values fall back to `30d`.

Fixed ranges compare with the immediately preceding equal-duration period. The all-available range has no previous-period comparison. Exact rolling timestamp boundaries determine KPI/table membership; chart buckets can include a partial first calendar bucket while still excluding events before the exact boundary.

## Implemented presentation

The canonical admin workspace renders:

- a five-column current summary table for total tokens, audio hours, input
  tokens, output tokens, and generations;
- current-versus-previous trend series;
- generation token/activity charts;
- audio/ingestion charts;
- failure charts;
- team comparison rows in global scope;
- provider/model, generator-type, ingestion-type, and failure aggregates;
- selected-team user aggregate rows;
- a selected-team Users view containing the user aggregate table without the
  Team operational-report panels;
- a bounded recent split-failure metadata projection in global and selected-team
  Usage views;
- bounded recent operational failure/retry projections and a 15-minute health
  summary in global and selected-team Usage views.

Charts use the locally vendored Apache ECharts runtime and server-rendered aggregate data. No usage data or runtime code is loaded from a public CDN.

The current summary table formats token counts with thousands separators and
audio duration to two decimal places. Total tokens come from recorded
provider-reported totals, which may differ from input plus output token counts.

Multi-row Usage and selected Quota tables can be sorted in the browser. Sort
buttons compare the rendered rows' typed metadata rather than formatted cell
text: numeric fields use numeric values, timestamps use ISO values, and text
fields use their documented primary key. Composite columns state the key they
use; allowance Amount keeps resource groups in a fixed order and sorts amounts
within each resource. Equal values retain their current relative order, and
missing values remain last in either direction. Sorting only rearranges rows
already present in the bounded table; it does not fetch more records or change
server-side filters. Without JavaScript, tables retain their original readable
order. Sort controls are keyboard buttons and expose the active direction with
`aria-sort`.

Global Usage does not expose per-user rows. The selected-team Team, Users, and
Quotas links retain the validated team identifier and reporting range; the
Users table constrains rows to members of that team. The Quotas link opens the
existing quota-management route and does not carry a selected member.

## Comparison and display rules

- Compare fixed windows with equal previous windows.
- Show percentages for success/failure rates.
- Use per-generation averages where raw token volume would otherwise dominate comparison.
- Display sparse/missing telemetry honestly rather than synthesizing values.
- Estimated cost can remain zero/blank where pricing data is unavailable.
- Provider HTTP status and STT model metadata can be incomplete for older or adapter-specific records.
- Older split-generation records can lack provider code/HTTP metadata because
  those fields were not persisted before the split diagnostics change.
- Keep usage and security reporting separate: frequent client IPs and security events belong in Audit/Security, not Usage.

## Privacy and lifecycle constraints

- System-administrator access to aggregates does not grant owner-content access.
- Usage identifiers and dimensions are operational metadata only.
- Deletion/retention follows the current model/service cleanup rules; this view does not create a parallel content store.
- Charts/tables must not make content-derived classifications unless an explicit privacy-reviewed metadata field is persisted for that purpose.
- Free-text administrative reasons or user-supplied content must not be copied into usage events.
- The split diagnostic projection maps application/provider fields through
  controlled allowlists. Unknown or malformed values become a generic
  `other_split_failure` application code or are omitted; arbitrary persisted
  strings, exception text, provider responses, topic labels, transcript IDs,
  and owner identifiers never render.

## Remaining roadmap

Potential improvements that are not part of the current contract include:

- additional short/custom reporting ranges beyond the implemented `30d`, `90d`, `1y`, and `all` values;
- P50/P95 latency reporting;
- configurable table columns, filters, and grouping;
- improved failed-token accounting where providers expose reliable usage on failures;
- active-seat/login-adoption reporting after an intentional privacy-reviewed session-telemetry design;
- richer pricing/cost reporting after a maintained pricing source and accounting policy exist.

Implement these only through metadata-safe service queries and update this document, the admin workspace map, tests, and README documentation index as appropriate.
