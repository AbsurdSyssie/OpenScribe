import json
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from app.errors import AppError
from app.models import LlmAdapterKind
from app.services.consultation_split_analysis import (
    CONSULTATION_SPLIT_MAX_PROMPT_CHARS,
    CONSULTATION_SPLIT_MAX_RESPONSE_CHARS,
    CONSULTATION_SPLIT_MAX_TOPICS,
    CONSULTATION_SPLIT_ANALYSIS_OUTPUT_TOKENS,
    build_split_analysis_prompt,
    normalize_split_template_candidates,
    parse_split_analysis_output,
    prepare_split_analysis_request,
    split_analysis_response_json_schema,
)
from app.services.llm_adapters.types import LlmProviderSnapshot
from app.services.llm_adapters.runtime import request_output_token_cap
from app.services.quotas import estimate_token_reservation


def _candidate(*, template_id: UUID | None = None, name: str = "Primary", mode: str = "freeform") -> dict:
    return {
        "id": str(template_id or uuid4()),
        "name": name,
        "description": "A short description",
        "mode": mode,
    }


def _output(*topics: dict) -> str:
    return json.dumps({"topics": list(topics)})


def _topic(title: str = "Headache", **overrides: object) -> dict:
    value = {
        "title": title,
        "is_primary": True,
        "disposition": "separate_note",
        "template_id": None,
    }
    value.update(overrides)
    return value


def _provider_snapshot(adapter: LlmAdapterKind = LlmAdapterKind.openai_chat) -> LlmProviderSnapshot:
    return LlmProviderSnapshot(
        llm_config_id=str(uuid4()),
        provider_preset="synthetic",
        adapter_kind=adapter.value,
        base_url="https://provider.example.test/v1",
        model="synthetic-model",
        auth_mode="bearer",
        provider_config={},
    )


def _prepared_sources(*, transcript: object = "Redacted transcript", working_note: object = None, dictation: object = "") -> dict:
    return {
        "source_state": {"transcript": {"present": bool(transcript)}},
        "sources": {
            "transcript": transcript,
            "working_note": working_note if isinstance(working_note, dict) else {"mode": None, "value": working_note},
            "dictation": dictation,
        },
        "clinical_nlp_hints": [],
        "phi_index": [],
    }


def _prepared_candidates(*items: dict) -> dict:
    return {"templates": list(items)}


def _invalid_output(output: str, *, candidate_ids: list[UUID | str] = ()) -> AppError:
    with pytest.raises(AppError) as exc_info:
        parse_split_analysis_output(output, candidate_ids=candidate_ids)
    return exc_info.value


def test_empty_topics_is_valid_and_has_no_primary():
    result = parse_split_analysis_output('{"topics": []}')
    assert result.topics == []


def test_valid_topics_get_distinct_server_uuid_and_preserve_contract():
    template_id = uuid4()
    result = parse_split_analysis_output(
        _output(
            _topic("Headache", template_id=str(template_id)),
            _topic("Medication review", is_primary=False, template_id=None),
        ),
        candidate_ids=[template_id],
    )
    assert len(result.topics) == 2
    assert all(topic.topic_uuid.version == 4 for topic in result.topics)
    assert result.topics[0].topic_uuid != result.topics[1].topic_uuid
    assert result.topics[0].template_id == template_id


@pytest.mark.parametrize(
    "output",
    [
        "",
        "not json",
        "[]",
        "null",
        "{}",
        '{"topics": []} trailing text',
        '{"topics": [',
    ],
)
def test_malformed_json_and_envelope_fail_without_truncation(output: str):
    error = _invalid_output(output)
    assert error.code == "consultation_split_analysis_invalid"
    assert error.status_code == 502
    assert "not json" not in error.message


@pytest.mark.parametrize(
    "output",
    [
        '{"topics": [], "topics": []}',
        '{"topics": [{"title":"A","is_primary":true,"disposition":"separate_note","template_id":null,"template_id":null}]}',
        '{"topics": [{"title":"A","is_primary":true,"disposition":"separate_note","template_id":null,"metadata":{"x":1,"x":2}}]}',
    ],
)
def test_duplicate_json_object_keys_at_root_or_nested_levels_are_rejected_safely(output: str):
    error = _invalid_output(output)
    assert error.code == "consultation_split_analysis_invalid"
    assert error.details is None


def test_oversized_response_fails_without_attempting_to_recover_a_prefix():
    error = _invalid_output("{" + "x" * CONSULTATION_SPLIT_MAX_RESPONSE_CHARS)
    assert error.code == "consultation_split_analysis_invalid"


def test_deeply_nested_response_fails_safely_without_recursion_error():
    output = "[" * 10_000 + "0" + "]" * 10_000
    assert len(output) <= CONSULTATION_SPLIT_MAX_RESPONSE_CHARS
    error = _invalid_output(output)
    assert error.code == "consultation_split_analysis_invalid"
    assert error.status_code == 502


@pytest.mark.parametrize(
    "output",
    [
        '{"topics": [], "extra": true}',
        '{"topics": [{"title":"A","is_primary":true,"disposition":"separate_note","template_id":null,"topic_id":"bad"}]}',
        '{"topics": [{"title":"A","is_primary":true,"disposition":"separate_note","template_id":null,"confidence":0.9}]}',
    ],
)
def test_extra_fields_are_rejected_instead_of_being_ignored(output: str):
    assert _invalid_output(output).code == "consultation_split_analysis_invalid"


def test_topic_fields_are_required_even_when_template_is_null():
    missing_template_id = {"title": "A", "is_primary": True, "disposition": "separate_note"}
    assert _invalid_output(json.dumps({"topics": [missing_template_id]})).code == "consultation_split_analysis_invalid"


def test_topic_count_is_capped_at_six():
    topics = [_topic(f"Topic {index}", is_primary=index == 0) for index in range(CONSULTATION_SPLIT_MAX_TOPICS + 1)]
    assert _invalid_output(_output(*topics)).code == "consultation_split_analysis_invalid"


@pytest.mark.parametrize(
    "topics",
    [
        [_topic("A", is_primary=False)],
        [_topic("A"), _topic("B")],
        [_topic("A", disposition="include_in_primary")],
        [_topic("A", disposition="exclude_from_notes")],
    ],
)
def test_primary_rules_are_strict(topics: list[dict]):
    assert _invalid_output(_output(*topics)).code == "consultation_split_analysis_invalid"


def test_secondary_topic_can_be_included_or_excluded():
    result = parse_split_analysis_output(
        _output(
            _topic("A"),
            _topic("B", is_primary=False, disposition="include_in_primary"),
            _topic("C", is_primary=False, disposition="exclude_from_notes"),
        )
    )
    assert [topic.disposition for topic in result.topics] == [
        "separate_note",
        "include_in_primary",
        "exclude_from_notes",
    ]


def test_old_short_exclude_disposition_is_rejected():
    assert _invalid_output(_output(_topic("A"), _topic("B", is_primary=False, disposition="exclude"))).code == "consultation_split_analysis_invalid"


@pytest.mark.parametrize("title", ["", " ", "\n\t"])
def test_blank_titles_fail(title: str):
    assert _invalid_output(_output(_topic(title))).code == "consultation_split_analysis_invalid"


def test_duplicate_titles_are_rejected_case_insensitively_after_normalization():
    assert _invalid_output(_output(_topic("  Headache  "), _topic("headache", is_primary=False))).code == "consultation_split_analysis_invalid"


def test_duplicate_template_use_is_allowed_for_distinct_topics():
    template_id = uuid4()
    result = parse_split_analysis_output(
        _output(
            _topic("A", template_id=str(template_id)),
            _topic("B", is_primary=False, template_id=str(template_id)),
        ),
        candidate_ids=[template_id],
    )
    assert [topic.template_id for topic in result.topics] == [template_id, template_id]


def test_unknown_template_id_is_rejected():
    known = uuid4()
    unknown = uuid4()
    error = _invalid_output(_output(_topic(template_id=str(unknown))), candidate_ids=[known])
    assert error.code == "consultation_split_analysis_invalid"
    assert "template" in error.message.lower()


def test_provider_cannot_supply_topic_identity():
    output = _output(_topic(topic_uuid=str(uuid4())))
    assert _invalid_output(output).code == "consultation_split_analysis_invalid"


def test_server_uuid_factory_is_called_only_after_full_validation():
    calls: list[int] = []

    def factory() -> UUID:
        calls.append(1)
        return uuid4()

    _invalid_output(_output(_topic("A"), _topic("a", is_primary=False)))
    assert calls == []
    result = parse_split_analysis_output(_output(_topic("A")), topic_uuid_factory=factory)
    assert len(calls) == 1
    assert result.topics[0].topic_uuid


def test_uuid_factory_cannot_return_provider_controlled_or_duplicate_identity():
    fixed = uuid4()
    values = iter([fixed, fixed])
    with pytest.raises(AppError):
        parse_split_analysis_output(
            _output(_topic("A"), _topic("B", is_primary=False)),
            topic_uuid_factory=lambda: next(values),
        )


def test_prompt_contains_only_allowlisted_candidate_metadata_and_no_source_instruction_confusion():
    candidate_id = uuid4()
    prompt = build_split_analysis_prompt(
        transcript="[PERSON_1] has cough.",
        working_note="Assess [PERSON_1].",
        dictation="Follow-up in two weeks.",
        clinical_nlp_hints=[{"entity_type": "SYMPTOM", "text": "cough", "secret": "do not send"}],
        candidates=[
            {
                **_candidate(template_id=candidate_id, mode="structured"),
                "prompt_text": "secret template instructions",
                "config_json": {"sections": ["secret"]},
            }
        ],
    )
    assert "secret template instructions" not in prompt.user_message
    assert "config_json" not in prompt.user_message
    payload = json.loads(prompt.user_message)
    assert payload["template_candidates"] == [
        {
            "id": str(candidate_id),
            "name": "Primary",
            "description": "A short description",
            "mode": "structured",
        }
    ]
    assert payload["clinical_nlp_hints"] == [{"entity_type": "SYMPTOM", "text": "cough"}]
    assert "untrusted data" in prompt.system_message
    assert "Preserve PHI placeholders exactly" in prompt.system_message
    assert "individual clinical-NLP entity mentions" in prompt.system_message
    assert "small incidental facts" in prompt.system_message


def test_prompt_serializes_missing_sources_without_inventing_content():
    prompt = build_split_analysis_prompt(candidates=[])
    payload = json.loads(prompt.user_message)
    assert payload["sources"]["transcript"] == "[transcript: none provided]"
    assert payload["sources"]["working_note"] == "[working note: none provided]"
    assert payload["sources"]["dictation"] == "[dictation: none provided]"
    assert payload["template_candidates"] == []


def test_candidate_normalization_rejects_duplicate_ids_and_keeps_actual_mode():
    candidate_id = uuid4()
    with pytest.raises(AppError) as exc_info:
        normalize_split_template_candidates([_candidate(template_id=candidate_id), _candidate(template_id=candidate_id, name="Copy")])
    assert exc_info.value.code == "consultation_split_analysis_candidates_invalid"


@pytest.mark.parametrize(
    "candidate",
    [
        {"id": "not-a-uuid", "name": "T", "description": None, "mode": "freeform"},
        {"id": str(uuid4()), "name": "", "description": None, "mode": "freeform"},
        {"id": str(uuid4()), "name": "T", "description": None, "mode": "unsupported"},
        {"id": str(uuid4()), "name": "T", "description": {"secret": "value"}, "mode": "freeform"},
    ],
)
def test_invalid_candidate_metadata_is_safe_app_error(candidate: dict):
    with pytest.raises(AppError) as exc_info:
        normalize_split_template_candidates([candidate])
    assert exc_info.value.code == "consultation_split_analysis_candidates_invalid"
    assert "secret" not in str(exc_info.value)


def test_prompt_has_an_aggregate_size_bound():
    source = "x" * 100_000
    with pytest.raises(AppError) as exc_info:
        build_split_analysis_prompt(transcript=source, working_note=source, dictation=source)
    assert exc_info.value.code == "consultation_split_analysis_input_too_large"


def test_prompt_size_constant_is_meaningfully_bounded():
    assert CONSULTATION_SPLIT_MAX_PROMPT_CHARS < 1_000_000


def test_candidate_normalization_accepts_enum_like_mode_and_template_like_objects():
    class Mode:
        value = "structured"

    class Version:
        mode = Mode()

    class Template:
        id = uuid4()
        name = "Structured"
        description = "A note"
        latest_version = Version()
        prompt_text = "must never appear"
        config_json = {"secret": True}

    normalized = normalize_split_template_candidates([Template()])
    assert normalized[0].mode == "structured"
    assert normalized[0].id == Template.id


@pytest.mark.parametrize(
    ("adapter", "expected_keys"),
    [
        (LlmAdapterKind.openai_chat, {"model", "temperature", "max_completion_tokens", "user", "messages"}),
        (LlmAdapterKind.bedrock_chat, {"model", "temperature", "max_completion_tokens", "user", "messages"}),
        (LlmAdapterKind.ollama_chat, {"model", "stream", "messages", "options"}),
        (LlmAdapterKind.gemini_enterprise, {"model", "contents", "config"}),
    ],
)
def test_prepare_request_uses_existing_adapter_shapes_and_nominal_cap(adapter, expected_keys):
    owner_id = uuid4()
    prepared = prepare_split_analysis_request(
        owner_user_id=owner_id,
        provider_snapshot=_provider_snapshot(adapter),
        source_snapshot=_prepared_sources(),
        candidate_template_snapshot=_prepared_candidates(),
    )

    assert set(prepared.request_body) == expected_keys
    assert prepared.output_token_cap == CONSULTATION_SPLIT_ANALYSIS_OUTPUT_TOKENS
    actual_cap = request_output_token_cap(prepared.request_body)
    assert isinstance(actual_cap, int)
    system_message, user_message = build_split_analysis_prompt(
        transcript="Redacted transcript", working_note=None, dictation=None, candidates=[]
    )
    # The exact content differs from the helper fixture, but the reservation
    # must always cover the actual provider cap. Gemini's is 30,000.
    assert prepared.reservation_units >= actual_cap
    if adapter in {LlmAdapterKind.openai_chat, LlmAdapterKind.bedrock_chat}:
        assert prepared.request_body["temperature"] == 0.0
        assert prepared.request_body["max_completion_tokens"] == CONSULTATION_SPLIT_ANALYSIS_OUTPUT_TOKENS
        assert prepared.request_body["user"] == str(owner_id)
    elif adapter is LlmAdapterKind.ollama_chat:
        assert prepared.request_body["options"] == {"num_predict": CONSULTATION_SPLIT_ANALYSIS_OUTPUT_TOKENS}
    else:
        # Gemini's shared runtime intentionally applies its provider ceiling;
        # the returned cap remains the nominal split-analysis cap.
        assert prepared.request_body["config"]["temperature"] == 0.0
        assert prepared.request_body["config"]["max_output_tokens"] == 30_000
        assert prepared.request_body["config"]["response_json_schema"] == prepared.response_json_schema
        assert actual_cap == 30_000


def test_prepare_request_schema_is_exact_and_parser_remains_authoritative():
    schema = split_analysis_response_json_schema()
    assert schema == {
        "type": "object",
        "additionalProperties": False,
        "required": ["topics"],
        "properties": {
            "topics": {
                "type": "array",
                "minItems": 0,
                "maxItems": CONSULTATION_SPLIT_MAX_TOPICS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["title", "is_primary", "disposition", "template_id"],
                    "properties": {
                        "title": {"type": "string", "minLength": 1, "maxLength": 255},
                        "is_primary": {"type": "boolean"},
                        "disposition": {
                            "type": "string",
                            "enum": ["separate_note", "include_in_primary", "exclude_from_notes"],
                        },
                        "template_id": {"type": ["string", "null"]},
                    },
                },
            }
        },
    }
    assert parse_split_analysis_output('{"topics": []}').topics == []


def test_prepare_request_preserves_structured_working_note_and_does_not_mutate_inputs():
    source = _prepared_sources(
        transcript="Redacted transcript",
        working_note={
            "mode": "structured",
            "value": {"profile": "emis", "sections": {"problem": ["[PHI-1]"]}},
        },
        dictation=None,
    )
    candidates = _prepared_candidates(_candidate(name="Structured", mode="structured"))
    before_source = json.loads(json.dumps(source))
    before_candidates = json.loads(json.dumps(candidates))
    prepared = prepare_split_analysis_request(
        owner_user_id=uuid4(),
        provider_snapshot=_provider_snapshot(),
        source_snapshot=source,
        candidate_template_snapshot=candidates,
    )
    payload = json.loads(prepared.request_body["messages"][1]["content"])
    working_note = json.loads(payload["sources"]["working_note"])
    assert working_note == source["sources"]["working_note"]
    assert source == before_source
    assert candidates == before_candidates
    assert "phi_index" not in json.dumps(prepared.request_body)


@pytest.mark.parametrize(
    "structured_value",
    [
        {
            "profile": "emis",
            "sections": {"problem": ["Supported"], "unknown": ["Not supported"]},
        },
        {
            "profile": "emis",
            "sections": {"problem": [{"nested": "Not a line"}]},
        },
        {
            "profile": "emis",
            "sections": {"problem": ["x" * 4_001]},
        },
    ],
)
def test_prepare_request_rejects_noncanonical_structured_emis_values(structured_value):
    source = _prepared_sources(
        transcript=None,
        working_note={"mode": "structured", "value": structured_value},
        dictation=None,
    )
    with pytest.raises(AppError) as exc_info:
        prepare_split_analysis_request(
            owner_user_id=uuid4(),
            provider_snapshot=_provider_snapshot(),
            source_snapshot=source,
            candidate_template_snapshot=_prepared_candidates(),
        )
    assert exc_info.value.code == "consultation_split_analysis_input_invalid"
    assert "Not supported" not in str(exc_info.value)
    assert "Not a line" not in str(exc_info.value)


def test_prepare_request_accepts_canonical_structured_emis_with_all_sections():
    section_keys = (
        "problem",
        "history",
        "family_history",
        "social_history",
        "examination",
        "comment",
        "tasks",
        "investigations",
    )
    structured_value = {
        "profile": "emis",
        "sections": {key: [f"Synthetic {key}"] for key in section_keys},
    }
    source = _prepared_sources(
        transcript=None,
        working_note={"mode": "structured", "value": structured_value},
        dictation=None,
    )
    prepared = prepare_split_analysis_request(
        owner_user_id=uuid4(),
        provider_snapshot=_provider_snapshot(),
        source_snapshot=source,
        candidate_template_snapshot=_prepared_candidates(),
    )
    payload = json.loads(prepared.request_body["messages"][1]["content"])
    assert json.loads(payload["sources"]["working_note"]) == {
        "mode": "structured",
        "value": structured_value,
    }


def test_prepare_request_rejects_empty_sources_even_when_hints_exist():
    empty_source = _prepared_sources(transcript=" \n", working_note={"mode": None, "value": None}, dictation="")
    empty_source["clinical_nlp_hints"] = [{"text": "hint"}]
    with pytest.raises(AppError) as empty_error:
        prepare_split_analysis_request(
            owner_user_id=uuid4(), provider_snapshot=_provider_snapshot(), source_snapshot=empty_source,
            candidate_template_snapshot=_prepared_candidates(),
        )
    assert empty_error.value.code == "consultation_split_source_empty"

    hint_only = _prepared_sources(transcript="source")
    hint_only["sources"]["transcript"] = None
    hint_only["clinical_nlp_hints"] = [{}]
    with pytest.raises(AppError) as hint_error:
        prepare_split_analysis_request(
            owner_user_id=uuid4(), provider_snapshot=_provider_snapshot(), source_snapshot=hint_only,
            candidate_template_snapshot=_prepared_candidates(),
        )
    assert hint_error.value.code == "consultation_split_source_empty"

    malformed_hint = _prepared_sources()
    malformed_hint["clinical_nlp_hints"] = [{}]
    with pytest.raises(AppError) as malformed_hint_error:
        prepare_split_analysis_request(
            owner_user_id=uuid4(), provider_snapshot=_provider_snapshot(), source_snapshot=malformed_hint,
            candidate_template_snapshot=_prepared_candidates(),
        )
    assert malformed_hint_error.value.code == "consultation_split_analysis_input_invalid"


@pytest.mark.parametrize(
    "source",
    [
        {"sources": {}, "source_state": {}, "clinical_nlp_hints": [], "phi_index": []},
        _prepared_sources(transcript=object()),
        _prepared_sources(working_note={"mode": "structured", "value": {"profile": "wrong", "sections": {}}}),
        _prepared_sources(working_note={"mode": "freeform", "value": {"raw": "object"}}),
        _prepared_sources(transcript="x", dictation=object()),
        dict(_prepared_sources(), extra="not allowed"),
    ],
)
def test_prepare_request_rejects_malformed_source_snapshots(source):
    with pytest.raises(AppError) as exc_info:
        prepare_split_analysis_request(
            owner_user_id=uuid4(),
            provider_snapshot=_provider_snapshot(),
            source_snapshot=source,
            candidate_template_snapshot=_prepared_candidates(),
        )
    assert exc_info.value.code == "consultation_split_analysis_input_invalid"


def test_prepare_request_rejects_candidate_extras_duplicates_and_overflow():
    candidate_id = uuid4()
    valid = _candidate(template_id=candidate_id)
    with pytest.raises(AppError):
        prepare_split_analysis_request(
            owner_user_id=uuid4(), provider_snapshot=_provider_snapshot(), source_snapshot=_prepared_sources(),
            candidate_template_snapshot=_prepared_candidates(dict(valid, extra="not allowed")),
        )
    with pytest.raises(AppError):
        prepare_split_analysis_request(
            owner_user_id=uuid4(), provider_snapshot=_provider_snapshot(), source_snapshot=_prepared_sources(),
            candidate_template_snapshot=_prepared_candidates(valid, dict(valid)),
        )
    with pytest.raises(AppError):
        prepare_split_analysis_request(
            owner_user_id=uuid4(), provider_snapshot=_provider_snapshot(), source_snapshot=_prepared_sources(),
            candidate_template_snapshot=_prepared_candidates(*[dict(_candidate(name=f"T{i}")) for i in range(101)]),
        )


def test_prepare_request_rejects_provider_secrets_unsupported_adapter_and_bad_cap():
    secret = replace(_provider_snapshot(), provider_config={"api_key": "secret"})
    with pytest.raises(AppError) as secret_error:
        prepare_split_analysis_request(
            owner_user_id=uuid4(), provider_snapshot=secret, source_snapshot=_prepared_sources(),
            candidate_template_snapshot=_prepared_candidates(),
        )
    assert secret_error.value.code == "consultation_split_analysis_input_invalid"

    empty_config = replace(_provider_snapshot(), provider_config={"project_id": " "})
    with pytest.raises(AppError) as empty_config_error:
        prepare_split_analysis_request(
            owner_user_id=uuid4(), provider_snapshot=empty_config, source_snapshot=_prepared_sources(),
            candidate_template_snapshot=_prepared_candidates(),
        )
    assert empty_config_error.value.code == "consultation_split_analysis_input_invalid"

    unsafe_url = replace(_provider_snapshot(), base_url="http://metadata.google.internal/v1")
    with pytest.raises(AppError) as unsafe_url_error:
        prepare_split_analysis_request(
            owner_user_id=uuid4(), provider_snapshot=unsafe_url, source_snapshot=_prepared_sources(),
            candidate_template_snapshot=_prepared_candidates(),
        )
    assert unsafe_url_error.value.code == "consultation_split_analysis_provider_invalid"

    unsupported = LlmProviderSnapshot(
        llm_config_id=str(uuid4()), provider_preset="synthetic", adapter_kind="unsupported",
        base_url="https://provider.example.test/v1", model="synthetic-model", auth_mode="bearer", provider_config={},
    )
    with pytest.raises(AppError) as adapter_error:
        prepare_split_analysis_request(
            owner_user_id=uuid4(), provider_snapshot=unsupported, source_snapshot=_prepared_sources(),
            candidate_template_snapshot=_prepared_candidates(),
        )
    assert adapter_error.value.code == "consultation_split_analysis_provider_invalid"
    with pytest.raises(AppError):
        prepare_split_analysis_request(
            owner_user_id=uuid4(), provider_snapshot=_provider_snapshot(), source_snapshot=_prepared_sources(),
            candidate_template_snapshot=_prepared_candidates(), output_token_cap=513,
        )
