"""Concurrency regressions for consultation-split source writer locking."""

from datetime import timedelta
from threading import Event, Thread
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.errors import AppError
from app.models import (
    GeneratedDocument,
    ProviderAttempt,
    RedactionRun,
    RedactionRunStatus,
    TaskDispatchOutbox,
    Transcript,
    TranscriptIngestionMode,
    TranscriptStatus,
    TranscriptWorkingNoteMode,
    User,
    UserAppPreference,
    utcnow,
)
from app.schemas.preferences import UserAppPreferencesUpsert
from app.schemas.transcripts import WorkingNoteUpdate
from app.services.consultation_split_locks import lock_consultation_split_source_scope
from app.services.consultation_split_sources import prepare_source_bound_consultation_split_analysis
from app.services.content_crypto import decrypt_text_for_owner, encrypt_text_for_owner
from app.services.dictations import update_post_consultation_dictation
from app.services.preferences import set_user_app_preferences
from app.services import templates as template_service
from app.services.templates import queue_document_generation_from_template
from app.services.transcripts import commit_transcript_text, create_manual_pii_entity, save_working_note


def _source_transcript(db, owner) -> Transcript:
    transcript = Transcript(
        owner_user_id=owner.id,
        team_id=owner.team_id,
        title="Synthetic lock test",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=utcnow() + timedelta(days=30),
    )
    db.add(transcript)
    db.commit()
    db.refresh(transcript)
    return transcript


@pytest.mark.real_db_connections
@pytest.mark.parametrize("writer", ["working_note", "dictation_insert", "manual_pii", "transcript_commit"])
def test_source_writers_wait_for_the_runtime_owner_root_lock(
    db_session,
    make_user,
    monkeypatch,
    writer,
):
    """A source write cannot commit between the final proof and submission."""
    owner = make_user(email=f"split-lock-{writer}-{uuid4()}@example.com")
    transcript = _source_transcript(db_session, owner)
    owner_id, transcript_id = owner.id, transcript.id
    session_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, future=True)
    lock_acquired, writer_entered, release, writer_done = Event(), Event(), Event(), Event()
    errors: list[BaseException] = []

    if writer == "dictation_insert":
        patch_target = "app.services.dictations.lock_consultation_split_source_scope"
    else:
        patch_target = "app.services.transcripts.lock_consultation_split_source_scope"
    original_lock = __import__(patch_target.rsplit(".", 1)[0], fromlist=["lock_consultation_split_source_scope"]).lock_consultation_split_source_scope

    def announce_writer_lock(*args, **kwargs):
        writer_entered.set()
        return original_lock(*args, **kwargs)

    monkeypatch.setattr(patch_target, announce_writer_lock)

    def hold_runtime_lock() -> None:
        with session_factory() as session:
            scope = lock_consultation_split_source_scope(
                session,
                owner_user_id=owner_id,
                transcript_id=transcript_id,
            )
            assert scope is not None
            lock_acquired.set()
            assert release.wait(5)
            session.rollback()

    def run_writer() -> None:
        assert lock_acquired.wait(5)
        with session_factory() as session:
            try:
                actor = session.get(User, owner_id)
                assert actor is not None
                if writer == "working_note":
                    save_working_note(
                        session,
                        actor,
                        transcript_id=transcript_id,
                        payload=WorkingNoteUpdate(
                            mode=TranscriptWorkingNoteMode.freeform,
                            freeform_text="Synthetic changed working note",
                        ),
                    )
                elif writer == "dictation_insert":
                    update_post_consultation_dictation(
                        session,
                        actor,
                        transcript_id=transcript_id,
                        combined_text="Synthetic changed dictation",
                    )
                elif writer == "manual_pii":
                    create_manual_pii_entity(
                        session,
                        actor,
                        transcript_id=transcript_id,
                        entity_type="PERSON",
                        value="Synthetic Person",
                    )
                else:
                    commit_transcript_text(
                        session,
                        actor,
                        transcript_id=transcript_id,
                        plaintext="Synthetic changed transcript",
                    )
            except BaseException as exc:  # surfaced after both threads release
                errors.append(exc)
            finally:
                writer_done.set()

    holder = Thread(target=hold_runtime_lock)
    competing_writer = Thread(target=run_writer)
    holder.start()
    assert lock_acquired.wait(5)
    competing_writer.start()
    assert writer_entered.wait(5)
    assert not writer_done.wait(0.2)
    release.set()
    holder.join(5)
    competing_writer.join(5)
    assert not holder.is_alive() and not competing_writer.is_alive()
    assert errors == []


@pytest.mark.real_db_connections
@pytest.mark.parametrize("writer", ["working_note", "dictation", "manual_pii"])
def test_ordinary_generation_and_split_source_writers_share_lock_order_and_snapshot(
    db_session,
    make_user,
    make_template,
    make_llm_config,
    make_llm_selection,
    monkeypatch,
    writer,
):
    """Generation snapshots all mutable sources before a split writer can commit."""
    owner = make_user(email=f"ordinary-generation-lock-{writer}-{uuid4()}@example.com")
    transcript = _source_transcript(db_session, owner)
    commit_transcript_text(db_session, owner, transcript_id=transcript.id, plaintext="Synthetic source before writer")
    save_working_note(
        db_session,
        owner,
        transcript_id=transcript.id,
        payload=WorkingNoteUpdate(mode=TranscriptWorkingNoteMode.freeform, freeform_text="Synthetic working note before writer"),
    )
    update_post_consultation_dictation(
        db_session, owner, transcript_id=transcript.id, combined_text="Synthetic dictation before writer"
    )
    db_session.refresh(transcript)
    working_note_updated_at = transcript.working_note_updated_at
    config = make_llm_config(team=owner.team, actor=owner, available_models_json=["gpt-4o-mini"])
    make_llm_selection(config=config, actor=owner, allowed_models_json=["gpt-4o-mini"])
    template = make_template(owner=owner, actor=owner)
    owner_id, transcript_id, template_id = owner.id, transcript.id, template.id
    session_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, future=True)
    snapshot_ready, writer_entered, release_snapshot = Event(), Event(), Event()
    generation_errors: list[BaseException] = []
    writer_errors: list[BaseException] = []
    generated_ids: list[UUID] = []

    original_snapshot = template_service._snapshot_transcript_version

    def hold_after_snapshot(*args, **kwargs):
        result = original_snapshot(*args, **kwargs)
        snapshot_ready.set()
        assert release_snapshot.wait(5)
        return result

    monkeypatch.setattr(template_service, "_snapshot_transcript_version", hold_after_snapshot)
    monkeypatch.setattr(template_service, "try_publish_task_dispatch_safely", lambda *_args: None)
    patch_target = (
        "app.services.dictations.lock_consultation_split_source_scope"
        if writer == "dictation"
        else "app.services.transcripts.lock_consultation_split_source_scope"
    )
    module = __import__(patch_target.rsplit(".", 1)[0], fromlist=["lock_consultation_split_source_scope"])
    original_writer_lock = module.lock_consultation_split_source_scope

    def announce_writer_lock(*args, **kwargs):
        writer_entered.set()
        return original_writer_lock(*args, **kwargs)

    monkeypatch.setattr(patch_target, announce_writer_lock)

    def queue_generation() -> None:
        with session_factory() as session:
            try:
                actor = session.get(User, owner_id)
                assert actor is not None
                generated_ids.append(
                    queue_document_generation_from_template(
                        session, actor, transcript_id=transcript_id, template_id=template_id
                    ).id
                )
            except BaseException as exc:
                generation_errors.append(exc)

    def write_source() -> None:
        assert snapshot_ready.wait(5)
        with session_factory() as session:
            try:
                actor = session.get(User, owner_id)
                assert actor is not None
                if writer == "working_note":
                        save_working_note(
                            session,
                            actor,
                            transcript_id=transcript_id,
                            payload=WorkingNoteUpdate(
                                mode=TranscriptWorkingNoteMode.freeform,
                                expected_updated_at=working_note_updated_at,
                                freeform_text="Synthetic working note after writer",
                            ),
                    )
                elif writer == "dictation":
                    update_post_consultation_dictation(
                        session, actor, transcript_id=transcript_id, combined_text="Synthetic dictation after writer"
                    )
                else:
                    create_manual_pii_entity(
                        session, actor, transcript_id=transcript_id, entity_type="PERSON", value="Synthetic manual PII after writer"
                    )
            except BaseException as exc:
                writer_errors.append(exc)

    generation = Thread(target=queue_generation)
    competing_writer = Thread(target=write_source)
    generation.start()
    assert snapshot_ready.wait(5)
    competing_writer.start()
    assert writer_entered.wait(5)
    assert competing_writer.is_alive(), "writer must wait for generation's canonical source lock"
    release_snapshot.set()
    generation.join(5)
    competing_writer.join(5)
    assert not generation.is_alive() and not competing_writer.is_alive()
    assert generation_errors == []
    assert writer_errors == []
    assert len(generated_ids) == 1

    db_session.expire_all()
    document = db_session.get(template_service.GeneratedDocument, generated_ids[0])
    assert document is not None
    assert len(
        db_session.scalars(
            select(GeneratedDocument).where(GeneratedDocument.transcript_id == transcript_id)
        ).all()
    ) == 1
    assert len(
        db_session.scalars(
            select(ProviderAttempt).where(ProviderAttempt.generated_document_id == document.id)
        ).all()
    ) == 1
    assert len(
        db_session.scalars(
            select(TaskDispatchOutbox).where(TaskDispatchOutbox.source_id == document.id)
        ).all()
    ) == 1
    version = db_session.get(template_service.TranscriptVersion, document.transcript_version_id)
    assert version is not None
    assert decrypt_text_for_owner(
        db_session, owner_user_id=owner_id, table="transcript_versions", field="text_encrypted", record_id=version.id,
        stored_value=version.text_encrypted,
    ) == "Synthetic source before writer"
    assert decrypt_text_for_owner(
        db_session, owner_user_id=owner_id, table="generated_documents", field="freeform_working_note_snapshot_encrypted", record_id=document.id,
        stored_value=document.freeform_working_note_snapshot_encrypted,
    ) == "Synthetic working note before writer"
    assert decrypt_text_for_owner(
        db_session, owner_user_id=owner_id, table="generated_documents", field="dictation_snapshot_encrypted", record_id=document.id,
        stored_value=document.dictation_snapshot_encrypted,
    ) == "Synthetic dictation before writer"


@pytest.mark.real_db_connections
def test_preference_setter_waits_for_the_runtime_owner_lock(db_session, make_user, monkeypatch):
    """The optional preference row cannot bypass the final runtime gate."""
    owner = make_user(email=f"split-preference-lock-{uuid4()}@example.com")
    db_session.add(
        UserAppPreference(
            user_id=owner.id,
            preferences_json={"split_consultations_into_separate_notes": True},
        )
    )
    db_session.commit()
    owner_id: UUID = owner.id
    session_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, future=True)
    lock_acquired, setter_entered, release, setter_done = Event(), Event(), Event(), Event()
    errors: list[BaseException] = []

    from app.services import preferences as preference_service

    original_lock = preference_service._lock_user_app_preference_owner

    def announce_setter_lock(*args, **kwargs):
        setter_entered.set()
        return original_lock(*args, **kwargs)

    monkeypatch.setattr(preference_service, "_lock_user_app_preference_owner", announce_setter_lock)

    def hold_runtime_gate() -> None:
        with session_factory() as session:
            assert session.scalar(select(User).where(User.id == owner_id).with_for_update()) is not None
            lock_acquired.set()
            assert release.wait(5)
            session.rollback()

    def turn_splitting_off() -> None:
        assert lock_acquired.wait(5)
        with session_factory() as session:
            try:
                actor = session.get(User, owner_id)
                assert actor is not None
                set_user_app_preferences(
                    session,
                    actor,
                    UserAppPreferencesUpsert(split_consultations_into_separate_notes=False),
                )
            except BaseException as exc:
                errors.append(exc)
            finally:
                setter_done.set()

    holder = Thread(target=hold_runtime_gate)
    setter = Thread(target=turn_splitting_off)
    holder.start()
    assert lock_acquired.wait(5)
    setter.start()
    assert setter_entered.wait(5)
    assert not setter_done.wait(0.2)
    release.set()
    holder.join(5)
    setter.join(5)
    assert not holder.is_alive() and not setter.is_alive()
    assert errors == []
    db_session.expire_all()
    preference = db_session.scalar(select(UserAppPreference).where(UserAppPreference.user_id == owner_id))
    assert preference is not None and preference.preferences_json == {}


@pytest.mark.real_db_connections
def test_source_writer_is_not_blocked_by_dynamic_redaction_and_stales_preparation(
    db_session,
    make_user,
    monkeypatch,
):
    """Provider-bound redaction releases source locks and re-proves the source."""
    owner = make_user(email=f"split-provider-barrier-{uuid4()}@example.com")
    transcript = _source_transcript(db_session, owner)
    save_working_note(
        db_session,
        owner,
        transcript_id=transcript.id,
        payload=WorkingNoteUpdate(
            mode=TranscriptWorkingNoteMode.freeform,
            freeform_text="Synthetic initial working note",
        ),
    )
    db_session.commit()
    owner_id, transcript_id = owner.id, transcript.id
    expected_updated_at = db_session.get(Transcript, transcript_id).working_note_updated_at
    session_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, future=True)
    provider_entered, release_provider, writer_done, preparation_done = Event(), Event(), Event(), Event()
    writer_errors: list[BaseException] = []
    preparation_errors: list[BaseException] = []
    provider_calls: list[str] = []

    def blocking_redaction(_db, text, *, team_id, start_index):
        provider_calls.append(text)
        provider_entered.set()
        assert release_provider.wait(5)
        return {
            "redacted_text": text,
            "phi_mapping": {},
            "phi_index": [],
            "phi_count": 0,
            "api_provider": "synthetic",
            "api_model_or_version": None,
        }

    monkeypatch.setattr(
        "app.services.consultation_split_sources.redact_transient_text",
        blocking_redaction,
    )

    def prepare() -> None:
        with session_factory() as session:
            try:
                actor = session.get(User, owner_id)
                assert actor is not None
                prepare_source_bound_consultation_split_analysis(session, actor, transcript_id=transcript_id)
            except BaseException as exc:
                preparation_errors.append(exc)
            finally:
                preparation_done.set()

    def write_source() -> None:
        assert provider_entered.wait(5)
        with session_factory() as session:
            try:
                actor = session.get(User, owner_id)
                assert actor is not None
                save_working_note(
                    session,
                    actor,
                    transcript_id=transcript_id,
                        payload=WorkingNoteUpdate(
                            mode=TranscriptWorkingNoteMode.freeform,
                            freeform_text="Synthetic changed while provider waited",
                            expected_updated_at=expected_updated_at,
                        ),
                )
            except BaseException as exc:
                writer_errors.append(exc)
            finally:
                writer_done.set()

    preparing = Thread(target=prepare)
    writer = Thread(target=write_source)
    preparing.start()
    assert provider_entered.wait(5)
    writer.start()
    assert writer_done.wait(5), "a source writer must not wait for external redaction"
    assert writer_errors == []
    release_provider.set()
    preparing.join(5)
    writer.join(5)
    assert not preparing.is_alive() and not writer.is_alive()
    assert preparation_done.is_set()
    assert len(preparation_errors) == 1
    assert isinstance(preparation_errors[0], AppError)
    assert preparation_errors[0].code == "consultation_split_source_stale"
    # Final locked reproof reads identity only; it must not redaction-call again.
    assert provider_calls == ["Synthetic initial working note"]
    assert db_session.query(Transcript).filter_by(id=transcript_id).one() is not None


@pytest.mark.real_db_connections
def test_source_writer_is_not_blocked_by_required_redaction_and_stales_preparation(
    db_session,
    make_user,
    monkeypatch,
):
    """Required remote redaction starts only after snapshot locks are released."""
    owner = make_user(email=f"split-required-provider-barrier-{uuid4()}@example.com")
    transcript = _source_transcript(db_session, owner)
    transcript.current_draft_text_encrypted = encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table="transcripts",
        field="current_draft_text_encrypted",
        record_id=transcript.id,
        plaintext="Synthetic initial transcript",
    )
    db_session.add(transcript)
    db_session.commit()
    owner_id, transcript_id = owner.id, transcript.id
    session_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, future=True)
    provider_entered, release_provider, writer_done, preparation_done = Event(), Event(), Event(), Event()
    writer_errors: list[BaseException] = []
    preparation_errors: list[BaseException] = []
    provider_calls: list[object] = []

    def blocking_required_redaction(session, *, transcript_version):
        provider_calls.append(transcript_version.id)
        run_id = uuid4()
        run = RedactionRun(
            id=run_id,
            transcript_id=transcript_version.transcript_id,
            transcript_version_id=transcript_version.id,
            owner_user_id=transcript_version.transcript.owner_user_id,
            team_id=transcript_version.transcript.team_id,
            status=RedactionRunStatus.succeeded,
            redacted_text_encrypted=encrypt_text_for_owner(
                session,
                owner_user_id=transcript_version.transcript.owner_user_id,
                table="redaction_runs",
                field="redacted_text_encrypted",
                record_id=run_id,
                plaintext="Synthetic redacted transcript",
            ),
            mapping_hash="synthetic",
            api_provider="synthetic",
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        provider_entered.set()
        assert release_provider.wait(5)
        return run

    monkeypatch.setattr(
        "app.services.consultation_split_sources.ensure_redaction_run_for_transcript_version",
        blocking_required_redaction,
    )

    def prepare() -> None:
        with session_factory() as session:
            try:
                actor = session.get(User, owner_id)
                assert actor is not None
                prepare_source_bound_consultation_split_analysis(session, actor, transcript_id=transcript_id)
            except BaseException as exc:
                preparation_errors.append(exc)
            finally:
                preparation_done.set()

    def write_source() -> None:
        assert provider_entered.wait(5)
        with session_factory() as session:
            try:
                actor = session.get(User, owner_id)
                assert actor is not None
                commit_transcript_text(
                    session,
                    actor,
                    transcript_id=transcript_id,
                    plaintext="Synthetic changed while required provider waited",
                )
            except BaseException as exc:
                writer_errors.append(exc)
            finally:
                writer_done.set()

    preparing = Thread(target=prepare)
    writer = Thread(target=write_source)
    preparing.start()
    assert provider_entered.wait(5)
    writer.start()
    assert writer_done.wait(5), "a source writer must not wait for required remote redaction"
    assert writer_errors == []
    release_provider.set()
    preparing.join(5)
    writer.join(5)
    assert not preparing.is_alive() and not writer.is_alive()
    assert preparation_done.is_set()
    assert len(preparation_errors) == 1
    assert isinstance(preparation_errors[0], AppError)
    assert preparation_errors[0].code == "consultation_split_source_stale"
    assert len(provider_calls) == 1
