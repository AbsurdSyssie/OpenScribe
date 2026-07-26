# Admin Usage Reporting

## Status

This document describes the implemented system-administrator Usage surfaces and the metadata boundary they must preserve. It is not a quota-policy contract; quota behavior is documented in [api.md](api.md), [security.md](security.md), and [admin_workspace_function_map.md](admin_workspace_function_map.md).

## Routes and scope

Usage is available in the canonical `/admin` workspace:

- global Usage: `/admin?tab=usage`;
- selected-team Usage: `/admin?team_id=<team_uuid>&team_tab=usage`;
- per-user aggregate rows appear only inside a validated selected-team scope.

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

Quota management remains under a selected team's Members detail:

`/admin?team_id=<team_uuid>&team_tab=members&member_id=<member_uuid>`

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

### Generated documents

- generator type (`template`, `followup`, `quick_action`);
- status;
- token/duration/cost metadata;
- bounded provider error metadata;
- created, started, and completed timestamps.

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

The reporting service returns aggregates to the browser. It does not send raw event rows to chart code.

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

- KPI summary cards;
- current-versus-previous trend series;
- generation token/activity charts;
- audio/ingestion charts;
- failure charts;
- team comparison rows in global scope;
- provider/model, generator-type, ingestion-type, and failure aggregates;
- selected-team user aggregate rows.

Charts use the locally vendored Apache ECharts runtime and server-rendered aggregate data. No usage data or runtime code is loaded from a public CDN.

Global Usage does not expose per-user rows. A selected-team Usage view constrains users and all other aggregates to the validated team.

## Comparison and display rules

- Compare fixed windows with equal previous windows.
- Show percentages for success/failure rates.
- Use per-generation averages where raw token volume would otherwise dominate comparison.
- Display sparse/missing telemetry honestly rather than synthesizing values.
- Estimated cost can remain zero/blank where pricing data is unavailable.
- Provider HTTP status and STT model metadata can be incomplete for older or adapter-specific records.
- Keep usage and security reporting separate: frequent client IPs and security events belong in Audit/Security, not Usage.

## Privacy and lifecycle constraints

- System-administrator access to aggregates does not grant owner-content access.
- Usage identifiers and dimensions are operational metadata only.
- Deletion/retention follows the current model/service cleanup rules; this view does not create a parallel content store.
- Charts/tables must not make content-derived classifications unless an explicit privacy-reviewed metadata field is persisted for that purpose.
- Free-text administrative reasons or user-supplied content must not be copied into usage events.

## Remaining roadmap

Potential improvements that are not part of the current contract include:

- additional short/custom reporting ranges beyond the implemented `30d`, `90d`, `1y`, and `all` values;
- P50/P95 latency reporting;
- configurable table columns, filters, and grouping;
- improved failed-token accounting where providers expose reliable usage on failures;
- active-seat/login-adoption reporting after an intentional privacy-reviewed session-telemetry design;
- richer pricing/cost reporting after a maintained pricing source and accounting policy exist.

Implement these only through metadata-safe service queries and update this document, the admin workspace map, tests, and README documentation index as appropriate.
