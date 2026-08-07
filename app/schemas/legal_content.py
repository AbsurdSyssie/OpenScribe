import json
import re
from datetime import date
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import LegalDocumentKind


MAX_LEGAL_BLOCKS = 1000
MAX_LEGAL_BLOCK_JSON_BYTES = 64 * 1024
MAX_LEGAL_INLINE_RUNS = 200
MAX_LEGAL_TABLE_COLUMNS = 10
MAX_LEGAL_TABLE_ROWS = 100
_CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HTML_RE = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")
_MARKDOWN_RE = re.compile(r"(^|\n)\s{0,3}(#{1,6}\s|[-+*]\s|>\s)|`|\*\*|__|\[[^\]]+\]\(")
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _plain_text(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if _CONTROL_CHARACTER_RE.search(normalized):
        raise ValueError(f"{field_name} contains a control character")
    if _HTML_RE.search(normalized):
        raise ValueError(f"{field_name} must not contain HTML")
    if _MARKDOWN_RE.search(normalized):
        raise ValueError(f"{field_name} must not contain Markdown")
    return normalized


class LegalInlineRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text", "italic", "bold", "bold_italic"]
    text: str = Field(min_length=1, max_length=5000)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if _CONTROL_CHARACTER_RE.search(value):
            raise ValueError("Inline text contains a control character")
        if _HTML_RE.search(value):
            raise ValueError("Inline text must not contain HTML")
        return value

    @model_validator(mode="after")
    def validate_styled_boundaries(self):
        if self.type != "text" and self.text != self.text.strip():
            raise ValueError("Styled inline text must not start or end with whitespace")
        return self


class LegalInlineLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["link"]
    text: str = Field(min_length=1, max_length=500)
    url: str = Field(min_length=1, max_length=2048)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _plain_text(value, field_name="Link text")

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        normalized = value.strip()
        if _CONTROL_CHARACTER_RE.search(normalized) or any(character.isspace() for character in normalized):
            raise ValueError("Inline link URL must not contain whitespace or control characters")
        parsed = urlsplit(normalized)
        if parsed.scheme.lower() == "mailto":
            if parsed.netloc or parsed.query or parsed.fragment or not _EMAIL_RE.fullmatch(parsed.path):
                raise ValueError("Inline email links must contain one plain email address")
            return f"mailto:{parsed.path}"
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("Inline link URL must be an absolute HTTPS URL or plain mailto address")
        return normalized


LegalInlineElement = Annotated[LegalInlineRun | LegalInlineLink, Field(discriminator="type")]
LegalInlineValue = str | list[LegalInlineElement]


def _inline_value_text(value: LegalInlineValue) -> str:
    return value if isinstance(value, str) else "".join(run.text for run in value)


def _validate_inline_value(
    value: LegalInlineValue,
    *,
    field_name: str,
    max_length: int,
    allow_empty: bool = False,
) -> LegalInlineValue:
    if isinstance(value, str):
        normalized = _plain_text(value, field_name=field_name)
        if not allow_empty and not normalized:
            raise ValueError(f"{field_name} must not be empty")
        if len(normalized) > max_length:
            raise ValueError(f"{field_name} must contain at most {max_length} characters")
        return normalized
    if not value or len(value) > MAX_LEGAL_INLINE_RUNS:
        raise ValueError(f"{field_name} must contain between 1 and {MAX_LEGAL_INLINE_RUNS} inline runs")
    combined = _inline_value_text(value)
    if (not allow_empty and not combined.strip()) or combined != combined.strip():
        raise ValueError(f"{field_name} must contain visible text without outer whitespace")
    if len(combined) > max_length:
        raise ValueError(f"{field_name} must contain at most {max_length} characters")
    return value


class LegalHeadingBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["heading"]
    text: LegalInlineValue

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: LegalInlineValue) -> LegalInlineValue:
        return _validate_inline_value(value, field_name="Heading", max_length=200)


class LegalParagraphBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["paragraph"]
    text: LegalInlineValue

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: LegalInlineValue) -> LegalInlineValue:
        return _validate_inline_value(value, field_name="Paragraph", max_length=5000)


class LegalBulletListBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["bullet_list"]
    items: list[LegalInlineValue] = Field(min_length=1, max_length=50)

    @field_validator("items")
    @classmethod
    def validate_items(cls, values: list[LegalInlineValue]) -> list[LegalInlineValue]:
        return [
            _validate_inline_value(value, field_name="Bullet item", max_length=1000)
            for value in values
        ]


class LegalTableBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["table"]
    headers: list[LegalInlineValue] = Field(min_length=1, max_length=MAX_LEGAL_TABLE_COLUMNS)
    rows: list[list[LegalInlineValue]] = Field(min_length=1, max_length=MAX_LEGAL_TABLE_ROWS)

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, values: list[LegalInlineValue]) -> list[LegalInlineValue]:
        clean = [
            _validate_inline_value(value, field_name="Table heading", max_length=500, allow_empty=True)
            for value in values
        ]
        if any("\n" in _inline_value_text(value) for value in clean):
            raise ValueError("Table headings must not contain line breaks")
        return clean

    @field_validator("rows")
    @classmethod
    def validate_rows(cls, rows: list[list[LegalInlineValue]]) -> list[list[LegalInlineValue]]:
        clean_rows: list[list[LegalInlineValue]] = []
        for row in rows:
            if len(row) > MAX_LEGAL_TABLE_COLUMNS:
                raise ValueError(f"Table rows must contain at most {MAX_LEGAL_TABLE_COLUMNS} cells")
            clean_row: list[LegalInlineValue] = []
            for value in row:
                clean = _validate_inline_value(value, field_name="Table cell", max_length=1000, allow_empty=True)
                if "\n" in _inline_value_text(clean):
                    raise ValueError("Table cells must not contain line breaks")
                clean_row.append(clean)
            clean_rows.append(clean_row)
        return clean_rows

    @model_validator(mode="after")
    def validate_dimensions(self):
        if any(len(row) != len(self.headers) for row in self.rows):
            raise ValueError("Every table row must contain the same number of cells as the heading row")
        return self


class LegalLabelledHttpsLinkBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["labelled_https_link"]
    label: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2048)

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        return _plain_text(value, field_name="Link label")

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        normalized = value.strip()
        if _CONTROL_CHARACTER_RE.search(normalized) or any(character.isspace() for character in normalized):
            raise ValueError("Link URL must not contain whitespace or control characters")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("Link URL must be an absolute HTTPS URL without credentials")
        return normalized


LegalContentBlock = Annotated[
    LegalHeadingBlock
    | LegalParagraphBlock
    | LegalBulletListBlock
    | LegalTableBlock
    | LegalLabelledHttpsLinkBlock,
    Field(discriminator="type"),
]


class LegalDocumentContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blocks: list[LegalContentBlock] = Field(min_length=1, max_length=MAX_LEGAL_BLOCKS)

    @model_validator(mode="after")
    def validate_serialized_size(self):
        encoded = json.dumps(self.model_dump(mode="json"), ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_LEGAL_BLOCK_JSON_BYTES:
            raise ValueError("Legal document content exceeds the 64 KiB limit")
        return self


class OperatorLegalProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int | None = Field(default=None, gt=0)
    legal_name: str | None = Field(default=None, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    company_number: str | None = Field(default=None, max_length=64)
    public_url: str | None = Field(default=None, max_length=2048)
    privacy_email: str | None = Field(default=None, max_length=254)
    complaints_email: str | None = Field(default=None, max_length=254)
    security_contact: str | None = Field(default=None, max_length=254)
    postal_address: str | None = Field(default=None, max_length=1000)
    cookie_banner_summary: str | None = Field(default=None, max_length=1000)

    @field_validator(
        "legal_name",
        "display_name",
        "company_number",
        "postal_address",
        "cookie_banner_summary",
    )
    @classmethod
    def validate_plain_optional_text(cls, value: str | None, info) -> str | None:
        if value is None or not value.strip():
            return None
        return _plain_text(value, field_name=info.field_name.replace("_", " ").title())

    @field_validator("public_url")
    @classmethod
    def validate_public_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return LegalLabelledHttpsLinkBlock(type="labelled_https_link", label="Operator", url=value).url

    @field_validator("privacy_email", "complaints_email", "security_contact")
    @classmethod
    def validate_email(cls, value: str | None, info) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip().lower()
        if _CONTROL_CHARACTER_RE.search(normalized) or not _EMAIL_RE.fullmatch(normalized):
            raise ValueError(f"{info.field_name.replace('_', ' ').title()} must be a valid email address")
        return normalized


class LegalDocumentDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: LegalDocumentKind
    effective_on: date
    content: LegalDocumentContent


class LegalDocumentDraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(gt=0)
    effective_on: date
    content: LegalDocumentContent
