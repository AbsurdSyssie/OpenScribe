# Pytest Failures

Status: resolved in focused regression run.

- `tests/test_admin_ui.py::test_login_page_exposes_bootstrap_when_database_is_empty`
  - Expected `action="/transcribe/sessions"` in rendered login/bootstrap HTML.
- `tests/test_admin_ui.py::test_user_transcribe_glm_2_page_shows_all_emis_sections_for_structured_templates`
  - Expected structured EMIS context input `name="context_problem"`.
- `tests/test_admin_ui.py::test_user_transcribe_page_truncates_document_switcher_labels`
  - Expected truncated document switcher label containing `Please arrange a review appointment with the duty`.
- `tests/test_admin_ui.py::test_user_transcribe_page_shows_structured_emis_context_inputs`
  - Expected structured EMIS context input `name="context_problem"`.
- `tests/test_admin_ui.py::test_user_transcribe_page_enables_followups_from_freeform_note_content`
  - Expected quick-action context textarea marker `data-quick-action-context-input></textarea>`.
- `tests/test_admin_ui.py::test_user_transcribe_page_shows_transcript_and_followup_empty_states`
  - Expected follow-up empty-state copy.
- `tests/test_admin_ui.py::test_user_transcribe_page_reloads_persisted_structured_emis_context`
  - Expected structured EMIS context input `name="context_problem"`.
- `tests/test_admin_ui.py::test_user_transcribe_page_can_queue_followup_generation`
  - Expected queued follow-up text `Waiting to be written...`.
- `tests/test_admin_ui.py::test_user_transcribe_page_can_run_quick_action`
  - Expected `Quick picks` section.
- `tests/test_admin_ui.py::test_admin_templates_sync_optional_provider_credential_actions`
  - Expected JS line `credentialAction.value = adapter === 'openai_cloud' ? 'replace' : 'keep';`.
- `tests/test_api.py::test_system_admin_can_provision_and_read_team_llm_configs_without_secret_reveal`
  - Expected status `200`, got `401`.

## Verification

- `.venv/bin/pytest -q tests/test_admin_ui.py::test_login_page_exposes_bootstrap_when_database_is_empty tests/test_admin_ui.py::test_user_transcribe_glm_2_page_uses_structured_template_sections tests/test_admin_ui.py::test_user_transcribe_page_truncates_document_switcher_labels tests/test_admin_ui.py::test_user_transcribe_page_shows_structured_emis_context_inputs tests/test_admin_ui.py::test_user_transcribe_page_enables_followups_from_freeform_note_content tests/test_admin_ui.py::test_user_transcribe_page_shows_transcript_and_followup_empty_states tests/test_admin_ui.py::test_user_transcribe_page_reloads_persisted_structured_emis_context tests/test_admin_ui.py::test_user_transcribe_page_can_queue_followup_generation tests/test_admin_ui.py::test_user_transcribe_page_can_run_quick_action tests/test_admin_ui.py::test_admin_templates_sync_optional_provider_credential_actions tests/test_api.py::test_system_admin_can_provision_and_read_team_llm_configs_without_secret_reveal`: passed, 11 tests.
- `.venv/bin/pytest -q tests/test_api.py -k "llm_draft_invalid_key_creates_no_config_or_vault_secret or llm_provider_preset_saves_and_reclassifies_base_url_override or llm_save_validates_model_against_successful_live_discovery or llm_zero_model_discovery_requires_manual_model or llm_endpoint_change_with_failed_rediscovery_clears_stale_models"`: passed, 5 tests.
