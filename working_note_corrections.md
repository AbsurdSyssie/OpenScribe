## Working-note correction critique

Status: reviewed and pruned on 2026-05-21.

### Kept

1. Generate availability must ignore generated-note-only content.

Reason: generated-note editor rows are output, not generation source context. Generation now uses saved transcript text, saved dictation, and saved/dirty Working note only.

Applied rule:

```js
const canGenerateNote = Boolean(
  transcriptId && hasLlmSelection && selectedTemplateId && (hasDraft || hasWorkingNote || hasDictation)
);
```

2. Remove stale hidden `context_*` form fields and sync plumbing.

Reason: web/API generation accepts `template_id` only. Hidden EMIS fields can mislead future work and add dead DOM churn.

Removed:

```html
<input type="hidden" name="context_..." data-structured-context-hidden>
```

Removed JS surface:

```js
structuredContextHiddenInputs
syncStructuredContextHiddenInputs()
```

3. Add 3 second Generate click guard.

Reason: frontend should block accidental double submission before queued document state returns.

### Modified

1. Keep `collectStructuredContext()` for now.

Reason: despite bad name, editor code still uses it to seed visible structured drafts from current rows when no generated note is active. Removing now would be broader refactor.

2. Keep legacy generated-document structured context reader for now.

Reason: old queued/failed/generated documents may still carry `generated_documents.structured_context_json`. Removing reader needs deliberate migration/compatibility decision.

### Deferred

1. Replace static JS substring assertions with behavioral JS runner tests.

Reason: worthwhile, but this slice keeps current repo test style and adds only focused regression checks. Future behavioral tests should cover:

- Generate button ignores generated-note-only content.
- Working-note dirty baseline does not advance on workspace refresh.
- Generate request body contains only `template_id`.
- Duplicate Generate submits within 3 seconds are ignored.
