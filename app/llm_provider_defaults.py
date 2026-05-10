from urllib.parse import urlparse


OPENAI_CHAT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_BEDROCK_CHAT_REGION = "eu-west-2"
OLLAMA_CHAT_BASE_URL = "http://localhost:11434"


def normalize_bedrock_region(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError("Bedrock region is required")
    if not normalized.replace("-", "").isalnum():
        raise ValueError("Bedrock region must contain only letters, numbers, and hyphens")
    return normalized


def bedrock_chat_base_url(region: str) -> str:
    return f"https://bedrock-mantle.{normalize_bedrock_region(region)}.api.aws/v1"


def bedrock_region_from_base_url(base_url: str) -> str | None:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    prefix = "bedrock-mantle."
    suffix = ".api.aws"
    if host.startswith(prefix) and host.endswith(suffix):
        candidate = host[len(prefix) : -len(suffix)]
        if candidate:
            return candidate
    return None
