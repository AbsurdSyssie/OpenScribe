# Consultation-splitting Phase 10 release evidence — 10 September 2026

## Scope and status

This is a dated evidence record for the uncommitted consultation-splitting working tree based on `HEAD` `41b03055303dd0923592dd937db3e8ec612e8b89`, updated after the source-only materialization fix was verified on 11 September 2026. It does not replace the maintained rollout contract in [consultation-splitting-preference.md](consultation-splitting-preference.md).

**Gate status:** `CONSULTATION_SPLITTING_ENABLED=false` remains the default in [`.env.example`](../.env.example) and the Compose defaults. The feature is code-ready behind that disabled gate. This evidence does **not** authorize enabling it.

All automated scenarios used synthetic text, synthetic identifiers, fake provider transports, and local test infrastructure. No live provider, patient data, credential, or production deployment was used.

The final source-only defect is fixed. Confirmation now binds an encrypted empty `TranscriptVersion` to Working-note-only and dictation-only batches through `materialization_transcript_version_id`; analysis keeps its truthful null transcript-version and redaction-run links. Generation validates that batch-bound version and uses it for every child document. The migration uses the PostgreSQL-safe FK name `fk_split_batches_materialization_version`.

## Commands and results

| Command | Result |
| --- | --- |
| `Focused source-only service/browser/migration checks` | **6 passed** |
| `Full consultation-splitting suite` | **415 passed**, 7 warnings |
| `.venv/bin/pytest -q` | **2104 passed**, 25 warnings |
| `python .github/scripts/check-operational-docs.py` | **45 checked; passed** |
| `git diff --check` | **Passed; clean** |
| `APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app ./.venv/bin/python scripts/audit_api_auth.py` | Passed; every `/api/v1` route matched its manifest and negative-access expectations |
| `./.venv/bin/python -m compileall -q app` | Passed |

The source-only checks cover both Working-note-only and dictation-only service and Playwright paths, null analysis lineage, empty encrypted batch versions, no-retarget behavior after confirmation, invalid missing/wrong-root bindings, no-document failure, deletion cascade, and the isolated migration roundtrip. The migration test originally exposed an overlong PostgreSQL FK identifier; the migration and test now use `fk_split_batches_materialization_version`.

## Focused synthetic coverage

The 412-test focused command covers the required split evaluation through the named split suites:

- no-topic/single-topic `not_required`, multiple topics, primary/shared-fact boundaries, and `include_in_primary`/`exclude_from_notes` dispositions;
- Working-note-only and dictation-only confirmation and materialization, including the batch-bound empty encrypted version and truthful null analysis lineage;
- mixed freeform and structured templates, EMIS section validation, malformed/ambiguous provider envelopes, and exact server-issued UUID mapping;
- automatic recovery limited to failed topics, manual retry with the current eligible provider, primary-failure partial retention, and idempotent **Keep available notes**;
- optional verification: unchanged, corrected, invalid-patch rejection, mixed/freeform unchecked behavior, and fail-open configuration, credential, quota, expiry, provider, and patch outcomes;
- quota reservation/exhaustion and settlement, duplicate delivery, submitted-before-call recovery, persistence-crash handoff, durable outbox behavior, and writer locks;
- retention expiry, transcript/user/team deletion cascades, owner/team/admin denial, encrypted scope failures, and safe workspace/API projections;
- browser-controller restoration/backoff, SSE fallback, transcript switching, stale-response races, draft concurrency, and partial-action single-flight guards.

The focused review found one missing case: a valid single-topic analysis response must be `not_required`, not open split review. A focused runtime test was added and the runtime now applies the ordinary one-note outcome to fewer than two topics. The focused and full suites above include that regression.

## Browser harness

The repository has executable local Playwright harnesses. `tests/test_csrf_browser.py` passed with a local Uvicorn server and Chromium; it requires no external provider. `tests/test_consultation_split_browser.py` covers rendered Create/replay, draft save/confirm, generated drafts, partial Keep, stale transcript switching, and both Working-note-only and dictation-only consultations. The source-only browser paths assert that the analysis retains null transcript/redaction lineage while generated children use the confirmed batch-bound empty version. `tests/test_split_review_js.py` also passed its Node-based restoration and race harnesses.

Manual browser verification remains pending for the gated end-to-end split workflow, real worker/SSE timing, responsive behavior, and the broader checklist in [transcribe-playwright-checklist.md](transcribe-playwright-checklist.md). Those checks require a deployment-like configured provider, worker/Beat, Vault, and an enabled gate; they were deliberately not run here.

## Known warnings and unverified areas

- The full suite reports 25 third-party deprecation warnings: FastAPI/Starlette `TestClient`/HTTP 422 constants and legacy `websockets` server imports. The full split suite reports 7 warnings. No test failed.
- Live provider behavior, real credential/Vault integration, deployment migration backup/rollback, and operational worker/Beat delivery were not exercised.
- This is a dirty working-tree evidence record, not a committed release artifact. A release reviewer must review the complete diff and rerun the gates from the release candidate.

## Reviewer status

Automated checks: complete and passing. Independent release review: **pending**. Enablement decision: **pending; gate remains false**.
