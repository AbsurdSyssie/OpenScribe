"""Closed-schema, owner-gated Telehealth browser diagnostics.

This sink uses the same hardened rotating-file handler as Deepgram diagnostics.
It never propagates to stdout, and never accepts clinical text or raw errors.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.deepgram_diagnostics import _SafeRotatingHandler

_LOGGER = logging.getLogger("openscribe.telehealth_capture")
_LOGGER.propagate = False
_LOGGER.setLevel(logging.INFO)
_LOGGER.addHandler(logging.NullHandler())
_LOCK = threading.RLock()
_HANDLER: logging.Handler | None = None
_PATH: str | None = None
_ID = re.compile(r"^[0-9a-f]{32}$")

# Only enumerated browser events, fields and finite values may enter log storage.
EVENTS = frozenset({
    "picker_requested", "picker_selected", "picker_rejected", "window_acknowledged",
    "microphone_requested", "microphone_ready", "capture_started", "capture_stopping",
    "capture_stopped", "capture_failed", "capture_state", "capture_interrupted",
    "track_ended", "track_muted", "source_changed", "worklet_ready",
    "vad_loading", "vad_ready", "vad_unavailable", "vad_speech_confirmed",
    "journal_check", "journal_failed", "window_prepared", "upload_started",
    "upload_accepted", "upload_failed", "seal_started", "seal_completed",
    "seal_failed", "transcribe_started", "transcribe_accepted", "transcribe_failed",
})
ENUM_FIELDS: dict[str, frozenset[str]] = {
    "channel": frozenset({"patient", "clinician", "none"}),
    "surface": frozenset({"browser", "window", "monitor", "unknown"}),
    "state": frozenset({
        "idle", "starting", "recording", "running", "stopping", "stopped",
        "failed", "recovery_required", "incomplete", "sealed", "transcribing",
        "discarded",
    }),
    "code": frozenset({
        "none", "user_cancelled", "permission_denied", "window_declined",
        "unsupported_surface", "missing_audio", "missing_video", "source_ended",
        "source_muted", "audio_clock_interrupted", "audio_backpressure",
        "audio_processor_failed", "audio_flush_failed", "browser_capture_failed",
        "vad_unavailable", "vad_initialization_failed", "journal_unavailable",
        "upload_failed", "seal_failed", "transcribe_failed",
    }),
}
MAX_PART = 1_000_000


class _TelehealthRotatingHandler(_SafeRotatingHandler):
    """Share secure path and no-follow rotation with existing diagnostics."""

    def __init__(self, filename: str):
        super().__init__(filename)
        # The base handler includes a Deepgram-only filter by default.
        self.filters.clear()
        self.addFilter(lambda record: getattr(record, "telehealth_diagnostic", False) is True)

    def handleError(self, record: logging.LogRecord) -> None:
        # Never put filesystem paths or exception text on stdout/stderr.
        disable_file_sink()


def disable_file_sink() -> None:
    global _HANDLER, _PATH
    with _LOCK:
        previous, _HANDLER = _HANDLER, None
        _PATH = None
        if previous is not None:
            _LOGGER.removeHandler(previous)
            try:
                previous.close()
            except Exception:
                pass


def configure_file_sink(value: str | None) -> bool:
    """Fail closed to no sink when logging storage is absent or unsafe."""
    global _HANDLER, _PATH
    path = os.path.abspath(value) if value else None
    with _LOCK:
        if path == _PATH and _HANDLER in _LOGGER.handlers:
            return True
        disable_file_sink()
        if path is None:
            return False
        try:
            handler = _TelehealthRotatingHandler(path)
            _LOGGER.addHandler(handler)
            _HANDLER, _PATH = handler, path
            return True
        except Exception:
            # Diagnostic setup must not block normal clinical recording.
            return False


def safe_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Reject unknown event schemas instead of stringifying arbitrary input."""
    if not isinstance(event, dict) or event.get("event") not in EVENTS:
        return None
    if set(event) - ({"event", "part"} | set(ENUM_FIELDS)):
        return None
    clean: dict[str, Any] = {"event": event["event"]}
    for name, allowed in ENUM_FIELDS.items():
        if name in event:
            if event[name] not in allowed:
                return None
            clean[name] = event[name]
    if "part" in event:
        value = event["part"]
        if type(value) is not int or not 0 <= value <= MAX_PART:
            return None
        clean["part"] = value
    return clean


def emit_many(diagnostic_id: str, events: list[dict[str, Any]]) -> int:
    """Server timestamps and emits only strictly validated metadata."""
    if not isinstance(diagnostic_id, str) or not _ID.fullmatch(diagnostic_id):
        return 0
    accepted = 0
    for raw in events[:20]:
        event = safe_event(raw)
        if event is None:
            continue
        record = {
            "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "diagnostic_id": diagnostic_id,
            **event,
        }
        try:
            _LOGGER.info(
                json.dumps(record, sort_keys=True, separators=(",", ":")),
                extra={"telehealth_diagnostic": True},
            )
            accepted += 1
        except Exception:
            # Diagnostics are best-effort; the recording is never interrupted.
            pass
    return accepted


def reset_for_tests() -> None:
    disable_file_sink()
