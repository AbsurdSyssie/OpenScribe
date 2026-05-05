from pydantic import BaseModel

from .dictation import PostConsultationDictationDetail
from .smart_phrases import SmartPhraseDetail
from .templates import GeneratedDocumentDetail, PromptTemplateDetail, QuickActionDetail
from .transcripts import TranscriptDetail, TranscriptListItem, TranscriptPiiEntityDetail


class TranscribeWorkspaceDetail(BaseModel):
    recent_transcripts: list[TranscriptListItem]
    active_transcript: TranscriptDetail | None = None
    active_transcript_pii_entities: list[TranscriptPiiEntityDetail] = []
    active_transcript_redaction_status: dict | None = None
    active_transcript_clinical_nlp_status: dict | None = None
    post_consultation_dictation: PostConsultationDictationDetail | None = None
    generated_documents: list[GeneratedDocumentDetail]
    available_templates: list[PromptTemplateDetail]
    available_quick_actions: list[QuickActionDetail]
    smart_phrases: list[SmartPhraseDetail] = []
    active_structured_context: dict[str, list[str]]
    stt_selected: bool
    stt_available: bool
    stt_status_message: str | None = None
    dictation_stt_selected: bool = False
    dictation_stt_available: bool = False
    dictation_stt_status_message: str | None = None
    llm_selected: bool
    resolved_user_llm_model: str | None = None
    can_create_new_session: bool
    new_session_block_message: str | None = None
    can_switch_to_whole_file: bool
    switch_mode_block_message: str | None = None
    team_leader_email: str | None = None
