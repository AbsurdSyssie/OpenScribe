"""Regression coverage for streamed request limits and upload cleanup."""
from __future__ import annotations

import asyncio
import builtins
from pathlib import Path

import pytest
from starlette.requests import Request

from app import request_limits
from app.request_limits import RequestSizeLimitMiddleware, request_body_limit


def _asgi_request(*, path: str, body_parts: list[bytes], headers: list[tuple[bytes, bytes]], app):
    messages = [{"type": "http.request", "body": part, "more_body": i < len(body_parts) - 1}
                for i, part in enumerate(body_parts)]
    received = []
    sent = []

    async def receive():
        received.append(True)
        return messages.pop(0) if messages else {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": headers, "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    asyncio.run(RequestSizeLimitMiddleware(app)(scope, receive, send))
    return sent, received


def _responding_app(status: int = 200):
    async def app(scope, receive, send):
        while True:
            message = await receive()
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})
    return app


def _status(messages):
    return next(m["status"] for m in messages if m["type"] == "http.response.start")


def test_upload_routes_have_expected_whole_file_and_chunk_limits():
    from app.services.audio import LIVE_CHUNK_MAX_UPLOAD_BYTES, WHOLE_FILE_MAX_UPLOAD_BYTES
    from app.services.quick_action_io import QUICK_ACTION_BUNDLE_MAX_BYTES
    from app.services.smart_phrase_io import SMART_PHRASE_BUNDLE_MAX_BYTES
    from app.services.templates import TEMPLATE_BUNDLE_MAX_BYTES

    whole = WHOLE_FILE_MAX_UPLOAD_BYTES + request_limits.MULTIPART_OVERHEAD_BYTES
    chunk = LIVE_CHUNK_MAX_UPLOAD_BYTES + request_limits.MULTIPART_OVERHEAD_BYTES
    for path in (
        "/home/transcripts/upload", "/transcribe/upload", "/transcribe/dictation/upload",
        "/api/v1/transcripts/abc/audio-file",
        "/api/v1/transcripts/abc/post-consultation-dictation/audio-file",
        "/api/v1/transcripts/abc/post-consultation-dictation/preview-audio-file",
        "/api/v1/transcripts/abc/quick-action-context/audio-file",
        "/api/v1/transcripts/abc/quick-action-context/preview-audio-file",
    ):
        assert request_body_limit(path) == whole
    for path in (
        "/api/v1/transcripts/abc/audio-chunks",
        "/api/v1/transcripts/abc/telehealth/captures/capture/audio-parts",
        "/api/v1/transcripts/abc/deepgram-stream/session/retry",
    ):
        assert request_body_limit(path) == chunk
    bundle_limits = {
        "templates": TEMPLATE_BUNDLE_MAX_BYTES,
        "quick-actions": QUICK_ACTION_BUNDLE_MAX_BYTES,
        "smart-phrases": SMART_PHRASE_BUNDLE_MAX_BYTES,
    }
    for resource, bundle_limit in bundle_limits.items():
        for suffix in ("import", "import/preflight"):
            assert request_body_limit(f"/api/v1/{resource}/{suffix}") == (
                bundle_limit + request_limits.MULTIPART_OVERHEAD_BYTES
            )


def test_streamed_multipart_limit_rejects_chunked_and_misleading_length():
    content_type = (b"content-type", b"multipart/form-data; boundary=synthetic")
    for headers in ([content_type], [content_type, (b"content-length", b"1")]):
        messages, _received = _asgi_request(
            path="/ordinary/multipart", body_parts=[b"x" * (request_limits.DEFAULT_MULTIPART_MAX_BYTES + 1)],
            headers=headers, app=_responding_app(),
        )
        assert _status(messages) == 413


def test_declared_multipart_length_over_limit_is_rejected_without_reading_body():
    messages, received = _asgi_request(
        path="/ordinary/multipart", body_parts=[b"unused"],
        headers=[
            (b"content-type", b"multipart/form-data; boundary=synthetic"),
            (b"content-length", str(request_limits.DEFAULT_MULTIPART_MAX_BYTES + 1).encode()),
        ], app=_responding_app(),
    )
    assert _status(messages) == 413
    assert not received


def test_multipart_exact_limit_is_allowed_and_ordinary_route_gets_fallback():
    limit = request_limits.DEFAULT_MULTIPART_MAX_BYTES
    assert request_body_limit("/ordinary/multipart") is None
    messages, _received = _asgi_request(
        path="/ordinary/multipart", body_parts=[b"x" * limit],
        headers=[
            (b"content-type", b"multipart/form-data; boundary=synthetic"),
            (b"content-length", str(limit).encode()),
        ], app=_responding_app(),
    )
    assert _status(messages) == 200


def test_unrelated_json_request_is_not_capped_by_multipart_fallback():
    messages, _received = _asgi_request(
        path="/ordinary/json", body_parts=[b"{" + b"x" * (request_limits.DEFAULT_MULTIPART_MAX_BYTES + 5)],
        headers=[(b"content-type", b"application/json")], app=_responding_app(),
    )
    assert _status(messages) == 200


def test_rejected_multipart_closes_partially_spooled_upload(monkeypatch):
    import starlette.formparsers

    monkeypatch.setattr(request_limits, "DEFAULT_MULTIPART_MAX_BYTES", 256)
    monkeypatch.setattr(starlette.formparsers.MultiPartParser, "spool_max_size", 32)
    opened = []
    original = starlette.formparsers.SpooledTemporaryFile

    def tracked_spool(*args, **kwargs):
        spool = original(*args, **kwargs)
        opened.append(spool)
        return spool

    monkeypatch.setattr(starlette.formparsers, "SpooledTemporaryFile", tracked_spool)

    async def parse_form(scope, receive, send):
        try:
            await Request(scope, receive).form()
            status = 200
        except Exception:
            status = 400
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    head = b'--synthetic\r\nContent-Disposition: form-data; name="file"; filename="audio.bin"\r\nContent-Type: application/octet-stream\r\n\r\n'
    body_parts = [head + b"a" * 40, b"b" * 180 + b"\r\n--synthetic--\r\n"]
    messages, _received = _asgi_request(
        path="/ordinary/multipart", body_parts=body_parts,
        headers=[(b"content-type", b"multipart/form-data; boundary=synthetic")], app=parse_form,
    )
    assert _status(messages) == 413
    assert opened
    assert all(getattr(spool, "_rolled", False) for spool in opened)
    assert all(spool.closed for spool in opened)


@pytest.mark.parametrize("operation", ["probe", "normalize"])
def test_audio_temp_directory_is_removed_when_source_write_fails(monkeypatch, tmp_path, operation):
    from app.services import audio

    directories = []
    real_temp_directory = audio.tempfile.TemporaryDirectory

    class TrackingTemporaryDirectory:
        def __init__(self, *args, **kwargs):
            kwargs["dir"] = str(tmp_path)
            self._inner = real_temp_directory(*args, **kwargs)
            self.name = self._inner.name
            directories.append(Path(self.name))

        def __enter__(self):
            return self._inner.__enter__()

        def __exit__(self, *args):
            return self._inner.__exit__(*args)

    monkeypatch.setattr(audio.tempfile, "TemporaryDirectory", TrackingTemporaryDirectory)
    real_open = builtins.open

    def fail_audio_source(path, mode="r", *args, **kwargs):
        if isinstance(path, (str, Path)) and str(path).startswith(str(tmp_path / "openscribe-audio-")) and "w" in mode:
            raise OSError("synthetic disk write failure")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(audio, "open", fail_audio_source, raising=False)
    with pytest.raises(OSError, match="synthetic disk write failure"):
        if operation == "probe":
            audio.probe_audio_duration_seconds(audio_bytes=b"synthetic", source_filename="clip.bin")
        else:
            audio.normalize_audio_to_wav_16k_mono(audio_bytes=b"synthetic", source_filename="clip.bin")
    assert directories and all(not directory.exists() for directory in directories)
