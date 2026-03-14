from pydantic import BaseModel, EmailStr, Field

from app.models import SessionAuthLevel, UserOnboardingState


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class LoginResponse(BaseModel):
    authenticated: bool
    auth_level: SessionAuthLevel | None = None
    redirect_to: str | None = None


class PasswordChangeRequest(BaseModel):
    new_password: str = Field(min_length=8)


class TotpEnrollmentStartResponse(BaseModel):
    secret: str
    provisioning_uri: str


class TotpVerifyRequest(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class RecoveryCodesResponse(BaseModel):
    codes: list[str]


class CurrentUserResponse(BaseModel):
    id: str
    full_name: str | None
    email: str
    is_system_admin: bool
    team_id: str | None
    team_role: str | None
    auth_level: SessionAuthLevel
    onboarding_state: UserOnboardingState
