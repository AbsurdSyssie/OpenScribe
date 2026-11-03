import pytest

from app.errors import AppError
from app.services import llm, stt
from app.services.provider_errors import safe_provider_error_diagnostic
from app.services.provider_inspection import dereference_openapi_document, display_default_from_schema_property, extract_json_path


def test_dereference_openapi_document_resolves_local_refs():
    document = {
        "openapi": "3.1.0",
        "info": {"title": "Provider Test API", "version": "1.0.0"},
        "paths": {"/transcribe": {"post": {"requestBody": {"$ref": "#/components/requestBodies/Audio"}, "responses": {"200": {"description": "OK"}}}}},
        "components": {"requestBodies": {"Audio": {"content": {"multipart/form-data": {"schema": {"type": "object"}}}}}},
    }

    resolved = dereference_openapi_document(document)

    assert resolved["paths"]["/transcribe"]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"] == {"type": "object"}


def test_extract_json_path_supports_dot_paths_and_jsonpath_indexes():
    payload = {"result": {"text": "dot text"}, "results": [{"alternatives": [{"transcript": "jsonpath text"}]}]}

    assert extract_json_path(payload, "result.text") == "dot text"
    assert extract_json_path(payload, "results.0.alternatives.0.transcript") == "jsonpath text"
    assert extract_json_path(payload, "$.results[0].alternatives[0].transcript") == "jsonpath text"


def test_extract_json_path_uses_jsonpath_ng_features():
    payload = {"choices": [{"message": {"content": "first"}}, {"message": {"content": "second"}}]}

    assert extract_json_path(payload, "$['choices'][0]['message']['content']") == "first"
    assert extract_json_path(payload, "$.choices[*].message.content") == ["first", "second"]


def test_extract_json_path_fails_without_payload_leak():
    with pytest.raises(AppError) as exc_info:
        extract_json_path({"secret": "do-not-leak"}, "$.missing[0].value")

    assert exc_info.value.code == "provider_response_invalid"
    assert "do-not-leak" not in str(exc_info.value.details)


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (503, "provider_server_error", "Provider returned HTTP 503 Service Unavailable (provider_server_error: the provider reported a server error)."),
        (403, "permission_denied", "Provider returned HTTP 403 Forbidden (permission_denied: the provider denied access)."),
        (None, "connection_error", "No upstream HTTP response was received; OpenScribe could not establish a provider connection (connection_error)."),
        (None, "invalid_json", "The provider returned invalid JSON (invalid_json)."),
    ],
)
def test_provider_diagnostic_formats_only_controlled_status_and_reason(status, code, expected):
    assert safe_provider_error_diagnostic(status_code=status, provider_error_code=code) == expected


def test_provider_diagnostic_ignores_unrecognized_codes_and_non_http_statuses():
    assert safe_provider_error_diagnostic(status_code="503", provider_error_code="private=sentinel") is None
    assert safe_provider_error_diagnostic(status_code=200, provider_error_code="private=sentinel") is None


def test_provider_inspection_diagnostic_ignores_provider_messages_and_unknown_codes():
    failure = AppError(
        502,
        "llm_inspection_failed",
        "private-message-with-synthetic-key",
        {
            "provider_status": 503,
            "provider_error_code": "private=provider-code",
            "provider_message": "private-response-with-synthetic-key",
        },
    )

    for diagnostic in (llm._provider_discovery_note(failure), stt._provider_inspection_diagnostic(failure)):
        assert diagnostic is not None
        assert "HTTP 503 Service Unavailable" in diagnostic
        assert "provider_server_error" in diagnostic
        assert "synthetic-key" not in diagnostic
        assert "private-response" not in diagnostic


@pytest.mark.parametrize(
    ("code", "expected_reason"),
    [
        ("invalid_json", "provider returned invalid JSON"),
        ("invalid_response", "provider returned an unreadable response"),
        ("response_too_large", "provider response exceeded the allowed size"),
        ("unsupported_content_encoding", "provider used an unsupported response encoding"),
    ],
)
def test_llm_wrapper_preserves_only_known_internal_diagnostic_codes(code, expected_reason):
    details = llm._provider_diagnostic_details({"provider_error_code": code})

    assert details == {"provider_error_code": code}
    assert expected_reason in safe_provider_error_diagnostic(provider_error_code=details["provider_error_code"])


def test_display_default_from_schema_property_priority():
    assert display_default_from_schema_property({"default": "a", "example": "b", "enum": ["c"]}) == "a"
    assert display_default_from_schema_property({"example": "b", "enum": ["c"]}) == "b"
    assert display_default_from_schema_property({"enum": ["c"]}) == "c"
    assert display_default_from_schema_property({}) is None


def test_dereference_openapi_document_rejects_invalid_specs():
    with pytest.raises(AppError) as exc_info:
        dereference_openapi_document({"openapi": "3.1.0", "info": {"title": "Broken", "version": "1"}})

    assert exc_info.value.code == "business_rule_violation"


def test_dereference_openapi_document_wraps_validator_detection_errors():
    with pytest.raises(AppError) as exc_info:
        dereference_openapi_document(
            {
                "openapi": "nope",
                "info": {"title": "Broken", "version": "1"},
                "paths": {},
            }
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "business_rule_violation"
