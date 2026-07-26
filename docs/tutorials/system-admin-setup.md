# System Administrator Setup Tutorial

## Audience

This guide is for the operator establishing a new OpenScribe instance and creating its first system-administrator account. Day-to-day browser administration is covered in [admin.md](admin.md).

## Choose a runtime

For host-based development, use [../setup.md](../setup.md).

For the persistent single-host container runtime, use [../docker.md](../docker.md):

```bash
cp .env.example .env
docker compose --profile runtime up -d --build
docker compose --profile runtime ps
curl --fail http://127.0.0.1:8080/health
```

The persistent profile starts PostgreSQL, Redis, Vault, FastAPI, a Celery worker, and Celery Beat. It is a restartable local/small-host baseline, not a complete production architecture.

Before exposing OpenScribe or processing real clinical content, read:

- [../environment.md](../environment.md)
- [../security.md](../security.md)
- [../auth.md](../auth.md)

## Configure the environment

Local sample settings are intentionally development-oriented. Production must explicitly configure:

- `APP_ENV=production`;
- `COOKIE_SECURE_MODE=always`;
- a stable explicit or Vault-backed CSRF secret;
- the public HTTPS `APP_PUBLIC_URL`;
- one intentional HSTS owner through `HSTS_SOURCE`;
- trusted reverse-proxy addresses through `FORWARDED_ALLOW_IPS`;
- forwarded-origin/audit header trust only when the proxy sanitizes those headers and direct origin access is blocked;
- production database, Redis, Vault, mail, and provider identities/secrets;
- backup, restore, monitoring, and incident procedures.

Do not reuse the local Compose PostgreSQL password, local Vault root token/unseal material, seeded test accounts, or development fallback secrets in production.

## Verify infrastructure

Before bootstrap:

1. confirm migrations completed successfully;
2. confirm `/health` returns success;
3. confirm PostgreSQL, Redis, and Vault are healthy;
4. confirm the Celery worker consumes `control`, `generation`, `ingestion`, and default queues;
5. confirm Celery Beat is running;
6. confirm Vault KV-v2 and Transit mounts/key are available;
7. confirm the application can resolve a stable CSRF signing key;
8. confirm logs contain operational metadata only;
9. confirm database and Vault data can be backed up and restored together.

PostgreSQL and Vault form one encrypted-content recovery set. A database restore without the matching Vault key/material can make content unreadable.

## Bootstrap the first system administrator

When the database contains zero users, `/login` exposes the first-admin bootstrap form.

1. Open `/login`.
2. Create the first system administrator.
3. Set the permanent password required by onboarding.
4. Enroll TOTP MFA.
5. Store optional recovery codes according to local policy.
6. Confirm the account is redirected to `/admin`.

Bootstrap closes after the first user exists. Do not seed local development accounts on a shared/production instance.

## Create teams

Normal users and team leaders belong to exactly one team. System administrators can remain teamless.

For each team:

- use the correct display name and retention policy;
- keep the default retention within the configured `MAX_RETENTION_DAYS`;
- understand that a transcript snapshots the team retention at creation/start;
- avoid attaching system-administrator accounts unless operationally necessary, because team deletion blocks while a system administrator remains linked;
- identify the initial team leader before inviting clinical users.

## Configure transactional email

Email enables activation/setup, self-service password reset, and manager-assisted recovery links.

Supported transports:

- `disabled`;
- `stdout` for local/test only;
- `resend`.

For production Resend configuration, set the verified sender/public URL and inject the API key through a deployment secret or a provisioned `RESEND_API_KEY_VAULT_REF`. Test with:

```bash
python scripts/send_test_email.py --to you@example.com
```

When mail is disabled, public self-service reset is unavailable and managers must use approved out-of-band or break-glass procedures.

## Provision speech-to-text

Open the selected team's STT setup in `/admin`.

1. Choose a supported provider preset.
2. Enter endpoint/identity metadata and the credential once.
3. Inspect/discover the contract and models.
4. Finalize a ready configuration.
5. Run the saved diagnostic with the bundled synthetic audio fixture.
6. Activate the config.
7. Have the team leader select it for the intended purpose.
8. Test owner whole-file and live capture with synthetic/approved material.

Consultation and post-consultation dictation selections can be purpose-specific. Verify both when the team uses both workflows.

Credentials are stored in Vault and represented in PostgreSQL by metadata/reference/fingerprint only. See [../stt-config.md](../stt-config.md).

## Provision writing assistants

Open the selected team's LLM setup in `/admin`.

- Choose the provider preset/adapter and team.
- Supply the required credential or workload identity.
- Inspect or enter a supported model.
- Finalize/activate the configuration.
- Configure the team default/allowed selection policy.
- Test generation using synthetic transcript/working-note material.

Gemini Enterprise uses Google workload identity/ADC rather than a stored bearer token. Its setup can be hidden/rejected for new configs with `ENABLE_GEMINI_ENTERPRISE_PROVIDER=false`; existing persisted configs remain usable by authorized runtime paths. See [../llm-providers.md](../llm-providers.md) and [../gemini-enterprise-setup.md](../gemini-enterprise-setup.md).

## Configure de-identification and clinical NLP

System administrators provision/assign remote providers. Team leaders choose from assigned selectable options for their own team.

- Remote production endpoints require HTTPS.
- Local/private HTTP exceptions are development-only.
- When no valid remote de-identification selection exists, the runtime can use the built-in native Presidio path.
- Validate redaction/reidentification behavior with synthetic identifiable text before clinical rollout.

Provider connectivity alone does not establish privacy/regulatory suitability.

## Create initial users and shared assets

Use account requests, activation links, or managed creation.

- Give the team-leader role only with approved management responsibility.
- Never promote a normal user to system administrator through a leader flow.
- Require permanent password change and TOTP onboarding.
- Create/review default and Team Templates/Quick Actions with synthetic content only.
- Validate structured EMIS section keys and prompt instructions.
- Train users to treat all transcripts/generated text as drafts requiring review.

## Validate security and lifecycle behavior

Before real use, perform controlled checks for:

- anonymous/onboarding/pending-MFA/full/leader/system-admin route boundaries;
- owner-only transcript/generated-content access;
- account suspension/reactivation/recovery/deletion;
- CSRF, same-origin, cookie, CSP, cache, and HSTS behavior behind the actual proxy;
- provider credential replacement/removal and cleanup;
- temporary source-audio cleanup;
- retention expiry/deletion;
- durable outbox behavior when Redis publication is temporarily unavailable;
- quota/usage metadata without content leakage;
- backup and full restore of PostgreSQL plus Vault.

Use [../testing.md](../testing.md) and [../dbtesting.md](../dbtesting.md). Dated compliance evidence is a point-in-time record and must be rerun for the deployed build rather than treated as evergreen proof.

## Production readiness checklist

Do not begin clinical use until the deployment owner has documented:

- controller/processor responsibilities and subprocessor contracts;
- provider data residency, retention, training/use, and deletion terms;
- recording consent and clinical safety procedures;
- access review and account recovery processes;
- backup/restore and key-loss response;
- monitoring for web/worker/Beat/Vault/database/Redis health;
- retention, source-audio, and provider-secret cleanup monitoring;
- audit review and incident escalation;
- tested destructive user/team/transcript deletion procedures;
- clinician training and review requirements.

## Architecture rule

Setup work must not silently change owner-only access, system-admin content ownership, hard-delete/retention roots, DEK/KEK behavior, provider-secret versioning/cleanup, quota/outbox semantics, or structured-note contracts. Such changes require an explicit design, code/migrations/tests, and updates to the operational documentation and repository README.
