_STT_OPTIONAL_SENTINELS = {
    "none",
    "null",
    "undefined",
    "auto",
    "default",
    "provider_default",
}


def normalize_optional_stt_text(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = str(value).strip()
    if not trimmed:
        return None
    if trimmed.lower() in _STT_OPTIONAL_SENTINELS:
        return None
    return trimmed


def normalize_stt_language(value: str | None) -> str | None:
    return normalize_optional_stt_text(value)
