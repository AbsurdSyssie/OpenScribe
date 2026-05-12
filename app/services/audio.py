import io
import os
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

from app.errors import AppError


WHOLE_FILE_MAX_UPLOAD_BYTES = int(os.getenv("WHOLE_FILE_MAX_UPLOAD_BYTES", str(200 * 1024 * 1024)))
WHOLE_FILE_MAX_DURATION_SECONDS = int(os.getenv("WHOLE_FILE_MAX_DURATION_SECONDS", str(4 * 60 * 60)))
AUDIO_FFPROBE_TIMEOUT_SECONDS = float(os.getenv("AUDIO_FFPROBE_TIMEOUT_SECONDS", "15"))
AUDIO_FFMPEG_TIMEOUT_SECONDS = float(os.getenv("AUDIO_FFMPEG_TIMEOUT_SECONDS", "1800"))


@dataclass(slots=True)
class NormalizedAudio:
    filename: str
    content_type: str
    data: bytes


def enforce_whole_file_upload_size(*, audio_bytes: bytes) -> None:
    if not audio_bytes:
        raise AppError(422, "business_rule_violation", "Audio chunk is required", {"field": "audio"})
    if len(audio_bytes) > WHOLE_FILE_MAX_UPLOAD_BYTES:
        raise AppError(
            413,
            "payload_too_large",
            "Audio file exceeds the current maximum upload size",
            {"field": "audio", "max_bytes": WHOLE_FILE_MAX_UPLOAD_BYTES},
        )


def normalized_wav_duration_seconds(*, audio_bytes: bytes) -> float:
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav_file:
            frame_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
    except (wave.Error, EOFError) as exc:
        raise AppError(502, "audio_normalization_failed", "Normalized audio metadata could not be read") from exc
    if frame_rate <= 0:
        raise AppError(502, "audio_normalization_failed", "Normalized audio metadata could not be read")
    return frame_count / frame_rate


def probe_audio_duration_seconds(*, audio_bytes: bytes, source_filename: str) -> float:
    if not audio_bytes:
        raise AppError(422, "business_rule_violation", "Audio chunk is required", {"field": "audio"})

    source_suffix = Path(source_filename or "audio.bin").suffix or ".bin"
    with tempfile.NamedTemporaryFile(suffix=source_suffix, delete=False, dir="/tmp") as source_file:
        source_file.write(audio_bytes)
        source_path = source_file.name

    try:
        try:
            completed = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    source_path,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=AUDIO_FFPROBE_TIMEOUT_SECONDS,
            )
        except OSError as exc:
            raise AppError(502, "audio_duration_probe_failed", "ffprobe is unavailable for audio duration inspection") from exc
        except subprocess.TimeoutExpired as exc:
            raise AppError(502, "audio_duration_probe_failed", "Audio duration inspection timed out") from exc

        if completed.returncode != 0:
            raise AppError(502, "audio_duration_probe_failed", "Audio duration could not be inspected")

        try:
            duration_seconds = float((completed.stdout or "").strip())
        except ValueError as exc:
            raise AppError(502, "audio_duration_probe_failed", "Audio duration could not be inspected") from exc
        if duration_seconds <= 0:
            raise AppError(502, "audio_duration_probe_failed", "Audio duration could not be inspected")
        return duration_seconds
    finally:
        try:
            os.unlink(source_path)
        except FileNotFoundError:
            pass


def inspect_audio_duration_seconds(*, audio_bytes: bytes, source_filename: str) -> float:
    try:
        return probe_audio_duration_seconds(audio_bytes=audio_bytes, source_filename=source_filename)
    except AppError as probe_exc:
        if probe_exc.code != "audio_duration_probe_failed":
            raise
    normalized_audio = normalize_audio_to_wav_16k_mono(audio_bytes=audio_bytes, source_filename=source_filename)
    return normalized_wav_duration_seconds(audio_bytes=normalized_audio.data)


def enforce_whole_file_duration_limit(*, audio_bytes: bytes) -> None:
    duration_seconds = normalized_wav_duration_seconds(audio_bytes=audio_bytes)
    if duration_seconds > WHOLE_FILE_MAX_DURATION_SECONDS:
        raise AppError(
            422,
            "business_rule_violation",
            "Audio duration exceeds the current maximum",
            {
                "field": "audio",
                "max_duration_seconds": WHOLE_FILE_MAX_DURATION_SECONDS,
                "duration_seconds": round(duration_seconds, 3),
            },
        )


def normalize_audio_to_wav_16k_mono(*, audio_bytes: bytes, source_filename: str) -> NormalizedAudio:
    if not audio_bytes:
        raise AppError(422, "business_rule_violation", "Audio chunk is required", {"field": "audio"})

    source_suffix = Path(source_filename or "chunk.bin").suffix or ".bin"
    with tempfile.NamedTemporaryFile(suffix=source_suffix, delete=False, dir="/tmp") as source_file:
        source_file.write(audio_bytes)
        source_path = source_file.name

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir="/tmp") as output_file:
        output_path = output_file.name

    try:
        try:
            completed = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    source_path,
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    output_path,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=AUDIO_FFMPEG_TIMEOUT_SECONDS,
            )
        except OSError as exc:
            raise AppError(502, "audio_normalization_failed", "ffmpeg is unavailable for audio normalization") from exc
        except subprocess.TimeoutExpired as exc:
            raise AppError(502, "audio_normalization_failed", "Audio normalization timed out") from exc

        if completed.returncode != 0:
            raise AppError(502, "audio_normalization_failed", "Audio normalization failed")

        with open(output_path, "rb") as normalized_file:
            normalized_bytes = normalized_file.read()
    finally:
        for path in (source_path, output_path):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    return NormalizedAudio(
        filename=f"{Path(source_filename or 'chunk').stem or 'chunk'}.wav",
        content_type="audio/wav",
        data=normalized_bytes,
    )
