"""Server-derived, redacted source bindings for consultation-split analysis.

This module has no route, queue, worker, retry, or LLM-provider path.  Source
preparation may invoke the established redaction boundary, but never while it
holds the canonical consultation-source locks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    ConsultationSplitAnalysis,
    ConsultationSplitAnalysisStatus,
    PostConsultationDictation,
    PromptTemplateVersion,
    RedactionRun,
    RedactionRunStatus,
    Transcript,
    TranscriptVersion,
    TranscriptWorkingNoteMode,
    User,
)
from app.services.clinical_nlp import (
    successful_redacted_clinical_hint_identity,
    successful_redacted_clinical_hints,
)
from app.services.consultation_splits import create_split_analysis, require_split_owner_transcript
from app.services.consultation_split_analysis import CONSULTATION_SPLIT_MAX_CANDIDATES, normalize_split_template_candidates
from app.services.consultation_split_locks import lock_consultation_split_source_scope
from app.services.content_crypto import (
    decrypt_text_for_owner,
    keyed_digest_for_existing_owner,
    keyed_digest_for_owner,
)
from app.services.dictations import dictation_effective_text
from app.services.redaction import (
    combined_phi_index,
    ensure_redaction_run_for_transcript_version,
    next_placeholder_index,
    redact_transient_text,
    redaction_run_text,
    validate_redacted_output_placeholders,
)
from app.services.redaction_primitives import (
    apply_manual_pii_redaction_to_values,
    manual_pii_entities_for_transcript,
    manual_pii_protections_from_entities,
    redact_dynamic_prompt_text,
    redact_dynamic_prompt_value,
)
from app.services.templates import list_available_templates_for_user
from app.services.transcripts import (
    freeform_working_note_text,
    normalize_structured_working_note,
    manual_pii_entity_value,
    snapshot_current_transcript_version,
    transcript_structured_context,
    transcript_working_note_mode,
)


_CONTENT_DIGEST_PURPOSE = "consultation_split_analysis.source_content"
_FINGERPRINT_PURPOSE = "consultation_split_analysis.source_fingerprint"


@dataclass(frozen=True)
class ConsultationSplitAnalysisSourceState:
    """A server-derived analysis binding, safe to persist only in encrypted slots."""

    transcript_id: UUID
    owner_user_id: UUID
    team_id: UUID
    transcript_version_id: UUID | None
    redaction_run_id: UUID | None
    source_fingerprint: str
    source_snapshot: dict[str, Any]
    candidate_template_snapshot: dict[str, Any]


@dataclass(frozen=True)
class PreparedConsultationSplitAnalysis:
    """Current, redacted analysis inputs before any analysis row is created.

    ``source_snapshot`` contains transcript-derived material and is therefore
    caller-memory only until the existing constructor encrypts it.  This type
    has no provider, task, quota, or persistence side effect of its own.
    """

    source_state: ConsultationSplitAnalysisSourceState
    source_snapshot: dict[str, Any]
    retention_expires_at: datetime

    @property
    def candidate_template_snapshot(self) -> dict[str, Any]:
        return self.source_state.candidate_template_snapshot


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _content_digest(
    db: Session,
    *,
    owner_user_id: UUID,
    value: str,
    create_digest_key: bool = True,
) -> str:
    digest = keyed_digest_for_owner if create_digest_key else keyed_digest_for_existing_owner
    return digest(
        db,
        owner_user_id=owner_user_id,
        purpose=_CONTENT_DIGEST_PURPOSE,
        value=value,
    )


def _require_bound_version(
    db: Session,
    *,
    transcript: Transcript,
    transcript_version_id: UUID | None,
    force_no_transcript_version: bool = False,
) -> TranscriptVersion | None:
    if force_no_transcript_version:
        if transcript_version_id is not None:
            raise AppError(409, "consultation_split_source_stale", "Consultation sources changed before analysis")
        return None
    if transcript_version_id is None:
        return db.scalar(
            select(TranscriptVersion)
            .where(TranscriptVersion.transcript_id == transcript.id)
            .order_by(TranscriptVersion.version_no.desc(), TranscriptVersion.id.desc())
            .limit(1)
        )
    version = db.get(TranscriptVersion, transcript_version_id)
    if version is None or version.transcript_id != transcript.id:
        raise AppError(422, "validation_error", "Transcript version does not belong to this consultation")
    return version


def _require_bound_redaction_run(
    db: Session,
    *,
    transcript: Transcript,
    transcript_version: TranscriptVersion | None,
    redaction_run_id: UUID | None,
) -> RedactionRun | None:
    if redaction_run_id is None:
        if transcript_version is None:
            return None
        return db.scalar(
            select(RedactionRun)
            .where(
                RedactionRun.transcript_version_id == transcript_version.id,
                RedactionRun.transcript_id == transcript.id,
                RedactionRun.owner_user_id == transcript.owner_user_id,
                RedactionRun.team_id == transcript.team_id,
                RedactionRun.status == RedactionRunStatus.succeeded,
            )
            .order_by(RedactionRun.created_at.desc(), RedactionRun.id.desc())
            .limit(1)
        )
    run = db.get(RedactionRun, redaction_run_id)
    if (
        run is None
        or transcript_version is None
        or run.transcript_id != transcript.id
        or run.transcript_version_id != transcript_version.id
        or run.owner_user_id != transcript.owner_user_id
        or run.team_id != transcript.team_id
    ):
        raise AppError(422, "validation_error", "Redaction run does not match this consultation source")
    return run


def _working_note_state(
    db: Session, *, transcript: Transcript, create_digest_key: bool = True
) -> dict[str, Any]:
    mode = transcript_working_note_mode(db, transcript=transcript)
    if mode is TranscriptWorkingNoteMode.freeform:
        text = freeform_working_note_text(db, transcript=transcript).strip()
    elif mode is TranscriptWorkingNoteMode.structured:
        structured = normalize_structured_working_note(transcript_structured_context(db, transcript=transcript))
        text = _canonical_json(structured) if structured is not None else ""
    else:
        text = ""
    return {
        "present": mode is not None,
        "mode": mode.value if mode is not None else None,
        # This makes a save which restores identical text a new source state.
        "updated_at": _timestamp(transcript.working_note_updated_at),
        "content_digest": _content_digest(
            db,
            owner_user_id=transcript.owner_user_id,
            value=text,
            create_digest_key=create_digest_key,
        ) if mode is not None else None,
    }


def _dictation_state(
    db: Session, *, transcript: Transcript, create_digest_key: bool = True
) -> dict[str, Any]:
    dictation = db.scalar(
        select(PostConsultationDictation).where(PostConsultationDictation.transcript_id == transcript.id)
    )
    if dictation is None:
        return {
            "row_exists": False,
            "present": False,
            "dictation_id": None,
            "updated_at": None,
            "latest_appended_at": None,
            "is_combined_text_user_edited": False,
            "content_digest": None,
            "segments": [],
        }
    if dictation.owner_user_id != transcript.owner_user_id or dictation.team_id != transcript.team_id:
        raise AppError(500, "consultation_split_scope_invalid", "Consultation split content is unavailable")
    if any(
        segment.owner_user_id != transcript.owner_user_id or segment.team_id != transcript.team_id
        for segment in dictation.segments
    ):
        raise AppError(500, "consultation_split_scope_invalid", "Consultation split content is unavailable")
    text = dictation_effective_text(db, dictation=dictation).strip()
    return {
        "row_exists": True,
        "present": bool(text),
        "dictation_id": str(dictation.id),
        "updated_at": _timestamp(dictation.updated_at),
        "latest_appended_at": _timestamp(dictation.latest_appended_at),
        "is_combined_text_user_edited": dictation.is_combined_text_user_edited,
        "content_digest": _content_digest(
            db,
            owner_user_id=transcript.owner_user_id,
            value=text,
            create_digest_key=create_digest_key,
        ) if text else None,
        # Segment identity prevents an untracked storage mutation from reusing a result.
        "segments": [
            {"id": str(segment.id), "sequence_no": segment.sequence_no, "created_at": _timestamp(segment.created_at)}
            for segment in dictation.segments
        ],
    }


def _candidate_template_snapshot(db: Session, *, actor: User) -> dict[str, Any]:
    # Keep selection and ordering aligned with the workspace's operational
    # availability rules.  Do not recreate this scope query here.
    templates = list_available_templates_for_user(db, actor)
    if len(templates) > CONSULTATION_SPLIT_MAX_CANDIDATES:
        raise AppError(422, "consultation_split_analysis_candidates_invalid", "Too many template candidates")
    entries: list[dict[str, str | None]] = []
    for template in templates:
        version = db.scalar(
            select(PromptTemplateVersion)
            .where(PromptTemplateVersion.template_id == template.id)
            .order_by(PromptTemplateVersion.version_no.desc())
            .limit(1)
        )
        if version is None:
            # A visible template with no version cannot generate and cannot be a candidate.
            continue
        entries.append(
            {
                "id": str(template.id),
                "name": template.name,
                "description": template.description,
                "mode": version.mode.value,
            }
        )
    normalized = normalize_split_template_candidates(entries)
    return {
        "templates": [
            {"id": str(candidate.id), "name": candidate.name, "description": candidate.description, "mode": candidate.mode}
            for candidate in normalized
        ]
    }


def _manual_pii_identity(db: Session, *, transcript: Transcript) -> list[dict[str, str | None]]:
    return [
        {
            "id": str(entity.id),
            "entity_type": entity.entity_type,
            "created_at": _timestamp(entity.created_at),
        }
        for entity in manual_pii_entities_for_transcript(
            db,
            transcript_id=transcript.id,
            owner_user_id=transcript.owner_user_id,
        )
    ]


def _optional_clinical_hint_identity(
    db: Session,
    *,
    transcript_version: TranscriptVersion | None,
    redaction_run: RedactionRun | None,
) -> dict[str, object] | None:
    if transcript_version is None or redaction_run is None:
        return None
    try:
        return successful_redacted_clinical_hint_identity(
            db,
            transcript_version=transcript_version,
            redaction_run=redaction_run,
        )
    except Exception:
        return None


def _current_transcript_text(db: Session, *, transcript: Transcript) -> str:
    return (
        decrypt_text_for_owner(
            db,
            owner_user_id=transcript.owner_user_id,
            table="transcripts",
            field="current_draft_text_encrypted",
            record_id=transcript.id,
            stored_value=transcript.current_draft_text_encrypted,
        )
        or ""
    ).strip()


def _current_server_transcript_binding(
    db: Session,
    *,
    transcript: Transcript,
    requested_transcript_version_id: UUID | None,
    requested_redaction_run_id: UUID | None,
) -> tuple[TranscriptVersion | None, RedactionRun | None]:
    """Snapshot the current draft and bind its required successful redaction.

    This deliberately shares normal generation's snapshot semantics, rather
    than treating the latest historical version as the current transcript.
    """
    if not _current_transcript_text(db, transcript=transcript):
        if requested_transcript_version_id is not None or requested_redaction_run_id is not None:
            raise AppError(409, "consultation_split_source_stale", "Consultation sources changed before analysis")
        return None, None
    version = snapshot_current_transcript_version(
        db,
        transcript=transcript,
        allow_empty=False,
        mark_transcript_ready=False,
    )
    # Snapshotting obtains the canonical owner/root/dictation locks.  Make the
    # immutable version durable and release that lock chain before required
    # redaction, whose provider call may be remote.  The caller later
    # re-acquires and proves this exact version/run before it can be used.
    version_id = version.id
    db.commit()
    db.expire_all()
    version = db.get(TranscriptVersion, version_id)
    if version is None or version.transcript_id != transcript.id:
        raise AppError(409, "consultation_split_source_stale", "Consultation sources changed before analysis")
    if requested_transcript_version_id is not None and requested_transcript_version_id != version.id:
        raise AppError(409, "consultation_split_source_stale", "Consultation sources changed before analysis")
    # This is the mandatory redaction boundary for every current transcript
    # source.  It may commit (and run optional clinical NLP), so callers must
    # re-acquire all mutable state before computing a persistable binding.
    run = ensure_redaction_run_for_transcript_version(db, transcript_version=version)
    if requested_redaction_run_id is not None and requested_redaction_run_id != run.id:
        raise AppError(409, "consultation_split_source_stale", "Consultation sources changed before analysis")
    return version, run


def _read_only_current_transcript_binding(
    db: Session,
    *,
    transcript: Transcript,
    transcript_version_id: UUID | None,
    redaction_run_id: UUID | None,
) -> tuple[TranscriptVersion | None, RedactionRun | None] | None:
    """Read an exact current transcript binding without snapshotting or redacting.

    A caller may reuse only the named version and successful run.  On
    any changed, unversioned, or unredacted draft, it returns no binding so a
    later preparation path can perform the required redaction boundary.  An
    empty current draft deliberately does not inspect historical versions.
    """
    current_text = _current_transcript_text(db, transcript=transcript)
    if not current_text:
        if (
            transcript_version_id is not None
            or redaction_run_id is not None
        ):
            return None
        return None, None
    if (
        transcript_version_id is None
        or redaction_run_id is None
    ):
        return None
    version = db.get(TranscriptVersion, transcript_version_id)
    if version is None or version.transcript_id != transcript.id:
        return None
    version_text = (
        decrypt_text_for_owner(
            db,
            owner_user_id=transcript.owner_user_id,
            table="transcript_versions",
            field="text_encrypted",
            record_id=version.id,
            stored_value=version.text_encrypted,
        )
        or ""
    ).strip()
    if version_text != current_text:
        return None
    run = db.get(RedactionRun, redaction_run_id)
    if (
        run is None
        or run.transcript_id != transcript.id
        or run.transcript_version_id != version.id
        or run.owner_user_id != transcript.owner_user_id
        or run.team_id != transcript.team_id
        or run.status is not RedactionRunStatus.succeeded
    ):
        return None
    return version, run


def current_consultation_split_analysis_source_matches(
    db: Session,
    owner: User,
    *,
    transcript: Transcript,
    analysis: ConsultationSplitAnalysis,
) -> bool:
    """Read and compare the complete current source binding for a locked analysis.

    The caller must already hold the owner, transcript, and ordered dictation
    locks. This function neither creates a transcript version nor runs
    redaction. It recomputes Working-note, dictation, manual-PII, clinical-NLP,
    and template-candidate identity from existing rows only.
    """
    if (
        analysis.owner_user_id != owner.id
        or analysis.team_id != owner.team_id
        or analysis.transcript_id != transcript.id
        or transcript.owner_user_id != owner.id
        or transcript.team_id != owner.team_id
        or analysis.retention_expires_at != transcript.retention_expires_at
    ):
        return False
    try:
        binding = _read_only_current_transcript_binding(
            db,
            transcript=transcript,
            transcript_version_id=analysis.transcript_version_id,
            redaction_run_id=analysis.redaction_run_id,
        )
        if binding is None:
            return False
        version, run = binding
        current = resolve_ready_consultation_split_analysis_source_state(
            db,
            owner,
            transcript_id=transcript.id,
            transcript_version_id=version.id if version is not None else None,
            redaction_run_id=run.id if run is not None else None,
            force_no_transcript_version=version is None,
            create_digest_key=False,
        )
    except (AppError, UnicodeDecodeError):
        return False
    return current.source_fingerprint == analysis.source_fingerprint


def current_consultation_split_analysis_clinical_source_matches(
    db: Session,
    owner: User,
    *,
    transcript: Transcript,
    analysis: ConsultationSplitAnalysis,
    persisted_source_state: dict[str, Any],
) -> bool:
    """Compare only the clinical source identity for one queued one-note run.

    Candidate-template metadata belongs to split analysis selection, not to the
    consultation source.  It must not make an already submitted one-note
    fallback stale or cause another analysis run.  The caller has already
    decrypted the analysis's encrypted source snapshot and retains the
    canonical owner/root/dictation locks.
    """
    if not isinstance(persisted_source_state, dict) or (
        analysis.owner_user_id != owner.id
        or analysis.team_id != owner.team_id
        or analysis.transcript_id != transcript.id
        or analysis.retention_expires_at != transcript.retention_expires_at
    ):
        return False
    try:
        binding = _read_only_current_transcript_binding(
            db,
            transcript=transcript,
            transcript_version_id=analysis.transcript_version_id,
            redaction_run_id=analysis.redaction_run_id,
        )
        if binding is None:
            return False
        version, run = binding
        current = resolve_consultation_split_analysis_source_state(
            db,
            owner,
            transcript_id=transcript.id,
            transcript_version_id=version.id if version is not None else None,
            redaction_run_id=run.id if run is not None else None,
            force_no_transcript_version=version is None,
            create_digest_key=False,
            include_candidate_templates=False,
        )
    except (AppError, UnicodeDecodeError):
        return False
    return current.source_snapshot == persisted_source_state


def resolve_consultation_split_analysis_source_state(
    db: Session,
    actor: User,
    *,
    transcript_id: UUID,
    transcript_version_id: UUID | None = None,
    redaction_run_id: UUID | None = None,
    force_no_transcript_version: bool = False,
    create_digest_key: bool = True,
    include_candidate_templates: bool = True,
) -> ConsultationSplitAnalysisSourceState:
    """Resolve an analysis binding exclusively from server-owned state.

    Callers may identify a root and its immutable transcript/redaction rows, but
    cannot provide a digest or source fingerprint.  A root with only a Working
    note and/or dictation is valid and therefore may have no transcript version.
    """
    transcript = require_split_owner_transcript(db, actor, transcript_id=transcript_id)
    if force_no_transcript_version and redaction_run_id is not None:
        raise AppError(409, "consultation_split_source_stale", "Consultation sources changed before analysis")
    version = _require_bound_version(
        db,
        transcript=transcript,
        transcript_version_id=transcript_version_id,
        force_no_transcript_version=force_no_transcript_version,
    )
    redaction_run = _require_bound_redaction_run(
        db,
        transcript=transcript,
        transcript_version=version,
        redaction_run_id=redaction_run_id,
    )
    source_snapshot = {
        "owner_user_id": str(transcript.owner_user_id),
        "team_id": str(transcript.team_id),
        "transcript_id": str(transcript.id),
        "transcript": {
            "present": version is not None,
            "transcript_version_id": str(version.id) if version is not None else None,
            "redaction_run_id": str(redaction_run.id) if redaction_run is not None else None,
        },
        "working_note": _working_note_state(
            db, transcript=transcript, create_digest_key=create_digest_key
        ),
        "dictation": _dictation_state(
            db, transcript=transcript, create_digest_key=create_digest_key
        ),
        "manual_pii": _manual_pii_identity(db, transcript=transcript),
        "clinical_nlp_hints": _optional_clinical_hint_identity(
            db,
            transcript_version=version,
            redaction_run=redaction_run,
        ),
    }
    candidate_template_snapshot = _candidate_template_snapshot(db, actor=actor) if include_candidate_templates else {}
    fingerprint_digest = keyed_digest_for_owner if create_digest_key else keyed_digest_for_existing_owner
    fingerprint = fingerprint_digest(
        db,
        owner_user_id=transcript.owner_user_id,
        purpose=_FINGERPRINT_PURPOSE,
        value=_canonical_json({"source": source_snapshot, "candidates": candidate_template_snapshot}),
    )
    return ConsultationSplitAnalysisSourceState(
        transcript_id=transcript.id,
        owner_user_id=transcript.owner_user_id,
        team_id=transcript.team_id,
        transcript_version_id=version.id if version is not None else None,
        redaction_run_id=redaction_run.id if redaction_run is not None else None,
        source_fingerprint=fingerprint,
        source_snapshot=source_snapshot,
        candidate_template_snapshot=candidate_template_snapshot,
    )


def resolve_ready_consultation_split_analysis_source_state(
    db: Session,
    actor: User,
    *,
    transcript_id: UUID,
    transcript_version_id: UUID | None = None,
    redaction_run_id: UUID | None = None,
    force_no_transcript_version: bool = False,
    create_digest_key: bool = True,
) -> ConsultationSplitAnalysisSourceState:
    """Resolve only a state safe to persist for provider-bound analysis.

    A transcript source must have a matching successful redaction run.  Roots
    that use only saved Working-note and/or dictation sources remain valid with
    no transcript version or redaction run.  The looser resolver above is for
    pre-redaction comparison only and must not create analysis rows.
    """
    state = resolve_consultation_split_analysis_source_state(
        db,
        actor,
        transcript_id=transcript_id,
        transcript_version_id=transcript_version_id,
        redaction_run_id=redaction_run_id,
        force_no_transcript_version=force_no_transcript_version,
        create_digest_key=create_digest_key,
    )
    supplemental_source_present = any((
        state.source_snapshot["working_note"]["present"],
        state.source_snapshot["dictation"]["present"],
    ))
    if state.transcript_version_id is None:
        if not supplemental_source_present:
            raise AppError(422, "consultation_split_source_empty", "Consultation split analysis needs a saved source")
        return state
    if state.redaction_run_id is None:
        raise AppError(422, "redaction_required", "Transcript source must be redacted before split analysis")
    run = db.get(RedactionRun, state.redaction_run_id)
    if run is None or run.status is not RedactionRunStatus.succeeded:
        raise AppError(422, "redaction_required", "Transcript source must be redacted before split analysis")
    # Do not persist this plaintext; it is used only to reject an unusable
    # transcript source when there is no separately usable saved source.
    if not (redaction_run_text(db, run=run) or "").strip() and not supplemental_source_present:
        raise AppError(422, "consultation_split_source_empty", "Consultation split analysis needs a saved source")
    return state


def _working_note_source_value(db: Session, *, transcript: Transcript) -> tuple[str | None, object | None]:
    mode = transcript_working_note_mode(db, transcript=transcript)
    if mode is TranscriptWorkingNoteMode.freeform:
        text = freeform_working_note_text(db, transcript=transcript).strip()
        return (mode.value, text) if text else (None, None)
    if mode is TranscriptWorkingNoteMode.structured:
        note = normalize_structured_working_note(transcript_structured_context(db, transcript=transcript))
        return (mode.value, note) if note is not None else (None, None)
    return None, None


def _redact_working_note_value(
    db: Session,
    *,
    value: object,
    mode: str | None,
    team_id: UUID,
    start_index: int,
) -> tuple[object, list[dict[str, Any]]]:
    if mode != TranscriptWorkingNoteMode.structured.value:
        return redact_dynamic_prompt_value(
            db,
            value,
            team_id=team_id,
            start_index=start_index,
            redact_text=redact_transient_text,
        )
    if not isinstance(value, dict) or value.get("profile") != "emis" or not isinstance(value.get("sections"), dict):
        raise AppError(500, "redaction_failed", "Structured working note changed shape")
    redacted_sections, phi_index = redact_dynamic_prompt_value(
        db,
        value["sections"],
        team_id=team_id,
        start_index=start_index,
        redact_text=redact_transient_text,
    )
    if not isinstance(redacted_sections, dict):
        raise AppError(500, "redaction_failed", "Structured working note redaction changed shape")
    return {"profile": "emis", "sections": redacted_sections}, phi_index


def _optional_redacted_clinical_hints(
    db: Session,
    *,
    transcript_version: TranscriptVersion | None,
    redaction_run: RedactionRun | None,
) -> list[dict[str, str]]:
    """Contain optional NLP reader failure without masking required redaction."""
    if transcript_version is None or redaction_run is None:
        return []
    try:
        return successful_redacted_clinical_hints(
            db,
            transcript_version=transcript_version,
            redaction_run=redaction_run,
        )
    except Exception:
        # Clinical NLP is enrichment only.  Deliberately do not log entity or
        # source values, and do not invoke the enrichment provider here.
        return []


def _prepared_analysis_source_snapshot(
    db: Session,
    *,
    transcript: Transcript,
    transcript_version: TranscriptVersion | None,
    redaction_run: RedactionRun | None,
    source_state: ConsultationSplitAnalysisSourceState,
) -> dict[str, Any]:
    """Build the complete encrypted-only redacted source payload."""
    transcript_text = redaction_run_text(db, run=redaction_run).strip() if redaction_run is not None else ""
    base_phi_index = combined_phi_index(db, redaction_run) if redaction_run is not None else []
    start_index = next_placeholder_index(redaction_run) if redaction_run is not None else 1

    working_mode, working_note = _working_note_source_value(db, transcript=transcript)
    if working_note is not None:
        working_note, working_phi_index = _redact_working_note_value(
            db,
            value=working_note,
            mode=working_mode,
            team_id=transcript.team_id,
            start_index=start_index,
        )
    else:
        working_phi_index = []
    dictation = _dictation_state(db, transcript=transcript)
    dictation_text = ""
    if dictation["present"]:
        row = db.scalar(select(PostConsultationDictation).where(PostConsultationDictation.transcript_id == transcript.id))
        if row is None:  # Defensive: the metadata reader verified this a moment ago.
            raise AppError(409, "consultation_split_source_stale", "Consultation sources changed before analysis")
        dictation_text = dictation_effective_text(db, dictation=row).strip()
    if dictation_text:
        dictation_text, dictation_phi_index = redact_dynamic_prompt_text(
            db,
            dictation_text,
            team_id=transcript.team_id,
            start_index=start_index + len(working_phi_index),
            redact_text=redact_transient_text,
        )
        dictation_text = (dictation_text or "").strip()
    else:
        dictation_phi_index = []

    values: dict[str, object] = {
        "transcript": transcript_text,
        "working_note": working_note,
        "dictation": dictation_text,
        "clinical_nlp_hints": _optional_redacted_clinical_hints(
            db,
            transcript_version=transcript_version,
            redaction_run=redaction_run,
        ),
    }
    protections = manual_pii_protections_from_entities(
        db,
        entities=manual_pii_entities_for_transcript(
            db,
            transcript_id=transcript.id,
            owner_user_id=transcript.owner_user_id,
        ),
        value_reader=manual_pii_entity_value,
    )
    redacted_values, manual_phi_index = apply_manual_pii_redaction_to_values(
        values,
        start_index=start_index + len(working_phi_index) + len(dictation_phi_index),
        protections=protections,
    )
    if not any((
        bool(str(redacted_values["transcript"]).strip()),
        redacted_values["working_note"] is not None,
        bool(str(redacted_values["dictation"]).strip()),
    )):
        raise AppError(422, "consultation_split_source_empty", "Consultation split analysis needs a saved source")
    return {
        # No raw values are allowed in state metadata.  The following full
        # source payload and mapping live only in this encrypted analysis slot.
        "source_state": source_state.source_snapshot,
        "sources": {
            "transcript": redacted_values["transcript"],
            "working_note": {"mode": working_mode, "value": redacted_values["working_note"]},
            "dictation": redacted_values["dictation"],
        },
        "clinical_nlp_hints": redacted_values["clinical_nlp_hints"],
        "phi_index": [*base_phi_index, *working_phi_index, *dictation_phi_index, *manual_phi_index],
    }


def _validate_prepared_placeholder_integrity(value: object, *, phi_index: list[dict[str, Any]]) -> None:
    if isinstance(value, str):
        validate_redacted_output_placeholders(value, phi_index=phi_index)
    elif isinstance(value, list):
        for item in value:
            _validate_prepared_placeholder_integrity(item, phi_index=phi_index)
    elif isinstance(value, dict):
        for item in value.values():
            _validate_prepared_placeholder_integrity(item, phi_index=phi_index)


def prepare_source_bound_consultation_split_analysis(
    db: Session,
    actor: User,
    *,
    transcript_id: UUID,
    transcript_version_id: UUID | None = None,
    redaction_run_id: UUID | None = None,
) -> PreparedConsultationSplitAnalysis:
    """Prepare a current, redacted analysis source boundary without an analysis row.

    Required transcript and dynamic-source redaction retain their established
    provider boundaries outside source locks.  This function intentionally
    does not select an LLM config, resolve a credential, create an execution,
    reserve quota, or add an outbox row.
    """
    # Do not acquire the source lock before this boundary.  Required
    # transcript redaction (and its optional clinical enrichment) may call a
    # remote provider and may commit.  The later lock/reproof makes this
    # unlocked read safe for persistable work.
    transcript = require_split_owner_transcript(db, actor, transcript_id=transcript_id)
    version, run = _current_server_transcript_binding(
        db,
        transcript=transcript,
        requested_transcript_version_id=transcript_version_id,
        requested_redaction_run_id=redaction_run_id,
    )
    # Required redaction can commit and optional NLP can commit independently.
    # Re-acquire the actor, root and extant dictation after that boundary, then
    # prove the exact draft/version and redaction run still match before we
    # derive the fingerprint.  This first locked state is intentionally
    # released before dynamic Working-note/dictation redaction below.
    bound_version_id = version.id if version is not None else None
    bound_run_id = run.id if run is not None else None
    db.expire_all()
    require_split_owner_transcript(db, actor, transcript_id=transcript_id)
    scope = lock_consultation_split_source_scope(
        db,
        owner_user_id=actor.id,
        transcript_id=transcript_id,
    )
    if scope is None:
        raise AppError(403, "forbidden", "Transcript access is restricted to the owning user")
    actor = scope.owner
    transcript = scope.transcript
    locked_retention_expires_at = transcript.retention_expires_at
    if bound_version_id is not None:
        current_version = snapshot_current_transcript_version(
            db,
            transcript=transcript,
            allow_empty=False,
            mark_transcript_ready=False,
        )
        if current_version.id != bound_version_id:
            raise AppError(409, "consultation_split_source_stale", "Consultation sources changed before analysis")
        version = current_version
        run = _require_bound_redaction_run(
            db,
            transcript=transcript,
            transcript_version=version,
            redaction_run_id=bound_run_id,
        )
        if run is None or run.status is not RedactionRunStatus.succeeded:
            raise AppError(422, "redaction_required", "Transcript source must be redacted before split analysis")
    else:
        version, run = None, None
    state = resolve_ready_consultation_split_analysis_source_state(
        db,
        actor,
        transcript_id=transcript.id,
        transcript_version_id=version.id if version is not None else None,
        redaction_run_id=run.id if run is not None else None,
        force_no_transcript_version=bound_version_id is None,
    )
    # Dynamic source redaction can use a remote de-identification provider.
    # Commit only after the required-redaction boundary above has made its own
    # state durable, so these canonical locks never span a provider call.
    db.commit()
    db.expire_all()
    transcript = require_split_owner_transcript(db, actor, transcript_id=transcript_id)
    source_snapshot = _prepared_analysis_source_snapshot(
        db,
        transcript=transcript,
        transcript_version=version,
        redaction_run=run,
        source_state=state,
    )
    _validate_prepared_placeholder_integrity(
        source_snapshot["sources"],
        phi_index=source_snapshot["phi_index"],
    )
    _validate_prepared_placeholder_integrity(
        source_snapshot["clinical_nlp_hints"],
        phi_index=source_snapshot["phi_index"],
    )
    # Source values can change while dynamic redaction runs.  Re-lock the full
    # canonical scope and re-prove the current transcript binding, retention,
    # manual-PII identity, Working note, dictation, hints, and candidates
    # before returning anything that can create/reuse usable analysis work.
    db.expire_all()
    scope = lock_consultation_split_source_scope(
        db,
        owner_user_id=actor.id,
        transcript_id=transcript_id,
    )
    if scope is None:
        raise AppError(403, "forbidden", "Transcript access is restricted to the owning user")
    actor = scope.owner
    transcript = scope.transcript
    if transcript.retention_expires_at != locked_retention_expires_at:
        raise AppError(409, "consultation_split_source_stale", "Consultation sources changed before analysis")
    final_binding = _read_only_current_transcript_binding(
        db,
        transcript=transcript,
        transcript_version_id=bound_version_id,
        redaction_run_id=bound_run_id,
    )
    if final_binding is None:
        raise AppError(409, "consultation_split_source_stale", "Consultation sources changed before analysis")
    final_version, final_run = final_binding
    if (final_version.id if final_version is not None else None) != bound_version_id or (
        final_run.id if final_run is not None else None
    ) != bound_run_id:
        raise AppError(409, "consultation_split_source_stale", "Consultation sources changed before analysis")
    current = resolve_ready_consultation_split_analysis_source_state(
        db,
        actor,
        transcript_id=state.transcript_id,
        transcript_version_id=state.transcript_version_id,
        redaction_run_id=state.redaction_run_id,
        force_no_transcript_version=bound_version_id is None,
    )
    if (
        current.source_fingerprint != state.source_fingerprint
        or current.source_snapshot != state.source_snapshot
        or current.candidate_template_snapshot != state.candidate_template_snapshot
    ):
        raise AppError(409, "consultation_split_source_stale", "Consultation sources changed before analysis")
    return PreparedConsultationSplitAnalysis(
        source_state=state,
        source_snapshot=source_snapshot,
        retention_expires_at=locked_retention_expires_at,
    )


def find_cached_prepared_consultation_split_analysis(
    db: Session,
    actor: User,
    *,
    prepared: PreparedConsultationSplitAnalysis,
) -> ConsultationSplitAnalysis | None:
    """Return the exact non-stale analysis for a prepared source.

    The caller-memory result is not trusted.  Under the final lock order this
    recomputes current server state from existing rows only.  A changed draft
    that lacks an existing successful redaction run is not cached; later
    preparation must perform the required redaction/re-acquisition boundary.
    """
    require_split_owner_transcript(db, actor, transcript_id=prepared.source_state.transcript_id)
    scope = lock_consultation_split_source_scope(
        db,
        owner_user_id=actor.id,
        transcript_id=prepared.source_state.transcript_id,
    )
    if scope is None:
        return None
    owner = scope.owner
    locked_transcript = scope.transcript
    current_binding = _read_only_current_transcript_binding(
        db,
        transcript=locked_transcript,
        transcript_version_id=prepared.source_state.transcript_version_id,
        redaction_run_id=prepared.source_state.redaction_run_id,
    )
    if current_binding is None:
        return None
    version, run = current_binding
    current = resolve_ready_consultation_split_analysis_source_state(
        db,
        owner,
        transcript_id=locked_transcript.id,
        transcript_version_id=version.id if version is not None else None,
        redaction_run_id=run.id if run is not None else None,
        force_no_transcript_version=version is None,
    )
    if (
        current.source_fingerprint != prepared.source_state.source_fingerprint
        or locked_transcript.retention_expires_at != prepared.retention_expires_at
    ):
        return None
    # The caller still holds the canonical source scope.  Do not lock the
    # analysis here: cache/replay then take execution -> analysis, matching the
    # worker's final lock chain and avoiding an analysis -> execution inversion.
    return db.scalar(
        select(ConsultationSplitAnalysis)
        .where(
            ConsultationSplitAnalysis.owner_user_id == owner.id,
            ConsultationSplitAnalysis.team_id == owner.team_id,
            ConsultationSplitAnalysis.transcript_id == locked_transcript.id,
            ConsultationSplitAnalysis.source_fingerprint == current.source_fingerprint,
            ConsultationSplitAnalysis.status != ConsultationSplitAnalysisStatus.stale,
        )
    )


def create_source_bound_consultation_split_analysis(
    db: Session,
    actor: User,
    *,
    transcript_id: UUID,
    transcript_version_id: UUID | None = None,
    redaction_run_id: UUID | None = None,
) -> ConsultationSplitAnalysis:
    """Prepare and persist an encrypted, redacted analysis source boundary.

    The preparation stage remains separately reusable for cache inspection,
    while this established constructor retains its encryption and insertion
    behavior.  It neither queues work nor calls an LLM provider.
    """
    prepared = prepare_source_bound_consultation_split_analysis(
        db,
        actor,
        transcript_id=transcript_id,
        transcript_version_id=transcript_version_id,
        redaction_run_id=redaction_run_id,
    )
    state = prepared.source_state
    return create_split_analysis(
        db,
        actor,
        transcript_id=state.transcript_id,
        source_fingerprint=state.source_fingerprint,
        transcript_version_id=state.transcript_version_id,
        redaction_run_id=state.redaction_run_id,
        source_snapshot=prepared.source_snapshot,
        candidate_template_snapshot=state.candidate_template_snapshot,
    )
