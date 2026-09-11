from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class LlmGenerationRequest:
    model: str
    system_message: str
    user_message: str
    temperature: float
    max_output_tokens: int
    expect_json: bool
    response_json_schema: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class LlmGenerationResult:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    duration_ms: int
    provider_duration_ms: int | None
    finish_reason: str | None


@dataclass(frozen=True, slots=True)
class LlmProviderSnapshot:
    """Non-secret provider execution metadata suitable for encrypted storage."""

    llm_config_id: str
    provider_preset: str
    adapter_kind: str
    base_url: str
    model: str
    auth_mode: str
    provider_config: Mapping[str, str]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation without credentials or Vault references."""
        return {
            "llm_config_id": self.llm_config_id,
            "provider_preset": self.provider_preset,
            "adapter_kind": self.adapter_kind,
            "base_url": self.base_url,
            "model": self.model,
            "auth_mode": self.auth_mode,
            "provider_config": dict(self.provider_config),
        }
