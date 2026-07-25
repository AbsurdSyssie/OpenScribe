# Library Import and Export Plan

Status: implemented on `feature/template-import-export`; focused verification is tracked in `tests/test_template_io.py` and `tests/test_template_io_workspace_ui.py`.

## Purpose

Add portable Template, Quick Action, and Smart Phrase import and export to the unified workspace. Each asset type has a separate versioned interchange contract; none is a database backup or a way to transfer authority.

## Additional portable asset contracts

- `openscribe-quick-action-bundle` version 1 carries names, descriptions, and
  latest freeform prompts. It mirrors Template Personal/Team visibility,
  destination authority, version-1 independent-copy creation, name collision,
  review, and atomicity rules.
- `openscribe-smart-phrase-bundle` version 1 carries Personal triggers,
  expansions, and descriptions. Usage metadata is deliberately excluded and
  resets on import. Trigger conflicts use deterministic length-safe `_COPY_N`
  suffixes.
- The three formats remain separate so a Smart Phrase bundle cannot imply Team
  scope and an older Template bundle retains its existing meaning.

## Current model

- A personal template is owned by one normal user.
- A team template belongs to one team and can be managed only by that team's leader.
- Any normal user may view their team's templates and copy one to Personal.
- Template roots hold mutable metadata; template versions are immutable.
- Existing duplicate and Team-to-Personal fork operations copy only the latest version.
- The implemented schema has no personal-template sharing or watcher layer, despite older planned material in `docs/DatabasePlan.md`.

## Public bundle contract

- UTF-8 nested JSON with `format: "openscribe-template-bundle"` and integer `format_version: 1`.
- A bundle contains one or more templates and may mix Personal and Team templates visible to the exporter.
- Maximum encoded size: 1 MiB. Maximum entries: 100.
- Each entry contains portable content only: name, description, and the latest version's mode, prompt text, and configuration.
- Scope, active state, UUIDs, owner/team/creator identity, version number, and timestamps are omitted.
- Export includes only the latest version. Import creates a new independent template root at version 1.
- A checked-in public JSON Schema documents version 1 and is linked from `docs/api.md` and the import UI.
- Conforming hand-authored and externally generated bundles are supported.

Unknown additive fields outside structured configuration are discarded with preview warnings that identify their paths. Unsupported format versions, missing or malformed required fields, unsupported modes, and malformed known fields are errors. Structured `config_json` remains strict: unknown profiles or section keys and malformed sections are errors.

## Ownership and authorisation

- Export is permitted for any template the authenticated user can legitimately view.
- Export visibility does not transfer source scope or grant import authority.
- One caller-selected destination applies to the whole import selection.
- Personal import creates templates owned by the importer.
- Team import creates templates in the importer's current team and requires existing team-template management authority.
- File contents never select a destination, grant authority, identify an overwrite target, or preserve source ownership.

## Import behaviour

- Import is preflighted without database writes.
- Uploaded files and pasted JSON use the same preflight and commit endpoints. The browser converts pasted text into the same file payload; it does not introduce an alternative server parser.
- For pasted input only, one obvious surrounding Markdown code fence may be removed before submission. The client does not repair malformed JSON or alter template content.
- A bundle whose preflight response contains exactly one `ready`, selectable, default-selected entry with no bundle-level or entry-level warning is committed immediately after successful preflight.
- Every multi-template bundle, and any single-template bundle with a warning, conflict, rename, exact copy, or validation error, is shown for review. The user may deselect selectable entries before explicitly confirming.
- Atomic validation and creation apply to the final selected subset.
- Commit, whether automatic or explicitly confirmed, resubmits the original browser-held payload and selected indexes; the server reparses and revalidates the bundle, authorization, selection, duplicates, and conflicts.
- Import payloads are not retained server-side between preflight and commit. Pasted text is cleared from browser memory when the dialog closes or import succeeds.
- All imported templates are active.
- A validation failure in the selected subset creates nothing.

### Names and duplicates

- Name comparison uses the existing trimmed, case-insensitive destination uniqueness rule.
- An exact destination copy has the same normalized name, description, mode, prompt text, and canonical structured configuration as the active template import would create.
- Exact copies are shown as already present and deselected by default.
- Users may force-import an exact copy; it then receives the next deterministic `copy N` suffix.
- A same-name entry with different meaningful content is selected by default and receives the next deterministic `copy N` suffix.
- Different names remain distinct even if their instructions match.
- Duplicate normalized names within the bundle are resolved deterministically without violating destination uniqueness.
- Invalid entries are visible with field-specific errors but cannot be selected.

## Workspace interaction

- The template rail has a bottom-anchored utility area, visually separate from the scrolling Personal and Team groups.
- Accessible Import, Export, and import/export Help controls appear there. Import and Export have short explanatory tooltips.
- Import opens a modal shared by file and pasted-JSON input.
- The import modal provides one destination selector, defaulting to Personal; Team is offered only to callers with existing Team-template management authority.
- Users may upload a JSON file or paste JSON and press Submit. Either path performs destination-specific preflight through the same endpoint.
- Preflight lists unchanged imports, proposed suffixed names, exact copies, ignored-field warnings, and field-specific validation errors.
- New and automatically renamed entries are selected by default. Exact copies are deselected but selectable. Invalid entries are disabled.
- One clean unchanged template follows the automatic commit rule above. All other importable results require explicit confirmation, which is disabled when nothing importable is selected.
- Export temporarily changes both visible template groups into checkbox selection mode, with export and cancel actions. Permanent checkboxes and per-row export actions are omitted.
- Help opens a tutorial headed "Create a template with AI". It first explains in plain language what templates, Import, and Export do and why someone might create a template. A five-step walkthrough takes the user from copying the hidden technical instructions through describing the note they want to pasting the finished JSON back into Import. It includes the confidential-data warning and a manual-copy fallback.

### AI template tutorial

- The copied text is a vendor-neutral, self-contained prompt containing the public bundle schema constraints and instructions needed to produce importable JSON.
- A user-provided description is the brief. The AI is told to ask only unresolved questions about purpose, output, headings or supported EMIS sections, detail, formatting, tone, audience, inclusions, omissions, and missing-information handling.
- The AI infers freeform versus structured output when clear and must ask when it is not clear. It may use only the supported structured profile and section keys and must not weaken or extend the structured-output contract.
- The public schema and copied prompt omit redundant section labels. OpenScribe derives each canonical label from `section_key`; custom wording belongs in the section instruction.
- One template is generated by default; multiple templates require an explicit request or an unmistakable request for a set.
- Final output is the complete version 1 bundle as JSON without commentary or Markdown fences. The paste UI nevertheless tolerates one outer fence because AI tools may add one.
- The copied prompt requires a strict JSON parse check and explains escaping for quotation marks and line breaks inside text fields. Pasted text is parsed locally before upload so malformed JSON receives immediate, actionable guidance; OpenScribe does not guess-repair it.
- The tutorial and copied prompt say: "Do not include patient information, transcripts, clinical notes, credentials, or other confidential data."
- The prompt does not impose clinical-document content rules that the user did not request.

## Security and audit

- Reuse existing template authorization, normalization, structured-output validation, and creation services.
- Do not log bundle JSON, prompt text, or descriptions.
- Audit only metadata such as destination, counts, warning count, outcome, and appropriate created object identifiers.
- Do not weaken structured-output validation to accommodate imported data.
- Escape imported names and descriptions through the existing rendering boundary.

## Implemented areas

- Separate Template, Quick Action, and Smart Phrase bundle schemas and public JSON Schemas.
- Asset-specific service export, preflight, duplicate planning, and atomic import operations.
- Authenticated API routes used by the workspace for export, preflight, and commit.
- Library rails, file/paste import modals, conditional clean-single-entry commit, export selection behavior, plain-language help, and focused styling/scripts.
- Focused service, route, authorization, schema, atomicity, duplicate, warning, audit-safety, search-restoration, and workspace UI tests.
- Tracked contract, security, and testing documentation in `docs/api.md`, `docs/security.md`, and `docs/testing.md`.

No database migration or provider/configuration change is required.

## Implementation notes

- The public schema is checked in at [`app/static/schemas/openscribe-template-bundle-v1.schema.json`](../app/static/schemas/openscribe-template-bundle-v1.schema.json) and served from `/static/schemas/openscribe-template-bundle-v1.schema.json`.
- The API exposes `POST /api/v1/templates/export`, `POST /api/v1/templates/import/preflight`, and `POST /api/v1/templates/import`.
- Commit resubmits the browser-held original file or pasted payload and selected source indexes; the server reparses and replans before one transaction creates the selected roots and version-1 rows.
- Successful import/export audits contain metadata only. Bundle content, names, descriptions, prompts, and structured instructions are excluded.
- The implementation requires no database migration, runtime configuration, provider, encryption, retention, deletion, or structured-output contract change.
- Historical watcher, visibility, system-scope, derived-template, content-hash, and canvas proposals in `DatabasePlan.md` remain unimplemented and are not interchange fields.
