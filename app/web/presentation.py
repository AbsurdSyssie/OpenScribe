import json
import os
from urllib.parse import urlencode
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import AppError
from ..models import (
    DeidentificationAdapterKind,
    DeidentificationAuthMode,
    GeneratedDocument,
    HallucinationCheckStatus,
    LlmAdapterKind,
    LlmConfigSetupStatus,
    LlmProviderPreset,
    PromptTemplate,
    QuickAction,
    SmartPhrase,
    SttAdapterKind,
    SttConfigSetupStatus,
    SttProviderPreset,
    SttSelectionPurpose,
    TeamRole,
    TeamStatus,
    TemplateMode,
    TranscriptIngestionJobKind,
    TranscriptIngestionJobStatus,
    TranscriptIngestionMode,
    User,
    UserStatus,
)
from ..services.llm_presets import LLM_PROVIDER_PRESETS, BEDROCK_HTTP_GATEWAY_REGIONS, get_llm_provider_preset, infer_llm_provider_preset
from ..services.stt_presets import STT_PROVIDER_PRESETS, get_stt_provider_preset, infer_stt_provider_preset, resolve_stt_provider_preset
from ..schemas import (
    ClinicalNlpSelectionDetail,
    DeidentificationInspectResult,
    DeidentificationProviderAssignmentDetail,
    DeidentificationProviderDetail,
    DeidentificationSelectionDetail,
    EMIS_SECTION_KEYS,
    EMIS_SECTION_LABELS,
    GeneratedDocumentDetail,
    GeneratedDocumentPiiEntityDetail,
    GeneratedDocumentRedactionDebugDetail,
    GeneratedDocumentSectionDetail,
    HallucinationCheckSelectionDetail,
    LlmConfigDetail,
    LlmConfigInspectResult,
    LlmSelectionDetail,
    PromptTemplateDetail,
    QuickActionDetail,
    SmartPhraseDetail,
    SttConfigDetail,
    SttInspectResult,
    SttSelectionDetail,
    UserAppPreferencesDetail,
    UserLlmPreferenceDetail,
)
from ..llm_provider_defaults import DEFAULT_BEDROCK_CHAT_REGION, bedrock_region_from_base_url
from ..services.admin import (
    admin_usage_overview as admin_usage_overview_service,
    list_manageable_account_requests as list_manageable_account_requests_service,
    list_manageable_users as list_manageable_users_service,
    list_teams as list_teams_service,
    list_users as list_users_service,
    user_count as user_count_service,
)
from ..services.audit_detection import (
    audit_filter_options as audit_filter_options_service,
    list_security_audit_events as list_security_audit_events_service,
    parse_since as parse_audit_since,
    summarize_security_audit_events as summarize_security_audit_events_service,
)
from ..services.auth_email import email_password_reset_enabled as email_password_reset_enabled_service
from ..services.llm import (
    active_team_llm_selection as active_team_llm_selection_service,
    get_team_hallucination_check_selection as get_team_hallucination_check_selection_service,
    get_team_llm_selection as get_team_llm_selection_service,
    get_llm_config as get_llm_config_service,
    get_user_llm_preference as get_user_llm_preference_service,
    list_selectable_llm_configs as list_selectable_llm_configs_service,
    list_llm_configs as list_llm_configs_service,
    resolve_user_llm as resolve_user_llm_service,
)
from ..services.preferences import get_user_app_preferences as get_user_app_preferences_service
from ..services.deidentification import (
    get_team_clinical_nlp_selection as get_team_clinical_nlp_selection_service,
    get_team_deidentification_selection as get_team_deidentification_selection_service,
    list_selectable_clinical_nlp_providers as list_selectable_clinical_nlp_providers_service,
    list_deidentification_providers as list_deidentification_providers_service,
    list_selectable_deidentification_providers as list_selectable_deidentification_providers_service,
    list_team_deidentification_provider_assignments as list_team_deidentification_provider_assignments_service,
)
from ..services.redaction import (
    redaction_run_text as redaction_run_text_service,
)
from ..services.stt import (
    active_team_stt_selection as active_team_stt_selection_service,
    get_team_stt_selection as get_team_stt_selection_service,
    get_stt_config as get_stt_config_service,
    list_selectable_stt_configs as list_selectable_stt_configs_service,
    list_stt_configs as list_stt_configs_service,
)
from ..services.templates import (
    generated_document_llm_request_payload as generated_document_llm_request_payload_service,
    generated_document_section_text as generated_document_section_text_service,
    generated_document_text as generated_document_text_service,
    list_personal_quick_actions as list_personal_quick_actions_service,
    list_personal_templates as list_personal_templates_service,
    list_team_quick_actions as list_team_quick_actions_service,
    list_team_templates as list_team_templates_service,
)
from ..services.smart_phrases import list_personal_smart_phrases as list_personal_smart_phrases_service
from ..services.default_assets import (
    list_default_quick_actions as list_default_quick_actions_service,
    list_default_templates as list_default_templates_service,
)
from .templates import templates


def stt_config_response(config) -> SttConfigDetail:
    provider_preset = resolve_stt_provider_preset(config.provider_preset, config.adapter_kind, config.base_url)
    provider_display_name = get_stt_provider_preset(provider_preset).display_name
    setup_status = config.setup_status or SttConfigSetupStatus.ready
    setup_status_label = "Setup incomplete" if setup_status == SttConfigSetupStatus.pending_model_selection else None
    return SttConfigDetail(
        id=config.id,
        team_id=config.team_id,
        label=config.label,
        provider_preset=provider_preset,
        provider_display_name=provider_display_name,
        adapter_kind=config.adapter_kind,
        base_url=config.base_url,
        transcribe_path=config.transcribe_path,
        auth_mode=config.auth_mode,
        model_name=config.model_name,
        model_field_name=config.model_field_name,
        available_models_json=list(config.available_models_json or []),
        file_field_name=config.file_field_name,
        language=config.language,
        language_field_name=config.language_field_name,
        response_text_path=config.response_text_path,
        segments_path=config.segments_path,
        segment_text_field=config.segment_text_field,
        segment_start_field=config.segment_start_field,
        segment_end_field=config.segment_end_field,
        segment_speaker_field=config.segment_speaker_field,
        extra_form_fields_json=config.extra_form_fields_json or {},
        credential_status=config.credential_status,
        inspection_metadata_json=config.inspection_metadata_json or {},
        setup_status=setup_status,
        setup_status_label=setup_status_label,
        is_active=config.is_active,
        has_secret=bool(config.vault_secret_ref),
        created_by_user_id=config.created_by_user_id,
        updated_by_user_id=config.updated_by_user_id,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


def stt_selection_response(selection) -> SttSelectionDetail:
    config = selection.config
    available_models_json = list(config.available_models_json or [])
    resolved_model_name = selection.model_name_override or config.model_name
    if available_models_json and resolved_model_name not in available_models_json:
        resolved_model_name = available_models_json[0]
    resolved_language = selection.language_override or config.language
    return SttSelectionDetail(
        id=selection.id,
        team_id=selection.team_id,
        purpose=selection.purpose,
        stt_config_id=selection.stt_config_id,
        selected_by_user_id=selection.selected_by_user_id,
        selected_config_label=config.label,
        selected_config_adapter_kind=config.adapter_kind,
        selected_config_base_url=config.base_url,
        selected_config_transcribe_path=config.transcribe_path,
        model_name_override=selection.model_name_override,
        language_override=selection.language_override,
        resolved_model_name=resolved_model_name,
        resolved_language=resolved_language,
        available_models_json=available_models_json,
        created_at=selection.created_at,
        updated_at=selection.updated_at,
    )


def llm_config_response(config) -> LlmConfigDetail:
    provider_preset = config.provider_preset or infer_llm_provider_preset(config.adapter_kind, config.base_url)
    provider_display_name = get_llm_provider_preset(provider_preset).display_name
    setup_status = config.setup_status or LlmConfigSetupStatus.ready
    setup_status_label = "Setup incomplete" if setup_status == LlmConfigSetupStatus.pending_model_selection else None
    return LlmConfigDetail(
        id=config.id,
        team_id=config.team_id,
        label=config.label,
        provider_preset=provider_preset,
        adapter_kind=config.adapter_kind,
        base_url=config.base_url,
        auth_mode=config.auth_mode,
        model_name=config.model_name,
        available_models_json=list(config.available_models_json or []),
        inspection_metadata_json=config.inspection_metadata_json or {},
        setup_status=setup_status,
        provider_display_name=provider_display_name,
        setup_status_label=setup_status_label,
        is_active=config.is_active,
        has_secret=bool(config.vault_secret_ref),
        created_by_user_id=config.created_by_user_id,
        updated_by_user_id=config.updated_by_user_id,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


def llm_selection_response(selection) -> LlmSelectionDetail:
    config = selection.config
    resolved_model_name = selection.model_name_override or config.model_name
    allowed_models_json = list(selection.allowed_models_json or config.available_models_json or [])
    if resolved_model_name and allowed_models_json and resolved_model_name not in allowed_models_json:
        resolved_model_name = allowed_models_json[0]
    provider_preset = config.provider_preset or infer_llm_provider_preset(config.adapter_kind, config.base_url)
    provider_display_name = get_llm_provider_preset(provider_preset).display_name
    return LlmSelectionDetail(
        id=selection.id,
        team_id=selection.team_id,
        llm_config_id=selection.llm_config_id,
        selected_by_user_id=selection.selected_by_user_id,
        selected_config_label=config.label,
        selected_config_provider_preset=provider_preset,
        selected_config_provider_display_name=provider_display_name,
        selected_config_adapter_kind=config.adapter_kind,
        selected_config_base_url=config.base_url,
        provider_available_models_json=list(config.available_models_json or []),
        allowed_models_json=allowed_models_json,
        model_name_override=selection.model_name_override,
        resolved_model_name=resolved_model_name,
        created_at=selection.created_at,
        updated_at=selection.updated_at,
    )


def hallucination_check_selection_response(selection) -> HallucinationCheckSelectionDetail:
    config = selection.config
    resolved_model_name = selection.model_name_override or config.model_name
    provider_preset = config.provider_preset or infer_llm_provider_preset(config.adapter_kind, config.base_url)
    provider_display_name = get_llm_provider_preset(provider_preset).display_name
    return HallucinationCheckSelectionDetail(
        id=selection.id,
        team_id=selection.team_id,
        llm_config_id=selection.llm_config_id,
        selected_by_user_id=selection.selected_by_user_id,
        selected_config_label=config.label,
        selected_config_provider_preset=provider_preset,
        selected_config_provider_display_name=provider_display_name,
        selected_config_adapter_kind=config.adapter_kind,
        selected_config_base_url=config.base_url,
        provider_available_models_json=list(config.available_models_json or []),
        model_name_override=selection.model_name_override,
        resolved_model_name=resolved_model_name,
        created_at=selection.created_at,
        updated_at=selection.updated_at,
    )


def deidentification_provider_response(provider) -> DeidentificationProviderDetail:
    return DeidentificationProviderDetail(
        id=provider.id,
        label=provider.label,
        adapter_kind=provider.adapter_kind,
        base_url=provider.base_url,
        detect_path=provider.detect_path,
        auth_mode=provider.auth_mode,
        request_text_field=provider.request_text_field,
        request_language_field=provider.request_language_field,
        extra_headers_json=provider.extra_headers_json or {},
        extra_body_json=provider.extra_body_json or {},
        response_entities_path=provider.response_entities_path,
        response_start_field=provider.response_start_field,
        response_end_field=provider.response_end_field,
        response_type_field=provider.response_type_field,
        response_score_field=provider.response_score_field,
        response_model_version_path=provider.response_model_version_path,
        entity_type_map_json=provider.entity_type_map_json or {},
        clinical_detection_enabled=provider.clinical_detection_enabled,
        clinical_detection_allow_unredacted=provider.clinical_detection_allow_unredacted,
        is_active=provider.is_active,
        is_builtin=provider.is_builtin,
        has_secret=bool(provider.vault_secret_ref),
        created_by_user_id=provider.created_by_user_id,
        updated_by_user_id=provider.updated_by_user_id,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


def deidentification_provider_assignment_response(assignment) -> DeidentificationProviderAssignmentDetail:
    return DeidentificationProviderAssignmentDetail(
        id=assignment.id,
        team_id=assignment.team_id,
        provider_id=assignment.provider_id,
        provider_label=assignment.provider.label,
        provider_adapter_kind=assignment.provider.adapter_kind,
        assigned_by_user_id=assignment.assigned_by_user_id,
        created_at=assignment.created_at,
    )


def deidentification_selection_response(selection) -> DeidentificationSelectionDetail:
    provider = selection.provider
    return DeidentificationSelectionDetail(
        id=selection.id,
        team_id=selection.team_id,
        provider_id=selection.provider_id,
        selected_by_user_id=selection.selected_by_user_id,
        selected_provider_label=provider.label,
        selected_provider_adapter_kind=provider.adapter_kind,
        selected_provider_is_builtin=provider.is_builtin,
        created_at=selection.created_at,
        updated_at=selection.updated_at,
    )


def clinical_nlp_selection_response(selection) -> ClinicalNlpSelectionDetail:
    provider = selection.provider
    return ClinicalNlpSelectionDetail(
        id=selection.id,
        team_id=selection.team_id,
        provider_id=selection.provider_id,
        selected_by_user_id=selection.selected_by_user_id,
        selected_provider_label=provider.label,
        selected_provider_adapter_kind=provider.adapter_kind,
        selected_provider_allows_unredacted=provider.clinical_detection_allow_unredacted,
        created_at=selection.created_at,
        updated_at=selection.updated_at,
    )


def user_llm_preference_response(preference, *, resolved_model_name: str | None, allowed_models: list[str]) -> UserLlmPreferenceDetail:
    return UserLlmPreferenceDetail(
        id=preference.id,
        user_id=preference.user_id,
        preferred_model_name=preference.preferred_model_name,
        resolved_model_name=resolved_model_name,
        allowed_models_json=allowed_models,
        created_at=preference.created_at,
        updated_at=preference.updated_at,
    )


def user_app_preferences_response(preference) -> UserAppPreferencesDetail:
    payload = preference.preferences_json or {}
    return UserAppPreferencesDetail(
        id=preference.id,
        user_id=preference.user_id,
        favorite_quick_action_ids=list(payload.get("favorite_quick_action_ids") or []),
        favorite_template_ids=list(payload.get("favorite_template_ids") or []),
        default_quick_action_id=payload.get("default_quick_action_id"),
        default_template_id=payload.get("default_template_id"),
        llm_detail_level=payload.get("llm_detail_level"),
        note_generation_length=payload.get("note_generation_length"),
        preferred_recording_mode=payload.get("preferred_recording_mode"),
        preferred_transcribe_tab=payload.get("preferred_transcribe_tab"),
        created_at=preference.created_at,
        updated_at=preference.updated_at,
    )


def break_glass_recovery_enabled() -> bool:
    email_enabled = email_password_reset_enabled_service()
    if os.getenv("BREAK_GLASS_RECOVERY_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        return False
    return not email_enabled or os.getenv("BREAK_GLASS_ALLOW_WITH_MAIL_ENABLED", "false").lower() in {"1", "true", "yes"}


def _latest_template_version(template: PromptTemplate):
    return max(template.versions, key=lambda version: version.version_no)


def _latest_quick_action_version(quick_action: QuickAction):
    return max(quick_action.versions, key=lambda version: version.version_no)


def _structured_section_prompt_map(version) -> dict[str, str]:
    if version is None or not version.config_json or not isinstance(version.config_json, dict):
        return {}
    sections = version.config_json.get("sections")
    if not isinstance(sections, list):
        return {}
    prompts: dict[str, str] = {}
    for section in sections:
        if not isinstance(section, dict):
            continue
        key = section.get("section_key")
        instruction = section.get("instruction")
        if isinstance(key, str) and isinstance(instruction, str):
            prompts[key] = instruction
    return prompts


def template_response(template: PromptTemplate) -> PromptTemplateDetail:
    latest_version = _latest_template_version(template)
    return PromptTemplateDetail(
        id=template.id,
        scope=template.scope,
        owner_user_id=template.owner_user_id,
        team_id=template.team_id,
        name=template.name,
        description=template.description,
        is_active=template.is_active,
        created_by_user_id=template.created_by_user_id,
        created_at=template.created_at,
        updated_at=template.updated_at,
        latest_version={
            "id": latest_version.id,
            "version_no": latest_version.version_no,
            "mode": latest_version.mode,
            "prompt_text": latest_version.prompt_text,
            "config_json": latest_version.config_json,
            "created_by_user_id": latest_version.created_by_user_id,
            "created_at": latest_version.created_at,
        },
    )


def quick_action_response(quick_action: QuickAction) -> QuickActionDetail:
    latest_version = _latest_quick_action_version(quick_action)
    return QuickActionDetail(
        id=quick_action.id,
        scope=quick_action.scope,
        owner_user_id=quick_action.owner_user_id,
        team_id=quick_action.team_id,
        name=quick_action.name,
        description=quick_action.description,
        is_active=quick_action.is_active,
        created_by_user_id=quick_action.created_by_user_id,
        created_at=quick_action.created_at,
        updated_at=quick_action.updated_at,
        latest_version={
            "id": latest_version.id,
            "version_no": latest_version.version_no,
            "mode": latest_version.mode,
            "prompt_text": latest_version.prompt_text,
            "created_by_user_id": latest_version.created_by_user_id,
            "created_at": latest_version.created_at,
        },
    )


def smart_phrase_response(phrase: SmartPhrase) -> SmartPhraseDetail:
    return SmartPhraseDetail(
        id=phrase.id,
        owner_user_id=phrase.owner_user_id,
        trigger=phrase.trigger,
        expansion_text=phrase.expansion_text,
        description=phrase.description,
        last_used_at=phrase.last_used_at,
        times_used=phrase.times_used or 0,
        created_at=phrase.created_at,
        updated_at=phrase.updated_at,
    )


def _hallucination_check_bucket(status: HallucinationCheckStatus | str | None) -> str:
    value = status.value if isinstance(status, HallucinationCheckStatus) else status
    if value in {HallucinationCheckStatus.checked_unchanged.value, HallucinationCheckStatus.checked_corrected.value}:
        return "checked"
    if value == HallucinationCheckStatus.not_applicable.value or value is None:
        return "not_applicable"
    return "unchecked"


def generated_document_response(db: Session, document: GeneratedDocument, *, actor: User | None = None) -> GeneratedDocumentDetail:
    payload = GeneratedDocumentDetail.model_validate(document, from_attributes=True).model_dump()
    payload["follow_up_prompt_text"] = generated_document_text_service(db, document=document, field="follow_up_prompt_text") or None
    payload["original_output_text"] = generated_document_text_service(db, document=document, field="original_output_text_encrypted")
    payload["edited_output_text"] = generated_document_text_service(db, document=document, field="edited_output_text_encrypted")
    payload["llm_request_payload_json"] = generated_document_llm_request_payload_service(db, document=document)
    payload["structured_section_definitions_json"] = document.structured_section_definitions_json if isinstance(document.structured_section_definitions_json, dict) else None
    payload["hallucination_check_bucket"] = _hallucination_check_bucket(document.hallucination_check_status)
    payload["hallucination_check_debug_json"] = None
    if (
        actor is not None
        and actor.id == document.owner_user_id
        and os.getenv("HALLUCINATION_CHECK_DEBUG_UI", "").lower() in {"1", "true", "yes"}
    ):
        from ..services.templates import generated_document_hallucination_check_debug

        payload["hallucination_check_debug_json"] = generated_document_hallucination_check_debug(db, document=document)
    payload["sections"] = [
        GeneratedDocumentSectionDetail.model_validate(
            {
                "id": section.id,
                "section_key": section.section_key,
                "section_label": section.section_label,
                "section_order": section.section_order,
                "original_text_encrypted": generated_document_section_text_service(db, section=section, field="original_text_encrypted"),
                "edited_text_encrypted": generated_document_section_text_service(db, section=section, field="edited_text_encrypted"),
                "is_edited": section.is_edited,
            }
        )
        for section in getattr(document, "sections", [])
    ]
    redaction_run = document.redaction_run
    payload["pii_entities"] = [
        GeneratedDocumentPiiEntityDetail(
            entity_type=entity.entity_type,
            placeholder=entity.placeholder,
            occurrence_count=entity.occurrence_count,
            has_value=True,
        ).model_dump()
        for entity in sorted(getattr(redaction_run, "entities", []) or [], key=lambda item: item.entity_order)
    ] if redaction_run is not None else []
    return GeneratedDocumentDetail.model_validate(payload)


def generated_document_redaction_debug_response(db: Session, document: GeneratedDocument) -> GeneratedDocumentRedactionDebugDetail:
    if document.redaction_run is None:
        raise AppError(404, "not_found", "No redaction run is linked to this generated document")
    redaction_run = document.redaction_run
    entities = sorted(redaction_run.entities, key=lambda entity: entity.entity_order)
    return GeneratedDocumentRedactionDebugDetail(
        generated_document_id=document.id,
        redaction_run_id=redaction_run.id,
        transcript_version_id=redaction_run.transcript_version_id,
        status=redaction_run.status.value,
        api_provider=redaction_run.api_provider,
        api_model_or_version=redaction_run.api_model_or_version,
        entity_count=redaction_run.entity_count,
        mapping_hash=redaction_run.mapping_hash,
        redacted_text=redaction_run_text_service(db, run=redaction_run) or "",
        failed_provider_output_redacted_text=generated_document_text_service(db, document=document, field="failed_provider_output_redacted_encrypted") or None,
        entities=[
            {
                "entity_order": entity.entity_order,
                "entity_type": entity.entity_type,
                "placeholder": entity.placeholder,
                "occurrence_count": entity.occurrence_count,
            }
            for entity in entities
        ],
    )


def stt_form_defaults(config, inspection: SttInspectResult | None) -> dict[str, object]:
    def default_credential_action(adapter_kind: SttAdapterKind) -> str:
        return "replace" if adapter_kind is SttAdapterKind.openai_cloud else "keep"

    if inspection is not None:
        return {
            "config_id": "",
            "label": "",
            "provider_preset": infer_stt_provider_preset(inspection.adapter_kind, inspection.base_url),
            "provider_display_name": get_stt_provider_preset(infer_stt_provider_preset(inspection.adapter_kind, inspection.base_url)).display_name,
            "adapter_kind": inspection.adapter_kind.value,
            "base_url": inspection.base_url,
            "openapi_path": inspection.openapi_path or "/openapi.json",
            "transcribe_path": inspection.transcribe_path,
            "model_name": inspection.model_name or "",
            "model_field_name": inspection.model_field_name or "",
            "available_models": inspection.available_models,
            "available_model_options": [option.model_dump(mode="json") for option in inspection.available_model_options],
            "file_field_name": inspection.file_field_name,
            "language": inspection.language or "",
            "language_field_name": inspection.language_field_name or "",
            "response_text_path": inspection.response_text_path,
            "segments_path": inspection.segments_path or "",
            "segment_text_field": inspection.segment_text_field or "",
            "segment_start_field": inspection.segment_start_field or "",
            "segment_end_field": inspection.segment_end_field or "",
            "segment_speaker_field": inspection.segment_speaker_field or "",
            "extra_form_fields_json": json.dumps(inspection.extra_form_fields_json) if inspection.extra_form_fields_json else "",
            "is_active": True,
            "credential_action": default_credential_action(inspection.adapter_kind),
            "setup_status": "",
            "is_setup_incomplete": False,
            "stt_provider_presets": list(STT_PROVIDER_PRESETS.values()),
        }
    if config is not None:
        provider_preset = resolve_stt_provider_preset(config.provider_preset, config.adapter_kind, config.base_url)
        return {
            "config_id": str(config.id),
            "label": config.label,
            "provider_preset": provider_preset,
            "provider_display_name": get_stt_provider_preset(provider_preset).display_name,
            "adapter_kind": config.adapter_kind.value,
            "base_url": config.base_url,
            "openapi_path": "/openapi.json" if config.adapter_kind is SttAdapterKind.generic_rest else "",
            "transcribe_path": config.transcribe_path,
            "model_name": config.model_name or "",
            "model_field_name": config.model_field_name or "",
            "available_models": list(config.available_models_json or []),
            "available_model_options": [
                {"id": model, "source": "saved", "label": f"{model} (saved)"}
                for model in (config.available_models_json or [])
            ],
            "file_field_name": config.file_field_name,
            "language": config.language or "",
            "language_field_name": config.language_field_name or "",
            "response_text_path": config.response_text_path,
            "segments_path": config.segments_path or "",
            "segment_text_field": config.segment_text_field or "",
            "segment_start_field": config.segment_start_field or "",
            "segment_end_field": config.segment_end_field or "",
            "segment_speaker_field": config.segment_speaker_field or "",
            "extra_form_fields_json": json.dumps(config.extra_form_fields_json) if config.extra_form_fields_json else "",
            "is_active": config.is_active,
            "credential_action": "keep",
            "setup_status": config.setup_status,
            "is_setup_incomplete": config.setup_status == SttConfigSetupStatus.pending_model_selection,
            "stt_provider_presets": list(STT_PROVIDER_PRESETS.values()),
        }
    return {
        "config_id": "",
        "label": "",
        "provider_preset": SttProviderPreset.openai.value,
        "provider_display_name": get_stt_provider_preset(SttProviderPreset.openai.value).display_name,
        "adapter_kind": SttAdapterKind.generic_rest.value,
        "base_url": "",
        "openapi_path": "/openapi.json",
        "transcribe_path": "/v1/audio/transcriptions",
        "model_name": "",
        "model_field_name": "model",
        "available_models": [],
        "available_model_options": [],
        "file_field_name": "file",
        "language": "",
        "language_field_name": "language",
        "response_text_path": "text",
        "segments_path": "",
        "segment_text_field": "",
        "segment_start_field": "",
        "segment_end_field": "",
        "segment_speaker_field": "",
        "extra_form_fields_json": "",
        "is_active": True,
        "credential_action": default_credential_action(SttAdapterKind.generic_rest),
        "setup_status": "",
        "is_setup_incomplete": False,
        "stt_provider_presets": list(STT_PROVIDER_PRESETS.values()),
    }


def llm_form_defaults(config, inspection: LlmConfigInspectResult | None) -> dict[str, object]:
    def default_credential_action(adapter_kind: LlmAdapterKind) -> str:
        return "keep" if adapter_kind is LlmAdapterKind.ollama_chat else "replace"

    if inspection is not None:
        bedrock_region = bedrock_region_from_base_url(inspection.base_url) if inspection.adapter_kind is LlmAdapterKind.bedrock_chat else ""
        return {
            "config_id": "",
            "label": "",
            "provider_preset": inspection.provider_preset,
            "provider_display_name": inspection.provider_display_name,
            "adapter_kind": inspection.adapter_kind.value,
            "base_url": inspection.base_url,
            "bedrock_region": bedrock_region or (DEFAULT_BEDROCK_CHAT_REGION if inspection.adapter_kind is LlmAdapterKind.bedrock_chat else ""),
            "model_name": inspection.model_name or "",
            "available_models": inspection.available_models,
            "available_model_options": [option.model_dump(mode="json") for option in inspection.available_model_options],
            "is_active": True,
            "credential_action": default_credential_action(inspection.adapter_kind),
            "setup_status": "",
            "has_secret": False,
            "is_setup_incomplete": False,
            "show_credential_step": True,
            "show_model_step": False,
            "can_finalize": False,
            "llm_provider_presets": list(LLM_PROVIDER_PRESETS.values()),
            "bedrock_regions": BEDROCK_HTTP_GATEWAY_REGIONS,
        }
    if config is not None:
        bedrock_region = bedrock_region_from_base_url(config.base_url) if config.adapter_kind is LlmAdapterKind.bedrock_chat else ""
        provider_preset = config.provider_preset or infer_llm_provider_preset(config.adapter_kind, config.base_url)
        setup_status = config.setup_status or LlmConfigSetupStatus.ready
        return {
            "config_id": str(config.id),
            "label": config.label,
            "provider_preset": provider_preset,
            "provider_display_name": LLM_PROVIDER_PRESETS.get(
                provider_preset,
                LLM_PROVIDER_PRESETS[LlmProviderPreset.custom_openai_compatible.value],
            ).display_name,
            "adapter_kind": config.adapter_kind.value,
            "base_url": config.base_url,
            "bedrock_region": bedrock_region or (DEFAULT_BEDROCK_CHAT_REGION if config.adapter_kind is LlmAdapterKind.bedrock_chat else ""),
            "model_name": config.model_name or "",
            "available_models": list(config.available_models_json or []),
            "available_model_options": [
                {"id": model, "source": "saved", "label": f"{model} (saved)"}
                for model in (config.available_models_json or [])
            ],
            "is_active": config.is_active,
            "credential_action": "keep",
            "setup_status": setup_status.value if hasattr(setup_status, "value") else str(setup_status),
            "has_secret": bool(config.vault_secret_ref),
            "is_setup_incomplete": setup_status == LlmConfigSetupStatus.pending_model_selection,
            "show_credential_step": False,
            "show_model_step": setup_status == LlmConfigSetupStatus.pending_model_selection,
            "can_finalize": setup_status == LlmConfigSetupStatus.pending_model_selection,
            "llm_provider_presets": list(LLM_PROVIDER_PRESETS.values()),
            "bedrock_regions": BEDROCK_HTTP_GATEWAY_REGIONS,
        }
    return {
        "config_id": "",
        "label": "",
        "provider_preset": LlmProviderPreset.openai.value,
        "provider_display_name": LLM_PROVIDER_PRESETS[LlmProviderPreset.openai.value].display_name,
        "adapter_kind": LlmAdapterKind.openai_chat.value,
        "base_url": "https://api.openai.com/v1",
        "bedrock_region": "",
        "model_name": "",
        "available_models": [],
        "available_model_options": [],
        "is_active": True,
        "credential_action": default_credential_action(LlmAdapterKind.openai_chat),
        "setup_status": "",
        "has_secret": False,
        "is_setup_incomplete": False,
        "show_credential_step": True,
        "show_model_step": False,
        "can_finalize": False,
        "llm_provider_presets": list(LLM_PROVIDER_PRESETS.values()),
        "bedrock_regions": BEDROCK_HTTP_GATEWAY_REGIONS,
    }


def deidentification_form_defaults(provider) -> dict[str, object]:
    if provider is not None:
        return {
            "provider_id": str(provider.id),
            "label": provider.label,
            "adapter_kind": provider.adapter_kind.value,
            "base_url": provider.base_url,
            "detect_path": provider.detect_path,
            "openapi_path": "",
            "auth_mode": provider.auth_mode.value,
            "request_text_field": provider.request_text_field,
            "request_language_field": provider.request_language_field or "",
            "extra_headers_json": json.dumps(provider.extra_headers_json) if provider.extra_headers_json else "",
            "extra_body_json": json.dumps(provider.extra_body_json) if provider.extra_body_json else "",
            "response_entities_path": provider.response_entities_path,
            "response_start_field": provider.response_start_field,
            "response_end_field": provider.response_end_field,
            "response_type_field": provider.response_type_field,
            "response_score_field": provider.response_score_field or "",
            "response_model_version_path": provider.response_model_version_path or "",
            "entity_type_map_json": json.dumps(provider.entity_type_map_json) if provider.entity_type_map_json else "",
            "clinical_detection_enabled": provider.clinical_detection_enabled,
            "clinical_detection_allow_unredacted": provider.clinical_detection_allow_unredacted,
            "sample_text": "Jane Smith attended on 22 April 2026.",
            "is_active": provider.is_active,
            "preserved_bearer_token": "",
            "candidate_paths": [],
        }
    return {
        "provider_id": "",
        "label": "",
        "adapter_kind": DeidentificationAdapterKind.generic_rest.value,
        "base_url": "",
        "detect_path": "/detect",
        "openapi_path": "",
        "auth_mode": DeidentificationAuthMode.none.value,
        "request_text_field": "text",
        "request_language_field": "",
        "extra_headers_json": "",
        "extra_body_json": "",
        "response_entities_path": "entities",
        "response_start_field": "start",
        "response_end_field": "end",
        "response_type_field": "entity_type",
        "response_score_field": "",
        "response_model_version_path": "",
        "entity_type_map_json": "",
        "clinical_detection_enabled": False,
        "clinical_detection_allow_unredacted": False,
        "sample_text": "Jane Smith attended on 22 April 2026.",
        "is_active": True,
        "preserved_bearer_token": "",
        "candidate_paths": [],
    }


def render_auth_page(
    request,
    db: Session,
    message: str | None = None,
    message_kind: str = "error",
    status_code: int = 200,
):
    context = {
        "request": request,
        "bootstrap_allowed": user_count_service(db) == 0,
        "password_reset_email_enabled": email_password_reset_enabled_service(),
        "message": message,
        "message_kind": message_kind,
    }
    return templates.TemplateResponse(request, "login.html", context, status_code=status_code)


def render_request_access_page(
    request,
    *,
    message: str | None = None,
    message_kind: str = "success",
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request,
        "request_access.html",
        {"request": request, "message": message, "message_kind": message_kind},
        status_code=status_code,
    )


def render_admin(
    request,
    db: Session,
    *,
    current_user: User,
    selected_team_id: str | None = None,
    selected_stt_config_id: str | None = None,
    selected_llm_config_id: str | None = None,
    selected_deidentification_provider_id: str | None = None,
    selected_default_template_id: str | None = None,
    selected_default_quick_action_id: str | None = None,
    stt_inspection: SttInspectResult | None = None,
    stt_form_override: dict[str, object] | None = None,
    stt_test_result: dict[str, object] | None = None,
    llm_inspection: LlmConfigInspectResult | None = None,
    llm_form_override: dict[str, object] | None = None,
    deidentification_inspection: DeidentificationInspectResult | None = None,
    deidentification_form_override: dict[str, object] | None = None,
    message: str | None = None,
    message_kind: str = "success",
    recovery_temporary_password: str | None = None,
    status_code: int = 200,
    active_admin_tab: str | None = None,
    active_provider_tab: str | None = None,
    admin_page_route: str = "/admin",
    admin_return_view: str = "",
    template_name: str | None = None,
    extra_admin_tabs: set[str] | None = None,
    workspace_team_tab: str | None = None,
):
    workspace_team_tabs = {"overview", "members", "provider-policy", "stt", "llm", "deidentification", "defaults", "usage", "security", "danger"}
    if admin_return_view == "workspace" and workspace_team_tab is None and active_admin_tab in workspace_team_tabs:
        workspace_team_tab = active_admin_tab
    selected_uuid = UUID(selected_team_id) if selected_team_id else None
    stt_configs = list_stt_configs_service(db, current_user, team_id=selected_uuid) if selected_uuid else []
    edit_stt_config = next((config for config in stt_configs if str(config.id) == selected_stt_config_id), None)
    if edit_stt_config is None and selected_uuid and selected_stt_config_id and selected_stt_config_id != "new":
        try:
            edit_stt_config = get_stt_config_service(db, current_user, config_id=UUID(selected_stt_config_id), team_id=selected_uuid)
        except (ValueError, AppError):
            edit_stt_config = None
    stt_selection = get_team_stt_selection_service(db, current_user, team_id=selected_uuid) if selected_uuid else None
    stt_dictation_selection = (
        get_team_stt_selection_service(
            db,
            current_user,
            team_id=selected_uuid,
            purpose=SttSelectionPurpose.post_consultation_dictation,
        )
        if selected_uuid
        else None
    )
    llm_configs = list_llm_configs_service(db, current_user, team_id=selected_uuid) if selected_uuid else []
    edit_llm_config = next((config for config in llm_configs if str(config.id) == selected_llm_config_id), None)
    if edit_llm_config is None and selected_uuid and selected_llm_config_id and selected_llm_config_id != "new":
        try:
            edit_llm_config = get_llm_config_service(db, current_user, config_id=UUID(selected_llm_config_id), team_id=selected_uuid)
        except (ValueError, AppError):
            edit_llm_config = None
    llm_selection = get_team_llm_selection_service(db, current_user, team_id=selected_uuid) if selected_uuid else None
    hallucination_check_selection = get_team_hallucination_check_selection_service(db, current_user, team_id=selected_uuid) if selected_uuid else None
    deidentification_providers = list_deidentification_providers_service(db, current_user)
    edit_deidentification_provider = next(
        (provider for provider in deidentification_providers if str(provider.id) == selected_deidentification_provider_id),
        None,
    )
    deidentification_assignments = (
        list_team_deidentification_provider_assignments_service(db, current_user, team_id=selected_uuid) if selected_uuid else []
    )
    deidentification_selection = get_team_deidentification_selection_service(db, current_user, team_id=selected_uuid) if selected_uuid else None
    clinical_nlp_selection = get_team_clinical_nlp_selection_service(db, current_user, team_id=selected_uuid) if selected_uuid else None
    default_templates = list_default_templates_service(db, current_user)
    default_quick_actions = list_default_quick_actions_service(db, current_user)
    selected_default_template = next((template for template in default_templates if str(template.id) == selected_default_template_id), None)
    selected_default_quick_action = next((quick_action for quick_action in default_quick_actions if str(quick_action.id) == selected_default_quick_action_id), None)
    default_template_latest_version = _latest_template_version(selected_default_template) if selected_default_template is not None else None
    default_quick_action_latest_version = _latest_quick_action_version(selected_default_quick_action) if selected_default_quick_action is not None else None
    available_admin_tabs = {"providers", "directory", "requests", "usage", "defaults", "audit"}
    if extra_admin_tabs:
        available_admin_tabs = available_admin_tabs | extra_admin_tabs
    resolved_admin_tab = active_admin_tab if active_admin_tab in available_admin_tabs else "providers"
    available_provider_tabs = {"stt", "llm", "deidentification"}
    if active_provider_tab in available_provider_tabs:
        resolved_provider_tab = active_provider_tab
    elif selected_llm_config_id or llm_inspection:
        resolved_provider_tab = "llm"
    elif selected_deidentification_provider_id or deidentification_inspection:
        resolved_provider_tab = "deidentification"
    else:
        resolved_provider_tab = "stt"
    usage_context = {
        "usage_scope_team": None,
        "usage_kpi_cards": [],
        "usage_window_summaries": [],
        "usage_trend_points": [],
        "usage_comparison_trend_points": [],
        "usage_range_key": "30d",
        "usage_range_label": "Last 30 days",
        "usage_range_bucket": "day",
        "usage_has_comparison": True,
        "usage_has_activity": False,
        "usage_team_rows": [],
        "usage_user_rows": [],
        "usage_provider_rows": [],
        "usage_generator_rows": [],
        "usage_ingestion_rows": [],
        "usage_failure_rows": [],
    }
    if resolved_admin_tab in {"usage", "failures"} or workspace_team_tab == "usage":
        usage_context = admin_usage_overview_service(
            db,
            team_id=selected_uuid,
            range_key=request.query_params.get("range", "30d"),
        )
    audit_context = {
        "audit_since": request.query_params.get("audit_since", "24h"),
        "audit_action_filter": request.query_params.get("audit_action", ""),
        "audit_category_filter": request.query_params.get("audit_category", ""),
        "audit_outcome_filter": request.query_params.get("audit_outcome", ""),
        "audit_request_ip_filter": request.query_params.get("audit_request_ip", ""),
        "audit_team_id_filter": request.query_params.get("audit_team_id", ""),
        "audit_actor_user_id_filter": request.query_params.get("audit_actor_user_id", ""),
        "audit_report": {"event_count": 0, "action_counts": {}, "category_counts": {}, "outcome_counts": {}, "signals": []},
        "audit_events": [],
        "audit_filter_options": {"actions": [], "categories": [], "outcomes": [], "request_ips": []},
    }
    if resolved_admin_tab == "audit":
        audit_since_value = audit_context["audit_since"]
        try:
            audit_since = parse_audit_since(audit_since_value)
        except ValueError:
            audit_since = parse_audit_since("24h")
            audit_context["audit_since"] = "24h"

        def query_uuid(name: str) -> UUID | None:
            value = request.query_params.get(name)
            if not value:
                return None
            try:
                return UUID(value)
            except ValueError:
                return None

        try:
            audit_limit = int(request.query_params.get("audit_limit", "100"))
        except ValueError:
            audit_limit = 100
        audit_report = summarize_security_audit_events_service(db, since=audit_since)
        audit_report = {
            **audit_report,
            "signals": [
                {**signal, "key": "subject_hash_present", "display_key": "subject_hash_present"}
                if signal.get("signal") == "auth_failure_burst_by_subject"
                else signal
                for signal in audit_report["signals"]
            ],
        }
        audit_context.update(
            {
                "audit_report": audit_report,
                "audit_events": list_security_audit_events_service(
                    db,
                    since=audit_since,
                    limit=audit_limit,
                    action=request.query_params.get("audit_action") or None,
                    category=request.query_params.get("audit_category") or None,
                    outcome=request.query_params.get("audit_outcome") or None,
                    request_ip=request.query_params.get("audit_request_ip") or None,
                    team_id=query_uuid("audit_team_id"),
                    actor_user_id=query_uuid("audit_actor_user_id"),
                ),
                "audit_filter_options": audit_filter_options_service(
                    db,
                    since=audit_since,
                    limit=audit_limit,
                ),
            }
        )
    email_recovery_enabled = email_password_reset_enabled_service()
    teams = list_teams_service(db)
    users = list_users_service(db)
    selected_team = next((team for team in teams if str(team.id) == selected_team_id), None)
    selected_team_users = [
        user
        for user in users
        if selected_team is not None and user.team_id == selected_team.id and not user.is_system_admin
    ]
    context = {
        "request": request,
        "current_user": current_user,
        "teams": teams,
        "users": users,
        "selected_team": selected_team,
        "selected_team_users": selected_team_users,
        "selected_team_id": selected_team_id,
        "selected_stt_config_id": selected_stt_config_id,
        "selected_llm_config_id": selected_llm_config_id,
        "selected_deidentification_provider_id": selected_deidentification_provider_id,
        "selected_default_template_id": selected_default_template_id,
        "selected_default_quick_action_id": selected_default_quick_action_id,
        "stt_configs": stt_configs,
        "stt_config": edit_stt_config,
        "stt_selection": stt_selection,
        "stt_dictation_selection": stt_dictation_selection,
        "stt_inspection": stt_inspection,
        "stt_form": stt_form_override or stt_form_defaults(edit_stt_config, None),
        "stt_test_result": stt_test_result,
        "selectable_stt_configs": list_selectable_stt_configs_service(db, current_user, team_id=selected_uuid) if selected_uuid else [],
        "llm_configs": llm_configs,
        "llm_config": edit_llm_config,
        "llm_selection": llm_selection,
        "hallucination_check_selection": hallucination_check_selection,
        "llm_inspection": llm_inspection,
        "llm_form": llm_form_override or llm_form_defaults(edit_llm_config, None),
        "selectable_llm_configs": list_selectable_llm_configs_service(db, current_user, team_id=selected_uuid) if selected_uuid else [],
        "deidentification_providers": deidentification_providers,
        "deidentification_provider": edit_deidentification_provider,
        "deidentification_inspection": deidentification_inspection,
        "deidentification_form": deidentification_form_override or deidentification_form_defaults(edit_deidentification_provider),
        "deidentification_assignments": deidentification_assignments,
        "assigned_deidentification_provider_ids": {assignment.provider_id for assignment in deidentification_assignments},
        "deidentification_selection": deidentification_selection,
        "clinical_nlp_selection": clinical_nlp_selection,
        "selectable_deidentification_providers": (
            list_selectable_deidentification_providers_service(db, current_user, team_id=selected_uuid) if selected_uuid else []
        ),
        "selectable_clinical_nlp_providers": (
            list_selectable_clinical_nlp_providers_service(db, current_user, team_id=selected_uuid) if selected_uuid else []
        ),
        "default_templates": default_templates,
        "default_template": selected_default_template,
        "default_template_latest_version": default_template_latest_version,
        "default_template_section_prompts": _structured_section_prompt_map(default_template_latest_version),
        "default_quick_actions": default_quick_actions,
        "default_quick_action": selected_default_quick_action,
        "default_quick_action_latest_version": default_quick_action_latest_version,
        "template_editor_scope": "default" if template_name == "template_editor.html" else None,
        "emis_sections": [{"key": key, "label": EMIS_SECTION_LABELS[key]} for key in EMIS_SECTION_KEYS],
        "account_requests": list_manageable_account_requests_service(db, current_user),
        "team_statuses": list(TeamStatus),
        "team_roles": list(TeamRole),
        "user_statuses": list(UserStatus),
        "active_admin_tab": resolved_admin_tab,
        "active_provider_tab": resolved_provider_tab,
        "admin_page_route": admin_page_route,
        "admin_return_view": admin_return_view,
        "workspace_team_tab": workspace_team_tab,
        "message": message,
        "message_kind": message_kind,
        "recovery_temporary_password": recovery_temporary_password,
        "email_recovery_enabled": email_recovery_enabled,
        "break_glass_recovery_enabled": break_glass_recovery_enabled(),
        **usage_context,
        **audit_context,
    }
    resolved_template_name = template_name or "admin.html"
    return templates.TemplateResponse(request, resolved_template_name, context, status_code=status_code)


def admin_page_route_from_return_view(return_view: str | None) -> str:
    if return_view == "workspace":
        return "/admin"
    if return_view == "admin2":
        return "/admin2"
    return "/admin-restyled" if return_view == "restyled" else "/legacy-admin"


def admin_return_view_value(return_view: str | None) -> str:
    if return_view == "workspace":
        return "workspace"
    if return_view == "admin2":
        return "admin2"
    return "restyled" if return_view == "restyled" else "legacy"


def admin_redirect_url(
    *,
    return_view: str | None,
    return_tab: str | None = None,
    team_id: str | None = None,
    stt_config_id: str | None = None,
    llm_config_id: str | None = None,
    deidentification_provider_id: str | None = None,
) -> str:
    base = admin_page_route_from_return_view(return_view)
    params: dict[str, str] = {}
    if team_id:
        params["team_id"] = team_id
    if stt_config_id:
        params["stt_config_id"] = stt_config_id
    if llm_config_id:
        params["llm_config_id"] = llm_config_id
    if deidentification_provider_id:
        params["deidentification_provider_id"] = deidentification_provider_id
    if return_tab:
        if return_view == "workspace" and return_tab in {"overview", "members", "provider-policy", "stt", "llm", "deidentification", "defaults", "usage", "security", "danger"}:
            params["team_tab"] = return_tab
        else:
            params["tab"] = return_tab
    return f"{base}?{urlencode(params)}" if params else base


def render_home(
    request,
    db: Session,
    *,
    current_user: User,
    selected_team_template_id: str | None = None,
    selected_personal_template_id: str | None = None,
    selected_team_quick_action_id: str | None = None,
    selected_personal_quick_action_id: str | None = None,
    message: str | None = None,
    message_kind: str = "success",
    recovery_temporary_password: str | None = None,
    queued_transcript_id: str | None = None,
    active_home_tab: str | None = None,
    active_home_modal: str | None = None,
    status_code: int = 200,
    template_name: str = "home.html",
    home_page_route: str = "/home",
    home_return_view: str = "",
    transcribe_return_tab: str | None = None,
    template_editor_scope: str | None = None,
    home_style_variant: str = "",
):
    is_manager = current_user.is_system_admin or current_user.team_role is TeamRole.leader
    stt_selection = None
    stt_dictation_selection = None
    if current_user.team_id is not None:
        try:
            stt_selection = active_team_stt_selection_service(db, team_id=current_user.team_id)
        except AppError:
            stt_selection = get_team_stt_selection_service(db, current_user) if is_manager else None
        try:
            stt_dictation_selection = active_team_stt_selection_service(
                db,
                team_id=current_user.team_id,
                purpose=SttSelectionPurpose.post_consultation_dictation,
            )
        except AppError:
            stt_dictation_selection = (
                get_team_stt_selection_service(
                    db,
                    current_user,
                    purpose=SttSelectionPurpose.post_consultation_dictation,
                )
                if is_manager
                else None
            )
    selectable_stt_configs = list_selectable_stt_configs_service(db, current_user) if is_manager else []
    llm_selection = None
    if current_user.team_id is not None:
        try:
            llm_selection = active_team_llm_selection_service(db, team_id=current_user.team_id)
        except AppError:
            llm_selection = get_team_llm_selection_service(db, current_user) if is_manager else None
    selectable_llm_configs = list_selectable_llm_configs_service(db, current_user) if is_manager else []
    deidentification_selection = None
    if current_user.team_id is not None:
        try:
            deidentification_selection = get_team_deidentification_selection_service(db, current_user)
        except AppError:
            deidentification_selection = None
    selectable_deidentification_providers = list_selectable_deidentification_providers_service(db, current_user) if is_manager else []
    clinical_nlp_selection = None
    if current_user.team_id is not None:
        try:
            clinical_nlp_selection = get_team_clinical_nlp_selection_service(db, current_user)
        except AppError:
            clinical_nlp_selection = None
    selectable_clinical_nlp_providers = list_selectable_clinical_nlp_providers_service(db, current_user) if is_manager else []
    user_llm_preference = None
    resolved_user_llm_model = None
    user_app_preferences_json = {}
    if not current_user.is_system_admin and current_user.team_id is not None:
        try:
            _, _, resolved_user_llm_model, user_llm_preference = resolve_user_llm_service(db, current_user)
        except AppError:
            user_llm_preference = get_user_llm_preference_service(db, current_user)
        user_app_preference = get_user_app_preferences_service(db, current_user)
        if user_app_preference is not None and isinstance(user_app_preference.preferences_json, dict):
            user_app_preferences_json = user_app_preference.preferences_json
    team_leader_email = None
    if current_user.team_id is not None:
        team_leader_email = db.scalar(
            select(User.email)
            .where(
                User.team_id == current_user.team_id,
                User.team_role == TeamRole.leader,
                User.is_system_admin.is_(False),
                User.status == UserStatus.active,
            )
            .order_by(User.created_at.asc())
        )
    team_templates = list_team_templates_service(db, current_user) if is_manager else []
    personal_templates = list_personal_templates_service(db, current_user) if not current_user.is_system_admin and current_user.team_id is not None else []
    team_quick_actions = list_team_quick_actions_service(db, current_user) if is_manager else []
    personal_quick_actions = list_personal_quick_actions_service(db, current_user) if not current_user.is_system_admin and current_user.team_id is not None else []
    personal_smart_phrases = list_personal_smart_phrases_service(db, current_user) if not current_user.is_system_admin and current_user.team_id is not None else []
    selected_team_template = next((template for template in team_templates if str(template.id) == selected_team_template_id), None)
    selected_personal_template = next((template for template in personal_templates if str(template.id) == selected_personal_template_id), None)
    selected_team_quick_action = next((quick_action for quick_action in team_quick_actions if str(quick_action.id) == selected_team_quick_action_id), None)
    selected_personal_quick_action = next((quick_action for quick_action in personal_quick_actions if str(quick_action.id) == selected_personal_quick_action_id), None)
    team_template_latest_version = _latest_template_version(selected_team_template) if selected_team_template is not None else None
    personal_template_latest_version = _latest_template_version(selected_personal_template) if selected_personal_template is not None else None
    team_quick_action_latest_version = _latest_quick_action_version(selected_team_quick_action) if selected_team_quick_action is not None else None
    personal_quick_action_latest_version = _latest_quick_action_version(selected_personal_quick_action) if selected_personal_quick_action is not None else None
    available_home_tabs = ["overview"]
    if not current_user.is_system_admin and current_user.team_id is not None:
        available_home_tabs.extend(["templates", "quick-actions", "smart-phrases"])
    if is_manager:
        available_home_tabs.extend(["ai-services", "team-management", "account-requests"])

    if active_home_tab in available_home_tabs:
        resolved_home_tab = active_home_tab
    elif selected_team_template or selected_personal_template:
        resolved_home_tab = "templates" if "templates" in available_home_tabs else "overview"
    elif selected_team_quick_action or selected_personal_quick_action:
        resolved_home_tab = "quick-actions" if "quick-actions" in available_home_tabs else "overview"
    else:
        resolved_home_tab = "overview"

    allowed_home_modals = {
        "personal-template",
        "team-template",
        "personal-quick-action",
        "team-quick-action",
        "stt-settings",
        "llm-settings",
        "deidentification-settings",
        "clinical-nlp-settings",
    }
    resolved_home_modal = active_home_modal if active_home_modal in allowed_home_modals else None
    email_recovery_enabled = email_password_reset_enabled_service()

    context = {
        "request": request,
        "current_user": current_user,
        "is_manager": is_manager,
        "manageable_users": list_manageable_users_service(db, current_user) if is_manager else [],
        "account_requests": list_manageable_account_requests_service(db, current_user) if is_manager else [],
        "stt_selection": stt_selection,
        "stt_dictation_selection": stt_dictation_selection,
        "selectable_stt_configs": selectable_stt_configs,
        "llm_selection": llm_selection,
        "selectable_llm_configs": selectable_llm_configs,
        "deidentification_selection": deidentification_selection,
        "selectable_deidentification_providers": selectable_deidentification_providers,
        "clinical_nlp_selection": clinical_nlp_selection,
        "selectable_clinical_nlp_providers": selectable_clinical_nlp_providers,
        "user_llm_preference": user_llm_preference,
        "user_app_preferences_json": user_app_preferences_json,
        "resolved_user_llm_model": resolved_user_llm_model,
        "team_leader_email": team_leader_email,
        "team_templates": team_templates,
        "personal_templates": personal_templates,
        "team_quick_actions": team_quick_actions,
        "personal_quick_actions": personal_quick_actions,
        "personal_smart_phrases": personal_smart_phrases,
        "selected_team_template_id": selected_team_template_id,
        "selected_personal_template_id": selected_personal_template_id,
        "selected_team_quick_action_id": selected_team_quick_action_id,
        "selected_personal_quick_action_id": selected_personal_quick_action_id,
        "active_home_tab": resolved_home_tab,
        "active_home_modal": resolved_home_modal,
        "home_page_route": home_page_route,
        "home_return_view": home_return_view,
        "template_editor_scope": template_editor_scope,
        "team_template": selected_team_template,
        "personal_template": selected_personal_template,
        "team_quick_action": selected_team_quick_action,
        "personal_quick_action": selected_personal_quick_action,
        "team_template_latest_version": team_template_latest_version,
        "personal_template_latest_version": personal_template_latest_version,
        "team_template_section_prompts": _structured_section_prompt_map(team_template_latest_version),
        "personal_template_section_prompts": _structured_section_prompt_map(personal_template_latest_version),
        "team_quick_action_latest_version": team_quick_action_latest_version,
        "personal_quick_action_latest_version": personal_quick_action_latest_version,
        "emis_sections": [{"key": key, "label": EMIS_SECTION_LABELS[key]} for key in EMIS_SECTION_KEYS],
        "message": message,
        "message_kind": message_kind,
        "recovery_temporary_password": recovery_temporary_password,
        "email_recovery_enabled": email_recovery_enabled,
        "break_glass_recovery_enabled": break_glass_recovery_enabled(),
        "queued_transcript_id": queued_transcript_id,
        "transcribe_return_tab": transcribe_return_tab,
        "home_style_variant": home_style_variant,
    }
    return templates.TemplateResponse(request, template_name, context, status_code=status_code)


def home_template_name_from_return_view(return_view: str | None) -> str:
    return "home.html"


def home_page_route_from_return_view(return_view: str | None) -> str:
    if return_view == "home2":
        return "/home2"
    return "/home-restyled" if return_view == "restyled" else "/home"


def home_template_editor_url(
    *,
    scope: str,
    template_id: str | None = None,
    return_view: str | None = None,
    queued_transcript_id: str | None = None,
    transcribe_tab: str | None = None,
) -> str:
    params: dict[str, str] = {"scope": scope}
    if template_id:
        params["template_id"] = template_id
    if return_view:
        params["return_view"] = return_view
    if queued_transcript_id:
        params["queued_transcript_id"] = queued_transcript_id
    if transcribe_tab:
        params["transcribe_tab"] = transcribe_tab
    return f"/home/templates/editor?{urlencode(params)}"


def home_return_view_value(return_view: str | None) -> str:
    if return_view == "home2":
        return "home2"
    if return_view == "restyled":
        return "restyled"
    if return_view == "transcribe":
        return "transcribe"
    return ""


def home_redirect_url(
    *,
    return_view: str | None,
    return_tab: str | None = None,
    queued_transcript_id: str | None = None,
    transcribe_tab: str | None = None,
) -> str:
    if return_view == "transcribe":
        params: dict[str, str] = {}
        if queued_transcript_id:
            params["transcript_id"] = queued_transcript_id
        params["tab"] = transcribe_tab or ("followups" if return_tab == "quick-actions" else "output")
        return f"/transcribe?{urlencode(params)}" if params else "/transcribe"
    base = "/home2" if return_view == "home2" else ("/home-restyled" if return_view == "restyled" else "/home")
    if return_tab:
        return f"{base}?tab={return_tab}"
    return base


def home_redirect(*, message: str, message_kind: str, queued_transcript_id=None) -> str:
    params: dict[str, str] = {"message": message, "message_kind": message_kind}
    if queued_transcript_id is not None:
        params["queued_transcript_id"] = str(queued_transcript_id)
    return f"/home?{urlencode(params)}"


def transcribe_redirect(*, message: str, message_kind: str, queued_transcript_id=None) -> str:
    params: dict[str, str] = {"message": message, "message_kind": message_kind}
    if queued_transcript_id is not None:
        params["queued_transcript_id"] = str(queued_transcript_id)
    return f"/transcribe?{urlencode(params)}"


def render_onboarding(
    request,
    *,
    current_user: User,
    totp_secret: str | None = None,
    totp_uri: str | None = None,
    totp_qr_svg_data_uri: str | None = None,
    recovery_codes: list[str] | None = None,
    message: str | None = None,
    message_kind: str = "error",
    status_code: int = 200,
):
    context = {
        "request": request,
        "current_user": current_user,
        "totp_secret": totp_secret,
        "totp_uri": totp_uri,
        "totp_qr_svg_data_uri": totp_qr_svg_data_uri,
        "recovery_codes": recovery_codes,
        "message": message,
        "message_kind": message_kind,
    }
    return templates.TemplateResponse(request, "onboarding.html", context, status_code=status_code)


def render_mfa_challenge(
    request,
    *,
    current_user: User,
    message: str | None = None,
    message_kind: str = "error",
    status_code: int = 200,
):
    context = {
        "request": request,
        "current_user": current_user,
        "message": message,
        "message_kind": message_kind,
    }
    return templates.TemplateResponse(request, "mfa_challenge.html", context, status_code=status_code)


def parse_extra_form_fields_json(raw_value: str) -> dict[str, str]:
    if not raw_value.strip():
        return {}
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise AppError(422, "business_rule_violation", "Extra form fields must be valid JSON", {"field": "extra_form_fields_json"}) from exc
    if not isinstance(parsed, dict):
        raise AppError(422, "business_rule_violation", "Extra form fields must be a JSON object", {"field": "extra_form_fields_json"})
    cleaned: dict[str, str] = {}
    for key, value in parsed.items():
        cleaned[str(key)] = str(value)
    return cleaned


def parse_string_map_json(raw_value: str, *, field_name: str, label: str) -> dict[str, str]:
    if not raw_value.strip():
        return {}
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise AppError(422, "business_rule_violation", f"{label} must be valid JSON", {"field": field_name}) from exc
    if not isinstance(parsed, dict):
        raise AppError(422, "business_rule_violation", f"{label} must be a JSON object", {"field": field_name})
    cleaned: dict[str, str] = {}
    for key, value in parsed.items():
        cleaned[str(key)] = str(value)
    return cleaned


def parse_json_object(raw_value: str, *, field_name: str, label: str) -> dict[str, object]:
    if not raw_value.strip():
        return {}
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise AppError(422, "business_rule_violation", f"{label} must be valid JSON", {"field": field_name}) from exc
    if not isinstance(parsed, dict):
        raise AppError(422, "business_rule_violation", f"{label} must be a JSON object", {"field": field_name})
    return {str(key): value for key, value in parsed.items()}
