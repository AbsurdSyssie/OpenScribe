"""Content-free stage logging tests for generated-note workers."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models import GeneratedDocumentGeneratorType
from app.services import templates


def _log_document(*, generator=GeneratedDocumentGeneratorType.template, error_code=None):
    return SimpleNamespace(
        id=uuid4(),
        transcript_id=uuid4(),
        owner_user_id=uuid4(),
        team_id=uuid4(),
        template_version_id=None,
        generator_type=generator,
        model_used="model-private-sentinel",
        status=SimpleNamespace(value="failed" if error_code else "ready"),
        estimated_cost_usd=None,
        error_code=error_code,
        provider_error_code="provider-private-sentinel",
        provider_http_status=502 if error_code else None,
    )


def _last_record(caplog):
    return next(record for record in reversed(caplog.records) if record.name == "openscribe.usage")


def test_main_usage_log_has_stage_in_message_and_safe_structured_fields(caplog):
    caplog.set_level("INFO", logger="openscribe.usage")
    document = _log_document(
        generator=GeneratedDocumentGeneratorType.quick_action,
        error_code="patient text must not be logged",
    )

    templates._generation_usage_event(
        event="llm_generation_failed",
        document=document,
        status="failed",
        prompt_tokens=120,
        completion_tokens=18,
        total_tokens=138,
    )

    record = _last_record(caplog)
    assert "stage=generation" in record.getMessage()
    assert "generator=quick_action" in record.getMessage()
    assert f"generated_document_id={document.id}" in record.getMessage()
    assert record.stage == "generation"
    assert record.generator == "quick_action"
    assert record.status == "failed"
    assert record.error_category == "other_error"
    assert record.total_tokens == 138
    assert record.error_code == "other_error"
    assert record.provider_error_code == "provider_server_error"
    assert record.model_used == "model-private-sentinel"
    assert "patient text must not be logged" not in caplog.text
    assert "provider-private-sentinel" not in caplog.text
    assert "model-private-sentinel" not in caplog.text


@pytest.mark.parametrize(
    ("event", "status", "error_code", "expected_category", "expected_code"),
    [
        ("llm_hallucination_check_completed", "checked_unchanged", None, None, None),
        ("llm_hallucination_check_failed", "failed_invalid_response", "duplicate_json_key", "invalid_output", "duplicate_json_key"),
        ("llm_hallucination_check_skipped", "skipped_quota", "quota_exceeded", "quota", "quota_exceeded"),
    ],
)
def test_checker_usage_log_records_controlled_outcome_without_content(
    caplog, event, status, error_code, expected_category, expected_code,
):
    caplog.set_level("INFO", logger="openscribe.usage")
    document = _log_document(generator=GeneratedDocumentGeneratorType.template)
    usage = {
        "input_tokens": 80,
        "output_tokens": 12,
        "total_tokens": 92,
        "duration_ms": 41,
        "provider_duration_ms": 35,
    }

    templates._checker_usage_event(
        event=event,
        context=templates._checker_log_context(document),
        status=status,
        usage=usage if status == "checked_unchanged" else None,
        error_code=error_code,
        provider_error_code="provider-private-sentinel" if error_code else None,
        provider_http_status=502 if error_code else None,
    )

    record = _last_record(caplog)
    assert "stage=checker" in record.getMessage()
    assert "generator=template" in record.getMessage()
    assert f"generated_document_id={document.id}" in record.getMessage()
    assert record.stage == "checker"
    assert record.status == status
    assert record.error_category == expected_category
    assert record.error_code == expected_code
    assert record.provider_error_code == ("provider_server_error" if error_code else None)
    assert record.prompt_tokens == (80 if status == "checked_unchanged" else None)
    assert record.total_tokens == (92 if status == "checked_unchanged" else None)
    assert "model-private-sentinel" not in caplog.text
    assert "provider-private-sentinel" not in caplog.text


def test_checker_log_uses_context_after_document_becomes_inaccessible(caplog):
    caplog.set_level("INFO", logger="openscribe.usage")
    class ExpiringDocument:
        def __init__(self, source):
            self.source = source
            self.expired = False

        def __getattr__(self, name):
            if self.expired:
                raise AssertionError("checker logging must not read expired ORM state")
            return getattr(self.source, name)

    document = ExpiringDocument(_log_document(generator=GeneratedDocumentGeneratorType.followup))
    context = templates._checker_log_context(document)
    document.expired = True

    templates._checker_usage_event(
        event="llm_hallucination_check_completed",
        context=context,
        status="checked_corrected",
    )

    record = _last_record(caplog)
    assert f"generated_document_id={context.generated_document_id}" in record.getMessage()
    assert f"transcript_id={context.transcript_id}" in record.getMessage()
    assert f"owner_user_id={context.owner_user_id}" in record.getMessage()
    assert f"team_id={context.team_id}" in record.getMessage()
    assert record.generator == "followup"
