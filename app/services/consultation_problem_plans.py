"""Owner-only persistence for a transcript-root living problem plan."""

from __future__ import annotations

import json
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (ConsultationProblemPlan, ConsultationSplitAnalysis, ConsultationSplitBatch,
    ConsultationSplitBatchStatus, ConsultationSplitExecution, ConsultationSplitExecutionStatus,
    ConsultationSplitIntent, ConsultationSplitIntentStatus, ConsultationSplitTopicDisposition,
    GeneratedDocument, GeneratedDocumentStatus, PromptTemplate, PromptTemplateVersion,
    TaskDispatchKind, TeamRole, User, utcnow)
from app.schemas.consultation_split import (ConsultationProblemPlanCreateResponse,
    ConsultationProblemPlanProjection, ConsultationProblemPlanReplace)
from app.services.content_crypto import decrypt_json_for_owner, encrypt_json_for_owner
from app.services.consultation_split_drafts import _latest_accessible_template
from app.services.consultation_split_gates import consultation_splitting_available
from app.services.consultation_split_locks import lock_consultation_split_source_scope
from app.services.consultation_splits import require_split_owner_transcript
from app.services.transcripts import transcript_is_expired

_TABLE = "consultation_problem_plans"


def _owner(actor: User) -> None:
    if (not consultation_splitting_available(actor) or actor.is_system_admin or actor.team_id is None
            or actor.team_role not in {TeamRole.user, TeamRole.leader}):
        raise AppError(403, "forbidden", "Consultation split content is restricted to the owning user")


def _decrypt(db: Session, owner: User, row: ConsultationProblemPlan) -> dict:
    value = decrypt_json_for_owner(db, owner_user_id=owner.id, table=_TABLE,
                                  field="plan_encrypted", record_id=row.id,
                                  stored_value=row.plan_encrypted)
    if not isinstance(value, dict) or set(value) != {"tiles", "problems"}:
        raise AppError(500, "consultation_problem_plan_invalid", "Consultation plan is unavailable")
    return value


def _projection(db: Session, owner: User, row: ConsultationProblemPlan | None) -> ConsultationProblemPlanProjection:
    if row is None:
        return ConsultationProblemPlanProjection(plan_id=None, revision=0, manual_override=False, tiles=[], problems=[])
    value = _decrypt(db, owner, row)
    suggestion = None
    if row.suggestion_intent_id is not None and row.suggestion_analysis_id is not None and row.suggestion_status:
        suggestion = {"intent_id": row.suggestion_intent_id, "analysis_id": row.suggestion_analysis_id,
                      "status": row.suggestion_status, "error_code": row.suggestion_error_code}
    return ConsultationProblemPlanProjection(
        plan_id=row.id, revision=row.revision, manual_override=row.manual_override,
        tiles=value["tiles"], problems=value["problems"], suggestion=suggestion,
    )


def read_consultation_problem_plan(db: Session, actor: User, *, transcript_id: UUID) -> ConsultationProblemPlanProjection:
    _owner(actor)
    transcript = require_split_owner_transcript(db, actor, transcript_id=transcript_id)
    row = db.scalar(select(ConsultationProblemPlan).where(
        ConsultationProblemPlan.owner_user_id == actor.id,
        ConsultationProblemPlan.team_id == actor.team_id,
        ConsultationProblemPlan.transcript_id == transcript.id,
        ConsultationProblemPlan.retention_expires_at == transcript.retention_expires_at,
    ))
    return _projection(db, actor, row)


def ensure_empty_problem_plan_for_suggestion(
    db: Session, actor: User, *, transcript_id: UUID, expected_revision: int,
) -> ConsultationProblemPlan:
    """Create the revision-zero storage root only when Auto suggest is submitted."""
    _owner(actor)
    scope = lock_consultation_split_source_scope(db, owner_user_id=actor.id, transcript_id=transcript_id)
    if scope is None or transcript_is_expired(scope.transcript):
        raise AppError(404, "not_found", "Transcript not found", {"resource": "transcript"})
    row = db.scalar(select(ConsultationProblemPlan).where(
        ConsultationProblemPlan.transcript_id == scope.transcript.id,
        ConsultationProblemPlan.owner_user_id == scope.owner.id,
        ConsultationProblemPlan.team_id == scope.owner.team_id,
    ).with_for_update())
    if row is None:
        if expected_revision != 0:
            raise AppError(409, "consultation_problem_plan_conflict", "The consultation plan changed elsewhere")
        now = utcnow()
        row = ConsultationProblemPlan(
            id=uuid4(), owner_user_id=scope.owner.id, team_id=scope.owner.team_id,
            transcript_id=scope.transcript.id, retention_expires_at=scope.transcript.retention_expires_at,
            revision=0, manual_override=False, created_at=now, updated_at=now,
        )
        row.plan_encrypted = encrypt_json_for_owner(
            db, owner_user_id=scope.owner.id, table=_TABLE, field="plan_encrypted",
            record_id=row.id, plaintext={"tiles": [], "problems": []},
        ) or ""
        db.add(row)
        db.flush()
    elif row.revision != expected_revision:
        raise AppError(409, "consultation_problem_plan_conflict", "The consultation plan changed elsewhere")
    db.commit()
    return row


def start_consultation_problem_suggestion(
    db: Session, actor: User, *, transcript_id: UUID, client_idempotency_key: UUID,
    expected_revision: int, selected_template_id: UUID, retry_failed_analysis: bool = False,
):
    """Queue/replay the existing analysis workflow as an Auto suggest action."""
    from app.services.consultation_split_intents import (
        _existing_intent, _safe_replay_result, create_or_replay_consultation_split_intent,
    )
    existing = _existing_intent(db, owner_user_id=actor.id, client_idempotency_key=client_idempotency_key)
    if existing is not None:
        if existing.intent_kind != "problem_suggestion" or existing.transcript_id != transcript_id:
            raise AppError(409, "consultation_problem_plan_conflict", "This suggestion key is unavailable")
        result = _safe_replay_result(db, actor=actor, intent=existing)
        db.commit()
        return result
    plan = ensure_empty_problem_plan_for_suggestion(
        db, actor, transcript_id=transcript_id, expected_revision=expected_revision,
    )
    return create_or_replay_consultation_split_intent(
        db, actor, transcript_id=transcript_id,
        client_idempotency_key=client_idempotency_key,
        selected_template_id=selected_template_id, manual_review_requested=True,
        retry_failed_analysis=retry_failed_analysis, problem_plan_id=plan.id,
        problem_plan_revision=expected_revision, intent_kind="problem_suggestion",
    )


def apply_problem_plan_suggestion(
    db: Session, actor: User, *, intent_id: UUID, analysis: ConsultationSplitAnalysis,
) -> bool:
    """Apply a successful analysis only to the unchanged revision it started from."""
    from app.services.consultation_splits import read_split_analysis_json
    from app.services.consultation_split_intents import _submitted_generation_snapshot
    from app.services.consultation_split_analysis import ValidatedSplitAnalysis

    _owner(actor)
    scope = lock_consultation_split_source_scope(
        db, owner_user_id=actor.id, transcript_id=analysis.transcript_id
    )
    if scope is None or transcript_is_expired(scope.transcript):
        return False
    intent = db.get(ConsultationSplitIntent, intent_id)
    if intent is None or intent.intent_kind != "problem_suggestion":
        return False
    row = db.scalar(select(ConsultationProblemPlan).where(
        ConsultationProblemPlan.id == intent.problem_plan_id,
        ConsultationProblemPlan.owner_user_id == actor.id,
        ConsultationProblemPlan.team_id == actor.team_id,
        ConsultationProblemPlan.transcript_id == analysis.transcript_id,
        ConsultationProblemPlan.retention_expires_at == scope.transcript.retention_expires_at,
    ).with_for_update())
    if row is None or intent.problem_plan_revision is None or row.revision != intent.problem_plan_revision:
        intent.status = ConsultationSplitIntentStatus.failed
        intent.error_code = "consultation_problem_plan_conflict"
        intent.completed_at = utcnow()
        if row is not None and row.suggestion_intent_id == intent.id and row.suggestion_base_revision == intent.problem_plan_revision:
            row.suggestion_status = "failed"
            row.suggestion_error_code = "consultation_problem_plan_conflict"
        return False
    current = _decrypt(db, actor, row)
    ignored = [p for p in current["problems"] if p["ignored"]]
    ignored_keys = {" ".join(p["title"].split()).casefold() for p in ignored}
    ignored_ids = {p["uuid"] for p in ignored}
    from app.services.consultation_split_sources import current_consultation_split_analysis_source_matches
    if not current_consultation_split_analysis_source_matches(db, actor, transcript=scope.transcript, analysis=analysis):
        intent.status = ConsultationSplitIntentStatus.failed
        intent.error_code = "consultation_split_source_stale"
        intent.completed_at = utcnow()
        if row.suggestion_intent_id == intent.id and row.suggestion_base_revision == intent.problem_plan_revision:
            row.suggestion_status = "failed"
            row.suggestion_error_code = "consultation_split_source_stale"
        return False
    proposal_value = read_split_analysis_json(db, actor, analysis=analysis, field="proposal_encrypted")
    if proposal_value is None and analysis.status.value != "not_required":
        row.suggestion_status = "failed"
        row.suggestion_error_code = "consultation_split_proposal_unavailable"
        return False
    proposal = ValidatedSplitAnalysis.model_validate(proposal_value) if proposal_value is not None else None
    submitted = _submitted_generation_snapshot(db, intent=intent)
    fallback_template_id = submitted["template_id"]
    proposed = [t for t in (proposal.topics if proposal is not None else []) if str(t.topic_uuid) not in ignored_ids
                and " ".join(t.title.split()).casefold() not in ignored_keys]
    if len(ignored) + len(proposed) > 6 or sum(t.disposition == "separate_note" for t in proposed) > 6:
        intent.status = ConsultationSplitIntentStatus.failed
        intent.error_code = "consultation_split_proposal_unavailable"
        intent.completed_at = utcnow()
        if row.suggestion_intent_id == intent.id and row.suggestion_base_revision == intent.problem_plan_revision:
            row.suggestion_status = "failed"
            row.suggestion_error_code = "consultation_split_proposal_unavailable"
        return False
    primary_proposal = next((t for t in proposed if t.is_primary and t.disposition == "separate_note"), None)
    previous_primary = next((t for t in current["tiles"] if t["is_primary"]), None)
    primary_uuid = str(uuid4()) if primary_proposal else (
        previous_primary["uuid"] if previous_primary else str(uuid4())
    )
    primary_template_id = primary_proposal.template_id if primary_proposal and primary_proposal.template_id else (
        previous_primary.get("template_id") if previous_primary else str(fallback_template_id)
    )
    if isinstance(primary_template_id, str):
        primary_template_id = UUID(primary_template_id)
    tiles = []
    if primary_template_id is not None:
        template_id, version_id = _latest_accessible_template(db, actor, template_id=primary_template_id, required=True)
    else:
        template_id = version_id = None
    tiles.append({"uuid": primary_uuid, "is_primary": True,
                  "template_id": str(template_id) if template_id else None,
                  "template_version_id": str(version_id) if version_id else None})
    problems = list(ignored)
    for topic in proposed:
        if topic.disposition == "exclude_from_notes":
            problems.append({"uuid": str(topic.topic_uuid), "title": topic.title, "origin": "suggested",
                             "ignored": True, "group_id": None, "restore_group_id": primary_uuid})
            continue
        group_id = primary_uuid
        if topic.disposition == "separate_note" and not topic.is_primary:
            group_id = str(topic.topic_uuid)
            template_id_for_topic = topic.template_id or fallback_template_id
            template_id, version_id = _latest_accessible_template(
                db, actor, template_id=template_id_for_topic, required=True
            )
            tiles.append({"uuid": group_id, "is_primary": False,
                          "template_id": str(template_id), "template_version_id": str(version_id)})
        problems.append({"uuid": str(topic.topic_uuid), "title": topic.title, "origin": "suggested",
                         "ignored": False, "group_id": group_id, "restore_group_id": None})
    # Preserve ignored exclusions even if a provider proposal has no topics.
    row.plan_encrypted = encrypt_json_for_owner(
        db, owner_user_id=actor.id, table=_TABLE, field="plan_encrypted", record_id=row.id,
        plaintext={"tiles": tiles, "problems": problems},
    ) or ""
    row.last_operation_id = None
    row.last_operation_base_revision = None
    row.last_operation_receipt_encrypted = None
    row.revision += 1
    row.updated_at = max(utcnow(), row.updated_at + timedelta(microseconds=1))
    row.suggestion_status = "ready"
    row.suggestion_error_code = None
    intent.status = ConsultationSplitIntentStatus.confirmed
    db.flush()
    return True


def sync_problem_plan_from_split_draft_detail(
    db: Session, actor: User, *, transcript_id: UUID, detail,
) -> None:
    """Bridge a pre-living-plan analysis draft once, without overriding edits."""
    _owner(actor)
    scope = lock_consultation_split_source_scope(db, owner_user_id=actor.id, transcript_id=transcript_id)
    if scope is None or transcript_is_expired(scope.transcript):
        return
    row = db.scalar(select(ConsultationProblemPlan).where(
        ConsultationProblemPlan.owner_user_id == actor.id,
        ConsultationProblemPlan.team_id == actor.team_id,
        ConsultationProblemPlan.transcript_id == scope.transcript.id,
        ConsultationProblemPlan.retention_expires_at == scope.transcript.retention_expires_at,
    ).with_for_update())
    if row is not None and (row.revision != 0 or row.manual_override):
        return
    separate = [topic for topic in detail.topics if topic.disposition == "separate_note"]
    primary = next((topic for topic in separate if topic.is_primary), None)
    if primary is None:
        return
    tiles = [{"uuid": str(topic.topic_uuid), "is_primary": topic.is_primary,
              "template_id": str(topic.template_id) if topic.template_id else None,
              "template_version_id": str(topic.template_version_id) if topic.template_version_id else None}
             for topic in separate]
    problems = []
    for topic in detail.topics:
        ignored = topic.disposition == "exclude_from_notes"
        group = None if ignored else (str(topic.topic_uuid) if topic.disposition == "separate_note" and not topic.is_primary else str(primary.topic_uuid))
        problems.append({"uuid": str(topic.topic_uuid), "title": topic.title, "origin": "suggested",
                         "ignored": ignored, "group_id": group,
                         "restore_group_id": str(primary.topic_uuid) if ignored else None})
    now = utcnow()
    if row is None:
        row = ConsultationProblemPlan(id=uuid4(), owner_user_id=actor.id, team_id=actor.team_id,
            transcript_id=scope.transcript.id, retention_expires_at=scope.transcript.retention_expires_at,
            revision=1, manual_override=False, created_at=now, updated_at=now)
        db.add(row)
    else:
        row.revision = 1
        row.updated_at = max(now, row.updated_at + timedelta(microseconds=1))
    row.plan_encrypted = encrypt_json_for_owner(db, owner_user_id=actor.id, table=_TABLE,
        field="plan_encrypted", record_id=row.id, plaintext={"tiles": tiles, "problems": problems}) or ""
    db.flush()
    db.commit()


def _normalized_request(payload: ConsultationProblemPlanReplace) -> str:
    return json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def create_consultation_notes_from_problem_plan(
    db: Session, actor: User, *, transcript_id: UUID, client_idempotency_key: UUID,
    expected_revision: int,
) -> ConsultationProblemPlanCreateResponse:
    """Freeze a living plan against current redacted sources and queue one batch."""
    from app.services.consultation_split_sources import prepare_source_bound_consultation_split_analysis, resolve_ready_consultation_split_analysis_source_state
    from app.services.consultation_split_confirmation import _locked_template_snapshots
    from app.services.consultation_split_intents import _existing_intent, _insert_or_replay_intent
    from app.services.consultation_split_generation_queue import queue_confirmed_split_generation
    from app.services.consultation_splits import create_split_batch, create_split_batch_topic, create_split_topic_outcome
    from app.services.transcripts import assert_no_unresolved_stream_session, snapshot_current_transcript_version
    from app.services.task_outbox import try_publish_task_dispatch_safely
    from app.services.templates import _note_generation_options_for_user

    _owner(actor)
    replay = _existing_intent(db, owner_user_id=actor.id, client_idempotency_key=client_idempotency_key)
    if replay is not None:
        if replay.intent_kind != "problem_plan_create" or replay.transcript_id != transcript_id:
            raise AppError(409, "consultation_problem_plan_conflict", "This create key is unavailable")
        batch = db.scalar(select(ConsultationSplitBatch).where(ConsultationSplitBatch.intent_id == replay.id))
        if batch is None:
            raise AppError(409, "consultation_problem_plan_conflict", "This create key is unavailable")
        from app.services.consultation_splits import _require_lineage
        _require_lineage(db, actor, batch)
        if batch.owner_user_id != actor.id or batch.team_id != actor.team_id or batch.transcript_id != transcript_id:
            raise AppError(409, "consultation_problem_plan_conflict", "This create key is unavailable")
        db.commit()
        return ConsultationProblemPlanCreateResponse(batch_id=batch.id, status="generation_queued",
            separate_note_count=len(batch.topics), topic_count=len(batch.topics), created_at=batch.created_at,
            updated_at=batch.updated_at, idempotency_replayed=True)

    scope = lock_consultation_split_source_scope(db, owner_user_id=actor.id, transcript_id=transcript_id)
    if scope is None or transcript_is_expired(scope.transcript):
        raise AppError(404, "not_found", "Transcript not found", {"resource": "transcript"})
    transcript = scope.transcript
    plan_row = db.scalar(select(ConsultationProblemPlan).where(
        ConsultationProblemPlan.owner_user_id == actor.id, ConsultationProblemPlan.team_id == actor.team_id,
        ConsultationProblemPlan.transcript_id == transcript.id,
        ConsultationProblemPlan.retention_expires_at == transcript.retention_expires_at,
    ).with_for_update())
    if plan_row is None or plan_row.revision != expected_revision or expected_revision < 1:
        raise AppError(409, "consultation_problem_plan_conflict", "The consultation plan changed elsewhere")
    plan_value = _decrypt(db, actor, plan_row)
    tiles = plan_value["tiles"]
    problems = plan_value["problems"]
    active = [item for item in problems if not item["ignored"]]
    if not tiles or sum(bool(tile["is_primary"]) for tile in tiles) != 1:
        raise AppError(422, "validation_error", "Add a primary note before creating notes")
    primary = next(tile for tile in tiles if tile["is_primary"])
    if any(not item.get("group_id") for item in active):
        raise AppError(422, "validation_error", "Assign each active problem to a note before creating notes")

    # Refuse another live split attempt, including recovery/verifying work, and
    # ordinary document generation under the same transcript root.
    live_execution = db.scalar(select(ConsultationSplitExecution.id).where(
        ConsultationSplitExecution.owner_user_id == actor.id,
        ConsultationSplitExecution.transcript_id == transcript.id,
        ConsultationSplitExecution.status.in_([ConsultationSplitExecutionStatus.queued, ConsultationSplitExecutionStatus.processing]),
    ).limit(1))
    live_document = db.scalar(select(GeneratedDocument.id).where(
        GeneratedDocument.owner_user_id == actor.id, GeneratedDocument.transcript_id == transcript.id,
        GeneratedDocument.status.in_([GeneratedDocumentStatus.queued, GeneratedDocumentStatus.processing]),
    ).limit(1))
    if live_execution is not None or live_document is not None:
        raise AppError(409, "consultation_split_generation_in_progress", "Wait for the current note generation to finish")
    assert_no_unresolved_stream_session(db, transcript_id=transcript.id)
    db.commit()  # source preparation may cross required and dynamic redaction boundaries
    prepared = prepare_source_bound_consultation_split_analysis(db, actor, transcript_id=transcript.id)
    source_snapshot = prepared.source_snapshot
    db.commit()  # release all canonical locks before dynamic problem-title redaction
    from app.services.consultation_problem_plan_sources import redact_problem_plan_titles
    redacted_by_uuid, merged_phi = redact_problem_plan_titles(
        db, actor, transcript_id=transcript.id, source_snapshot=source_snapshot, problems=problems)
    scope = lock_consultation_split_source_scope(db, owner_user_id=actor.id, transcript_id=transcript.id)
    if scope is None or scope.transcript.retention_expires_at != prepared.retention_expires_at:
        raise AppError(409, "consultation_split_source_stale", "Consultation sources changed before note creation")
    concurrent_replay = _existing_intent(
        db, owner_user_id=actor.id, client_idempotency_key=client_idempotency_key, lock=True,
    )
    if concurrent_replay is not None:
        if concurrent_replay.intent_kind != "problem_plan_create" or concurrent_replay.transcript_id != transcript_id:
            raise AppError(409, "consultation_problem_plan_conflict", "This create key is unavailable")
        replay_batch = db.scalar(select(ConsultationSplitBatch).where(ConsultationSplitBatch.intent_id == concurrent_replay.id))
        if replay_batch is None:
            raise AppError(409, "consultation_problem_plan_conflict", "This create key is unavailable")
        from app.services.consultation_splits import _require_lineage
        _require_lineage(db, actor, replay_batch)
        db.commit()
        return ConsultationProblemPlanCreateResponse(batch_id=replay_batch.id, status="generation_queued",
            separate_note_count=len(replay_batch.topics), topic_count=len(replay_batch.topics),
            created_at=replay_batch.created_at, updated_at=replay_batch.updated_at, idempotency_replayed=True)
    assert_no_unresolved_stream_session(db, transcript_id=transcript.id)
    if db.scalar(select(ConsultationSplitExecution.id).where(
        ConsultationSplitExecution.owner_user_id == actor.id,
        ConsultationSplitExecution.transcript_id == transcript.id,
        ConsultationSplitExecution.status.in_([ConsultationSplitExecutionStatus.queued, ConsultationSplitExecutionStatus.processing]),
    ).limit(1)) is not None or db.scalar(select(GeneratedDocument.id).where(
        GeneratedDocument.owner_user_id == actor.id, GeneratedDocument.transcript_id == transcript.id,
        GeneratedDocument.status.in_([GeneratedDocumentStatus.queued, GeneratedDocumentStatus.processing]),
    ).limit(1)) is not None:
        raise AppError(409, "consultation_split_generation_in_progress", "Wait for the current note generation to finish")
    plan_row = db.scalar(select(ConsultationProblemPlan).where(
        ConsultationProblemPlan.id == plan_row.id,
        ConsultationProblemPlan.owner_user_id == actor.id,
        ConsultationProblemPlan.transcript_id == transcript.id,
    ).with_for_update())
    if plan_row is None or plan_row.revision != expected_revision:
        raise AppError(409, "consultation_problem_plan_conflict", "The consultation plan changed elsewhere")
    current_state = resolve_ready_consultation_split_analysis_source_state(
        db, actor, transcript_id=transcript.id,
        transcript_version_id=prepared.source_state.transcript_version_id,
        redaction_run_id=prepared.source_state.redaction_run_id,
        force_no_transcript_version=prepared.source_state.transcript_version_id is None,
    )
    if current_state.source_fingerprint != prepared.source_state.source_fingerprint:
        raise AppError(409, "consultation_split_source_stale", "Consultation sources changed before note creation")

    requested: dict[UUID, UUID] = {}
    for tile in tiles:
        if tile.get("template_id") is None or tile.get("template_version_id") is None:
            if tile["is_primary"]:
                raise AppError(422, "validation_error", "Choose a template for the primary note")
            raise AppError(422, "validation_error", "Choose a template for each note")
        tid, vid = UUID(tile["template_id"]), UUID(tile["template_version_id"])
        if tid in requested and requested[tid] != vid:
            raise AppError(409, "consultation_split_template_unavailable", "A selected template has inconsistent versions")
        requested[tid] = vid
    template_rows = _locked_template_snapshots(db, actor, requested=requested)
    tile_plans = []
    for order, tile in enumerate(tiles):
        template = template_rows[UUID(tile["template_id"])].value
        assigned = [redacted_by_uuid[problem["uuid"]] for problem in active if problem["group_id"] == tile["uuid"]]
        tile_plans.append({"topic_uuid": tile["uuid"], "order": order, "is_primary": bool(tile["is_primary"]),
            "template": template, "problem_scope": assigned,
            "include_unassigned_source": bool(tile["is_primary"])})
    redacted_ignored = [redacted_by_uuid[item["uuid"]] for item in problems if item["ignored"]]
    plan = {"version": 2, "intent_id": "", "source_lineage": {"problem_plan_id": str(plan_row.id), "revision": expected_revision},
        "notes": tile_plans, "ignored_problems": redacted_ignored}
    primary_template_id = UUID(primary["template_id"])
    intent_snapshot = {
        "selected_template": template_rows[primary_template_id].value,
        "generation_configuration": dict(_note_generation_options_for_user(db, user_id=actor.id)),
    }
    intent, replay_result = _insert_or_replay_intent(db, actor=actor, owner_user_id=actor.id, team_id=actor.team_id,
        transcript_id=transcript.id, retention_expires_at=transcript.retention_expires_at, analysis_id=None,
        problem_plan_id=plan_row.id, problem_plan_revision=expected_revision, intent_kind="problem_plan_create",
        snapshot=intent_snapshot, manual_review_requested=True, client_idempotency_key=client_idempotency_key)
    if replay_result is not None:
        batch = db.scalar(select(ConsultationSplitBatch).where(ConsultationSplitBatch.intent_id == replay_result.intent.id))
        db.commit()
        if batch is None:
            raise AppError(409, "consultation_problem_plan_conflict", "This create key is unavailable")
        return ConsultationProblemPlanCreateResponse(batch_id=batch.id, status="generation_queued", separate_note_count=len(batch.topics), topic_count=len(batch.topics), created_at=batch.created_at, updated_at=batch.updated_at, idempotency_replayed=True)
    plan["intent_id"] = str(intent.id)
    source_snapshot["phi_index"] = merged_phi
    pii_snapshot = {"manual_pii": source_snapshot.get("source_state", {}).get("manual_pii", []), "phi_index": merged_phi}
    materialization_version = snapshot_current_transcript_version(db, transcript=scope.transcript, allow_empty=True, mark_transcript_ready=False)
    batch = create_split_batch(db, actor, problem_plan=plan_row, problem_plan_revision=expected_revision,
        source_fingerprint=prepared.source_state.source_fingerprint, intent_id=intent.id, confirmed_plan=plan,
        clinical_snapshot={"clinical_nlp_hints": source_snapshot.get("clinical_nlp_hints", [])},
        source_snapshot=source_snapshot,
        template_snapshot={"topic_templates": {str(key): value.value for key, value in template_rows.items()}},
        pii_snapshot=pii_snapshot, provider_snapshot={}, note_options_snapshot=dict(_note_generation_options_for_user(db, user_id=actor.id)),
        materialization_transcript_version_id=materialization_version.id)
    for order, tile_plan in enumerate(tile_plans):
        original_titles = [item["title"] for item in active if item["group_id"] == tile_plan["topic_uuid"]]
        label = ", ".join(original_titles) if original_titles else "Primary note"
        topic = create_split_batch_topic(db, actor, batch=batch, title=label[:255], topic_order=order,
            is_primary=tile_plan["is_primary"], disposition=ConsultationSplitTopicDisposition.separate_note,
            template_snapshot=tile_plan["template"], topic_uuid=UUID(tile_plan["topic_uuid"]))
        create_split_topic_outcome(db, actor, batch_topic=topic)
    intent.status = ConsultationSplitIntentStatus.confirmed
    dispatch = queue_confirmed_split_generation(db, batch=batch)
    db.commit()
    if dispatch.dispatch_task_id is not None:
        try_publish_task_dispatch_safely(dispatch.dispatch_task_id)
    return ConsultationProblemPlanCreateResponse(batch_id=batch.id, status="generation_queued", separate_note_count=len(tiles),
        topic_count=len(tiles), created_at=batch.created_at, updated_at=batch.updated_at, idempotency_replayed=False)


def _validate_plan(db: Session, actor: User, payload: ConsultationProblemPlanReplace, current: dict | None) -> dict:
    if not 1 <= len(payload.tiles) <= 6 or len(payload.problems) > 6:
        raise AppError(422, "validation_error", "A plan requires one to six notes and up to six problems")
    tile_ids = [tile.uuid for tile in payload.tiles]
    problem_ids = [problem.uuid for problem in payload.problems]
    if len(set(tile_ids)) != len(tile_ids) or len(set(problem_ids)) != len(problem_ids):
        raise AppError(422, "validation_error", "Plan identifiers must be distinct")
    if sum(tile.is_primary for tile in payload.tiles) != 1:
        raise AppError(422, "validation_error", "Exactly one note must be primary")
    by_id = {tile.uuid for tile in payload.tiles}
    old_problems = {p["uuid"]: p for p in (current or {}).get("problems", [])}
    problems = []
    seen_titles: set[str] = set()
    for item in payload.problems:
        title = " ".join(item.title.split())
        title_key = title.casefold()
        if not title or len(title) > 255 or title_key in seen_titles:
            raise AppError(422, "validation_error", "Problem titles must be nonempty and distinct")
        seen_titles.add(title_key)
        if not item.ignored and item.group_id is not None and item.group_id not in by_id:
            raise AppError(422, "validation_error", "A problem must refer to a note in this plan")
        # Row UUIDs are identifiers, never authority. New user rows are manual;
        # an existing AI origin is retained for display.
        old = old_problems.get(str(item.uuid))
        origin = old["origin"] if old else "manual"
        problems.append({"uuid": str(item.uuid), "title": title, "origin": origin,
                         "ignored": item.ignored,
                         "group_id": str(item.group_id) if item.group_id and not item.ignored else None,
                         "restore_group_id": str(item.restore_group_id) if item.restore_group_id else None})

    tiles = []
    for tile in payload.tiles:
        if tile.template_id is None:
            if not tile.is_primary:
                raise AppError(422, "validation_error", "A secondary note requires an available template")
            template_id = version_id = None
        else:
            template_id, version_id = _latest_accessible_template(
                db, actor, template_id=tile.template_id, required=True
            )
        tiles.append({"uuid": str(tile.uuid), "is_primary": tile.is_primary,
                      "template_id": str(template_id) if template_id else None,
                      "template_version_id": str(version_id) if version_id else None})
    active_group_ids = {p["group_id"] for p in problems if not p["ignored"]}
    if any(p["group_id"] is None and not p["ignored"] for p in problems):
        raise AppError(422, "validation_error", "Every active problem must belong to a note")
    primary_id = next(t["uuid"] for t in tiles if t["is_primary"])
    if any(t["uuid"] != primary_id and t["uuid"] not in active_group_ids for t in tiles):
        raise AppError(422, "validation_error", "A secondary note must contain an active problem")
    return {"tiles": tiles, "problems": problems}


def replace_consultation_problem_plan(
    db: Session, actor: User, *, transcript_id: UUID, payload: ConsultationProblemPlanReplace,
) -> ConsultationProblemPlanProjection:
    _owner(actor)
    scope = lock_consultation_split_source_scope(db, owner_user_id=actor.id, transcript_id=transcript_id)
    if scope is None or transcript_is_expired(scope.transcript):
        raise AppError(404, "not_found", "Transcript not found", {"resource": "transcript"})
    owner, transcript = scope.owner, scope.transcript
    row = db.scalar(select(ConsultationProblemPlan).where(
        ConsultationProblemPlan.transcript_id == transcript.id,
        ConsultationProblemPlan.owner_user_id == owner.id,
        ConsultationProblemPlan.team_id == owner.team_id,
    ).with_for_update())
    submitted = _normalized_request(payload)
    if row is not None and row.last_operation_id == payload.operation_id:
        receipt = decrypt_json_for_owner(
            db, owner_user_id=owner.id, table=_TABLE, field="last_operation_receipt_encrypted",
            record_id=row.id, stored_value=row.last_operation_receipt_encrypted or "",
        )
        if not isinstance(receipt, dict) or receipt.get("request") != submitted:
            raise AppError(409, "consultation_problem_plan_conflict", "This plan operation was already used")
        result = _projection(db, owner, row)
        db.commit()
        return result
    current_revision = row.revision if row else 0
    if payload.expected_revision != current_revision:
        raise AppError(409, "consultation_problem_plan_conflict", "The consultation plan changed elsewhere")
    current = _decrypt(db, owner, row) if row else None
    plan = _validate_plan(db, owner, payload, current)
    now = utcnow()
    if row is None:
        row = ConsultationProblemPlan(
            id=uuid4(), owner_user_id=owner.id, team_id=owner.team_id,
            transcript_id=transcript.id, retention_expires_at=transcript.retention_expires_at,
            revision=1, manual_override=True, last_operation_id=payload.operation_id,
            last_operation_base_revision=0, created_at=now, updated_at=now,
        )
        row.plan_encrypted = encrypt_json_for_owner(
            db, owner_user_id=owner.id, table=_TABLE, field="plan_encrypted",
            record_id=row.id, plaintext=plan,
        ) or ""
        row.last_operation_receipt_encrypted = encrypt_json_for_owner(
            db, owner_user_id=owner.id, table=_TABLE, field="last_operation_receipt_encrypted",
            record_id=row.id, plaintext={"request": submitted},
        ) or ""
        db.add(row)
        db.flush()
    else:
        row.revision += 1
        row.manual_override = True
        row.last_operation_id = payload.operation_id
        row.last_operation_base_revision = payload.expected_revision
        row.updated_at = max(now, row.updated_at + timedelta(microseconds=1))
        row.plan_encrypted = encrypt_json_for_owner(
            db, owner_user_id=owner.id, table=_TABLE, field="plan_encrypted",
            record_id=row.id, plaintext=plan,
        ) or ""
        row.last_operation_receipt_encrypted = encrypt_json_for_owner(
            db, owner_user_id=owner.id, table=_TABLE, field="last_operation_receipt_encrypted",
            record_id=row.id, plaintext={"request": submitted},
        ) or ""
    db.commit()
    return _projection(db, owner, row)
