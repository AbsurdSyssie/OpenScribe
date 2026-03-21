import enum
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import JSON, CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class TeamRole(str, enum.Enum):
    leader = "leader"
    user = "user"


class TeamStatus(str, enum.Enum):
    active = "active"
    suspended = "suspended"


class UserStatus(str, enum.Enum):
    active = "active"
    suspended = "suspended"
    locked = "locked"
    disabled = "disabled"


class UserOnboardingState(str, enum.Enum):
    pending_password_change = "pending_password_change"
    pending_totp_enrollment = "pending_totp_enrollment"
    pending_recovery_codes = "pending_recovery_codes"
    complete = "complete"


class TranscriptStatus(str, enum.Enum):
    recording = "recording"
    transcribing = "transcribing"
    ready = "ready"
    failed = "failed"


class TranscriptIngestionMode(str, enum.Enum):
    whole_file = "whole_file"
    live_chunked = "live_chunked"


class TranscriptIngestionJobKind(str, enum.Enum):
    live_chunk = "live_chunk"
    audio_file = "audio_file"


class TranscriptIngestionJobStatus(str, enum.Enum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    applied = "applied"
    failed = "failed"


class AccountRequestStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    reviewed = "reviewed"
    withdrawn = "withdrawn"


class SessionAuthLevel(str, enum.Enum):
    onboarding = "onboarding"
    pending_mfa = "pending_mfa"
    full = "full"


class SessionStatus(str, enum.Enum):
    active = "active"
    revoked = "revoked"
    expired = "expired"


class MfaMethodType(str, enum.Enum):
    totp = "totp"


class SttAuthMode(str, enum.Enum):
    bearer = "bearer"


class SttAdapterKind(str, enum.Enum):
    generic_rest = "generic_rest"
    openai_cloud = "openai_cloud"
    openai_compatible_rest = "openai_compatible_rest"


class LlmAuthMode(str, enum.Enum):
    none = "none"
    bearer = "bearer"


class LlmAdapterKind(str, enum.Enum):
    openai_chat = "openai_chat"
    ollama_chat = "ollama_chat"


class TemplateScope(str, enum.Enum):
    team = "team"
    user = "user"


class TemplateMode(str, enum.Enum):
    freeform = "freeform"


class GeneratedDocumentGeneratorType(str, enum.Enum):
    template = "template"
    followup = "followup"
    quick_action = "quick_action"


class GeneratedDocumentStatus(str, enum.Enum):
    queued = "queued"
    processing = "processing"
    ready = "ready"
    failed = "failed"


class ProviderFeatureType(str, enum.Enum):
    llm_generation = "llm_generation"


class ProviderUsageEventType(str, enum.Enum):
    queued = "queued"
    started = "started"
    completed = "completed"
    failed = "failed"
    enqueue_failed = "enqueue_failed"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    status: Mapped[TeamStatus] = mapped_column(Enum(TeamStatus), default=TeamStatus.active, nullable=False)
    default_retention_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    users: Mapped[list["User"]] = relationship(back_populates="team")
    transcripts: Mapped[list["Transcript"]] = relationship(back_populates="team")
    stt_configs: Mapped[list["TeamSttConfig"]] = relationship(back_populates="team")
    stt_selection: Mapped["TeamSttSelection | None"] = relationship(back_populates="team", uselist=False, cascade="all, delete-orphan")
    llm_configs: Mapped[list["TeamLlmConfig"]] = relationship(back_populates="team")
    llm_selection: Mapped["TeamLlmSelection | None"] = relationship(back_populates="team", uselist=False, cascade="all, delete-orphan")
    templates: Mapped[list["PromptTemplate"]] = relationship(back_populates="team")
    quick_actions: Mapped[list["QuickAction"]] = relationship(back_populates="team")
    provider_usage_events: Mapped[list["ProviderUsageEvent"]] = relationship(back_populates="team")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (Index("uq_users_email_lower", text("lower(email)"), unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=True)
    team_role: Mapped[TeamRole | None] = mapped_column(Enum(TeamRole), nullable=True)
    is_system_admin: Mapped[bool] = mapped_column(default=False, nullable=False)
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus), default=UserStatus.active, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(default=False, nullable=False)
    onboarding_state: Mapped[UserOnboardingState] = mapped_column(
        Enum(UserOnboardingState),
        default=UserOnboardingState.complete,
        nullable=False,
    )
    mfa_required: Mapped[bool] = mapped_column(default=True, nullable=False)
    mfa_enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    team: Mapped[Team | None] = relationship(back_populates="users")
    transcripts: Mapped[list["Transcript"]] = relationship(back_populates="owner")
    created_account_requests: Mapped[list["AccountRequest"]] = relationship(
        back_populates="linked_user",
        foreign_keys="AccountRequest.linked_user_id",
    )
    reviewed_account_requests: Mapped[list["AccountRequest"]] = relationship(
        back_populates="reviewed_by",
        foreign_keys="AccountRequest.reviewed_by_user_id",
    )
    sessions: Mapped[list["UserSession"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    trusted_devices: Mapped[list["UserTrustedDevice"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    mfa_methods: Mapped[list["UserMfaMethod"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    recovery_codes: Mapped[list["UserRecoveryCode"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    llm_preference: Mapped["UserLlmPreference | None"] = relationship(
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    owned_templates: Mapped[list["PromptTemplate"]] = relationship(
        back_populates="owner",
        foreign_keys="PromptTemplate.owner_user_id",
    )
    created_templates: Mapped[list["PromptTemplate"]] = relationship(
        back_populates="created_by",
        foreign_keys="PromptTemplate.created_by_user_id",
    )
    owned_quick_actions: Mapped[list["QuickAction"]] = relationship(
        back_populates="owner",
        foreign_keys="QuickAction.owner_user_id",
    )
    created_quick_actions: Mapped[list["QuickAction"]] = relationship(
        back_populates="created_by",
        foreign_keys="QuickAction.created_by_user_id",
    )
    generated_documents: Mapped[list["GeneratedDocument"]] = relationship(back_populates="owner")
    provider_usage_events: Mapped[list["ProviderUsageEvent"]] = relationship(back_populates="owner")


class AccountRequest(Base):
    __tablename__ = "account_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    requested_name: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_email: Mapped[str] = mapped_column(String(320), nullable=False)
    requested_team_name: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_team_name_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[AccountRequestStatus] = mapped_column(
        Enum(AccountRequestStatus),
        default=AccountRequestStatus.pending,
        nullable=False,
    )
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    linked_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    reviewed_by: Mapped[User | None] = relationship(
        back_populates="reviewed_account_requests",
        foreign_keys=[reviewed_by_user_id],
    )
    linked_user: Mapped[User | None] = relationship(
        back_populates="created_account_requests",
        foreign_keys=[linked_user_id],
    )


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    session_token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    auth_level: Mapped[SessionAuthLevel] = mapped_column(Enum(SessionAuthLevel), nullable=False)
    status: Mapped[SessionStatus] = mapped_column(Enum(SessionStatus), default=SessionStatus.active, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="sessions")


class UserTrustedDevice(Base):
    __tablename__ = "user_trusted_devices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    device_token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_mfa_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="trusted_devices")


class UserMfaMethod(Base):
    __tablename__ = "user_mfa_methods"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    method_type: Mapped[MfaMethodType] = mapped_column(Enum(MfaMethodType), nullable=False)
    secret: Mapped[str] = mapped_column(Text, nullable=False)
    is_primary: Mapped[bool] = mapped_column(default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=False, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="mfa_methods")


class UserRecoveryCode(Base):
    __tablename__ = "user_recovery_codes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="recovery_codes")


class TeamSttConfig(Base):
    __tablename__ = "team_stt_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    adapter_kind: Mapped[SttAdapterKind] = mapped_column(Enum(SttAdapterKind), default=SttAdapterKind.generic_rest, nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    transcribe_path: Mapped[str] = mapped_column(String(255), nullable=False)
    auth_mode: Mapped[SttAuthMode] = mapped_column(Enum(SttAuthMode), default=SttAuthMode.bearer, nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    available_models_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    file_field_name: Mapped[str] = mapped_column(String(255), default="file", nullable=False)
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    response_text_path: Mapped[str] = mapped_column(String(255), default="text", nullable=False)
    extra_form_fields_json: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    vault_secret_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    updated_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    team: Mapped[Team] = relationship(back_populates="stt_configs")
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_user_id])
    updated_by: Mapped[User] = relationship(foreign_keys=[updated_by_user_id])
    selections: Mapped[list["TeamSttSelection"]] = relationship(back_populates="config")


class TeamSttSelection(Base):
    __tablename__ = "team_stt_selections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id"), unique=True, nullable=False)
    stt_config_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("team_stt_configs.id"), nullable=False)
    model_name_override: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language_override: Mapped[str | None] = mapped_column(String(32), nullable=True)
    selected_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    team: Mapped[Team] = relationship(back_populates="stt_selection")
    config: Mapped[TeamSttConfig] = relationship(back_populates="selections")
    selected_by: Mapped[User] = relationship(foreign_keys=[selected_by_user_id])


class TeamLlmConfig(Base):
    __tablename__ = "team_llm_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    adapter_kind: Mapped[LlmAdapterKind] = mapped_column(Enum(LlmAdapterKind), default=LlmAdapterKind.openai_chat, nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    auth_mode: Mapped[LlmAuthMode] = mapped_column(Enum(LlmAuthMode), default=LlmAuthMode.bearer, nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    available_models_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    vault_secret_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    updated_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    team: Mapped[Team] = relationship(back_populates="llm_configs")
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_user_id])
    updated_by: Mapped[User] = relationship(foreign_keys=[updated_by_user_id])
    selections: Mapped[list["TeamLlmSelection"]] = relationship(back_populates="config")


class TeamLlmSelection(Base):
    __tablename__ = "team_llm_selections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id"), unique=True, nullable=False)
    llm_config_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("team_llm_configs.id"), nullable=False)
    allowed_models_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    model_name_override: Mapped[str | None] = mapped_column(String(255), nullable=True)
    selected_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    team: Mapped[Team] = relationship(back_populates="llm_selection")
    config: Mapped[TeamLlmConfig] = relationship(back_populates="selections")
    selected_by: Mapped[User] = relationship(foreign_keys=[selected_by_user_id])


class UserLlmPreference(Base):
    __tablename__ = "user_llm_preferences"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), unique=True, nullable=False)
    preferred_model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="llm_preference")


class PromptTemplate(Base):
    __tablename__ = "templates"
    __table_args__ = (
        CheckConstraint(
            "(scope = 'user' AND owner_user_id IS NOT NULL AND team_id IS NULL) "
            "OR (scope = 'team' AND team_id IS NOT NULL AND owner_user_id IS NULL)",
            name="ck_templates_scope_owner_team",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scope: Mapped[TemplateScope] = mapped_column(Enum(TemplateScope), nullable=False)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    owner: Mapped[User | None] = relationship(back_populates="owned_templates", foreign_keys=[owner_user_id])
    team: Mapped[Team | None] = relationship(back_populates="templates")
    created_by: Mapped[User] = relationship(back_populates="created_templates", foreign_keys=[created_by_user_id])
    versions: Mapped[list["PromptTemplateVersion"]] = relationship(back_populates="template", cascade="all, delete-orphan")


class PromptTemplateVersion(Base):
    __tablename__ = "template_versions"
    __table_args__ = (UniqueConstraint("template_id", "version_no", name="uq_template_version_number"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("templates.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[TemplateMode] = mapped_column(Enum(TemplateMode), default=TemplateMode.freeform, nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    template: Mapped[PromptTemplate] = relationship(back_populates="versions")
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_user_id])


class QuickAction(Base):
    __tablename__ = "quick_actions"
    __table_args__ = (
        CheckConstraint(
            "(scope = 'user' AND owner_user_id IS NOT NULL AND team_id IS NULL) "
            "OR (scope = 'team' AND team_id IS NOT NULL AND owner_user_id IS NULL)",
            name="ck_quick_actions_scope_owner_team",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scope: Mapped[TemplateScope] = mapped_column(Enum(TemplateScope), nullable=False)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    owner: Mapped[User | None] = relationship(back_populates="owned_quick_actions", foreign_keys=[owner_user_id])
    team: Mapped[Team | None] = relationship(back_populates="quick_actions")
    created_by: Mapped[User] = relationship(back_populates="created_quick_actions", foreign_keys=[created_by_user_id])
    versions: Mapped[list["QuickActionVersion"]] = relationship(back_populates="quick_action", cascade="all, delete-orphan")


class QuickActionVersion(Base):
    __tablename__ = "quick_action_versions"
    __table_args__ = (UniqueConstraint("quick_action_id", "version_no", name="uq_quick_action_version_number"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    quick_action_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("quick_actions.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[TemplateMode] = mapped_column(Enum(TemplateMode), default=TemplateMode.freeform, nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    quick_action: Mapped[QuickAction] = relationship(back_populates="versions")
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_user_id])


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current_draft_text_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingestion_mode: Mapped[TranscriptIngestionMode] = mapped_column(
        Enum(TranscriptIngestionMode),
        default=TranscriptIngestionMode.whole_file,
        nullable=False,
    )
    status: Mapped[TranscriptStatus] = mapped_column(
        Enum(TranscriptStatus), default=TranscriptStatus.recording, nullable=False
    )
    next_live_chunk_sequence_no_applied: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    retention_days_applied: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    retention_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    owner: Mapped[User] = relationship(back_populates="transcripts")
    team: Mapped[Team] = relationship(back_populates="transcripts")
    versions: Mapped[list["TranscriptVersion"]] = relationship(
        back_populates="transcript", cascade="all, delete-orphan"
    )
    ingestion_jobs: Mapped[list["TranscriptIngestionJob"]] = relationship(
        back_populates="transcript",
        cascade="all, delete-orphan",
    )
    generated_documents: Mapped[list["GeneratedDocument"]] = relationship(
        back_populates="transcript",
        cascade="all, delete-orphan",
    )


class TranscriptVersion(Base):
    __tablename__ = "transcript_versions"
    __table_args__ = (UniqueConstraint("transcript_id", "version_no", name="uq_transcript_version_number"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transcript_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("transcripts.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    text_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    transcript: Mapped[Transcript] = relationship(back_populates="versions")


class TranscriptIngestionJob(Base):
    __tablename__ = "transcript_ingestion_jobs"
    __table_args__ = (
        UniqueConstraint("transcript_id", "chunk_sequence_no", name="uq_transcript_ingestion_job_chunk_sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transcript_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("transcripts.id"), nullable=False)
    job_kind: Mapped[TranscriptIngestionJobKind] = mapped_column(Enum(TranscriptIngestionJobKind), nullable=False)
    chunk_sequence_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stt_config_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    stt_adapter_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stt_base_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    stt_transcribe_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stt_model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stt_language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stt_file_field_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stt_response_text_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stt_extra_form_fields_json: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[TranscriptIngestionJobStatus] = mapped_column(
        Enum(TranscriptIngestionJobStatus),
        default=TranscriptIngestionJobStatus.queued,
        nullable=False,
    )
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result_text_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    transcript: Mapped[Transcript] = relationship(back_populates="ingestion_jobs")


class GeneratedDocument(Base):
    __tablename__ = "generated_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False)
    transcript_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("transcripts.id"), nullable=False)
    transcript_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("transcript_versions.id"), nullable=False)
    generator_type: Mapped[GeneratedDocumentGeneratorType] = mapped_column(
        Enum(GeneratedDocumentGeneratorType),
        default=GeneratedDocumentGeneratorType.template,
        nullable=False,
    )
    template_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("template_versions.id"), nullable=True)
    quick_action_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("quick_action_versions.id"), nullable=True)
    llm_config_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_template_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_quick_action_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    follow_up_prompt_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_snapshot_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[GeneratedDocumentStatus] = mapped_column(
        Enum(GeneratedDocumentStatus),
        default=GeneratedDocumentStatus.queued,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    document_mode: Mapped[TemplateMode] = mapped_column(Enum(TemplateMode), default=TemplateMode.freeform, nullable=False)
    original_output_text_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    edited_output_text_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    is_edited: Mapped[bool] = mapped_column(default=False, nullable=False)
    retention_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    model_used: Mapped[str | None] = mapped_column(String(255), nullable=True)
    llm_adapter_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    llm_base_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_error_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    owner: Mapped[User] = relationship(back_populates="generated_documents")
    transcript: Mapped[Transcript] = relationship(back_populates="generated_documents")
    transcript_version: Mapped[TranscriptVersion] = relationship(foreign_keys=[transcript_version_id])
    template_version: Mapped[PromptTemplateVersion | None] = relationship(foreign_keys=[template_version_id])
    quick_action_version: Mapped[QuickActionVersion | None] = relationship(foreign_keys=[quick_action_version_id])
    provider_usage_events: Mapped[list["ProviderUsageEvent"]] = relationship(back_populates="generated_document")


class ProviderUsageEvent(Base):
    __tablename__ = "provider_usage_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="SET NULL"), nullable=True)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    generated_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generated_documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    transcript_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    llm_config_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    feature_type: Mapped[ProviderFeatureType] = mapped_column(Enum(ProviderFeatureType), nullable=False)
    event_type: Mapped[ProviderUsageEventType] = mapped_column(Enum(ProviderUsageEventType), nullable=False)
    provider_adapter: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_error_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    team: Mapped[Team | None] = relationship(back_populates="provider_usage_events")
    owner: Mapped[User | None] = relationship(back_populates="provider_usage_events")
    generated_document: Mapped[GeneratedDocument | None] = relationship(back_populates="provider_usage_events")


def transcript_expiry(days: int) -> datetime:
    return utcnow() + timedelta(days=days)
