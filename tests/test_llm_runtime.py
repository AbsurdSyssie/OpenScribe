from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.errors import AppError
from app.models import LlmAdapterKind, LlmAuthMode
from app.services.llm_adapters import runtime
from app.services.llm_adapters.types import LlmProviderSnapshot
from app.services import templates as template_service


_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.mark.parametrize(
    ("adapter", "expected"),
    [
        (
            LlmAdapterKind.openai_chat,
            {
                "model": "test-model",
                "temperature": 0.2,
                "max_completion_tokens": 1600,
                "user": str(_USER_ID),
                "messages": [
                    {"role": "system", "content": "System"},
                    {"role": "user", "content": "User"},
                ],
            },
        ),
        (
            LlmAdapterKind.bedrock_chat,
            {
                "model": "test-model",
                "temperature": 0.2,
                "max_completion_tokens": 1600,
                "user": str(_USER_ID),
                "messages": [
                    {"role": "system", "content": "System"},
                    {"role": "user", "content": "User"},
                ],
            },
        ),
        (
            LlmAdapterKind.ollama_chat,
            {
                "model": "test-model",
                "stream": True,
                "messages": [
                    {"role": "system", "content": "System"},
                    {"role": "user", "content": "User"},
                ],
            },
        ),
    ],
)
def test_generation_request_snapshot_matches_existing_adapter_shapes(adapter, expected):
    assert runtime.generation_request_snapshot(
        adapter_kind=adapter,
        model="test-model",
        user_id=_USER_ID,
        system_message="System",
        user_message="User",
    ) == expected


def test_generation_request_snapshot_matches_gemini_shape():
    assert runtime.generation_request_snapshot(
        adapter_kind=LlmAdapterKind.gemini_enterprise,
        model="publishers/google/models/gemini-test",
        user_id=_USER_ID,
        system_message="System",
        user_message="User",
        output_token_cap=800,
        response_json_schema={"type": "object"},
    ) == {
        "model": "publishers/google/models/gemini-test",
        "contents": [{"role": "user", "parts": [{"text": "User"}]}],
        "config": {
            "system_instruction": "System",
            "temperature": 0.2,
            "max_output_tokens": 30_000,
            "response_mime_type": "application/json",
            "response_json_schema": {"type": "object"},
        },
    }


def test_provider_snapshot_allowlists_config_and_omits_secrets():
    config = SimpleNamespace(
        id=_USER_ID,
        provider_preset="gemini_enterprise",
        adapter_kind=LlmAdapterKind.gemini_enterprise,
        base_url="https://europe-west2-aiplatform.googleapis.com",
        model_name="publishers/google/models/gemini-test",
        auth_mode=LlmAuthMode.google_service_account,
        vault_secret_ref="vault:must-not-leak",
        provider_config_json={
            "project_id": "project-1",
            "location": "europe-west2",
            "api_version": "v1",
            "capacity_mode": "shared",
            "credentials": "must-not-leak",
            "legacy_option": "must-not-leak",
        },
    )

    payload = runtime.build_provider_snapshot(config=config).to_dict()

    assert payload == {
        "llm_config_id": str(_USER_ID),
        "provider_preset": "gemini_enterprise",
        "adapter_kind": "gemini_enterprise",
        "base_url": "https://europe-west2-aiplatform.googleapis.com",
        "model": "publishers/google/models/gemini-test",
        "auth_mode": "google_service_account",
        "provider_config": {
            "project_id": "project-1",
            "location": "europe-west2",
            "api_version": "v1",
            "capacity_mode": "shared",
        },
    }
    assert "vault" not in json.dumps(payload).lower()
    assert "credential" not in json.dumps(payload).lower()
    assert json.loads(json.dumps(payload)) == payload


def test_provider_snapshot_rejects_unsafe_base_url():
    config = SimpleNamespace(
        id=_USER_ID,
        provider_preset="custom_openai_compatible",
        adapter_kind=LlmAdapterKind.openai_chat,
        base_url="http://metadata.google.internal",
        model_name="test-model",
        auth_mode=LlmAuthMode.none,
        provider_config_json={},
    )

    with pytest.raises(AppError, match="blocked"):
        runtime.build_provider_snapshot(config=config)


def test_provider_snapshot_rejects_missing_model():
    config = SimpleNamespace(
        id=_USER_ID,
        provider_preset="custom_openai_compatible",
        adapter_kind=LlmAdapterKind.openai_chat,
        base_url="https://provider.example",
        model_name="",
        auth_mode=LlmAuthMode.none,
        provider_config_json={},
    )

    with pytest.raises(AppError) as exc_info:
        runtime.build_provider_snapshot(config=config)

    assert exc_info.value.code == "business_rule_violation"


def test_provider_snapshot_config_validation_can_bind_a_resolved_model_override():
    config = SimpleNamespace(
        id=_USER_ID,
        provider_preset="custom_openai_compatible",
        adapter_kind=LlmAdapterKind.openai_chat,
        base_url="https://provider.example",
        model_name="team-default",
        auth_mode=LlmAuthMode.none,
        provider_config_json={},
    )
    override = runtime.build_provider_snapshot(config=config, model="user-override").to_dict()

    assert runtime.validate_provider_snapshot_for_config(
        override, config=config, expected_model="user-override"
    ) == override
    with pytest.raises(AppError) as exc_info:
        runtime.validate_provider_snapshot_for_config(override, config=config)
    assert exc_info.value.code == "llm_provider_snapshot_config_mismatch"


def test_invoke_llm_dispatches_from_snapshot(monkeypatch):
    snapshot = LlmProviderSnapshot(
        llm_config_id=str(_USER_ID), provider_preset="ollama", adapter_kind="ollama_chat",
        base_url="http://localhost:11434", model="llama", auth_mode="none", provider_config={},
    )
    calls = []
    monkeypatch.setattr(runtime, "invoke_ollama", lambda **kwargs: (calls.append(kwargs) or ("note", runtime.generation_usage(total_tokens=3))))

    output, usage = runtime.invoke_llm(snapshot=snapshot, credential=None, request_body={"model": "llama"})

    assert output == "note"
    assert usage["total_tokens"] == 3
    assert calls == [{"base_url": "http://localhost:11434", "bearer_token": None, "request_body": {"model": "llama"}}]


def test_invoke_llm_rejects_invalid_snapshot_adapter():
    snapshot = LlmProviderSnapshot(
        llm_config_id=str(_USER_ID), provider_preset="custom", adapter_kind="unsafe",
        base_url="https://provider.example", model="test", auth_mode="none", provider_config={},
    )

    with pytest.raises(AppError) as exc_info:
        runtime.invoke_llm(snapshot=snapshot, credential=None, request_body={})

    assert exc_info.value.code == "business_rule_violation"


def test_openai_content_extraction_and_usage_normalization():
    assert runtime.openai_message_content_text([
        {"type": "text", "text": {"value": "first"}},
        {"type": "refusal", "text": "ignored"},
        {"type": "output_text", "text": " second"},
    ]) == "first second"
    assert runtime.generation_usage(input_tokens=2, output_tokens=3, duration_ms=7) == {
        "input_tokens": 2,
        "output_tokens": 3,
        "total_tokens": 5,
        "duration_ms": 7,
        "provider_duration_ms": None,
    }


@pytest.mark.parametrize(
    ("failure", "expected_code", "expected_details"),
    [
        ("connection", "llm_provider_unreachable", {"provider_error_code": "connection_error"}),
        ("status", "llm_generation_failed", {"provider_http_status": 429, "provider_error_code": "rate_limit_exceeded"}),
    ],
)
def test_templates_openai_transport_seam_preserves_safe_runtime_errors(monkeypatch, failure, expected_code, expected_details):
    """The legacy templates seam passes its SDK exception classes to runtime."""
    sensitive = "patient Jane Doe secret=synthetic"

    class FakeTimeout(Exception):
        pass

    class FakeConnection(Exception):
        pass

    class FakeStatus(Exception):
        def __init__(self):
            self.status_code = 429
            self.body = {"error": {"code": "rate_limit_exceeded", "message": sensitive}}

    class FailingClient:
        def __init__(self, **_kwargs):
            if failure == "connection":
                raise FakeConnection(sensitive)
            raise FakeStatus()

    monkeypatch.setattr(template_service, "OpenAI", FailingClient)
    monkeypatch.setattr(template_service, "APITimeoutError", FakeTimeout)
    monkeypatch.setattr(template_service, "APIConnectionError", FakeConnection)
    monkeypatch.setattr(template_service, "APIStatusError", FakeStatus)

    with pytest.raises(AppError) as caught:
        template_service._generate_freeform_output_openai(
            api_key="synthetic", base_url="https://provider.example", request_body={"model": "synthetic", "messages": []}
        )

    assert caught.value.code == expected_code
    assert caught.value.details == expected_details
    assert sensitive not in str(caught.value)


def test_templates_gemini_seams_preserve_snapshot_invocation_and_usage_parsing(monkeypatch):
    schema = {"type": "object", "properties": {"content": {"type": "object"}}}
    snapshot = template_service._generation_request_snapshot(
        adapter_kind=LlmAdapterKind.gemini_enterprise,
        model="publishers/google/models/gemini-3.5-flash",
        user_id=_USER_ID,
        system_message="System",
        user_message="User",
        output_token_cap=800,
        response_json_schema=schema,
    )
    captured = {}

    def fake_generate(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            text="generated", input_tokens=4, output_tokens=6, total_tokens=None,
            duration_ms=12, provider_duration_ms=9,
        )

    monkeypatch.setattr(runtime, "generate_gemini_text", fake_generate)
    text, usage = template_service._generate_freeform_output_gemini(
        config=SimpleNamespace(),
        provider_config={"project_id": "project", "location": "eu", "capacity_mode": "dedicated"},
        credential="credential-object",
        request_body=snapshot,
    )

    assert text == "generated"
    assert usage == {
        "input_tokens": 4,
        "output_tokens": 6,
        "total_tokens": 10,
        "duration_ms": 12,
        "provider_duration_ms": 9,
    }
    request = captured["request"]
    assert (captured["project_id"], captured["location"], captured["capacity_mode"]) == ("project", "eu", "dedicated")
    assert request.model == snapshot["model"]
    assert request.user_message == "User"
    assert request.max_output_tokens == 30_000
    assert request.response_json_schema == schema
    assert template_service._request_output_token_cap(snapshot) == 30_000
