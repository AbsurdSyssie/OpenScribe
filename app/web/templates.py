import os
import subprocess
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.templating import Jinja2Templates


templates = Jinja2Templates(directory=os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates"))


DEFAULT_SOURCE_CODE_URL = "https://github.com/AbsurdSyssie/OpenScribe"
DEFAULT_APP_RELEASE = "unversioned build"


def source_code_url() -> str:
    """Return a safe source-offer URL for the running deployment."""
    configured = os.getenv("APP_SOURCE_CODE_URL", "").strip()
    if not configured:
        return DEFAULT_SOURCE_CODE_URL
    parsed = urlsplit(configured)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return configured
    return DEFAULT_SOURCE_CODE_URL


@lru_cache(maxsize=1)
def _git_release() -> str:
    repository_root = Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return DEFAULT_APP_RELEASE
    return result.stdout.strip() or DEFAULT_APP_RELEASE


def app_release() -> str:
    """Identify the running build without claiming a release that is unknown."""
    return os.getenv("APP_RELEASE", "").strip() or _git_release()


def _format_quota_units(value: int, resource: str) -> str:
    amount = int(value)
    if resource == "tokens":
        return f"{amount:,} tokens"
    hours, remainder = divmod(amount, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


templates.env.filters["quota_units"] = _format_quota_units
templates.env.globals["app_release"] = app_release
templates.env.globals["source_code_url"] = source_code_url
