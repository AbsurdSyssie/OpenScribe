import json
from copy import deepcopy
from uuid import UUID, uuid4

import pytest

from app.errors import AppError
from app.schemas.templates import EMIS_SECTION_KEYS
from app.services.consultation_split_generation import (
    CONSULTATION_SPLIT_GENERATION_MAX_RESPONSE_CHARS,
    CONSULTATION_SPLIT_GENERATION_CHARS_PER_OUTPUT_TOKEN,
    CONSULTATION_SPLIT_GENERATION_TITLE_OUTPUT_TOKEN_CAP,
    SplitGenerationTopic,
    parse_split_generation_partial,
    parse_split_generation,
    prepare_split_generation_request,
    split_generation_response_json_schema,
)
from app.services.templates import NOTE_GENERATION_LENGTH_TOKEN_CAPS, _structured_section_definitions_snapshot
from app.schemas.templates import StructuredTemplateConfig


def _template(*, structured: bool) -> dict[str, object]:
    template_id, version_id = uuid4(), uuid4()
    config = {
        "profile": "emis",
        "sections": [
            {"section_key": "problem", "instruction": "State the problem", "section_order": 0},
            {"section_key": "tasks", "instruction": "State tasks", "section_order": 1},
        ],
    } if structured else None
    sections = _structured_section_definitions_snapshot(StructuredTemplateConfig.model_validate(config)) if config else None
    return {
        "template_id": str(template_id), "template_version_id": str(version_id),
        "template_version_no": 1, "name": "Synthetic template", "description": None,
        "mode": "structured" if structured else "freeform", "prompt_text": "Use only supported facts.",
        "config": config, "structured_sections": sections,
    }


def _snapshots(*, count: int = 2):
    topics = []
    for index in range(count):
        separate = index < 2
        topics.append({
            "topic_uuid": str(uuid4()), "title": f"Synthetic scope {index}", "order": index,
            "is_primary": index == 0, "disposition": "separate_note" if separate else "include_in_primary",
            "template": _template(structured=index == 1) if separate else None,
        })
    return {
        "source_snapshot": {
            "source_state": {},
            "sources": {
                "transcript": "[PHI-1] synthetic consultation",
                "working_note": {"mode": "structured", "value": {"profile": "emis", "sections": {"problem": ["Synthetic problem"]}}},
                "dictation": "Synthetic dictation",
            },
            "clinical_nlp_hints": [], "phi_index": [],
        },
        "clinical_snapshot": {"clinical_nlp_hints": [{"label": "Synthetic hint"}]},
        "confirmed_plan": {"intent_id": str(uuid4()), "analysis_id": str(uuid4()), "topics": topics},
        "note_options_snapshot": {"note_generation_length": "normal", "llm_detail_level": "balanced"},
    }


def _prepared(**overrides):
    values = _snapshots()
    values.update(overrides)
    return prepare_split_generation_request(**values)


def _topics_from_plan(plan):
    result = []
    for topic in plan["topics"]:
        if topic["disposition"] != "separate_note":
            continue
        template = topic["template"]
        keys = tuple(section["section_key"] for section in template["structured_sections"]["sections"]) if template["mode"] == "structured" else ()
        result.append(SplitGenerationTopic(UUID(topic["topic_uuid"]), template["mode"], keys, 6_400))
    return result


def test_prepares_one_mixed_mode_request_with_shared_sources_once_and_deterministic_estimate():
    snapshots = _snapshots()
    first = prepare_split_generation_request(**snapshots)
    second = prepare_split_generation_request(**deepcopy(snapshots))

    assert first.request_body == second.request_body
    assert first.reservation_units == second.reservation_units
    assert first.output_token_cap == 2 * NOTE_GENERATION_LENGTH_TOKEN_CAPS["normal"] + CONSULTATION_SPLIT_GENERATION_TITLE_OUTPUT_TOKEN_CAP
    user = json.loads(first.request_body["messages"][1]["content"])
    assert set(user) == {"sources", "clinical_nlp_hints", "note_options", "topics", "sibling_boundaries"}
    assert user["sources"]["transcript"] == "[PHI-1] synthetic consultation"
    assert "sources" not in user["topics"][0]
    assert [item["topic_uuid"] for item in user["topics"]] == [item["topic_uuid"] for item in snapshots["confirmed_plan"]["topics"][:2]]
    assert user["topics"][1]["template"]["structured_section_keys"] == ["problem", "tasks"]
    assert "title" in split_generation_response_json_schema()["properties"]
    assert "title" not in split_generation_response_json_schema()["properties"]["notes"]["items"]["properties"]
    assert "synthetic consultation" not in repr(first)


def test_prepares_six_topics_and_counts_shared_input_once():
    snapshots = _snapshots(count=6)
    for topic in snapshots["confirmed_plan"]["topics"][2:]:
        topic["disposition"] = "separate_note"
        topic["template"] = _template(structured=False)
    prepared = prepare_split_generation_request(**snapshots)
    user_message = prepared.request_body["messages"][1]["content"]
    assert prepared.output_token_cap == 6 * NOTE_GENERATION_LENGTH_TOKEN_CAPS["normal"] + CONSULTATION_SPLIT_GENERATION_TITLE_OUTPUT_TOKEN_CAP
    assert user_message.count("[PHI-1] synthetic consultation") == 1
    assert len(json.loads(user_message)["topics"]) == 6


def test_parser_accepts_exact_mixed_modes_in_confirmed_order_and_emis_contract_keys():
    snapshots = _snapshots()
    topics = _topics_from_plan(snapshots["confirmed_plan"])
    raw = {
        "title": "Overall consultation",
        "notes": [
            {"topic_uuid": str(topics[1].topic_uuid), "mode": "structured", "content": {"problem": "Problem", "tasks": "Task"}},
            {"topic_uuid": str(topics[0].topic_uuid), "mode": "freeform", "content": "Freeform note"},
        ]
    }
    notes = parse_split_generation(json.dumps(raw), topics=topics)
    assert [note.topic_uuid for note in notes] == [topic.topic_uuid for topic in topics]
    assert notes[1].content == {"problem": "Problem", "tasks": "Task"}
    assert set(EMIS_SECTION_KEYS) == {"problem", "history", "family_history", "social_history", "examination", "comment", "tasks", "investigations"}
    assert "Freeform note" not in repr(notes[0])


@pytest.mark.parametrize("payload", [
    lambda topics: {"extra": 1, "notes": []},
    lambda topics: {"title": "Overall consultation", "notes": [{"topic_uuid": str(topics[0].topic_uuid), "mode": "freeform", "content": "x", "title": "no"}, {"topic_uuid": str(topics[1].topic_uuid), "mode": "structured", "content": {"problem": "p", "tasks": "t"}}]},
    lambda topics: {"title": "Overall consultation", "notes": [{"topic_uuid": str(topics[0].topic_uuid), "mode": "freeform", "content": "x"}, {"topic_uuid": str(topics[0].topic_uuid), "mode": "structured", "content": {"problem": "p", "tasks": "t"}}]},
    lambda topics: {"title": "Overall consultation", "notes": [{"topic_uuid": str(uuid4()), "mode": "freeform", "content": "x"}, {"topic_uuid": str(topics[1].topic_uuid), "mode": "structured", "content": {"problem": "p", "tasks": "t"}}]},
    lambda topics: {"title": "Overall consultation", "notes": [{"topic_uuid": str(topics[0].topic_uuid), "mode": "structured", "content": {"problem": "p", "tasks": "t"}}, {"topic_uuid": str(topics[1].topic_uuid), "mode": "structured", "content": {"problem": "p", "tasks": "t"}}]},
    lambda topics: {"title": "Overall consultation", "notes": [{"topic_uuid": str(topics[0].topic_uuid), "mode": "freeform", "content": "[PHI-1]"}, {"topic_uuid": str(topics[1].topic_uuid), "mode": "structured", "content": {"problem": "p", "tasks": "[PHI-2]"}}]},
    lambda topics: {"title": "Overall consultation", "notes": [{"topic_uuid": str(topics[0].topic_uuid), "mode": "freeform", "content": "x"}, {"topic_uuid": str(topics[1].topic_uuid), "mode": "structured", "content": {"problem": "p"}}]},
    lambda topics: {"title": "Overall consultation", "notes": [{"topic_uuid": str(topics[0].topic_uuid), "mode": "freeform", "content": "x"}, {"topic_uuid": str(topics[1].topic_uuid), "mode": "structured", "content": {"problem": "p", "tasks": 3}}]},
    lambda topics: {"title": "Overall consultation", "notes": [{"topic_uuid": str(topics[0].topic_uuid), "mode": "freeform", "content": "x"}, {"topic_uuid": str(topics[1].topic_uuid), "mode": "structured", "content": {"problem": "p", "tasks": "t", "other": "x"}}]},
])
def test_parser_rejects_untrusted_envelopes_exact_uuid_set_modes_and_content(payload):
    topics = _topics_from_plan(_snapshots()["confirmed_plan"])
    with pytest.raises(AppError) as exc:
        parse_split_generation(payload(topics), topics=topics)
    assert exc.value.code == "consultation_split_generation_invalid_output"


def test_parser_rejects_duplicate_json_keys_malformed_json_and_per_note_bound_without_content_leaks():
    topic = SplitGenerationTopic(uuid4(), "freeform", max_content_chars=4)
    cases = [
        '{"title":"Overall consultation","notes":[{"topic_uuid":"%s","topic_uuid":"%s","mode":"freeform","content":"x"}]}' % (topic.topic_uuid, topic.topic_uuid),
        '{"title":"Overall consultation","notes":[{"topic_uuid":"%s","mode":"structured","content":{"problem":"a","problem":"b"}}]}' % topic.topic_uuid,
        '{bad json',
        json.dumps({"title": "Overall consultation", "notes": [{"topic_uuid": str(topic.topic_uuid), "mode": "freeform", "content": "secret-too-large"}]}),
    ]
    for raw in cases:
        with pytest.raises(AppError) as exc:
            parse_split_generation(raw, topics=[topic])
        assert exc.value.code == "consultation_split_generation_invalid_output"
        assert "secret-too-large" not in str(exc.value)


def test_rejects_snapshot_bounds_and_malformed_structured_contract_without_echoing_content():
    snapshots = _snapshots()
    snapshots["confirmed_plan"]["topics"][0]["template"]["prompt_text"] = "x" * 20_001
    with pytest.raises(AppError) as exc:
        prepare_split_generation_request(**snapshots)
    assert exc.value.code == "consultation_split_generation_input_too_large"

    snapshots = _snapshots()
    snapshots["confirmed_plan"]["topics"][1]["template"]["config"]["sections"][0]["section_key"] = "unknown"
    with pytest.raises(AppError) as exc:
        prepare_split_generation_request(**snapshots)
    assert exc.value.code == "consultation_split_generation_input_invalid"
    assert "unknown" not in str(exc.value)


def test_output_bound_tracks_frozen_note_option():
    snapshots = _snapshots()
    snapshots["note_options_snapshot"] = {"note_generation_length": "short", "llm_detail_level": "concise"}
    prepared = prepare_split_generation_request(**snapshots)
    assert prepared.output_token_cap == 1600 + CONSULTATION_SPLIT_GENERATION_TITLE_OUTPUT_TOKEN_CAP
    assert CONSULTATION_SPLIT_GENERATION_CHARS_PER_OUTPUT_TOKEN * 800 == 3200


def test_parser_applies_total_response_cap_to_mapping_inputs_too():
    topic = SplitGenerationTopic(uuid4(), "freeform")
    payload = {"title": "Overall consultation", "notes": [{"topic_uuid": str(topic.topic_uuid), "mode": "freeform", "content": "x" * CONSULTATION_SPLIT_GENERATION_MAX_RESPONSE_CHARS}]}
    with pytest.raises(AppError) as exc:
        parse_split_generation(payload, topics=[topic])
    assert exc.value.code == "consultation_split_generation_invalid_output"


def test_partial_parser_salvages_only_identified_valid_topics():
    first, second = SplitGenerationTopic(uuid4(), "freeform"), SplitGenerationTopic(uuid4(), "freeform")
    parsed = parse_split_generation_partial({"title": "Overall consultation", "notes": [
        {"topic_uuid": str(first.topic_uuid), "mode": "freeform", "content": "Valid draft"},
        {"topic_uuid": str(second.topic_uuid), "mode": "freeform", "content": "   "},
    ]}, topics=[first, second])
    assert [note.topic_uuid for note in parsed.notes] == [first.topic_uuid]
    assert parsed.title == "Overall consultation"
    assert parsed.failed_topic_uuids == (second.topic_uuid,)


def test_structured_parser_allows_empty_optional_sections():
    topic = SplitGenerationTopic(uuid4(), "structured", ("problem", "investigations"))
    parsed = parse_split_generation({"title": "Overall consultation", "notes": [{
        "topic_uuid": str(topic.topic_uuid), "mode": "structured",
        "content": {"problem": "Synthetic problem", "investigations": ""},
    }]}, topics=[topic])
    assert parsed[0].content["investigations"] == ""


def test_partial_parser_rejects_ambiguous_outer_envelope():
    topic = SplitGenerationTopic(uuid4(), "freeform")
    with pytest.raises(AppError) as exc:
        parse_split_generation_partial({"title": "Overall consultation", "notes": [
            {"topic_uuid": str(topic.topic_uuid), "mode": "freeform", "content": "One"},
            {"topic_uuid": str(topic.topic_uuid), "mode": "freeform", "content": "Two"},
        ]}, topics=[topic])
    assert exc.value.code == "consultation_split_generation_invalid_output"
