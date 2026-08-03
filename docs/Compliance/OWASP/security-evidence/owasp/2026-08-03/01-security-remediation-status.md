# 01 - Security Remediation Status

Date: 2026-08-03
Status: repository-change evidence; not a production deployment attestation.

## Scope

This note records the repository remediation prepared on this date. It does not change the 2026-06-14 evidence. Source, focused tests, Compose configuration, and the Docker smoke workflow are the evidence for this note. It does not attest that production has been deployed or retested.

## Implemented remediation

- Production Trusted Host configuration accepts exact `ALLOWED_HOSTS` entries and rejects wildcards, including a wildcard in `APP_PUBLIC_URL`. `APP_HEALTHCHECK_HOST` must name an allowed canonical host and does not bypass host validation.
- Production first-admin bootstrap requires `BOOTSTRAP_ADMIN_TOKEN`. Creation uses a PostgreSQL transaction advisory lock and rechecks the zero-user condition.
- Public `POST /api/v1/account-requests` returns one generic `202 Accepted` status/body for a created request, duplicate request, or existing user. A partial unique index protects concurrent pending duplicates. Login performs dummy Argon2id verification for an unknown email. Processing time is not claimed to be identical.
- `LIVE_CHUNK_MAX_UPLOAD_BYTES` defaults to 24 MiB. Upload readers stop at the first byte over their configured cap before queueing or transcription.
- Provider OpenAPI inspection, model discovery, generic redaction, HTTP STT responses, and Ollama stream handling have byte limits; the Ollama stream also has a fragment limit.
- Streamed STT error responses are read within the active response context under a 64 KiB cap, preserving safe provider-error translation without reading a closed stream.
- Provider URL validation rejects known cloud metadata names and addresses while retaining private/loopback local provider support. Persisted dynamic provider URLs are rechecked immediately before covered outbound calls. DNS rebinding remains an egress-policy concern.
- Account lifecycle log records no longer include a raw target email.
- Runtime requirements pin `python-multipart` and `idna` and install the pinned `en_core_web_sm` wheel. Docker smoke CI runs `pip-audit -r requirements.txt`.

## Trust and deployment prerequisites

`RATE_LIMIT_TRUST_CLOUDFLARE` and `RATE_LIMIT_TRUST_X_FORWARDED_FOR` default to `false`. Cloudflare trust takes precedence. Enable the correct flag only after the proxy/CDN is the sole origin route and overwrites the trusted client-IP header. Otherwise a caller can influence the rate-limit key.

Application upload readers do not replace a public proxy/CDN request-body limit. Configure and test matching body caps before deployment.

## Open operator actions

- Restrict origin access, then enable the appropriate Cloudflare rate-limit trust setting.
- Add Cloudflare WAF rate rules for public abuse paths.
- Configure proxy/CDN request-body limits.
- Redirect `www` to the canonical host and remove unneeded wildcard DNS.
- Lock images by digest and dependency artifacts by full hashes.
- Design durable asynchronous password-reset mail if stronger response-timing uniformity is needed.

## Repository verification

The following checks passed on 2026-08-03:

- full test suite: `1385 passed`, with 21 deprecation warnings;
- `pip check`: no broken requirements;
- `pip-audit -r requirements.txt`: no known vulnerabilities in auditable packages; the pinned `en_core_web_sm` wheel is not present on PyPI and was reported as unaudited;
- runtime and demo Compose configuration validation;
- production Trusted Host import assertion;
- Alembic reports `f7a8b9c0d1e3` as the single head;
- maintained-document validation and `git diff --check`.

Run deployment checks separately for origin restriction, header overwrite, WAF rules, body limits, DNS, and image/digest locks.
