from datetime import timedelta
from dataclasses import replace
from hashlib import sha256
from inspect import signature
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from app.errors import AppError
from app.models import (
    ClinicalEntity,
    ClinicalEntityRun,
    ConsultationSplitAnalysis,
    ConsultationSplitAnalysisStatus,
    PostConsultationDictation,
    ProviderAttempt,
    PromptTemplateVersion,
    RedactionRun,
    RedactionRunStatus,
    TemplateMode,
    Transcript,
    TranscriptIngestionMode,
    TranscriptManualPiiEntity,
    TranscriptStatus,
    TranscriptVersion,
    TranscriptWorkingNoteMode,
    TaskDispatchOutbox,
    UserEncryptionKey,
    utcnow,
)
from app.services.consultation_split_sources import (
    create_source_bound_consultation_split_analysis,
    current_consultation_split_analysis_source_matches,
    find_cached_prepared_consultation_split_analysis,
    prepare_source_bound_consultation_split_analysis,
    resolve_consultation_split_analysis_source_state,
)
from app.services.templates import _ensure_generation_can_be_queued
from app.services.clinical_nlp import successful_redacted_clinical_hints
from app.services.consultation_splits import read_split_analysis_json
from app.services.content_crypto import encrypt_text_for_owner, is_encrypted_envelope
from app.services.transcripts import create_manual_pii_entity, set_freeform_working_note_text


SOURCE_TEXT = "Patient raw consultation text: secret diagnosis"
WORKING_TEXT = "Working-note raw source: secret plan"
DICTATION_TEXT = "Dictation raw source: secret follow-up"
PROMPT_TEXT = "Never include this template prompt in analysis metadata"


def _transcript(db, owner):
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=owner.team_id,
        title="Synthetic consultation",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db.add(transcript)
    db.flush()
    return transcript


def _version(db, owner, transcript, *, version_no=1):
    version = TranscriptVersion(id=uuid4(), transcript_id=transcript.id, version_no=version_no, text_encrypted="")
    version.text_encrypted = encrypt_text_for_owner(
        db, owner_user_id=owner.id, table="transcript_versions", field="text_encrypted", record_id=version.id, plaintext=SOURCE_TEXT
    ) or ""
    db.add(version)
    transcript.current_draft_text_encrypted = encrypt_text_for_owner(
        db, owner_user_id=owner.id, table="transcripts", field="current_draft_text_encrypted",
        record_id=transcript.id, plaintext=SOURCE_TEXT,
    )
    db.add(transcript)
    db.flush()
    return version


def _redaction_run(db, owner, transcript, version, *, status=RedactionRunStatus.succeeded, redacted_text="Redacted synthetic"):
    run = RedactionRun(
        transcript_id=transcript.id,
        transcript_version_id=version.id,
        owner_user_id=owner.id,
        team_id=owner.team_id,
        status=status,
        redacted_text_encrypted=redacted_text,
        mapping_hash="synthetic",
        api_provider="native_presidio",
    )
    db.add(run)
    db.flush()
    return run


def _working_note(db, transcript, *, text=WORKING_TEXT, at=None):
    set_freeform_working_note_text(db, transcript=transcript, plaintext=text)
    transcript.working_note_mode = TranscriptWorkingNoteMode.freeform
    transcript.working_note_updated_at = at or utcnow()
    db.flush()


def _dictation(db, owner, transcript, *, text=DICTATION_TEXT, at=None):
    row = PostConsultationDictation(
        id=uuid4(), transcript_id=transcript.id, owner_user_id=owner.id, team_id=owner.team_id,
        combined_edited_text_encrypted="", is_combined_text_user_edited=True, updated_at=at or utcnow(),
    )
    row.combined_edited_text_encrypted = encrypt_text_for_owner(
        db, owner_user_id=owner.id, table="post_consultation_dictations", field="combined_edited_text_encrypted",
        record_id=row.id, plaintext=text,
    )
    db.add(row)
    db.flush()
    return row


@pytest.mark.parametrize("include_transcript,include_working,include_dictation", [
    (True, False, False),
    (False, True, False),
    (False, False, True),
    (True, True, True),
    (False, False, False),
])
def test_source_state_supports_each_source_combination_and_is_stable(
    db_session, make_user, make_template, include_transcript, include_working, include_dictation,
):
    owner = make_user(email=f"source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    version = _version(db_session, owner, transcript) if include_transcript else None
    run = _redaction_run(db_session, owner, transcript, version) if version is not None else None
    if include_working:
        _working_note(db_session, transcript)
    if include_dictation:
        _dictation(db_session, owner, transcript)
    make_template(owner=owner, actor=owner, prompt_text=PROMPT_TEXT)

    kwargs = {"transcript_id": transcript.id, "transcript_version_id": version.id if version else None,
              "redaction_run_id": run.id if run else None}
    first = resolve_consultation_split_analysis_source_state(db_session, owner, **kwargs)
    second = resolve_consultation_split_analysis_source_state(db_session, owner, **kwargs)

    assert first.source_fingerprint == second.source_fingerprint
    assert first.source_snapshot == second.source_snapshot
    assert first.source_snapshot["transcript"]["present"] is include_transcript
    assert first.source_snapshot["working_note"]["present"] is include_working
    assert first.source_snapshot["dictation"]["present"] is include_dictation
    assert first.transcript_version_id == (version.id if version else None)
    assert first.redaction_run_id == (run.id if run else None)


def test_source_fingerprint_binds_every_mutable_or_immutable_input(db_session, make_user, make_template):
    owner = make_user(email=f"source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    first_version = _version(db_session, owner, transcript, version_no=1)
    first_run = _redaction_run(db_session, owner, transcript, first_version)
    _working_note(db_session, transcript, at=utcnow())
    dictation = _dictation(db_session, owner, transcript, at=utcnow())
    template = make_template(owner=owner, actor=owner, name="Initial template", description="Initial description", prompt_text=PROMPT_TEXT)

    def state(version=first_version, run=first_run):
        return resolve_consultation_split_analysis_source_state(
            db_session, owner, transcript_id=transcript.id, transcript_version_id=version.id, redaction_run_id=run.id,
        ).source_fingerprint

    baseline = state()
    second_version = _version(db_session, owner, transcript, version_no=2)
    second_run = _redaction_run(db_session, owner, transcript, second_version)
    assert state(second_version, second_run) != baseline

    # A new run for the same immutable version is a distinct redaction boundary.
    replacement_run = _redaction_run(db_session, owner, transcript, first_version)
    assert state(first_version, replacement_run) != baseline

    # Restoring identical content after a save still changes source identity.
    _working_note(db_session, transcript, text=WORKING_TEXT, at=utcnow() + timedelta(seconds=1))
    after_working_save = state()
    assert after_working_save != baseline

    dictation.updated_at = utcnow() + timedelta(seconds=2)
    db_session.flush()
    after_dictation_save = state()
    assert after_dictation_save != after_working_save

    template.description = "Changed candidate description"
    db_session.flush()
    after_description = state()
    assert after_description != after_dictation_save
    template.name = "Renamed candidate"
    db_session.flush()
    after_name = state()
    assert after_name != after_description
    latest = db_session.scalar(
        select(PromptTemplateVersion)
        .where(PromptTemplateVersion.template_id == template.id)
        .order_by(PromptTemplateVersion.version_no.desc())
    )
    db_session.add(PromptTemplateVersion(
        template_id=template.id,
        version_no=latest.version_no + 1,
        mode=TemplateMode.structured,
        prompt_text=PROMPT_TEXT,
        config_json={"profile": "emis", "sections": [{"key": "problem", "label": "Problem"}]},
        created_by_user_id=owner.id,
    ))
    db_session.flush()
    assert state() != after_name


def test_candidate_snapshot_uses_actual_mode_but_never_prompt_or_source_text(db_session, make_user, make_template):
    owner = make_user(email=f"source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    _working_note(db_session, transcript)
    template = make_template(
        owner=owner, actor=owner, name="Clinical summary", description="Card metadata", prompt_text=PROMPT_TEXT,
        mode=TemplateMode.structured,
    )
    state = resolve_consultation_split_analysis_source_state(db_session, owner, transcript_id=transcript.id)
    serialized = str({"source": state.source_snapshot, "candidates": state.candidate_template_snapshot})
    candidate = state.candidate_template_snapshot["templates"][0]

    assert candidate == {"id": str(template.id), "name": "Clinical summary", "description": "Card metadata", "mode": "structured"}
    assert PROMPT_TEXT not in serialized
    assert WORKING_TEXT not in serialized
    assert SOURCE_TEXT not in serialized
    assert DICTATION_TEXT not in serialized
    assert sha256(WORKING_TEXT.encode()).hexdigest() not in serialized


def test_source_state_owner_and_root_scope_fail_closed(db_session, make_user, make_team, make_template):
    owner = make_user(email=f"source-owner-{uuid4()}@example.com")
    other = make_user(email=f"source-other-{uuid4()}@example.com", team=make_team(name=f"Other {uuid4()}"))
    transcript = _transcript(db_session, owner)
    version = _version(db_session, owner, transcript)
    run = _redaction_run(db_session, owner, transcript, version)
    make_template(owner=owner, actor=owner)

    with pytest.raises(AppError) as other_owner:
        resolve_consultation_split_analysis_source_state(
            db_session, other, transcript_id=transcript.id, transcript_version_id=version.id, redaction_run_id=run.id,
        )
    assert other_owner.value.status_code == 403

    other_transcript = _transcript(db_session, owner)
    other_version = _version(db_session, owner, other_transcript)
    with pytest.raises(AppError) as wrong_version:
        resolve_consultation_split_analysis_source_state(
            db_session, owner, transcript_id=transcript.id, transcript_version_id=other_version.id,
        )
    assert wrong_version.value.status_code == 422

    other_run = _redaction_run(db_session, owner, other_transcript, other_version)
    with pytest.raises(AppError) as wrong_redaction:
        resolve_consultation_split_analysis_source_state(
            db_session, owner, transcript_id=transcript.id, transcript_version_id=version.id, redaction_run_id=other_run.id,
        )
    assert wrong_redaction.value.status_code == 422


def test_constructor_derives_current_successful_redaction_and_rejects_stale_requested_runs(db_session, make_user, make_template):
    owner = make_user(email=f"source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    version = _version(db_session, owner, transcript)
    make_template(owner=owner, actor=owner)

    succeeded_run = _redaction_run(db_session, owner, transcript, version)
    failed_run = _redaction_run(db_session, owner, transcript, version, status=RedactionRunStatus.failed)
    with pytest.raises(AppError) as stale_run:
        create_source_bound_consultation_split_analysis(
            db_session, owner, transcript_id=transcript.id, transcript_version_id=version.id,
            redaction_run_id=failed_run.id,
        )
    assert stale_run.value.code == "consultation_split_source_stale"
    analysis = create_source_bound_consultation_split_analysis(
        db_session, owner, transcript_id=transcript.id, transcript_version_id=version.id,
        redaction_run_id=succeeded_run.id,
    )
    assert analysis.redaction_run_id == succeeded_run.id


def test_current_transcript_without_preview_establishes_required_redaction_before_analysis(
    db_session, make_user, make_template, monkeypatch,
):
    owner = make_user(email=f"source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    transcript.current_draft_text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="transcripts",
        field="current_draft_text_encrypted",
        record_id=transcript.id,
        plaintext=SOURCE_TEXT,
    )
    db_session.add(transcript)
    db_session.flush()
    make_template(owner=owner, actor=owner)
    seen_versions = []

    def establish_redaction(db, *, transcript_version):
        seen_versions.append(transcript_version.id)
        run = _redaction_run(db, owner, transcript, transcript_version)
        # The production boundary commits before optional NLP.  Exercise the
        # re-acquisition path rather than merely a same-transaction shortcut.
        db.commit()
        db.refresh(run)
        return run

    monkeypatch.setattr(
        "app.services.consultation_split_sources.ensure_redaction_run_for_transcript_version",
        establish_redaction,
    )
    analysis = create_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)

    assert seen_versions == [analysis.transcript_version_id]
    assert analysis.redaction_run_id is not None


def test_current_transcript_required_redaction_failure_creates_no_analysis(
    db_session, make_user, make_template, monkeypatch,
):
    owner = make_user(email=f"source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    transcript.current_draft_text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="transcripts",
        field="current_draft_text_encrypted",
        record_id=transcript.id,
        plaintext=SOURCE_TEXT,
    )
    db_session.add(transcript)
    db_session.flush()
    make_template(owner=owner, actor=owner)
    monkeypatch.setattr(
        "app.services.consultation_split_sources.ensure_redaction_run_for_transcript_version",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AppError(502, "redaction_failed", "Synthetic redaction failure")),
    )

    with pytest.raises(AppError) as failure:
        create_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)

    assert failure.value.code == "redaction_failed"
    assert db_session.query(ConsultationSplitAnalysis).filter_by(transcript_id=transcript.id).count() == 0


def test_constructor_rejects_blank_successful_redaction_without_another_saved_source(db_session, make_user, make_template):
    owner = make_user(email=f"source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    version = _version(db_session, owner, transcript)
    blank_run = _redaction_run(db_session, owner, transcript, version, redacted_text=" \n\t ")
    make_template(owner=owner, actor=owner)

    with pytest.raises(AppError) as blank_source:
        create_source_bound_consultation_split_analysis(
            db_session, owner, transcript_id=transcript.id, transcript_version_id=version.id,
            redaction_run_id=blank_run.id,
        )
    assert blank_source.value.code == "consultation_split_source_empty"

    _working_note(db_session, transcript)
    analysis = create_source_bound_consultation_split_analysis(
        db_session, owner, transcript_id=transcript.id, transcript_version_id=version.id,
        redaction_run_id=blank_run.id,
    )
    assert analysis.redaction_run_id == blank_run.id


def test_constructor_rejects_an_empty_source_but_pure_resolution_can_describe_it(db_session, make_user, make_template):
    owner = make_user(email=f"source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    make_template(owner=owner, actor=owner)
    state = resolve_consultation_split_analysis_source_state(db_session, owner, transcript_id=transcript.id)
    assert not any((
        state.source_snapshot["transcript"]["present"],
        state.source_snapshot["working_note"]["present"],
        state.source_snapshot["dictation"]["present"],
    ))
    with pytest.raises(AppError) as empty_source:
        create_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)
    assert empty_source.value.code == "consultation_split_source_empty"


def test_identical_source_text_is_owner_keyed_and_cannot_share_a_fingerprint(db_session, make_user, make_template):
    owner = make_user(email=f"source-owner-{uuid4()}@example.com")
    other = make_user(email=f"source-other-{uuid4()}@example.com", team=owner.team)
    first = _transcript(db_session, owner)
    second = _transcript(db_session, other)
    _working_note(db_session, first, at=utcnow())
    _working_note(db_session, second, at=first.working_note_updated_at)
    make_template(owner=owner, actor=owner, name="Same", description="Same", prompt_text=PROMPT_TEXT)
    make_template(owner=other, actor=other, name="Same", description="Same", prompt_text=PROMPT_TEXT)

    first_state = resolve_consultation_split_analysis_source_state(db_session, owner, transcript_id=first.id)
    second_state = resolve_consultation_split_analysis_source_state(db_session, other, transcript_id=second.id)
    assert first_state.source_snapshot["working_note"]["content_digest"] != second_state.source_snapshot["working_note"]["content_digest"]
    assert first_state.source_fingerprint != second_state.source_fingerprint


def test_server_bound_constructor_persists_encrypted_snapshots_without_client_fingerprint(db_session, make_user, make_template):
    owner = make_user(email=f"source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    _working_note(db_session, transcript)
    make_template(owner=owner, actor=owner, prompt_text=PROMPT_TEXT)

    analysis = create_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)
    db_session.flush()
    assert is_encrypted_envelope(analysis.source_snapshot_encrypted)
    assert is_encrypted_envelope(analysis.candidate_template_snapshot_encrypted)
    assert analysis.source_fingerprint == resolve_consultation_split_analysis_source_state(
        db_session, owner, transcript_id=transcript.id
    ).source_fingerprint
    stored_source = read_split_analysis_json(db_session, owner, analysis=analysis, field="source_snapshot_encrypted")
    stored_candidates = read_split_analysis_json(
        db_session, owner, analysis=analysis, field="candidate_template_snapshot_encrypted"
    )
    assert WORKING_TEXT in str(stored_source)
    assert WORKING_TEXT not in (analysis.source_snapshot_encrypted or "")
    assert PROMPT_TEXT not in str(stored_candidates)
    assert "source_fingerprint" not in signature(create_source_bound_consultation_split_analysis).parameters


def test_preparation_redacts_dynamic_sources_and_manual_pii_in_one_placeholder_namespace(
        db_session, make_user, make_template, monkeypatch,
):
    sensitive_marker = "Alice-Synthetic-Consultation-Marker-9d3d0f6a-7e51-4c14-8f3c-b2a7ee4f45cc"
    owner = make_user(email=f"source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    version = _version(db_session, owner, transcript)
    run = _redaction_run(db_session, owner, transcript, version, redacted_text=f"{sensitive_marker} Bob")
    _working_note(db_session, transcript, text=f"{sensitive_marker} Bob")
    _dictation(db_session, owner, transcript, text=f"{sensitive_marker} Bob")
    create_manual_pii_entity(db_session, owner, transcript_id=transcript.id, entity_type="PERSON", value="Alice")
    create_manual_pii_entity(db_session, owner, transcript_id=transcript.id, entity_type="PERSON", value=sensitive_marker)
    make_template(owner=owner, actor=owner)

    def redact_dynamic(db, text, *, team_id, start_index):
        return {
            "redacted_text": text.replace("Bob", f"[PHI-{start_index}]"),
            "phi_index": [{"index": start_index, "type": "PERSON", "value": "Bob", "placeholder": f"[PHI-{start_index}]"}],
        }

    monkeypatch.setattr("app.services.consultation_split_sources.redact_transient_text", redact_dynamic)
    analysis = create_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)
    snapshot = read_split_analysis_json(db_session, owner, analysis=analysis, field="source_snapshot_encrypted")
    values = snapshot["sources"]

    assert values["transcript"] == "[PHI-3] Bob"
    assert values["working_note"] == {"mode": "freeform", "value": "[PHI-3] [PHI-1]"}
    assert values["dictation"] == "[PHI-3] [PHI-2]"
    assert [item["index"] for item in snapshot["phi_index"]] == [1, 2, 3]
    # Ciphertext is base64 and can coincidentally contain a short plaintext
    # substring.  Use a long synthetic marker to prove that the encrypted
    # envelope does not persist the manual PII value without a flaky sentinel.
    assert sensitive_marker not in (analysis.source_snapshot_encrypted or "")
    assert is_encrypted_envelope(analysis.source_snapshot_encrypted)


def test_preparation_preserves_structured_working_note_shape_and_order(db_session, make_user, make_template, monkeypatch):
    owner = make_user(email=f"source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    transcript.working_note_mode = TranscriptWorkingNoteMode.structured
    transcript.working_note_updated_at = utcnow()
    transcript.structured_context_json = {
        "profile": "emis",
        "sections": {"problem": ["Alice"], "history": ["Bob"], "tasks": ["Call Alice"]},
    }
    make_template(owner=owner, actor=owner)

    def redact_dynamic(db, text, *, team_id, start_index):
        return {
            "redacted_text": f"[PHI-{start_index}]",
            "phi_index": [{"index": start_index, "type": "PERSON", "value": text, "placeholder": f"[PHI-{start_index}]"}],
        }

    monkeypatch.setattr("app.services.consultation_split_sources.redact_transient_text", redact_dynamic)
    analysis = create_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)
    snapshot = read_split_analysis_json(db_session, owner, analysis=analysis, field="source_snapshot_encrypted")
    working = snapshot["sources"]["working_note"]
    assert working["mode"] == "structured"
    assert working["value"]["sections"]["problem"] == ["[PHI-1]"]
    assert working["value"]["sections"]["history"] == ["[PHI-2]"]
    assert working["value"]["sections"]["tasks"] == ["[PHI-3]"]
    assert [item["index"] for item in snapshot["phi_index"]] == [1, 2, 3]


def test_preparation_continues_without_optional_nlp_and_uses_only_redacted_hints(db_session, make_user, make_template, monkeypatch):
    owner = make_user(email=f"source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    version = _version(db_session, owner, transcript)
    run = _redaction_run(db_session, owner, transcript, version)
    make_template(owner=owner, actor=owner)

    monkeypatch.setattr(
        "app.services.consultation_split_sources.successful_redacted_clinical_hints",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("synthetic optional NLP failure")),
    )
    analysis = create_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)
    snapshot = read_split_analysis_json(db_session, owner, analysis=analysis, field="source_snapshot_encrypted")
    assert snapshot["clinical_nlp_hints"] == []


def test_preparation_reads_only_successful_redacted_clinical_hints(db_session, make_user, make_template):
    owner = make_user(email=f"source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    version = _version(db_session, owner, transcript)
    redaction = _redaction_run(db_session, owner, transcript, version)
    make_template(owner=owner, actor=owner)
    before = resolve_consultation_split_analysis_source_state(db_session, owner, transcript_id=transcript.id)
    clinical_run = ClinicalEntityRun(
        transcript_id=transcript.id,
        transcript_version_id=version.id,
        redaction_run_id=redaction.id,
        owner_user_id=owner.id,
        team_id=owner.team_id,
        status=RedactionRunStatus.succeeded,
        source_text_redacted=True,
        api_provider="native",
        entity_count=1,
    )
    db_session.add(clinical_run)
    db_session.flush()
    entity = ClinicalEntity(
        id=uuid4(),
        clinical_entity_run_id=clinical_run.id,
        entity_order=1,
        entity_type="SYMPTOM",
        value_encrypted="",
        normalized_value_hash="synthetic",
    )
    entity.value_encrypted = encrypt_text_for_owner(
        db_session, owner_user_id=owner.id, table="clinical_entities", field="value_encrypted",
        record_id=entity.id, plaintext="cough",
    ) or ""
    db_session.add(entity)
    db_session.flush()

    assert successful_redacted_clinical_hints(
        db_session, transcript_version=version, redaction_run=redaction
    ) == [{"entity_type": "SYMPTOM", "text": "cough"}]
    after = resolve_consultation_split_analysis_source_state(db_session, owner, transcript_id=transcript.id)
    assert after.source_fingerprint != before.source_fingerprint
    assert "cough" not in str(after.source_snapshot["clinical_nlp_hints"])

    analysis = create_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)
    snapshot = read_split_analysis_json(db_session, owner, analysis=analysis, field="source_snapshot_encrypted")
    assert snapshot["clinical_nlp_hints"] == [{"entity_type": "SYMPTOM", "text": "cough"}]


def test_preparation_fails_closed_for_malformed_dynamic_redaction_and_stale_state(
    db_session, make_user, make_template, monkeypatch,
):
    owner = make_user(email=f"source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    _working_note(db_session, transcript)
    make_template(owner=owner, actor=owner)
    monkeypatch.setattr(
        "app.services.consultation_split_sources.redact_transient_text",
        lambda *args, **kwargs: {"redacted_text": "[PHI-1]", "phi_index": [{"bad": "shape"}]},
    )
    with pytest.raises(AppError) as malformed:
        create_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)
    assert malformed.value.code == "redaction_failed"
    assert db_session.query(ConsultationSplitAnalysis).filter_by(transcript_id=transcript.id).count() == 0

    def mutate_source(db, text, *, team_id, start_index):
        create_manual_pii_entity(
            db, owner, transcript_id=transcript.id, entity_type="PERSON", value="New protection"
        )
        return {"redacted_text": text, "phi_index": []}

    monkeypatch.setattr("app.services.consultation_split_sources.redact_transient_text", mutate_source)
    with pytest.raises(AppError) as stale:
        create_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)
    assert stale.value.code == "consultation_split_source_stale"
    assert db_session.query(ConsultationSplitAnalysis).filter_by(transcript_id=transcript.id).count() == 0


def test_manual_pii_identity_changes_the_server_fingerprint_without_decrypting_values(db_session, make_user, make_template):
    owner = make_user(email=f"source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    _working_note(db_session, transcript)
    make_template(owner=owner, actor=owner)
    before = resolve_consultation_split_analysis_source_state(db_session, owner, transcript_id=transcript.id)
    create_manual_pii_entity(db_session, owner, transcript_id=transcript.id, entity_type="PERSON", value="Alice Example")
    after = resolve_consultation_split_analysis_source_state(db_session, owner, transcript_id=transcript.id)
    assert after.source_fingerprint != before.source_fingerprint
    assert "Alice Example" not in str(after.source_snapshot["manual_pii"])


def test_manual_pii_identity_excludes_legacy_hash_and_source_value_but_tracks_add_remove(
    db_session, make_user, make_template,
):
    owner = make_user(email=f"source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    _working_note(db_session, transcript)
    make_template(owner=owner, actor=owner)
    baseline = resolve_consultation_split_analysis_source_state(db_session, owner, transcript_id=transcript.id)
    source_value = "Legacy manual PII source value"
    legacy_hash = sha256(source_value.strip().lower().encode("utf-8")).hexdigest()
    entity_id = uuid4()
    entity = TranscriptManualPiiEntity(
        id=entity_id,
        transcript_id=transcript.id,
        owner_user_id=owner.id,
        team_id=owner.team_id,
        entity_type="PERSON",
        original_value_encrypted=encrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="transcript_manual_pii_entities",
            field="original_value_encrypted",
            record_id=entity_id,
            plaintext=source_value,
        ) or "",
        normalized_value_hash=legacy_hash,
    )
    db_session.add(entity)
    db_session.flush()

    added = resolve_consultation_split_analysis_source_state(db_session, owner, transcript_id=transcript.id)
    analysis = create_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)
    persisted = read_split_analysis_json(db_session, owner, analysis=analysis, field="source_snapshot_encrypted")
    assert added.source_fingerprint != baseline.source_fingerprint
    assert source_value not in str(added.source_snapshot)
    assert legacy_hash not in str(added.source_snapshot)
    assert source_value not in str(persisted["source_state"])
    assert legacy_hash not in str(persisted["source_state"])

    db_session.delete(entity)
    db_session.flush()
    removed = resolve_consultation_split_analysis_source_state(db_session, owner, transcript_id=transcript.id)
    assert removed.source_fingerprint != added.source_fingerprint
    assert removed.source_fingerprint == baseline.source_fingerprint


def test_candidate_snapshot_applies_shared_catalogue_and_metadata_bounds(db_session, make_user, make_template, monkeypatch):
    owner = make_user(email=f"source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    _working_note(db_session, transcript)
    make_template(owner=owner, actor=owner, description="x" * 2001)
    with pytest.raises(AppError) as long_description:
        resolve_consultation_split_analysis_source_state(db_session, owner, transcript_id=transcript.id)
    assert long_description.value.code == "consultation_split_analysis_candidates_invalid"
    assert "x" * 20 not in long_description.value.message

    monkeypatch.setattr(
        "app.services.consultation_split_sources.list_available_templates_for_user",
        lambda *_args, **_kwargs: [object()] * 101,
    )
    with pytest.raises(AppError) as overflow:
        resolve_consultation_split_analysis_source_state(db_session, owner, transcript_id=transcript.id)
    assert overflow.value.code == "consultation_split_analysis_candidates_invalid"


def test_constructor_rejects_an_old_transcript_version_when_saved_draft_has_changed(db_session, make_user, make_template):
    owner = make_user(email=f"source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    old_version = _version(db_session, owner, transcript)
    make_template(owner=owner, actor=owner)
    transcript.current_draft_text_encrypted = encrypt_text_for_owner(
        db_session, owner_user_id=owner.id, table="transcripts", field="current_draft_text_encrypted",
        record_id=transcript.id, plaintext="A newer saved transcript draft",
    )
    db_session.flush()

    with pytest.raises(AppError) as stale:
        create_source_bound_consultation_split_analysis(
            db_session, owner, transcript_id=transcript.id, transcript_version_id=old_version.id,
        )
    assert stale.value.code == "consultation_split_source_stale"
    assert db_session.query(ConsultationSplitAnalysis).filter_by(transcript_id=transcript.id).count() == 0


@pytest.mark.parametrize("include_transcript,include_working,include_dictation", [
    (True, False, False),
    (False, True, False),
    (False, False, True),
    (True, True, True),
])
def test_prepare_source_bound_analysis_returns_redacted_current_sources_without_an_analysis_row(
    db_session, make_user, make_template, include_transcript, include_working, include_dictation,
):
    owner = make_user(email=f"prepared-source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    version = _version(db_session, owner, transcript) if include_transcript else None
    run = _redaction_run(db_session, owner, transcript, version) if version is not None else None
    if include_working:
        _working_note(db_session, transcript)
    if include_dictation:
        _dictation(db_session, owner, transcript)
    make_template(owner=owner, actor=owner, prompt_text=PROMPT_TEXT)
    attempts_before = db_session.query(ProviderAttempt).count()
    outbox_before = db_session.query(TaskDispatchOutbox).count()

    prepared = prepare_source_bound_consultation_split_analysis(
        db_session,
        owner,
        transcript_id=transcript.id,
        transcript_version_id=version.id if version is not None else None,
        redaction_run_id=run.id if run is not None else None,
    )

    assert prepared.source_state.transcript_id == transcript.id
    assert prepared.source_state.transcript_version_id == (version.id if version is not None else None)
    assert prepared.source_state.redaction_run_id == (run.id if run is not None else None)
    assert prepared.source_snapshot["source_state"] == prepared.source_state.source_snapshot
    assert prepared.source_snapshot["sources"]["transcript"] == ("Redacted synthetic" if include_transcript else "")
    assert bool(prepared.source_snapshot["sources"]["working_note"]["value"]) is include_working
    assert bool(prepared.source_snapshot["sources"]["dictation"]) is include_dictation
    assert db_session.query(ConsultationSplitAnalysis).filter_by(transcript_id=transcript.id).count() == 0
    assert db_session.query(ProviderAttempt).count() == attempts_before
    assert db_session.query(TaskDispatchOutbox).count() == outbox_before


def test_constructor_delegates_to_preparation_and_preserves_encrypted_persistence(
    db_session, make_user, make_template, monkeypatch,
):
    owner = make_user(email=f"prepared-source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    _working_note(db_session, transcript)
    make_template(owner=owner, actor=owner, prompt_text=PROMPT_TEXT)
    original_prepare = prepare_source_bound_consultation_split_analysis
    calls: list[object] = []

    def observe_prepare(*args, **kwargs):
        result = original_prepare(*args, **kwargs)
        calls.append(result)
        return result

    monkeypatch.setattr("app.services.consultation_split_sources.prepare_source_bound_consultation_split_analysis", observe_prepare)
    analysis = create_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)

    assert len(calls) == 1
    assert analysis.source_fingerprint == calls[0].source_state.source_fingerprint
    assert is_encrypted_envelope(analysis.source_snapshot_encrypted)


@pytest.mark.parametrize(
    "status",
    [
        ConsultationSplitAnalysisStatus.queued,
        ConsultationSplitAnalysisStatus.processing,
        ConsultationSplitAnalysisStatus.ready,
        ConsultationSplitAnalysisStatus.not_required,
        ConsultationSplitAnalysisStatus.failed,
    ],
)
def test_locked_prepared_analysis_cache_is_owner_root_fingerprint_scoped_and_reuses_non_stale_statuses(
    db_session, make_user, make_template, status,
):
    owner = make_user(email=f"prepared-source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    _working_note(db_session, transcript)
    make_template(owner=owner, actor=owner, prompt_text=PROMPT_TEXT)
    analysis = create_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)
    analysis.status = status
    db_session.flush()
    prepared = prepare_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)

    cached = find_cached_prepared_consultation_split_analysis(db_session, owner, prepared=prepared)

    assert cached is not None
    assert cached.id == analysis.id
    assert cached.owner_user_id == owner.id
    assert cached.transcript_id == transcript.id
    assert cached.source_fingerprint == prepared.source_state.source_fingerprint

    analysis.status = ConsultationSplitAnalysisStatus.stale
    db_session.flush()
    assert find_cached_prepared_consultation_split_analysis(db_session, owner, prepared=prepared) is None


def test_preparation_rejects_stale_current_transcript_without_inserting_analysis(
    db_session, make_user, make_template,
):
    owner = make_user(email=f"prepared-source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    version = _version(db_session, owner, transcript)
    _redaction_run(db_session, owner, transcript, version)
    make_template(owner=owner, actor=owner, prompt_text=PROMPT_TEXT)
    transcript.current_draft_text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="transcripts",
        field="current_draft_text_encrypted",
        record_id=transcript.id,
        plaintext="newer saved source",
    )
    db_session.flush()

    with pytest.raises(AppError) as stale:
        prepare_source_bound_consultation_split_analysis(
            db_session,
            owner,
            transcript_id=transcript.id,
            transcript_version_id=version.id,
        )

    assert stale.value.code == "consultation_split_source_stale"
    assert db_session.query(ConsultationSplitAnalysis).filter_by(transcript_id=transcript.id).count() == 0


def test_preparation_locks_user_then_transcript_then_ordered_dictation_rows(
    db_session, make_user, make_template,
):
    owner = make_user(email=f"prepared-source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    _working_note(db_session, transcript)
    _dictation(db_session, owner, transcript)
    make_template(owner=owner, actor=owner, prompt_text=PROMPT_TEXT)
    locked_statements: list[str] = []

    def observe_lock(_connection, _cursor, statement, _parameters, _context, _executemany):
        normalized = " ".join(statement.split()).lower()
        if "for update" in normalized:
            locked_statements.append(normalized)

    event.listen(db_session.bind, "before_cursor_execute", observe_lock)
    try:
        prepare_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)
    finally:
        event.remove(db_session.bind, "before_cursor_execute", observe_lock)

    user_lock = next(index for index, statement in enumerate(locked_statements) if "from users" in statement)
    transcript_lock = next(index for index, statement in enumerate(locked_statements) if "from transcripts" in statement)
    dictation_locks = [
        (index, statement)
        for index, statement in enumerate(locked_statements)
        if "from post_consultation_dictations" in statement
    ]
    assert user_lock < transcript_lock < dictation_locks[0][0]
    assert all("order by post_consultation_dictations.id" in statement for _, statement in dictation_locks)


def test_generation_queue_precheck_uses_canonical_source_lock_order(db_session, make_user):
    owner = make_user(email=f"generation-lock-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    _dictation(db_session, owner, transcript)
    locked_statements: list[str] = []

    def observe_lock(_connection, _cursor, statement, _parameters, _context, _executemany):
        normalized = " ".join(statement.split()).lower()
        if "for update" in normalized:
            locked_statements.append(normalized)

    event.listen(db_session.bind, "before_cursor_execute", observe_lock)
    try:
        locked_transcript, waiting = _ensure_generation_can_be_queued(db_session, transcript=transcript)
    finally:
        event.remove(db_session.bind, "before_cursor_execute", observe_lock)

    assert locked_transcript.id == transcript.id
    assert waiting is False
    user_lock = next(index for index, statement in enumerate(locked_statements) if "from users" in statement)
    transcript_lock = next(index for index, statement in enumerate(locked_statements) if "from transcripts" in statement)
    dictation_lock = next(
        index for index, statement in enumerate(locked_statements) if "from post_consultation_dictations" in statement
    )
    assert user_lock < transcript_lock < dictation_lock
    assert "order by post_consultation_dictations.id" in locked_statements[dictation_lock]


def test_empty_current_transcript_never_reuses_historical_version_for_preparation(
    db_session, make_user, make_template,
):
    owner = make_user(email=f"prepared-source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    historical_version = _version(db_session, owner, transcript)
    _redaction_run(db_session, owner, transcript, historical_version)
    transcript.current_draft_text_encrypted = None
    _working_note(db_session, transcript)
    make_template(owner=owner, actor=owner, prompt_text=PROMPT_TEXT)

    prepared = prepare_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)
    forced_current_state = resolve_consultation_split_analysis_source_state(
        db_session,
        owner,
        transcript_id=transcript.id,
        force_no_transcript_version=True,
    )

    assert prepared.source_state.transcript_version_id is None
    assert prepared.source_state.redaction_run_id is None
    assert prepared.source_snapshot["source_state"]["transcript"] == {
        "present": False,
        "transcript_version_id": None,
        "redaction_run_id": None,
    }
    assert prepared.source_snapshot["sources"]["transcript"] == ""
    assert prepared.source_state.source_fingerprint == forced_current_state.source_fingerprint

    transcript.working_note_mode = None
    transcript.freeform_working_note_encrypted = None
    transcript.working_note_updated_at = None
    db_session.flush()
    with pytest.raises(AppError) as empty:
        prepare_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)
    assert empty.value.code == "consultation_split_source_empty"


@pytest.mark.parametrize("change", ["working_note", "dictation", "transcript", "manual_pii", "candidate"])
def test_cache_recomputes_current_server_binding_and_rejects_old_or_forged_prepared_state(
    db_session, make_user, make_template, change,
):
    owner = make_user(email=f"prepared-source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    if change == "transcript":
        version = _version(db_session, owner, transcript)
        _redaction_run(db_session, owner, transcript, version)
    _working_note(db_session, transcript)
    template = make_template(owner=owner, actor=owner, prompt_text=PROMPT_TEXT)
    analysis = create_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)
    prepared = prepare_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)
    assert find_cached_prepared_consultation_split_analysis(db_session, owner, prepared=prepared).id == analysis.id

    if change == "working_note":
        _working_note(db_session, transcript, text="changed saved working note", at=utcnow() + timedelta(seconds=1))
    elif change == "dictation":
        _dictation(db_session, owner, transcript)
    elif change == "transcript":
        transcript.current_draft_text_encrypted = encrypt_text_for_owner(
            db_session,
            owner_user_id=owner.id,
            table="transcripts",
            field="current_draft_text_encrypted",
            record_id=transcript.id,
            plaintext="changed saved transcript",
        )
        db_session.flush()
    elif change == "manual_pii":
        create_manual_pii_entity(
            db_session,
            owner,
            transcript_id=transcript.id,
            entity_type="PERSON",
            value="Synthetic PII",
        )
    else:
        template.description = "changed candidate metadata"
        db_session.flush()

    assert find_cached_prepared_consultation_split_analysis(db_session, owner, prepared=prepared) is None
    forged = replace(prepared, source_state=replace(prepared.source_state, source_fingerprint="f" * 64))
    assert find_cached_prepared_consultation_split_analysis(db_session, owner, prepared=forged) is None


def test_current_analysis_source_proof_never_creates_a_missing_owner_key(db_session, make_user):
    owner = make_user(email=f"prepared-source-owner-{uuid4()}@example.com")
    assert db_session.scalar(
        select(UserEncryptionKey).where(UserEncryptionKey.user_id == owner.id)
    ) is None
    transcript = _transcript(db_session, owner)
    transcript.working_note_mode = TranscriptWorkingNoteMode.freeform
    transcript.freeform_working_note_encrypted = "Legacy plaintext working note"
    transcript.working_note_updated_at = utcnow()
    analysis = ConsultationSplitAnalysis(
        owner_user_id=owner.id,
        team_id=owner.team_id,
        transcript_id=transcript.id,
        source_fingerprint="a" * 64,
        status=ConsultationSplitAnalysisStatus.queued,
        retention_expires_at=transcript.retention_expires_at,
    )
    db_session.add(analysis)
    db_session.flush()

    assert current_consultation_split_analysis_source_matches(
        db_session, owner, transcript=transcript, analysis=analysis
    ) is False
    assert db_session.scalar(
        select(UserEncryptionKey).where(UserEncryptionKey.user_id == owner.id)
    ) is None
    assert not any(isinstance(row, UserEncryptionKey) for row in db_session.new)


def test_cache_recomputation_rejects_expired_transcript_root(db_session, make_user, make_template):
    owner = make_user(email=f"prepared-source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    _working_note(db_session, transcript)
    make_template(owner=owner, actor=owner, prompt_text=PROMPT_TEXT)
    create_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)
    prepared = prepare_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)
    transcript.retention_expires_at = utcnow() - timedelta(seconds=1)
    db_session.flush()

    with pytest.raises(AppError) as expired:
        find_cached_prepared_consultation_split_analysis(db_session, owner, prepared=prepared)
    assert expired.value.status_code == 404


def test_cache_never_creates_or_redacts_when_prepared_transcript_run_is_missing_or_failed(
    db_session, make_user, make_template, monkeypatch,
):
    owner = make_user(email=f"prepared-source-owner-{uuid4()}@example.com")
    transcript = _transcript(db_session, owner)
    version = _version(db_session, owner, transcript)
    run = _redaction_run(db_session, owner, transcript, version)
    make_template(owner=owner, actor=owner, prompt_text=PROMPT_TEXT)
    create_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)
    prepared = prepare_source_bound_consultation_split_analysis(db_session, owner, transcript_id=transcript.id)
    attempts_before = db_session.query(ProviderAttempt).count()
    outbox_before = db_session.query(TaskDispatchOutbox).count()
    monkeypatch.setattr(
        "app.services.consultation_split_sources.ensure_redaction_run_for_transcript_version",
        lambda *_args, **_kwargs: pytest.fail("cache must not redaction-prepare"),
    )
    monkeypatch.setattr(db_session, "commit", lambda: pytest.fail("cache must not commit"))

    forged_missing_run = replace(
        prepared,
        source_state=replace(prepared.source_state, redaction_run_id=uuid4()),
    )
    assert find_cached_prepared_consultation_split_analysis(db_session, owner, prepared=forged_missing_run) is None

    run.status = RedactionRunStatus.failed
    db_session.flush()
    assert find_cached_prepared_consultation_split_analysis(db_session, owner, prepared=prepared) is None
    assert db_session.query(ProviderAttempt).count() == attempts_before
    assert db_session.query(TaskDispatchOutbox).count() == outbox_before
