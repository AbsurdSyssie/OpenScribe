import json
from uuid import uuid4

import pytest

from app.errors import AppError
from app.schemas.templates import StructuredTemplateConfig
from app.services.consultation_split_verification import apply_split_verification_response
from app.services.templates import (
    _parse_generated_note_json,
    _parse_generated_structured_note_json,
    _parse_hallucination_check_response,
    _structured_section_definitions_snapshot,
)


def _structured_config():
    return StructuredTemplateConfig.model_validate({
        "profile": "emis",
        "sections": [
            {"section_key": "problem", "instruction": "Problem", "section_order": 0},
            {"section_key": "tasks", "instruction": "Tasks", "section_order": 1},
        ],
    })


def _split_plan_and_outputs():
    topic_id = uuid4()
    section_snapshot = _structured_section_definitions_snapshot(_structured_config())
    plan = {"topics": [{
        "topic_uuid": str(topic_id),
        "disposition": "separate_note",
        "template": {"mode": "structured", "structured_sections": section_snapshot},
    }]}
    outputs = {topic_id: {
        "mode": "structured",
        "content": {"problem": "Synthetic problem", "tasks": "Synthetic task"},
    }}
    return topic_id, plan, outputs


def _assert_safe_app_error(call, *, code: str, failure_code: str | None = None):
    with pytest.raises(AppError) as caught:
        call()
    error = caught.value
    assert error.code == code
    if failure_code is not None:
        assert (error.details or {}).get("failure_code") == failure_code
    assert "SensitiveKey" not in str(error)
    assert "SensitiveValue" not in str(error)
    assert "SensitiveKey" not in repr(error.details)
    assert "SensitiveValue" not in repr(error.details)
    if error.__context__ is not None:
        assert "SensitiveKey" not in str(error.__context__)
        assert "SensitiveValue" not in str(error.__context__)


@pytest.mark.parametrize("payload", [
    '{"title":"SensitiveValue","title":"Synthetic note","content":"Synthetic body"}',
    '{"title":"Synthetic note","content":"discarded","content":"Synthetic body"}',
])
def test_freeform_note_parser_rejects_duplicate_top_level_keys(payload):
    _assert_safe_app_error(
        lambda: _parse_generated_note_json(payload),
        code="llm_generation_invalid_json",
    )


def test_structured_note_parser_rejects_duplicate_keys_at_every_depth():
    config = _structured_config()
    payloads = [
        '{"title":"Synthetic note","title":"Synthetic note 2","content":{"problem":"x"}}',
        '{"title":"Synthetic note","content":{},"content":{"problem":"Synthetic problem"}}',
        '{"title":"Synthetic note","content":{"problem":"discarded","problem":"Synthetic problem"}}',
        '{"title":"Synthetic note","content":{"problem":"Synthetic problem","tasks":"x","nested":{"SensitiveKey":"SensitiveValue","SensitiveKey":"x"}}}',
        '{"title":"Synthetic note","content":{"problem":"Synthetic problem","SensitiveKey":"SensitiveValue","SensitiveKey":"x"}}',
    ]
    for payload in payloads:
        _assert_safe_app_error(
            lambda payload=payload: _parse_generated_structured_note_json(payload, template_config=config),
            code="llm_generation_invalid_json",
        )


def test_ordinary_candidate_extraction_does_not_fall_through_duplicate_object():
    # The outer object is the first balanced object. It is ambiguous even
    # though later prose contains another valid-looking object.
    payload = (
        '{"title":"discarded","title":"Synthetic note","content":"Synthetic body"}'
        ' trailing {"title":"Other note","content":"Other body"}'
    )
    _assert_safe_app_error(
        lambda: _parse_generated_note_json(payload),
        code="llm_generation_invalid_json",
    )


@pytest.mark.parametrize("payload", [
    '{"status":"corrected","status":"unchanged"}',
    '{"status":"corrected","edits":[{"section_key":"problem","original":"a","original":"SyntheticValue","replacement":"b"}]}',
    '{"status":"corrected","edits":[{"section_key":"problem","original":"a","replacement":"discarded","replacement":"SyntheticValue"}]}',
])
def test_hallucination_checker_parser_rejects_duplicate_keys(payload):
    _assert_safe_app_error(
        lambda: _parse_hallucination_check_response(payload, edit_cap=4),
        code="hallucination_check_invalid_response",
        failure_code="duplicate_json_key",
    )


def test_hallucination_checker_parser_maps_deep_json_recursion_to_safe_invalid_response():
    depth = 10_000
    deeply_nested = "{\"key\":" * depth + "0" + "}" * depth
    payload = '{"status":"unchanged","extra":' + deeply_nested + "}"
    _assert_safe_app_error(
        lambda: _parse_hallucination_check_response(payload, edit_cap=4),
        code="hallucination_check_invalid_response",
        failure_code="invalid_json",
    )


@pytest.mark.parametrize("nested", [
    '{"status":"unchanged","status":"corrected","edits":[]}',
    '{"status":"corrected","edits":[{"section_key":"problem","original":"ignored","original":"Synthetic problem","replacement":"Corrected problem"}]}',
    '{"status":"corrected","edits":[{"section_key":"problem","original":"Synthetic problem","replacement":"ignored","replacement":"Corrected problem"}]}',
])
def test_split_verifier_rejects_duplicate_nested_topic_or_edit_keys(nested):
    topic_id, plan, outputs = _split_plan_and_outputs()
    payload = '{"' + str(topic_id) + '":' + nested + '}'
    _assert_safe_app_error(
        lambda: apply_split_verification_response(
            payload_text=payload,
            confirmed_plan=plan,
            survivor_outputs=outputs,
        ),
        code="consultation_split_verification_invalid_output",
    )


def test_split_verifier_rejects_duplicate_topic_uuid_envelope_key():
    topic_id, plan, outputs = _split_plan_and_outputs()
    topic = str(topic_id)
    payload = (
        '{"' + topic + '":{"status":"unchanged"},'
        '"' + topic + '":{"status":"unchanged"}}'
    )
    _assert_safe_app_error(
        lambda: apply_split_verification_response(
            payload_text=payload,
            confirmed_plan=plan,
            survivor_outputs=outputs,
        ),
        code="consultation_split_verification_invalid_output",
    )


def test_ordinary_note_parser_keeps_fenced_and_balanced_object_compatibility():
    assert _parse_generated_note_json(
        '```json\n{"title":"Synthetic note","content":"Synthetic body"}\n```'
    ) == ("Synthetic note", "Synthetic body")
    assert _parse_generated_note_json(
        'Provider preamble: {"title":"Synthetic note","content":"Synthetic body"} trailing text'
    ) == ("Synthetic note", "Synthetic body")


def test_structured_note_parser_keeps_optional_sections_and_fenced_compatibility():
    config = _structured_config()
    assert _parse_generated_structured_note_json(
        '```json\n{"title":"Synthetic note","content":{"problem":"Synthetic problem"}}\n```',
        template_config=config,
    ) == ("Synthetic note", [{
        "section_key": "problem", "section_label": "Problem", "section_order": 0,
        "text": "Synthetic problem",
    }])


def test_checker_and_split_verifier_keep_valid_response_shapes():
    assert _parse_hallucination_check_response('{"status":"unchanged"}', edit_cap=4) == {
        "status": "unchanged", "edits": [],
    }
    topic_id, plan, outputs = _split_plan_and_outputs()
    corrected, count = apply_split_verification_response(
        payload_text=json.dumps({str(topic_id): {"status": "unchanged"}}),
        confirmed_plan=plan,
        survivor_outputs=outputs,
    )
    assert count == 0
    assert corrected[topic_id] == outputs[topic_id]
