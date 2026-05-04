## Agent brief: safe account recovery with email-first + break-glass fallback

### Scope

Implement a safer recovery system for OpenScribe that preserves account recovery when Resend/email is not configured.

Current repo state relevant to this change:

* API recovery endpoints currently return `temporary_password` via `ManagerRecoveryResponse`. 
* Browser team-management routes currently generate temporary passwords for `/home/users/{user_id}/recover-password` and `/home/users/{user_id}/recover-account`. 
* Existing email token purposes already include `manager_password_reset` and `manager_account_recovery`. 
* `confirm_password_reset` already treats `manager_account_recovery` as a password + MFA reset flow. 
* Email transport can be disabled and is controlled through `MAIL_TRANSPORT`. 

The implementation should **not remove temporary-password recovery**. It should make it an explicit, audited, MFA-gated, time-limited **break-glass** path.

---

# Desired behaviour

## Recovery decision table

| Condition                            | UI/API behaviour                                                                                                              |
| ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| Email transport available            | Prefer email reset/account recovery. Do not show ordinary temporary-password recovery.                                        |
| Email transport unavailable          | Show break-glass recovery actions. Temporary password may be shown once to manager.                                           |
| Break-glass used                     | Require manager TOTP code, reason, explicit confirmation, audit log, expiry, session revocation, trusted-device revocation.   |
| User logs in with temporary password | User only enters onboarding/recovery state. No transcript/home access until permanent password + MFA/recovery flow completed. |

---

# PR structure

Implement this as one focused PR if possible. If it gets large, split into:

1. **Backend recovery policy + audit + migrations**
2. **Browser/UI recovery changes**
3. **Tests**

---

# Files to modify

Likely files:

```text
app/models.py
app/schemas/auth.py
app/services/auth.py
app/services/auth_email.py
app/services/admin.py
app/routes/api_routes.py
app/routes/web_team_management.py
app/routes/web_admin.py
app/web/presentation.py
app/templates/home*.html
app/templates/admin*.html
alembic/versions/<new>_break_glass_recovery.py
tests/test_auth_service.py
tests/test_auth_email.py
tests/test_admin_ui.py
tests/test_break_glass_recovery.py
```

Exact template names may vary; search for:

```text
recover-password
recover-account
recovery_temporary_password
temporary_password
```

---

# 1. Add recovery/audit fields

## `app/models.py`

Add recovery metadata to `User`.

```python
class UserRecoveryMode(str, enum.Enum):
    manager_password_reset = "manager_password_reset"
    manager_account_recovery = "manager_account_recovery"
    break_glass_password_reset = "break_glass_password_reset"
    break_glass_account_recovery = "break_glass_account_recovery"
```

Add fields to `User`:

```python
temporary_password_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
recovery_mode: Mapped[UserRecoveryMode | None] = mapped_column(Enum(UserRecoveryMode), nullable=True)
recovery_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
recovery_started_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
```

Add relationship if useful:

```python
recovery_started_by: Mapped["User | None"] = relationship(
    foreign_keys=[recovery_started_by_user_id],
)
```

Add a security audit table. Keep `action` as `String` rather than enum to avoid future migrations for every new audit action.

```python
class SecurityAuditEvent(Base):
    __tablename__ = "security_audit_events"
    __table_args__ = (
        Index("ix_security_audit_events_actor_created", "actor_user_id", "created_at"),
        Index("ix_security_audit_events_target_created", "target_user_id", "created_at"),
        Index("ix_security_audit_events_action_created", "action", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=True)
    request_ip: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    actor: Mapped["User | None"] = relationship(foreign_keys=[actor_user_id])
    target: Mapped["User | None"] = relationship(foreign_keys=[target_user_id])
    team: Mapped["Team | None"] = relationship()
```

---

# 2. Add Alembic migration

Create something like:

```text
alembic/versions/<revision>_break_glass_recovery_and_security_audit.py
```

Skeleton:

```python
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "<new_revision>"
down_revision = "<current_head>"
branch_labels = None
depends_on = None


user_recovery_mode = postgresql.ENUM(
    "manager_password_reset",
    "manager_account_recovery",
    "break_glass_password_reset",
    "break_glass_account_recovery",
    name="userrecoverymode",
)


def upgrade():
    bind = op.get_bind()
    user_recovery_mode.create(bind, checkfirst=True)

    op.add_column("users", sa.Column("temporary_password_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("recovery_mode", user_recovery_mode, nullable=True))
    op.add_column("users", sa.Column("recovery_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("recovery_started_by_user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_users_recovery_started_by_user_id_users",
        "users",
        "users",
        ["recovery_started_by_user_id"],
        ["id"],
    )

    op.create_table(
        "security_audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_ip", sa.String(length=255), nullable=True),
        sa.Column("user_agent", sa.String(length=1024), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
    )
    op.create_index("ix_security_audit_events_actor_created", "security_audit_events", ["actor_user_id", "created_at"])
    op.create_index("ix_security_audit_events_target_created", "security_audit_events", ["target_user_id", "created_at"])
    op.create_index("ix_security_audit_events_action_created", "security_audit_events", ["action", "created_at"])


def downgrade():
    op.drop_index("ix_security_audit_events_action_created", table_name="security_audit_events")
    op.drop_index("ix_security_audit_events_target_created", table_name="security_audit_events")
    op.drop_index("ix_security_audit_events_actor_created", table_name="security_audit_events")
    op.drop_table("security_audit_events")

    op.drop_constraint("fk_users_recovery_started_by_user_id_users", "users", type_="foreignkey")
    op.drop_column("users", "recovery_started_by_user_id")
    op.drop_column("users", "recovery_started_at")
    op.drop_column("users", "recovery_mode")
    op.drop_column("users", "temporary_password_expires_at")

    bind = op.get_bind()
    user_recovery_mode.drop(bind, checkfirst=True)
```

Adjust `down_revision` to the current repo head.

---

# 3. Add audit service

Create:

```text
app/services/security_audit.py
```

```python
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import SecurityAuditEvent, User


SENSITIVE_AUDIT_KEYS = {
    "password",
    "temporary_password",
    "token",
    "mfa_code",
    "totp",
    "secret",
    "authorization",
}


def _safe_details(details: dict[str, Any] | None) -> dict[str, Any]:
    if not details:
        return {}
    clean: dict[str, Any] = {}
    for key, value in details.items():
        normalized = key.lower()
        if any(sensitive in normalized for sensitive in SENSITIVE_AUDIT_KEYS):
            continue
        clean[key] = value
    return clean


def request_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.client.host if request.client else None


def record_security_event(
    db: Session,
    *,
    action: str,
    actor: User | None = None,
    target: User | None = None,
    team_id: UUID | None = None,
    request: Request | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    event = SecurityAuditEvent(
        action=action,
        actor_user_id=actor.id if actor else None,
        target_user_id=target.id if target else None,
        team_id=team_id or (target.team_id if target else None),
        request_ip=request_ip(request),
        user_agent=request.headers.get("user-agent") if request else None,
        details_json=_safe_details(details),
    )
    db.add(event)
    db.commit()
```

Do not log passwords, TOTP codes, email tokens, reset links, transcript text, generated document text, or provider payloads.

---

# 4. Update auth schemas

## `app/schemas/auth.py`

Replace the existing manager response with an extended one:

```python
class ManagerRecoveryResponse(BaseModel):
    message: str
    temporary_password: str
    temporary_password_expires_at: datetime
    recovery_mode: str
```

Add request models:

```python
class ManagerRecoveryEmailRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class BreakGlassRecoveryRequest(BaseModel):
    mfa_code: str = Field(min_length=6, max_length=8)
    reason: str = Field(min_length=3, max_length=500)
    confirm_email_unavailable: bool = False
```

The confirmation field is deliberately explicit. The API route should reject break-glass requests unless `confirm_email_unavailable` is true.

---

# 5. Add manager TOTP verification helper

## `app/services/auth.py`

Add a reusable helper rather than misusing login MFA.

```python
def verify_active_totp_for_user(user: User, *, code: str) -> None:
    method = active_primary_totp_method(user)
    if method is None:
        raise AppError(403, "fresh_mfa_required", "A verified TOTP method is required for this action")

    totp = pyotp.TOTP(method.secret)
    if not totp.verify(code, valid_window=1):
        raise AppError(422, "business_rule_violation", "Invalid TOTP code")
```

This should **not** create a trusted device and should **not** change the user’s MFA state. It is only a fresh authorisation check for dangerous manager operations.

---

# 6. Enforce temporary-password expiry on login

## `app/services/auth.py`

In `authenticate_user`, after password verification succeeds and before returning the user, add:

```python
if (
    user.must_change_password
    and user.temporary_password_expires_at is not None
    and user.temporary_password_expires_at <= utcnow()
):
    raise AppError(
        403,
        "temporary_password_expired",
        "Temporary password has expired. Ask your team leader or administrator to generate a new recovery password.",
    )
```

Also ensure the login path for a user in recovery gets onboarding-level access only. Existing `determine_auth_level` already returns onboarding unless `onboarding_state` is complete. Keep that invariant.

---

# 7. Clear recovery flags after permanent password set

## `app/services/auth.py`

In `update_password_for_onboarding`, after setting the permanent password:

```python
user.temporary_password_expires_at = None
user.recovery_mode = None
user.recovery_started_at = None
user.recovery_started_by_user_id = None
```

Full intended function shape:

```python
def update_password_for_onboarding(db: Session, user: User, *, new_password_hash: str) -> User:
    if user.onboarding_state is not UserOnboardingState.pending_password_change:
        raise AppError(409, "conflict", "Password change is not pending for this user")

    user.password_hash = new_password_hash
    user.must_change_password = False

    user.temporary_password_expires_at = None
    user.recovery_mode = None
    user.recovery_started_at = None
    user.recovery_started_by_user_id = None

    user.onboarding_state = (
        UserOnboardingState.complete
        if user.mfa_enabled and active_primary_totp_method(user) is not None
        else UserOnboardingState.pending_totp_enrollment
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
```

This means:

* password-only break-glass preserves existing MFA if present
* account-recovery break-glass clears MFA first, then forces TOTP re-enrolment

---

# 8. Replace admin recovery service behaviour

## `app/services/admin.py`

Keep `reset_user_password_to_temporary_service`, but make it clearly break-glass capable and safe.

Current routes call `reset_user_password_to_temporary_service(db, user, reset_mfa=True/False)`. Replace or extend it.

Suggested code:

```python
BREAK_GLASS_TEMPORARY_PASSWORD_LIFETIME_MINUTES = int(
    os.getenv("BREAK_GLASS_TEMPORARY_PASSWORD_LIFETIME_MINUTES", "60")
)


def generate_temporary_password() -> str:
    # Long enough for temporary transport over phone/in-person; simple enough to dictate.
    return secrets.token_urlsafe(18)


def reset_user_password_to_temporary(
    db: Session,
    user: User,
    *,
    actor: User,
    reset_mfa: bool = False,
    break_glass: bool = False,
) -> tuple[str, datetime]:
    if user.status is not UserStatus.active:
        raise AppError(403, "forbidden", "User account is not active", {"status": user.status.value})

    temporary_password = generate_temporary_password()
    expires_at = utcnow() + timedelta(minutes=BREAK_GLASS_TEMPORARY_PASSWORD_LIFETIME_MINUTES)

    user.password_hash = hash_password(temporary_password)
    user.must_change_password = True
    user.onboarding_state = UserOnboardingState.pending_password_change
    user.temporary_password_expires_at = expires_at
    user.recovery_started_at = utcnow()
    user.recovery_started_by_user_id = actor.id
    user.recovery_mode = (
        UserRecoveryMode.break_glass_account_recovery
        if reset_mfa and break_glass
        else UserRecoveryMode.break_glass_password_reset
        if break_glass
        else UserRecoveryMode.manager_account_recovery
        if reset_mfa
        else UserRecoveryMode.manager_password_reset
    )

    if reset_mfa:
        user.mfa_enabled = False
        for method in db.scalars(
            select(UserMfaMethod).where(UserMfaMethod.user_id == user.id)
        ):
            db.delete(method)
        for code in db.scalars(
            select(UserRecoveryCode).where(UserRecoveryCode.user_id == user.id)
        ):
            db.delete(code)

    db.add(user)
    db.commit()

    revoke_sessions_for_user(db, user, reason="break_glass_recovery" if break_glass else "manager_recovery")
    revoke_trusted_devices_for_user(db, user, reason="break_glass_recovery" if break_glass else "manager_recovery")

    return temporary_password, expires_at
```

Add imports if needed:

```python
import os
from app.models import UserRecoveryMode
```

Important: after this change, all callers must pass `actor=`.

---

# 9. Email-aware service helpers

## `app/services/auth_email.py`

You already have:

* `email_password_reset_enabled`
* `send_password_reset_email`
* `confirm_password_reset`
* `manager_password_reset`
* `manager_account_recovery`

Add convenience helpers:

```python
def send_manager_password_reset_email(db: Session, *, actor: User, target: User) -> None:
    send_password_reset_email(
        db,
        target,
        purpose=AuthEmailTokenPurpose.manager_password_reset,
        created_by=actor,
    )


def send_manager_account_recovery_email(db: Session, *, actor: User, target: User) -> None:
    send_password_reset_email(
        db,
        target,
        purpose=AuthEmailTokenPurpose.manager_account_recovery,
        created_by=actor,
    )
```

These are thin wrappers but make route code clearer.

---

# 10. API routes

## `app/routes/api_routes.py`

### Add imports

```python
from ..services.security_audit import record_security_event
from ..services.auth import verify_active_totp_for_user
```

Also import new schemas:

```python
BreakGlassRecoveryRequest
ManagerRecoveryEmailRequest
```

### Add helper

```python
def _break_glass_allowed() -> bool:
    # Default: enabled only when mail recovery is unavailable.
    # Optional env override allows deployments to hard-disable it entirely.
    if os.getenv("BREAK_GLASS_RECOVERY_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        return False
    if email_password_reset_enabled_service():
        return os.getenv("BREAK_GLASS_ALLOW_WITH_MAIL_ENABLED", "false").lower() in {"1", "true", "yes"}
    return True
```

Make sure `os` is imported if not already available through `main`.

### Replace current recovery endpoints

Current endpoints:

```python
@api.post("/users/{user_id}/recover-password", response_model=ManagerRecoveryResponse)
@api.post("/users/{user_id}/recover-account", response_model=ManagerRecoveryResponse)
```

Change them to reject/deprecate or make them call safe paths. Preferred: return 410/409 so old clients must be updated.

```python
@api.post("/users/{user_id}/recover-password", response_model=GenericMessageResponse, responses=error_responses)
def recover_user_password_deprecated(user_id: UUID):
    raise AppError(
        410,
        "deprecated_recovery_endpoint",
        "Use /send-password-reset for email recovery or /break-glass-password-reset when email is unavailable.",
    )
```

Same for `/recover-account`.

### Add email recovery endpoints

```python
@api.post("/users/{user_id}/send-password-reset", response_model=GenericMessageResponse, responses=error_responses)
def send_manager_password_reset(
    user_id: UUID,
    payload: ManagerRecoveryEmailRequest,
    request: Request,
    context: AuthenticatedContext = Depends(require_user_manager),
    db: Session = Depends(get_db),
):
    if not email_password_reset_enabled_service():
        raise AppError(
            503,
            "mail_transport_disabled",
            "Email recovery is not enabled. Use break-glass recovery if appropriate.",
        )

    user = get_manageable_user_for_recovery_service(db, context.user, user_id)
    send_password_reset_email_service(
        db,
        user,
        purpose=AuthEmailTokenPurpose.manager_password_reset,
        created_by=context.user,
    )
    record_security_event(
        db,
        action="manager_password_reset_email_sent",
        actor=context.user,
        target=user,
        request=request,
        details={"reason": payload.reason},
    )
    return GenericMessageResponse(message="Recovery email sent if the account is eligible.")
```

```python
@api.post("/users/{user_id}/send-account-recovery", response_model=GenericMessageResponse, responses=error_responses)
def send_manager_account_recovery(
    user_id: UUID,
    payload: ManagerRecoveryEmailRequest,
    request: Request,
    context: AuthenticatedContext = Depends(require_user_manager),
    db: Session = Depends(get_db),
):
    if not email_password_reset_enabled_service():
        raise AppError(
            503,
            "mail_transport_disabled",
            "Email recovery is not enabled. Use break-glass recovery if appropriate.",
        )

    user = get_manageable_user_for_recovery_service(db, context.user, user_id)
    send_password_reset_email_service(
        db,
        user,
        purpose=AuthEmailTokenPurpose.manager_account_recovery,
        created_by=context.user,
    )
    record_security_event(
        db,
        action="manager_account_recovery_email_sent",
        actor=context.user,
        target=user,
        request=request,
        details={"reason": payload.reason},
    )
    return GenericMessageResponse(message="Account recovery email sent if the account is eligible.")
```

### Add break-glass endpoints

```python
@api.post(
    "/users/{user_id}/break-glass-password-reset",
    response_model=ManagerRecoveryResponse,
    responses=error_responses,
)
def break_glass_password_reset(
    user_id: UUID,
    payload: BreakGlassRecoveryRequest,
    request: Request,
    context: AuthenticatedContext = Depends(require_user_manager),
    db: Session = Depends(get_db),
):
    if not payload.confirm_email_unavailable:
        raise AppError(422, "confirmation_required", "Confirm that email recovery is unavailable before using break-glass recovery")
    if not _break_glass_allowed():
        raise AppError(409, "break_glass_not_available", "Break-glass recovery is not available while email recovery is enabled")

    verify_active_totp_for_user(context.user, code=payload.mfa_code)

    user = get_manageable_user_for_recovery_service(db, context.user, user_id)
    temporary_password, expires_at = reset_user_password_to_temporary_service(
        db,
        user,
        actor=context.user,
        reset_mfa=False,
        break_glass=True,
    )
    record_security_event(
        db,
        action="break_glass_password_reset_generated",
        actor=context.user,
        target=user,
        request=request,
        details={"reason": payload.reason, "expires_at": expires_at.isoformat()},
    )
    return ManagerRecoveryResponse(
        message="Break-glass temporary password generated. Share it with the user out of band. It is shown once.",
        temporary_password=temporary_password,
        temporary_password_expires_at=expires_at,
        recovery_mode="break_glass_password_reset",
    )
```

```python
@api.post(
    "/users/{user_id}/break-glass-account-recovery",
    response_model=ManagerRecoveryResponse,
    responses=error_responses,
)
def break_glass_account_recovery(
    user_id: UUID,
    payload: BreakGlassRecoveryRequest,
    request: Request,
    context: AuthenticatedContext = Depends(require_user_manager),
    db: Session = Depends(get_db),
):
    if not payload.confirm_email_unavailable:
        raise AppError(422, "confirmation_required", "Confirm that email recovery is unavailable before using break-glass recovery")
    if not _break_glass_allowed():
        raise AppError(409, "break_glass_not_available", "Break-glass recovery is not available while email recovery is enabled")

    verify_active_totp_for_user(context.user, code=payload.mfa_code)

    user = get_manageable_user_for_recovery_service(db, context.user, user_id)
    temporary_password, expires_at = reset_user_password_to_temporary_service(
        db,
        user,
        actor=context.user,
        reset_mfa=True,
        break_glass=True,
    )
    record_security_event(
        db,
        action="break_glass_account_recovery_generated",
        actor=context.user,
        target=user,
        request=request,
        details={"reason": payload.reason, "expires_at": expires_at.isoformat()},
    )
    return ManagerRecoveryResponse(
        message="Break-glass temporary password generated and MFA reset. Share it with the user out of band. It is shown once.",
        temporary_password=temporary_password,
        temporary_password_expires_at=expires_at,
        recovery_mode="break_glass_account_recovery",
    )
```

### Audit successful recovery login

In `api_login`, after `user = authenticate_user(...)`, add:

```python
if user.recovery_mode is not None and user.must_change_password:
    record_security_event(
        db,
        action="temporary_recovery_password_login",
        actor=user,
        target=user,
        request=request,
        details={"recovery_mode": user.recovery_mode.value},
    )
```

Do not log the password.

---

# 11. Browser routes

## `app/routes/web_team_management.py`

Replace existing:

```text
/home/users/{user_id}/recover-password
/home/users/{user_id}/recover-account
```

with:

```text
/home/users/{user_id}/send-password-reset
/home/users/{user_id}/send-account-recovery
/home/users/{user_id}/break-glass-password-reset
/home/users/{user_id}/break-glass-account-recovery
```

### Email password reset route

```python
@app.post("/home/users/{user_id}/send-password-reset", response_class=HTMLResponse)
def home_send_password_reset(
    request: Request,
    user_id: UUID,
    reason: str = Form(""),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        if not email_password_reset_enabled_service():
            raise AppError(503, "mail_transport_disabled", "Email recovery is not enabled. Use break-glass recovery if appropriate.")
        user = get_manageable_user_for_recovery_service(db, context.user, user_id)
        send_password_reset_email_service(
            db,
            user,
            purpose=AuthEmailTokenPurpose.manager_password_reset,
            created_by=context.user,
        )
        record_security_event(
            db,
            action="manager_password_reset_email_sent",
            actor=context.user,
            target=user,
            request=request,
            details={"reason": reason or None},
        )
    except AppError as exc:
        return render_home(... same error rendering pattern ...)
    return RedirectResponse(
        url=_home_redirect_url(return_view=return_view, return_tab=return_tab or "team-management"),
        status_code=status.HTTP_303_SEE_OTHER,
    )
```

Use existing render error block style from the current route.

### Break-glass route

```python
@app.post("/home/users/{user_id}/break-glass-password-reset", response_class=HTMLResponse)
def home_break_glass_password_reset(
    request: Request,
    user_id: UUID,
    mfa_code: str = Form(...),
    reason: str = Form(...),
    confirm_email_unavailable: str | None = Form(default=None),
    return_view: str = Form(""),
    return_tab: str = Form(""),
    csrf_protected: BrowserCsrf = None,
    db: Session = Depends(get_db),
):
    context, response = _page_context_or_redirect(request, db, require_full=True)
    if response is not None:
        return response
    try:
        if confirm_email_unavailable != "true":
            raise AppError(422, "confirmation_required", "Confirm that email recovery is unavailable before using break-glass recovery")
        if email_password_reset_enabled_service() and os.getenv("BREAK_GLASS_ALLOW_WITH_MAIL_ENABLED", "false").lower() not in {"1", "true", "yes"}:
            raise AppError(409, "break_glass_not_available", "Break-glass recovery is not available while email recovery is enabled")
        verify_active_totp_for_user(context.user, code=mfa_code)
        user = get_manageable_user_for_recovery_service(db, context.user, user_id)
        temporary_password, expires_at = reset_user_password_to_temporary_service(
            db,
            user,
            actor=context.user,
            reset_mfa=False,
            break_glass=True,
        )
        record_security_event(
            db,
            action="break_glass_password_reset_generated",
            actor=context.user,
            target=user,
            request=request,
            details={"reason": reason, "expires_at": expires_at.isoformat()},
        )
    except AppError as exc:
        return render_home(... same error rendering pattern ...)
    return render_home(
        request,
        db,
        current_user=context.user,
        message="Break-glass temporary password generated. It is shown once.",
        message_kind="success",
        recovery_temporary_password=temporary_password,
        active_home_tab=return_tab or "team-management",
        template_name=_home_template_name_from_return_view(return_view),
        home_page_route=_home_page_route_from_return_view(return_view),
        home_return_view=_home_return_view_value(return_view),
    )
```

Add equivalent account-recovery route with `reset_mfa=True` and action `break_glass_account_recovery_generated`.

### Deprecated old browser routes

Keep old paths temporarily but make them fail closed:

```python
@app.post("/home/users/{user_id}/recover-password", response_class=HTMLResponse)
def home_recover_password_deprecated(...):
    return render_home(
        ...,
        message="This recovery action has moved. Use email recovery, or break-glass recovery when email is unavailable.",
        message_kind="error",
        status_code=status.HTTP_410_GONE,
        ...
    )
```

Same for `/recover-account`.

---

# 12. Admin browser routes

`web_admin.py` likely has equivalent recovery routes further down. Search for:

```text
/admin/users/{user_id}/recover-password
/admin/users/{user_id}/recover-account
reset_user_password_to_temporary_service
```

Apply the same pattern as `web_team_management.py`.

System admins should still be subject to break-glass TOTP verification. Do not exempt system admins from fresh MFA.

---

# 13. UI changes

## Data passed to templates

Where `render_home` / `render_admin` assemble context, add:

```python
email_recovery_enabled = email_password_reset_enabled_service()
break_glass_recovery_enabled = (
    os.getenv("BREAK_GLASS_RECOVERY_ENABLED", "true").lower() in {"1", "true", "yes"}
    and (
        not email_recovery_enabled
        or os.getenv("BREAK_GLASS_ALLOW_WITH_MAIL_ENABLED", "false").lower() in {"1", "true", "yes"}
    )
)
```

Pass both to templates.

## Template behaviour

For each manageable user row:

### If email enabled

Show:

```html
<form method="post" action="/home/users/{{ user.id }}/send-password-reset">
  <input type="hidden" name="_csrf_token" value="{{ csrf_token }}">
  <input type="hidden" name="return_tab" value="team-management">
  <button type="submit">Send password reset email</button>
</form>

<form method="post" action="/home/users/{{ user.id }}/send-account-recovery">
  <input type="hidden" name="_csrf_token" value="{{ csrf_token }}">
  <input type="hidden" name="return_tab" value="team-management">
  <button type="submit">Send account recovery email</button>
</form>
```

### If email disabled

Show a visibly dangerous section:

```html
<section class="recovery-danger-zone">
  <p>
    Email recovery is not configured. Break-glass recovery generates a temporary password visible to you once.
    Use only when you have verified the user's identity out of band. This action is audited.
  </p>

  <form method="post" action="/home/users/{{ user.id }}/break-glass-password-reset">
    <input type="hidden" name="_csrf_token" value="{{ csrf_token }}">
    <input type="hidden" name="return_tab" value="team-management">

    <label>
      Reason
      <input name="reason" required minlength="3" maxlength="500">
    </label>

    <label>
      Your TOTP code
      <input name="mfa_code" inputmode="numeric" autocomplete="one-time-code" required minlength="6" maxlength="8">
    </label>

    <label>
      <input type="checkbox" name="confirm_email_unavailable" value="true" required>
      I confirm email recovery is unavailable and this is a break-glass action.
    </label>

    <button type="submit">Generate break-glass temporary password</button>
  </form>
</section>
```

For full account recovery, label it more strongly:

```text
Generate break-glass password and reset MFA
```

Copy should state:

```text
This resets the user's MFA. The user must set a permanent password and enrol TOTP again before accessing OpenScribe.
```

---

# 14. Account creation and account request approval

This change is mainly recovery. But because account creation currently takes a `temporary_password` in browser routes, do not remove that in this PR unless you are ready to convert onboarding fully to email.

Leave account creation as-is for now, but add a follow-up task:

```text
When mail is enabled, account creation should send activation email automatically and avoid manager-known temporary passwords where possible.
When mail is disabled, creation with a temporary password remains acceptable.
```

Do not scope-creep unless tests are stable.

---

# 15. Tests

Create `tests/test_break_glass_recovery.py`.

## Test cases

### Email mode

```python
def test_manager_password_reset_uses_email_when_mail_enabled(client, db, leader, team_user, monkeypatch):
    # Arrange mail enabled.
    # POST /api/v1/users/{id}/send-password-reset
    # Assert no temporary_password in response.
    # Assert auth email token exists with manager_password_reset purpose.
    # Assert audit event exists.
```

### Mail disabled

```python
def test_send_password_reset_rejects_when_mail_disabled(client, db, leader, team_user, monkeypatch):
    # MAIL_TRANSPORT=disabled
    # POST /api/v1/users/{id}/send-password-reset
    # Assert 503 mail_transport_disabled.
```

### Break-glass allowed when mail disabled

```python
def test_break_glass_password_reset_returns_temporary_password_when_mail_disabled_and_mfa_valid(...):
    # MAIL_TRANSPORT=disabled
    # manager has active TOTP
    # POST /api/v1/users/{id}/break-glass-password-reset with mfa_code + reason + confirm
    # Assert temporary_password returned once.
    # Assert target must_change_password true.
    # Assert target onboarding_state pending_password_change.
    # Assert temporary_password_expires_at set.
    # Assert existing sessions/trusted devices revoked.
    # Assert audit event exists.
```

### Break-glass account recovery clears MFA

```python
def test_break_glass_account_recovery_resets_mfa_and_recovery_codes(...):
    # Target starts with TOTP + recovery codes.
    # POST break-glass-account-recovery.
    # Assert mfa_enabled false.
    # Assert TOTP methods deleted.
    # Assert recovery codes deleted.
    # Assert onboarding_state pending_password_change.
```

### Break-glass blocked when mail enabled

```python
def test_break_glass_blocked_when_mail_enabled_by_default(...):
    # MAIL_TRANSPORT=resend or stdout test config
    # POST break-glass endpoint
    # Assert 409 break_glass_not_available.
```

### TOTP required

```python
def test_break_glass_requires_valid_manager_totp(...):
    # Missing/invalid mfa_code
    # Assert no password returned.
    # Assert no target state change.
```

### Expired temporary password cannot log in

```python
def test_expired_temporary_password_cannot_login(...):
    # Generate recovery password.
    # Set temporary_password_expires_at in past.
    # POST /api/v1/auth/login with temp password.
    # Assert 403 temporary_password_expired.
```

### Recovery login has onboarding only

```python
def test_recovery_password_login_cannot_access_transcribe_until_onboarding_complete(...):
    # Generate break-glass password.
    # Login as target.
    # Assert auth_level onboarding.
    # GET /home or /transcribe redirects/blocks until password/MFA flow complete.
```

### Password-only recovery preserves MFA

```python
def test_password_only_recovery_preserves_existing_mfa(...):
    # Target has active TOTP.
    # Break-glass password reset, reset_mfa=False.
    # Login temp password.
    # Change password.
    # Assert onboarding_state complete if active TOTP still exists.
```

### Permanent password clears recovery flags

```python
def test_onboarding_password_change_clears_recovery_metadata(...):
    # Generate break-glass password.
    # Login and POST /onboarding/password.
    # Assert temporary_password_expires_at is None.
    # Assert recovery_mode is None.
    # Assert recovery_started_by_user_id is None.
```

---

# 16. Acceptance criteria

The implementing agent is done when:

* No API route named ordinary `recover-password` or `recover-account` returns a temporary password.
* Email recovery returns only a generic message.
* Break-glass recovery returns a temporary password only when:

  * email recovery is unavailable, unless env override explicitly allows it
  * actor has user-management authority
  * actor supplies valid TOTP
  * actor supplies reason
  * actor explicitly confirms email is unavailable
* Break-glass temporary passwords expire.
* Break-glass resets revoke target sessions and trusted devices.
* Password-only break-glass does not clear target MFA.
* Account-recovery break-glass clears target MFA and recovery codes.
* Target user cannot access transcripts/home until recovery/onboarding state is complete.
* Security audit events are created and contain no password/token/TOTP/transcript content.
* Browser UI clearly distinguishes email recovery from break-glass recovery.
* Tests cover email-enabled, email-disabled, invalid MFA, expiry, state transitions, and audit logging.

---

# Suggested commit message

```text
Harden manager account recovery with email-first and break-glass flows
```
