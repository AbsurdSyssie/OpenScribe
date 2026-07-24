from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class QuickActionBundleVersion(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    mode: Literal["freeform"]
    prompt_text: str = Field(min_length=1)


class QuickActionBundleEntry(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    name: str = Field(min_length=1, max_length=255)
    description: str | None
    latest_version: QuickActionBundleVersion


class QuickActionBundle(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    format: Literal["openscribe-quick-action-bundle"]
    format_version: Literal[1]
    quick_actions: list[QuickActionBundleEntry] = Field(min_length=1, max_length=100)


class QuickActionBundleExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quick_action_ids: list[UUID] = Field(min_length=1, max_length=100)


class QuickActionBundleIssue(BaseModel):
    path: str
    message: str


class QuickActionBundleImportEntry(BaseModel):
    index: int
    source_name: str | None
    proposed_name: str | None
    status: Literal["invalid", "ready", "exact_copy", "renamed"]
    selected_by_default: bool
    selectable: bool
    errors: list[QuickActionBundleIssue]
    warnings: list[QuickActionBundleIssue]


class QuickActionBundleImportSummary(BaseModel):
    total: int
    importable: int
    exact_copies: int
    invalid: int
    renamed: int
    unknown_fields: int


class QuickActionBundlePreflightResponse(BaseModel):
    entries: list[QuickActionBundleImportEntry]
    warnings: list[QuickActionBundleIssue]
    summary: QuickActionBundleImportSummary


class ImportedQuickAction(BaseModel):
    index: int
    quick_action_id: UUID
    name: str


class QuickActionBundleCommitSummary(BaseModel):
    selected: int
    imported: int
    skipped: int
    warning_count: int


class QuickActionBundleImportResponse(BaseModel):
    created: list[ImportedQuickAction]
    skipped_indexes: list[int]
    warnings: list[QuickActionBundleIssue]
    summary: QuickActionBundleCommitSummary
