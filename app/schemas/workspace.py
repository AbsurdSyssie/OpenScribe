from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from .dictation import PostConsultationDictationDetail
from .consultation_split import ConsultationSplitAnalysisDetail, ConsultationSplitBatchDetail, ConsultationSplitDraftDetail, ConsultationSplitIntentWorkspaceDetail
from .smart_phrases import SmartPhraseDetail
from .templates import GeneratedDocumentDetail, PromptTemplateDetail, QuickActionDetail
from .transcripts import TranscriptDetail, TranscriptListItem, TranscriptPiiEntityDetail, WorkingNoteDetail


class TranscriptStreamGapDetail(BaseModel):
    stream_id: UUID
    window_no: int
    source_start_sample_offset: int
    source_end_sample_offset: int
    forwarded_sample_count: int
    recovery_available: bool
    recovery_in_progress: bool = False
    recovery_job_retry_available: bool = False
    recovery_expires_at: datetime | None = None


class TranscribeWorkspaceDetail(BaseModel):
    example_consultation_pending: bool = False
    recent_transcripts: list[TranscriptListItem]
    recent_transcripts_next_cursor: str | None = None
    recent_transcripts_has_more: bool = False
    active_transcript: TranscriptDetail | None = None
    active_transcript_pii_entities: list[TranscriptPiiEntityDetail] = []
    active_transcript_redaction_status: dict | None = None
    active_transcript_clinical_nlp_status: dict | None = None
    post_consultation_dictation: PostConsultationDictationDetail | None = None
    active_working_note: WorkingNoteDetail | None = None
    generated_documents: list[GeneratedDocumentDetail]
    available_templates: list[PromptTemplateDetail]
    available_quick_actions: list[QuickActionDetail]
    smart_phrases: list[SmartPhraseDetail] = []
    active_structured_context: dict[str, list[str]]
    active_template_generation_input_available: bool = False
    stt_selected: bool
    stt_available: bool
    deepgram_live_streaming_available: bool = False
    dictation_live_streaming_available: bool = False
    diarization_available: bool = False
    diarization_live_available: bool = False
    diarization_batch_available: bool = False
    active_transcript_stream_gap: TranscriptStreamGapDetail | None = None
    stt_status_message: str | None = None
    stt_health: dict | None = None
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
    consultation_splitting_enabled: bool = False
    consultation_splitting_available: bool = False
    consultation_split_analysis: ConsultationSplitAnalysisDetail | None = None
    consultation_split_intent: ConsultationSplitIntentWorkspaceDetail | None = None
    consultation_split_draft: ConsultationSplitDraftDetail | None = None
    consultation_split_batch: ConsultationSplitBatchDetail | None = None


class ExampleConsultationResponse(BaseModel):
    id: UUID | None
