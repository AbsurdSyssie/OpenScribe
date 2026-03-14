import re
import unicodedata


_whitespace_re = re.compile(r"\s+")


def normalize_team_name_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = _whitespace_re.sub(" ", normalized.strip())
    return normalized.casefold()


def normalize_email(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return normalized.strip().casefold()
