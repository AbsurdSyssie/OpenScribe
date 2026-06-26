from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.errors import AppError
from app.models import SessionAuthLevel, UserOnboardingState
from app.services.passwords import validate_password_strength


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, description="Existing account password. New permanent passwords use the stronger 12-character policy.")


class LoginResponse(BaseModel):
    authenticated: bool
    auth_level: SessionAuthLevel | None = None
    redirect_to: str | None = None


class PasswordChangeRequest(BaseModel):
    new_password: str = Field(min_length=12)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        try:
            return validate_password_strength(value)
        except AppError as exc:
            raise ValueError("password does not meet strength requirements") from exc


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(min_length=16)
    new_password: str = Field(min_length=12)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        try:
            return validate_password_strength(value)
        except AppError as exc:
            raise ValueError("password does not meet strength requirements") from exc


class AccountActivationConfirmRequest(BaseModel):
    token: str = Field(min_length=16)
    new_password: str = Field(min_length=12)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        try:
            return validate_password_strength(value)
        except AppError as exc:
            raise ValueError("password does not meet strength requirements") from exc


class GenericMessageResponse(BaseModel):
    message: str


class ManagerRecoveryResponse(BaseModel):
    message: str
    temporary_password: str
    temporary_password_expires_at: datetime
    recovery_mode: str


class ManagerRecoveryEmailRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class BreakGlassRecoveryRequest(BaseModel):
    mfa_code: str = Field(min_length=6, max_length=8)
    reason: str = Field(min_length=3, max_length=500)
    confirm_email_unavailable: bool = False


class TotpEnrollmentStartResponse(BaseModel):
    secret: str
    provisioning_uri: str
    qr_code_svg_data_uri: str


class TotpVerifyRequest(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class MfaChallengeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=8)
    remember_device: bool = False


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


class TrustedDeviceStatusResponse(BaseModel):
    trusted: bool
    requires_mfa: bool
    freshness_expires_at: datetime | None = None
