from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models import TeamStatus


class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    status: TeamStatus = TeamStatus.active
    default_retention_days: int = Field(default=30, ge=1)


class TeamListItem(BaseModel):
    id: UUID
    name: str
    status: TeamStatus
    default_retention_days: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TeamDetail(TeamListItem):
    pass
