from types import SimpleNamespace

import httpx
import pytest

from app.errors import AppError
from app.services import deidentification, llm, provider_inspection, redaction, stt, templates


class FakeStreamResponse:
    def __init__(self, *, chunks=(), lines=(), headers=None):
        self._chunks = chunks
        self._lines = lines
        self.headers = headers or {}
        self.status_code = 200
        self.iter_bytes_called = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    def iter_bytes(self):
        self.iter_bytes_called = True
        return iter(self._chunks)

    def iter_lines(self):
        return iter(self._lines)

    def iter_raw(self, *, chunk_size=None):
        return iter(self._chunks)


class FakeUnconsumedErrorStream(FakeStreamResponse):
    def __init__(self):
        super().__init__(chunks=[b'{"error":{"code":"invalid_request_error"}}'])
        self.status_code = 400
        self.closed = False
        self._body_consumed = False

    def __exit__(self, exc_type, exc, tb):
        self.closed = True
        return False

    def raise_for_status(self):
        request = httpx.Request("POST", "https://provider.example/transcribe")
        raise httpx.HTTPStatusError("bad request", request=request, response=self)

    def iter_bytes(self):
        if self.closed or self._body_consumed:
            raise httpx.StreamClosed()
        self.iter_bytes_called = True
        self._body_consumed = True
        return iter(self._chunks)


def test_openapi_inspection_rejects_oversized_content_length_before_reading(monkeypatch):
    response = FakeStreamResponse(headers={"content-length": str(provider_inspection.OPENAPI_DOCUMENT_MAX_RESPONSE_BYTES + 1)})
    monkeypatch.setattr(provider_inspection.httpx, "stream", lambda *args, **kwargs: response)

    with pytest.raises(AppError) as exc_info:
        provider_inspection.fetch_openapi_document(
            base_url="https://provider.example",
            candidate_paths=["/openapi.json"],
            bearer_token=None,
        )

    assert exc_info.value.code == "business_rule_violation"
    assert response.iter_bytes_called is False


def test_openapi_inspection_rejects_oversized_streamed_body(monkeypatch):
    monkeypatch.setattr(provider_inspection, "OPENAPI_DOCUMENT_MAX_RESPONSE_BYTES", 4)
    monkeypatch.setattr(
        provider_inspection.httpx,
        "stream",
        lambda *args, **kwargs: FakeStreamResponse(chunks=[b"{}", b"{}", b"{}"]),
    )

    with pytest.raises(AppError) as exc_info:
        provider_inspection.fetch_openapi_document(
            base_url="https://provider.example",
            candidate_paths=["/openapi.json"],
            bearer_token=None,
        )

    assert exc_info.value.code == "business_rule_violation"


def test_model_discovery_rejects_oversized_streamed_body(monkeypatch):
    monkeypatch.setattr(llm, "MODEL_DISCOVERY_MAX_RESPONSE_BYTES", 4)
    monkeypatch.setattr(
        llm.httpx,
        "stream",
        lambda *args, **kwargs: FakeStreamResponse(chunks=[b"{}", b"{}", b"{}"]),
    )

    with pytest.raises(AppError) as exc_info:
        llm._list_ollama_chat_models(base_url="http://localhost:11434", bearer_token=None)

    assert exc_info.value.code == "llm_inspection_failed"


@pytest.mark.parametrize(
    ("headers", "chunks"),
    [
        ({"content-length": str(deidentification.DEIDENTIFICATION_OPENAPI_MAX_RESPONSE_BYTES + 1)}, []),
        ({}, [b"{}", b"{}"]),
    ],
)
def test_deidentification_openapi_rejects_oversized_response(monkeypatch, headers, chunks):
    monkeypatch.setattr(deidentification, "DEIDENTIFICATION_OPENAPI_MAX_RESPONSE_BYTES", 3)
    response = FakeStreamResponse(headers=headers, chunks=chunks)
    monkeypatch.setattr(deidentification.httpx, "stream", lambda *args, **kwargs: response)

    with pytest.raises(AppError) as exc_info:
        deidentification._fetch_openapi_document(
            deidentification.DeidentificationProviderInspectRequest(
                label="Test provider",
                base_url="https://provider.example",
                detect_path="/openapi.json",
            )
        )

    assert exc_info.value.code == "redaction_provider_invalid_response"
    assert exc_info.value.details == {"provider_error_code": "response_too_large"}
    if headers:
        assert response.iter_bytes_called is False


@pytest.mark.parametrize(
    ("headers", "chunks"),
    [
        ({"content-length": str(stt.STT_MODEL_DISCOVERY_MAX_RESPONSE_BYTES + 1)}, []),
        ({}, [b"{}", b"{}"]),
    ],
)
def test_stt_model_discovery_rejects_oversized_response(monkeypatch, headers, chunks):
    monkeypatch.setattr(stt, "STT_MODEL_DISCOVERY_MAX_RESPONSE_BYTES", 3)
    response = FakeStreamResponse(headers=headers, chunks=chunks)
    monkeypatch.setattr(stt.httpx, "stream", lambda *args, **kwargs: response)

    with pytest.raises(AppError) as exc_info:
        stt._list_deepgram_stt_models(api_key="test-key", base_url="https://provider.example")

    assert exc_info.value.code == "stt_inspection_failed"
    assert exc_info.value.details == {"provider_error_code": "response_too_large"}
    if headers:
        assert response.iter_bytes_called is False


def test_ollama_generation_rejects_oversized_streamed_fragment(monkeypatch):
    line = '{"message":{"content":"note"}}'
    monkeypatch.setattr(templates, "OLLAMA_STREAM_MAX_RESPONSE_BYTES", len(line.encode("utf-8")) - 1)
    monkeypatch.setattr(templates.httpx, "stream", lambda *args, **kwargs: FakeStreamResponse(chunks=[line.encode("utf-8")]))

    with pytest.raises(AppError) as exc_info:
        templates._generate_freeform_output_ollama(
            base_url="http://localhost:11434",
            bearer_token=None,
            request_body={"model": "test", "stream": True, "messages": []},
        )

    assert exc_info.value.code == "llm_provider_bad_response"
    assert exc_info.value.details == {"provider_error_code": "response_too_large"}


def test_ollama_generation_stops_endless_fragment_stream(monkeypatch):
    monkeypatch.setattr(templates, "OLLAMA_STREAM_MAX_FRAGMENTS", 2)
    monkeypatch.setattr(templates.httpx, "stream", lambda *args, **kwargs: FakeStreamResponse(chunks=[b"{}\n{}\n{}\n"]))

    with pytest.raises(AppError) as exc_info:
        templates._generate_freeform_output_ollama(
            base_url="http://localhost:11434",
            bearer_token=None,
            request_body={"model": "test", "stream": True, "messages": []},
        )

    assert exc_info.value.code == "llm_provider_bad_response"
    assert exc_info.value.details == {"provider_error_code": "response_too_large"}


def test_ollama_generation_rejects_oversized_unterminated_line(monkeypatch):
    oversized_line = b"{" + b"x" * templates.OLLAMA_STREAM_MAX_RESPONSE_BYTES
    monkeypatch.setattr(templates.httpx, "stream", lambda *args, **kwargs: FakeStreamResponse(chunks=[oversized_line]))

    with pytest.raises(AppError) as exc_info:
        templates._generate_freeform_output_ollama(
            base_url="http://localhost:11434",
            bearer_token=None,
            request_body={"model": "test", "stream": True, "messages": []},
        )

    assert exc_info.value.code == "llm_provider_bad_response"
    assert exc_info.value.details == {"provider_error_code": "response_too_large"}


@pytest.mark.parametrize(
    ("headers", "chunks"),
    [
        ({"content-length": "4"}, []),
        ({}, [b"{}", b"{}"]),
    ],
)
def test_generic_redaction_rejects_oversized_response(monkeypatch, headers, chunks):
    monkeypatch.setattr(redaction, "REDACTION_PROVIDER_RESPONSE_MAX_BYTES", 3)
    response = FakeStreamResponse(headers=headers, chunks=chunks)
    monkeypatch.setattr(redaction.httpx, "stream", lambda *args, **kwargs: response)
    provider = SimpleNamespace(
        base_url="https://provider.example",
        detect_path="/detect",
        extra_body_json={},
        request_text_field="text",
        request_language_field=None,
        extra_headers_json={},
        auth_mode=SimpleNamespace(value="none"),
    )

    with pytest.raises(AppError) as exc_info:
        redaction._detect_with_generic_rest(
            None,
            provider=provider,
            text="Patient Jane Doe",
            language="en",
            score_threshold=0.5,
            entities=None,
        )

    assert exc_info.value.code == "redaction_provider_invalid_response"
    assert exc_info.value.details == {"provider_error_code": "response_too_large"}
    if headers:
        assert response.iter_bytes_called is False


@pytest.mark.parametrize(
    ("headers", "chunks"),
    [
        ({"content-length": "4"}, []),
        ({}, [b"{}", b"{}"]),
    ],
)
@pytest.mark.parametrize("provider", ["generic", "deepgram", "elevenlabs"])
def test_stt_transcription_rejects_oversized_response(monkeypatch, headers, chunks, provider):
    monkeypatch.setattr(stt, "STT_TRANSCRIPTION_RESPONSE_MAX_BYTES", 3)
    response = FakeStreamResponse(headers=headers, chunks=chunks)
    monkeypatch.setattr(stt.httpx, "stream", lambda *args, **kwargs: response)

    if provider == "generic":
        transcribe = lambda: stt._transcribe_via_http(
            base_url="https://provider.example",
            transcribe_path="/transcribe",
            file_field_name="file",
            response_text_path="text",
            extra_form_fields_json={},
            bearer_token="test-token",
            model_name="test-model",
            model_field_name="model",
            language="en",
            language_field_name="language",
            audio_bytes=b"audio",
            filename="audio.wav",
            content_type="audio/wav",
        )
    elif provider == "deepgram":
        transcribe = lambda: stt._transcribe_via_deepgram(
            url="https://api.deepgram.com/v1/listen",
            bearer_token="test-token",
            audio_bytes=b"audio",
            content_type="audio/wav",
            model_name="nova-3",
            language="en",
            extra_query_params={},
            response_text_path="results.channels.0.alternatives.0.transcript",
        )
    else:
        transcribe = lambda: stt._transcribe_via_elevenlabs_speech_to_text(
            base_url="https://api.elevenlabs.io",
            api_key="test-token",
            audio_bytes=b"audio",
            filename="audio.wav",
            content_type="audio/wav",
            model_name="scribe_v2",
            language="en",
        )

    with pytest.raises(AppError) as exc_info:
        transcribe()

    assert exc_info.value.code == "stt_response_invalid"
    assert exc_info.value.details == {"provider_error_code": "response_too_large"}
    if headers:
        assert response.iter_bytes_called is False


@pytest.mark.parametrize("provider", ["generic", "deepgram", "elevenlabs"])
def test_stt_transcription_translates_unconsumed_streaming_client_error(monkeypatch, provider):
    response = FakeUnconsumedErrorStream()
    monkeypatch.setattr(stt.httpx, "stream", lambda *args, **kwargs: response)

    if provider == "generic":
        transcribe = lambda: stt._transcribe_via_http(
            base_url="https://provider.example",
            transcribe_path="/transcribe",
            file_field_name="file",
            response_text_path="text",
            extra_form_fields_json={},
            bearer_token="test-token",
            model_name="test-model",
            model_field_name="model",
            language="en",
            language_field_name="language",
            audio_bytes=b"audio",
            filename="audio.wav",
            content_type="audio/wav",
        )
    elif provider == "deepgram":
        transcribe = lambda: stt._transcribe_via_deepgram(
            url="https://api.deepgram.com/v1/listen",
            bearer_token="test-token",
            audio_bytes=b"audio",
            content_type="audio/wav",
            model_name="nova-3",
            language="en",
            extra_query_params={},
            response_text_path="results.channels.0.alternatives.0.transcript",
        )
    else:
        transcribe = lambda: stt._transcribe_via_elevenlabs_speech_to_text(
            base_url="https://api.elevenlabs.io",
            api_key="test-token",
            audio_bytes=b"audio",
            filename="audio.wav",
            content_type="audio/wav",
            model_name="scribe_v2",
            language="en",
        )

    with pytest.raises(AppError) as exc_info:
        transcribe()

    assert exc_info.value.status_code == 502
    assert exc_info.value.code == "stt_request_failed"
    assert exc_info.value.details == {
        "status_code": 400,
        "provider_status_code": 400,
        "provider_error_code": "invalid_request_error",
    }
    assert response.closed is True
    assert response.iter_bytes_called is True
