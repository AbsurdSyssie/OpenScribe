# Account Recovery Brief

This note defines a recovery model that fits the current OpenScribe architecture.

It is intentionally scoped around authentication recovery only.
It does not widen transcript visibility, alter deletion semantics, or couple data access to the user password.

## Existing baseline

Current implemented pieces:

- email + password login
- TOTP enrollment during onboarding
- post-password MFA challenge for completed accounts
- optional one-time recovery codes generated during onboarding
- trusted-device skip window for recent MFA
- revocable opaque sessions and trusted-device records

Current gaps:

- no full self-service forgot-password flow
- no recovery-code login challenge for lost TOTP
- no manager-assisted TOTP reset flow
- no dedicated recovery UX for "lost password and lost authenticator"
- no outbound email transport abstraction for reset delivery
- no external IdP / Auth0 auth mode

## Hard rules

Any recovery design must preserve these:

- passwords are auth material only
- passwords do not encrypt transcript-derived data
- password reset must not rotate or destroy the user DEK
- leaders and system admins still do not gain transcript readability
- recovery must revoke stale sessions and trusted-device trust where appropriate
- recovery flows must not leak whether an email exists
- one account must have one clear auth authority; do not mix local-password reset and Auth0 reset for the same identity without an explicit model

## Auth authority models

There are two valid models.
Pick one per account.

### Model A: local auth owned by OpenScribe

OpenScribe owns:

- password verification
- password reset
- TOTP enrollment
- recovery codes
- manager-assisted recovery

This is the current architectural direction.

### Model B: external auth owned by Auth0

Auth0 owns:

- primary login
- password reset
- MFA challenge and MFA reset for Auth0-managed factors
- recovery flows for Auth0-managed accounts

OpenScribe still owns:

- application sessions after successful Auth0 login
- authorization, roles, teams, and ownership
- transcript privacy boundaries
- optional local break-glass logic only if explicitly designed

Strong rule:

- do not run both local-password recovery and Auth0-password recovery against the same account without a formal `auth_provider` model
- otherwise users and managers will not know which system is authoritative

## Supported recovery scenarios

The scenarios below assume local-auth accounts unless noted otherwise.

### 1. Lost password, still has TOTP

Recommended flow:

1. user requests password reset by email
2. system issues a single-use reset token with short expiry
3. user sets a new password
4. existing sessions and trusted-device records are revoked
5. user logs in with new password
6. normal MFA challenge still applies

This is standard password recovery only.
It should not reset TOTP by itself.

### 2. Lost TOTP, still has password and a recovery code

Recommended flow:

1. user logs in with email + password
2. MFA challenge offers either TOTP or recovery code
3. recovery code is verified and consumed
4. current `pending_mfa` session is elevated into a restricted recovery session
5. user is forced to enroll a new TOTP secret
6. old recovery codes are deleted
7. new recovery codes may be generated
8. trusted devices are reset because old MFA trust is no longer valid

Important point:
recovery code should not just drop the user into ordinary full access forever.
It should be treated as a one-time bridge into MFA re-enrollment.

### 3. Lost password, lost TOTP, still has recovery code

Recommended flow:

1. user completes self-service password reset
2. user logs in with the new password
3. MFA challenge accepts recovery code
4. system consumes the code and forces TOTP re-enrollment
5. new recovery codes are generated or explicitly skipped

This stays self-service.

### 4. Lost TOTP and no recovery codes, still knows password

Recommended flow:

1. user contacts a manager out of band
2. team leader or system admin verifies identity outside the app
3. manager triggers "reset MFA"
4. system revokes sessions and trusted devices
5. system deletes active TOTP methods and recovery codes
6. account state is moved back to `pending_totp_enrollment`
7. user logs in with existing password and is forced through TOTP setup again

This is manager-assisted recovery.
It does not require a password reset if the password is still known.

### 5. Lost password, lost TOTP, and no recovery codes

Recommended flow:

1. user contacts a manager out of band
2. team leader or system admin verifies identity outside the app
3. manager triggers "reset password and MFA"
4. system sets a temporary password or password-reset token
5. system deletes active TOTP methods and recovery codes
6. system revokes sessions and trusted devices
7. account state returns to onboarding
8. user changes password, enrolls TOTP, and optionally regenerates recovery codes

This is the strongest recovery action and should be audited clearly.

### 6. Auth0-managed account recovery

For Auth0-managed accounts:

1. user chooses sign-in with Auth0
2. lost-password flow is handled by Auth0 reset email
3. lost MFA flow is handled by Auth0 guardian/MFA recovery path
4. once Auth0 authenticates the user, OpenScribe creates or refreshes its own app session

Manager role in this case:

- leaders and system admins may disable the OpenScribe account
- they may not reset Auth0 secrets from inside OpenScribe unless we intentionally build admin-management integration with Auth0 APIs

Best first plan:

- keep Auth0 recovery self-service only
- do not try to build manager-triggered Auth0 admin actions in phase 1

## Recommended product behavior

### Self-service features

Add:

- forgot-password request form
- emailed password reset link
- password reset completion form
- recovery-code option on MFA challenge

Strong recommendation:

- keep recovery codes, but stop treating them as a soft extra in practice
- either make them mandatory for MFA-required accounts
- or keep skip available but warn clearly that manager intervention will otherwise be required

### Manager-assisted features

Allow team leaders and system admins to do metadata-only recovery actions:

- issue password reset
- reset MFA only
- reset password and MFA together

Do not allow them to:

- view TOTP secrets
- view recovery codes
- bypass ownership and read content

## Suggested state model

The cleanest shape is:

- keep current onboarding states for first-time setup
- add a separate security-recovery requirement instead of overloading full access

Recommended new session/auth concept:

- `recovery_reenroll`

Meaning:

- user has passed password plus a one-time recovery factor
- user may access only the recovery flow
- user must set up a new TOTP method before regaining full access

If you want lower schema churn, a simpler variant is:

- reuse onboarding gating
- after manager-assisted reset or recovery-code login, set user back to a pending setup state

That is cheaper to ship, but it mixes first-time onboarding with later account recovery.
It is acceptable in MVP if the UX copy is explicit.

## Schema additions

Minimum new schema:

- `password_reset_tokens`
  - `id`
  - `user_id`
  - `token_hash`
  - `expires_at`
  - `used_at`
  - `created_at`
  - optional `created_by_user_id` for manager-issued resets
  - optional `purpose` enum such as `self_service` or `manager_reset`

Possible small additions:

- `user_sessions.auth_level` new value for recovery-only sessions
- optional `users.recovery_required_at`
- optional audit/event table if recovery actions need durable review trail beyond logs

## Email delivery and instance linking

Self-service password reset is not complete until the app can deliver a reset message.

Recommended model:

- outbound email is instance-level platform infrastructure
- it is not team-scoped
- it is not user-configurable
- system admins configure it once per deployment

This section applies to local-auth password reset.
If Auth0 owns password reset for a user, Auth0 sends the reset email instead of OpenScribe.

Why:

- reset delivery is platform security infrastructure
- it should not depend on team leaders
- it should not reuse transcript/LLM/STT provider config tables

### Transport options

Support one transport first:

- SMTP relay

Examples in practice could be:

- Postmark SMTP
- SendGrid SMTP
- AWS SES SMTP
- internal corporate SMTP relay

This keeps the first implementation simple.
Later, if needed, add API-based providers behind the same mailer interface.

### Recommended config shape

Add one instance-level mail transport config, for example:

- `MAIL_TRANSPORT=disabled|smtp`
- `MAIL_FROM_ADDRESS`
- `MAIL_FROM_NAME`
- `MAIL_REPLY_TO` optional
- `MAIL_RESET_BASE_URL`

Sensitive SMTP values should not live in plain env for production if the rest of the app already uses Vault-backed secrets.

Recommended secret model:

- store SMTP credential material in Vault
- store only a Vault reference in the database if you need editable admin-managed config
- or load the secret from env only in local/dev if you want the smallest first slice

Pragmatic split:

- dev: env vars acceptable
- production: Vault-backed secret strongly preferred

### Suggested ownership model

Best fit for current architecture:

- system admin provisions outbound email transport
- database stores only metadata and optional Vault reference
- runtime mailer resolves the secret from Vault when sending
- leaders have no power to edit or view mail credentials

That mirrors the existing provider-secret pattern.

### Send path

Recommended send path:

1. create password reset token row
2. enqueue email job or write to outbox
3. worker sends email using instance mail transport
4. job stores delivery status metadata only

Do not send reset mail synchronously in the request path unless shipping a very small MVP.

Why queue it:

- avoids tying auth latency to mail server latency
- gives retry behavior
- gives auditability

### Minimum email content

Reset email should contain:

- generic security wording
- short-lived reset link
- expiry duration
- ignore-this-if-not-you copy

Do not include:

- sensitive account state
- role/team data unless necessary
- anything transcript-related

### URL generation

The reset link needs an external base URL.

Add one explicit public app origin setting, for example:

- `APP_PUBLIC_URL=https://openscribe.example.com`

Then generate links like:

- `${APP_PUBLIC_URL}/reset-password?token=...`

Do not infer this from request headers alone for security-sensitive emails.

### Local development

For development, easiest options are:

- MailHog/Mailpit SMTP
- console/file outbox mode

Suggested dev option:

- `MAIL_TRANSPORT=stdout` or `mailpit`

That lets tests and local runs verify the flow without a real external provider.

### Tests needed for email transport slice

- reset request writes token row but never reveals user existence
- email job/outbox row created for real users only
- public response remains generic for both existing and missing emails
- token confirmation works without requiring mail provider network access in tests
- logs contain event metadata only, not token plaintext
- manager roles cannot view raw mail credentials

## Endpoint / route shape

### Public self-service

- `POST /api/v1/auth/password-reset/request`
- `POST /api/v1/auth/password-reset/confirm`
- browser pages for request and completion

Rules:

- always return generic success text from request
- rate limit tightly
- token must be single-use and short-lived

### MFA fallback

- `POST /api/v1/auth/mfa/recovery-code`

Rules:

- only valid for a `pending_mfa` session
- consumes exactly one stored recovery code
- rotates the session
- sends user into TOTP re-enrollment, not ordinary steady-state access

### Manager-assisted

- `POST /api/v1/users/{user_id}/recover-password`
- `POST /api/v1/users/{user_id}/reset-mfa`
- `POST /api/v1/users/{user_id}/recover-account`

Authority:

- system admins: any allowed non-protected account
- team leaders: non-system-admin users in their own team only

Behavior:

- revoke sessions
- revoke trusted devices
- clear the right MFA/recovery state
- update onboarding or recovery gating state
- never expose secrets in response payloads

### Auth0 login / callback

If Auth0 is added, the minimum route shape is:

- `GET /auth/auth0/login`
- `GET /auth/auth0/callback`
- optional logout handoff if single logout is desired

OpenScribe then:

- verifies Auth0 identity token / callback result
- resolves local user by stable subject/email mapping
- creates the normal opaque OpenScribe app session
- continues to enforce local team/role/content ownership

Recommended user model additions if Auth0 is adopted:

- `auth_provider` enum such as `local` or `auth0`
- `external_subject` for stable Auth0 identity binding
- optional `password_hash` nullable only for non-local accounts

Important:

- email alone is not the strongest long-term binding key
- stable Auth0 subject should be stored if available

## Security controls

Required:

- generic responses for public reset request
- hashed reset tokens only
- short expiry
- single-use token invalidation
- revoke all existing reset tokens on successful reset
- revoke trusted devices on password reset and MFA reset
- audit log events for request, issue, use, failure, and manager action
- rate limits for reset request, reset confirm, and recovery-code attempts

Recommended:

- notify the user by email when password or MFA is reset
- require reason text for manager-assisted recovery
- show last recovery event timestamp to the user after login

If Auth0 is used:

- verify issuer, audience, nonce, and expiry correctly
- never trust raw browser profile data without token validation
- treat Auth0 as the auth source only; OpenScribe authorization still happens locally
- avoid silent local-user auto-linking on email alone unless policy is explicit

## UX guidance

The recovery UX should present plain choices:

- forgot password
- use recovery code instead
- ask your team lead for help

Avoid mixing all recovery logic into the regular login form.
The login form should stay simple.

If both local auth and Auth0 exist:

- present them as clearly separate sign-in methods
- recovery help must branch based on account auth mode
- local accounts see OpenScribe reset path
- Auth0 accounts see "Continue with Auth0" and Auth0-managed recovery guidance

The MFA challenge page is the right place to surface:

- TOTP code entry
- "use a recovery code"
- "I lost my authenticator"

Manager tools should use careful wording:

- `Reset MFA`
- `Reset password`
- `Reset password and MFA`

Not:

- `Recover account` as the only label

That is too vague for an action with strong consequences.

## Recommended implementation order

### Phase 1

- decide auth scope:
  - local auth only
  - or mixed local + Auth0 account model
- if mixed, add explicit `auth_provider` model first

### Phase 2

- self-service password reset
- outbound email transport or dev outbox mode
- `password_reset_tokens`
- public request/confirm routes
- session + trusted-device revocation on success

### Phase 3

- recovery-code MFA challenge
- forced TOTP re-enrollment after recovery-code use
- recovery-code regeneration

### Phase 4

- Auth0 login/callback implementation if chosen
- external-subject binding
- local session creation after Auth0 auth
- recovery UX branching by auth mode

### Phase 5

- manager-assisted MFA reset
- manager-assisted password+MFA reset
- audit trail and notifications

### Phase 6

- polish recovery UX
- decide whether recovery codes become mandatory
- add dedicated recovery event history if needed

## Recommendation

Best MVP path:

1. build self-service password reset first
2. build recovery-code MFA fallback second
3. build manager-assisted reset third

That order covers the biggest real-world failures without changing the privacy or encryption model.

If Auth0 is desired, the first question is not "how do we send reset email?"
It is "which accounts are local and which are Auth0-managed?"
