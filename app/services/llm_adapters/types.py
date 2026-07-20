from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LlmGenerationRequest:
    model: str
    system_message: str
    user_message: str
    temperature: float
    max_output_tokens: int
    expect_json: bool
    response_schema: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class LlmGenerationResult:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    duration_ms: int
    provider_duration_ms: int | None
    finish_reason: str | None
