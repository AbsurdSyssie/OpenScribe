import httpx
import pytest

from app.errors import AppError
from app.services import llm


class _ModelDiscoveryResponse:
    headers = {}

    def __init__(self, *, status_code: int = 200, payload: bytes = b"{}"):
        self.status_code = status_code
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://provider.example/models")
            raise httpx.HTTPStatusError("model discovery failed", request=request, response=self)

    def iter_bytes(self):
        return iter([self._payload])


@pytest.mark.parametrize(
    ("list_models", "expected_message"),
    [
        (llm._list_mistral_chat_models, "Could not load available Mistral chat models"),
        (llm._list_together_chat_models, "Could not load available Together AI chat models"),
    ],
)
def test_provider_specific_model_discovery_status_errors_are_preserved(monkeypatch, list_models, expected_message):
    monkeypatch.setattr(llm.httpx, "stream", lambda *args, **kwargs: _ModelDiscoveryResponse(status_code=502))

    with pytest.raises(AppError) as exc_info:
        list_models(api_key="test-key", base_url="https://provider.example/v1")

    assert exc_info.value.status_code == 502
    assert exc_info.value.code == "llm_inspection_failed"
    assert exc_info.value.message == expected_message
    assert exc_info.value.details == {"provider_status": 502}


def test_model_discovery_rejects_invalid_credentials_with_provider_status(monkeypatch):
    monkeypatch.setattr(llm.httpx, "stream", lambda *args, **kwargs: _ModelDiscoveryResponse(status_code=403))

    with pytest.raises(AppError) as exc_info:
        llm._list_openai_compatible_models(api_key="test-key", base_url="https://provider.example/v1")

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "llm_invalid_credential"
    assert exc_info.value.message == "The API key was rejected by the provider."
    assert exc_info.value.details == {"provider_status": 403}
