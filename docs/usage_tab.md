# Usage Tab

The usage overview uses the standard admin panel inset so its heading, scope,
filter, and KPI row remain clear of the rounded panel border.

## Purpose

- Define the intended system-admin usage experience for `/admin?tab=usage`.
- Keep the page strictly metadata-only.
- Use the telemetry already stored in the database before introducing new schema.

## Scope

- System-admin only.
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

Show large KPI cards for the current 7-day window with change against the previous 7-day window:

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

Show 14-day charts for:

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

## Comparison rules

- Use equal-window comparisons for all deltas.
- Use percentages for success and failure rates.
- Use per-generation averages where token counts would otherwise reward only volume.
- Use activity share bars for fast visual ranking.

## Visual direction

- Keep the page server-rendered in Jinja.
- Prefer chart cards and comparison cards over a table-first layout.
- Use small bar charts and in-cell meters instead of pulling in a JS chart dependency.
- Keep the admin visual language aligned with the current flat workspace styling.

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
