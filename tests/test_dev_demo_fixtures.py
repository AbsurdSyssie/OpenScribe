import json

from sqlalchemy import func, select

from app.models import (
    ConsultationSplitAnalysis,
    ConsultationSplitBatch,
    ConsultationSplitExecution,
    GeneratedDocument,
    PostConsultationDictation,
    ProviderAttempt,
    ProviderUsageEvent,
    RedactionRun,
    Transcript,
    TranscriptVersion,
    TranscriptWorkingNoteMode,
    SessionAuthLevel,
    SessionStatus,
    UserSession,
    UserAppPreference,
    utcnow,
)
from app.errors import AppError
from app.services.consultation_split_generation import prepare_split_generation_request
from app.services.consultation_split_sources import prepare_source_bound_consultation_split_analysis
from app.services.consultation_split_api import read_workspace_split_analysis, read_workspace_split_batch
from app.services.content_crypto import decrypt_json_for_owner
from app.services.dev_demo_fixtures import DEMO_CONSULTATIONS, demo_transcript_id, seed_dev_demo_consultations
from app.services.dictations import dictation_combined_text
from app.services.transcripts import transcript_draft_text, transcript_structured_context
from app.schemas.transcripts import TranscriptStart
from app.services.transcripts import commit_transcript_text, start_transcript
from app.main import _local_only_dev_emails
from scripts.seed_dev_accounts import ensure_dev_demo_user


def test_demo_seed_creates_full_owner_encrypted_examples_and_preserves_reruns(db_session, make_user, monkeypatch):
    monkeypatch.setenv("CONSULTATION_SPLITTING_ENABLED", "true")
    owner = make_user(email="demo-fixture@example.com")
    db_session.add(UserAppPreference(user_id=owner.id, preferences_json={"split_consultations_into_separate_notes": True}))
    db_session.commit()
    seeded = seed_dev_demo_consultations(db_session, owner=owner)
    assert len(seeded) == 5
    assert len({item.id for item in seeded}) == 5

    docs = list(db_session.scalars(select(GeneratedDocument).where(GeneratedDocument.owner_user_id == owner.id)))
    assert len(docs) == 12
    assert sum(document.consultation_split_batch_topic_id is not None for document in docs) == 4
    assert all(document.redaction_run_id is not None for document in docs)
    assert db_session.scalar(select(ProviderAttempt.id).where(ProviderAttempt.owner_user_id == owner.id)) is None
    assert db_session.scalar(select(ProviderUsageEvent.id).where(ProviderUsageEvent.owner_user_id == owner.id)) is None
    assert db_session.scalar(select(ConsultationSplitExecution.id).where(ConsultationSplitExecution.owner_user_id == owner.id)) is None

    for case in DEMO_CONSULTATIONS:
        transcript = db_session.get(Transcript, demo_transcript_id(owner, case["key"]))
        assert transcript is not None
        assert transcript_draft_text(db_session, transcript=transcript) == case["transcript"]
        assert transcript_structured_context(db_session, transcript=transcript) == case["working_note"]
        assert transcript.working_note_mode is TranscriptWorkingNoteMode.structured
        version = db_session.scalar(select(TranscriptVersion).where(TranscriptVersion.transcript_id == transcript.id))
        assert version is not None
        dictation = db_session.scalar(select(PostConsultationDictation).where(PostConsultationDictation.transcript_id == transcript.id))
        assert dictation is not None
        assert dictation_combined_text(db_session, dictation=dictation) == case["dictation"]
        assert len([document for document in docs if document.transcript_id == transcript.id]) == (3 if case.get("split_topics") else 2)

    for case_key in ("neck-pain-hand-eczema-split", "asthma-rhinitis-split"):
        transcript = db_session.get(Transcript, demo_transcript_id(owner, case_key))
        analysis = db_session.scalar(select(ConsultationSplitAnalysis).where(ConsultationSplitAnalysis.transcript_id == transcript.id))
        batch = db_session.scalar(select(ConsultationSplitBatch).where(ConsultationSplitBatch.transcript_id == transcript.id))
        run = db_session.scalar(select(RedactionRun).where(RedactionRun.transcript_id == transcript.id))
        assert analysis is not None and batch is not None and run is not None
        analysis_view = read_workspace_split_analysis(db_session, owner, transcript_id=transcript.id)
        batch_view = read_workspace_split_batch(db_session, owner, transcript_id=transcript.id)
        assert analysis_view is not None and analysis_view.status == "ready"
        assert batch_view is not None and batch_view.status == "ready"
        version = db_session.get(TranscriptVersion, batch.materialization_transcript_version_id)
        prepared = prepare_source_bound_consultation_split_analysis(
            db_session, owner, transcript_id=transcript.id,
            transcript_version_id=version.id, redaction_run_id=run.id,
        )
        assert prepared.source_state.source_fingerprint == analysis.source_fingerprint
        confirmed_plan = decrypt_json_for_owner(
            db_session, owner_user_id=owner.id, table="consultation_split_batches",
            field="confirmed_plan_encrypted", record_id=batch.id,
            stored_value=batch.confirmed_plan_encrypted,
        )
        clinical_snapshot = decrypt_json_for_owner(
            db_session, owner_user_id=owner.id, table="consultation_split_batches",
            field="clinical_snapshot_encrypted", record_id=batch.id,
            stored_value=batch.clinical_snapshot_encrypted,
        )
        sources = decrypt_json_for_owner(
            db_session, owner_user_id=owner.id, table="consultation_split_batches",
            field="source_snapshot_encrypted", record_id=batch.id,
            stored_value=batch.source_snapshot_encrypted,
        )
        note_options = decrypt_json_for_owner(
            db_session, owner_user_id=owner.id, table="consultation_split_batches",
            field="note_options_snapshot_encrypted", record_id=batch.id,
            stored_value=batch.note_options_snapshot_encrypted,
        )
        prepared_request = prepare_split_generation_request(
            source_snapshot=sources, clinical_snapshot=clinical_snapshot,
            confirmed_plan=confirmed_plan, note_options_snapshot=note_options,
        )
        assert prepared_request.request_body
        assert "synthetic_dev_demo" in json.dumps(
            decrypt_json_for_owner(db_session, owner_user_id=owner.id,
                table="consultation_split_analyses", field="provider_snapshot_encrypted",
                record_id=analysis.id, stored_value=analysis.provider_snapshot_encrypted)
        )

    original_titles = {item.id: item.title for item in seeded}
    again = seed_dev_demo_consultations(db_session, owner=owner)
    assert {item.id: item.title for item in again} == original_titles
    assert db_session.scalar(select(func.count(Transcript.id)).where(Transcript.owner_user_id == owner.id)) == 5
    unrelated = start_transcript(db_session, owner, TranscriptStart(title="My unrelated note"))
    commit_transcript_text(db_session, owner, transcript_id=unrelated.id, plaintext="Unrelated saved note.")
    reset = seed_dev_demo_consultations(db_session, owner=owner, reset=True)
    assert len(reset) == 5
    assert db_session.get(Transcript, unrelated.id) is not None
    assert db_session.scalar(select(func.count(Transcript.id)).where(Transcript.owner_user_id == owner.id)) == 6


def test_demo_seed_rerun_repairs_root_committed_before_child_documents(db_session, make_user, monkeypatch):
    owner = make_user(email="demo-repair@example.com")
    from app.services import redaction

    real_ensure_redaction = redaction.ensure_redaction_run_for_transcript_version
    interrupted = False

    def fail_after_redaction_commit(db, *, transcript_version):
        nonlocal interrupted
        result = real_ensure_redaction(db, transcript_version=transcript_version)
        if not interrupted:
            interrupted = True
            raise RuntimeError("simulated seed interruption after redaction commit")
        return result

    monkeypatch.setattr(redaction, "ensure_redaction_run_for_transcript_version", fail_after_redaction_commit)
    import pytest
    with pytest.raises(RuntimeError, match="simulated seed interruption"):
        seed_dev_demo_consultations(db_session, owner=owner)

    first_case = DEMO_CONSULTATIONS[0]
    root = db_session.get(Transcript, demo_transcript_id(owner, first_case["key"]))
    assert root is not None
    assert db_session.scalar(select(GeneratedDocument.id).where(GeneratedDocument.transcript_id == root.id)) is None

    monkeypatch.setattr(redaction, "ensure_redaction_run_for_transcript_version", real_ensure_redaction)
    seeded = seed_dev_demo_consultations(db_session, owner=owner)
    assert len(seeded) == 5
    assert db_session.scalar(select(func.count(GeneratedDocument.id)).where(GeneratedDocument.owner_user_id == owner.id)) == 12


def test_demo_account_reconciliation_preserves_existing_authentication_state(db_session, make_user, make_team):
    team = make_team(name="Dev Test Team")
    owner = make_user(email="demo@example.com", team=team, full_name="My Demo Name", password="changed-password",
                      mfa_required=True, mfa_enabled=True, must_change_password=True)
    session = UserSession(user_id=owner.id, session_token_hash="demo-session-hash",
                          auth_level=SessionAuthLevel.full, status=SessionStatus.active,
                          expires_at=utcnow())
    db_session.add(session)
    db_session.commit()
    original = (owner.full_name, owner.password_hash, owner.mfa_required, owner.mfa_enabled, owner.must_change_password)

    same_owner = ensure_dev_demo_user(db_session, password="test1234", team=team)

    assert same_owner.id == owner.id
    assert (same_owner.full_name, same_owner.password_hash, same_owner.mfa_required,
            same_owner.mfa_enabled, same_owner.must_change_password) == original
    assert db_session.get(UserSession, session.id) is not None
    assert same_owner.team_role.value == "user"
    preference = db_session.scalar(select(UserAppPreference).where(UserAppPreference.user_id == same_owner.id))
    assert preference.preferences_json["split_consultations_into_separate_notes"] is True


def test_demo_account_reconciliation_preserves_explicit_split_preference(db_session, make_user, make_team):
    team = make_team(name="Dev Test Team")
    owner = make_user(email="demo@example.com", team=team)
    preference = UserAppPreference(user_id=owner.id, preferences_json={"split_consultations_into_separate_notes": False})
    db_session.add(preference)
    db_session.commit()

    ensure_dev_demo_user(db_session, password="test1234", team=team)

    db_session.refresh(preference)
    assert preference.preferences_json.get("split_consultations_into_separate_notes") is not True


def test_demo_account_with_content_and_missing_key_fails_closed(db_session, make_user, make_team, monkeypatch):
    from scripts import seed_dev_accounts

    team = make_team(name="Dev Test Team")
    owner = make_user(email="demo@example.com", team=team)
    start_transcript(db_session, owner, TranscriptStart(title="Existing protected content"))
    monkeypatch.setattr(seed_dev_accounts, "get_active_user_key", lambda *_args, **_kwargs: None)

    import pytest
    with pytest.raises(AppError) as exc_info:
        ensure_dev_demo_user(db_session, password="test1234", team=team)
    assert exc_info.value.code == "dev_demo_content_key_missing"


def test_demo_email_is_localhost_restricted():
    assert "demo@example.com" in _local_only_dev_emails()
