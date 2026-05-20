## Working-note correction critique

### 1. Structured context on generation requests

Verdict: keep, with narrower scope.

The design is now explicit: generation accepts `template_id` only. Server loads transcript text, saved dictation, and saved Working note from the DB. Transient generated-note/editor context is not an input.

Fix kept:

- Remove `structured_context` from `GenerateTemplateOutputRequest`.
- Reject extra generation request fields with Pydantic `extra=forbid`.
- Remove API/web/service plumbing that saved request `structured_context` into transcript Working-note storage.

Fix narrowed:

- Do not rename/remove DB fields such as `structured_context_json` yet. They still store structured Working note data and may exist on old generated documents.

### 2. Rename old structured context concepts

Verdict: partial, mostly defer.

Broad rename would churn schemas, workspace payloads, encrypted DB columns, and tests without changing behavior. Keep names where they are storage/API compatibility seams. Prefer clearer docs and no new transient request path.

### 3. Generated notes are never context

Verdict: keep.

Added regression coverage for transcript + dictation + saved Working note + existing edited generated note. Provider request must include transcript/dictation/Working note and exclude generated-note content.

Existing/static coverage still checks dirty Working note save before generation, failed save blocking, Working-note-only generation, and empty-source blocking.

### 4. Dirty Working note concurrency baseline

Verdict: keep.

Workspace refresh can update rendered `updated_at` while dirty editor DOM is preserved. Save must use timestamp from when editing started, not latest refreshed timestamp. Store dirty edit baseline and refresh it only after this tab's own in-flight save succeeds with newer unsaved edits still pending.
