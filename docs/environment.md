# Environment variables

OpenScribe reads configuration from process environment variables. For local use, copy `.env.example` to `.env`. `start-dev.sh` sources that file directly; Docker Compose reads it for interpolation and explicitly maps supported runtime values into the application container.

The persistent Compose runtime replaces only addresses that must use Docker service DNS (`postgres`, `redis`, and `vault`) and the container's internal web bind. Other mapped values use `.env` when present and the defaults documented below when absent.

## Configuration rules

- Never commit `.env` or place real credentials in `.env.example`.
- Blank values mean intentionally unset unless a row says otherwise.
- Restart the web process, Celery worker, and Celery Beat after changing runtime settings.
- Prefer `APP_ENV`; `ENVIRONMENT` and `ENV` are compatibility fallbacks.
- Prefer `CSRF_SECRET`; `SECRET_KEY` is a compatibility alias. `APP_SECRET_KEY` is not used.
- Production must not use local Compose credentials, local Vault root tokens, seeded test accounts, or development fallback secrets.
- Proxy trust settings are independent: Uvicorn forwarded-header trust, CSRF origin reconstruction, audit client-IP trust, and rate-limit client-IP trust must each be configured deliberately.

## Application and HTTP

| Variable | Code default / example | Purpose |
| --- | --- | --- |
| `APP_ENV` | `production` when all environment selectors are unset; `.env.example` sets `local` | Runtime mode. Use `local` for local development and set `production` explicitly for deployment. |
| `ENVIRONMENT`, `ENV` | unset | Compatibility fallbacks used only when `APP_ENV` is unset. |
| `APP_HOST` | `127.0.0.1` in host tooling | Host bind used by `start-dev.sh`. Compose binds the container internally to `0.0.0.0`. |
| `APP_PORT` | `8080` | Host development port and Docker-published port. The application container listens on internal port `8080`. |
| `APP_PUBLIC_URL` | `http://127.0.0.1:8080` in local configuration | Canonical browser URL used in generated links and transactional email. |
| `APP_SOURCE_REPOSITORY_URL` | `https://github.com/AbsurdSyssie/OpenScribe` | Public repository used by `scripts/deploy-compose.sh` to build the exact commit URL. Forks must set this to their public repository. The application container does not use this variable directly. |
| `APP_SOURCE_CODE_URL` | `https://github.com/AbsurdSyssie/OpenScribe` | Source-code link shown in every application footer. Set it to complete corresponding source for the exact deployed release, including deployment modifications. Only absolute `http` or `https` URLs are used; an invalid value falls back to the default. |
| `APP_RELEASE` | Git commit when available; otherwise `unversioned build` | Text shown beside the source-code link. Set this to the immutable release identifier deployed by your build process. |
| `ALLOWED_HOSTS` | unset | Comma-separated hostnames accepted by Trusted Host middleware. In production, use exact public hosts only; wildcards are rejected. If unset, production uses the hostname from `APP_PUBLIC_URL`; startup fails if it cannot derive one. Non-production allows all hosts for local tooling. |
| `APP_HEALTHCHECK_HOST` | `127.0.0.1` locally | Host header sent by the Compose health check. In production it must equal one exact `ALLOWED_HOSTS` entry, normally the canonical public hostname. It does not create an additional allowed host. |
| `BOOTSTRAP_ADMIN_TOKEN` | unset | Deployment credential required to create the first system administrator in production. Keep it in deployment secret storage, not source control. Production hides/disables bootstrap when it is unset. |
| `CSRF_SECRET` | unset | Preferred explicit CSRF signing secret. Required in production unless stable Vault bootstrap is available. |
| `SECRET_KEY` | unset | Compatibility alias used by CSRF, audit hashing, and provider fingerprint fallback chains. Do not set it differently from `CSRF_SECRET`. |
| `CSRF_SECRET_VAULT_REF` | unset | Optional Vault KV reference for the platform CSRF secret. The default logical reference is `secret:openscribe/platform/csrf`. |
| `COOKIE_SECURE_MODE` | `auto` | `auto`, `always`, or `never`. Production startup requires `always`. |
| `HSTS_SOURCE` | `app` | `app`, `proxy`, or `proxy_static_fallback`; selects the single HSTS owner. |
| `PUBLIC_API_DOCS` | unset | Unset means public outside production and system-admin-only in production. Explicit `true` or `false` overrides this. |
| `TRUST_FORWARDED_ORIGIN_HEADERS` | `false` | Allows CSRF origin checks to reconstruct the expected origin from sanitized `X-Forwarded-Proto` and `X-Forwarded-Host`. Enable only when a trusted proxy is the sole route to the origin. |
| `AUDIT_TRUST_X_FORWARDED_FOR` | `false` | Trust the first sanitized `X-Forwarded-For` address for audit metadata. |
| `AUDIT_TRUST_CLOUDFLARE` | `false` | Trust Cloudflare's client-IP header for audit metadata. |
| `RATE_LIMIT_TRUST_X_FORWARDED_FOR` | `false` | Use the first valid `X-Forwarded-For` address for IP-keyed rate limits when Cloudflare trust is not enabled. |
| `RATE_LIMIT_TRUST_CLOUDFLARE` | `false` | Use a valid `CF-Connecting-IP` for IP-keyed rate limits. This takes precedence over `RATE_LIMIT_TRUST_X_FORWARDED_FOR`. |
| `AUDIT_SUBJECT_HASH_SECRET` | unset | Dedicated HMAC key for hashing login/reset subjects in audit events. When unset, code falls back to `SECRET_KEY`, `CSRF_SECRET`, or the stable Vault-backed CSRF secret. A dedicated production value reduces key reuse. |

`FORWARDED_ALLOW_IPS`, documented under Docker, controls which proxy addresses Uvicorn trusts. It does not by itself enable the CSRF, audit, or rate-limit trust switches above. Enable a rate-limit trust switch only after the named proxy/CDN is the sole route to the origin and overwrites that header. Otherwise a direct caller can choose another client's rate-limit bucket.

## OpenID Connect login

OIDC is off by default. Register the exact callback with the provider before enabling it. Users then link the provider from `/workspace/account`; OpenScribe does not create accounts from provider claims.

| Variable | Default | Purpose |
| --- | --- | --- |
| `OIDC_SUBJECT_HASH_SECRET` | unset | Optional environment override for the stable 32-4096-byte subject-hash key. When set, it takes precedence over Vault. Use only for local development or an existing deployment that already injects this key. Losing or changing it requires users to relink. |
| `OIDC_SUBJECT_HASH_SECRET_VAULT_REF` | `secret:openscribe/platform/oidc-subject-hash` | Vault KV-v2 reference for the shared subject-hash key. When the environment override is unset, OpenScribe reads this key or creates it once with compare-and-set. The field is `subject_hash_secret`. |
| `GOOGLE_OIDC_ENABLED` | `false` | Enables the built-in Google OIDC profile. |
| `GOOGLE_OIDC_CLIENT_ID` | unset | Google OAuth client identifier. |
| `GOOGLE_OIDC_CLIENT_SECRET` | unset | Optional environment override for the Google client secret. When set, it takes precedence over Vault. |
| `GOOGLE_OIDC_CLIENT_SECRET_VAULT_REF` | `secret:openscribe/oidc/google` | Vault KV-v2 reference for the Google client secret. The path must be exactly `openscribe/oidc/google` and the field is `client_secret`. |
| `GOOGLE_OIDC_REDIRECT_URI` | unset | Exact registered callback: `https://<host>/auth/oidc/google/callback`. Production requires HTTPS. |
| `GOOGLE_OIDC_RESPONSE_MODE` | `query` outside production; `form_post` in production | Callback mode. `form_post` requires an HTTPS redirect URI; production requires `form_post`. Use `query` for an HTTP localhost callback. |
| `MICROSOFT_OIDC_ENABLED` | `false` | Enables the built-in Microsoft identity-platform profile through the `common` tenant endpoint. |
| `MICROSOFT_OIDC_CLIENT_ID` | unset | Microsoft application client identifier. |
| `MICROSOFT_OIDC_CLIENT_SECRET` | unset | Optional environment override for the Microsoft client secret. When set, it takes precedence over Vault. |
| `MICROSOFT_OIDC_CLIENT_SECRET_VAULT_REF` | `secret:openscribe/oidc/microsoft` | Vault KV-v2 reference for the Microsoft client secret. The path must be exactly `openscribe/oidc/microsoft` and the field is `client_secret`. |
| `MICROSOFT_OIDC_REDIRECT_URI` | unset | Exact registered callback: `https://<host>/auth/oidc/microsoft/callback`. Production requires HTTPS. |
| `MICROSOFT_OIDC_RESPONSE_MODE` | `query` outside production; `form_post` in production | Callback mode. `form_post` requires an HTTPS redirect URI; production requires `form_post`. Use `query` for an HTTP localhost callback. |
| `MICROSOFT_ALLOWED_EMAIL_DOMAINS` | `nhs.net,nhs.uk,*.nhs.uk` | Comma-separated eligibility allowlist checked against signed `email`, then `preferred_username`, on linking and each login. `*.nhs.uk` permits real subdomains but not `nhs.uk` itself; the default lists both. |
| `OIDC_ENABLED` | `false` | Enables one custom OIDC provider, which may run alongside Google and Microsoft. |
| `OIDC_PROVIDER_KEY` | `oidc` | Stable lowercase audit/configuration and route label, 1-64 characters. It must differ from `google` and `microsoft` when those profiles are enabled. |
| `OIDC_PROVIDER_NAME` | `Single sign-on` | Custom provider name shown on login and account pages. |
| `OIDC_ISSUER` | unset | Exact case-sensitive issuer expected in discovery and signed ID tokens. |
| `OIDC_DISCOVERY_URL` | `<issuer>/.well-known/openid-configuration` | Optional exact custom-provider discovery URL. |
| `OIDC_CLIENT_ID` | unset | Custom provider client identifier. |
| `OIDC_CLIENT_SECRET` | unset | Optional environment override for a custom provider client secret used by `client_secret_basic` or `client_secret_post`. |
| `OIDC_CLIENT_SECRET_VAULT_REF` | `secret:openscribe/oidc/oidc` | Vault KV-v2 reference for a custom provider secret. Its exact path must be `openscribe/oidc/<OIDC_PROVIDER_KEY>` and its field is `client_secret`; change this default when changing the provider key. |
| `OIDC_CLIENT_AUTH_METHOD` | `client_secret_basic` | `client_secret_basic`, `client_secret_post`, or `none`; the custom provider must advertise the method. |
| `OIDC_REDIRECT_URI` | unset | Exact callback `https://<host>/auth/oidc/<OIDC_PROVIDER_KEY>/callback`. Production requires HTTPS. |
| `OIDC_SCOPES` | `openid profile email` | Space-separated scopes. `openid` is required. OpenScribe uses only signed issuer and `sub` claims for account identity. |
| `OIDC_RESPONSE_MODE` | `query` outside production; `form_post` in production | Callback mode. `form_post` requires an HTTPS redirect URI; production requires `form_post`. Use `query` for an HTTP localhost callback. |
| `OIDC_ALLOWED_ID_TOKEN_ALGORITHMS` | `RS256` | Comma-separated asymmetric signing-algorithm allowlist. The provider must advertise at least one listed value. Symmetric and unsigned ID tokens are rejected. |
| `OIDC_ACR_VALUES` | unset | Optional space-separated authentication-context values requested from the provider. |
| `OIDC_REQUIRED_ACR_VALUES` | unset | Optional accepted ACR values. Each value must also appear in `OIDC_ACR_VALUES`; a missing or different `acr` claim fails login. |

Discovery, authorization, token, and JWKS endpoints must use HTTPS when the configured issuer uses HTTPS. Each provider must support authorization code flow, `S256` PKCE, the chosen response mode, and its configured client authentication method. Register each callback exactly; callback paths contain the provider key. Client secrets are cached in each web process, so restart or roll the web processes after changing one in Vault.

Vault cannot invent the Google or Microsoft client secret because it must match the value issued by that provider. Store the issued value under the provider's fixed path. The app's runtime Vault identity needs `read` on those provider paths. It needs `read`, `create`, and `update` on the subject-hash path so the first enabled instance can create the shared key. Keep production operator write credentials separate from the app's runtime identity.

Google accepts any Google account, but an existing OpenScribe user must link it first. Microsoft accepts accounts whose signed email or preferred username matches the configured allowlist. Neither provider claim creates an account, matches an OpenScribe email, changes team membership, or grants an OpenScribe role.

### NHS CIS2

CIS2 publishes an OIDC discovery document and supports authorization code flow, `S256` PKCE, signed ID tokens, and registered client-secret methods. Register OpenScribe with the required CIS2 environment, then set the provider name, exact issuer and discovery URL, assigned client ID and secret, exact callback, scopes, and any ACR values agreed during onboarding. Use `form_post` and the client authentication method in the registration.

Set `OIDC_PROVIDER_KEY=nhs_cis2` and `OIDC_PROVIDER_NAME=NHS Care Identity` for a CIS2 deployment. Keep OpenScribe roles and team membership authoritative: this integration authenticates a linked CIS2 subject but does not import national RBAC or create a user. NHS England's current [CIS2 sign-in guide](https://digital.nhs.uk/services/care-identity-service/applications-and-services/cis2-authentication/integrate/design-and-build/sign-in-journey) and [discovery guide](https://digital.nhs.uk/services/care-identity-service/applications-and-services/cis2-authentication/integrate/design-and-build/discovery) contain the registration and environment details.

OpenScribe does not implement CIS2 back-channel logout or redirect local logout to the CIS2 end-session endpoint. Treat those as separate integration work if the deployment requires national single logout.

## Database, Redis, Celery, and durable dispatch

| Variable | Local default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://ambient:ambient@localhost:5432/ambient_scribe` | Primary SQLAlchemy database URL. Compose uses service host `postgres`. |
| `TEST_DATABASE_URL` | `postgresql+psycopg://ambient:ambient@localhost:5432/ambient_scribe_test` | Test database URL. It must never resolve to the application database. |
| `RATE_LIMIT_STORAGE_URL` | `redis://localhost:6379/0` | Redis store used by SlowAPI. Compose uses service host `redis`. |
| `TEST_RATE_LIMIT_STORAGE_URL` | `redis://localhost:6379/15` | Test rate-limit store. |
| `RATE_LIMIT_KEY_PREFIX` | empty | Optional SlowAPI key namespace. The test harness sets per-worker prefixes; separate deployments sharing Redis should use distinct values. |
| `CELERY_BROKER_URL` | `redis://localhost:6379/2` | Celery broker. Compose uses service host `redis`. |
| `CELERY_RESULT_BACKEND` | broker URL | Celery result backend. |
| `CELERY_LOG_LEVEL` | `INFO` | Worker and Beat log level. |
| `CELERY_TASK_ALWAYS_EAGER` | `false` | Test-only synchronous execution switch. Compose forces `false`. |
| `TASK_OUTBOX_MAX_ATTEMPTS` | `10` | Maximum broker-publication attempts before a durable task-dispatch row becomes failed. Retry delay starts at 10 seconds and is capped at one hour. |

Celery Beat publishes pending task-dispatch outbox rows every second. Transcript-root retention, 24-hour failed-ingestion source expiry, transcript-audio cleanup, six-month security-audit expiry, legal-document retention, provider-secret cleanup, and quota lifecycle processing run every 10 seconds. These schedules are code constants, not environment settings.

Redis append-only persistence protects queued work, result data, and rate-limit state across normal restarts. PostgreSQL remains authoritative and Redis is not a substitute for database backups.

## Vault and application cryptography

| Variable | Local default | Purpose |
| --- | --- | --- |
| `VAULT_ADDR` | `http://127.0.0.1:8200` | Vault API address. Compose uses `http://vault:8200`. |
| `VAULT_TOKEN` | code fallback `root`; `.env.example` leaves it blank | Direct Vault token. Do not rely on the code fallback outside the local development server. |
| `VAULT_TOKEN_FILE` | local bootstrap file when available | File containing the Vault token. Compose uses `/app/.local/vault/root-token`. |
| `VAULT_KV_MOUNT` | `secret` | KV-v2 mount for provider and platform secrets. |
| `VAULT_TRANSIT_MOUNT` | `transit` | Transit mount used to wrap and unwrap user-content DEKs. |
| `VAULT_USER_CONTENT_KEK_KEY_NAME` | `openscribe-user-content-kek` | Transit key used for user-content DEKs. |
| `LOCAL_VAULT_WAIT_TIMEOUT_SECONDS` | `90` | Maximum local bootstrap wait for Vault. |
| `LOCAL_VAULT_WAIT_RETRY_INTERVAL_SECONDS` | `1` | Local Vault bootstrap retry interval. |
| `PROVIDER_CREDENTIAL_FINGERPRINT_SECRET` | development fallback when unset | HMAC key used only for non-reversible STT duplicate-credential fingerprints. Set a stable secret in production so fingerprints do not depend on compatibility keys or the development fallback. |

The local bootstrap stores a root token and unseal key and is suitable only for a controlled local or single-host development environment. Production should pre-provision Vault mounts and keys and inject least-privilege runtime credentials. PostgreSQL, Vault storage, and Vault bootstrap material form one recoverable encrypted-content set and must be backed up consistently.

## Transactional email

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAIL_TRANSPORT` | `disabled` | `disabled`, `stdout`, or `resend`. `stdout` is restricted to local/test environments. |
| `MAIL_FROM_ADDRESS` | `no-reply@example.com` in sample/Compose | Sender address; required when mail is enabled. |
| `MAIL_FROM_NAME` | `OpenScribe` | Sender display name. |
| `MAIL_REPLY_TO` | unset | Optional reply-to address. |
| `RESEND_API_KEY` | unset | Plaintext Resend key; use only in controlled local development or inject it as a deployment secret. |
| `RESEND_API_KEY_VAULT_REF` | unset | Vault reference for a Resend key. |

`APP_PUBLIC_URL` is also required when mail is enabled so setup and recovery links point at the correct instance.

## Request rate limits, quotas, and retention

| Variable | Default | Purpose |
| --- | --- | --- |
| `LIVE_CHUNK_UPLOAD_RATE_LIMIT` | `1/second` | Live audio chunk request rate limit. |
| `WHOLE_FILE_UPLOAD_BURST_RATE_LIMIT` | `1/5 seconds` | Whole-file burst request limit. |
| `WHOLE_FILE_UPLOAD_DAILY_RATE_LIMIT` | `100/day` | Whole-file daily request limit. |
| `LLM_GENERATION_BURST_RATE_LIMIT` | `20/3 minutes` | LLM generation burst limit. |
| `LLM_GENERATION_DAILY_RATE_LIMIT` | `200/day` | LLM generation daily limit. |
| `LIVE_CHUNK_HOURLY_DURATION_LIMIT_SECONDS` | `3600` | Hourly live-audio duration safeguard. |
| `WHOLE_FILE_HOURLY_UPLOAD_BYTES` | `209715200` | Hourly whole-file byte safeguard (200 MiB). |
| `WHOLE_FILE_HOURLY_DURATION_LIMIT_SECONDS` | `14400` | Hourly whole-file duration safeguard (4 hours). |
| `MAX_RETENTION_DAYS` | `90` | Maximum team default retention accepted by management services. Minimum is fixed at one day. |

Authentication limits are fixed in code: login `5/5 minutes`, MFA `10/10 minutes`, account-security changes `5/5 minutes`, and public account requests `3/hour`.

## Audio ingestion and generation deadlines

| Variable | Default | Purpose |
| --- | --- | --- |
| `WHOLE_FILE_MAX_UPLOAD_BYTES` | `209715200` | Maximum individual upload size (200 MiB). |
| `LIVE_CHUNK_MAX_UPLOAD_BYTES` | `25165824` | Maximum individual live audio chunk (24 MiB). The browser rolls WAV chunks at 22 MiB and retains transport margin. |
| `WHOLE_FILE_MAX_DURATION_SECONDS` | `14400` | Maximum normalized individual duration (4 hours). |
| `AUDIO_FFPROBE_TIMEOUT_SECONDS` | `15` | Audio duration probe timeout. |
| `AUDIO_FFMPEG_TIMEOUT_SECONDS` | `1800` | Audio normalization timeout. |
| `STT_TRANSCRIPTION_TIMEOUT_SECONDS` | `14400` | Timeout for a synchronous provider transcription call. |
| `LIVE_CHUNK_PROCESSING_STALE_AFTER_SECONDS` | `600` | Age after which a processing live-chunk job may be reconciled as stale. |
| `INGESTION_RESERVATION_VALIDITY_SECONDS` | `900` | Initial provider quota reservation validity for ingestion. |
| `INGESTION_PROVIDER_DEADLINE_SECONDS` | at least `STT_TRANSCRIPTION_TIMEOUT_SECONDS + 300`; default `14700` | Hard provider-attempt deadline. Values below the STT timeout plus five minutes are raised to that minimum. |
| `GENERATION_WAIT_FOR_TRANSCRIPT_TIMEOUT_SECONDS` | `120` | Maximum period a queued generation waits for transcript ingestion before failing. |

The Docker image installs `ffmpeg` and `ffprobe`; both are required for whole-file inspection and normalization.

Application readers reject uploads beyond these limits without building an unbounded in-process buffer. Configure a matching request-body limit at the reverse proxy/CDN as well, because it receives the request before the application.

## Provider rollout controls

| Variable | Default | Purpose |
| --- | --- | --- |
| `ENABLE_GEMINI_ENTERPRISE_PROVIDER` | `true` | When false, hides and rejects new Gemini Enterprise setup. Existing persisted Gemini configs remain available to authorized runtime paths. |

Google SDK identity is configured through standard Google variables rather than OpenScribe secrets. Do not place credential JSON in `.env`. For the optional Docker ADC override, set `GOOGLE_ADC_HOST_FILE` in the host shell and include `docker-compose.adc.yml`; it mounts one file read-only and sets `GOOGLE_APPLICATION_CREDENTIALS` inside the container. See [docker.md](docker.md) and [gemini-enterprise-setup.md](gemini-enterprise-setup.md).

## Dependency installation

`requirements.txt` pins the runtime `python-multipart`, `idna`, and Authlib versions and installs the pinned `en_core_web_sm` 3.8.0 wheel directly. Authlib handles OAuth/OIDC protocol and JOSE validation; do not replace it with custom token parsing. Do not run a separate `spacy download` command after installing requirements. The Docker smoke workflow runs `pip-audit` against `requirements.txt`.

## Persistent Docker runtime

| Variable | Default | Purpose |
| --- | --- | --- |
| `DOCKER_APP_BIND` | `127.0.0.1` | Host interface on which Compose publishes the web port. |
| `DOCKER_SEED_TEST_ACCOUNTS` | `false` | Seed reusable local accounts during application-container startup. Keep disabled on persistent or shared instances. |
| `CELERY_WORKER_CONCURRENCY` | `1` | Concurrency for the single local runtime worker consuming all queues. |
| `CONTAINER_STARTUP_TIMEOUT_SECONDS` | `90` | Wait for PostgreSQL and Redis before bootstrap. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxy IPs whose forwarded headers Uvicorn accepts. Do not use `*` unless direct origin access is blocked. |

See [docker.md](docker.md) for migration, startup, persistence, backup, and reverse-proxy instructions.

## Local demo bootstrap

The isolated demo Compose file sets these values. They are not normal deployment settings.

| Variable | Demo value | Purpose |
| --- | --- | --- |
| `DEMO_TEAM_NAME` | `OpenScribe Demo Team` | Name of the seeded team. |
| `DEMO_ADMIN_EMAIL` | `admin@openscribe.local` | Fixed system-administrator login. |
| `DEMO_LEADER_EMAIL` | `leader@openscribe.local` | Fixed team-leader login. |
| `DEMO_CLINICIAN_EMAIL` | `clinician@openscribe.local` | Fixed clinician login and owner of the synthetic consultation. |
| `DEMO_PASSWORD` | `OpenScribeLocal27` | Fixed password for all three local accounts. |
| `DEMO_BOOTSTRAP_ENABLED` | `true` | Explicitly permits the demo seed. The demo Compose file sets this only for its one-shot seed service. |
| `DEMO_BOOTSTRAP_MARKER` | `/app/.local/demo/bootstrap-complete` | Optional marker path used by tests or alternate local packaging. The demo Compose file uses the default path in its seed-state volume. |

The seed refuses to run unless `APP_ENV` is local or development and `DEMO_BOOTSTRAP_ENABLED` is true. The demo Compose file supplies both values, so its normal start command needs no extra flags. The seed writes its marker only after accounts, encrypted synthetic content, Presidio redaction, and the example draft succeed. The marker records the seeded team ID. Later starts confirm that the marker belongs to the current PostgreSQL data set, then leave the database unchanged. See [local-demo.md](local-demo.md).

## Host development only

These variables are consumed by `start-dev.sh` and are not application runtime policy:

- `DEV_START_CELERY`
- `DEV_START_BRAVE`
- `DEV_ALLOW_REMOTE_BIND`
- `DEV_FORWARDED_ALLOW_IPS` (defaults to `127.0.0.1`; set an exact proxy address only when the proxy is the sole path to the host server)
- `DEV_ALLOW_REMOTE_SERVICE_EXPOSURE`
- `DEV_RESTART_EXISTING_PROCESSES`
- `DEV_PURGE_CELERY_QUEUE`
- `DEV_SEED_TEST_ACCOUNTS`
- `DEV_TEST_TEAM_NAME`
- `DEV_TEST_ADMIN_EMAIL`, `DEV_TEST_ADMIN_PASSWORD`
- `DEV_TEST_LEADER_EMAIL`, `DEV_TEST_LEADER_PASSWORD`
- `DEV_TEST_USER_EMAIL`, `DEV_TEST_USER_PASSWORD`

`OWASP_LIFECYCLE_*` and `HALLUCINATION_CHECK_DEBUG_UI` are security/testing helpers. Leave them blank or disabled during normal runtime use.
