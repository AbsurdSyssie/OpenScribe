# Regression review — 2026-10-19

## Review result

Resolved high-risk findings cover unresolved-stream generation, recovery audio identity and byte preservation, private provider logging, and quota terminalization. The resolved medium-risk finding covers mobile drawer focus interfering with the guide. Focused regressions cover these paths; no blocking finding remains in the reviewed set.

The review found and fixed an order-dependent quota lifecycle defect. `process_quota_lifecycle` changed expired reservations and submitted attempts in memory, then reconciled their split source. A verification fail-open path refreshed the same attempt with `populate_existing=True` before the change had flushed, which could restore the active state and lose cancellation or unknown settlement. The lifecycle now flushes terminal attempt state before source reconciliation. A deterministic verification-first test failed before the fix and passes afterward; a second test covers verification-first submitted-timeout settlement. The reproduced quota defect is resolved.

The first broad run also found stale test expectations for the two additive migrations and the accessible guide/template changes. The migration assertions now include the append-only Deepgram attempt and settlement labels. UI assertions follow the current accessible guide markup, select naming, help copy, and asset cache key. The drawer browser regression now uses the real guide controller and serves only local static assets; the earlier bare-dialog harness did not model the guide's focus handler.

The stream changes retain owner-scoped transcript roots and encrypted transcript content. Provider and recovery metadata remain bounded; the inspected paths keep provider output and audio out of logs. New barriers reject generation snapshots and split confirmation while a stream remains unresolved. Recovery checks compare exact PCM identity, and connection startup reasserts the private SDK logger's disabled state after application logging configuration. The review found no change to the established redaction, ownership, retention, or draft-review boundaries. Two additive migrations add the accessibility legal-document kind and durable Deepgram stream accounting. Docker Uvicorn now limits WebSocket frames to 65,536 bytes and its queue to four messages; dependencies add the Deepgram SDK and Playwright development runner. Provider and worker checks use synthetic inputs and fakes; no public provider was called.

## Verification

The broad focused selection covered API and route-audit tests, migrations, retention, outbox/quota, LLM execution/runtime, generated-document regeneration, consultation-split lifecycle and browser paths, templates, legal content, dictation, Deepgram streaming, generation diagnostics, and workspace UI contracts. Command:

```bash
APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app .venv/bin/pytest -q \
  tests/test_api.py tests/test_api_route_audit.py tests/test_migrations.py \
  tests/test_retention.py tests/test_dspt_retention.py tests/test_task_outbox.py \
  tests/test_quota_lifecycle.py tests/test_user_quotas.py tests/test_llm_execution.py \
  tests/test_llm_runtime.py tests/test_generated_document_regeneration.py \
  tests/test_generated_document_list_loading.py tests/test_transcript_streaming.py \
  tests/test_transcript_ingestion_diagnostics.py tests/test_consultation_split_analysis.py \
  tests/test_consultation_split_api.py tests/test_consultation_split_browser.py \
  tests/test_consultation_split_generation.py tests/test_consultation_split_generation_runtime.py \
  tests/test_consultation_split_intents.py tests/test_consultation_split_intent_progress.py \
  tests/test_consultation_split_pre_submit.py tests/test_consultation_split_queue.py \
  tests/test_consultation_split_runtime.py tests/test_consultation_split_sources.py \
  tests/test_consultation_splits.py tests/test_consultation_split_verification.py \
  tests/test_consultation_split_worker_diagnostics.py tests/test_consultation_split_writer_locks.py \
  tests/test_template_io.py tests/test_template_io_workspace_ui.py \
  tests/test_template_portability_contracts.py tests/test_template_suggestions.py \
  tests/test_template_suggestions_js.py tests/test_legal_content.py \
  tests/test_dictation_modal_actions.py tests/test_deepgram_live_route.py \
  tests/test_deepgram_streaming.py tests/test_deepgram_vad_memory_bound.py \
  tests/test_deepgram_live_stream_frontend.py tests/test_deepgram_recovery_wav.py \
  tests/test_deepgram_media_teardown.py tests/test_generation_json_validation.py \
  tests/test_generation_unsafe_failures.py tests/test_generation_ollama_unsafe.py \
  tests/test_generation_usage_logging.py tests/test_generation_task_diagnostics.py \
  tests/test_generation_presentation.py tests/test_stream_generation_barrier.py \
  tests/test_workspace_drawer_modal_browser.py tests/test_admin_ui.py \
  tests/test_settings_quick_actions.py tests/test_document_navigator_js.py \
  tests/test_split_review_js.py tests/test_web_refactor.py tests/test_workspace_navigation.py \
  tests/test_workspace_frontend_contract.py tests/test_workspace_scribe_layout_contract.py \
  tests/test_workspace_sidebar_visual_contract.py tests/test_docker_runtime.py
```

That run reported **1,893 passed and 6 failed**: stale assertions for the Deepgram settlement enum, Quick Action select, Quick Action help text, and stylesheet cache key (two tests), plus a bare-dialog focus harness. The split quota defect did not fail in that randomized-order run. A subsequent affected run reported 114 passed and 2 failed: the corrected Quick Action test used a nonexistent ID, and a split verification reservation remained active. A deterministic verification-first red run reproduced the quota defect. After updating the selector and flushing terminal attempt state before source reconciliation, the affected run passed: **217 passed, 3 warnings**.

The follow-up selection was:

```bash
APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app .venv/bin/pytest -q \
  tests/test_migrations.py::test_quota_accounting_foundation_schema_has_metadata_only_constraints_and_fks \
  tests/test_migrations.py::test_consultation_split_passive_schema_has_encrypted_content_and_reversible_membership_links \
  tests/test_workspace_drawer_modal_browser.py \
  tests/test_admin_ui.py::test_user_transcribe_page_enables_followups_from_structured_note_content \
  tests/test_settings_quick_actions.py::test_quick_action_io_configures_shared_controller_and_wires_help_adapter \
  tests/test_web_refactor.py::test_followup_detailing_keeps_controls_aligned_and_output_scrollable \
  tests/test_web_refactor.py::test_splash_and_transcribe_styles_are_cacheable_static_assets \
  tests/test_transcript_streaming.py tests/test_deepgram_recovery_wav.py \
  tests/test_deepgram_streaming.py tests/test_consultation_splits.py \
  tests/test_stream_generation_barrier.py tests/test_quota_lifecycle.py \
  tests/test_consultation_split_generation_runtime.py \
  tests/test_consultation_split_verification.py tests/test_llm_execution.py
```

The deterministic quota checks were also run directly:

```bash
APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app .venv/bin/pytest -q \
  tests/test_consultation_splits.py::test_split_reservation_expiry_fails_analysis_but_keeps_verification_fail_open \
  tests/test_consultation_splits.py::test_split_submitted_timeout_terminalizes_attempts_and_preserves_phase_parent_semantics
```

Result: **2 passed** after the lifecycle flush. The expiry regression was forced to process verification first by assigning its synthetic attempt a lower UUID; the tests refresh persisted rows before checking terminal state.

The full opt-in browser audit covered all 26 tests in `tests/accessibility/`, including the local network guard, authenticated Help states, personal settings, editor states, transcription guide states, and keyboard/reflow checks:

```bash
APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app \
  .venv/bin/pytest -q --accessibility-audit tests/accessibility
```

Result: **26 passed, 3 warnings**. The installed Python Playwright is 1.58.2 while `requirements-dev.txt` pins 1.61.0; Chromium was installed and the browser checks ran in this environment.

The documented API authorization audit passed with exit status 0:

```bash
APP_ENV=test COOKIE_SECURE_MODE=auto HSTS_SOURCE=app \
  .venv/bin/python scripts/audit_api_auth.py
```

Maintained documentation validation checked 54 files successfully. `git diff --check` and Python compilation of the app and new test harnesses also passed.

## Review limits

The entire repository suite was not run. Browser verification used the installed Playwright version noted above. Manual assistive-technology checks remain governed by the existing accessibility checklist; this audit is not a conformance certification.

The review covered the pending changes on baseline `717d269`; commit and sync are recommended for that reviewed set. Three delegated Luna workstreams contributed implementation, tests, and documentation review. No production deployment or live provider call was made. The separate `TEMPLATE-SUGGESTION-DUPLICATE-JSON-KEY` follow-up remains recorded in [DISCOVERED_ISSUES.md](../../DISCOVERED_ISSUES.md) and was outside this review's requested change set.
