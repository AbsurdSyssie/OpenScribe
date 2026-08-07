# Security model

This document records implemented security boundaries and operational requirements. Repeatable browser/XSS checks are in [security-xss.md](security-xss.md). Authentication details are in [auth.md](auth.md).

## Core invariants

- Transcript-derived content is owner-only.
- Leader or system-administrator authority does not grant transcript, working-note, dictation, generated-document, prompt, redaction, or PII readability.
- System-administrator accounts do not own transcript content.
- Transcript retention is snapshotted from server-owned team policy; user payloads cannot extend it.
- Expired transcript roots are unavailable to owner APIs before asynchronous physical cleanup.
- User and team deletion follow implemented hard-delete semantics and must not be described as recoverable soft deletion.
- Provider credentials, mail credentials, session tokens, trusted-device tokens, auth email tokens, recovery codes, and password material are never stored as recoverable plaintext when hashing or Vault references are sufficient.
- Audit records must contain bounded metadata only, never credentials, cookies, request bodies, prompt text, transcript-derived text, or provider responses.

## Authentication boundary

OpenScribe uses opaque browser tokens with server-side state:

- `openscribe_session` is the authentication bearer token; PostgreSQL stores only its hash.
- `openscribe_trusted_device` is a separate bearer token used only to skip TOTP after a correct password login; PostgreSQL stores only its hash.
- `openscribe_csrf` and `openscribe_csrf_anon` are CSRF controls, not authentication tokens.
- Session levels explicitly distinguish onboarding, pending MFA, and full access.
- Suspension, disable/lock handling, password/account recovery, and sensitive account changes revoke the applicable sessions and trusted-device records.
- Password hashes use Argon2id.
- Unknown-user login attempts still run Argon2id password verification against a dummy hash before returning the same invalid-credential response.
- New TOTP seeds are encrypted under the owning user's DEK; recovery codes and auth email tokens are hash-only.

Cookies are `HttpOnly`, `SameSite=Lax`, and use `Secure` according to `COOKIE_SECURE_MODE`. Production startup fails unless `COOKIE_SECURE_MODE=always`.

The first system administrator is a one-time bootstrap path. In production it is unavailable unless `BOOTSTRAP_ADMIN_TOKEN` is configured, and the submitted value must match. Creation obtains a transaction advisory lock and rechecks that no user exists, preventing concurrent requests from creating more than one first administrator.

## CSRF and origin validation

Browser unsafe requests require project-owned HMAC CSRF tokens:

- anonymous forms use a token bound to an opaque anonymous nonce;
- authenticated forms and same-origin API requests use a token bound to the active session token;
- session rotation invalidates the old token;
- forms submit `_csrf_token`;
- browser JavaScript receives the value through nonce-protected server-rendered state and sends `X-CSRF-Token`;
- the `HttpOnly` CSRF cookie is not read by JavaScript;
- unsafe `/api/v1` requests with session or trusted-device cookies also require a matching `Origin` or `Referer`;
- safe `GET`, `HEAD`, and `OPTIONS` requests are exempt.

Direct request scheme/host is authoritative by default. Set `TRUST_FORWARDED_ORIGIN_HEADERS=true` only when a trusted proxy sanitizes forwarded host/protocol values and direct origin access is blocked.

`CSRF_SECRET` is the preferred explicit signing key. `SECRET_KEY` is a compatibility alias. When neither is set, production startup obtains a stable secret through Vault KV using `CSRF_SECRET_VAULT_REF` or the default logical reference `secret:openscribe/platform/csrf`. Startup fails closed when no stable key can be resolved.

## HTTP security headers and caching

OpenScribe currently emits:

- nonce-based `Content-Security-Policy`;
- `X-Content-Type-Options: nosniff`;
- `Referrer-Policy: strict-origin-when-cross-origin`;
- `X-Frame-Options: DENY`;
- `Cross-Origin-Opener-Policy: same-origin`;
- `Cross-Origin-Resource-Policy: same-origin`;
- `Cross-Origin-Embedder-Policy: credentialless`;
- a restrictive `Permissions-Policy`;
- `X-Robots-Tag: noindex, nofollow, noarchive, nosnippet, noimageindex`;
- HSTS on HTTPS when `HSTS_SOURCE=app`, or only static fallback responses when `HSTS_SOURCE=proxy_static_fallback`.

Choose exactly one HSTS owner. Set `HSTS_SOURCE=proxy` when the reverse proxy emits HSTS for every response.

No-store responses include:

- `/`, login, account request, reset, activation, onboarding, MFA, settings/workspace and admin surfaces;
- public legal routes `/privacy`, `/cookies`, and `/terms` when a matching notice is published;
- `/api` and `/api/*`;
- transcript, generated-document, dictation, and preview content routes.

Short public caching is limited to explicit metadata routes and static assets. `robots.txt` denies crawling, but crawler directives are not authorization controls.

API docs are public by default outside production. Production defaults `/docs`, `/redoc`, and `/openapi.json` to fully authenticated system administrators unless `PUBLIC_API_DOCS=true` is explicitly configured.

## Content Security Policy and browser assets

Production browser dependencies must be same-origin. Do not load runtime JavaScript, CSS, fonts, WASM, ONNX models, or other executable assets from public CDNs.

Current policy goals:

- scripts only from `'self'` and the response nonce;
- no `script-src 'unsafe-inline'`;
- no third-party script/style/font/connect origins;
- no inline style attributes;
- `frame-ancestors 'none'`;
- `object-src 'none'`;
- same-origin API, EventSource, and WebSocket connections;
- narrow WASM support only where the pinned local runtime requires it.

Templates must not use `style="..."`. Dynamic visual values should be emitted as escaped/clamped data attributes and applied by nonce-approved code through direct CSS property assignment, not `setAttribute("style", ...)` or `cssText`.

Run the focused checks before merging browser-asset or CSP changes:

```bash
.venv/bin/pytest -q tests/test_cookie_csrf_security.py tests/test_xss_coverage.py
rg '\bstyle\s*=' app/templates
rg 'cdn\.jsdelivr\.net|cdn\.tailwindcss\.com|unpkg\.com|fonts\.googleapis\.com|fonts\.gstatic\.com' app/templates app/static/js
```

Expected search result: no production runtime dependency matches.

## Encryption and key hierarchy

Transcript-derived and other designated owner content is encrypted before PostgreSQL persistence with per-user DEKs. Vault Transit wraps those DEKs under `VAULT_USER_CONTENT_KEK_KEY_NAME`.

Operational rules:

- PostgreSQL stores wrapped DEKs and ciphertext, not plaintext DEKs.
- Vault and PostgreSQL backups are one recovery set; restoring only one side can make encrypted content unreadable.
- User DEKs are not rotated or deleted during ordinary password reset.
- Authentication-material encryption does not expand transcript ownership.
- Encryption failures are controlled service failures and must not silently fall back to plaintext.
- User/content deletion must also remove or durably queue cleanup for corresponding Vault material where the current model requires it.

See [mfa-secret-encryption.md](mfa-secret-encryption.md) for TOTP compatibility and recovery behavior.

## Provider credentials and outbound requests

Provider metadata lives in PostgreSQL. Raw credentials live in Vault or the deployment identity layer.

- API/browser responses expose `has_secret` or credential status, never the raw credential or unrestricted Vault reference.
- Credentials are read only inside authorized provisioning/inspection or worker runtime paths.
- Required-auth edit drafts that inherit a saved credential copy it to a draft-owned versioned Vault path before commit; they do not persist an alias to the active secret.
- Replacement/removal/deletion records a durable cleanup intent for retired Vault references.
- Cleanup workers verify that a reference is no longer live before deleting it.
- Rollback compensation queues orphan cleanup when a Vault write succeeded but the database transaction failed.
- Provider error persistence/logging uses bounded safe codes rather than raw response bodies.
- Remote provider endpoints require HTTPS; HTTP is limited to localhost/private development targets under explicit adapter rules.
- SSRF-sensitive inspection follows validated schemes/hosts, redirect rules, response size/content constraints, and provider-specific contracts. Provider base URLs reject known cloud metadata names and addresses, including AWS, Alibaba, AWS IPv6, and Google metadata targets. This denylist does not remove support for `localhost`, loopback, or private-address local providers such as Ollama and Presidio.
- Provider OpenAPI inspection reads at most 2 MiB. Model discovery reads at most 1 MiB. Ollama generation streams accept at most 8 MiB and 10,000 fragments. A declared or streamed excess fails as a bounded provider error rather than being retained in memory.

`PROVIDER_CREDENTIAL_FINGERPRINT_SECRET` is used for non-reversible STT duplicate-credential fingerprints. Set a stable dedicated production value.

## Queued work and temporary audio

Generation and ingestion creation use a durable metadata-only task-dispatch outbox. Business data commits before broker publication; a one-second Beat publisher is the fallback when immediate publish fails.

- Broker payloads identify database rows; they do not contain transcript-derived content or credentials.
- Provider credentials are resolved before a provider attempt is marked submitted.
- Definite pre-dispatch credential failure cancels quota reservation without consuming provider quota.
- Duplicate delivery is handled through database claim/idempotency controls.
- Uploaded source audio needed for asynchronous processing or retry is stored under a bounded Vault reference. Its deadline is fixed at the original Vault write and does not reset on retry. Successful processing and transcript deletion clear it sooner; the periodic worker expires every remaining source at 24 hours and uses the durable cleanup queue when Vault deletion fails.
- Retention cleanup, failed-ingestion source expiry, transcript-audio cleanup, provider-secret cleanup, audit expiry, legal-document expiry, and quota lifecycle processing run every 10 seconds.

## Security audit

`security_audit_events` stores bounded metadata such as action, actor/target/team IDs, outcome, reason codes, route, method, sanitized IP, and user agent.

Ordinary identifiable rows expire after six calendar months. The control-queue worker deletes due rows in locked batches and is safe to repeat. A fully authenticated system administrator can place a documented hold for an incident, contractual investigation, legal duty or dispute. Each approval records an owner, reason, review date and expiry, lasts no more than 90 days, and must be renewed explicitly. An account that owns an active hold cannot be deleted until the hold is released or transferred. Release or expiry restores the ordinary deletion rule.

The sanitizer removes nested keys containing sensitive terms, escapes CR/LF, bounds strings, lists, maps, and serialized detail size, and never intentionally records request bodies. Login/reset subjects are stored as HMAC-SHA256 digests, not raw email addresses. Account lifecycle log records use the target user ID and do not include the raw target email.

Configure a dedicated `AUDIT_SUBJECT_HASH_SECRET` in production where practical. The fallback chain uses `SECRET_KEY`, `CSRF_SECRET`, or the stable Vault-backed CSRF key. Audit client-IP trust is disabled by default:

- `AUDIT_TRUST_X_FORWARDED_FOR=true` trusts the first forwarded address;
- `AUDIT_TRUST_CLOUDFLARE=true` trusts Cloudflare's client address;
- enable either only behind the expected sanitizing proxy with blocked direct origin access.

## Operator legal content

Each deployment has one optional global operator profile and fixed privacy, cookie/browser-storage and terms document roots. System administrators write a restricted Markdown subset in the Legal content admin tab. The server parses headings, paragraphs, flat bullet lists, bold and italic text, bounded tables, inline or standalone HTTPS links, and inline email links into validated structured blocks before saving. Email links accept one plain address and do not accept query parameters. It removes harmless unsupported presentation marks, keeps their wording and warns the administrator. It rejects HTML, unsafe link schemes and structures that could change meaning, such as numbered or nested lists, block quotes and code blocks. Preview and publication render only validated blocks; they never render parser-produced HTML. Publishing atomically supersedes the previous version; rollback creates a new draft. Each legal change and its sanitized audit row commit in the same transaction. Public routes return 404 until the matching kind is published, and footer links appear only for published kinds.

The profile and documents must not contain patient, transcript, provider-secret or customer-specific content. Missing identity, contact or required notices creates a persistent system-administrator warning but does not block startup, readiness, sign-in or use. `/.well-known/security.txt` uses only the configured security contact and returns 404 when it is absent; it never falls back to a project or Memre identity.

Abandoned drafts expire after 12 calendar months. Superseded published versions remain for six years after replacement. An active system-administrator legal hold blocks scheduled deletion until release. Current published versions are never retention candidates.

## Rate limiting and quotas

Authentication and account-security endpoints use fixed SlowAPI limits. Upload and LLM generation routes use configurable burst/daily limits and owner/session/IP-aware keys. Redis is the limiter store; `RATE_LIMIT_KEY_PREFIX` can isolate deployments sharing Redis.

System-admin quotas and provider attempts are abuse/usage controls, not authorization grants. Quota rows contain accounting metadata and must not contain prompt or transcript text.

IP-keyed rate limits use the socket peer by default. `RATE_LIMIT_TRUST_CLOUDFLARE=true` uses a valid `CF-Connecting-IP`; if it is not enabled, `RATE_LIMIT_TRUST_X_FORWARDED_FOR=true` uses the first valid `X-Forwarded-For` address. Invalid trusted-header values fall back to the socket peer. Enable either setting only after the proxy/CDN is the sole origin path and overwrites that header. Header trust without those two conditions lets direct callers select another client's rate-limit key.

`ALLOWED_HOSTS` uses Trusted Host middleware. Production accepts only explicit hostnames and rejects wildcard entries, including a wildcard derived from `APP_PUBLIC_URL`. Set `APP_HEALTHCHECK_HOST` to one allowed canonical host so internal health probes do not require a localhost exception.

See [environment.md](environment.md) for exact variables/defaults and [auth.md](auth.md) for route limits.

## Local and Docker exposure

Checked-in defaults bind FastAPI, PostgreSQL, Redis, and Vault to localhost. Persistent Compose publishes only the application port selected by `DOCKER_APP_BIND`; service ports remain localhost-bound.

- Do not expose the local Vault server or its root/unseal material.
- Exposing Redis also exposes Celery broker/result data and limiter state.
- Do not use `FORWARDED_ALLOW_IPS=*` unless the application origin is unreachable except through the trusted proxy.
- Enabling forwarded-origin or audit-header trust does not secure direct origin access.
- Enabling Cloudflare or forwarded-header rate-limit trust also does not secure direct origin access; it requires origin restriction and header overwrite.
- `start-dev.sh` remote bind/service exposure requires explicit development opt-ins.

The persistent Docker profile is a single-host runtime baseline, not a complete production security architecture. Production requires external TLS/proxy controls, managed secrets, backups, monitoring, least-privilege service identities, and deliberate database/Redis/Vault hardening. See [docker.md](docker.md).

## Deployment work not completed by the application

Operators still need to restrict the origin before enabling Cloudflare rate-limit trust, add Cloudflare WAF rate rules, configure proxy request-body caps, and redirect `www` or remove unneeded wildcard DNS. Image digest locking and full dependency-hash locking remain open. Password-reset email timing is not yet backed by durable asynchronous delivery; adopt that design if stronger uniformity is required.

## Library import/export boundary

Template and Quick Action bundles are limited to caller-owned personal assets and current-team visible assets. Smart Phrase import/export is owner-only. Import never accepts file-supplied ownership, team, creator, active state, version, usage, or authority metadata.

Preflight is read-only. Confirmation revalidates the original file and creates selected assets in one transaction. Bundle uploads are limited to 1 MiB and 100 entries. Audit events exclude uploaded JSON and asset content.

## Explicitly forbidden

- storing session or trusted-device tokens in `localStorage` or `sessionStorage`;
- exposing authentication tokens to browser JavaScript;
- treating CSRF cookies as authentication;
- granting leaders/admins owner-content access because of management authority;
- logging credentials, cookies, raw reset/setup tokens, TOTP values, recovery codes, prompts, transcripts, notes, dictation, PII values, uploaded audio, or raw provider responses;
- storing provider secrets in PostgreSQL;
- loading production runtime browser dependencies from public CDNs;
- describing dated compliance evidence as proof of the current build without a current rerun.

Files under `docs/Compliance/` and dated security-evidence directories are point-in-time records. Preserve them as evidence snapshots and add newer assessments rather than rewriting historical results.
