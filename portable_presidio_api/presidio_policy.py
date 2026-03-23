#!/usr/bin/env python3
"""Shared policy filters for Presidio false-positive reduction."""

from __future__ import annotations

import re
from typing import Any

PAIN_SCORE_PATTERN = re.compile(r"\b(?:10|[1-9])\/10\b")
SPEAKER_LABEL_PATTERN = re.compile(r"^speaker[_-]?\d+$", re.IGNORECASE)
UK_POSTCODE_PATTERN = re.compile(r"\b[A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2}\b", re.IGNORECASE)

NUMERIC_DATE_PATTERN = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|\d{4}[/-]\d{1,2}[/-]\d{1,2})\b"
)
MONTH_DATE_PATTERN = re.compile(
    r"\b(?:jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|"
    r"aug|august|sep|sept|september|oct|october|nov|november|dec|december)\b",
    re.IGNORECASE,
)
YEAR_PATTERN = re.compile(r"\b(?:19|20)\d{2}\b")
DURATION_PATTERN = re.compile(
    r"\b(?:a|an|about|around|approximately|roughly|few|several|couple(?:\s+of)?)?\s*"
    r"(?:minute|hour|day|week|month|year)s?\b",
    re.IGNORECASE,
)
FREQUENCY_PATTERN = re.compile(
    r"\b(?:daily|weekly|monthly|yearly|annually|every\s+\w+\s+(?:day|week|month|year)s?)\b",
    re.IGNORECASE,
)
RELATIVE_TEMPORAL_PATTERN = re.compile(
    r"\b(?:today|yesterday|tomorrow|tonight|day\s+before|last\s+night|"
    r"this\s+(?:morning|afternoon|evening|night|week|month|year)|"
    r"last\s+(?:morning|afternoon|evening|night|week|month|year)|"
    r"next\s+(?:morning|afternoon|evening|night|week|month|year))\b",
    re.IGNORECASE,
)
SPELLED_NUMBER_TIME_PATTERN = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
    r"(?:point\s+\w+|\w+)\b",
    re.IGNORECASE,
)

UNIT_TOKEN_PATTERN = re.compile(
    r"\b(?:flat|apt|apartment|suite|unit|room|rm|house|building|block)\b",
    re.IGNORECASE,
)
ADDRESS_NUMBER_PATTERN = re.compile(r"\b\d{1,5}[A-Za-z]?\b")
ADDRESS_SUFFIX_PATTERN = re.compile(
    r"\b(?:road|rd|street|st|avenue|ave|lane|ln|close|way|drive|dr|"
    r"crescent|cres|place|pl|court|ct|terrace|ter|square|sq|grove|row)\b",
    re.IGNORECASE,
)
TITLE_CASE_TOKEN_PATTERN = re.compile(r"\b[A-Z][a-z]{2,}\b")

ADDRESS_LEADING_STOPWORDS = {
    "go",
    "just",
    "no",
    "the",
    "which",
    "very",
    "all",
    "is",
    "are",
    "was",
    "were",
    "be",
    "being",
    "been",
}

FIELD_LABEL_PATTERN = re.compile(
    r"^[A-Z][A-Za-z]+(?:\s+[A-Za-z][A-Za-z]+){0,7}$"
)


def _is_speaker_label(value: str) -> bool:
    return bool(SPEAKER_LABEL_PATTERN.fullmatch(value.strip()))


def _is_concrete_datetime(value: str) -> bool:
    text = value.strip()
    if NUMERIC_DATE_PATTERN.search(text):
        return True
    if MONTH_DATE_PATTERN.search(text) and (re.search(r"\d", text) or YEAR_PATTERN.search(text)):
        return True
    return False


def _is_non_identifying_datetime(value: str) -> bool:
    text = value.strip()
    low = text.lower()
    if RELATIVE_TEMPORAL_PATTERN.search(low):
        return True
    if DURATION_PATTERN.search(low):
        return True
    if FREQUENCY_PATTERN.search(low):
        return True
    if SPELLED_NUMBER_TIME_PATTERN.search(low):
        return True
    return not _is_concrete_datetime(text)


def _looks_conversational_address(value: str) -> bool:
    low = value.strip().lower()
    if "all the way" in low or "very close" in low:
        return True
    tokens = re.findall(r"[a-z]+", low)
    if not tokens:
        return True
    if tokens[0] in ADDRESS_LEADING_STOPWORDS and len(tokens) <= 5:
        return True
    return False


def _has_address_evidence(value: str) -> bool:
    if UK_POSTCODE_PATTERN.search(value):
        return True
    if UNIT_TOKEN_PATTERN.search(value):
        return True
    if ADDRESS_NUMBER_PATTERN.search(value):
        return True
    if "," in value and TITLE_CASE_TOKEN_PATTERN.search(value):
        return True
    if ADDRESS_SUFFIX_PATTERN.search(value) and TITLE_CASE_TOKEN_PATTERN.search(value):
        return True
    return False


def _looks_like_field_label(text: str, line_start: int, line_end: int) -> bool:
    line = text[line_start:line_end].strip()
    if not line or not FIELD_LABEL_PATTERN.fullmatch(line):
        return False
    next_char_index = line_end
    while next_char_index < len(text) and text[next_char_index] in " \t":
        next_char_index += 1
    return next_char_index < len(text) and text[next_char_index] == ":"


def normalize_span_bounds(
    text: str,
    start: int,
    end: int,
    entity_type: str,
) -> tuple[int, int] | None:
    if start >= end:
        return None

    value = text[start:end]

    if entity_type == "PERSON" and "\n" in value:
        offset = 0
        for line in value.splitlines(keepends=True):
            line_text = line.rstrip("\r\n")
            line_start = start + offset
            line_end = line_start + len(line_text)
            if offset > 0 and _looks_like_field_label(text, line_start, line_end):
                trimmed_end = line_start - 1
                if trimmed_end > start:
                    end = trimmed_end
                break
            offset += len(line)

    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1

    if start >= end:
        return None
    return start, end


def should_keep_detection(text: str, result: Any) -> bool:
    value = text[result.start:result.end]
    entity = str(result.entity_type)

    if not value.strip():
        return False
    if PAIN_SCORE_PATTERN.fullmatch(value):
        return False
    if _is_speaker_label(value):
        return False
    if entity == "DATE_TIME" and _is_non_identifying_datetime(value):
        return False
    if entity == "STREET_ADDRESS_PHRASE":
        if _looks_conversational_address(value):
            return False
        if not _has_address_evidence(value):
            return False
    return True


def filter_analyzer_results(text: str, raw_results: list[Any]) -> list[Any]:
    return [result for result in raw_results if should_keep_detection(text, result)]
