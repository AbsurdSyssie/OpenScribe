from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.models import AccountRequestStatus, TeamRole


class AccountRequestCreate(BaseModel):
    requested_name: str = Field(min_length=1, max_length=255)
    requested_email: EmailStr
    requested_team_name: str = Field(min_length=1, max_length=255)
    request_details: str | None = Field(default=None, max_length=2000)


class AccountRequestListItem(BaseModel):
    id: UUID
    requested_name: str
    requested_email: str
    requested_team_name: str
    request_details: str | None
    status: AccountRequestStatus
    review_notes: str | None
    linked_user_id: UUID | None
    reviewed_by_user_id: UUID | None
    created_at: datetime
    reviewed_at: datetime | None

    model_config = {"from_attributes": True}


class AccountRequestDetail(AccountRequestListItem):
    pass


class AccountRequestApprove(BaseModel):
    full_name: str | None = Field(default=None, max_length=255)
    team_id: UUID | None = None
    team_role: TeamRole = TeamRole.user
    temporary_password: str = Field(min_length=8)
    mfa_required: bool = True
    review_notes: str | None = Field(default=None, max_length=2000)


class AccountRequestReject(BaseModel):
    review_notes: str = Field(min_length=1, max_length=2000)
