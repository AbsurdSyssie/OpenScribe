# Account Recovery Brief

## Status

This document records the implemented local-auth recovery model and explicitly separates unimplemented future options. The earlier “current gaps” were obsolete: transactional email, self-service password reset, activation/setup links, manager MFA reset, manager recovery links, break-glass recovery, session/trusted-device revocation, and durable security audit metadata are implemented.

The authoritative route and access contract is [auth.md](auth.md) and [api.md](api.md).

## Hard rules

- Passwords are authentication material only and never encrypt transcript-derived content.
- Password/account recovery must not rotate or destroy the user's content DEK.
- Recovery never grants leaders/system administrators owner-content readability.
- Password, MFA, trusted-device, session, setup/reset token, and recovery-code material are separate authorities.
- Recovery revokes stale sessions/trusted-device trust according to the action performed.
- Public reset requests do not disclose whether an email exists when mail is enabled.
- Plaintext setup/reset tokens and temporary passwords are shown/sent only through their intended one-time channel; only hashes are persisted.
- Recovery audit contains bounded action/outcome/reason metadata, never credentials, submitted TOTP values, recovery codes, tokens, or owner content.

## Current authentication authority

OpenScribe currently owns local authentication:

- password verification/reset;
- TOTP enrollment/challenge/reset;
- optional recovery-code generation/storage;
- session and trusted-device state;
- activation/setup tokens;
- manager-assisted recovery;
- break-glass temporary-password recovery.

There is no implemented `auth_provider` model or Auth0 login/recovery mode in the current route/schema contract. External identity integration requires a separate architecture decision; do not document Auth0 as an available recovery path.

## Implemented self-service recovery

### Password reset request

`POST /api/v1/auth/password-reset/request`

- requires configured transactional mail;
- returns a generic response for existing/missing users when enabled;
- returns `503 mail_transport_disabled` when the deployment cannot send recovery mail;
- creates a bounded single-use hashed email token for eligible accounts;
- never returns the plaintext token through the API.

### Password reset completion

`POST /api/v1/auth/password-reset/confirm`

- validates the single-use token and expiry;
- applies the permanent-password policy;
- revokes sessions and trusted devices;
- preserves TOTP methods/recovery codes for password-only reset;
- preserves the user DEK and encrypted owner content;
- requires normal login/MFA after reset.

### Account activation/setup completion

`POST /api/v1/auth/account-activation/confirm`

Activation/setup is first-use account setup rather than ordinary password-only recovery. It establishes a permanent password and forces TOTP onboarding before full access.

## Recovery codes

Recovery codes are optionally generated during onboarding and stored as one-way hashes. Their plaintext cannot be recovered from database state.

The current operational API/auth documentation does not expose a recovery-code alternative on `POST /api/v1/auth/mfa/totp` or a dedicated recovery-code-to-TOTP-re-enrollment route. Do not instruct users that they can enter a recovery code at the MFA challenge until that flow is explicitly implemented, tested, and documented.

Current user guidance should say:

- store generated codes according to local policy;
- treat each as credential material;
- contact a leader/system administrator for approved recovery when the authenticator is lost and no implemented self-service path is available.

A future recovery-code flow should consume one code, issue only restricted recovery authority, force TOTP re-enrollment, revoke old trust, and never grant indefinite full access directly.

## Implemented manager-assisted recovery

Eligible team leaders and system administrators can perform metadata-only actions within their authorization scope:

- send activation/setup link;
- send password-reset link;
- send full account-recovery link;
- reset MFA;
- break-glass password reset;
- break-glass full account recovery.

Leaders are restricted to non-system-admin users in their own team and cannot act on themselves through protected manager lifecycle routes. System-admin protected-account/last-admin rules remain authoritative.

### Send password-reset link

Use when the password is lost but existing MFA should remain. Completion revokes session/trusted-device authority and preserves TOTP/recovery-code state.

### Send full account-recovery link

Use when the account needs password plus MFA recovery. Completion resets the applicable authentication factors and returns the user to required setup/onboarding without touching owner-content keys.

### Reset MFA

Use when the user knows the password but cannot use the authenticator. The action revokes existing authority, removes active TOTP/recovery-code state, and forces re-enrollment through the implemented onboarding state.

### Break-glass password reset

Use only when policy permits and email recovery is unavailable. It requires:

- the manager's current TOTP code;
- a reason;
- explicit confirmation that email recovery is unavailable;
- rate-limited/protected manager authority.

It returns an expiring temporary password once, stores only its hash, revokes authority, and forces password change. It preserves MFA state for password-only recovery.

### Break-glass full account recovery

Uses the same safeguards but also clears MFA/recovery-code state and forces full password/TOTP setup.

Legacy `recover-password` and `recover-account` endpoints are deprecated and fail closed with `410 deprecated_recovery_endpoint`.

## Mail infrastructure

Transactional email is instance-level infrastructure, not team/user provider policy.

Supported transports:

- `disabled`;
- `stdout` in local/test only;
- `resend`.

Required production considerations:

- verified sender and correct public HTTPS `APP_PUBLIC_URL`;
- API key injected through deployment secret or provisioned Vault reference;
- no reset/setup token logging;
- clear expiry/single-use behavior;
- delivery monitoring without copying message/token content into support logs.

See [environment.md](environment.md) and [setup.md](setup.md).

## Recovery effects matrix

| Action | Password | TOTP / recovery codes | Sessions / trusted devices | User DEK / owner content |
| --- | --- | --- | --- | --- |
| Self-service password reset | Replace | Preserve | Revoke | Preserve |
| Manager password-reset link | Replace on completion | Preserve | Revoke | Preserve |
| Activation/setup link | Establish/replace | Force enrollment | Revoke/replace | Preserve/initialize as applicable |
| Manager MFA reset | Preserve | Clear and re-enroll | Revoke | Preserve |
| Full account-recovery link | Replace | Clear and re-enroll | Revoke | Preserve |
| Break-glass password reset | Temporary then permanent | Preserve | Revoke | Preserve |
| Break-glass full recovery | Temporary then permanent | Clear and re-enroll | Revoke | Preserve |
| Suspension | Preserve | Preserve but trust unusable | Revoke/block | Preserve |
| Hard deletion | Delete account | Delete | Delete | Delete through current lifecycle/cascades |

## Future external identity

External IdP support is not currently implemented. Before adding it, define:

- per-account auth authority (`local` versus specific external provider);
- account linking/provisioning/deprovisioning;
- MFA and password recovery owner;
- app-session creation/revocation;
- trusted-device interaction;
- break-glass policy;
- audit/incident behavior;
- migration and rollback.

Do not run local password recovery and an external IdP recovery flow against the same identity without an explicit authority model.
