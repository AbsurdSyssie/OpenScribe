"""Transcribe browser routes extracted from the home/transcribe route module."""

from .. import main as main_module
from ..main import *  # noqa: F401,F403
from ..main import (
    _local_only_dev_emails,
    _page_context_or_redirect,
    _request_is_localhost_only,
    _structured_context_from_form,
    _transcribe_redirect,
)
from ..web.transcribe_workspace import _missing_stt_selection_message


def _transcribe_redirect_response(*, message: str, message_kind: str, queued_transcript_id: UUID | None = None):
    return RedirectResponse(
        url=_transcribe_redirect(
            message=message,
            message_kind=message_kind,
            queued_transcript_id=queued_transcript_id,
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _render_transcribe_page(request: Request, db: Session, *, current_user: User, **kwargs):
    return render_transcribe(
        request,
        db,
        current_user=current_user,
        local_dev_emails=_local_only_dev_emails(),
        request_is_localhost_only=_request_is_localhost_only,
        **kwargs,
    )


@app.get("/transcribe", response_class=HTMLResponse)
def transcribe_page(
    request: Request,
    message: str | None = None,
    message_kind: str = "success",
    transcript_id: str | None = None,
    queued_transcript_id: str | None = None,
    tab: str = "transcript",
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if context.user.is_system_admin:
        return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    safe_message_kind = message_kind if message_kind in {"success", "error"} else "success"
    return _render_transcribe_page(
        request,
        db,
        current_user=context.user,
        transcript_id=transcript_id,
        queued_transcript_id=queued_transcript_id,
        active_tab=tab,
        message=message,
        message_kind=safe_message_kind,
    )


@app.get("/transcribe-claude", response_class=HTMLResponse)
def transcribe_claude_page(
    request: Request,
    message: str | None = None,
    message_kind: str = "success",
    transcript_id: str | None = None,
    queued_transcript_id: str | None = None,
    tab: str = "transcript",
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if context.user.is_system_admin:
        return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    safe_message_kind = message_kind if message_kind in {"success", "error"} else "success"
    return _render_transcribe_page(
        request,
        db,
        current_user=context.user,
        template_name="transcribe_claude.html",
        transcript_id=transcript_id,
        queued_transcript_id=queued_transcript_id,
        active_tab=tab,
        message=message,
        message_kind=safe_message_kind,
    )


@app.get("/transcribe-glm-2", response_class=HTMLResponse)
def transcribe_glm_2_page(
    request: Request,
    message: str | None = None,
    message_kind: str = "success",
    transcript_id: str | None = None,
    queued_transcript_id: str | None = None,
    tab: str = "transcript",
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if context.user.is_system_admin:
        return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    safe_message_kind = message_kind if message_kind in {"success", "error"} else "success"
    return render_transcribe(
        request,
        db,
        current_user=context.user,
        template_name="transcribe.html",
        transcript_id=transcript_id,
        queued_transcript_id=queued_transcript_id,
        active_tab=tab,
        message=message,
        message_kind=safe_message_kind,
    )


@app.post("/home/transcripts/upload", response_class=HTMLResponse)
@WHOLE_FILE_UPLOAD_DAILY_RATE_LIMIT
@WHOLE_FILE_UPLOAD_BURST_RATE_LIMIT
def home_upload_transcript_file(
    request: Request,
    title: str = Form(""),
    audio: UploadFile = File(...),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if context.user.team_id is None:
        return _render_transcribe_page(
            request,
            db,
            current_user=context.user,
            message="Current user does not belong to a team",
            message_kind="error",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    try:
        active_team_stt_selection_service(db, team_id=context.user.team_id)
    except AppError as exc:
        if exc.code == "business_rule_violation":
            leader_email = db.scalar(
                select(User.email)
                .where(
                    User.team_id == context.user.team_id,
                    User.team_role == TeamRole.leader,
                    User.is_system_admin.is_(False),
                    User.status == UserStatus.active,
                )
                .order_by(User.created_at.asc())
            )
            return _transcribe_redirect_response(
                message=_missing_stt_selection_message(team_leader_email=leader_email),
                message_kind="error",
            )
        return _transcribe_redirect_response(message=exc.message, message_kind="error")
    audio_bytes = audio.file.read()
    job = None
    try:
        enforce_whole_file_upload_size(audio_bytes=audio_bytes)
        transcript = start_transcript_service(
            db,
            context.user,
            TranscriptStart(
                title=title or audio.filename or "Uploaded audio",
                ingestion_mode=TranscriptIngestionMode.whole_file,
            ),
        )
        _, job = queue_audio_file_ingestion(
            db,
            context.user,
            transcript_id=transcript.id,
            filename=audio.filename or "audio.bin",
            source_audio_blob=audio_bytes,
        )
        task_result = main_module.enqueue_transcript_ingestion_job(job_id=job.id, audio_bytes=audio_bytes)
        attach_task_id_to_ingestion_job(db, job_id=job.id, task_id=getattr(task_result, "id", None))
    except AppError as exc:
        return _transcribe_redirect_response(message=exc.message, message_kind="error")
    except Exception:
        if job is not None:
            mark_ingestion_job_enqueue_failed(db, job_id=job.id, message="Could not enqueue file ingestion")
        return _transcribe_redirect_response(
            message="Could not enqueue file ingestion",
            message_kind="error",
        )

    return _transcribe_redirect_response(
        message="Audio file queued for transcription.",
        message_kind="success",
        queued_transcript_id=transcript.id,
    )


@app.post("/transcribe/upload", response_class=HTMLResponse)
@WHOLE_FILE_UPLOAD_DAILY_RATE_LIMIT
@WHOLE_FILE_UPLOAD_BURST_RATE_LIMIT
def transcribe_upload_transcript_file(
    request: Request,
    transcript_id: str | None = Form(default=None),
    title: str = Form(""),
    audio: UploadFile = File(...),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    if context.user.team_id is None:
        return _render_transcribe_page(
            request,
            db,
            current_user=context.user,
            message="Current user does not belong to a team",
            message_kind="error",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    try:
        active_team_stt_selection_service(db, team_id=context.user.team_id)
    except AppError as exc:
        if exc.code == "business_rule_violation":
            leader_email = db.scalar(
                select(User.email)
                .where(
                    User.team_id == context.user.team_id,
                    User.team_role == TeamRole.leader,
                    User.is_system_admin.is_(False),
                    User.status == UserStatus.active,
                )
                .order_by(User.created_at.asc())
            )
            return _transcribe_redirect_response(
                message=_missing_stt_selection_message(team_leader_email=leader_email),
                message_kind="error",
                queued_transcript_id=UUID(transcript_id) if transcript_id else None,
            )
        return _transcribe_redirect_response(message=exc.message, message_kind="error")
    audio_bytes = audio.file.read()
    job = None
    try:
        enforce_whole_file_upload_size(audio_bytes=audio_bytes)
        if not transcript_id:
            raise AppError(409, "business_rule_violation", "Create or choose a transcript session before uploading audio")
        transcript = db.get(Transcript, UUID(transcript_id))
        if transcript is None or transcript.owner_user_id != context.user.id:
            raise AppError(404, "not_found", "Transcript not found", {"resource": "transcript", "transcript_id": transcript_id})
        if title.strip():
            transcript = update_transcript_title_service(
                db,
                context.user,
                transcript_id=transcript.id,
                title=title,
            )
        _, job = queue_audio_file_ingestion(
            db,
            context.user,
            transcript_id=transcript.id,
            filename=audio.filename or "audio.bin",
            source_audio_blob=audio_bytes,
        )
        task_result = main_module.enqueue_transcript_ingestion_job(job_id=job.id, audio_bytes=audio_bytes)
        attach_task_id_to_ingestion_job(db, job_id=job.id, task_id=getattr(task_result, "id", None))
    except AppError as exc:
        return _transcribe_redirect_response(
            message=exc.message,
            message_kind="error",
            queued_transcript_id=UUID(transcript_id) if transcript_id else None,
        )
    except Exception:
        if job is not None:
            mark_ingestion_job_enqueue_failed(db, job_id=job.id, message="Could not enqueue file ingestion")
        return _transcribe_redirect_response(
            message="Could not enqueue file ingestion",
            message_kind="error",
            queued_transcript_id=UUID(transcript_id) if transcript_id else None,
        )
    return _transcribe_redirect_response(
        message="Audio file queued for transcription.",
        message_kind="success",
        queued_transcript_id=transcript.id,
    )


@app.post("/transcribe/retry-file-ingestion", response_class=HTMLResponse)
@WHOLE_FILE_UPLOAD_DAILY_RATE_LIMIT
@WHOLE_FILE_UPLOAD_BURST_RATE_LIMIT
def transcribe_retry_file_ingestion(
    request: Request,
    transcript_id: UUID = Form(...),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        transcript, job, source_audio_blob, previous_job = retry_audio_file_ingestion(
            db,
            context.user,
            transcript_id=transcript_id,
        )
        task_result = main_module.enqueue_transcript_ingestion_job(job_id=job.id, audio_bytes=source_audio_blob)
        clear_ingestion_retry_source(
            db,
            job_id=previous_job.id,
            clear_storage=True,
            clear_accounting=False,
            delete_backing_secret=False,
        )
        attach_task_id_to_ingestion_job(db, job_id=job.id, task_id=getattr(task_result, "id", None))
    except AppError as exc:
        return _transcribe_redirect_response(
            message=exc.message,
            message_kind="error",
            queued_transcript_id=transcript_id,
        )
    except Exception:
        if "job" in locals():
            mark_ingestion_job_enqueue_failed(db, job_id=job.id, message="Could not enqueue file ingestion retry")
        return _transcribe_redirect_response(
            message="Could not enqueue file ingestion retry",
            message_kind="error",
            queued_transcript_id=transcript_id,
        )
    return _transcribe_redirect_response(
        message="Audio file queued for transcription retry.",
        message_kind="success",
        queued_transcript_id=transcript.id,
    )


@app.post("/transcribe/generate-output", response_class=HTMLResponse)
@LLM_GENERATION_DAILY_RATE_LIMIT
@LLM_GENERATION_BURST_RATE_LIMIT
def transcribe_generate_output(
    request: Request,
    transcript_id: UUID = Form(...),
    template_id: UUID = Form(...),
    context_problem: str = Form(""),
    context_history: str = Form(""),
    context_family_history: str = Form(""),
    context_social_history: str = Form(""),
    context_examination: str = Form(""),
    context_comment: str = Form(""),
    context_tasks: str = Form(""),
    context_investigations: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    document = None
    try:
        document = queue_document_generation_from_template_service(
            db,
            context.user,
            transcript_id=transcript_id,
            template_id=template_id,
            structured_context=_structured_context_from_form(
                section_values={
                    "problem": context_problem,
                    "history": context_history,
                    "family_history": context_family_history,
                    "social_history": context_social_history,
                    "examination": context_examination,
                    "comment": context_comment,
                    "tasks": context_tasks,
                    "investigations": context_investigations,
                }
            ),
        )
        task_result = main_module.enqueue_generated_document_job(document_id=document.id)
        attach_generated_document_task_id_service(db, document_id=document.id, task_id=getattr(task_result, "id", None))
    except AppError as exc:
        return _transcribe_redirect_response(
            message=exc.message,
            message_kind="error",
            queued_transcript_id=transcript_id,
        )
    except Exception:
        if document is not None:
            mark_generated_document_enqueue_failed_service(db, document_id=document.id, message="Could not enqueue note generation")
        return _transcribe_redirect_response(
            message="Could not enqueue note generation",
            message_kind="error",
            queued_transcript_id=transcript_id,
        )
    return RedirectResponse(
        url=f"/transcribe?transcript_id={transcript_id}&tab=output&message=Queued+note+generation.&message_kind=success",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/transcribe/generate-followup", response_class=HTMLResponse)
@LLM_GENERATION_DAILY_RATE_LIMIT
@LLM_GENERATION_BURST_RATE_LIMIT
def transcribe_generate_followup(
    request: Request,
    transcript_id: UUID = Form(...),
    prompt_text: str = Form(...),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    document = None
    try:
        document = queue_followup_generation_service(db, context.user, transcript_id=transcript_id, prompt_text=prompt_text)
        task_result = main_module.enqueue_generated_document_job(document_id=document.id)
        attach_generated_document_task_id_service(db, document_id=document.id, task_id=getattr(task_result, "id", None))
    except AppError as exc:
        return RedirectResponse(
            url=f"/transcribe?{urlencode({'transcript_id': str(transcript_id), 'tab': 'followups', 'message': exc.message, 'message_kind': 'error'})}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except Exception:
        if document is not None:
            mark_generated_document_enqueue_failed_service(db, document_id=document.id, message="Could not enqueue follow-up generation")
        return RedirectResponse(
            url=f"/transcribe?{urlencode({'transcript_id': str(transcript_id), 'tab': 'followups', 'message': 'Could not enqueue follow-up generation.', 'message_kind': 'error'})}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=f"/transcribe?{urlencode({'transcript_id': str(transcript_id), 'tab': 'followups', 'message': 'Queued follow-up generation.', 'message_kind': 'success'})}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/transcribe/run-quick-action", response_class=HTMLResponse)
@LLM_GENERATION_DAILY_RATE_LIMIT
@LLM_GENERATION_BURST_RATE_LIMIT
def transcribe_run_quick_action(
    request: Request,
    transcript_id: UUID = Form(...),
    quick_action_id: UUID = Form(...),
    context_text: str = Form("", alias="quick_action_context_text"),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    clean_context_text = (context_text or "").strip()
    if len(clean_context_text) > 4000:
        return RedirectResponse(
            url=f"/transcribe?{urlencode({'transcript_id': str(transcript_id), 'tab': 'followups', 'message': 'Additional context must be 4000 characters or fewer', 'message_kind': 'error'})}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    document = None
    try:
        document = queue_quick_action_generation_service(
            db,
            context.user,
            transcript_id=transcript_id,
            quick_action_id=quick_action_id,
            context_text=clean_context_text,
        )
        task_result = main_module.enqueue_generated_document_job(document_id=document.id)
        attach_generated_document_task_id_service(db, document_id=document.id, task_id=getattr(task_result, "id", None))
    except AppError as exc:
        return RedirectResponse(
            url=f"/transcribe?{urlencode({'transcript_id': str(transcript_id), 'tab': 'followups', 'message': exc.message, 'message_kind': 'error'})}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except Exception:
        if document is not None:
            mark_generated_document_enqueue_failed_service(db, document_id=document.id, message="Could not enqueue quick action generation")
        return RedirectResponse(
            url=f"/transcribe?{urlencode({'transcript_id': str(transcript_id), 'tab': 'followups', 'message': 'Could not enqueue quick action generation.', 'message_kind': 'error'})}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=f"/transcribe?{urlencode({'transcript_id': str(transcript_id), 'tab': 'followups', 'message': 'Queued quick action generation.', 'message_kind': 'success'})}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/transcribe/sessions", response_class=HTMLResponse)
def transcribe_create_session(
    request: Request,
    title: str = Form(""),
    ingestion_mode: TranscriptIngestionMode = Form(default=TranscriptIngestionMode.whole_file),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        transcript = start_transcript_service(
            db,
            context.user,
            TranscriptStart(
                title=title or "Untitled session",
                ingestion_mode=ingestion_mode,
            ),
        )
    except AppError as exc:
        return _render_transcribe_page(
            request,
            db,
            current_user=context.user,
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
        )
    return RedirectResponse(url=f"/transcribe?transcript_id={transcript.id}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/transcribe/sessions/delete", response_class=HTMLResponse)
def transcribe_delete_sessions(
    request: Request,
    transcript_ids: list[str] = Form(default=[]),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response

    try:
        delete_transcripts_service(
            db,
            context.user,
            transcript_ids=[UUID(transcript_id) for transcript_id in transcript_ids],
        )
    except (ValueError, AppError) as exc:
        detail = exc.message if isinstance(exc, AppError) else "Invalid transcript selection"
        status_code = exc.status_code if isinstance(exc, AppError) else status.HTTP_400_BAD_REQUEST
        return _render_transcribe_page(
            request,
            db,
            current_user=context.user,
            message=detail,
            message_kind="error",
            status_code=status_code,
        )
    return RedirectResponse(url="/transcribe", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/transcribe/sessions/{transcript_id}/title", response_class=HTMLResponse)
def transcribe_update_session_title(
    request: Request,
    transcript_id: UUID,
    title: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        update_transcript_title_service(
            db,
            context.user,
            transcript_id=transcript_id,
            title=title,
        )
    except AppError as exc:
        return _render_transcribe_page(
            request,
            db,
            current_user=context.user,
            transcript_id=str(transcript_id),
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
        )
    return RedirectResponse(url=f"/transcribe?transcript_id={transcript_id}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/transcribe/sessions/{transcript_id}/mode", response_class=HTMLResponse)
def transcribe_update_session_mode(
    request: Request,
    transcript_id: UUID,
    ingestion_mode: TranscriptIngestionMode = Form(...),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        update_transcript_service(
            db,
            context.user,
            transcript_id=transcript_id,
            title=None,
            ingestion_mode=ingestion_mode,
            structured_context_json=None,
        )
    except AppError as exc:
        return _render_transcribe_page(
            request,
            db,
            current_user=context.user,
            transcript_id=str(transcript_id),
            message=exc.message,
            message_kind="error",
            status_code=exc.status_code,
        )
    return RedirectResponse(url=f"/transcribe?transcript_id={transcript_id}", status_code=status.HTTP_303_SEE_OTHER)
