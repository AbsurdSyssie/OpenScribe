#!/usr/bin/env python3

from __future__ import annotations

import os
import sys
from pathlib import Path

from openai import OpenAI


def load_root_env() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def main() -> int:
    load_root_env()

    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    if not api_key:
        print("OPENAI_API_KEY is not set in the environment or repo-root .env", file=sys.stderr)
        return 1

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        models = client.models.list()
    except Exception as exc:
        print(f"OpenAI model lookup failed: {exc}", file=sys.stderr)
        return 2

    model_ids = sorted(
        model.id
        for model in getattr(models, "data", [])
        if getattr(model, "id", None)
    )
    transcription_models = [
        model_id for model_id in model_ids if "transcribe" in model_id or "whisper" in model_id
    ]

    print(f"Base URL: {base_url}")
    print(f"Total models returned: {len(model_ids)}")
    print("Transcription-related models:")
    for model_id in transcription_models:
        print(f"- {model_id}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
