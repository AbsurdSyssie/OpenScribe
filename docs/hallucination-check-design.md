# Hallucination Check Design

## Goal

Add a post-generation hallucination check for structured clinical notes. The checker reviews an AI-generated note against redacted source material and removes or softens unsupported claims before the final note is stored.

Primary risk: note-generation LLMs can add diagnoses, treatments, investigations, follow-up plans, safety-netting, symptoms, durations, negatives, or exam findings not present in the consultation source.

Target behavior: structured note generation may produce a first-pass note, then a separately configured checker LLM receives the redacted sources and first-pass note, returns exact string edits, and the system persists only the final checked or unchecked note.

## Scope

In scope for MVP:

- `generator_type=template` structured note generation only.
- EMIS-style structured notes and allowed section keys already supported by the template system.
- Redacted transcript, redacted Working note, and redacted dictation content as evidence, when those sources were available to the first-pass generation.
- Admin-only checker provider selection per team, using existing team LLM provider configs.
- Exact-substring patch response from checker LLM.
- One retry on invalid checker JSON, schema, or patch application.
- User-visible checked/unchecked status bucket.
- Admin-visible non-content reason metadata.
- Separate provider usage event(s) for checker calls.
- Development-only debug visibility for first-pass note and checker edits, behind explicit env flag and owner-only generated-document access.

Out of scope for MVP:

- Follow-up generated documents.
- Quick actions.
- Freeform template outputs.
- Checker access to plaintext transcript, note, Working note, or dictation content.
- Storing raw checker responses.
- Storing first-pass hallucinated note after successful correction.
- Sending template instructions to checker.
- Creating new note sections during checking.
- Clinical inference beyond strict source evidence.
- Production visibility of first-pass hallucinated note or raw checker edits.

## Design Principles

- Reuse existing redaction, LLM config, provider runtime, encrypted generated-document fields, and usage-event plumbing.
- Reduce churn: add small service helpers around existing generation flow instead of broad refactors.
- Preserve current structured note validation and section rendering where possible.
- Prefer explicit validation over fuzzy patching.
- Fail open to an unchecked note rather than blocking note creation.
- Never log or store transcript/note/prompt/checker response content outside existing encrypted final document storage.

## Evidence Rules

Checker prompt must explicitly say:

> You are checking an AI-generated clinical note. The note may or may not contain hallucinated text. The transcript and other provided redacted source material are messy and may contain transcription errors, but they are your only source of truth. Do not use clinical knowledge, likely intent, or normal practice to fill gaps.

Evidence standard:

- Strict source evidence only.
- Source wording can be messy; cleaned note wording may stay only when it preserves the same explicit fact and certainty.
- No added specificity.
- If source says `maybe asthma`, note may say `possible asthma`, not `asthma diagnosis`.
- If source says `try inhaler?`, note may say `inhaler discussed`, not `started salbutamol`.
- If source says `bloods maybe`, note may say `blood tests discussed`, not `FBC/U&E ordered`.
- If the source does not support a diagnosis, treatment, test, advice, safety-netting, follow-up plan, medication, symptom, duration, negative, or exam finding, the checker must remove it or soften it only where softer wording is itself supported.

## Privacy Boundary

Checker input:

- Redacted transcript text.
- Redacted Working note content, if included in first-pass generation.
- Redacted dictation content, if included in first-pass generation.
- First-pass generated structured note in redacted form.

Checker must never receive plaintext content.

Reidentification, where applicable, remains after the checker stage. The checker output is still redacted and is the only candidate passed onward for final storage/reidentification.

Logs and stored metadata may include:

- event type
- team/config IDs
- provider/model names
- statuses/reason codes
- durations
- token counts
- applied edit count

Logs and stored metadata must not include:

- transcript text
- note text
- prompt text
- checker response body
- redaction original values
- provider secrets

## Checker Configuration

Add admin-only team checker selection using existing `team_llm_configs` rows:

- selection exists means checker enabled
- clearing selection disables checker
- selected config must belong to team
- optional model override supported
- no fallback to active generation LLM
- invalid/missing config skips checker and saves unchecked note
- team leaders and normal users cannot read or modify checker config

Candidate table:

`team_hallucination_check_selections`

- `team_id` primary key, FK teams cascade
- `llm_config_id` FK `team_llm_configs`
- `model_name_override` nullable string
- timestamps

Keep provider credentials in existing Vault-backed LLM config storage. Do not add new secret storage.

## Generated Document Metadata

Add non-content checker metadata to `generated_documents`:

- `hallucination_check_status` internal enum/reason
- `hallucination_check_llm_config_id` nullable UUID
- `hallucination_check_model_name` nullable string
- `hallucination_check_provider_snapshot_json` nullable JSON
- `hallucination_check_completed_at` nullable timestamp
- `hallucination_check_applied_edit_count` nullable integer

Internal statuses:

- `not_applicable`
- `skipped_not_configured`
- `skipped_config_invalid`
- `failed_provider`
- `failed_invalid_response`
- `checked_unchanged`
- `checked_corrected`

User-facing bucket:

- `checked` for `checked_unchanged` and `checked_corrected`
- `unchecked` for skipped/failed statuses
- `not_applicable` outside structured template generation

Admins may see internal reason metadata. Normal users see bucket only.

Do not store raw checker response content. Do not store first-pass note if checker successfully corrects it.

## Development Debug UI

Development sometimes needs side-by-side visibility of:

- first-pass note before hallucination check
- checker returned edits
- retry count
- patch/checker failure code

This is a development-only exception to the normal no-first-pass-storage rule.

Guardrails:

- disabled by default
- enabled only by explicit env flag, for example `HALLUCINATION_CHECK_DEBUG_UI=1`
- visible only to the owning user through existing owner-only generated-document responses
- never visible cross-owner, including to team leaders or system admins who do not own the generated document
- never enabled in production deployments
- debug payload encrypted at rest using existing owner-scoped generated-document encryption helpers
- debug payload deleted with the generated document/transcript root
- no debug payload in logs, usage events, provider metadata, or admin list summaries
- no raw provider secrets or redaction original values in debug payload

Candidate debug payload shape:

```json
{
  "initial_note": {
    "title": "string",
    "sections": [
      {"key": "problem", "content": "string"}
    ]
  },
  "checker_edits": [
    {
      "section_key": "problem",
      "original": "exact substring from existing note",
      "replacement": "replacement text, or empty string"
    }
  ],
  "retry_count": 0,
  "failure_code": null
}
```

Recommended quickest implementation:

- Add nullable encrypted debug JSON field on `generated_documents`, populated only when env flag is enabled.
- Reuse existing generated-document encryption/decryption helpers rather than adding new storage mechanics.
- Add a collapsible owner-only UI panel on generated document detail/workspace response when env flag is enabled.
- Keep normal generated-document API response unchanged unless both env flag and owner context are true.

If implementation wants zero schema churn, an alternative is temporarily storing debug payload inside existing encrypted request payload while env flag is enabled. This is faster but less clean because request payload becomes mixed with runtime debug output. Prefer a dedicated nullable encrypted field if migration churn is acceptable.

## Checker Response Contract

Checker returns JSON object only. No markdown fences. No extra top-level keys.

Unchanged response:

```json
{"status":"unchanged"}
```

Corrected response:

```json
{
  "status": "corrected",
  "edits": [
    {
      "section_key": "problem",
      "original": "exact substring from existing note",
      "replacement": "replacement text, or empty string"
    }
  ]
}
```

Rules:

- `status` must be exactly `unchanged` or `corrected`.
- `corrected` requires non-empty `edits` array.
- No `reason` field.
- No `removed_claim_count` field; system stores applied edit count.
- `section_key` must be an existing section key or `__title__`.
- Checker cannot create new sections.
- `original` must be non-empty.
- `replacement` may be empty for section content.
- `replacement` for title must not become empty after trim.
- `original` must occur exactly once in the target field.
- Matching is exact and includes spaces/newlines.
- If repeated, checker must choose a longer unique substring.
- No regex, fuzzy matching, or global replace.
- Edits should be sorted by note order and then appearance order.
- Empty sections after edits are omitted from final note.
- Section order remains unchanged.
- Internal newlines are preserved. Only leading/trailing whitespace is trimmed after all edits apply.

Edit cap uses the user note-length snapshot already stored for generation:

- `short`: 20 edits
- `normal`: 50 edits
- `long`: 100 edits

Checker output token cap reuses the existing note-length token cap:

- `short`: 800
- `normal`: 1600
- `long`: 3200

## Runtime Flow

Structured note generation pipeline:

1. Build first-pass prompt using existing generation flow and redacted sources.
2. Call generation LLM using existing provider runtime.
3. Validate first-pass structured note using existing structured-note rules.
4. Build in-memory note object from validated output.
5. Resolve team hallucination checker selection.
6. If no valid checker, mark unchecked reason and persist first-pass note.
7. If checker valid, call checker LLM with redacted sources and first-pass note object.
8. Parse and validate checker JSON.
9. Apply exact-string patches.
10. If invalid, retry once with stricter repair prompt and same redacted sources/note.
11. If retry fails, mark unchecked reason and persist first-pass note.
12. If unchanged, mark checked and persist first-pass note.
13. If corrected, mark checked and persist corrected note only.
14. Persist checker metadata and usage event(s).

Checker failure must not make the generated document fail. Primary generation failure still fails the document as today.

## Prompt Shape

Do not include template instructions.

Include only:

- strict checker instruction
- redacted source bundle, with absent sources omitted
- current note JSON
- response schema and patch rules

Suggested source labels:

- `TRANSCRIPT`
- `WORKING_NOTE`
- `DICTATION`
- `NOTE`

Provider settings:

- temperature `0` where adapter supports it
- no streaming
- JSON response format where adapter supports it
- prompt-only JSON enforcement where adapter does not support response format
- no fallback model/provider

Retry prompt should not include invalid checker response body. It may include only failure category, for example `patch_not_unique`, `invalid_json`, or `schema_invalid`.

## Affected Modules

Likely implementation touch points:

- `app/models.py`: checker selection model, generated-document metadata enum/fields.
- `alembic/versions/*`: migration for selection table and metadata columns.
- `app/services/llm.py`: admin-only CRUD helpers for checker selection, reusing existing LLM config validation.
- `app/services/templates.py`: checker runtime integration after structured output validation and before final persistence.
- `app/web/presentation.py`: admin-only presentation helpers and generated-document user bucket.
- `app/routes/api_routes.py`: admin-only checker selection endpoints if API parity is needed.
- `app/routes/web_admin.py` and admin templates: admin controls for selecting/clearing checker config.
- `app/schemas/*`: request/response schema for checker selection and generated-document check bucket.
- `tests/test_api.py`: provider selection, generation flow, patch validation, fallback/skip behavior.
- `tests/test_migrations.py`: schema assertions.
- `tests/test_admin_ui.py`: admin-only controls and no team-leader visibility.
- Docs: API, LLM provider docs, testing docs, transcript/generation docs, progress.

Implementation should reuse existing provider call helpers and encrypted generated-document storage rather than introducing a new LLM client path.

## Test Plan

Focused tests should cover:

- system admin can set and clear checker selection for a team
- non-system-admin cannot read or mutate checker selection
- checker selection must use team-owned ready LLM config
- structured note generation runs checker when configured
- unchanged response persists first-pass note and `checked` bucket
- corrected patch edits exact unique section text and stores applied edit count
- replacement preserves internal newlines
- empty section after replacement is omitted
- title cannot become empty
- patch with zero/repeated match retries once, then saves unchecked if still invalid
- invalid JSON/schema retries once, then saves unchecked if still invalid
- missing/invalid checker config saves unchecked note without failing document
- checker provider failure saves unchecked note without failing document
- checker is not run for follow-up, quick action, or freeform output
- checker receives redacted transcript/Working note/dictation only
- raw checker response is not persisted
- provider usage event is recorded for checker call(s)
- generated-document response exposes only user-facing bucket to normal users
- admin view can expose internal reason metadata without content
- migration creates selection table and metadata columns with expected constraints

Run focused pytest through project venv, for example:

```bash
.venv/bin/pytest -q tests/test_api.py -k "hallucination_check"
.venv/bin/pytest -q tests/test_migrations.py -k "hallucination_check"
.venv/bin/pytest -q tests/test_admin_ui.py -k "hallucination_check"
```

## Architecture Checkpoints

Schema checkpoint:

- Add a team-scoped checker selection table instead of overloading active generation LLM selection.
- Add non-content generated-document metadata only.
- Keep generated-document ownership and transcript-root cascade unchanged.

Auth/ownership checkpoint:

- Checker selection is system-admin only.
- Normal users and team leaders do not receive checker config/provider metadata.
- Generated document reads remain owner-only.

Lifecycle/deletion checkpoint:

- Checker metadata remains child metadata on generated documents and cascades with transcript-root deletion.
- Team deletion must remove checker selection rows with team-owned provider config cleanup.
- No separate content-bearing checker artifacts are created.

Provider checkpoint:

- Use existing team LLM configs and Vault-backed credentials.
- No fallback to active generation LLM.
- Invalid checker config means unchecked note, not silent substitution.
- Record checker usage as separate non-content usage event.

Structured-note checkpoint:

- Checker only mutates existing title/sections by exact substring replacement.
- No new sections.
- Allowed section keys and ordering remain governed by existing structured template snapshot.
- Empty sections are omitted.

Privacy checkpoint:

- Checker uses redacted sources only.
- No plaintext checker path.
- No raw checker response storage.
- No content-bearing logs.

## Open Risks

- Exact-substring patches may fail often if checker quotes near-matches. One retry should quantify this before considering full-note JSON fallback.
- Some provider adapters may not support JSON response format or temperature control. Hard validation remains required.
- Existing generation flow may currently reidentify before a convenient checker insertion point; implementation must verify and preserve redacted-only boundary.
- Admin UI should avoid exposing checker model details outside system-admin surfaces.
