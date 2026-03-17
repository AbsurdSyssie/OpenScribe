import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.errors import AppError


@dataclass(slots=True)
class NormalizedAudio:
    filename: str
    content_type: str
    data: bytes


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
            )
        except OSError as exc:
            raise AppError(502, "audio_normalization_failed", "ffmpeg is unavailable for audio normalization") from exc

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
