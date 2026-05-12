import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

from tests.db_utils import ensure_database_exists, ensure_safe_test_database_url


TEST_DATABASE_URL = ensure_safe_test_database_url()
ensure_database_exists(TEST_DATABASE_URL)
engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)


def alembic_config() -> Config:
    os.environ["ALEMBIC_DATABASE_URL"] = TEST_DATABASE_URL
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    return config


def reset_public_schema() -> None:
    engine.dispose()
    with engine.connect() as connection:
        connection = connection.execution_options(isolation_level="AUTOCOMMIT")
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public AUTHORIZATION CURRENT_USER"))
        connection.execute(text("GRANT ALL ON SCHEMA public TO CURRENT_USER"))


def current_tables() -> set[str]:
    inspector = inspect(engine)
    return set(inspector.get_table_names())


@pytest.mark.migration
def test_alembic_upgrade_head_creates_expected_schema():
    reset_public_schema()

    command.upgrade(alembic_config(), "head")

    assert current_tables() == {
        "alembic_version",
        "auth_email_tokens",
            "account_requests",
            "clinical_entities",
            "clinical_entity_runs",
            "default_quick_actions",
        "default_quick_action_versions",
        "default_templates",
        "default_template_versions",
        "deidentification_providers",
        "generated_document_sections",
        "generated_documents",
        "post_consultation_dictation_segments",
        "post_consultation_dictations",
            "provider_usage_events",
            "quick_actions",
            "quick_action_versions",
            "redaction_entities",
            "redaction_runs",
            "security_audit_events",
            "smart_phrases",
        "transcript_manual_pii_entities",
        "teams",
        "team_deidentification_provider_assignments",
        "team_deidentification_selections",
        "team_clinical_nlp_selections",
        "team_llm_configs",
        "team_llm_selections",
        "team_stt_configs",
        "team_stt_selections",
        "template_versions",
        "templates",
        "users",
        "user_llm_preferences",
        "user_encryption_keys",
        "user_app_preferences",
        "user_trusted_devices",
        "user_sessions",
        "user_mfa_methods",
        "user_recovery_codes",
        "transcripts",
        "transcript_ingestion_jobs",
        "transcript_versions",
    }


@pytest.mark.migration
def test_alembic_head_uses_normalized_uniqueness_rules():
    reset_public_schema()
    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000001',
                    'Clinic North',
                    'clinic north',
                    'active',
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                SELECT
                    '00000000-0000-0000-0000-000000000002',
                    'Mixed Case',
                    'mixed.case@example.com',
                    'hash',
                    id,
                    'user',
                    false,
                    'active',
                    false,
                    'complete',
                    true,
                    true,
                    NOW(),
                    NOW(),
                    NULL
                FROM teams
                LIMIT 1
                """
            )
        )

        teams_uniques = inspect(connection).get_unique_constraints("teams")
        users_indexes = inspect(connection).get_indexes("users")

        assert any(item["name"] == "uq_teams_name_key" for item in teams_uniques)
        assert not any(item["name"] == "teams_name_key" and item["column_names"] == ["name"] for item in teams_uniques)
        assert any(item["name"] == "uq_users_email_lower" for item in users_indexes)
        assert not any(item["name"] == "users_email_key" for item in users_indexes)


@pytest.mark.migration
def test_alembic_head_adds_onboarding_and_session_tables():
    reset_public_schema()
    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    inspector = inspect(isolated_engine)

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    session_columns = {column["name"] for column in inspector.get_columns("user_sessions")}
    trusted_device_columns = {column["name"] for column in inspector.get_columns("user_trusted_devices")}
    request_columns = {column["name"] for column in inspector.get_columns("account_requests")}
    stt_columns = {column["name"] for column in inspector.get_columns("team_stt_configs")}
    stt_selection_columns = {column["name"] for column in inspector.get_columns("team_stt_selections")}
    llm_columns = {column["name"] for column in inspector.get_columns("team_llm_configs")}
    llm_selection_columns = {column["name"] for column in inspector.get_columns("team_llm_selections")}
    deidentification_provider_columns = {column["name"] for column in inspector.get_columns("deidentification_providers")}
    deidentification_assignment_columns = {column["name"] for column in inspector.get_columns("team_deidentification_provider_assignments")}
    deidentification_selection_columns = {column["name"] for column in inspector.get_columns("team_deidentification_selections")}
    clinical_nlp_selection_columns = {column["name"] for column in inspector.get_columns("team_clinical_nlp_selections")}
    user_llm_preference_columns = {column["name"] for column in inspector.get_columns("user_llm_preferences")}
    user_app_preference_columns = {column["name"] for column in inspector.get_columns("user_app_preferences")}
    template_indexes = inspector.get_indexes("templates")
    template_columns = {column["name"] for column in inspector.get_columns("templates")}
    template_version_columns = {column["name"] for column in inspector.get_columns("template_versions")}
    default_template_indexes = inspector.get_indexes("default_templates")
    default_template_columns = {column["name"] for column in inspector.get_columns("default_templates")}
    default_template_version_columns = {column["name"] for column in inspector.get_columns("default_template_versions")}
    quick_action_indexes = inspector.get_indexes("quick_actions")
    quick_action_columns = {column["name"] for column in inspector.get_columns("quick_actions")}
    quick_action_version_columns = {column["name"] for column in inspector.get_columns("quick_action_versions")}
    default_quick_action_indexes = inspector.get_indexes("default_quick_actions")
    default_quick_action_columns = {column["name"] for column in inspector.get_columns("default_quick_actions")}
    default_quick_action_version_columns = {column["name"] for column in inspector.get_columns("default_quick_action_versions")}
    generated_document_columns = {column["name"] for column in inspector.get_columns("generated_documents")}
    generated_document_section_columns = {column["name"] for column in inspector.get_columns("generated_document_sections")}
    post_consultation_dictation_columns = {column["name"] for column in inspector.get_columns("post_consultation_dictations")}
    post_consultation_dictation_segment_columns = {
        column["name"] for column in inspector.get_columns("post_consultation_dictation_segments")
    }
    provider_usage_event_columns = {column["name"] for column in inspector.get_columns("provider_usage_events")}
    redaction_run_columns = {column["name"] for column in inspector.get_columns("redaction_runs")}
    redaction_entity_columns = {column["name"] for column in inspector.get_columns("redaction_entities")}
    clinical_entity_run_columns = {column["name"] for column in inspector.get_columns("clinical_entity_runs")}
    clinical_entity_columns = {column["name"] for column in inspector.get_columns("clinical_entities")}
    clinical_entity_run_fks = inspector.get_foreign_keys("clinical_entity_runs")
    manual_pii_columns = {column["name"] for column in inspector.get_columns("transcript_manual_pii_entities")}
    transcript_columns = {column["name"] for column in inspector.get_columns("transcripts")}
    transcript_ingestion_job_columns = {column["name"] for column in inspector.get_columns("transcript_ingestion_jobs")}
    user_encryption_key_columns = {column["name"] for column in inspector.get_columns("user_encryption_keys")}
    auth_email_token_columns = {column["name"] for column in inspector.get_columns("auth_email_tokens")}
    smart_phrase_columns = {column["name"] for column in inspector.get_columns("smart_phrases")}
    smart_phrase_indexes = inspector.get_indexes("smart_phrases")
    smart_phrase_checks = inspector.get_check_constraints("smart_phrases")
    smart_phrase_fks = inspector.get_foreign_keys("smart_phrases")

    assert {"full_name", "must_change_password", "onboarding_state"} <= user_columns
    assert {"session_token_hash", "auth_level", "status", "revoke_reason"} <= session_columns
    assert {"device_token_hash", "last_mfa_verified_at", "expires_at", "revoke_reason"} <= trusted_device_columns
    assert {"requested_name", "requested_email", "requested_team_name", "status"} <= request_columns
    assert {
        "team_id",
        "provider_preset",
        "adapter_kind",
        "base_url",
        "transcribe_path",
        "vault_secret_ref",
        "response_text_path",
        "available_models_json",
        "model_field_name",
        "language_field_name",
        "segments_path",
        "segment_text_field",
        "segment_start_field",
        "segment_end_field",
        "segment_speaker_field",
        "credential_status",
        "credential_fingerprint",
        "inspection_metadata_json",
        "setup_status",
    } <= stt_columns
    stt_indexes = inspector.get_indexes("team_stt_configs")
    assert any(item["name"] == "uq_team_stt_configs_team_label_lower" for item in stt_indexes)
    with isolated_engine.connect() as connection:
        stt_adapter_values = set(
            connection.execute(
                text(
                    """
                    SELECT enumlabel
                    FROM pg_enum
                    JOIN pg_type ON pg_enum.enumtypid = pg_type.oid
                    WHERE pg_type.typname = 'sttadapterkind'
                    """
                )
            ).scalars()
        )
    assert "elevenlabs_speech_to_text" in stt_adapter_values
    assert {"team_id", "purpose", "stt_config_id", "model_name_override", "language_override", "selected_by_user_id"} <= stt_selection_columns
    llm_indexes = inspector.get_indexes("team_llm_configs")
    assert {"team_id", "provider_preset", "adapter_kind", "base_url", "vault_secret_ref", "available_models_json", "inspection_metadata_json", "setup_status"} <= llm_columns
    assert any(item["name"] == "ix_team_llm_configs_setup_status" for item in llm_indexes)
    assert any(item["name"] == "uq_team_llm_configs_team_label_lower" for item in llm_indexes)
    assert {"team_id", "llm_config_id", "allowed_models_json", "model_name_override", "selected_by_user_id"} <= llm_selection_columns
    assert {
        "label",
        "adapter_kind",
        "base_url",
        "detect_path",
        "auth_mode",
        "request_text_field",
        "response_entities_path",
        "response_start_field",
        "response_end_field",
        "response_type_field",
        "entity_type_map_json",
        "vault_secret_ref",
        "clinical_detection_enabled",
        "clinical_detection_allow_unredacted",
        "is_active",
        "is_builtin",
    } <= deidentification_provider_columns
    assert {"team_id", "provider_id", "assigned_by_user_id"} <= deidentification_assignment_columns
    assert {"team_id", "provider_id", "selected_by_user_id"} <= deidentification_selection_columns
    assert {"team_id", "provider_id", "selected_by_user_id"} <= clinical_nlp_selection_columns
    assert {"user_id", "preferred_model_name"} <= user_llm_preference_columns
    assert {"user_id", "preferences_json"} <= user_app_preference_columns
    assert {"user_id", "purpose", "token_hash", "expires_at", "used_at", "created_by_user_id"} <= auth_email_token_columns
    assert {"owner_user_id", "trigger", "expansion_text", "description", "last_used_at", "times_used"} <= smart_phrase_columns
    assert any(item["name"] == "uq_smart_phrases_owner_trigger_lower" for item in smart_phrase_indexes)
    assert any(item["name"] == "ck_smart_phrases_trigger_format" for item in smart_phrase_checks)
    assert any(item["name"] == "ck_smart_phrases_expansion_length" for item in smart_phrase_checks)
    assert any(
        fk["referred_table"] == "users"
        and fk["constrained_columns"] == ["owner_user_id"]
        and fk.get("options", {}).get("ondelete") == "CASCADE"
        for fk in smart_phrase_fks
    )
    assert any(item["name"] == "uq_templates_team_name_lower" for item in template_indexes)
    assert any(item["name"] == "uq_templates_owner_name_lower" for item in template_indexes)
    assert {"scope", "owner_user_id", "team_id", "name", "created_by_user_id"} <= template_columns
    assert {"template_id", "version_no", "mode", "prompt_text", "config_json"} <= template_version_columns
    assert any(item["name"] == "uq_default_templates_name_lower" for item in default_template_indexes)
    assert {"name", "description", "is_active", "created_by_user_id"} <= default_template_columns
    assert {"default_template_id", "version_no", "mode", "prompt_text", "config_json"} <= default_template_version_columns
    assert any(item["name"] == "uq_quick_actions_team_name_lower" for item in quick_action_indexes)
    assert any(item["name"] == "uq_quick_actions_owner_name_lower" for item in quick_action_indexes)
    assert {"scope", "owner_user_id", "team_id", "name", "created_by_user_id"} <= quick_action_columns
    assert {"quick_action_id", "version_no", "mode", "prompt_text"} <= quick_action_version_columns
    assert any(item["name"] == "uq_default_quick_actions_name_lower" for item in default_quick_action_indexes)
    assert {"name", "description", "is_active", "created_by_user_id"} <= default_quick_action_columns
    assert {"default_quick_action_id", "version_no", "mode", "prompt_text"} <= default_quick_action_version_columns
    assert {
        "owner_user_id",
        "team_id",
        "transcript_id",
        "transcript_version_id",
        "redaction_run_id",
        "template_version_id",
        "quick_action_version_id",
        "llm_config_id",
        "source_template_name",
        "source_quick_action_name",
        "follow_up_prompt_text",
        "prompt_snapshot_text",
        "structured_context_json",
        "structured_section_definitions_json",
        "llm_request_payload_json_encrypted",
        "original_output_text_encrypted",
        "llm_adapter_kind",
        "llm_base_url",
        "celery_task_id",
        "input_token_count",
        "output_token_count",
        "total_token_count",
        "estimated_cost_usd",
        "duration_ms",
        "provider_duration_ms",
        "error_code",
        "provider_error_code",
        "provider_http_status",
        "error_message",
        "failed_provider_output_redacted_encrypted",
        "started_at",
        "completed_at",
    } <= generated_document_columns
    assert {
        "generated_document_id",
        "section_key",
        "section_label",
        "section_order",
        "original_text_encrypted",
        "edited_text_encrypted",
        "is_edited",
    } <= generated_document_section_columns
    assert {
        "transcript_id",
        "owner_user_id",
        "team_id",
        "combined_edited_text_encrypted",
        "is_combined_text_user_edited",
        "latest_appended_at",
        "created_at",
        "updated_at",
    } <= post_consultation_dictation_columns
    assert {
        "post_consultation_dictation_id",
        "owner_user_id",
        "team_id",
        "sequence_no",
        "asr_text_encrypted",
        "created_at",
    } <= post_consultation_dictation_segment_columns
    assert {
        "team_id",
        "owner_user_id",
        "generated_document_id",
        "transcript_id",
        "llm_config_id",
        "feature_type",
        "event_type",
        "provider_adapter",
        "model_name",
        "status",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "estimated_cost_usd",
        "duration_ms",
        "provider_duration_ms",
        "error_code",
        "provider_error_code",
        "provider_http_status",
        "created_at",
    } <= provider_usage_event_columns
    assert {
        "transcript_id",
        "transcript_version_id",
        "owner_user_id",
        "team_id",
        "status",
        "redacted_text_encrypted",
        "mapping_hash",
        "entity_count",
        "api_provider",
        "api_model_or_version",
        "error_code",
        "created_at",
        "updated_at",
        "failed_at",
    } <= redaction_run_columns
    assert {
        "redaction_run_id",
        "entity_order",
        "entity_type",
        "placeholder",
        "original_value_encrypted",
        "normalized_value_hash",
        "occurrence_count",
        "created_at",
    } <= redaction_entity_columns
    assert {
        "transcript_id",
        "transcript_version_id",
        "redaction_run_id",
        "owner_user_id",
        "team_id",
        "provider_id",
        "status",
        "source_text_redacted",
        "entity_count",
        "api_provider",
        "error_code",
        "created_at",
    } <= clinical_entity_run_columns
    provider_fk = next(item for item in clinical_entity_run_fks if item["referred_table"] == "deidentification_providers")
    assert provider_fk["options"].get("ondelete") == "SET NULL"
    assert {
        "clinical_entity_run_id",
        "entity_order",
        "entity_type",
        "value_encrypted",
        "normalized_value_hash",
        "occurrence_count",
        "score",
        "created_at",
    } <= clinical_entity_columns
    assert {
        "transcript_id",
        "owner_user_id",
        "team_id",
        "entity_type",
        "original_value_encrypted",
        "normalized_value_hash",
        "occurrence_count",
        "created_at",
        "updated_at",
    } <= manual_pii_columns
    assert {"owner_user_id", "team_id", "current_draft_text_encrypted", "structured_context_json", "ingestion_mode", "next_live_chunk_sequence_no_applied"} <= transcript_columns
    assert {
        "transcript_id",
        "owner_user_id",
        "team_id",
        "job_kind",
        "chunk_sequence_no",
        "status",
        "celery_task_id",
        "source_audio_blob",
        "source_audio_vault_ref",
        "source_audio_size_bytes",
        "source_audio_duration_seconds",
        "declared_duration_seconds",
        "result_text_encrypted",
        "stt_config_id",
        "stt_adapter_kind",
        "stt_base_url",
        "stt_transcribe_path",
        "stt_model_name",
        "stt_model_field_name",
        "stt_language",
        "stt_language_field_name",
        "stt_file_field_name",
        "stt_response_text_path",
        "stt_segments_path",
        "stt_segment_text_field",
        "stt_segment_start_field",
        "stt_segment_end_field",
        "stt_segment_speaker_field",
        "stt_extra_form_fields_json",
    } <= transcript_ingestion_job_columns
    assert {
        "user_id",
        "dek_version",
        "wrapped_dek",
        "kek_mount",
        "kek_key_name",
        "kek_key_version",
        "is_active",
        "created_at",
        "rotated_at",
    } <= user_encryption_key_columns


@pytest.mark.migration
def test_alembic_head_adds_post_consultation_dictation_constraints_and_cascades():
    reset_public_schema()
    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    inspector = inspect(isolated_engine)

    dictation_uniques = inspector.get_unique_constraints("post_consultation_dictations")
    segment_uniques = inspector.get_unique_constraints("post_consultation_dictation_segments")
    dictation_fks = inspector.get_foreign_keys("post_consultation_dictations")
    segment_fks = inspector.get_foreign_keys("post_consultation_dictation_segments")

    assert any(
        item["name"] == "uq_post_consultation_dictations_transcript"
        and item["column_names"] == ["transcript_id"]
        for item in dictation_uniques
    )
    assert any(
        item["name"] == "uq_post_consultation_dictation_segments_sequence"
        and item["column_names"] == ["post_consultation_dictation_id", "sequence_no"]
        for item in segment_uniques
    )
    assert any(
        item["referred_table"] == "transcripts"
        and item["constrained_columns"] == ["transcript_id"]
        and item.get("options", {}).get("ondelete") == "CASCADE"
        for item in dictation_fks
    )
    assert any(
        item["referred_table"] == "post_consultation_dictations"
        and item["constrained_columns"] == ["post_consultation_dictation_id"]
        and item.get("options", {}).get("ondelete") == "CASCADE"
        for item in segment_fks
    )


@pytest.mark.migration
def test_alembic_head_supports_multiple_stt_selection_purposes_per_team():
    reset_public_schema()
    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000401',
                    'Admin User',
                    'admin-purpose@example.com',
                    'hash',
                    NULL,
                    NULL,
                    true,
                    'active',
                    false,
                    'complete',
                    true,
                    false,
                    NOW(),
                    NOW(),
                    NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000402',
                    'Clinic Purpose',
                    'clinic purpose',
                    'active',
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO team_stt_configs (
                    id, team_id, label, adapter_kind, base_url, transcribe_path, auth_mode, model_name,
                    available_models_json, file_field_name, language, response_text_path, extra_form_fields_json, vault_secret_ref,
                    is_active, created_by_user_id, updated_by_user_id, created_at, updated_at
                )
                VALUES
                (
                    '00000000-0000-0000-0000-000000000403',
                    '00000000-0000-0000-0000-000000000402',
                    'Conversation STT',
                    'openai_compatible_rest',
                    'http://127.0.0.1:7000',
                    '/v1/audio/transcriptions',
                    'bearer',
                    'whisper-1',
                    '[]'::json,
                    'file',
                    'en',
                    'text',
                    '{}'::json,
                    'secret:openscribe/stt/team/402/config/403',
                    true,
                    '00000000-0000-0000-0000-000000000401',
                    '00000000-0000-0000-0000-000000000401',
                    NOW(),
                    NOW()
                ),
                (
                    '00000000-0000-0000-0000-000000000404',
                    '00000000-0000-0000-0000-000000000402',
                    'Dictation STT',
                    'openai_compatible_rest',
                    'http://127.0.0.1:7001',
                    '/v1/audio/transcriptions',
                    'bearer',
                    'gpt-4o-mini-transcribe',
                    '[]'::json,
                    'file',
                    'en',
                    'text',
                    '{}'::json,
                    'secret:openscribe/stt/team/402/config/404',
                    true,
                    '00000000-0000-0000-0000-000000000401',
                    '00000000-0000-0000-0000-000000000401',
                    NOW(),
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO team_stt_selections (
                    id, team_id, purpose, stt_config_id, model_name_override, language_override, selected_by_user_id, created_at, updated_at
                )
                VALUES
                (
                    '00000000-0000-0000-0000-000000000405',
                    '00000000-0000-0000-0000-000000000402',
                    'conversation',
                    '00000000-0000-0000-0000-000000000403',
                    NULL,
                    NULL,
                    '00000000-0000-0000-0000-000000000401',
                    NOW(),
                    NOW()
                ),
                (
                    '00000000-0000-0000-0000-000000000406',
                    '00000000-0000-0000-0000-000000000402',
                    'post_consultation_dictation',
                    '00000000-0000-0000-0000-000000000404',
                    NULL,
                    NULL,
                    '00000000-0000-0000-0000-000000000401',
                    NOW(),
                    NOW()
                )
                """
            )
        )

        count = connection.execute(
            text(
                """
                SELECT COUNT(*)
                FROM team_stt_selections
                WHERE team_id = '00000000-0000-0000-0000-000000000402'
                """
            )
        ).scalar_one()

    assert count == 2


@pytest.mark.migration
def test_unique_asset_name_migration_dedupes_existing_rows_before_indexes():
    reset_public_schema()
    command.upgrade(alembic_config(), "aa7c8d9e0f1a")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000101',
                    'Clinic Dedupe',
                    'clinic dedupe',
                    'active',
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000102',
                    'Dedupe User',
                    'dedupe@example.com',
                    'hash',
                    '00000000-0000-0000-0000-000000000101',
                    'user',
                    false,
                    'active',
                    false,
                    'complete',
                    true,
                    true,
                    NOW(),
                    NOW(),
                    NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO templates (id, scope, owner_user_id, team_id, name, description, is_active, created_by_user_id, created_at, updated_at)
                VALUES
                ('00000000-0000-0000-0000-000000000111', 'user', '00000000-0000-0000-0000-000000000102', NULL, 'Test Name', NULL, true, '00000000-0000-0000-0000-000000000102', NOW(), NOW()),
                ('00000000-0000-0000-0000-000000000112', 'user', '00000000-0000-0000-0000-000000000102', NULL, ' test name ', NULL, true, '00000000-0000-0000-0000-000000000102', NOW(), NOW())
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO quick_actions (id, scope, owner_user_id, team_id, name, description, is_active, created_by_user_id, created_at, updated_at)
                VALUES
                ('00000000-0000-0000-0000-000000000121', 'user', '00000000-0000-0000-0000-000000000102', NULL, 'Test Action', NULL, true, '00000000-0000-0000-0000-000000000102', NOW(), NOW()),
                ('00000000-0000-0000-0000-000000000122', 'user', '00000000-0000-0000-0000-000000000102', NULL, 'test action', NULL, true, '00000000-0000-0000-0000-000000000102', NOW(), NOW())
                """
            )
        )

    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        template_names = connection.execute(
            text(
                """
                SELECT name
                FROM templates
                WHERE owner_user_id = '00000000-0000-0000-0000-000000000102'
                ORDER BY name
                """
            )
        ).scalars().all()
        quick_action_names = connection.execute(
            text(
                """
                SELECT name
                FROM quick_actions
                WHERE owner_user_id = '00000000-0000-0000-0000-000000000102'
                ORDER BY name
                """
            )
        ).scalars().all()

    assert template_names == ["Test Name", "test name copy 2"]
    assert quick_action_names == ["Test Action", "test action copy 2"]


@pytest.mark.migration
def test_alembic_upgrade_dedupes_existing_asset_names_without_copy_name_collisions():
    reset_public_schema()
    command.upgrade(alembic_config(), "aa7c8d9e0f1a")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000201',
                    'Clinic Dedupe',
                    'clinic dedupe',
                    'active',
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000202',
                    'Collision User',
                    'collision@example.com',
                    'hash',
                    '00000000-0000-0000-0000-000000000201',
                    'user',
                    false,
                    'active',
                    false,
                    'complete',
                    true,
                    true,
                    NOW(),
                    NOW(),
                    NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO templates (id, scope, owner_user_id, team_id, name, description, is_active, created_by_user_id, created_at, updated_at)
                VALUES
                ('00000000-0000-0000-0000-000000000211', 'user', '00000000-0000-0000-0000-000000000202', NULL, 'Clinic Letter', NULL, true, '00000000-0000-0000-0000-000000000202', NOW() - INTERVAL '2 minutes', NOW() - INTERVAL '2 minutes'),
                ('00000000-0000-0000-0000-000000000212', 'user', '00000000-0000-0000-0000-000000000202', NULL, 'clinic letter', NULL, true, '00000000-0000-0000-0000-000000000202', NOW() - INTERVAL '1 minute', NOW() - INTERVAL '1 minute'),
                ('00000000-0000-0000-0000-000000000213', 'user', '00000000-0000-0000-0000-000000000202', NULL, 'Clinic Letter copy 2', NULL, true, '00000000-0000-0000-0000-000000000202', NOW(), NOW())
                """
            )
        )
    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        template_names = connection.execute(
            text(
                """
                SELECT name
                FROM templates
                WHERE owner_user_id = '00000000-0000-0000-0000-000000000202'
                ORDER BY name
                """
            )
        ).scalars().all()

    assert template_names == ["Clinic Letter", "Clinic Letter copy 2", "clinic letter copy 3"]


@pytest.mark.migration
def test_llm_config_label_migration_dedupes_before_unique_index():
    reset_public_schema()
    command.upgrade(alembic_config(), "b1c2d3e4f5a6")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES ('00000000-0000-0000-0000-000000000501', 'Clinic LLM Labels', 'clinic llm labels', 'active', 30, NOW(), NOW())
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000502', 'LLM Admin', 'llm-label-admin@example.com', 'hash', NULL, NULL,
                    true, 'active', false, 'complete', true, true, NOW(), NOW(), NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO team_llm_configs (
                    id, team_id, label, provider_preset, adapter_kind, base_url, auth_mode, model_name,
                    available_models_json, inspection_metadata_json, setup_status, vault_secret_ref, is_active,
                    created_by_user_id, updated_by_user_id, created_at, updated_at
                )
                VALUES
                ('00000000-0000-0000-0000-000000000511', '00000000-0000-0000-0000-000000000501', 'OpenRouter', 'openrouter', 'openai_chat', 'https://openrouter.ai/api/v1', 'bearer', 'model-a', '[]'::json, '{}'::json, 'ready', 'secret:a', true, '00000000-0000-0000-0000-000000000502', '00000000-0000-0000-0000-000000000502', NOW() - INTERVAL '2 minutes', NOW() - INTERVAL '2 minutes'),
                ('00000000-0000-0000-0000-000000000512', '00000000-0000-0000-0000-000000000501', ' openrouter ', 'openrouter', 'openai_chat', 'https://openrouter.ai/api/v1', 'bearer', 'model-b', '[]'::json, '{}'::json, 'ready', 'secret:b', true, '00000000-0000-0000-0000-000000000502', '00000000-0000-0000-0000-000000000502', NOW() - INTERVAL '1 minute', NOW() - INTERVAL '1 minute'),
                ('00000000-0000-0000-0000-000000000513', '00000000-0000-0000-0000-000000000501', 'OpenRouter copy 2', 'openrouter', 'openai_chat', 'https://openrouter.ai/api/v1', 'bearer', 'model-c', '[]'::json, '{}'::json, 'ready', 'secret:c', true, '00000000-0000-0000-0000-000000000502', '00000000-0000-0000-0000-000000000502', NOW(), NOW())
                """
            )
        )

    command.upgrade(alembic_config(), "head")

    with isolated_engine.begin() as connection:
        names = connection.execute(
            text(
                """
                SELECT label
                FROM team_llm_configs
                WHERE team_id = '00000000-0000-0000-0000-000000000501'
                ORDER BY label
                """
            )
        ).scalars().all()
        indexes = inspect(connection).get_indexes("team_llm_configs")

    assert names == ["OpenRouter", "OpenRouter copy 2", "openrouter copy 3"]
    assert any(item["name"] == "uq_team_llm_configs_team_label_lower" for item in indexes)


@pytest.mark.migration
def test_alembic_upgrade_backfills_structured_section_snapshots_for_existing_documents():
    reset_public_schema()
    command.upgrade(alembic_config(), "bb8d9e0f1a2b")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000301',
                    'Clinic Snapshot',
                    'clinic snapshot',
                    'active',
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000302',
                    'Snapshot User',
                    'snapshot-migration@example.com',
                    'hash',
                    '00000000-0000-0000-0000-000000000301',
                    'user',
                    false,
                    'active',
                    false,
                    'complete',
                    true,
                    true,
                    NOW(),
                    NOW(),
                    NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO transcripts (
                    id, owner_user_id, team_id, title, current_draft_text_encrypted, ingestion_mode, status,
                    retention_days_applied, retention_expires_at, created_at, next_live_chunk_sequence_no_applied
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000303',
                    '00000000-0000-0000-0000-000000000302',
                    '00000000-0000-0000-0000-000000000301',
                    'Visit',
                    'draft',
                    'whole_file',
                    'ready',
                    30,
                    NOW() + INTERVAL '30 days',
                    NOW(),
                    1
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO transcript_versions (id, transcript_id, version_no, text_encrypted, created_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000304',
                    '00000000-0000-0000-0000-000000000303',
                    1,
                    'draft',
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO generated_documents (
                    id, owner_user_id, team_id, transcript_id, transcript_version_id, generator_type,
                    source_template_name, prompt_snapshot_text, status, title, document_mode,
                    original_output_text_encrypted, edited_output_text_encrypted, is_edited,
                    retention_expires_at, created_at, updated_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000305',
                    '00000000-0000-0000-0000-000000000302',
                    '00000000-0000-0000-0000-000000000301',
                    '00000000-0000-0000-0000-000000000303',
                    '00000000-0000-0000-0000-000000000304',
                    'template',
                    'EMIS note',
                    'prompt',
                    'ready',
                    'Structured note',
                    'structured',
                    'Problem\nPain',
                    'Problem\nPain',
                    false,
                    NOW() + INTERVAL '30 days',
                    NOW(),
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO generated_document_sections (
                    id, generated_document_id, section_key, section_label, section_order,
                    original_text_encrypted, edited_text_encrypted, is_edited, created_at, updated_at
                )
                VALUES
                (
                    '00000000-0000-0000-0000-000000000306',
                    '00000000-0000-0000-0000-000000000305',
                    'problem',
                    'Problem',
                    1,
                    'Pain',
                    'Pain',
                    false,
                    NOW(),
                    NOW()
                ),
                (
                    '00000000-0000-0000-0000-000000000307',
                    '00000000-0000-0000-0000-000000000305',
                    'history',
                    'History',
                    2,
                    'Started yesterday',
                    'Started yesterday',
                    false,
                    NOW(),
                    NOW()
                )
                """
            )
        )

    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        snapshot = connection.execute(
            text(
                """
                SELECT structured_section_definitions_json
                FROM generated_documents
                WHERE id = '00000000-0000-0000-0000-000000000305'
                """
            )
        ).scalar_one()

    assert snapshot == {
        "profile": "emis",
        "sections": [
            {"section_key": "problem", "section_label": "Problem", "section_order": 1},
            {"section_key": "history", "section_label": "History", "section_order": 2},
        ],
    }


@pytest.mark.migration
def test_alembic_head_supports_new_stt_adapter_values():
    reset_public_schema()
    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000020',
                    'Admin User',
                    'admin@example.com',
                    'hash',
                    NULL,
                    NULL,
                    true,
                    'active',
                    false,
                    'complete',
                    true,
                    false,
                    NOW(),
                    NOW(),
                    NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000021',
                    'Clinic North',
                    'clinic north',
                    'active',
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO team_stt_configs (
                    id, team_id, label, adapter_kind, base_url, transcribe_path, auth_mode, model_name,
                    available_models_json, file_field_name, language, response_text_path, extra_form_fields_json, vault_secret_ref,
                    is_active, created_by_user_id, updated_by_user_id, created_at, updated_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000022',
                    '00000000-0000-0000-0000-000000000021',
                    'OpenAI Cloud',
                    'openai_cloud',
                    'https://api.openai.com/v1',
                    '/v1/audio/transcriptions',
                    'bearer',
                    'gpt-4o-mini-transcribe',
                    '[]'::json,
                    'file',
                    NULL,
                    'text',
                    '{}'::jsonb,
                    'secret:openscribe/stt/team/1/config/1',
                    true,
                    '00000000-0000-0000-0000-000000000020',
                    '00000000-0000-0000-0000-000000000020',
                    NOW(),
                    NOW()
                )
                """
            )
        )
        adapter_kind = connection.execute(text("SELECT adapter_kind::text FROM team_stt_configs")).scalar_one()

    assert adapter_kind == "openai_cloud"


@pytest.mark.migration
def test_alembic_head_supports_llm_adapter_values():
    reset_public_schema()
    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000120',
                    'Admin User',
                    'llm-admin@example.com',
                    'hash',
                    NULL,
                    NULL,
                    true,
                    'active',
                    false,
                    'complete',
                    true,
                    false,
                    NOW(),
                    NOW(),
                    NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000121',
                    'Clinic South',
                    'clinic south',
                    'active',
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO team_llm_configs (
                    id, team_id, label, provider_preset, adapter_kind, base_url, auth_mode, model_name,
                    available_models_json, inspection_metadata_json, setup_status, vault_secret_ref,
                    is_active, created_by_user_id, updated_by_user_id, created_at, updated_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000122',
                    '00000000-0000-0000-0000-000000000121',
                    'Local Ollama',
                    'ollama',
                    'ollama_chat',
                    'http://localhost:11434',
                    'none',
                    'llama3.2',
                    '[]'::json,
                    '{}'::json,
                    'ready',
                    '',
                    true,
                    '00000000-0000-0000-0000-000000000120',
                    '00000000-0000-0000-0000-000000000120',
                    NOW(),
                    NOW()
                )
                """
            )
        )

        adapter_kind, auth_mode = connection.execute(text("SELECT adapter_kind::text, auth_mode::text FROM team_llm_configs")).one()

    assert adapter_kind == "ollama_chat"
    assert auth_mode == "none"


@pytest.mark.migration
def test_alembic_head_supports_bedrock_llm_adapter_value():
    reset_public_schema()
    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000130',
                    'Admin User',
                    'bedrock-admin@example.com',
                    'hash',
                    NULL,
                    NULL,
                    true,
                    'active',
                    false,
                    'complete',
                    true,
                    false,
                    NOW(),
                    NOW(),
                    NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000131',
                    'Clinic Bedrock',
                    'clinic bedrock',
                    'active',
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO team_llm_configs (
                    id, team_id, label, provider_preset, adapter_kind, base_url, auth_mode, model_name,
                    available_models_json, inspection_metadata_json, setup_status, vault_secret_ref,
                    is_active, created_by_user_id, updated_by_user_id, created_at, updated_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000132',
                    '00000000-0000-0000-0000-000000000131',
                    'Amazon Bedrock',
                    'bedrock_http_gateway',
                    'bedrock_chat',
                    'https://bedrock-mantle.us-east-1.api.aws/v1',
                    'bearer',
                    'anthropic.claude-3-7-sonnet-20250219-v1:0',
                    '[]'::json,
                    '{}'::json,
                    'ready',
                    'secret:openscribe/llm/team/1/config/2',
                    true,
                    '00000000-0000-0000-0000-000000000130',
                    '00000000-0000-0000-0000-000000000130',
                    NOW(),
                    NOW()
                )
                """
            )
        )

        adapter_kind, auth_mode = connection.execute(text("SELECT adapter_kind::text, auth_mode::text FROM team_llm_configs")).one()

    assert adapter_kind == "bedrock_chat"
    assert auth_mode == "bearer"


@pytest.mark.migration
def test_alembic_backfills_llm_provider_presets():
    reset_public_schema()
    command.upgrade(alembic_config(), "z6b7c8d9e0f1")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000140',
                    'Admin User',
                    'llm-preset-admin@example.com',
                    'hash',
                    NULL,
                    NULL,
                    true,
                    'active',
                    false,
                    'complete',
                    true,
                    false,
                    NOW(),
                    NOW(),
                    NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000141',
                    'Clinic Presets',
                    'clinic presets',
                    'active',
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        for config_id, label, adapter, base_url, model in [
            ("00000000-0000-0000-0000-000000000142", "OpenAI", "openai_chat", "https://api.openai.com/v1", "gpt-4o-mini"),
            ("00000000-0000-0000-0000-000000000143", "OpenRouter", "openai_chat", "https://openrouter.ai/api/v1", "openai/gpt-4o"),
            ("00000000-0000-0000-0000-000000000144", "Bedrock", "bedrock_chat", "https://bedrock-mantle.eu-west-2.api.aws/v1", "anthropic.claude"),
            ("00000000-0000-0000-0000-000000000145", "Ollama", "ollama_chat", "http://localhost:11434", "llama3.2"),
            ("00000000-0000-0000-0000-000000000146", "Custom", "openai_chat", "https://llm.example.com/v1", "custom-model"),
        ]:
            connection.execute(
                text(
                    """
                    INSERT INTO team_llm_configs (
                        id, team_id, label, adapter_kind, base_url, auth_mode, model_name, available_models_json,
                        vault_secret_ref, is_active, created_by_user_id, updated_by_user_id, created_at, updated_at
                    )
                    VALUES (
                        :id,
                        '00000000-0000-0000-0000-000000000141',
                        :label,
                        :adapter_kind,
                        :base_url,
                        'bearer',
                        :model_name,
                        '[]'::json,
                        'secret:openscribe/llm/team/1/config/2',
                        true,
                        '00000000-0000-0000-0000-000000000140',
                        '00000000-0000-0000-0000-000000000140',
                        NOW(),
                        NOW()
                    )
                    """
                ),
                {"id": config_id, "label": label, "adapter_kind": adapter, "base_url": base_url, "model_name": model},
            )

    command.upgrade(alembic_config(), "head")

    with isolated_engine.connect() as connection:
        rows = dict(connection.execute(text("SELECT label, provider_preset FROM team_llm_configs")).all())
        metadata = connection.execute(text("SELECT inspection_metadata_json::text FROM team_llm_configs LIMIT 1")).scalar_one()

    assert rows == {
        "OpenAI": "openai",
        "OpenRouter": "openrouter",
        "Bedrock": "bedrock_http_gateway",
        "Ollama": "ollama",
        "Custom": "custom_openai_compatible",
    }
    assert metadata == "{}"


@pytest.mark.migration
def test_alembic_backfills_llm_setup_status():
    reset_public_schema()
    command.upgrade(alembic_config(), "a0b1c2d3e4f6")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000150',
                    'Admin User',
                    'llm-setup-admin@example.com',
                    'hash',
                    NULL,
                    NULL,
                    true,
                    'active',
                    false,
                    'complete',
                    true,
                    false,
                    NOW(),
                    NOW(),
                    NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000151',
                    'Clinic Setup',
                    'clinic setup',
                    'active',
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        for config_id, label, model_name in [
            ("00000000-0000-0000-0000-000000000152", "Ready LLM", "gpt-4o-mini"),
            ("00000000-0000-0000-0000-000000000153", "Draft LLM", None),
        ]:
            connection.execute(
                text(
                    """
                    INSERT INTO team_llm_configs (
                        id, team_id, label, provider_preset, adapter_kind, base_url, auth_mode, model_name,
                        available_models_json, inspection_metadata_json, vault_secret_ref, is_active,
                        created_by_user_id, updated_by_user_id, created_at, updated_at
                    )
                    VALUES (
                        :id,
                        '00000000-0000-0000-0000-000000000151',
                        :label,
                        'openai',
                        'openai_chat',
                        'https://api.openai.com/v1',
                        'bearer',
                        :model_name,
                        '[]'::json,
                        '{}'::json,
                        'secret:openscribe/llm/team/1/config/2',
                        true,
                        '00000000-0000-0000-0000-000000000150',
                        '00000000-0000-0000-0000-000000000150',
                        NOW(),
                        NOW()
                    )
                    """
                ),
                {"id": config_id, "label": label, "model_name": model_name},
            )

    command.upgrade(alembic_config(), "head")

    with isolated_engine.connect() as connection:
        rows = dict(connection.execute(text("SELECT label, setup_status FROM team_llm_configs")).all())

    assert rows == {"Ready LLM": "ready", "Draft LLM": "pending_model_selection"}


@pytest.mark.migration
def test_alembic_head_supports_simplified_transcript_ingestion_mode():
    reset_public_schema()
    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO teams (id, name, name_key, status, default_retention_days, created_at, updated_at)
                VALUES (
                    '00000000-0000-0000-0000-000000000030',
                    'Clinic North',
                    'clinic north',
                    'active',
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000031',
                    'Owner User',
                    'owner@example.com',
                    'hash',
                    '00000000-0000-0000-0000-000000000030',
                    'user',
                    false,
                    'active',
                    false,
                    'complete',
                    true,
                    false,
                    NOW(),
                    NOW(),
                    NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO transcripts (
                    id, owner_user_id, team_id, title, current_draft_text_encrypted, ingestion_mode, status,
                    next_live_chunk_sequence_no_applied, retention_days_applied, retention_expires_at, created_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000032',
                    '00000000-0000-0000-0000-000000000031',
                    '00000000-0000-0000-0000-000000000030',
                    'Whole-file transcript',
                    NULL,
                    'whole_file',
                    'recording',
                    1,
                    30,
                    NOW(),
                    NOW()
                )
                """
            )
        )
        ingestion_mode = connection.execute(text("SELECT ingestion_mode::text FROM transcripts")).scalar_one()

    assert ingestion_mode == "whole_file"


@pytest.mark.migration
def test_alembic_head_supports_suspended_user_status():
    reset_public_schema()
    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, full_name, email, password_hash, team_id, team_role, is_system_admin, status,
                    must_change_password, onboarding_state, mfa_required, mfa_enabled, created_at, updated_at, last_login_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000010',
                    'Suspended User',
                    'suspended@example.com',
                    'hash',
                    NULL,
                    NULL,
                    true,
                    'suspended',
                    false,
                    'complete',
                    true,
                    false,
                    NOW(),
                    NOW(),
                    NULL
                )
                """
            )
        )

        status = connection.execute(text("SELECT status::text FROM users WHERE email = 'suspended@example.com'")).scalar_one()

    assert status == "suspended"


@pytest.mark.migration
def test_alembic_head_supports_quick_action_generated_document_type():
    reset_public_schema()
    command.upgrade(alembic_config(), "head")

    isolated_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    with isolated_engine.begin() as connection:
        values = connection.execute(
            text(
                """
                SELECT enumlabel
                FROM pg_enum
                JOIN pg_type ON pg_enum.enumtypid = pg_type.oid
                WHERE pg_type.typname = 'generateddocumentgeneratortype'
                ORDER BY enumsortorder
                """
            )
        ).scalars().all()

    assert "quick_action" in values
