from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models import TeamStatus
from app.quota_policy import (
    DEFAULT_DAILY_AUDIO_SECONDS_LIMIT,
    DEFAULT_DAILY_TOKEN_LIMIT,
    DEFAULT_MONTHLY_AUDIO_SECONDS_LIMIT,
    DEFAULT_MONTHLY_TOKEN_LIMIT,
    MAX_DAILY_AUDIO_SECONDS,
    MAX_DAILY_TOKENS,
    MAX_MONTHLY_AUDIO_SECONDS,
    MAX_MONTHLY_TOKENS,
)


class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    status: TeamStatus = TeamStatus.active
    default_retention_days: int = Field(default=30, ge=1)
    default_daily_token_limit: int = Field(default=DEFAULT_DAILY_TOKEN_LIMIT, ge=0, le=MAX_DAILY_TOKENS)
    default_monthly_token_limit: int = Field(default=DEFAULT_MONTHLY_TOKEN_LIMIT, ge=0, le=MAX_MONTHLY_TOKENS)
    default_daily_audio_seconds_limit: int = Field(default=DEFAULT_DAILY_AUDIO_SECONDS_LIMIT, ge=0, le=MAX_DAILY_AUDIO_SECONDS)
    default_monthly_audio_seconds_limit: int = Field(default=DEFAULT_MONTHLY_AUDIO_SECONDS_LIMIT, ge=0, le=MAX_MONTHLY_AUDIO_SECONDS)


class TeamListItem(BaseModel):
    id: UUID
    name: str
    status: TeamStatus
    default_retention_days: int
    default_daily_token_limit: int
    default_monthly_token_limit: int
    default_daily_audio_seconds_limit: int
    default_monthly_audio_seconds_limit: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TeamDetail(TeamListItem):
    pass
