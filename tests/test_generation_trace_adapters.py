"""The trace sees synthetic model text before whitespace/finish validation."""
from types import SimpleNamespace
import pytest
from app.errors import AppError
from app.services import generation_trace
from app.services.llm_adapters import runtime, gemini_enterprise
from app.services.llm_adapters.types import LlmGenerationRequest


def test_openai_truncated_response_is_captured_untrimmed(monkeypatch):
    captured = []
    monkeypatch.setattr(generation_trace, "raw_model_text", captured.append)
    monkeypatch.setattr(generation_trace, "capture_active", lambda: True)
    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="  synthetic\n"), finish_reason="length")], usage=None)))
        def close(self):
            pass
    with pytest.raises(AppError) as caught:
        runtime._invoke_openai_compatible_once(api_key="test", base_url="https://provider.example", request_body={}, client_factory=Client)
    assert caught.value.code == "llm_generation_truncated"
    assert captured == ["  synthetic\n"]


@pytest.mark.parametrize("terminal", [False, True])
def test_ollama_partial_stream_is_captured_before_failure(monkeypatch, terminal):
    captured = []
    monkeypatch.setattr(generation_trace, "raw_model_text", captured.append)
    monkeypatch.setattr(generation_trace, "capture_active", lambda: True)
    class Response:
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_raw(self, *, chunk_size):
            if terminal:
                yield b'{"message":{"content":"  synthetic\\n"},"done":true,"done_reason":"length"}\n'
            else:
                yield b'{"message":{"content":"  synthetic\\n"},"done":false}\n'
    with pytest.raises(AppError):
        runtime._invoke_ollama_once(base_url="http://localhost:11434", bearer_token=None, request_body={}, stream_factory=lambda *a, **k: Response())
    assert captured == ["  synthetic\n"]


def test_gemini_truncated_response_is_captured_untrimmed(monkeypatch):
    captured = []
    monkeypatch.setattr(generation_trace, "raw_model_text", captured.append)
    monkeypatch.setattr(generation_trace, "capture_active", lambda: True)
    response = SimpleNamespace(text="  synthetic\n", candidates=[SimpleNamespace(finish_reason=SimpleNamespace(value="MAX_TOKENS"))])
    client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kwargs: response), close=lambda: None)
    monkeypatch.setattr(gemini_enterprise, "build_gemini_client", lambda **kwargs: client)
    request = LlmGenerationRequest(model="gemini-test", system_message="test", user_message="test", temperature=0.2,
                                   max_output_tokens=100, expect_json=False, response_json_schema=None)
    with pytest.raises(AppError) as caught:
        gemini_enterprise.generate_gemini_text(project_id="test", location="eu", credentials=None, capacity_mode="auto", request=request)
    assert caught.value.code == "llm_generation_truncated"
    assert captured == ["  synthetic\n"]
