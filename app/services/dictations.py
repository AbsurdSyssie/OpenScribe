from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import PostConsultationDictation, PostConsultationDictationSegment, SttSelectionPurpose, Transcript, User, utcnow
from app.schemas import PostConsultationDictationDetail
from app.services.audio import enforce_whole_file_duration_limit, enforce_whole_file_upload_size, normalize_audio_to_wav_16k_mono
from app.services.content_crypto import decrypt_text_for_owner, encrypt_text_for_owner
from app.services.stt import transcribe_with_team_stt
from app.services.transcripts import get_active_owner_transcript


def _get_owner_transcript(db: Session, owner: User, *, transcript_id: UUID) -> Transcript:
    return get_active_owner_transcript(db, owner, transcript_id=transcript_id)


def dictation_combined_text(db: Session, *, dictation: PostConsultationDictation) -> str | None:
    return decrypt_text_for_owner(
        db,
        owner_user_id=dictation.owner_user_id,
        table="post_consultation_dictations",
        field="combined_edited_text_encrypted",
        record_id=dictation.id,
        stored_value=dictation.combined_edited_text_encrypted,
    )


def dictation_segment_text(db: Session, *, segment: PostConsultationDictationSegment) -> str:
    return (
        decrypt_text_for_owner(
            db,
            owner_user_id=segment.owner_user_id,
            table="post_consultation_dictation_segments",
            field="asr_text_encrypted",
            record_id=segment.id,
            stored_value=segment.asr_text_encrypted,
        )
        or ""
    )


def dictation_effective_text(db: Session, *, dictation: PostConsultationDictation) -> str:
    combined_text = dictation_combined_text(db, dictation=dictation)
    if dictation.is_combined_text_user_edited:
        return combined_text or ""
    segment_texts = [dictation_segment_text(db, segment=segment).strip() for segment in dictation.segments]
    return "\n\n".join(text for text in segment_texts if text)


def dictation_detail_response(db: Session, *, dictation: PostConsultationDictation) -> PostConsultationDictationDetail:
    return PostConsultationDictationDetail(
        id=dictation.id,
        transcript_id=dictation.transcript_id,
        owner_user_id=dictation.owner_user_id,
        team_id=dictation.team_id,
        combined_edited_text_encrypted=dictation_combined_text(db, dictation=dictation),
        effective_text=dictation_effective_text(db, dictation=dictation),
        is_combined_text_user_edited=dictation.is_combined_text_user_edited,
        segment_count=len(dictation.segments),
        latest_appended_at=dictation.latest_appended_at,
        created_at=dictation.created_at,
        updated_at=dictation.updated_at,
    )


def get_post_consultation_dictation(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
) -> PostConsultationDictation | None:
    transcript = _get_owner_transcript(db, owner, transcript_id=transcript_id)
    return db.scalar(
        select(PostConsultationDictation)
        .where(PostConsultationDictation.transcript_id == transcript.id)
    )


def _get_or_create_post_consultation_dictation(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
) -> tuple[Transcript, PostConsultationDictation]:
    transcript = _get_owner_transcript(db, owner, transcript_id=transcript_id)
    dictation = db.scalar(select(PostConsultationDictation).where(PostConsultationDictation.transcript_id == transcript.id))
    if dictation is None:
        dictation = PostConsultationDictation(
            id=uuid4(),
            transcript_id=transcript.id,
            owner_user_id=owner.id,
            team_id=transcript.team_id,
            combined_edited_text_encrypted=None,
            is_combined_text_user_edited=False,
            latest_appended_at=None,
        )
        db.add(dictation)
        db.flush()
    return transcript, dictation


def update_post_consultation_dictation(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
    combined_text: str,
) -> PostConsultationDictation:
    _, dictation = _get_or_create_post_consultation_dictation(db, owner, transcript_id=transcript_id)
    dictation.combined_edited_text_encrypted = encrypt_text_for_owner(
        db,
        owner_user_id=owner.id,
        table="post_consultation_dictations",
        field="combined_edited_text_encrypted",
        record_id=dictation.id,
        plaintext=combined_text,
    )
    dictation.is_combined_text_user_edited = True
    dictation.updated_at = utcnow()
    db.add(dictation)
    db.commit()
    db.refresh(dictation)
    return dictation


def transcribe_prompt_context_audio(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
    audio_bytes: bytes,
    filename: str,
) -> str:
    transcript = _get_owner_transcript(db, owner, transcript_id=transcript_id)
    enforce_whole_file_upload_size(audio_bytes=audio_bytes)
    normalized_audio = normalize_audio_to_wav_16k_mono(audio_bytes=audio_bytes, source_filename=filename)
    enforce_whole_file_duration_limit(audio_bytes=normalized_audio.data)
    transcript_text = transcribe_with_team_stt(
        db,
        team_id=transcript.team_id,
        purpose=SttSelectionPurpose.post_consultation_dictation,
        audio_bytes=normalized_audio.data,
        filename=normalized_audio.filename,
        content_type=normalized_audio.content_type,
    ).strip()
    if not transcript_text:
        raise AppError(502, "stt_response_invalid", "STT provider response did not contain transcript text")
    return transcript_text


def transcribe_post_consultation_dictation_audio(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
    audio_bytes: bytes,
    filename: str,
) -> str:
    return transcribe_prompt_context_audio(
        db,
        owner,
        transcript_id=transcript_id,
        audio_bytes=audio_bytes,
        filename=filename,
    )


def append_post_consultation_dictation_audio(
    db: Session,
    owner: User,
    *,
    transcript_id: UUID,
    audio_bytes: bytes,
    filename: str,
) -> PostConsultationDictation:
    transcript, dictation = _get_or_create_post_consultation_dictation(db, owner, transcript_id=transcript_id)
    transcript_text = transcribe_post_consultation_dictation_audio(
        db,
        owner,
        transcript_id=transcript_id,
        audio_bytes=audio_bytes,
        filename=filename,
    )
    next_sequence_no = (
        db.scalar(
            select(func.coalesce(func.max(PostConsultationDictationSegment.sequence_no), 0)).where(
                PostConsultationDictationSegment.post_consultation_dictation_id == dictation.id
            )
        )
        or 0
    ) + 1
    segment = PostConsultationDictationSegment(
        id=uuid4(),
        post_consultation_dictation_id=dictation.id,
        owner_user_id=owner.id,
        team_id=transcript.team_id,
        sequence_no=next_sequence_no,
        asr_text_encrypted="",
    )
    segment.asr_text_encrypted = encrypt_text_for_owner(
        db,
        owner_user_id=owner.id,
        table="post_consultation_dictation_segments",
        field="asr_text_encrypted",
        record_id=segment.id,
        plaintext=transcript_text,
    ) or ""
    dictation.latest_appended_at = utcnow()
    dictation.updated_at = dictation.latest_appended_at
    db.add(segment)
    db.add(dictation)
    db.commit()
    db.refresh(dictation)
    return dictation
