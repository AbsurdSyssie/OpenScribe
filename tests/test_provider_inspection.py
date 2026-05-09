import pytest

from app.errors import AppError
from app.services.provider_inspection import dereference_openapi_document, display_default_from_schema_property, extract_json_path


def test_dereference_openapi_document_resolves_local_refs():
    document = {
        "openapi": "3.1.0",
        "paths": {"/transcribe": {"post": {"requestBody": {"$ref": "#/components/requestBodies/Audio"}}}},
        "components": {"requestBodies": {"Audio": {"content": {"multipart/form-data": {"schema": {"type": "object"}}}}}},
    }

    resolved = dereference_openapi_document(document)

    assert resolved["paths"]["/transcribe"]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"] == {"type": "object"}


def test_extract_json_path_supports_dot_paths_and_jsonpath_indexes():
    payload = {"result": {"text": "dot text"}, "results": [{"alternatives": [{"transcript": "jsonpath text"}]}]}

    assert extract_json_path(payload, "result.text") == "dot text"
    assert extract_json_path(payload, "$.results[0].alternatives[0].transcript") == "jsonpath text"


def test_extract_json_path_fails_without_payload_leak():
    with pytest.raises(AppError) as exc_info:
        extract_json_path({"secret": "do-not-leak"}, "$.missing[0].value")

    assert exc_info.value.code == "provider_response_invalid"
    assert "do-not-leak" not in str(exc_info.value.details)


def test_display_default_from_schema_property_priority():
    assert display_default_from_schema_property({"default": "a", "example": "b", "enum": ["c"]}) == "a"
    assert display_default_from_schema_property({"example": "b", "enum": ["c"]}) == "b"
    assert display_default_from_schema_property({"enum": ["c"]}) == "c"
    assert display_default_from_schema_property({}) is None
