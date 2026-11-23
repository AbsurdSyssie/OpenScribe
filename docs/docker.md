# Persistent Docker Runtime

The `runtime` Compose profile runs OpenScribe as a restartable single-host stack. It replaces `start-dev.sh` when the application must continue after a shell closes and return after host/Docker daemon restart.

The profile starts:

- PostgreSQL with a named data volume;
- Redis with append-only persistence;
- Vault with a named storage volume;
- an isolated local Vault bootstrap/unseal supervisor with the privileged bootstrap mount;
- one OpenScribe container that applies migrations and supervises FastAPI, Celery workers, one Celery Beat, and scoped Vault token renewal (one combined worker by default).

This preserves the current local architecture. It is not a production reference topology: bundled database credentials and persistent local Vault root/unseal material must not be used as production secrets, and production workers should be isolated by queue after checking host capacity.

## Prerequisites

- Docker Engine with Compose v2 (`docker compose`);
- Buildx is recommended; legacy builder fallback can still complete with a deprecation warning.

## First start

```bash
cp .env.example .env
# Review APP_PUBLIC_URL, ALLOWED_HOSTS, BOOTSTRAP_ADMIN_TOKEN, secrets, mail, proxy trust, and Docker settings.
scripts/deploy-compose.sh
```

The deployment script accepts only a clean commit available on the current branch's upstream. It passes the commit SHA and its source URL to the recreated application container. Run the same command for every release.

The web application publishes on `127.0.0.1:8080` by default.

The runtime Uvicorn command caps each inbound WebSocket message at 64 KiB and
the per-connection receive queue at four messages. Direct Uvicorn launches
outside `docker/entrypoint.sh` should use `--ws-max-size 65536
--ws-max-queue 4` as well. The live STT route accepts audio frames up to 32,013
bytes and small JSON controls; configure a reverse proxy to pass WebSocket
upgrades and allow at least that frame size. Proxy limits are separate from
Uvicorn's limits.

```bash
docker compose --profile runtime ps
docker compose logs -f openscribe
curl --fail http://127.0.0.1:8080/health
```

The separate `vault-bootstrap` service initializes or unseals local Vault, ensures KV-v2 and the existing Transit key, and provisions the `openscribe-runtime` policy and a renewable 24-hour orphan token. Repeated bootstrap preserves a valid scoped token. Its steady-state supervisor checks for a sealed Vault every five seconds and unseals after a Vault/host restart; it does not continuously provision policy. Only this service mounts `vault_bootstrap`.

The bootstrap container health check runs `scripts/check_local_vault.py`. This small standard-library probe reads only the scoped runtime token, calls Vault's token self-lookup directly, and succeeds only when the policy set is exactly `openscribe-runtime` and the token TTL is positive. Invalid credentials, malformed identity data, and unavailable Vault produce a generic unhealthy result; the token and response body are not printed. This health check is intentionally separate from bootstrap/provisioning work.

On application startup the entrypoint drops to UID/GID `10001`, waits for PostgreSQL/Redis, checks scoped token renewal, applies migrations, optionally seeds accounts, and starts the web, worker, Beat, and token-renewal processes. The application mounts only `vault_runtime` read-only, never the root token or unseal key. Token renewal runs at most hourly, and a renewal failure stops the supervised runtime so it fails visibly. A scoped token that expired during extended downtime is replaced by restarting `vault-bootstrap` before the application.

The runtime policy allows the existing provider-secret and retry-audio paths, platform CSRF/subject-hash secrets, OIDC credential reads, and only data-key/decrypt/rewrap operations on the configured Transit key. It cannot manage mounts, policies, tokens, or Transit keys. Custom CSRF, OIDC subject-hash, and Resend references grant only the configured exact KV-v2 paths; keep their mount equal to `VAULT_KV_MOUNT`, and recreate bootstrap with `LOCAL_VAULT_FORCE_PROVISION=true` after changing them, then remove that switch. Other custom deployment references require explicit operator policy review.

The container `/tmp` is a bounded tmpfs with `noexec,nosuid,nodev`, configurable through `DOCKER_TMPFS_SIZE` (default `1g`, or 1 GiB). One default maximum four-hour mono 16 kHz PCM conversion produces about 440 MiB, alongside up to 200 MiB of source audio and an overlapping request spool of up to 200 MiB. The default accommodates that single-job footprint plus modest temporary overhead; increase it for concurrent uploads/conversions or higher audio limits. Size host/container RAM for the occupied tmpfs plus application/worker buffers, PostgreSQL, Redis, and Vault; a ceiling is not reserved memory. Disable host swap or use encrypted swap so plaintext temporary content cannot reach swap storage. Exhaustion fails requests rather than spilling temporary clinical data into the writable container layer.

Celery Beat also stores its replaceable schedule state under `/tmp`; it rebuilds that state after container recreation. PostgreSQL, Redis, and Vault retain their existing separate named volumes, and the tmpfs does not change database persistence. Audio inspection and normalization use private temporary directories that remove source/output files on completion and on write, staging, or subprocess failure.

Set `CELERY_WORKER_MODE=isolated` to start separate prefork workers for `control`, `generation`, and `ingestion`. Their concurrency defaults are 1, 2, and 1; configure `CELERY_CONTROL_CONCURRENCY`, `CELERY_GENERATION_CONCURRENCY`, and `CELERY_INGESTION_CONCURRENCY` with positive integers. The default `combined` mode uses `CELERY_WORKER_CONCURRENCY=1`. Invalid modes or concurrency values stop startup before bootstrap. Both modes use prefetch multiplier 1, run bootstrap/web/Beat once, and stop all children if any supervised process exits or the container receives a termination signal.

Ten generation slots are configurable but require measured CPU, memory, and provider capacity. Do not enable them blindly on a 1 CPU / 2 GB host. This change does not resize or reconfigure any production host. See [environment.md](environment.md) for settings.

If any supervised long-running process exits, the container stops and `restart: unless-stopped` restarts the complete runtime consistently.

The entrypoint never purges Redis/Celery queues. Queued work resumes after restart. Beat publishes pending durable task-dispatch rows every second and runs transcript-root retention, failed-ingestion source expiry, Telehealth capture reconciliation, transcript-audio cleanup, security-audit expiry, legal-document retention, provider-secret cleanup, and quota lifecycle processing every 10 seconds.

## Common operations

```bash
# Status
docker compose --profile runtime ps

# Follow web, worker, and Beat output
docker compose logs -f openscribe

# Restart only the application runtime
docker compose restart openscribe

# Rebuild after code/dependency changes
docker compose --profile runtime up -d --build

# Stop while preserving named volumes
docker compose --profile runtime down

# Start again without rebuilding
docker compose --profile runtime up -d
```

Do not add `--volumes` unless PostgreSQL, Redis, Vault, and bootstrap data should be destroyed intentionally.

## Migrating from `start-dev.sh`

The host development flow and runtime profile share the same Compose project. Existing PostgreSQL and Vault named volumes carry over. Wrapped user DEKs/provider credentials remain usable only when matching PostgreSQL, Vault storage, and bootstrap material are kept together.

Redis did not originally use a named volume. Its old container-local queue/result/rate-limit state does not automatically migrate when Compose creates `redis_data`.

Before the first runtime start:

1. Stop the host FastAPI server, Celery worker, and Celery Beat. Running both workflows competes for `APP_PORT` and queues, and multiple Beat processes duplicate periodic work.
2. Copy host Vault bootstrap files into `vault_bootstrap` when the existing `vault_data` was initialized by `start-dev.sh`:

```bash
docker compose --profile runtime build openscribe

docker compose --profile runtime run --rm --no-deps \
  --volume "$PWD/.local/vault:/host-bootstrap:ro" \
  --entrypoint sh \
  vault-bootstrap \
  -c 'install -o 10001 -g 10001 -m 600 /host-bootstrap/root-token /app/.local/vault/root-token && install -o 10001 -g 10001 -m 600 /host-bootstrap/unseal-key /app/.local/vault/unseal-key'
```

The command copies without printing values. Skip only when Vault has never been initialized on this host.

On first start, Compose can recreate dependency containers. PostgreSQL/Vault named volumes persist. `redis_data` starts empty and does not copy an earlier unpersisted Redis container; drain or accept loss of important queued work/results before migration.

## Upgrading the Vault identity boundary

This is an operator-coordinated cutover; repository verification does not perform production changes. Back up matching PostgreSQL, Vault storage, and bootstrap material first. Preserve the existing Compose project/volume names, Transit mount/key, wrapped DEKs, and database secret references. Never initialize a replacement Vault to complete an upgrade. Bootstrap refuses initialization when root, unseal, or scoped-token files already exist; restore matching storage instead of deleting those recovery files.

Stop the old application runtime, build the new images, and start `vault-bootstrap` with the existing `vault_bootstrap` and `vault_data` volumes. Confirm its health without printing tokens, then start the application and check `/health`. Use a synthetic owned record to confirm existing encrypted content remains readable and new content can be created/deleted. Confirm the application has only the read-only scoped-token mount; verify scoped identity denial of policy/key management from an operator environment without printing credentials. Restart Vault and the stack to verify unseal and renewal before declaring rollout complete.

Rollback keeps the same database/Vault volumes and restores the previous application image and Compose definition. Stop the new application before starting the old one. The previous root token remains available only in the bootstrap volume during the rollback window; the scoped token and policy do not change existing keys or ciphertext. Never delete a live credential during rollback.

Retirement of privileged credentials is operator-controlled after validation and rollback acceptance. Revoke any previously distributed root tokens and remove old application mounts/copies. The local supervisor supports ordinary restarts using only unseal material and a healthy scoped lease. Expired/revoked scoped tokens and policy changes still require privileged provisioning. Before retiring the bootstrap root token, establish an operator-managed provisioning identity/workflow for those events; root retirement does not remove the need for recoverable unseal material. Keep recoverable offline unseal material and an audited emergency recovery procedure. Production policy/identity changes and credential retirement are not automated by the application.

## Persistent data

| Volume | Contents |
| --- | --- |
| `postgres_data` | Application database and migration state. |
| `redis_data` | Celery broker/results and rate-limit state. |
| `vault_data` | Vault file storage, KV secrets, Transit key state. |
| `vault_bootstrap` | Local Vault root token and unseal key, mounted only by the bootstrap supervisor. |
| `vault_runtime` | Scoped renewable token; application mount is read-only. |

Back up and restore `postgres_data`, `vault_data`, and `vault_bootstrap` as one encrypted-content recovery set. Database rows contain wrapped user keys requiring the corresponding Vault Transit state, and the isolated local bootstrap supervisor requires bootstrap material to unseal Vault. The runtime token is replaceable without changing encrypted data.

Include `redis_data` when queued work/result/limiter continuity matters, but PostgreSQL remains authoritative and Redis is not a database backup.

Do not wipe/reinitialize Vault while retaining encrypted application data unless following the explicit unreadable-content recovery procedure.

## Compose configuration

Compose reads `.env` for interpolation and explicitly maps supported runtime variables. It overrides addresses that must use Docker service DNS:

- `DATABASE_URL` -> `postgres:5432`;
- limiter storage -> `redis:6379/0`;
- Celery broker/result backend -> `redis:6379/2`;
- `VAULT_ADDR` -> `vault:8200`;
- `VAULT_TOKEN_FILE` -> `/app/.local/vault-runtime/token`;
- web bind -> internal `0.0.0.0:8080`.

`APP_PORT` controls the published host port:

```env
APP_PORT=8090
APP_PUBLIC_URL=http://127.0.0.1:8090
```

Then recreate:

```bash
docker compose --profile runtime up -d
```

See [environment.md](environment.md) for all mapped settings and code defaults.

## OIDC secrets in Vault

Google and Microsoft issue their own OAuth client secrets. Vault stores those values; it cannot generate replacements that the providers will accept. OpenScribe does generate its separate subject-hash key on first use and stores it at `secret:openscribe/platform/oidc-subject-hash` with KV-v2 compare-and-set.

For the bundled local Vault, store each issued client secret through the hidden prompt. The command neither accepts the value as an argument nor prints it:

```bash
VAULT_TOKEN_FILE=.local/vault/root-token .venv/bin/python scripts/set_oidc_client_secret.py google
VAULT_TOKEN_FILE=.local/vault/root-token .venv/bin/python scripts/set_oidc_client_secret.py microsoft
```

These host-development commands explicitly use bootstrap authority for this operator-only write; they do not change the application environment. For the Compose-managed Vault, run the command inside `vault-bootstrap` with `VAULT_TOKEN_FILE=/app/.local/vault/root-token`, not inside the application container.

In production, run the same command only from an operator environment with a short-lived Vault write token. Do not grant provider-secret write access to the OpenScribe runtime identity. That identity needs the following KV-v2 policy when both providers are enabled:

```hcl
path "secret/data/openscribe/platform/oidc-subject-hash" {
  capabilities = ["create", "read", "update"]
}

path "secret/data/openscribe/oidc/google" {
  capabilities = ["read"]
}

path "secret/data/openscribe/oidc/microsoft" {
  capabilities = ["read"]
}
```

Keep client IDs, callback URLs, enable switches, and Vault references in deployment configuration. Leave `GOOGLE_OIDC_CLIENT_SECRET`, `MICROSOFT_OIDC_CLIENT_SECRET`, and `OIDC_SUBJECT_HASH_SECRET` empty. The references are locators, not credentials. Restart or roll all web processes after changing a provider secret because each process caches it until exit.

Never delete or replace the subject-hash secret independently of the database. Existing linked identities depend on it and would otherwise need to be linked again.

## Google Application Default Credentials

Gemini Enterprise can use ADC. Do not copy credential JSON into the image or `.env`.

The optional `docker-compose.adc.yml` mounts one host file read-only into the application container and sets `GOOGLE_APPLICATION_CREDENTIALS`:

```bash
export GOOGLE_ADC_HOST_FILE="$HOME/.config/gcloud/application_default_credentials.json"

docker compose \
  -f docker-compose.yml \
  -f docker-compose.adc.yml \
  --profile runtime \
  up -d --build
```

Verify identity resolution without printing a token/credential:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.adc.yml \
  exec openscribe \
  python -c 'import google.auth; c,p=google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"]); print(type(c).__name__, p, c.quota_project_id)'
```

Application processes run as UID `10001`; the mounted file must be readable by that UID. Do not make it world-readable. Production should prefer attached workload identity/Workload Identity Federation over a user ADC refresh-token file.

## Reverse proxy and network exposure

Safe default:

```env
DOCKER_APP_BIND=127.0.0.1
```

A host reverse proxy can connect to loopback. When a proxy in another container/private network must connect, bind deliberately and firewall the origin.

Configure all relevant boundaries independently:

- `APP_PUBLIC_URL` = public HTTPS origin;
- `ALLOWED_HOSTS` = exact public hostnames, with no wildcard. If the edge redirects `www` to the canonical host, list only the canonical host; otherwise list both exact hosts;
- `APP_HEALTHCHECK_HOST` = one exact `ALLOWED_HOSTS` entry, normally the canonical public hostname. Compose sends this Host header to the loopback health endpoint rather than weakening host validation for localhost;
- `APP_ENV=production`;
- `COOKIE_SECURE_MODE=always`;
- `BOOTSTRAP_ADMIN_TOKEN` = a deployment secret available only until the first system administrator is created;
- one HSTS owner through `HSTS_SOURCE`;
- `FORWARDED_ALLOW_IPS` = actual trusted proxy addresses accepted by Uvicorn;
- `TRUST_FORWARDED_ORIGIN_HEADERS=true` only when that proxy sanitizes forwarded host/proto and direct origin access is blocked;
- `AUDIT_TRUST_X_FORWARDED_FOR`/`AUDIT_TRUST_CLOUDFLARE` only for the expected sanitizing proxy/CDN path.
- `RATE_LIMIT_TRUST_CLOUDFLARE=true` only after Cloudflare is the sole route to the origin and overwrites `CF-Connecting-IP`; use `RATE_LIMIT_TRUST_X_FORWARDED_FOR=true` only for an equivalent trusted proxy that overwrites `X-Forwarded-For`. Cloudflare takes precedence when both are true.
- request-body limits at the reverse proxy/CDN that include multipart framing: by default 201 MiB for whole-file uploads, 25 MiB for live/recovery audio, and 2 MiB for imports. Follow [upload resource limits](security.md#upload-resource-limits) for route ceilings, temporary-storage bounds and chunked-request proxy buffering controls.

These switches are separate. Trusting Uvicorn forwarded headers does not automatically trust them for CSRF origin reconstruction, audit client IP, or rate-limit client IP.

Do not use `FORWARDED_ALLOW_IPS=*` on an origin that is directly reachable.

### Access logging at the ingress

Uvicorn access logging is disabled by the production and development launchers
because its default format includes the request target and referrer. Keep
request targets, query strings, referrers, headers, and bodies out of ingress
and centralized logs too. For Nginx, define a minimal format and explicitly
use it on the OpenScribe virtual host:

```nginx
log_format openscribe_safe '$remote_addr [$time_local] $request_method $status $body_bytes_sent';

server {
    # ... TLS and proxy configuration ...
    access_log /var/log/nginx/openscribe_access.log openscribe_safe;
    # Nginx error logs have no configurable safe format and can include request lines.
    error_log /dev/null;
    # ... locations ...
}
```

Nginx does not provide a configurable error-log format. Route this virtual
host's error log to `/dev/null` so failed requests cannot copy raw request
lines or referrers into a host or cloud log. Keep the safe access log for
status, byte-count, and request-rate diagnostics, and monitor Nginx process
health and upstream availability separately. Check the complete Nginx
configuration for inherited or duplicate `error_log` destinations and ensure
the deployment's log agent does not collect a broader Nginx error log.

Validate and reload Nginx (`nginx -t` then `systemctl reload nginx`). Confirm
the configured log destination and any CloudWatch/other cloud agent input point
to this safe log, and remove any access-log input that still collects the
default Nginx or application format. After rollout, send a synthetic request
with a unique query-string marker and a unique `Referer` marker through the
public ingress. Confirm the request succeeds and that neither marker appears
in the Nginx access log, application logs, or the corresponding centralized
cloud log stream. Do not use real user data for this check.

Also verify failure and size-limit behavior in a staging deployment. Use a
staging-only location that deliberately returns an error response, then send a
synthetic request containing unique query-string and `Referer` markers through
the CDN and directly to the restricted origin. Repeat with a synthetic body
one byte larger than the configured ingress limit to confirm the expected
413 response at both paths. Check Nginx, application, CDN, and centralized
cloud logs: the status and byte metrics should remain available in the safe
access log, while none of the markers or raw request targets appear. Keep the
failure location and test credentials unavailable in production, use no
clinical or real account data, and remove the staging test setup after the
check.

## Test-account seeding

Persistent runtime does not seed accounts by default. For a private localhost disposable instance:

```env
DOCKER_SEED_TEST_ACCOUNTS=true
```

`DEV_TEST_*` values control seeded account data. Disable before adding real/shared data. Seeded accounts are localhost-only in application policy and are not production accounts.

## Troubleshooting

### Vault initialized but bootstrap files missing

Existing initialized `vault_data` requires the matching unseal material when Vault is sealed. A healthy renewable runtime lease can restart without a root token; missing privileged credentials block provisioning or replacement of runtime authority, rather than an otherwise healthy restart. Bootstrap fails closed if required unseal material is missing or runtime authority needs provisioning without privileged credentials.

Recovery:

1. restore the matching original `.local/vault/unseal-key` or deployment backup; restore `.local/vault/root-token` only if runtime authority needs privileged provisioning;
2. copy them using the migration command above without printing them;
3. restart:

```bash
docker compose --profile runtime up -d
```

If those files/Vault state are unrecoverable, existing wrapped DEKs cannot be re-derived. Removing Vault/bootstrap volumes or running `down --volumes` is content/key loss, not recovery.

Only when destructive loss handling is explicit, audit affected owners with:

```bash
python scripts/reset_unreadable_owner_content.py
```

Run again with `--apply` only when deleting unreadable transcript-derived content and issuing fresh DEKs is the approved intent.

### Runtime unhealthy

```bash
docker compose --profile runtime ps -a
docker inspect --format '{{json .State.Health}}' "$(docker compose --profile runtime ps -q openscribe)"
docker compose --profile runtime logs --tail=300 openscribe
```

Check migrations, Vault bootstrap/unseal, PostgreSQL/Redis connectivity, stable CSRF secret, production cookie settings, and worker/Beat startup.

For a Deepgram live recording failure, correlate the JSON lifecycle lines by
`diagnostic_id` in `docker compose --profile runtime logs --since=1h --no-color openscribe`.
The terminal event includes the active phase and last completed stage. Docker
logging retention remains controlled by the deployment's daemon or logging
driver configuration; Compose does not add a separate Deepgram file sink.

## Host development remains available

`start-dev.sh` starts only PostgreSQL, Redis, and Vault in Compose, then runs FastAPI/Celery/Beat and optional Brave on the host virtualenv for reload/debugging.

When launching Uvicorn directly for local development, use the same WebSocket
message and queue caps documented above. `start-dev.sh` launches FastAPI's dev
server without those caps; keep remote binding on a trusted development
network. Use the runtime profile for networked live-audio testing.

Do not run it alongside the `runtime` profile.

## Production boundary

Before clinical production:

- use separately managed strong database/Redis credentials and network/TLS controls;
- pre-provision Vault and inject least-privilege runtime identities instead of persistent root credentials;
- split Celery workers by queue and monitor worker/Beat independently;
- use a configured HTTPS reverse proxy and deliberate forwarded-header trust;
- coordinate PostgreSQL/Vault backup and restore drills;
- monitor web, worker, Beat, database, Redis, Vault, transcript/legal/audit retention, source-audio cleanup, provider-secret cleanup, and quota/outbox lifecycle;
- establish deployment secret rotation, audit review, recovery, and destructive deletion procedures.

## Operator actions still required

The application changes do not complete edge or supply-chain work. Before relying on the related controls:

- When Cloudflare Origin CA certificates are used, keep the private key on the
  server, keep Cloudflare proxying enabled, and use SSL/TLS mode **Full
  (strict)**. Rotate certificates before expiry by installing the replacement
  certificate and matching key, validating the Nginx configuration, reloading
  Nginx, and checking both the served origin certificate and public site.
  Configure expiry and availability monitoring for the deployed environment.
  See [Cloudflare Origin CA](https://developers.cloudflare.com/ssl/origin-configuration/origin-ca/).
- restrict origin access and confirm Cloudflare overwrites client-IP headers before enabling `RATE_LIMIT_TRUST_CLOUDFLARE`;
- add Cloudflare WAF rate rules for public abuse paths;
- set and test proxy/CDN request-body limits;
- redirect `www` to the canonical host and remove wildcard DNS records that are not needed;
- lock container image references by digest and dependency artifacts by full hashes; this work remains open;
- use the current password-reset behaviour as implemented. A durable asynchronous mail design is still needed if stronger response-timing uniformity is required.
