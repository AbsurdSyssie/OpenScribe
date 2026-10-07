# Multiple problems toolbar toggle

Status: implementation plan approved by the user's agreement to the proposed toggle flow, 5 October 2026.

## Intended behavior

- Add an accessible **Multiple problems** toggle next to Create/Regenerate.
- Store the mark on its owning consultation; restore it across refreshes and consultation switches.
- Marking requests split review on Create even when automatic detection is off. Deployment availability, eligible owner, retention, and all existing content/security boundaries remain mandatory.
- Unmarked consultations retain the existing automatic-detection preference and behavior.
- Snapshot the explicit-review decision on the durable Create intent, derived on the server from the locked consultation. Replay preserves it; changing the mark affects later requests, not an accepted request.
- Explicitly requested review opens an editable draft even when validated analysis finds zero or one problem. Explain the result and allow manual additions or continuation as one note. Never fabricate a problem or change provider output validation.
- Preserve the previous all-merged Continue as one note action, frozen template semantics, durable dispatch/quota/idempotency, source freshness and redaction, and clinician-review draft outputs.

## Current behavior and reconciliation

Transcript has no split marker. Owner PATCH and owner workspace projections are existing persistence/UI paths. The effective split gate currently combines deployment and personal automatic-detection preference; UI-only forcing would fail server/worker checks. Durable split intents already preserve replay across preference changes. Analysis with fewer than two separate notes is `not_required` and browser auto-continues as one note. Review draft initialization currently requires at least two detected topics, although replacements already allow zero to six. Preserve analysis statuses and provider schemas; extend only explicitly requested review-draft initialization.

## Implementation boundaries

Backend owns schema/migration, owner PATCH/projections, explicit-mode snapshots, narrow transcript/accepted-intent authorization, draft initialization and focused API/security/runtime tests. Frontend owns toolbar markup/styling/controller wiring, manual-review handling, cache versions, JS/browser tests and operational prose. No overlapping writes; root coordinates contracts and reviews integration. Existing uncommitted work must be preserved.

## Acceptance and verification

- Owner-only mark writes and projections; other owners, expired roots and system administrators are denied without disclosing content. Feature disabled never enables splitting.
- Persist and restore marks; prevent generation racing an unfinished toggle save and restore the prior state on save failure.
- Automatic off + marked consult can complete analysis/review/confirmation; automatic off + unmarked consult cannot create fresh split work.
- Same-key replay retains accepted explicit mode; queued manual work and review honor it after later unmarking without enabling new requests.
- Explicit `not_required` opens review without generation; ordinary `not_required` retains automatic continuation. Failed/stale/incomplete analysis never silently generates.
- Normal multi-note and all-merged flows remain functional; use synthetic data only.
- Run focused project-venv tests, migration checks, API authorization manifest/audit, optional browser verification where available, and maintained-document validation. Respect shared test lock; report unavailable verification honestly.
