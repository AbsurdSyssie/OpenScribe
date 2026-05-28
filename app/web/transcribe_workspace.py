import asyncio
import json
from contextlib import contextmanager

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..errors import AppError
from ..models import (
    GeneratedDocument,
    ClinicalEntityRun,
    GeneratedDocumentGeneratorType,
    RedactionRun,
    RedactionRunStatus,
    SessionAuthLevel,
    SessionStatus,
    SttSelectionPurpose,
    TeamRole,
    TemplateMode,
    Transcript,
    TranscriptIngestionJobKind,
    TranscriptIngestionJobStatus,
    TranscriptIngestionMode,
    TranscriptManualPiiEntity,
    TranscriptStatus,
    TranscriptVersion,
    User,
    UserSession,
    UserStatus,
    utcnow,
)
from ..schemas import (
    EMIS_SECTION_KEYS,
    EMIS_SECTION_LABELS,
    TranscriptDetail,
    TranscriptIngestionAccepted,
    TranscriptIngestionJobDetail,
    TranscriptListItem,
    TranscriptPiiEntityDetail,
    TranscriptPiiEntitySummary,
    TranscribeWorkspaceDetail,
)
from ..schemas.transcripts import WorkingNoteDetail
from ..services.auth import determine_auth_level, resolve_authenticated_session, revoke_session_by_token, session_token_hash
from ..services.llm import (
    active_team_llm_selection as active_team_llm_selection_service,
    get_user_llm_preference as get_user_llm_preference_service,
    resolve_user_llm as resolve_user_llm_service,
)
from ..services.preferences import get_user_app_preferences as get_user_app_preferences_service
from ..services.dictations import dictation_detail_response, dictation_effective_text, get_post_consultation_dictation
from ..services.stt import active_team_stt_selection as active_team_stt_selection_service
from ..services.stt import check_selected_stt_health as check_selected_stt_health_service
from ..services.templates import (
    list_available_quick_actions_for_user as list_available_quick_actions_for_user_service,
    list_available_templates_for_user as list_available_templates_for_user_service,
    list_generated_documents_for_transcript as list_generated_documents_for_transcript_service,
    generated_document_section_text as generated_document_section_text_service,
)
from ..services.smart_phrases import list_available_smart_phrases as list_available_smart_phrases_service
from ..services.transcripts import (
    can_create_new_session as can_create_new_session_service,
    can_switch_transcript_ingestion_mode as can_switch_transcript_ingestion_mode_service,
    latest_ingestion_job_for_transcript as latest_ingestion_job_for_transcript_service,
    latest_successful_ingestion_completed_at as latest_successful_ingestion_completed_at_service,
    next_live_chunk_sequence_no_for_transcript as next_live_chunk_sequence_no_for_transcript_service,
    reconcile_transcript_status as reconcile_transcript_status_service,
    manual_pii_entity_value as manual_pii_entity_value_service,
    transcript_draft_text as transcript_draft_text_service,
    transcript_has_working_note as transcript_has_working_note_service,
    transcript_working_note_mode as transcript_working_note_mode_service,
    transcript_structured_context as transcript_structured_context_service,
    transcript_version_text as transcript_version_text_service,
    working_note_detail as working_note_detail_service,
)
from ..services.redaction import redaction_entity_original_value as redaction_entity_original_value_service
from ..services.clinical_nlp import clinical_entity_value as clinical_entity_value_service
from .presentation import generated_document_response, quick_action_response, smart_phrase_response, template_response
from .templates import templates


LOCALHOST_NAMES = {"localhost", "127.0.0.1", "::1", "testserver", "testclient"}


def _missing_stt_selection_message(*, team_leader_email: str | None) -> str:
    if team_leader_email:
        return f"No STT configured, please ask your team leader {team_leader_email}"
    return "No STT configured, please ask your team leader."


def _active_structured_context_map(db: Session, transcript: Transcript | None) -> dict[str, list[str]]:
    if transcript is None:
        return {}
    transcript_context = transcript_structured_context_service(db, transcript=transcript)
    if not isinstance(transcript_context, dict):
        return {}
    if transcript_context.get("profile") != "emis":
        return {}
    sections = transcript_context.get("sections")
    if not isinstance(sections, dict):
        return {}
    normalized: dict[str, list[str]] = {}
    for section_key, value in sections.items():
        if not isinstance(section_key, str):
            continue
        if isinstance(value, list):
            lines = [str(item).strip() for item in value if isinstance(item, str) and item.strip()]
        elif isinstance(value, str) and value.strip():
            lines = [value.strip()]
        else:
            lines = []
        if lines:
            normalized[section_key] = lines
    return normalized


def _working_note_json_payload(working_note: object | None) -> dict | None:
    if working_note is None:
        return None
    return WorkingNoteDetail.model_validate(working_note).model_dump(mode="json")


def _document_section_lines(db: Session, document: GeneratedDocument | None) -> dict[str, list[str]]:
    if document is None:
        return {}
    line_map: dict[str, list[str]] = {}
    for section in getattr(document, "sections", []):
        raw_text = generated_document_section_text_service(db, section=section, field="edited_text_encrypted")
        lines = [line for line in raw_text.splitlines() if line.strip()]
        line_map[str(section.id)] = lines or ([raw_text.strip()] if raw_text.strip() else [])
    return line_map


def _document_section_lines_by_key(db: Session, document: GeneratedDocument | None) -> dict[str, list[str]]:
    if document is None:
        return {}
    line_map: dict[str, list[str]] = {}
    for section in getattr(document, "sections", []):
        section_key = str(section.section_key or "").strip()
        if not section_key:
            continue
        raw_text = generated_document_section_text_service(db, section=section, field="edited_text_encrypted")
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        if lines:
            line_map[section_key] = lines
    return line_map


def _default_emis_section_definitions() -> list[dict[str, str]]:
    return [{"key": key, "label": EMIS_SECTION_LABELS[key]} for key in EMIS_SECTION_KEYS]


def _order_assets_by_preferences(assets, favorite_ids: list[str] | None, default_id: str | None = None):
    if not assets:
        return assets
    favorite_position = {value: index for index, value in enumerate(favorite_ids or [])}
    return sorted(
        assets,
        key=lambda asset: (
            0 if default_id and str(asset.id) == default_id else 1,
            0 if str(asset.id) in favorite_position else 1,
            favorite_position.get(str(asset.id), 999),
            str(getattr(asset, "name", "")).lower(),
        ),
    )


def _order_assets_by_favourites(assets, favorite_ids: list[str] | None):
    return _order_assets_by_preferences(assets, favorite_ids)


def _preferred_template_from_preferences(available_templates, preferred_template_id: str | None):
    if preferred_template_id:
        preferred = next((template for template in available_templates if str(template.id) == preferred_template_id), None)
        if preferred is not None:
            return preferred
    return available_templates[0] if available_templates else None


def _latest_template_version(template):
    versions = sorted(getattr(template, "versions", []) or [], key=lambda version: version.version_no)
    return versions[-1] if versions else None


def _structured_section_definitions_for_template(template) -> list[dict[str, str]]:
    latest_version = _latest_template_version(template)
    if latest_version is None or latest_version.mode is not TemplateMode.structured:
        return _default_emis_section_definitions()
    config = latest_version.config_json if isinstance(latest_version.config_json, dict) else {}
    raw_sections = config.get("sections")
    if not isinstance(raw_sections, list):
        return _default_emis_section_definitions()
    ordered_sections = sorted(
        [section for section in raw_sections if isinstance(section, dict)],
        key=lambda section: section.get("section_order") if isinstance(section.get("section_order"), int) else 999,
    )
    definitions: list[dict[str, str]] = []
    for section in ordered_sections:
        section_key = section.get("section_key")
        if section_key not in EMIS_SECTION_KEYS:
            continue
        label = section.get("section_label")
        definitions.append(
            {
                "key": section_key,
                "label": label if isinstance(label, str) and label.strip() else EMIS_SECTION_LABELS[section_key],
            }
        )
    return definitions or _default_emis_section_definitions()


def _structured_section_definitions_for_document(document: GeneratedDocument | None) -> list[dict[str, str]]:
    snapshot = document.structured_section_definitions_json if document is not None else None
    if not isinstance(snapshot, dict):
        return []
    raw_sections = snapshot.get("sections")
    if not isinstance(raw_sections, list):
        return []
    definitions: list[dict[str, str]] = []
    ordered_sections = sorted(
        [section for section in raw_sections if isinstance(section, dict)],
        key=lambda section: section.get("section_order") if isinstance(section.get("section_order"), int) else 999,
    )
    for section in ordered_sections:
        section_key = section.get("section_key")
        if section_key not in EMIS_SECTION_KEYS:
            continue
        label = section.get("section_label")
        definitions.append(
            {
                "key": section_key,
                "label": label if isinstance(label, str) and label.strip() else EMIS_SECTION_LABELS[section_key],
            }
        )
    return definitions


def _structured_section_option_payloads(templates) -> dict[str, list[dict[str, object]]]:
    payloads: dict[str, list[dict[str, object]]] = {}
    for template in templates:
        latest_version = _latest_template_version(template)
        if latest_version is None or latest_version.mode is not TemplateMode.structured:
            continue
        definitions = _structured_section_definitions_for_template(template)
        payloads[str(template.id)] = [
            {
                "section_key": definition["key"],
                "section_label": definition["label"],
                "section_order": index,
            }
            for index, definition in enumerate(definitions)
        ]
    return payloads


def _structured_editor_sections(
    db: Session,
    *,
    generated_document: GeneratedDocument | None,
    active_structured_context: dict[str, list[str]],
    section_definitions: list[dict[str, str]] | None = None,
) -> list[dict[str, object]]:
    generated_lines = _document_section_lines_by_key(db, generated_document)
    sections: list[dict[str, object]] = []
    for definition in section_definitions or _default_emis_section_definitions():
        section_key = definition["key"]
        visible_lines = generated_lines.get(section_key)
        if visible_lines is None:
            visible_lines = list(active_structured_context.get(section_key) or [])
        rows = [{"text": line, "checked": True} for line in visible_lines if isinstance(line, str) and line.strip()]
        rows.append({"text": "", "checked": True})
        sections.append(
            {
                "key": section_key,
                "label": definition.get("label") or EMIS_SECTION_LABELS[section_key],
                "rows": rows,
            }
        )
    return sections


def _freeform_editor_rows(
    db: Session,
    *,
    generated_document: GeneratedDocument | None,
) -> list[dict[str, object]]:
    if generated_document is None:
        return [{"text": "", "checked": True}]
    if generated_document.document_mode is not TemplateMode.freeform:
        return []
    raw_text = generated_document_response(db, generated_document).edited_output_text or ""
    visible_lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    rows = [{"text": line, "checked": True} for line in visible_lines]
    rows.append({"text": "", "checked": True})
    return rows


def _generated_note_has_content(db: Session, document: GeneratedDocument | None) -> bool:
    if document is None:
        return False
    if document.document_mode is TemplateMode.structured:
        return any(lines for lines in _document_section_lines_by_key(db, document).values())
    raw_text = generated_document_response(db, document).edited_output_text or ""
    return bool(raw_text.strip())


def _transcript_has_content(db: Session, transcript: Transcript) -> bool:
    draft_text = transcript_draft_text_service(db, transcript=transcript) or ""
    if draft_text.strip():
        return True
    versions = db.scalars(
        select(TranscriptVersion)
        .where(TranscriptVersion.transcript_id == transcript.id)
    )
    return any((transcript_version_text_service(db, transcript_version=version) or "").strip() for version in versions)


def transcript_list_item_response(db: Session, transcript: Transcript) -> TranscriptListItem:
    payload = TranscriptListItem.model_validate(transcript, from_attributes=True).model_dump()
    payload["has_transcript_content"] = _transcript_has_content(db, transcript)
    payload["latest_successful_ingestion_completed_at"] = latest_successful_ingestion_completed_at_service(
        db,
        transcript_id=transcript.id,
    )
    payload["working_note_mode"] = transcript_working_note_mode_service(db, transcript=transcript)
    payload["has_working_note"] = transcript_has_working_note_service(db, transcript=transcript)
    return TranscriptListItem.model_validate(payload)


def _pii_entity_response(
    *,
    id,
    entity_type: str,
    placeholder: str,
    occurrence_count: int,
    source: str,
    value: str | None,
    include_values: bool,
) -> TranscriptPiiEntitySummary | TranscriptPiiEntityDetail:
    if include_values:
        return TranscriptPiiEntityDetail(
            id=id,
            entity_type=entity_type,
            value=value or "",
            placeholder=placeholder,
            occurrence_count=occurrence_count,
            source=source,
            has_value=True,
        )
    return TranscriptPiiEntitySummary(
        id=id,
        entity_type=entity_type,
        placeholder=placeholder,
        occurrence_count=occurrence_count,
        source=source,
        has_value=True,
    )


def transcript_pii_entities_response(
    db: Session,
    transcript: Transcript | None,
    *,
    include_values: bool = False,
) -> list[TranscriptPiiEntitySummary | TranscriptPiiEntityDetail]:
    if transcript is None:
        return []
    redaction_run = db.scalar(
        select(RedactionRun)
        .where(
            RedactionRun.transcript_id == transcript.id,
            RedactionRun.owner_user_id == transcript.owner_user_id,
        )
        .order_by(RedactionRun.created_at.desc(), RedactionRun.id.desc())
        .limit(1)
    )
    if redaction_run is None or redaction_run.status is not RedactionRunStatus.succeeded:
        detected_entities = []
    else:
        detected_entities = [
            _pii_entity_response(
                id=None,
                entity_type=entity.entity_type,
                value=redaction_entity_original_value_service(db, entity=entity) if include_values else None,
                placeholder=entity.placeholder,
                occurrence_count=entity.occurrence_count,
                source="detected",
                include_values=include_values,
            )
            for entity in sorted(redaction_run.entities, key=lambda item: item.entity_order)
        ]
    manual_entities = list(
        db.scalars(
            select(TranscriptManualPiiEntity)
            .where(
                TranscriptManualPiiEntity.transcript_id == transcript.id,
                TranscriptManualPiiEntity.owner_user_id == transcript.owner_user_id,
            )
            .order_by(TranscriptManualPiiEntity.created_at.asc(), TranscriptManualPiiEntity.id.asc())
        )
    )
    clinical_run = db.scalar(
        select(ClinicalEntityRun)
        .where(
            ClinicalEntityRun.transcript_id == transcript.id,
            ClinicalEntityRun.owner_user_id == transcript.owner_user_id,
        )
        .order_by(ClinicalEntityRun.created_at.desc(), ClinicalEntityRun.id.desc())
        .limit(1)
    )
    clinical_entities = []
    if clinical_run is not None and clinical_run.status is RedactionRunStatus.succeeded:
        clinical_entities = [
            _pii_entity_response(
                id=None,
                entity_type=entity.entity_type,
                value=clinical_entity_value_service(db, entity=entity) if include_values else None,
                placeholder="Clinical NLP",
                occurrence_count=entity.occurrence_count,
                source="clinical",
                include_values=include_values,
            )
            for entity in sorted(clinical_run.entities, key=lambda item: item.entity_order)
        ]
    return detected_entities + clinical_entities + [
        transcript_manual_pii_entity_response(db, entity, include_values=include_values)
        for entity in manual_entities
    ]


def transcript_redaction_status_response(db: Session, transcript: Transcript | None) -> dict[str, object]:
    if transcript is None:
        return {"status": "unavailable", "entity_count": 0, "error_code": None}
    latest_run = db.scalar(
        select(RedactionRun)
        .where(
            RedactionRun.transcript_id == transcript.id,
            RedactionRun.owner_user_id == transcript.owner_user_id,
        )
        .order_by(RedactionRun.created_at.desc(), RedactionRun.id.desc())
        .limit(1)
    )
    if latest_run is None:
        return {"status": "not_run", "entity_count": 0, "error_code": None}
    return {
        "status": latest_run.status.value,
        "entity_count": latest_run.entity_count,
        "error_code": latest_run.error_code,
    }


def transcript_clinical_nlp_status_response(db: Session, transcript: Transcript | None) -> dict[str, object]:
    if transcript is None:
        return {"status": "unavailable", "entity_count": 0, "error_code": None}
    latest_run = db.scalar(
        select(ClinicalEntityRun)
        .where(
            ClinicalEntityRun.transcript_id == transcript.id,
            ClinicalEntityRun.owner_user_id == transcript.owner_user_id,
        )
        .order_by(ClinicalEntityRun.created_at.desc(), ClinicalEntityRun.id.desc())
        .limit(1)
    )
    if latest_run is None:
        return {"status": "not_run", "entity_count": 0, "error_code": None}
    return {
        "status": latest_run.status.value,
        "entity_count": latest_run.entity_count,
        "error_code": latest_run.error_code,
    }


def transcript_manual_pii_entity_response(
    db: Session,
    entity: TranscriptManualPiiEntity,
    *,
    include_values: bool = True,
) -> TranscriptPiiEntitySummary | TranscriptPiiEntityDetail:
    return _pii_entity_response(
        id=entity.id,
        entity_type=entity.entity_type,
        value=manual_pii_entity_value_service(db, entity=entity) if include_values else None,
        placeholder="Manual",
        occurrence_count=entity.occurrence_count,
        source="manual",
        include_values=include_values,
    )


def resolve_transcribe_workspace(
    db: Session,
    *,
    current_user: User,
    transcript_id: str | None = None,
    queued_transcript_id: str | None = None,
    request=None,
    local_dev_emails: set[str] | None = None,
    request_is_localhost_only=None,
    live_stt_health_check: bool = True,
) -> dict[str, object]:
    recent_transcripts = list(
        db.scalars(
            select(Transcript)
            .where(Transcript.owner_user_id == current_user.id)
            .order_by(Transcript.created_at.desc())
            .limit(12)
        )
    )
    active_transcript = None
    requested_transcript_id = queued_transcript_id or transcript_id
    if requested_transcript_id:
        from uuid import UUID

        try:
            selected_id = UUID(requested_transcript_id)
        except ValueError:
            selected_id = None
        if selected_id is not None:
            candidate = db.get(Transcript, selected_id)
            if candidate is not None and candidate.owner_user_id == current_user.id:
                active_transcript = candidate
    if active_transcript is None and recent_transcripts:
        active_transcript = recent_transcripts[0]
    if active_transcript is not None:
        active_transcript = reconcile_transcript_status_service(db, transcript=active_transcript)
    active_transcript_latest_job = (
        latest_ingestion_job_for_transcript_service(db, transcript_id=active_transcript.id)
        if active_transcript is not None
        else None
    )
    active_transcript_next_live_chunk_sequence_no_upload = (
        next_live_chunk_sequence_no_for_transcript_service(db, transcript_id=active_transcript.id)
        if active_transcript is not None and active_transcript.ingestion_mode is TranscriptIngestionMode.live_chunked
        else None
    )
    team_leader_email = (
        db.scalar(
            select(User.email)
            .where(
                User.team_id == current_user.team_id,
                User.team_role == TeamRole.leader,
                User.is_system_admin.is_(False),
                User.status == UserStatus.active,
            )
            .order_by(User.created_at.asc())
        )
        if current_user.team_id is not None
        else None
    )
    stt_selection = None
    stt_available = False
    stt_status_message = None
    stt_health = None
    dictation_stt_selection = None
    dictation_stt_available = False
    dictation_stt_status_message = None
    llm_selection = None
    user_llm_preference = None
    resolved_user_llm_model = None
    if current_user.team_id is not None:
        try:
            stt_selection = active_team_stt_selection_service(db, team_id=current_user.team_id)
        except AppError:
            stt_selection = None
        if stt_selection is None:
            stt_status_message = _missing_stt_selection_message(team_leader_email=team_leader_email)
        else:
            stt_available = True
            stt_health = check_selected_stt_health_service(
                db,
                current_user,
                purpose=SttSelectionPurpose.conversation,
                cache_only=not live_stt_health_check,
            )
        try:
            dictation_stt_selection = active_team_stt_selection_service(
                db,
                team_id=current_user.team_id,
                purpose=SttSelectionPurpose.post_consultation_dictation,
            )
        except AppError:
            dictation_stt_selection = None
        if dictation_stt_selection is None:
            dictation_stt_status_message = _missing_stt_selection_message(team_leader_email=team_leader_email)
        else:
            dictation_stt_available = True
        try:
            llm_selection = active_team_llm_selection_service(db, team_id=current_user.team_id)
        except AppError:
            llm_selection = None
    if not current_user.is_system_admin and current_user.team_id is not None:
        try:
            _, _, resolved_user_llm_model, user_llm_preference = resolve_user_llm_service(db, current_user)
        except AppError:
            user_llm_preference = get_user_llm_preference_service(db, current_user)
    user_app_preference = (
        get_user_app_preferences_service(db, current_user)
        if current_user.team_id is not None and not current_user.is_system_admin
        else None
    )
    user_app_preferences_json = user_app_preference.preferences_json if user_app_preference and isinstance(user_app_preference.preferences_json, dict) else {}
    can_create_new_session, new_session_block_message = can_create_new_session_service(db, current_user)
    can_switch_to_whole_file = False
    switch_mode_block_message = None
    if active_transcript is not None:
        _, can_switch_to_whole_file, whole_file_message = can_switch_transcript_ingestion_mode_service(
            db,
            current_user,
            transcript_id=active_transcript.id,
            target_mode=TranscriptIngestionMode.whole_file,
        )
        if not can_switch_to_whole_file and whole_file_message:
            switch_mode_block_message = whole_file_message
    available_templates = list_available_templates_for_user_service(db, current_user) if current_user.team_id is not None and not current_user.is_system_admin else []
    available_quick_actions = list_available_quick_actions_for_user_service(db, current_user) if current_user.team_id is not None and not current_user.is_system_admin else []
    available_smart_phrases = list_available_smart_phrases_service(db, current_user) if current_user.team_id is not None and not current_user.is_system_admin else []
    available_templates = _order_assets_by_preferences(
        available_templates,
        user_app_preferences_json.get("favorite_template_ids"),
        user_app_preferences_json.get("default_template_id"),
    )
    available_quick_actions = _order_assets_by_preferences(
        available_quick_actions,
        user_app_preferences_json.get("favorite_quick_action_ids"),
        user_app_preferences_json.get("default_quick_action_id"),
    )
    preferred_template = _preferred_template_from_preferences(available_templates, user_app_preferences_json.get("default_template_id"))
    generated_documents = (
        list_generated_documents_for_transcript_service(db, current_user, transcript_id=active_transcript.id)
        if active_transcript is not None and not current_user.is_system_admin
        else []
    )
    note_documents = [document for document in generated_documents if document.generator_type is GeneratedDocumentGeneratorType.template]
    followup_documents = [
        document
        for document in generated_documents
        if document.generator_type in {GeneratedDocumentGeneratorType.followup, GeneratedDocumentGeneratorType.quick_action}
    ]
    latest_generated_document = note_documents[0] if note_documents else None
    latest_followup_document = followup_documents[0] if followup_documents else None
    selected_template = preferred_template or (available_templates[0] if available_templates else None)
    structured_section_definitions = (
        _structured_section_definitions_for_document(latest_generated_document)
        or (_structured_section_definitions_for_template(selected_template) if selected_template is not None else _default_emis_section_definitions())
    )
    show_redaction_debug = bool(
        request is not None
        and local_dev_emails is not None
        and request_is_localhost_only is not None
        and current_user.email.lower() in local_dev_emails
        and request_is_localhost_only(request)
    )
    active_structured_context = _active_structured_context_map(db, active_transcript)
    active_working_note = (
        working_note_detail_service(db, current_user, transcript_id=active_transcript.id)
        if active_transcript is not None and not current_user.is_system_admin
        else None
    )
    active_draft_text = transcript_draft_text_service(db, transcript=active_transcript) if active_transcript is not None else ""
    post_consultation_dictation = (
        get_post_consultation_dictation(db, current_user, transcript_id=active_transcript.id)
        if active_transcript is not None and not current_user.is_system_admin
        else None
    )
    active_working_note_has_text = bool(active_working_note and (
        str(active_working_note.get("freeform_text") or "").strip()
        or any(
            isinstance(lines, list) and any(str(line).strip() for line in lines)
            for lines in (active_working_note.get("structured_note") or {}).get("sections", {}).values()
        )
    ))
    active_dictation_text = (
        dictation_effective_text(db, dictation=post_consultation_dictation)
        if post_consultation_dictation is not None
        else ""
    )
    active_template_generation_input_available = bool(
        active_transcript
        and (
            bool((active_draft_text or "").strip())
            or bool(active_structured_context)
            or active_working_note_has_text
            or bool(active_dictation_text.strip())
        )
    )
    active_note_input_available = bool(
        active_transcript
        and (
            bool((active_draft_text or "").strip())
            or bool(active_structured_context)
            or _generated_note_has_content(db, latest_generated_document)
        )
    )
    active_quick_action_input_available = active_template_generation_input_available
    structured_editor_sections = _structured_editor_sections(
        db,
        generated_document=latest_generated_document,
        active_structured_context=active_structured_context,
        section_definitions=structured_section_definitions,
    )
    freeform_editor_rows = _freeform_editor_rows(
        db,
        generated_document=latest_generated_document,
    )
    structured_editor_has_text = any(
        isinstance(row.get("text"), str) and row["text"].strip()
        for section in structured_editor_sections
        for row in section.get("rows", [])
    )
    freeform_editor_has_text = any(
        isinstance(row.get("text"), str) and row["text"].strip()
        for row in freeform_editor_rows
    )
    return {
        "recent_transcripts": recent_transcripts,
        "active_transcript": active_transcript,
        "active_transcript_latest_job": active_transcript_latest_job,
        "active_transcript_next_live_chunk_sequence_no_upload": active_transcript_next_live_chunk_sequence_no_upload,
        "active_transcript_id": str(active_transcript.id) if active_transcript is not None else None,
        "post_consultation_dictation": post_consultation_dictation,
        "stt_selection": stt_selection,
        "stt_available": stt_available,
        "stt_status_message": stt_status_message,
        "stt_health": stt_health,
        "dictation_stt_selection": dictation_stt_selection,
        "dictation_stt_selected": bool(dictation_stt_selection),
        "dictation_stt_available": dictation_stt_available,
        "dictation_stt_status_message": dictation_stt_status_message,
        "llm_selection": llm_selection,
        "user_llm_preference": user_llm_preference,
        "resolved_user_llm_model": resolved_user_llm_model,
        "queued_transcript_id": queued_transcript_id,
        "can_create_new_session": can_create_new_session,
        "new_session_block_message": new_session_block_message,
        "can_switch_to_whole_file": can_switch_to_whole_file,
        "switch_mode_block_message": switch_mode_block_message,
        "available_templates": available_templates,
        "preferred_template": preferred_template,
        "preferred_template_id": str(preferred_template.id) if preferred_template is not None else None,
        "preferred_recording_mode": user_app_preferences_json.get("preferred_recording_mode"),
        "user_app_preferences_json": user_app_preferences_json,
        "template_section_definitions_by_id": _structured_section_option_payloads(available_templates),
        "available_quick_actions": available_quick_actions,
        "available_smart_phrases": available_smart_phrases,
        "generated_documents": generated_documents,
        "note_documents": note_documents,
        "followup_documents": followup_documents,
        "latest_generated_document": latest_generated_document,
        "latest_generated_document_section_lines": _document_section_lines(db, latest_generated_document),
        "structured_editor_sections": structured_editor_sections,
        "structured_editor_has_text": structured_editor_has_text,
        "freeform_editor_rows": freeform_editor_rows,
        "freeform_editor_has_text": freeform_editor_has_text,
        "latest_followup_document": latest_followup_document,
        "active_structured_context": active_structured_context,
        "active_working_note": active_working_note,
        "active_transcript_pii_entities": transcript_pii_entities_response(db, active_transcript, include_values=True),
        "active_transcript_redaction_status": transcript_redaction_status_response(db, active_transcript),
        "active_transcript_clinical_nlp_status": transcript_clinical_nlp_status_response(db, active_transcript),
        "active_note_input_available": active_note_input_available,
        "active_quick_action_input_available": active_quick_action_input_available,
        "active_template_generation_input_available": active_template_generation_input_available,
        "show_redaction_debug": show_redaction_debug,
        "emis_sections": _default_emis_section_definitions(),
        "structured_section_definitions": structured_section_definitions,
        "team_leader_email": team_leader_email,
    }


def transcript_detail_response(db: Session, transcript: Transcript) -> TranscriptDetail:
    transcript = reconcile_transcript_status_service(db, transcript=transcript)
    latest_job = latest_ingestion_job_for_transcript_service(db, transcript_id=transcript.id)
    payload = transcript_list_item_response(db, transcript).model_dump()
    payload["current_draft_text"] = transcript_draft_text_service(db, transcript=transcript)
    payload["structured_context_json"] = transcript_structured_context_service(db, transcript=transcript)
    if latest_job is not None:
        payload["next_live_chunk_sequence_no_upload"] = (
            next_live_chunk_sequence_no_for_transcript_service(db, transcript_id=transcript.id)
            if transcript.ingestion_mode is TranscriptIngestionMode.live_chunked
            else None
        )
        payload["latest_ingestion_job_status"] = latest_job.status
        payload["latest_ingestion_error_code"] = latest_job.error_code
        payload["latest_ingestion_error_message"] = latest_job.error_message
        payload["latest_ingestion_retry_available"] = bool(
            latest_job.job_kind is TranscriptIngestionJobKind.audio_file
            and latest_job.status is TranscriptIngestionJobStatus.failed
            and (latest_job.source_audio_blob or latest_job.source_audio_vault_ref)
            and latest_job.source_audio_size_bytes
        )
    elif transcript.ingestion_mode is TranscriptIngestionMode.live_chunked:
        payload["next_live_chunk_sequence_no_upload"] = next_live_chunk_sequence_no_for_transcript_service(db, transcript_id=transcript.id)
    return TranscriptDetail.model_validate(payload)


def transcribe_workspace_response(db: Session, workspace: dict[str, object]) -> TranscribeWorkspaceDetail:
    active_transcript = workspace.get("active_transcript")
    post_consultation_dictation = workspace.get("post_consultation_dictation")
    recent_transcripts = workspace.get("recent_transcripts") or []
    generated_documents = workspace.get("generated_documents") or []
    available_templates = workspace.get("available_templates") or []
    available_quick_actions = workspace.get("available_quick_actions") or []
    available_smart_phrases = workspace.get("available_smart_phrases") or []
    return TranscribeWorkspaceDetail(
        recent_transcripts=[transcript_list_item_response(db, transcript) for transcript in recent_transcripts],
        active_transcript=transcript_detail_response(db, active_transcript) if isinstance(active_transcript, Transcript) else None,
        active_transcript_pii_entities=list(workspace.get("active_transcript_pii_entities") or []),
        active_transcript_redaction_status=dict(workspace.get("active_transcript_redaction_status") or {}),
        active_transcript_clinical_nlp_status=dict(workspace.get("active_transcript_clinical_nlp_status") or {}),
        post_consultation_dictation=(
            dictation_detail_response(db, dictation=post_consultation_dictation)
            if post_consultation_dictation is not None
            else None
        ),
        active_working_note=workspace.get("active_working_note"),
        generated_documents=[generated_document_response(db, document) for document in generated_documents],
        available_templates=[template_response(template) for template in available_templates],
        available_quick_actions=[quick_action_response(quick_action) for quick_action in available_quick_actions],
        smart_phrases=[smart_phrase_response(phrase) for phrase in available_smart_phrases],
        active_structured_context=dict(workspace.get("active_structured_context") or {}),
        active_template_generation_input_available=bool(workspace.get("active_template_generation_input_available")),
        stt_selected=bool(workspace.get("stt_selection")),
        stt_available=bool(workspace.get("stt_available")),
        stt_status_message=workspace.get("stt_status_message"),
        stt_health=workspace.get("stt_health"),
        dictation_stt_selected=bool(workspace.get("dictation_stt_selection")),
        dictation_stt_available=bool(workspace.get("dictation_stt_available")),
        dictation_stt_status_message=workspace.get("dictation_stt_status_message"),
        llm_selected=bool(workspace.get("llm_selection")),
        resolved_user_llm_model=workspace.get("resolved_user_llm_model"),
        can_create_new_session=bool(workspace.get("can_create_new_session")),
        new_session_block_message=workspace.get("new_session_block_message"),
        can_switch_to_whole_file=bool(workspace.get("can_switch_to_whole_file")),
        switch_mode_block_message=workspace.get("switch_mode_block_message"),
        team_leader_email=workspace.get("team_leader_email"),
    )


def resolve_transcribe_workspace_detail(
    db: Session,
    *,
    current_user: User,
    transcript_id: str | None = None,
    queued_transcript_id: str | None = None,
    request=None,
    local_dev_emails: set[str] | None = None,
    request_is_localhost_only=None,
) -> TranscribeWorkspaceDetail:
    workspace = resolve_transcribe_workspace(
        db,
        current_user=current_user,
        transcript_id=transcript_id,
        queued_transcript_id=queued_transcript_id,
        request=request,
        local_dev_emails=local_dev_emails,
        request_is_localhost_only=request_is_localhost_only,
    )
    return transcribe_workspace_response(db, workspace)


def render_transcribe(
    request,
    db: Session,
    *,
    current_user: User,
    local_dev_emails: set[str] | None = None,
    request_is_localhost_only=None,
    template_name: str = "transcribe.html",
    transcript_id: str | None = None,
    queued_transcript_id: str | None = None,
    active_tab: str = "transcript",
    message: str | None = None,
    message_kind: str = "success",
    status_code: int = 200,
):
    workspace = resolve_transcribe_workspace(
        db,
        current_user=current_user,
        transcript_id=transcript_id,
        queued_transcript_id=queued_transcript_id,
        request=request,
        local_dev_emails=local_dev_emails,
        request_is_localhost_only=request_is_localhost_only,
        live_stt_health_check=False,
    )
    workspace_endpoint = "/api/v1/transcribe/workspace"
    workspace_stream_endpoint = "/api/v1/transcribe/workspace/stream"
    active_transcript = workspace.get("active_transcript")
    if isinstance(active_transcript, Transcript):
        workspace["active_working_note"] = _working_note_json_payload(
            working_note_detail_service(db, current_user, transcript_id=active_transcript.id)
        )
        workspace["active_transcript"] = transcript_detail_response(db, active_transcript)
        workspace_endpoint = f"{workspace_endpoint}?transcript_id={active_transcript.id}"
        workspace_stream_endpoint = f"{workspace_stream_endpoint}?transcript_id={active_transcript.id}"
    recent_transcripts = workspace.get("recent_transcripts") or []
    workspace["recent_transcripts"] = [
        transcript_list_item_response(db, transcript)
        for transcript in recent_transcripts
        if isinstance(transcript, Transcript)
    ]
    post_consultation_dictation = workspace.get("post_consultation_dictation")
    if post_consultation_dictation is not None:
        workspace["post_consultation_dictation"] = dictation_detail_response(db, dictation=post_consultation_dictation)
    workspace["active_transcript_pii_entities"] = [
        entity.model_dump(mode="json")
        for entity in transcript_pii_entities_response(
            db,
            active_transcript if isinstance(active_transcript, Transcript) else None,
            include_values=True,
        )
    ]
    workspace["active_transcript_redaction_status"] = transcript_redaction_status_response(
        db,
        active_transcript if isinstance(active_transcript, Transcript) else None,
    )
    workspace["active_transcript_clinical_nlp_status"] = transcript_clinical_nlp_status_response(
        db,
        active_transcript if isinstance(active_transcript, Transcript) else None,
    )
    generated_documents = workspace.get("generated_documents") or []
    available_smart_phrases = workspace.get("available_smart_phrases") or []
    workspace["smart_phrases"] = [
        smart_phrase_response(phrase).model_dump(mode="json")
        for phrase in available_smart_phrases
    ]
    if generated_documents:
        generated_document_details = [generated_document_response(db, document) for document in generated_documents]
        workspace["generated_documents"] = generated_document_details
        note_documents = [document for document in generated_document_details if document.generator_type is GeneratedDocumentGeneratorType.template]
        followup_documents = [
            document
            for document in generated_document_details
            if document.generator_type in {GeneratedDocumentGeneratorType.followup, GeneratedDocumentGeneratorType.quick_action}
        ]
        workspace["note_documents"] = note_documents
        workspace["followup_documents"] = followup_documents
        workspace["latest_generated_document"] = note_documents[0] if note_documents else None
        workspace["latest_followup_document"] = followup_documents[0] if followup_documents else None
    context = {
        "request": request,
        "current_user": current_user,
        **workspace,
        "workspace_endpoint": workspace_endpoint,
        "workspace_stream_endpoint": workspace_stream_endpoint,
        "transcribe_route_base": request.url.path if request is not None else "/transcribe",
        "message": message,
        "message_kind": message_kind,
        "active_tab": active_tab if active_tab in {"transcript", "output", "followups"} else "transcript",
    }
    return templates.TemplateResponse(request, template_name, context, status_code=status_code)


def serialize_sse_event(*, event: str, payload: dict[str, object]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"event: {event}\ndata: {body}\n\n"


@contextmanager
def open_realtime_workspace_db_session(request):
    session_factory = getattr(request.app.state, "db_session_factory", SessionLocal)
    with session_factory() as db:
        yield db


def resolve_realtime_workspace_user(db: Session, *, raw_session_token: str | None) -> User | None:
    if not raw_session_token:
        return None
    session = db.scalar(
        select(UserSession).where(UserSession.session_token_hash == session_token_hash(raw_session_token))
    )
    if session is None:
        return None
    if session.status is not SessionStatus.active or session.expires_at <= utcnow():
        return None
    if session.auth_level is not SessionAuthLevel.full:
        return None
    user = db.get(User, session.user_id)
    if user is None or user.status is not UserStatus.active:
        return None
    if determine_auth_level(user) is not SessionAuthLevel.full:
        return None
    return user


async def stream_transcribe_workspace_events(
    *,
    request,
    raw_session_token: str | None,
    transcript_id: str | None,
    queued_transcript_id: str | None,
    once: bool,
):
    last_payload_json: str | None = None
    sent_initial_event = False
    heartbeat_interval_seconds = 15.0
    poll_interval_seconds = 1.0
    next_heartbeat_at = asyncio.get_running_loop().time() + heartbeat_interval_seconds

    while True:
        if sent_initial_event and await request.is_disconnected():
            break

        with open_realtime_workspace_db_session(request) as db:
            current_user = resolve_realtime_workspace_user(db, raw_session_token=raw_session_token)
            if current_user is None:
                break
            payload = resolve_transcribe_workspace_detail(
                db,
                current_user=current_user,
                transcript_id=transcript_id,
                queued_transcript_id=queued_transcript_id,
            ).model_dump(mode="json")

        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        current_time = asyncio.get_running_loop().time()
        if payload_json != last_payload_json:
            last_payload_json = payload_json
            next_heartbeat_at = current_time + heartbeat_interval_seconds
            yield serialize_sse_event(event="workspace", payload=payload)
            sent_initial_event = True
            if once:
                break
        elif current_time >= next_heartbeat_at:
            next_heartbeat_at = current_time + heartbeat_interval_seconds
            yield ": keepalive\n\n"

        await asyncio.sleep(poll_interval_seconds)
