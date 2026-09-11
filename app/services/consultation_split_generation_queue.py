"""Composable durable queue construction for confirmed split generation.

This module deliberately does not commit or publish.  Confirmation owns the
outer transaction and publishes the returned outbox task only after commit.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import ConsultationSplitBatch, ConsultationSplitBatchStatus, ConsultationSplitExecution, ConsultationSplitExecutionKind, User, utcnow
from app.services.consultation_split_generation import prepare_split_generation_request
from app.services.consultation_splits import queue_split_execution, read_split_batch_json
from app.services.content_crypto import encrypt_json_for_existing_owner
from app.services.llm import resolve_user_llm
from app.services.llm_adapters.runtime import build_provider_snapshot, generation_request_snapshot

SPLIT_GENERATION_RESERVATION_SECONDS = 1_500


@dataclass(frozen=True, slots=True)
class QueuedSplitGeneration:
    execution: ConsultationSplitExecution
    dispatch_task_id: UUID | None
    created_new_work: bool


def _locked_owner(db: Session, batch: ConsultationSplitBatch) -> User:
    owner = db.scalar(select(User).where(User.id == batch.owner_user_id).with_for_update())
    if owner is None or owner.is_system_admin or owner.team_id != batch.team_id:
        raise AppError(500, "consultation_split_scope_invalid", "Consultation split content is unavailable")
    return owner


def queue_confirmed_split_generation(
    db: Session, *, batch: ConsultationSplitBatch | None = None, batch_id: UUID | None = None,
) -> QueuedSplitGeneration:
    """Create exactly one initial generation execution under the caller's transaction.

    ``batch_id`` remains for a narrow internal replay caller, but this function
    never commits.  A matching generation execution is authoritative replay and
    cannot reserve quota or add a second outbox row.
    """
    if batch is None:
        if batch_id is None:
            raise ValueError("batch or batch_id is required")
        batch = db.scalar(select(ConsultationSplitBatch).where(ConsultationSplitBatch.id == batch_id).with_for_update())
    else:
        batch = db.scalar(select(ConsultationSplitBatch).where(ConsultationSplitBatch.id == batch.id).with_for_update())
    if batch is None or batch.status is not ConsultationSplitBatchStatus.generation_queued:
        raise AppError(409, "consultation_split_batch_unavailable", "Confirmed split batch is unavailable")
    owner = _locked_owner(db, batch)
    existing = db.scalar(
        select(ConsultationSplitExecution).where(
            ConsultationSplitExecution.batch_id == batch.id,
            ConsultationSplitExecution.kind == ConsultationSplitExecutionKind.generation,
        ).with_for_update()
    )
    if existing is not None:
        return QueuedSplitGeneration(existing, None, False)

    _selection, config, model, _preference = resolve_user_llm(db, owner)
    if not isinstance(model, str) or not model.strip():
        raise AppError(422, "consultation_split_llm_model_unavailable", "No LLM model is available for consultation splitting")
    model = model.strip()
    prepared = prepare_split_generation_request(
        source_snapshot=read_split_batch_json(db, owner, batch=batch, field="source_snapshot_encrypted") or {},
        clinical_snapshot=read_split_batch_json(db, owner, batch=batch, field="clinical_snapshot_encrypted") or {},
        confirmed_plan=read_split_batch_json(db, owner, batch=batch, field="confirmed_plan_encrypted") or {},
        note_options_snapshot=read_split_batch_json(db, owner, batch=batch, field="note_options_snapshot_encrypted") or {},
    )
    messages = prepared.request_body["messages"]
    system_message = messages[0]["content"] if isinstance(messages, list) and isinstance(messages[0], dict) else None
    user_message = messages[1]["content"] if isinstance(messages, list) and isinstance(messages[1], dict) else None
    if not isinstance(system_message, str) or not isinstance(user_message, str):
        raise AppError(500, "consultation_split_request_payload_invalid", "Consultation split content is unavailable")
    provider = build_provider_snapshot(config=config, model=model)
    request = generation_request_snapshot(
        adapter_kind=config.adapter_kind,
        model=model,
        user_id=owner.id,
        system_message=system_message,
        user_message=user_message,
        output_token_cap=prepared.output_token_cap,
        response_json_schema=prepared.response_json_schema,
    )
    # Confirmation's provider snapshot is write-once and contains no secret.
    existing_batch_snapshot = read_split_batch_json(db, owner, batch=batch, field="provider_snapshot_encrypted")
    if existing_batch_snapshot != {}:
        raise AppError(500, "consultation_split_provider_snapshot_invalid", "Consultation split content is unavailable")
    snapshot = provider.to_dict()
    batch.provider_snapshot_encrypted = encrypt_json_for_existing_owner(
        db, owner_user_id=owner.id, table="consultation_split_batches",
        field="provider_snapshot_encrypted", record_id=batch.id, plaintext=snapshot,
    )
    execution, _attempt, dispatch = queue_split_execution(
        db, owner, kind=ConsultationSplitExecutionKind.generation, batch=batch,
        reserved_units=prepared.reservation_units,
        reservation_valid_until=utcnow() + timedelta(seconds=SPLIT_GENERATION_RESERVATION_SECONDS),
        llm_config_id=config.id, provider_snapshot=snapshot, request_payload=request, expected_model=model,
    )
    return QueuedSplitGeneration(execution, dispatch.task_id, True)
