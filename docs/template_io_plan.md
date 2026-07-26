# Library Import and Export

## Status

Implemented for Templates, Quick Actions, and Smart Phrases. The former feature-branch status line is obsolete; this document now records the current interchange/security contract. API route details are in [api.md](api.md).

## Separate bundle formats

OpenScribe uses three independent version-1 formats:

- `openscribe-template-bundle`;
- `openscribe-quick-action-bundle`;
- `openscribe-smart-phrase-bundle`.

They remain separate so a Smart Phrase bundle cannot imply Team scope and each asset type can retain its own validation/portable fields.

Checked-in JSON Schemas under `app/static/schemas/` document the public contracts.

## Portable data only

Bundles contain only the latest portable content for each selected asset, such as:

- name;
- description;
- Template mode/prompt/structured configuration;
- Quick Action prompt;
- Smart Phrase trigger/expansion.

Bundles do not transfer or preserve:

- UUIDs;
- owner/team/creator identity;
- scope authority;
- active state;
- version numbers/history;
- timestamps;
- watcher/fork/usage metadata;
- credentials or content-derived data.

Export is not a database backup and import is not an authority-transfer mechanism.

Limits:

- UTF-8 JSON;
- maximum encoded size: 1 MiB;
- maximum entries: 100.

## Scope and authorization

- Any authenticated normal owner can export assets they are authorized to view.
- Export visibility does not grant permission to recreate the source scope.
- Personal import creates caller-owned assets.
- Team Template/Quick Action import requires leader authority and creates assets in the caller's current team.
- Smart Phrase import is always Personal.
- File-supplied owner/team/scope/creator/version/active/usage fields are ignored or rejected as authority.
- System administrators do not become owners of normal personal/team generation assets.

## Preflight and commit

Preflight is read-only and parses/validates the original bundle for a caller-selected destination.

It reports:

- ready entries;
- proposed deterministic rename/copy suffixes;
- exact existing copies;
- ignored additive-field warnings;
- field-specific validation errors;
- which entries are selectable/default-selected.

Commit:

- resubmits the original browser-held file/pasted payload plus selected indexes;
- reparses, reauthorizes, and replans against current database state;
- creates the selected subset in one transaction;
- creates independent roots at version 1;
- creates nothing when any selected entry fails final validation.

OpenScribe does not retain uploaded bundle content between preflight and commit.

A single clean unchanged ready entry can commit immediately after preflight. Multi-entry, warning, conflict, rename, exact-copy, or error cases require review/confirmation.

## Validation and duplicates

- Destination uniqueness uses the existing trimmed, case-insensitive normalization rule.
- Exact copies are deselected by default but can be force-imported under the next deterministic copy suffix.
- Same-name/different-content entries receive deterministic copy suffixes.
- Different names remain distinct even if content matches.
- Unsupported format versions and malformed known fields are errors.
- Unknown additive fields outside strict structured configuration are discarded with warnings.
- Structured Template configuration remains strict: only the supported profile/section keys are accepted.
- Smart Phrase trigger conflicts use deterministic length-safe suffixes and usage counts reset.

## Workspace behavior

Library rails expose Import, Export, and Help controls.

- Import supports file upload or pasted JSON through the same server parser.
- The client may strip one obvious outer Markdown fence but does not repair malformed JSON/content.
- Export temporarily enters checkbox selection mode rather than adding permanent per-row export controls.
- Team destination is offered only to callers with existing team-management authority.
- Import/export Help explains safe AI-assisted bundle creation using the public schema.

The copied AI prompt is vendor-neutral and explicitly forbids patient information, transcripts, clinical notes, credentials, and other confidential data. Final expected output is only the complete JSON bundle.

## Security and audit

- Reuse existing owner/team authorization, normalization, and structured-output validation.
- Do not log or audit bundle JSON, names, descriptions, prompts, instructions, triggers, or expansions.
- Audit metadata can include asset type, destination, selected/created/warning/error counts, outcome, and safe object identifiers.
- Imported values render through normal escaping/XSS boundaries.
- Preflight must remain free of database/Vault side effects.

## Current API groups

Each asset family has:

- export;
- import preflight;
- import commit.

Exact routes and response schemas are listed in [api.md](api.md). Focused verification lives in the current test suite; use `pytest --collect-only -q` rather than relying on an old feature-branch file list.

Historical watcher, visibility, system-scope, derived-template, content-hash, and canvas proposals in [DatabasePlan.md](DatabasePlan.md) are not part of the version-1 bundle formats.
