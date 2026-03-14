from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.models import TeamRole, UserOnboardingState, UserStatus


class UserCreate(BaseModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr
    temporary_password: str = Field(min_length=8)
    team_id: UUID | None = None
    team_role: TeamRole | None = None
    is_system_admin: bool = False
    status: UserStatus = UserStatus.active
    mfa_required: bool = True


class UserListItem(BaseModel):
    id: UUID
    full_name: str | None
    email: str
    team_id: UUID | None
    team_role: TeamRole | None
    is_system_admin: bool
    status: UserStatus
    must_change_password: bool
    onboarding_state: UserOnboardingState
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None

    model_config = {"from_attributes": True}


class UserDetail(UserListItem):
    mfa_required: bool
    mfa_enabled: bool
