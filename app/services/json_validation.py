"""Small JSON parsing helpers for provider-output validation boundaries."""
from __future__ import annotations

from typing import Any


class DuplicateJsonKeyError(ValueError):
    """Duplicate-key marker that deliberately retains no key or value."""


def reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting duplicate keys at every depth."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError from None
        result[key] = value
    return result
