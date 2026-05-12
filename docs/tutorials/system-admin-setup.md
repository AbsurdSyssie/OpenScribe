# System Admin Setup Tutorial

## Audience

This tutorial is for the person setting up OpenScribe for the first time or maintaining system-level configuration.

After initial setup, read [Admin tutorial](admin.md) for daily admin workspace tasks.

## Local or Deployment Setup

Follow `docs/setup.md` for environment and startup details.

Before creating users or processing clinical content, confirm:

- database migrations are current
- Vault or the configured KEK layer is available
- app secrets are configured
- cookie/security settings match the deployment environment
- external exposure is intentional and protected
- logs do not include confidential content

## First System Admin Bootstrap

When the system is brand new, the login page may allow creation of the first system admin.

After bootstrap:

1. Complete onboarding.
2. Set up TOTP MFA.
3. Store recovery codes according to local policy.
4. Confirm the account redirects to Admin.

Bootstrap should not remain an open path after the first system admin exists.

## Create Teams

Create teams before adding normal users. Each normal user belongs to exactly one team.

Avoid linking system-admin accounts to teams unless there is a clear operational reason. A team hard-delete must block if any system-admin account remains linked to that team.

## Provision Providers

Provision provider credentials per team.

For each provider:

1. Choose team scope.
2. Enter provider metadata.
3. Enter credential once.
4. Inspect or test where supported.
5. Save only Vault-backed credential references.
6. Assign provider availability to the team.

Provider credential cleanup must not delete Vault secrets before the database commit that removes corresponding references unless compensation or retry cleanup exists.

## STT MVP Setup

For speech-to-text:

- system admins provision available endpoints and credentials
- team leaders choose active admin-provisioned service/model where allowed
- users do not see or recover raw provider secrets

If no speech service is active, users may be blocked from recording or upload workflows.

## LLM Setup

For writing assistants:

- system admins provision allowed LLM providers/models per team
- team defaults may be configured
- users choose one active LLM for their actions until changed
- invalid user selection falls back to team default

Do not expose prompts containing patient data or raw model responses in admin logs or setup screens.

## De-Identification Setup

De-identification providers are system-admin provisioned. Team leaders may select an assigned provider for their own team.

Remote de-identification endpoints must use HTTPS unless the endpoint is localhost, LAN/private, or link-local.

If no valid team de-identification selection exists, OpenScribe uses the built-in native Presidio provider.

## Security Validation Before Clinical Use

Before live clinical use, confirm:

- users complete onboarding and MFA
- team provider selections are clinically validated
- default templates and quick actions are reviewed
- tutorial and local SOP material are available
- backup and recovery process is documented
- deletion and account recovery paths are understood

## Operational Rule

Do not silently redesign privacy, ownership, deletion, encryption, provider resolution, or structured-note contracts during setup work. Escalate for architecture direction first.

