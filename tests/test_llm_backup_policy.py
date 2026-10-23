from __future__ import annotations

from uuid import uuid4

import pytest

from app.errors import AppError
from app.models import LlmAdapterKind
from app.services.llm_adapters.runtime import generation_request_snapshot
from app.services.llm_adapters.types import LlmProviderSnapshot
from app.services.llm_backup_policy import (
    can_failover,
    backup_is_distinct,
    prepare_backup_request,
    tag_output_validation_failure,
    tag_provider_failure,
)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AppError(502, "llm_generation_failed", "safe", {"provider_http_status": 503, "backup_failure_class": "provider_failure"}), True),
        (AppError(504, "llm_provider_timeout", "safe", {"provider_error_code": "timeout", "backup_failure_class": "provider_failure"}), True),
        (AppError(429, "llm_provider_rate_limited", "safe", {"provider_http_status": 429, "backup_failure_class": "temporary_capacity_exhausted"}), True),
        (AppError(429, "llm_provider_rate_limited", "safe", {"provider_http_status": 429, "backup_failure_class": "provider_quota_blocked"}), False),
        (AppError(429, "llm_provider_rate_limited", "safe", {"provider_http_status": 429}), False),
        (AppError(429, "quota_exceeded", "safe", {"backup_failure_class": "provider_failure"}), False),
        (AppError(429, "quota_disabled", "safe", {"backup_failure_class": "provider_failure"}), False),
        (AppError(409, "llm_attempt_inactive", "safe", {"backup_failure_class": "provider_failure"}), False),
        (AppError(500, "storage_unavailable", "safe", {"provider_http_status": 503, "backup_failure_class": "provider_failure"}), False),
    ],
)
def test_failover_requires_explicit_safe_failure_class(error, expected):
    assert can_failover(error) is expected


def test_provider_failure_tagging_retains_only_safe_category_after_transport_retry_controls():
    temporary = AppError(
        429,
        "llm_provider_rate_limited",
        "safe",
        {"provider_http_status": 429, "temporary_capacity": True, "retry_after_seconds": 10},
    )
    tag_provider_failure(temporary)
    assert can_failover(temporary)

    hard_quota = AppError(429, "llm_provider_rate_limited", "safe", {"provider_http_status": 429})
    tag_provider_failure(hard_quota)
    assert not can_failover(hard_quota)

    output = tag_output_validation_failure(AppError(502, "llm_generation_invalid_json", "safe"))
    assert can_failover(output)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (LlmAdapterKind.openai_chat, LlmAdapterKind.gemini_enterprise),
        (LlmAdapterKind.gemini_enterprise, LlmAdapterKind.ollama_chat),
        (LlmAdapterKind.ollama_chat, LlmAdapterKind.openai_chat),
        (LlmAdapterKind.bedrock_chat, LlmAdapterKind.gemini_enterprise),
    ],
)
def test_backup_request_remaps_frozen_messages_model_cap_temperature_and_schema(source, target):
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
    user_id = uuid4()
    source_request = generation_request_snapshot(
        adapter_kind=source,
        model="primary-model",
        user_id=user_id,
        system_message="Synthetic system prompt",
        user_message="Synthetic redacted request",
        output_token_cap=321,
        response_json_schema=schema,
        temperature=0.35,
    )
    provider = LlmProviderSnapshot(
        llm_config_id=str(uuid4()), provider_preset="custom", adapter_kind=target.value,
        base_url="https://provider.example", model="backup-model", auth_mode="bearer_token",
        provider_config={},
    )

    backup = prepare_backup_request(provider, source_request, user_id, response_json_schema=schema)

    assert backup["model"] == "backup-model"
    assert "Synthetic system prompt" in str(backup)
    assert "Synthetic redacted request" in str(backup)
    if target is LlmAdapterKind.openai_chat:
        assert backup["temperature"] == (0.2 if source is LlmAdapterKind.ollama_chat else 0.35)
        assert backup["max_completion_tokens"] == 321
    elif target is LlmAdapterKind.ollama_chat:
        assert backup["options"]["num_predict"] == 30_000
        assert "temperature" not in backup["options"]
    else:
        assert backup["config"]["temperature"] == 0.35
        assert backup["config"]["response_json_schema"] == schema


def test_backup_request_uses_explicit_schema_when_primary_adapter_does_not_encode_it():
    user_id = uuid4()
    source = generation_request_snapshot(
        adapter_kind=LlmAdapterKind.openai_chat,
        model="primary-model",
        user_id=user_id,
        system_message="Synthetic system",
        user_message="Synthetic redacted source",
        output_token_cap=222,
        temperature=0.4,
    )
    provider = LlmProviderSnapshot(
        llm_config_id=str(uuid4()), provider_preset="custom", adapter_kind="gemini_enterprise",
        base_url="https://provider.example", model="backup-model", auth_mode="service_account",
        provider_config={"project_id": "synthetic", "location": "europe-west2"},
    )
    schema = {"type": "object", "required": ["result"]}

    backup = prepare_backup_request(provider, source, user_id, response_json_schema=schema)

    assert backup["model"] == "backup-model"
    assert backup["config"]["max_output_tokens"] == 30_000
    assert backup["config"]["temperature"] == 0.4
    assert backup["config"]["response_json_schema"] == schema


@pytest.mark.parametrize("bad", [{}, {"model": "x", "messages": []}, {"model": "x", "messages": [{"role": "user", "content": "only"}]}])
def test_backup_request_rejects_unrecognized_source_snapshots(bad):
    provider = LlmProviderSnapshot(
        llm_config_id=str(uuid4()), provider_preset="custom", adapter_kind="openai_chat",
        base_url="https://provider.example", model="backup-model", auth_mode="bearer_token",
        provider_config={},
    )
    with pytest.raises(AppError) as caught:
        prepare_backup_request(provider, bad, uuid4())
    assert caught.value.code == "llm_request_invalid"


def test_backup_identical_provider_model_is_skipped_but_same_provider_other_model_is_distinct():
    config_id = uuid4()
    same_pair = LlmProviderSnapshot(
        llm_config_id=str(config_id), provider_preset="custom", adapter_kind="openai_chat",
        base_url="https://provider.example", model="same-model", auth_mode="bearer_token", provider_config={},
    )
    different_model = LlmProviderSnapshot(
        llm_config_id=str(config_id), provider_preset="custom", adapter_kind="openai_chat",
        base_url="https://provider.example", model="other-model", auth_mode="bearer_token", provider_config={},
    )
    assert not backup_is_distinct(primary_config_id=config_id, primary_model="same-model", backup_provider=same_pair)
    assert backup_is_distinct(primary_config_id=config_id, primary_model="same-model", backup_provider=different_model)
