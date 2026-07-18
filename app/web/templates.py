import os

from fastapi.templating import Jinja2Templates


templates = Jinja2Templates(directory=os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates"))


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
