from types import SimpleNamespace
from uuid import uuid4

import pytest
from google.auth import exceptions as google_auth_exceptions
from google.genai import errors as genai_errors

from app.errors import AppError
from app.models import LlmAdapterKind
from app.schemas.templates import StructuredTemplateConfig
from app.services.llm_adapters import LlmGenerationRequest
from app.services.llm_adapters import gemini_enterprise as adapter
from app.services.templates import (
    _generation_request_snapshot,
    _gemini_request_from_snapshot,
    _structured_note_response_json_schema,
)


class FakeModels:
    def __init__(self):
        self.list_result = []
        self.count_result = SimpleNamespace(total_tokens=7)
        self.generate_result = SimpleNamespace(
            text="generated",
            usage_metadata=SimpleNamespace(
                prompt_token_count=11,
                candidates_token_count=5,
                total_token_count=16,
            ),
            candidates=[SimpleNamespace(finish_reason=SimpleNamespace(value="STOP"))],
        )
        self.list_error = None
        self.count_error = None
        self.generate_error = None
        self.count_calls = []
        self.generate_calls = []

    def list(self, **kwargs):
        if self.list_error:
            raise self.list_error
        return self.list_result

    def count_tokens(self, **kwargs):
        self.count_calls.append(kwargs)
        if self.count_error:
            raise self.count_error
        return self.count_result

    def generate_content(self, **kwargs):
        self.generate_calls.append(kwargs)
        if self.generate_error:
            raise self.generate_error
        return self.generate_result


class FakeClient:
    def __init__(self):
        self.models = FakeModels()
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


def _install_client(monkeypatch, client):
    monkeypatch.setattr(adapter, "build_gemini_client", lambda **_: client)


def _request(*, expect_json=False, response_json_schema=None, model="publishers/google/models/gemini-test"):
    return LlmGenerationRequest(
        model=model,
        system_message="System",
        user_message="User",
        temperature=0.2,
        max_output_tokens=1600,
        expect_json=expect_json,
        response_json_schema=response_json_schema,
    )


def test_build_client_uses_enterprise_v1_and_explicit_settings(monkeypatch):
    captured = {}
    sentinel = object()

    def fake_client(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(adapter.genai, "Client", fake_client)

    result = adapter.build_gemini_client(
        project_id="clinical-prod",
        location="europe-west2",
        credentials=None,
        capacity_mode="dedicated",
    )

    assert result is sentinel
    assert captured["enterprise"] is True
    assert captured["project"] == "clinical-prod"
    assert captured["location"] == "europe-west2"
    assert captured["credentials"] is None
    assert captured["http_options"].api_version == "v1"
    assert captured["http_options"].headers == {"X-Vertex-AI-LLM-Request-Type": "dedicated"}


def test_service_account_conversion_uses_cloud_platform_scope(monkeypatch):
    captured = {}
    sentinel = object()

    def convert(info, **kwargs):
        captured["info"] = info
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(adapter.service_account.Credentials, "from_service_account_info", convert)
    result = adapter.service_account_credentials_from_info({"type": "service_account", "private_key": "secret"})

    assert result is sentinel
    assert captured["scopes"] == ["https://www.googleapis.com/auth/cloud-platform"]


def test_service_account_conversion_does_not_expose_credential(monkeypatch):
    monkeypatch.setattr(
        adapter.service_account.Credentials,
        "from_service_account_info",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("private_key=SECRET")),
    )

    with pytest.raises(AppError) as caught:
        adapter.service_account_credentials_from_info({"private_key": "SECRET"})

    assert caught.value.code == "llm_invalid_credential"
    assert "SECRET" not in caught.value.message
    assert caught.value.details is None


def test_discovery_uses_beta_catalog_filters_gemini_names_sorts_and_closes(monkeypatch):
    client = FakeClient()
    client.models.list_result = [
        SimpleNamespace(name="publishers/google/models/text-embedding-005"),
        SimpleNamespace(name="publishers/google/models/gemini-z"),
        SimpleNamespace(name="publishers/google/models/gemini-a"),
        SimpleNamespace(name="publishers/google/models/gemini-a"),
    ]
    captured = {}

    def build_client(**kwargs):
        captured.update(kwargs)
        return client

    monkeypatch.setattr(adapter, "build_gemini_client", build_client)

    result = adapter.discover_gemini_models(project_id="p", location="eu")

    assert result == [
        "publishers/google/models/gemini-a",
        "publishers/google/models/gemini-z",
    ]
    assert captured["api_version"] == "v1beta1"
    assert client.close_calls == 1


def test_regional_discovery_merges_jurisdictional_catalog(monkeypatch):
    clients = {
        "europe-west2": FakeClient(),
        "eu": FakeClient(),
    }
    clients["europe-west2"].models.list_result = [
        SimpleNamespace(name="publishers/google/models/gemini-2.5-flash"),
    ]
    clients["eu"].models.list_result = [
        SimpleNamespace(name="publishers/google/models/gemini-3.5-flash"),
        SimpleNamespace(name="publishers/google/models/gemini-3.1-flash-lite"),
        SimpleNamespace(name="publishers/google/models/gemini-embedding-2"),
        SimpleNamespace(name="publishers/google/models/gemini-3.1-flash-image"),
    ]
    calls = []

    def build_client(**kwargs):
        calls.append(kwargs)
        return clients[kwargs["location"]]

    monkeypatch.setattr(adapter, "build_gemini_client", build_client)

    result = adapter.discover_gemini_models(
        project_id="clinical-prod",
        location="europe-west2",
    )

    assert result == [
        "publishers/google/models/gemini-2.5-flash",
        "publishers/google/models/gemini-3.1-flash-lite",
        "publishers/google/models/gemini-3.5-flash",
    ]
    assert [call["location"] for call in calls] == ["europe-west2", "eu"]
    assert all(call["api_version"] == "v1beta1" for call in calls)
    assert all(client.close_calls == 1 for client in clients.values())


def test_validation_uses_non_clinical_probe_and_closes(monkeypatch):
    client = FakeClient()
    _install_client(monkeypatch, client)

    result = adapter.validate_gemini_model(project_id="p", location="eu", model="gemini-test")

    assert result == 7
    assert client.models.count_calls == [
        {"model": "gemini-test", "contents": "OpenScribe connection validation."}
    ]
    assert client.close_calls == 1


def test_generation_maps_request_usage_finish_reason_and_closes(monkeypatch):
    client = FakeClient()
    _install_client(monkeypatch, client)

    result = adapter.generate_gemini_text(
        project_id="p",
        location="eu",
        credentials=None,
        request=_request(expect_json=True),
    )

    assert result.text == "generated"
    assert (result.input_tokens, result.output_tokens, result.total_tokens) == (11, 5, 16)
    assert result.finish_reason == "STOP"
    assert result.provider_duration_ms is None
    call = client.models.generate_calls[0]
    assert call["model"] == "publishers/google/models/gemini-test"
    assert call["contents"][0].role == "user"
    assert call["contents"][0].parts[0].text == "User"
    assert call["config"].system_instruction == "System"
    assert call["config"].temperature == 0.2
    assert call["config"].max_output_tokens == 1600
    assert call["config"].response_mime_type == "application/json"
    assert client.close_calls == 1


def test_generation_constrains_gemini_3_json_and_uses_explicit_schema(monkeypatch):
    client = FakeClient()
    _install_client(monkeypatch, client)
    response_schema = {
        "type": "object",
        "required": ["title", "content"],
        "properties": {
            "title": {"type": "string"},
            "content": {"type": "object"},
        },
    }

    adapter.generate_gemini_text(
        project_id="p",
        location="eu",
        credentials=None,
        request=_request(
            expect_json=True,
            response_json_schema=response_schema,
            model="publishers/google/models/gemini-3.5-flash",
        ),
    )

    config = client.models.generate_calls[0]["config"]
    assert config.response_json_schema == response_schema
    assert config.thinking_config.thinking_level.value == "MINIMAL"


def test_generation_rejects_max_tokens_before_json_parser(monkeypatch):
    client = FakeClient()
    client.models.generate_result = SimpleNamespace(
        text="{",
        usage_metadata=SimpleNamespace(
            prompt_token_count=11,
            candidates_token_count=1600,
            total_token_count=1611,
        ),
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(value="MAX_TOKENS"))],
    )
    _install_client(monkeypatch, client)

    with pytest.raises(AppError) as caught:
        adapter.generate_gemini_text(
            project_id="p",
            location="eu",
            credentials=None,
            request=_request(expect_json=True),
        )

    assert caught.value.code == "llm_generation_truncated"
    assert caught.value.details == {"provider_finish_reason": "MAX_TOKENS"}
    assert client.close_calls == 1


def test_output_cap_is_fixed_for_all_gemini_models_and_semantic_lengths():
    assert adapter.gemini_output_token_cap("gemini-3.5-flash", 800) == 30_000
    assert adapter.gemini_output_token_cap(
        "publishers/google/models/gemini-3.5-flash",
        3200,
    ) == 30_000
    assert adapter.gemini_output_token_cap("gemini-3.1-flash-lite", 1600) == 30_000
    assert adapter.gemini_output_token_cap("gemini-2.5-flash", 1600) == 30_000


@pytest.mark.parametrize("invalid_text", [None, "", "   "])
def test_generation_bad_response_is_controlled_and_closes(monkeypatch, invalid_text):
    client = FakeClient()
    client.models.generate_result = SimpleNamespace(text=invalid_text)
    _install_client(monkeypatch, client)

    with pytest.raises(AppError) as caught:
        adapter.generate_gemini_text(
            project_id="p", location="eu", credentials=None, request=_request()
        )

    assert caught.value.code == "llm_provider_bad_response"
    assert client.close_calls == 1


def test_generation_blocked_response_is_controlled_and_closes(monkeypatch):
    class BlockedResponse:
        @property
        def text(self):
            raise ValueError("provider echoed private content")

    client = FakeClient()
    client.models.generate_result = BlockedResponse()
    _install_client(monkeypatch, client)

    with pytest.raises(AppError) as caught:
        adapter.generate_gemini_text(
            project_id="p", location="eu", credentials=None, request=_request()
        )

    assert caught.value.code == "llm_provider_bad_response"
    assert client.close_calls == 1


def test_provider_failure_is_translated_and_client_closes(monkeypatch):
    client = FakeClient()
    client.models.generate_error = genai_errors.ClientError(
        429,
        {"error": {"message": "sensitive prompt echo"}},
    )
    _install_client(monkeypatch, client)

    with pytest.raises(AppError) as caught:
        adapter.generate_gemini_text(
            project_id="p", location="eu", credentials=None, request=_request()
        )

    assert caught.value.code == "llm_provider_rate_limited"
    assert caught.value.details == {"provider_http_status": 429}
    assert "sensitive" not in caught.value.message
    assert client.close_calls == 1


@pytest.mark.parametrize(
    ("error", "operation", "code"),
    [
        (google_auth_exceptions.DefaultCredentialsError("secret"), "generation", "llm_invalid_credential"),
        (genai_errors.ClientError(403, {"error": {"details": [{"reason": "SERVICE_DISABLED"}]}}), "model_discovery", "llm_provider_api_disabled"),
        (genai_errors.ClientError(403, {"error": {"message": "private"}}), "generation", "llm_permission_denied"),
        (genai_errors.ClientError(404, {"error": {}}), "model_discovery", "llm_model_discovery_unavailable"),
        (genai_errors.ClientError(400, {"error": {}}), "model_discovery", "llm_location_unavailable"),
        (genai_errors.ClientError(404, {"error": {}}), "model_validation", "llm_model_unavailable"),
        (genai_errors.ServerError(504, {"error": {}}), "generation", "llm_provider_timeout"),
        (ConnectionError("credential=SECRET"), "generation", "llm_provider_unreachable"),
        (ValueError("credential=SECRET"), "generation", "llm_generation_failed"),
    ],
)
def test_error_translation_is_deterministic_and_content_safe(error, operation, code):
    translated = adapter.translate_gemini_error(error, operation=operation)

    assert translated.code == code
    assert "SECRET" not in translated.message
    assert "private" not in translated.message


def test_request_snapshot_matches_gemini_shape_without_credentials():
    snapshot = adapter.gemini_request_snapshot(_request(expect_json=True))

    assert snapshot == {
        "model": "publishers/google/models/gemini-test",
        "contents": [{"role": "user", "parts": [{"text": "User"}]}],
        "config": {
            "system_instruction": "System",
            "temperature": 0.2,
            "max_output_tokens": 1600,
            "response_mime_type": "application/json",
        },
    }
    assert "credentials" not in snapshot


def test_structured_schema_survives_snapshot_while_plain_actions_stay_plain_text():
    template_config = StructuredTemplateConfig.model_validate(
        {
            "profile": "emis",
            "sections": [
                {
                    "section_key": "problem",
                    "section_label": "Problem",
                    "instruction": "Return synthetic text.",
                    "section_order": 1,
                },
                {
                    "section_key": "tasks",
                    "section_label": "Tasks",
                    "instruction": "Return synthetic tasks.",
                    "section_order": 2,
                },
            ],
        }
    )
    schema = _structured_note_response_json_schema(template_config)
    structured = _generation_request_snapshot(
        adapter_kind=LlmAdapterKind.gemini_enterprise,
        model="publishers/google/models/gemini-3.5-flash",
        user_id=uuid4(),
        system_message="System",
        user_message="Synthetic",
        response_json_schema=schema,
    )
    plain = _generation_request_snapshot(
        adapter_kind=LlmAdapterKind.gemini_enterprise,
        model="publishers/google/models/gemini-3.5-flash",
        user_id=uuid4(),
        system_message="System",
        user_message="Synthetic follow-up",
    )

    assert structured["config"]["response_json_schema"] == schema
    assert structured["config"]["max_output_tokens"] == 30_000
    assert set(schema["properties"]["content"]["properties"]) == {"problem", "tasks"}
    assert _gemini_request_from_snapshot(structured).response_json_schema == schema
    assert "response_mime_type" not in plain["config"]
    assert "response_json_schema" not in plain["config"]
    assert plain["config"]["max_output_tokens"] == 30_000
