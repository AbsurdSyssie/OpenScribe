# LLM Generation Options Plan

## Goal

Add user-facing note generation options for model, output length, and detail style. Options must be editable from the transcription workspace and from the user's home/settings surface, persist for later use, and be read server-side during note generation.

## Product Decisions

- The workspace control is named `Note options` and sits near the `Create` note-generation button, not near recording controls.
- The home overview `Writing assistant preference` card is also updated with the same options.
- Model selection remains available in the same user-facing menu/card, but keeps its existing persistence/API path.
- Output length and detail apply only to note generation from templates.
- Output length and detail do not apply to quick actions, follow-ups, dictation cleanup, redaction, clinical extraction, or STT.
- Options save on change in the workspace.
- Preference save failures must not block note generation.
- If save fails, show a clear warning such as `Options not saved; next note may use previous settings.`
- Generation reads saved DB preferences at queue time, not client-submitted option values.
- Changing options while a generation is queued/running affects only future generations.
- No custom user-authored detail descriptions in this slice.
- No `Restore defaults` button needed while presets are fixed.
- No admin/team-leader defaults or policy limits in this slice.

## Controls

### Model

- Source: active team LLM selection plus existing user model preference.
- Storage: existing `user_llm_preferences.preferred_model_name`.
- API: existing `/api/v1/llm-preference` routes.
- UI behavior: show model row whenever an LLM provider is active.
- If multiple allowed models exist, show selector.
- If one allowed model exists, show read-only/disabled model value.
- If no provider is active, show `No LLM provider configured`.

### Length

- Source of truth: new `note_generation_length` value in `user_app_preferences.preferences_json`.
- Default when absent: `normal`.
- Values:
- `short`: max output tokens `800`; UI copy `Short (up to ~1 page)`.
- `normal`: max output tokens `1600`; UI copy `Normal (up to ~2 pages)`.
- `long`: max output tokens `3200`; UI copy `Long (up to ~4 pages)`.
- Length controls token cap only.
- Length must not add prompt instructions about bullets, prose, note sections, or clinical content categories.
- Page estimates are approximate maximums, not promises.

### Detail

- Source of truth: existing `llm_detail_level` value in `user_app_preferences.preferences_json`.
- Default when absent: `balanced`.
- Existing field is reused to avoid schema/API churn.
- Define `llm_detail_level` as note-generation output detail only.
- Values:
- `concise`: `Use compact wording. Avoid unnecessary phrasing.`
- `balanced`: `Use clear standard clinical wording.`
- `detailed`: `Use fuller wording where helpful. Include short direct quotations from the patient only when present in the source and clinically useful, for example: patient described headaches as "dreadful".`
- Detail guidance must be format-neutral.
- Detail guidance must not force bullets, prose, headings, statement style, or sentence style.
- Template instructions remain owner of output format and content categories.
- Structured EMIS contract remains higher priority: allowed section keys only, omit empty sections, valid JSON only.
- Detail must not add facts, invent quotes, add negatives, add reasoning, or add categories not present/requested.
- Redaction placeholders such as `[PHI-1]` must be preserved exactly.

## Persistence And Access

- `user_app_preferences` is already DB-backed with `user_id` unique FK and `preferences_json` JSON.
- Add `note_generation_length` to `UserAppPreferencesUpsert` and `UserAppPreferencesDetail`.
- Extend app-preference serialization/validation to accept only `short`, `normal`, or `long`.
- Keep existing owner/user scope rules for app preferences.
- System admins must not gain read access to user app preferences through this feature.
- Preferences are metadata, not transcript-derived content and not secrets; no encryption needed.
- Do not add new typed DB columns for this slice.

## Generation Behavior

- Note generation service loads current user's saved app preferences at queue time.
- Absent preferences resolve to `normal` length and `balanced` detail.
- The request builder maps length to output cap.
- OpenAI-compatible and Bedrock HTTP gateway request bodies use adapter-standard token cap field currently used by the code path.
- Ollama request body should include the equivalent cap, expected as `options.num_predict` for `/api/chat`.
- Verify provider request shape in code before implementation; do not assume all providers accept the same field.
- If provider rejects a token field, surface existing generation error rather than adding provider capability matrix in this slice.
- No special retry for too-short structured JSON output in this slice.
- If structured output is truncated/invalid, existing validation should fail; user can choose longer length and regenerate.
- Existing encrypted LLM request payload snapshot is enough provenance; no new generated-document metadata field required.

## UI Behavior

- Workspace `Note options` menu saves preferences immediately via existing JSON APIs.
- App preference save updates must preserve existing app preference fields:
- `favorite_quick_action_ids`
- `favorite_template_ids`
- `default_quick_action_id`
- `default_template_id`
- `llm_detail_level`
- `preferred_recording_mode`
- `preferred_transcribe_tab`
- new `note_generation_length`
- Model save uses `/api/v1/llm-preference`.
- Length/detail save uses `/api/v1/app-preferences`.
- Failed save shows non-blocking warning.
- Generate remains enabled based on existing generation eligibility, not preference-save state.
- Home overview form saves model, length, and detail. It may use existing HTML form flow plus any needed web route addition for app preferences.

## Architecture Checkpoints For Implementing Agent

### Checklist Before Coding

- Target behavior: user can set model, length, detail in transcribe UI and home settings; note generation uses saved prefs.
- Affected schema/modules/endpoints: `app/schemas/preferences.py`, `app/services/preferences.py`, generation request builder in `app/services/templates.py`, API app preferences route, home web route/template, transcribe template/JS bootstrap/app code.
- Affected tests: API preference validation, generation request snapshot/token caps, UI preference persistence if JS tests exist.
- Architecture risks: prompt injection from detail text, provider request field mismatch, accidental application to quick actions/follow-ups, preference save wiping unrelated JSON keys.
- Relevant docs: `docs/api.md`, `docs/llm-providers.md`, `docs/tutorials/user.md`, `docs/transcript-capture.md` or current transcribe docs, `docs/progress.md`.
- Reuse/refine existing code: reuse `user_app_preferences`, `llm_detail_level`, `/api/v1/app-preferences`, `/api/v1/llm-preference`, existing LLM request snapshot.
- Avoid extra code: no new table, no admin policy, no custom detail editor, no generated-document fields.

### Coding Checkpoints

- Schema checkpoint: no DB migration expected unless implementation discovers typed constraint need; JSON validation must reject invalid length/detail.
- Auth/ownership checkpoint: preference routes remain current-user only; no manager/admin content or preference visibility expansion.
- Lifecycle/deletion checkpoint: preferences follow user lifecycle as existing rows do; transcript deletion semantics unchanged.
- Docs/tests checkpoint: update API docs, user docs, LLM provider docs if request cap behavior changes, and progress note.

## Test Plan

- API test: `/api/v1/app-preferences` accepts `note_generation_length` values `short`, `normal`, `long`.
- API test: invalid `note_generation_length` rejects with validation/business-rule error.
- API test: omitted `note_generation_length` remains null/absent and generation resolves default `normal`.
- API test: app preference save preserves validation for favorites/default templates/default quick actions.
- Authorization test: system admin/non-team user cannot use app preferences, matching current behavior.
- Generation test: `short` maps to `800` output tokens.
- Generation test: `normal` maps to `1600` output tokens.
- Generation test: `long` maps to `3200` output tokens.
- Generation test: absent length uses `1600`.
- Generation test: `concise`, `balanced`, and `detailed` inject only format-neutral detail guidance.
- Generation test: detail guidance does not alter structured-note allowed-key contract.
- Generation test: generation reads DB preference, not client-provided payload.
- Adapter test: OpenAI-compatible request snapshot includes selected cap.
- Adapter test: Bedrock gateway request snapshot includes selected cap according to current adapter contract.
- Adapter test: Ollama request includes `options.num_predict`.
- UI/JS test if available: workspace option change persists app preferences and keeps existing preference fields.
- UI/JS test if available: failed preference save shows warning and does not block generation.
- Home route/template test: home writing assistant form can save model, length, and detail.

## Documentation Updates

- `docs/api.md`: document `note_generation_length` in app preferences and clarify `llm_detail_level` is note-generation detail.
- `docs/llm-providers.md`: document note generation request cap behavior and adapter-specific request fields if relevant.
- `docs/tutorials/user.md`: explain Note options in workspace and Home preference card.
- `docs/progress.md`: add daily note with scope, tests, docs, risks, and architecture checkpoint summary.

## Risks And Assumptions

- Assumes current `llm_detail_level` is not materially used elsewhere; verify before changing semantics.
- Assumes `user_app_preferences.preferences_json` is acceptable for product-grade UI preferences because these values are not queried cross-user and do not need DB constraints.
- Assumes home overview remains the user's settings surface for this slice.
- Assumes no team/admin cost cap is required.
- Assumes templates remain correct place for user/team custom writing rules.
- Provider-specific token parameter mismatches may appear in real deployments; defer capability matrix until needed.

## Out Of Scope

- Custom detail descriptions.
- Restore-defaults button.
- Team/admin default length/detail policy.
- Per-transcript/per-template stored overrides.
- Applying length/detail to quick actions, follow-ups, dictation, redaction, or clinical extraction.
- New generated-document metadata fields for selected length/detail.
- Automatic retry when structured output exceeds selected length.
