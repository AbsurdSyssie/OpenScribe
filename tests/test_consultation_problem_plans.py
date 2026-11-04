"""Focused tests for the owner-encrypted living consultation problem plan."""

from datetime import timedelta
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.errors import AppError
from app.models import (
    ConsultationProblemPlan,
    ConsultationSplitBatch,
    ConsultationSplitExecution,
    ConsultationSplitExecutionStatus,
    ConsultationSplitAnalysisStatus,
    ConsultationSplitIntent,
    ConsultationSplitIntentStatus,
    TeamRole,
    Transcript,
    TranscriptIngestionMode,
    TranscriptStatus,
    PromptTemplateVersion,
    TeamLlmSelection,
    UserAppPreference,
    utcnow,
)
from app.schemas.consultation_split import (
    ConsultationProblemPlanProblem,
    ConsultationProblemPlanReplace,
    ConsultationProblemPlanTile,
)
from app.services.consultation_problem_plans import (
    apply_problem_plan_suggestion,
    create_consultation_notes_from_problem_plan,
    read_consultation_problem_plan,
    replace_consultation_problem_plan,
)
from app.services.consultation_splits import create_split_analysis, read_split_batch_json
from app.services.consultation_problem_plan_sources import redact_problem_plan_titles
from app.services.content_crypto import is_encrypted_envelope
from app.services.transcripts import delete_transcripts


def _transcript(db, owner, *, expired=False):
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=owner.team_id,
        title="Synthetic consultation",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=(utcnow() - timedelta(seconds=1) if expired
                              else utcnow() + timedelta(days=30)),
    )
    db.add(transcript)
    db.commit()
    db.refresh(transcript)
    return transcript


def _enable(db, owner, monkeypatch):
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    db.add(UserAppPreference(
        user_id=owner.id,
        preferences_json={"split_consultations_into_separate_notes": True},
    ))
    db.commit()


def _payload(*, revision, operation=None, tiles=None, problems=None):
    return ConsultationProblemPlanReplace(
        expected_revision=revision,
        operation_id=operation or uuid4(),
        tiles=tiles or [ConsultationProblemPlanTile(uuid=uuid4(), is_primary=True)],
        problems=problems or [],
    )


def test_plan_replace_is_encrypted_manual_and_idempotent(db_session, make_user, monkeypatch):
    owner = make_user(email="living-plan-owner@example.com")
    _enable(db_session, owner, monkeypatch)
    transcript = _transcript(db_session, owner)
    tile_id = uuid4()
    problem_id = uuid4()
    payload = _payload(
        revision=0,
        operation=uuid4(),
        tiles=[ConsultationProblemPlanTile(uuid=tile_id, is_primary=True)],
        problems=[ConsultationProblemPlanProblem(
            uuid=problem_id, title="  Knee   pain ", origin="manual", ignored=False,
            group_id=tile_id,
        )],
    )

    first = replace_consultation_problem_plan(
        db_session, owner, transcript_id=transcript.id, payload=payload,
    )
    row = db_session.scalar(select(ConsultationProblemPlan).where(
        ConsultationProblemPlan.transcript_id == transcript.id,
    ))
    assert first.revision == 1 and first.manual_override is True
    assert first.problems[0].title == "Knee pain"
    assert row is not None and is_encrypted_envelope(row.plan_encrypted)
    assert "Knee pain" not in row.plan_encrypted

    replay = replace_consultation_problem_plan(
        db_session, owner, transcript_id=transcript.id, payload=payload,
    )
    assert replay.revision == first.revision
    assert replay.plan_id == first.plan_id


def test_plan_replace_rejects_stale_revision_and_reused_operation_with_changed_body(
    db_session, make_user, monkeypatch,
):
    owner = make_user(email="living-plan-cas@example.com")
    _enable(db_session, owner, monkeypatch)
    transcript = _transcript(db_session, owner)
    operation = uuid4()
    first_payload = _payload(revision=0, operation=operation)
    replace_consultation_problem_plan(db_session, owner, transcript_id=transcript.id, payload=first_payload)

    with pytest.raises(AppError) as stale:
        replace_consultation_problem_plan(
            db_session, owner, transcript_id=transcript.id,
            payload=_payload(revision=0),
        )
    assert stale.value.code == "consultation_problem_plan_conflict"

    with pytest.raises(AppError) as reused:
        replace_consultation_problem_plan(
            db_session, owner, transcript_id=transcript.id,
            payload=_payload(revision=0, operation=operation,
                             problems=[ConsultationProblemPlanProblem(
                                 uuid=uuid4(), title="Different", origin="manual", ignored=False,
                                 group_id=None,
                             )]),
        )
    assert reused.value.code == "consultation_problem_plan_conflict"


def test_plan_owner_boundary_expiry_and_deleted_root(db_session, make_user, monkeypatch):
    owner = make_user(email="living-plan-private-owner@example.com")
    foreign = make_user(email="living-plan-private-foreign@example.com", team=owner.team)
    leader = make_user(email="living-plan-private-leader@example.com", team=owner.team,
                       team_role=TeamRole.leader)
    admin = make_user(email="living-plan-private-admin@example.com", is_system_admin=True)
    _enable(db_session, owner, monkeypatch)
    for actor in (foreign, leader, admin):
        _enable(db_session, actor, monkeypatch)
    transcript = _transcript(db_session, owner)
    replace_consultation_problem_plan(
        db_session, owner, transcript_id=transcript.id, payload=_payload(revision=0),
    )

    for actor in (foreign, leader, admin):
        with pytest.raises(AppError) as denied:
            read_consultation_problem_plan(db_session, actor, transcript_id=transcript.id)
        assert denied.value.status_code in (403, 404)
        with pytest.raises(AppError) as write_denied:
            replace_consultation_problem_plan(
                db_session, actor, transcript_id=transcript.id,
                payload=_payload(revision=1),
            )
        assert write_denied.value.status_code in (403, 404)

    expired = _transcript(db_session, owner, expired=True)
    with pytest.raises(AppError) as hidden:
        read_consultation_problem_plan(db_session, owner, transcript_id=expired.id)
    assert hidden.value.status_code == 404

    delete_transcripts(db_session, owner, transcript_ids=[transcript.id])
    assert db_session.scalar(select(ConsultationProblemPlan).where(
        ConsultationProblemPlan.transcript_id == transcript.id,
    )) is None
    with pytest.raises(AppError) as deleted:
        read_consultation_problem_plan(db_session, owner, transcript_id=transcript.id)
    assert deleted.value.status_code == 404


def test_plan_schema_bounds_titles_and_replace_supports_six_notes_and_ignored_problems(
    db_session, make_user, make_template, monkeypatch,
):
    owner = make_user(email="living-plan-shapes@example.com")
    _enable(db_session, owner, monkeypatch)
    transcript = _transcript(db_session, owner)
    templates = [make_template(owner=owner, name=f"Plan template {i}") for i in range(5)]
    tile_ids = [uuid4() for _ in range(6)]
    tiles = [ConsultationProblemPlanTile(uuid=tile_ids[0], is_primary=True)]
    tiles.extend(ConsultationProblemPlanTile(uuid=tile_ids[i], is_primary=False,
                                             template_id=templates[i - 1].id)
                 for i in range(1, 6))
    problems = [ConsultationProblemPlanProblem(
        uuid=uuid4(), title=f"Problem {i}", origin="manual", ignored=False,
        group_id=tile_ids[i],
    ) for i in range(6)]
    projection = replace_consultation_problem_plan(
        db_session, owner, transcript_id=transcript.id,
        payload=_payload(revision=0, tiles=tiles, problems=problems),
    )
    assert len(projection.tiles) == 6
    assert len(projection.problems) == 6

    reduced_tiles = tiles[:5]
    ignored_plan = [ConsultationProblemPlanProblem(
        uuid=uuid4(), title=f"Next problem {i}", origin="manual", ignored=(i == 5),
        group_id=None if i == 5 else reduced_tiles[i].uuid,
    ) for i in range(6)]
    updated = replace_consultation_problem_plan(
        db_session, owner, transcript_id=transcript.id,
        payload=_payload(revision=1, tiles=reduced_tiles, problems=ignored_plan),
    )
    assert len(updated.tiles) == 5
    assert updated.problems[-1].ignored is True

    with pytest.raises(ValidationError):
        ConsultationProblemPlanProblem(
            uuid=uuid4(), title="   ", origin="manual", ignored=False,
        )
    with pytest.raises(ValidationError):
        ConsultationProblemPlanProblem(
            uuid=uuid4(), title="  ", origin="manual", ignored=True,
        )
    with pytest.raises(ValidationError):
        ConsultationProblemPlanReplace(
            expected_revision=2, operation_id=uuid4(), tiles=tiles,
            problems=[ConsultationProblemPlanProblem(
                uuid=uuid4(), title=f"Ignored {i}", origin="manual", ignored=True,
            ) for i in range(7)],
        )


def test_problem_title_redaction_maps_titles_without_returning_raw_phi(db_session, make_user, monkeypatch):
    owner = make_user(email="living-plan-redaction@example.com")
    transcript = _transcript(db_session, owner)
    problem_id = str(uuid4())
    source = {
        "source_state": {
            "owner_user_id": str(owner.id), "team_id": str(owner.team_id),
            "transcript_id": str(transcript.id),
        },
        "phi_index": [],
    }
    monkeypatch.setattr(
        "app.services.consultation_problem_plan_sources.redact_transient_text",
        lambda _db, value, *, team_id, start_index: {
            "redacted_text": f"[PHI-{start_index}]",
            "phi_index": [{"index": start_index, "placeholder": f"[PHI-{start_index}]",
                           "type": "PERSON", "value": value}],
        },
    )
    titles, phi_map = redact_problem_plan_titles(
        db_session, owner, transcript_id=transcript.id, source_snapshot=source,
        problems=[{"uuid": problem_id, "title": "Synthetic patient knee pain"}],
    )
    assert set(titles) == {problem_id}
    assert titles[problem_id] == "[PHI-1]"
    assert phi_map == [{"index": 1, "placeholder": "[PHI-1]",
                        "type": "PERSON", "value": "Synthetic patient knee pain"}]
    assert "Synthetic patient knee pain" not in titles[problem_id]


def _suggestion_rows(db, owner, transcript, *, template, plan_revision):
    analysis = create_split_analysis(
        db, owner, transcript_id=transcript.id, source_fingerprint="a" * 64,
        source_snapshot={"safe": "synthetic"}, candidate_template_snapshot={"templates": []},
        provider_snapshot={}, proposal={"topics": [
            {"topic_uuid": str(uuid4()), "title": "Existing ignored concern", "is_primary": False,
             "disposition": "separate_note", "template_id": str(template.id)},
            {"topic_uuid": str(uuid4()), "title": "New suggested concern", "is_primary": False,
             "disposition": "separate_note", "template_id": str(template.id)},
        ]},
    )
    analysis.status = ConsultationSplitAnalysisStatus.ready
    intent = ConsultationSplitIntent(
        owner_user_id=owner.id, team_id=owner.team_id, transcript_id=transcript.id,
        analysis_id=analysis.id, problem_plan_id=db.scalar(select(ConsultationProblemPlan.id).where(
            ConsultationProblemPlan.transcript_id == transcript.id,
        )),
        problem_plan_revision=plan_revision, intent_kind="problem_suggestion",
        client_idempotency_key=uuid4(), manual_review_requested=True,
        generation_snapshot_encrypted="", status=ConsultationSplitIntentStatus.analysis_pending,
        retention_expires_at=transcript.retention_expires_at,
    )
    db.add(intent)
    db.commit()
    return analysis, intent


def test_successful_suggestion_replaces_active_plan_preserves_ignored_and_manual_override(
    db_session, make_user, make_template, monkeypatch,
):
    owner = make_user(email="living-plan-suggestion@example.com")
    _enable(db_session, owner, monkeypatch)
    transcript = _transcript(db_session, owner)
    template = make_template(owner=owner, name="Suggestion template")
    tile_id, active_id, ignored_id = uuid4(), uuid4(), uuid4()
    initial = _payload(
        revision=0,
        tiles=[ConsultationProblemPlanTile(uuid=tile_id, is_primary=True, template_id=template.id)],
        problems=[
            ConsultationProblemPlanProblem(uuid=active_id, title="Old active concern", origin="manual",
                                           ignored=False, group_id=tile_id),
            ConsultationProblemPlanProblem(uuid=ignored_id, title="Existing ignored concern", origin="manual",
                                           ignored=True, restore_group_id=tile_id),
        ],
    )
    before = replace_consultation_problem_plan(
        db_session, owner, transcript_id=transcript.id, payload=initial,
    )
    analysis, intent = _suggestion_rows(
        db_session, owner, transcript, template=template, plan_revision=before.revision,
    )
    monkeypatch.setattr(
        "app.services.consultation_split_intents._submitted_generation_snapshot",
        lambda *_args, **_kwargs: {"template_id": str(template.id)},
    )
    monkeypatch.setattr(
        "app.services.consultation_split_sources.current_consultation_split_analysis_source_matches",
        lambda *_args, **_kwargs: True,
    )

    assert apply_problem_plan_suggestion(db_session, owner, intent_id=intent.id, analysis=analysis) is True
    projection = read_consultation_problem_plan(db_session, owner, transcript_id=transcript.id)
    assert projection.revision == before.revision + 1
    assert projection.manual_override is True
    ignored = [problem for problem in projection.problems if problem.ignored]
    assert len(ignored) == 1
    assert ignored[0].uuid == ignored_id
    assert ignored[0].title == "Existing ignored concern"
    active = [problem for problem in projection.problems if not problem.ignored]
    assert [problem.title for problem in active] == ["New suggested concern"]
    assert len(projection.tiles) == 2


def test_stale_suggestion_failure_keeps_current_plan_unchanged(
    db_session, make_user, make_template, monkeypatch,
):
    owner = make_user(email="living-plan-suggestion-stale@example.com")
    _enable(db_session, owner, monkeypatch)
    transcript = _transcript(db_session, owner)
    template = make_template(owner=owner, name="Stale suggestion template")
    tile_id = uuid4()
    initial = _payload(
        revision=0,
        tiles=[ConsultationProblemPlanTile(uuid=tile_id, is_primary=True, template_id=template.id)],
        problems=[ConsultationProblemPlanProblem(uuid=uuid4(), title="Current concern", origin="manual",
                                                 ignored=False, group_id=tile_id)],
    )
    before = replace_consultation_problem_plan(db_session, owner, transcript_id=transcript.id, payload=initial)
    analysis, intent = _suggestion_rows(
        db_session, owner, transcript, template=template, plan_revision=before.revision,
    )
    newer = _payload(
        revision=before.revision,
        tiles=[ConsultationProblemPlanTile(uuid=tile_id, is_primary=True, template_id=template.id)],
        problems=[ConsultationProblemPlanProblem(uuid=uuid4(), title="Clinician update", origin="manual",
                                                 ignored=False, group_id=tile_id)],
    )
    current = replace_consultation_problem_plan(db_session, owner, transcript_id=transcript.id, payload=newer)
    monkeypatch.setattr(
        "app.services.consultation_split_intents._submitted_generation_snapshot",
        lambda *_args, **_kwargs: {"template_id": str(template.id)},
    )

    assert apply_problem_plan_suggestion(db_session, owner, intent_id=intent.id, analysis=analysis) is False
    projection = read_consultation_problem_plan(db_session, owner, transcript_id=transcript.id)
    assert projection.revision == current.revision
    assert projection.manual_override is True
    assert [problem.title for problem in projection.problems] == ["Clinician update"]


def test_create_freezes_grouped_v2_sources_templates_provider_and_replay_vs_new_plan(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, monkeypatch,
):
    owner = make_user(email="living-plan-batch-snapshot@example.com")
    _enable(db_session, owner, monkeypatch)
    primary_template = make_template(owner=owner, name="Frozen primary", prompt_text="Original primary prompt")
    secondary_template = make_template(owner=owner, name="Frozen secondary", prompt_text="Original secondary prompt")
    first_config = make_llm_config(
        team=owner.team, actor=owner, available_models_json=["gpt-4o-mini"],
    )
    selection = make_llm_selection(
        config=first_config, actor=owner, allowed_models_json=["gpt-4o-mini"],
    )
    transcript = _transcript(db_session, owner)
    primary_id, secondary_id = uuid4(), uuid4()
    back_id, bp_id, ignored_id = uuid4(), uuid4(), uuid4()
    saved = replace_consultation_problem_plan(
        db_session, owner, transcript_id=transcript.id,
        payload=_payload(
            revision=0,
            tiles=[
                ConsultationProblemPlanTile(uuid=primary_id, is_primary=True, template_id=primary_template.id),
                ConsultationProblemPlanTile(uuid=secondary_id, is_primary=False, template_id=secondary_template.id),
            ],
            problems=[
                ConsultationProblemPlanProblem(uuid=back_id, title="Synthetic back pain", origin="manual",
                                               ignored=False, group_id=secondary_id),
                ConsultationProblemPlanProblem(uuid=bp_id, title="Synthetic blood pressure", origin="manual",
                                               ignored=False, group_id=primary_id),
                ConsultationProblemPlanProblem(uuid=ignored_id, title="Synthetic excluded issue", origin="manual",
                                               ignored=True, restore_group_id=primary_id),
            ],
        ),
    )
    state = SimpleNamespace(
        transcript_version_id=None, redaction_run_id=None, source_fingerprint="b" * 64,
    )
    source_snapshot = {
        "source_state": {"owner_user_id": str(owner.id), "team_id": str(owner.team_id),
                         "transcript_id": str(transcript.id),
                         "transcript": {"transcript_version_id": None,
                                        "redaction_run_id": None, "present": False}},
        "sources": {"transcript": "[PHI-1] synthetic source",
                    "working_note": {"mode": None, "value": None}, "dictation": ""},
        "clinical_nlp_hints": [], "phi_index": [],
    }
    prepared = SimpleNamespace(
        source_state=state, source_snapshot=source_snapshot,
        retention_expires_at=transcript.retention_expires_at,
    )
    monkeypatch.setattr(
        "app.services.consultation_split_sources.prepare_source_bound_consultation_split_analysis",
        lambda *_args, **_kwargs: prepared,
    )
    monkeypatch.setattr(
        "app.services.consultation_split_sources.resolve_ready_consultation_split_analysis_source_state",
        lambda *_args, **_kwargs: SimpleNamespace(source_fingerprint=state.source_fingerprint),
    )
    monkeypatch.setattr(
        "app.services.consultation_problem_plan_sources.redact_problem_plan_titles",
        lambda _db, _owner, *, problems, **_kwargs: (
            {problem["uuid"]: f"[PHI-{index + 2}]" for index, problem in enumerate(problems)},
            [{"index": index + 2, "placeholder": f"[PHI-{index + 2}]", "type": "PERSON",
              "value": problem["title"]} for index, problem in enumerate(problems)],
        ),
    )
    monkeypatch.setattr(
        "app.services.task_outbox.try_publish_task_dispatch_safely", lambda *_args, **_kwargs: None,
    )

    first_key = uuid4()
    first = create_consultation_notes_from_problem_plan(
        db_session, owner, transcript_id=transcript.id, client_idempotency_key=first_key,
        expected_revision=saved.revision,
    )
    first_batch = db_session.get(ConsultationSplitBatch, first.batch_id)
    frozen_plan = read_split_batch_json(db_session, owner, batch=first_batch, field="confirmed_plan_encrypted")
    frozen_source = read_split_batch_json(db_session, owner, batch=first_batch, field="source_snapshot_encrypted")
    frozen_templates = read_split_batch_json(db_session, owner, batch=first_batch, field="template_snapshot_encrypted")
    frozen_provider = read_split_batch_json(db_session, owner, batch=first_batch, field="provider_snapshot_encrypted")
    from app.services.consultation_split_generation import prepare_split_generation_request
    prepared_request = prepare_split_generation_request(
        source_snapshot=frozen_source,
        clinical_snapshot=read_split_batch_json(db_session, owner, batch=first_batch,
                                                field="clinical_snapshot_encrypted"),
        confirmed_plan=frozen_plan,
        note_options_snapshot=read_split_batch_json(db_session, owner, batch=first_batch,
                                                    field="note_options_snapshot_encrypted"),
    )
    assert frozen_plan["version"] == 2
    assert frozen_plan["source_lineage"] == {"problem_plan_id": str(saved.plan_id), "revision": saved.revision}
    assert frozen_plan["notes"][0]["is_primary"] is True
    secondary_note = next(note for note in frozen_plan["notes"] if not note["is_primary"])
    assert secondary_note["problem_scope"] == ["[PHI-2]"]
    assert frozen_plan["ignored_problems"] == ["[PHI-4]"]
    assert "Synthetic back pain" not in str(frozen_plan)
    assert "Synthetic blood pressure" not in str(frozen_plan)
    assert frozen_source["sources"]["transcript"] == "[PHI-1] synthetic source"
    assert frozen_source["phi_index"][-1]["value"] == "Synthetic excluded issue"
    provider_request_json = json.dumps(prepared_request.request_body, sort_keys=True)
    assert "[PHI-2]" in provider_request_json
    assert "Synthetic back pain" not in provider_request_json
    assert "Synthetic excluded issue" not in provider_request_json
    assert "phi_index" not in provider_request_json
    old_secondary_snapshot = frozen_templates["topic_templates"][str(secondary_template.id)]
    assert old_secondary_snapshot["prompt_text"] == "Original secondary prompt"
    assert frozen_provider["llm_config_id"] == str(first_config.id)

    # A retry with the submitted key returns the original frozen batch.
    replay = create_consultation_notes_from_problem_plan(
        db_session, owner, transcript_id=transcript.id, client_idempotency_key=first_key,
        expected_revision=saved.revision,
    )
    assert replay.batch_id == first.batch_id and replay.idempotency_replayed is True

    # Edit the live plan, template version, and provider selection. Complete the
    # first queued execution metadata so a fresh Create can start independently.
    secondary_version = db_session.scalar(select(PromptTemplateVersion).where(
        PromptTemplateVersion.template_id == secondary_template.id,
    ))
    db_session.add(PromptTemplateVersion(
        template_id=secondary_template.id, version_no=secondary_version.version_no + 1,
        mode=secondary_version.mode, prompt_text="Updated secondary prompt",
        config_json=secondary_version.config_json, created_by_user_id=owner.id,
    ))
    new_config = make_llm_config(
        team=owner.team, actor=owner, label="Fresh living-plan provider",
        model_name="gpt-4.1-mini", available_models_json=["gpt-4.1-mini"],
    )
    selection.llm_config_id = new_config.id
    selection.allowed_models_json = ["gpt-4.1-mini"]
    # While the original batch remains queued, local edits create revision 2.
    # The submitted batch must remain bound to its original grouping and source.
    state.source_fingerprint = "d" * 64
    prepared.source_snapshot = {
        **source_snapshot,
        "sources": {**source_snapshot["sources"], "transcript": "[PHI-9] updated source"},
    }
    current = replace_consultation_problem_plan(
        db_session, owner, transcript_id=transcript.id,
        payload=_payload(
            revision=saved.revision,
            tiles=[
                ConsultationProblemPlanTile(uuid=primary_id, is_primary=True, template_id=primary_template.id),
                ConsultationProblemPlanTile(uuid=secondary_id, is_primary=False, template_id=secondary_template.id),
            ],
            problems=[
                ConsultationProblemPlanProblem(uuid=back_id, title="Updated back pain", origin="manual",
                                               ignored=False, group_id=primary_id),
                ConsultationProblemPlanProblem(uuid=bp_id, title="Updated blood pressure", origin="manual",
                                               ignored=False, group_id=secondary_id),
                ConsultationProblemPlanProblem(uuid=ignored_id, title="Synthetic excluded issue", origin="manual",
                                               ignored=True, restore_group_id=primary_id),
            ],
        ),
    )
    # Recovery is rebuilt from the submitted v2 snapshots after live plan/source
    # edits. It requests only the failed note and retains the original sibling
    # boundaries, exclusions, shared source, and selected template.
    from app.services.consultation_split_generation import prepare_split_generation_recovery_request
    original_payload = json.loads(prepared_request.request_body["messages"][1]["content"])
    failed_note = next(note for note in original_payload["notes"] if not note["is_primary"])
    primary_note = next(note for note in original_payload["notes"] if note["is_primary"])
    recovery = prepare_split_generation_recovery_request(
        source_snapshot=frozen_source,
        clinical_snapshot=read_split_batch_json(db_session, owner, batch=first_batch,
                                                field="clinical_snapshot_encrypted"),
        confirmed_plan=frozen_plan,
        note_options_snapshot=read_split_batch_json(db_session, owner, batch=first_batch,
                                                    field="note_options_snapshot_encrypted"),
        failed_topic_uuids=[UUID(failed_note["topic_uuid"])],
        accepted_sibling_outputs={
            UUID(primary_note["topic_uuid"]): {
                "mode": "freeform", "content": "Synthetic accepted primary output",
            },
        },
    )
    recovery_payload = json.loads(recovery.request_body["messages"][1]["content"])
    assert recovery_payload["notes"] == [failed_note]
    assert recovery_payload["notes"][0]["problem_scope"] == ["[PHI-2]"]
    assert recovery_payload["notes"][0]["template"] == failed_note["template"]
    assert recovery_payload["ignored_problems"] == ["[PHI-4]"]
    assert recovery_payload["sources"] == original_payload["sources"]
    assert recovery_payload["sibling_boundaries"] == original_payload["sibling_boundaries"]
    assert "Synthetic back pain" not in json.dumps(recovery_payload)
    assert "Synthetic excluded issue" not in json.dumps(recovery_payload)

    first_execution = db_session.scalar(select(ConsultationSplitExecution).where(
        ConsultationSplitExecution.batch_id == first_batch.id,
    ))
    assert first_execution is not None
    assert first_execution.status is ConsultationSplitExecutionStatus.queued
    assert read_split_batch_json(db_session, owner, batch=first_batch,
                                 field="source_snapshot_encrypted")["sources"]["transcript"] == "[PHI-1] synthetic source"
    first_execution.status = ConsultationSplitExecutionStatus.failed
    db_session.commit()
    second = create_consultation_notes_from_problem_plan(
        db_session, owner, transcript_id=transcript.id, client_idempotency_key=uuid4(),
        expected_revision=current.revision,
    )
    second_batch = db_session.get(ConsultationSplitBatch, second.batch_id)
    second_plan = read_split_batch_json(db_session, owner, batch=second_batch, field="confirmed_plan_encrypted")
    second_templates = read_split_batch_json(db_session, owner, batch=second_batch, field="template_snapshot_encrypted")
    second_provider = read_split_batch_json(db_session, owner, batch=second_batch, field="provider_snapshot_encrypted")
    second_source = read_split_batch_json(db_session, owner, batch=second_batch, field="source_snapshot_encrypted")
    assert second.batch_id != first.batch_id
    assert second_plan["source_lineage"]["revision"] == current.revision
    assert second_plan["notes"][0]["problem_scope"] == ["[PHI-2]"]
    assert next(note for note in second_plan["notes"] if not note["is_primary"])["problem_scope"] == ["[PHI-3]"]
    assert second_templates["topic_templates"][str(secondary_template.id)]["prompt_text"] == "Updated secondary prompt"
    assert second_provider["llm_config_id"] == str(new_config.id)
    assert second_source["sources"]["transcript"] == "[PHI-9] updated source"
    assert read_split_batch_json(db_session, owner, batch=first_batch, field="confirmed_plan_encrypted") == frozen_plan
    assert read_split_batch_json(db_session, owner, batch=first_batch, field="template_snapshot_encrypted") == frozen_templates
    assert read_split_batch_json(db_session, owner, batch=first_batch, field="provider_snapshot_encrypted") == frozen_provider


def test_primary_only_create_freezes_current_source_without_analysis_or_provider_call(
    db_session, make_user, make_template, make_llm_config, make_llm_selection, monkeypatch,
):
    owner = make_user(email="living-plan-primary-only@example.com")
    _enable(db_session, owner, monkeypatch)
    template = make_template(owner=owner, name="Primary only template")
    config = make_llm_config(team=owner.team, actor=owner, available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=owner, allowed_models_json=["gpt-4o-mini"])
    transcript = _transcript(db_session, owner)
    plan = replace_consultation_problem_plan(
        db_session, owner, transcript_id=transcript.id,
        payload=_payload(
            revision=0,
            tiles=[ConsultationProblemPlanTile(uuid=uuid4(), is_primary=True, template_id=template.id)],
            problems=[],
        ),
    )
    source_state = SimpleNamespace(
        transcript_version_id=None, redaction_run_id=None, source_fingerprint="c" * 64,
    )
    source_snapshot = {
        "source_state": {"owner_user_id": str(owner.id), "team_id": str(owner.team_id),
                         "transcript_id": str(transcript.id),
                         "transcript": {"transcript_version_id": None,
                                        "redaction_run_id": None, "present": False}},
        "sources": {"transcript": "[PHI-1] current source",
                    "working_note": {"mode": None, "value": None}, "dictation": ""},
        "clinical_nlp_hints": [], "phi_index": [],
    }
    prepared = SimpleNamespace(
        source_state=source_state, source_snapshot=source_snapshot,
        retention_expires_at=transcript.retention_expires_at,
    )
    monkeypatch.setattr(
        "app.services.consultation_split_sources.prepare_source_bound_consultation_split_analysis",
        lambda *_args, **_kwargs: prepared,
    )
    monkeypatch.setattr(
        "app.services.consultation_split_sources.resolve_ready_consultation_split_analysis_source_state",
        lambda *_args, **_kwargs: SimpleNamespace(source_fingerprint=source_state.source_fingerprint),
    )
    monkeypatch.setattr(
        "app.services.consultation_problem_plan_sources.redact_problem_plan_titles",
        lambda *_args, **_kwargs: ({}, []),
    )
    monkeypatch.setattr(
        "app.services.task_outbox.try_publish_task_dispatch_safely", lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.consultation_split_generation_runtime.llm_runtime.invoke_llm",
        lambda **_kwargs: pytest.fail("Create must queue generation without calling the provider"),
    )

    result = create_consultation_notes_from_problem_plan(
        db_session, owner, transcript_id=transcript.id, client_idempotency_key=uuid4(),
        expected_revision=plan.revision,
    )
    batch = db_session.get(ConsultationSplitBatch, result.batch_id)
    frozen_plan = read_split_batch_json(db_session, owner, batch=batch, field="confirmed_plan_encrypted")
    frozen_source = read_split_batch_json(db_session, owner, batch=batch, field="source_snapshot_encrypted")
    assert result.topic_count == 1 and result.separate_note_count == 1
    assert len(frozen_plan["notes"]) == 1
    assert frozen_plan["notes"][0]["is_primary"] is True
    assert frozen_plan["notes"][0]["problem_scope"] == []
    assert frozen_source["sources"]["transcript"] == "[PHI-1] current source"
    assert db_session.scalar(select(ConsultationSplitBatch).where(
        ConsultationSplitBatch.transcript_id == transcript.id,
    )) is not None
