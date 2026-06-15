# 05 - Server Fingerprinting

Status: public unauthenticated HTTP/TLS/header/cookie capture completed for `openscribe.co.uk` on 2026-06-14. All hardening headers, cache policies, and cookie flags production-confirmed. HSTS, CSRF, metadata, Permissions-Policy, and COEP resolved via prior retests.

## Scope

| Target | Current permission | Status |
| --- | --- | --- |
| Local `http://127.0.0.1:8080` | In scope | Not executed in this pass. |
| Staging | To confirm | Gap. |
| Production | Passive/read-only only | Production headers, TLS, cookies, and cache policy captured and confirmed post-fix. See `07-tool-outputs/tls-header-cookie-evidence-2026-06-14.md`. |
| Third-party providers | Out of scope | Do not fingerprint. |

## Repo-Backed Fingerprint Baseline

| Signal | Expected/current repo evidence | Status | Source |
| --- | --- | --- | --- |
| App framework | FastAPI serving Jinja/browser pages and `/api/v1` JSON routes | Repo-evidenced | `docs/security.md`, `docs/api.md` |
| DB | Postgres 16 local service | Repo-evidenced | `docker-compose.yml` |
| Cache/broker | Redis 7 local service | Repo-evidenced | `docker-compose.yml` |
| Secret store | HashiCorp Vault 1.17 local service | Repo-evidenced | `docker-compose.yml` |
| Local binding | Postgres/Redis/Vault bind to `127.0.0.1` | Repo-evidenced | `docker-compose.yml` |
| Security headers | HSTS on HTTPS, CSP, X-Content-Type-Options, Referrer-Policy, X-Frame-Options, COOP, CORP | Repo-evidenced | `docs/security.md` |
| Cookie flags | `HttpOnly`, `SameSite=Lax`, `Secure` outside localhost/when configured | Repo-evidenced | `docs/security.md` |
| CSP | Nonce-based CSP; no third-party runtime assets in production | Repo-evidenced | `docs/security.md` |
| Sensitive response cache | `Cache-Control: no-store`, `Pragma: no-cache` for transcript/generated-document APIs | Repo-evidenced | `docs/api.md`, `docs/security.md` |

## Public Server Fingerprint - `openscribe.co.uk`

| Signal | Observed value | Status | Evidence |
| --- | --- | --- | --- |
| Edge/server header | `server: cloudflare` | Test-evidenced | `07-tool-outputs/passive-http-tls-summary.md` |
| DNS | Cloudflare IPv4/IPv6 edge addresses | Test-evidenced | `07-tool-outputs/passive-http-tls-summary.md` |
| HTTP to HTTPS | `301` from `http://openscribe.co.uk/` to `https://openscribe.co.uk/` | Test-evidenced | `07-tool-outputs/passive-http-tls-summary.md` |
| TLS | TLS 1.3 with `TLS_AES_256_GCM_SHA384` | Test-evidenced | `07-tool-outputs/passive-http-tls-summary.md` |
| Certificate | Let's Encrypt E7, `CN=openscribe.co.uk`, SAN includes `*.openscribe.co.uk` and `openscribe.co.uk`, expires 2026-07-27 | Test-evidenced | `07-tool-outputs/passive-http-tls-summary.md` |
| HSTS | `max-age=15552000; includeSubDomains`, single Cloudflare-owned header; app `HSTS_SOURCE=proxy`. ZAP retest passed `Strict-Transport-Security Header [10035]`. | Test-evidenced | `07-tool-outputs/tls-header-cookie-evidence-2026-06-14.md`, `07-tool-outputs/zap/zap-baseline-retest-hsts-cloudflare-2026-06-14.*` |
| CSP | Nonce-based CSP present; `frame-ancestors 'none'`; `object-src 'none'`; `connect-src 'self'`; `upgrade-insecure-requests` | Test-evidenced | `07-tool-outputs/tls-header-cookie-evidence-2026-06-14.md` |
| Clickjacking | `X-Frame-Options: DENY`, ZAP Anti-clickjacking check passed | Test-evidenced | `07-tool-outputs/tls-header-cookie-evidence-2026-06-14.md`, ZAP baseline |
| MIME sniffing | `X-Content-Type-Options: nosniff`, ZAP check passed | Test-evidenced | `07-tool-outputs/tls-header-cookie-evidence-2026-06-14.md`, ZAP baseline |
| Referrer policy | `strict-origin-when-cross-origin` | Test-evidenced | `07-tool-outputs/tls-header-cookie-evidence-2026-06-14.md` |
| COOP/CORP | `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Resource-Policy: same-origin` | Test-evidenced | `07-tool-outputs/tls-header-cookie-evidence-2026-06-14.md` |
| COEP | `Cross-Origin-Embedder-Policy: credentialless` added 2026-06-14. ZAP retest passed `Insufficient Site Isolation Against Spectre Vulnerability [90004]`. | Test-evidenced | `07-tool-outputs/tls-header-cookie-evidence-2026-06-14.md`, `07-tool-outputs/zap/zap-baseline-retest-headers-2026-06-14.*` |
| Permissions-Policy | `camera=(), geolocation=(), payment=(), usb=(), fullscreen=(self), microphone=(self)` added 2026-06-14. ZAP retest passed `Permissions Policy Header Not Set [10063]`. | Test-evidenced | `07-tool-outputs/tls-header-cookie-evidence-2026-06-14.md`, `07-tool-outputs/zap/zap-baseline-retest-headers-2026-06-14.*` |
| Cache-Control | Routes creating CSRF cookies or carrying account/API context emit `no-store`. Public metadata and static assets are cookie-free with `public, max-age=3600`. Cloudflare overrides some static assets to `max-age=14400`. | Test-evidenced | `07-tool-outputs/tls-header-cookie-evidence-2026-06-14.md`, `07-tool-outputs/cache-header-samples-2026-06-14.txt` |
| Cookie flags | Session and trusted-device cookies `HttpOnly`; CSRF cookie intentionally JS-readable. Anonymous nonce `HttpOnly`. All `Secure` and `SameSite=Lax` in production. | Test-evidenced | `07-tool-outputs/tls-header-cookie-evidence-2026-06-14.md`, `tests/test_cookie_csrf_security.py` |
| Public API schema | `/docs` and `/openapi.json` are public. Accepted because OpenScribe is open source. Recheck future cycles for secrets/internal details. | Accepted | `09-findings-and-remediation.md#owasp-2026-06-14-009` |
| HEAD behavior | Public HTML routes return `405 Method Not Allowed` for HEAD and `200 OK` for GET | Test-evidenced | `07-tool-outputs/passive-http-tls-summary.md` |
| Metadata paths | `/robots.txt` (crawl guidance), `/.well-known/security.txt` (contact), `/sitemap.xml` (intentional 404). No cookies, no redirect to login. | Test-evidenced | `07-tool-outputs/tls-header-cookie-evidence-2026-06-14.md`, `10-retest-log.md` |
| CSRF protection | Server-rendered hidden `_csrf_token` on public forms; signed CSRF cookie for browser API `X-CSRF-Token`. ZAP retest passed `Absence of Anti-CSRF Tokens [10202]`. | Test-evidenced | `10-retest-log.md`, `07-tool-outputs/zap/zap-baseline-retest-2026-06-14.*` |

## Live Capture Commands For Approved Targets

Local example:

```bash
curl -i http://127.0.0.1:8080/login
curl -i http://127.0.0.1:8080/api/v1/auth/me
```

HTTPS target example:

```bash
curl -I https://approved-openscribe.example/login
openssl s_client -connect approved-openscribe.example:443 -servername approved-openscribe.example </dev/null
```

Optional scanner commands only with approval:

```bash
nmap -sV -Pn -p 80,443 approved-openscribe.example
nikto -h https://approved-openscribe.example
```

## Evidence To Record

| Evidence | Required fields |
| --- | --- |
| HTTP headers | Date, target, status, `Server` if present, security headers, cache headers. |
| Cookies | Names only, flags, expiry/session behavior. Never values. |
| TLS | Certificate CN/SAN, issuer, expiry, TLS versions/ciphers accepted. |
| Ports | Approved target, ports checked, exposed services, scanner version. |
| Error pages | Status, route, whether stack traces/framework banners leak. |

## ZAP Baseline Fingerprint Summary

ZAP passive baseline observed 12 URLs, 0 fail alerts, 9 warning alert types, and 58 pass rules. Full report is under `07-tool-outputs/zap/`.

Passed passive checks included: vulnerable JS library, cross-domain JavaScript inclusion, content-type header missing, anti-clickjacking, X-Content-Type-Options missing, debug error disclosure, sensitive information in URL/referrer, suspicious comments, directory browsing, Heartbleed indicative, HTTP server response header, X-Powered-By leak, CSP header not set, mixed content, cross-domain misconfiguration, source-code disclosure, weak authentication method, dangerous JS functions, malicious polyfill domain, private IP disclosure, application error disclosure, and loosely scoped cookie.

## Gaps

- Port scan/service banner report not run; only HTTP/TLS application-level capture and ZAP passive baseline were performed.
- Need staged/production authenticated cookie/header capture with synthetic accounts (blocked on auth role crawl — `OWASP-2026-06-14-002`).

All other gaps from the initial 2026-06-14 capture are now closed: HSTS is single-owned by Cloudflare, COEP and Permissions-Policy are deployed and ZAP-confirmed, public metadata files are served with correct content types and no cookies, CSRF hidden tokens are rendered on public forms and ZAP-confirmed, and public `/docs`/`/openapi.json` exposure is accepted for open-source project.

Remediation plan: `11-remediation-plan.md`.
