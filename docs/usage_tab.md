# Usage Tab

The usage overview uses the standard admin panel inset so its heading, scope,
filter, and KPI row remain clear of the rounded panel border.

## Purpose

- Define the intended system-admin usage experience for `/admin?tab=usage`.
- Keep the page strictly metadata-only.
- Use the telemetry already stored in the database before introducing new schema.

## Scope

- System-admin only.
- `/admin?tab=usage` reports all-team aggregates and labels them as such.
- A validated `team_id` filters the aggregates and visible scope label to that team; invalid IDs do not create a team scope.
- No transcript text.
- No note text.
- No prompt text.
- No provider secrets.
- No content-derived browsing.

## Existing telemetry

### Provider usage events

Source: `provider_usage_events`

Available metadata:

- team
- owner user
- generated document
- transcript id reference
- LLM config id reference
- provider adapter
- model name
- event type: queued, started, completed, failed, enqueue_failed
- status snapshot
- prompt, completion, and total tokens
- estimated cost
- total duration and provider duration
- error code, provider error code, provider HTTP status
- created timestamp

### Generated documents

Source: `generated_documents`

Available metadata:

- generator type: template, followup, quick_action
- status
- token counts
- estimated cost
- duration fields
- provider errors
- created, started, completed timestamps

### Transcript ingestion jobs

Source: `transcript_ingestion_jobs`

Available metadata:

- team
- owner user
- transcript id reference
- job kind: audio_file, live_chunk
- chunk sequence number
- STT config reference
- STT adapter kind and resolved STT metadata snapshots
- status
- source audio size
- source or declared duration
- error code and message
- created, started, completed, applied timestamps

## UX goals

- Give an admin a fast operational read in one screen.
- Emphasize comparison, not just raw tables.
- Make activity trends visible with charts.
- Preserve the repo's privacy boundary by showing only metadata.
- Handle sparse telemetry gracefully where cost or model fields are not always populated.

## Recommended information architecture

### 1. Overview band

Default to monthly data. Show headline metrics for the current rolling 30-day window with change against the previous equal 30-day window:

- completed generations
- input tokens
- output tokens
- processed audio hours
- combined failures

### 2. Window comparison

Show compact cards for:

- last 24 hours
- last 7 days
- last 30 days

Each card should include:

- generated count
- provider success rate
- input tokens
- output tokens
- ingestion jobs
- uploaded volume
- audio hours
- delta vs prior equal window for generation, input tokens, output tokens, and audio

### 3. Daily trend charts

Show 30-day charts by default for:

- completed generations
- input tokens
- output tokens
- ingestion jobs
- failures

### 4. Team comparison

Use a sortable comparison table with in-cell bars for:

- generated count
- provider success rate
- average input and output tokens per generation
- live vs whole-file mix
- uploaded volume
- activity share

### 5. Team drilldown

For all teams or a selected team, show:

- provider and model mix
- generated document type mix
- ingestion mix by STT adapter and job kind
- top failure hotspots

### 6. Per-user comparison

When a team is selected, show:

- summary cards for top active users
- per-user comparison table
- share of team activity
- generation quality and ingestion mix metrics

Implemented in each selected team's **Usage** tab. User rows are constrained by validated team scope and show account metadata plus aggregate counts/tokens/audio/rates only. Global Usage does not expose per-user rows.

## Comparison rules

- Use equal-window comparisons for all deltas.
- Use percentages for success and failure rates.
- Use per-generation averages where token counts would otherwise reward only volume.
- Use activity share bars for fast visual ranking.

## Reporting ranges

- Default URL state is `range=30d` with daily buckets.
- `range=90d` uses daily buckets.
- `range=1y` uses weekly buckets.
- `range=all` starts at oldest retained provider or ingestion metadata and uses monthly buckets.
- Invalid range values fall back to 30 days.
- Fixed ranges compare against previous equal period. All-available range omits comparison because no earlier retained period exists.
- Fixed ranges use exact rolling timestamp boundaries for KPI cards, tables, and charts. Daily charts retain the partial first calendar-day bucket instead of dropping its first 24 hours; events before the exact boundary remain excluded.
- ECharts zoom explores aggregate buckets already returned; changing reporting range performs a new server-side aggregate query. Raw events are never sent to chart code.

## Visual direction

- Keep the page server-rendered in Jinja.
- Use a graph-first layout with a single KPI rail, dominant chart canvas, and aggregate tables.
- Do not use floating card grids for Usage. Separate sections with whitespace and restrained rules.
- Use locally vendored Apache ECharts 6.1.0 for responsive SVG charts, axes, tooltips, zoom, and current/previous period comparisons. Do not load chart code or usage data from a third-party CDN.
- Keep the admin visual language aligned with the current flat workspace styling.

## Redesign progress

- New `/admin` workspace defaults KPI rail, daily token/audio/failure charts, team comparison, and provider/activity/failure aggregates to rolling 30-day data. Full-width ECharts plots cluster solid current-period bars with shaded previous-30-day bars on a shared scale.
- Team table reuses existing usage rollup service and links each team into URL-scoped usage view. Detailed team breakdown belongs in each team's Usage area.
- Team workspace now includes Usage with the same reporting ranges and charts plus a team-scoped user breakdown table.
- Security and frequent-IP reporting belongs in Audit/Security, not Usage.
- Currency/cost reporting is excluded. Consumption uses input tokens, output tokens, audio hours, jobs, failures, rates, and latency.
- Planned follow-up: global 24-hour/7-day/30-day/90-day/custom range control (30 days selected by default), equal-period shaded comparison series, failed-token accounting, P50/P95 latency, and URL-configured table columns/filters/grouping.

## Limits of current telemetry

- Estimated cost exists structurally but may remain zero if pricing data is unavailable.
- Provider HTTP status exists structurally but may be sparse in live data.
- STT model-level comparison may be incomplete where `stt_model_name` is not populated.
- Active-seat or login-adoption views are out of scope until session telemetry is intentionally added.

## Architecture constraints

- Privacy boundaries preserved: metadata-only observability.
- Ownership rules preserved: system-admin-only route, no transcript-derived content access.
- Deletion semantics preserved: usage rows continue to follow existing cleanup rules.
- Provider rules preserved: this page reports provider metadata but does not expose secrets.
- Structured-note contract preserved: generator reporting must not change EMIS or output JSON rules.
