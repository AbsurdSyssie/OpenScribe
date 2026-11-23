"""Count request bytes before Starlette can spool multipart data to disk."""
from __future__ import annotations

import re

from starlette.formparsers import MultiPartException
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette._utils import get_route_path

from app.errors import error_response

from app.services.audio import LIVE_CHUNK_MAX_UPLOAD_BYTES, WHOLE_FILE_MAX_UPLOAD_BYTES
from app.services.quick_action_io import QUICK_ACTION_BUNDLE_MAX_BYTES
from app.services.smart_phrase_io import SMART_PHRASE_BUNDLE_MAX_BYTES
from app.services.templates import TEMPLATE_BUNDLE_MAX_BYTES

# Reserve room for boundaries, filenames, and the supported metadata fields.
MULTIPART_OVERHEAD_BYTES = 1024 * 1024
DEFAULT_MULTIPART_MAX_BYTES = 2 * 1024 * 1024


def request_body_limit(path: str) -> int | None:
    path = path.rstrip("/")
    bundles = {
        "templates": TEMPLATE_BUNDLE_MAX_BYTES,
        "quick-actions": QUICK_ACTION_BUNDLE_MAX_BYTES,
        "smart-phrases": SMART_PHRASE_BUNDLE_MAX_BYTES,
    }
    for resource, limit in bundles.items():
        if path in {f"/api/v1/{resource}/import", f"/api/v1/{resource}/import/preflight"}:
            return limit + MULTIPART_OVERHEAD_BYTES
    if path in {"/home/transcripts/upload", "/transcribe/upload", "/transcribe/dictation/upload"}:
        return WHOLE_FILE_MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD_BYTES
    if re.fullmatch(r"/api/v1/transcripts/[^/]+/(?:audio-file|(?:post-consultation-dictation|quick-action-context)/(?:preview-audio-file|audio-file))", path):
        return WHOLE_FILE_MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD_BYTES
    if re.fullmatch(r"/api/v1/transcripts/[^/]+/(?:audio-chunks|telehealth/captures/[^/]+/audio-parts|deepgram-stream/[^/]+/retry)", path):
        return LIVE_CHUNK_MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD_BYTES
    return None


class RequestBodyTooLarge(MultiPartException):
    """A parser-recognized exception ensures partially spooled files close."""


class RequestSizeLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = request_body_limit(get_route_path(scope))
        if limit is None:
            is_multipart = any(
                name.lower() == b"content-type" and value.lower().split(b";", 1)[0].strip() == b"multipart/form-data"
                for name, value in scope.get("headers", [])
            )
            if not is_multipart:
                await self.app(scope, receive, send)
                return
            limit = DEFAULT_MULTIPART_MAX_BYTES
        response = error_response(413, "request_too_large", "Request body exceeds the permitted size")
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    continue
                if declared > limit:
                    await response(scope, receive, send)
                    return
        total = 0
        exceeded = False
        replaced = False

        async def bounded_receive():
            nonlocal total, exceeded
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > limit:
                    exceeded = True
                    raise RequestBodyTooLarge("Request body exceeds the permitted size")
            return message

        async def bounded_send(message):
            nonlocal replaced
            # Request.form converts MultiPartException to HTTP 400. Preserve the
            # size response after the parser has closed all partial spool files.
            if exceeded:
                if message["type"] == "http.response.start" and not replaced:
                    replaced = True
                    await response(scope, receive, send)
                return
            await send(message)

        try:
            await self.app(scope, bounded_receive, bounded_send)
        except RequestBodyTooLarge:
            if not replaced:
                await response(scope, receive, send)
