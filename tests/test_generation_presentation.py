"""Content-free failure projections within existing owner-authorized views."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models import AttemptOutcome, AttemptStatus, GeneratedDocumentStatus, HallucinationCheckStatus, TeamRole
from app.services.generation_presentation import (
    active_retry_presentation,
    generated_document_status_projection,
    generation_failure_presentation,
)


class NoContentDatabase:
    def __init__(self, attempt=None):
        self.attempt = attempt
        self.queries = []

    def scalar(self, statement):
        self.queries.append(str(statement))
        return self.attempt


def document_and_actor(role=TeamRole.user):
    owner, team = uuid4(), uuid4()
    document = SimpleNamespace(
        id=uuid4(), owner_user_id=owner, team_id=team, transcript_id=uuid4(),
        status=GeneratedDocumentStatus.failed, created_at=datetime.now(UTC), completed_at=None,
        error_code='llm_provider_timeout', provider_error_code='timeout', provider_http_status=503,
        error_message='synthetic confidential diagnostic', hallucination_check_status=HallucinationCheckStatus.not_applicable,
    )
    actor = SimpleNamespace(id=owner, team_id=team, team_role=role, is_system_admin=False)
    return document, actor


@pytest.mark.parametrize('role', [TeamRole.user, TeamRole.leader])
def test_failure_projection_is_content_free_and_technical_details_require_owning_leader(role):
    document, actor = document_and_actor(role)
    db = NoContentDatabase()
    projection = generated_document_status_projection(db, document, actor)
    failure = projection['failure_presentation']
    assert failure['category'] == 'temporary'
    assert failure['support_reference'] == document.id
    assert failure['technical_details'] is not None if role is TeamRole.leader else failure['technical_details'] is None
    assert 'synthetic confidential' not in repr(projection)
    assert projection['checker_unavailable'] is False


@pytest.mark.parametrize('boundary', ['foreign_owner', 'foreign_team', 'system_admin'])
def test_technical_projection_rejects_authority_outside_exact_owner_team(boundary):
    document, actor = document_and_actor(TeamRole.leader)
    if boundary == 'foreign_owner':
        actor.id = uuid4()
    elif boundary == 'foreign_team':
        actor.team_id = uuid4()
    else:
        actor.is_system_admin = True
    db = NoContentDatabase()
    projection = generated_document_status_projection(db, document, actor)
    assert projection['failure_presentation']['technical_details'] is None
    assert db.queries == []


@pytest.mark.parametrize('outcome', [AttemptOutcome.failed, AttemptOutcome.unknown])
def test_checker_projection_uses_failed_checker_transport_not_generation_metadata(outcome):
    document, actor = document_and_actor(TeamRole.leader)
    document.status = GeneratedDocumentStatus.ready
    document.hallucination_check_status = HallucinationCheckStatus.failed_provider
    attempt = SimpleNamespace(status=AttemptStatus.settled, outcome=outcome,
        error_code=None, provider_error_code=None, provider_http_status=None, settled_at=datetime.now(UTC),
        transport_events_json=[{'code': 'provider_error', 'http_status': 401}])
    db = NoContentDatabase(attempt)
    projection = generated_document_status_projection(db, document, actor)
    assert projection['checker_unavailable'] is True
    details = projection['checker_failure_presentation']['technical_details']
    assert details['provider_http_status'] == 401
    assert details['provider_code'] == 'authentication_error'
    assert all('provider_attempts.owner_user_id' in query and 'provider_attempts.team_id' in query
               and 'provider_attempts.transcript_id' in query for query in db.queries)


def test_retry_diagnostic_expires_when_recorded_backoff_ends():
    now = datetime.now(UTC)
    event = dict(code='rate_limited', http_status=429, retry_wait_ms=2000, occurred_at=now.isoformat())
    attempt = SimpleNamespace(status=AttemptStatus.submitted, transport_events_json=[event])
    assert active_retry_presentation(attempt, reference=uuid4()) is not None
    event['occurred_at'] = (now - timedelta(seconds=3)).isoformat()
    assert active_retry_presentation(attempt, reference=uuid4()) is None
    event.update(occurred_at=now.isoformat(), http_status=503)
    assert active_retry_presentation(attempt, reference=uuid4()) is None


def test_active_checker_retry_is_visible_when_main_attempt_remains_submitted():
    document, actor = document_and_actor(TeamRole.leader)
    document.status = GeneratedDocumentStatus.processing
    now = datetime.now(UTC)
    main = SimpleNamespace(status=AttemptStatus.submitted, transport_events_json=[dict(code='success')])
    checker = SimpleNamespace(status=AttemptStatus.submitted, transport_events_json=[dict(
        code='rate_limited', http_status=429, retry_wait_ms=2000, occurred_at=now.isoformat())])
    class StageDatabase:
        def scalar(self, statement):
            return checker if 'llm_hallucination_check' in str(statement.compile(compile_kwargs={'literal_binds': True})) else main
    result = generated_document_status_projection(StageDatabase(), document, actor)
    assert result['active_retry_presentation']['technical_details']['provider_http_status'] == 429


@pytest.mark.parametrize('error_code', ['llm_generation_truncated', 'llm_provider_bad_response'])
def test_incomplete_provider_responses_use_safe_validation_failure_presentation(error_code):
    reference = uuid4()
    presentation = generation_failure_presentation(
        reference=reference,
        occurred_at=datetime.now(UTC),
        error_code=error_code,
        provider_error_code='incomplete_response',
        include_technical_details=True,
    )

    assert presentation['category'] == 'input_or_validation'
    assert presentation['support_reference'] == reference
    assert presentation['technical_details']['application_code'] == error_code
    assert presentation['technical_details']['provider_code'] is None
    assert 'incomplete_response' not in repr(presentation)
