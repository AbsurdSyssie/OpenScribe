import json
from uuid import uuid4

import pytest

from app.errors import AppError
from app.services.consultation_split_verification import (
    apply_split_verification_response,
    prepare_split_verification_request,
    split_verification_request_reservation_units,
)
from app.services.templates import _structured_section_definitions_snapshot
from app.schemas.templates import StructuredTemplateConfig
from app.models import LlmAdapterKind
from app.services.llm_adapters.runtime import generation_request_snapshot, request_output_token_cap
from app.services.quotas import estimate_token_reservation
from uuid import uuid4


def _plan():
    topic_ids = [uuid4(), uuid4()]
    sections = _structured_section_definitions_snapshot(StructuredTemplateConfig.model_validate({
        "profile": "emis",
        "sections": [
            {"section_key": "problem", "instruction": "p", "section_order": 0},
            {"section_key": "tasks", "instruction": "t", "section_order": 1},
        ],
    }))
    return topic_ids, {"topics": [{
        "topic_uuid": str(topic_id), "disposition": "separate_note",
        "template": {"mode": "structured", "structured_sections": sections},
    } for topic_id in topic_ids]}


def _outputs(topic_ids):
    return {topic_id: {"mode": "structured", "content": {"problem": "Original problem", "tasks": "Original task"}}
            for topic_id in topic_ids}


def test_bundled_verification_uses_exact_uuid_set_and_keeps_corrected_output_separate():
    topic_ids, plan = _plan()
    outputs = _outputs(topic_ids)
    prepared = prepare_split_verification_request(source_snapshot={"redacted": "synthetic"}, clinical_snapshot={},
        confirmed_plan=plan, survivor_outputs=outputs)
    assert set(prepared.response_json_schema["required"]) == {str(item) for item in topic_ids}
    assert "synthetic" not in repr(prepared)

    response = {str(topic_ids[0]): {"status": "corrected", "edits": [{
        "section_key": "problem", "original": "Original problem", "replacement": "Corrected problem",
    }]}, str(topic_ids[1]): {"status": "unchanged"}}
    corrected, count = apply_split_verification_response(payload_text=json.dumps(response), confirmed_plan=plan,
        survivor_outputs=outputs)
    assert count == 1
    assert outputs[topic_ids[0]]["content"]["problem"] == "Original problem"
    assert corrected[topic_ids[0]]["content"]["problem"] == "Corrected problem"


def test_bundled_verification_rejects_one_bad_topic_without_returning_a_partial_patch():
    topic_ids, plan = _plan()
    response = {str(topic_ids[0]): {"status": "unchanged"}, str(topic_ids[1]): {"status": "corrected", "edits": [{
        "section_key": "problem", "original": "not present", "replacement": "replacement",
    }]}}
    with pytest.raises(AppError, match="Bundled verification output was invalid"):
        apply_split_verification_response(payload_text=json.dumps(response), confirmed_plan=plan,
            survivor_outputs=_outputs(topic_ids))


def test_bundled_verification_rejects_freeform_survivors_instead_of_inventing_patches():
    topic_ids, plan = _plan()
    outputs = _outputs(topic_ids)
    outputs[topic_ids[0]] = {"mode": "freeform", "content": "Synthetic freeform"}
    with pytest.raises(AppError):
        prepare_split_verification_request(source_snapshot={}, clinical_snapshot={}, confirmed_plan=plan,
            survivor_outputs=outputs)


@pytest.mark.parametrize(
    ("adapter", "expected_cap"),
    [
        (LlmAdapterKind.openai_chat, 2_048),
        (LlmAdapterKind.ollama_chat, 2_048),
        (LlmAdapterKind.gemini_enterprise, 30_000),
    ],
)
def test_bundled_verification_reservation_matches_adapter_request_cap(adapter, expected_cap):
    topic_ids, plan = _plan()
    prepared = prepare_split_verification_request(
        source_snapshot={"sources": {"transcript": "Synthetic source"}},
        clinical_snapshot={}, confirmed_plan=plan, survivor_outputs=_outputs(topic_ids),
    )
    messages = prepared.request_body["messages"]
    request = generation_request_snapshot(
        adapter_kind=adapter, model="synthetic-model", user_id=uuid4(),
        system_message=messages[0]["content"], user_message=messages[1]["content"],
        output_token_cap=prepared.output_token_cap, response_json_schema=prepared.response_json_schema,
    )

    assert request_output_token_cap(request) == expected_cap
    if adapter is LlmAdapterKind.gemini_enterprise:
        assert "messages" not in request
    assert split_verification_request_reservation_units(request, prepared_messages=messages) == estimate_token_reservation(
        tuple(message["content"] for message in messages), max_completion_tokens=expected_cap,
    )
