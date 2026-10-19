# Discovered issues remediation and regression review — 19 October 2026

This is a dated record of remediation prepared for commit, based on `97da191926db1da74fdf741e01f4f8490d60f6c8`. It is not a replacement for maintained runtime documentation. Final verification completed on 20 October 2026; the changes were not deployed.

Work began on 19 October; final verification completed on 20 October 2026 (Europe/London).

## Reconciliation

All 39 original entries in [DISCOVERED_ISSUES.md](../../DISCOVERED_ISSUES.md) have explicit dispositions. Original dated observations and resolution evidence are retained. Resolved and superseded items are separated from deployment follow-ups, upgrade watchpoints, and the Ollama output-ceiling policy decision.

Current source already includes deleted split-slot handling and source-readiness guards. The earlier branch-merge instruction and incomplete-source-snapshot observation no longer describe this checkout. Existing request deadlines, disabled SDK retries, and opt-in isolated prefork workers supersede the missing-timeout part of the recorded worker-stall incident; deployment capacity still needs deployment-specific verification.

## Changes and current contracts

| Issue | Change | Main source and regression coverage |
| --- | --- | --- |
| Test diagnostics | Database/Redis isolation refusals omit configured connection URLs and credentials; the refusal itself is unchanged. | `tests/db_utils.py`, `tests/test_db_utils.py` |
| Template-suggestion JSON | Shared duplicate-key rejection applies at every object depth before suggestion validation. | `app/services/template_suggestions.py`, `tests/test_template_suggestions.py` |
| Unsupported worker preflight | Main generation, ordinary checker, suggestions, split analysis, split generation, and split verification check capability before marking attempts submitted. Definite local failures cancel reserved quota. Optional checkers preserve validated drafts through their existing unchecked path. | `app/services/llm_execution.py`, workflow submission sites, `tests/test_llm_execution.py`, `tests/test_generation_unsafe_failures.py`, suggestion and split runtime tests |
| SDK response bounds | Synchronous OpenAI-compatible and Gemini HTTPX hooks enforce an 8 MiB response ceiling before SDK parsing, retain proxy/TLS behavior, and reject compressed responses before decompression. Submitted failures retain existing settlement and no-replay rules. | `app/services/llm_adapters/bounded_transport.py`, SDK adapters, `tests/test_provider_response_bounds.py`, `tests/test_gemini_enterprise_llm.py` |
| Gemini HTTP 400 classification | Structured model/location reasons retain the specific classification; other HTTP 400 failures use a fixed invalid-request classification without provider message text. | `app/services/llm_adapters/gemini_enterprise.py`, `tests/test_gemini_enterprise_llm.py` |
| Targeted recovery schema | New requests constrain provider output to failed topic UUIDs. Already-queued legacy requests are accepted only when their complete request equals the exact legacy reconstruction from immutable batch snapshots. Local failed-topic validation remains authoritative. | `app/services/consultation_split_generation.py`, generation runtime, pure and runtime recovery tests |
| Deletion confirmation | The additive `has_saved_content` response flag includes transcript text plus owner-scoped Working-note, dictation, and ready generated-note record presence. New derived-source queries are batched and do not decrypt source content. Existing `has_transcript_content` semantics remain unchanged. Initial rendering and polling both update confirmation state. | Transcript schema, workspace projection, session-panel markup, `actions.js`, `app.js`, API/history tests and Node delete harness |
| Scribe resizing | Exposed the shared narrower/wider buttons in Scribe and connected them to its existing bounds/persistence; fixed expanding from collapsed width with ArrowRight. Existing pointer and keyboard controls remain. | Shared workspace sidebar, Scribe shell, Node resize harness and layout/browser tests |
| Stale UI expectations | Reconciled guide and cache-key expectations; Quick Action attribute-order independence was already present in the baseline. | Settings, guide, layout, and browser asset tests |

No migrations, selected provider/model/region changes, or deployment configuration changes were required. Content ownership, retention/deletion roots, encryption, redaction, clinician review, structured-output shapes, transactional outbox, and duplicate-delivery protections are preserved.

## Regression review

Three Luna agents implemented bounded areas with separate write ownership. The parent coordinated and reviewed the combined changes. Separate agents reviewed provider transport/reconstruction, preflight/settlement, and workspace privacy/UI paths they did not implement. No actionable findings remain from those source reviews.

The review caught and corrected legacy recovery reconstruction compatibility, compressed-response allocation risk, proxy/TLS preservation, all six pre-submit boundaries, checker rollback metadata, the overloaded transcript-status flag, and cache propagation. Rendered browser verification also caught resize buttons added to an unused legacy sidebar; the final change exposes the existing controls in the shared sidebar actually rendered by Scribe. Unused markup/CSS additions were removed. New-test fixture/type/snapshot mistakes were corrected against executable worker contracts rather than weakening existing tests.

Knowledge-graph change and impact queries informed the review. The graph returned no indexed affected flows and did not index the new untracked response-hook module; this was supplemented with direct call-site inspection and focused lifecycle/privacy/real-SDK tests. Empty graph results are not evidence of no impact or absent test coverage.

## Verification

Tests used `APP_ENV=test`, `COOKIE_SECURE_MODE=auto`, and `HSTS_SOURCE=app` with the project virtual environment. PostgreSQL/Redis-backed commands and the API audit ran sequentially. The initial sandbox connection failure was distinguished from service unavailability by a successful elevated retry; no service was installed/restarted and no isolation guard was bypassed.

| Check | Result |
| --- | --- |
| Fresh pre-commit provider response-bound, Gemini, execution/preflight, unsafe-generation, suggestion, recovery-schema, delete/resize Node and test-diagnostic regressions | 176 passed on 20 October |
| Combined focused provider, preflight, recovery, suggestion, JSON, test-safety, outbox, quota, retention, stream-readiness, workspace/history/API/UI and Node regressions | 508 passed |
| Final workspace/history/API/UI/Node selection after the rendered-sidebar correction, plus pinned Deepgram/VAD and Docker runtime checks | 177 passed |
| `pytest -q tests/test_consultation_split_browser.py` | 16 passed |
| `pytest -q --accessibility-audit tests/accessibility/test_help.py::test_scribe_sidebar_resize_controls` | 1 passed; run by the workspace implementation agent after correcting the rendered template |
| `python scripts/audit_api_auth.py` | Exit 0; all 162 route cases reported OK |
| `python .github/scripts/check-operational-docs.py` | 54 maintained documents checked |
| Changed Python compilation, `node --check` for `actions.js` and `app.js`, `git diff --check` | Passed |

The selections overlap; these are run results, not a sum of unique tests. Focused provider files include `test_llm_runtime.py`, `test_llm_execution.py`, `test_provider_response_bounds.py`, `test_gemini_enterprise_llm.py`, `test_generation_unsafe_failures.py`, `test_template_suggestions.py`, and the split generation/runtime/pre-submit tests. The workspace selection includes the new delete/resize Node harnesses, existing layout/settings/navigator/refactor tests, ten focused history/source/workspace API cases, and four rendered Scribe/bulk-deletion cases. See [testing.md](../testing.md) for the workflow commands. Only existing dependency deprecation warnings remained.

Earlier focused verification included passing pinned Deepgram/VAD compatibility and Docker runtime tests. Those watchpoints remain upgrade obligations rather than open defects in the pinned versions.

## Remaining decisions and operational work

- Ollama follow-ups and Quick Actions still omit `num_predict` while reserving a nominal 1,600 output tokens. The proposed change is a matching 1,600-token ceiling with existing truncation rejection. This policy change was presented to the user; no unanswered choice is treated as approval.
- Google still lists Gemini 2.5 Flash retirement on 20 October 2026 in its [model lifecycle documentation](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions), checked on 19 October. An approved replacement needs deployment residency/capacity and clinical validation. No live provider calls or selection changes were made.
- Worker capacity/isolation and the dated unhealthy application container require the affected deployment. The local metadata-only Docker inventory showed healthy PostgreSQL/Redis/Vault containers and no running OpenScribe application container; it cannot clear a historical production incident.
- Deepgram's pinned private connector hook and VAD callback/buffer compatibility must be revalidated when upgrading their dependencies.

No production health, live provider capacity, complete accessibility conformance, or full-suite verification is claimed.
