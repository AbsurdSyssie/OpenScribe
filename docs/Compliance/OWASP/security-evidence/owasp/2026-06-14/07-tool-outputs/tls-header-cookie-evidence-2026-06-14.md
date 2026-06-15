# 07 - TLS, Header, and Cookie Evidence

Date: 2026-06-14
Target: `https://openscribe.co.uk` (production)
Mode: public unauthenticated passive capture (Python stdlib, HackerTarget, ZAP baseline)
Post-fix state: all hardening headers deployed and production-confirmed.

## TLS

| Field | Value |
| --- | --- |
| Version | TLS 1.3 |
| Cipher | TLS_AES_256_GCM_SHA384 |
| Certificate CN | `openscribe.co.uk` |
| Certificate SAN | `DNS:*.openscribe.co.uk`, `DNS:openscribe.co.uk` |
| Issuer | Let's Encrypt E7 |
| Expiry | 2026-07-27 |
| SHA-256 | `62196bc1e69618ab7be768e29ced247fa0919b8e2e5d9254db24bad0bc42efec` |

## HTTP → HTTPS Redirect

`http://openscribe.co.uk/` → `301 Moved Permanently` → `https://openscribe.co.uk/`. Handled by Cloudflare edge.

## Security Headers (Production, Post-Fix)

| Header | Value | Source |
| --- | --- | --- |
| `Strict-Transport-Security` | `max-age=15552000; includeSubDomains` (single header, Cloudflare-owned) | Cloudflare; app `HSTS_SOURCE=proxy` |
| `Content-Security-Policy` | Nonce-based; `default-src 'self'`; `frame-ancestors 'none'`; `object-src 'none'`; `script-src 'self' 'wasm-unsafe-eval'`; `connect-src 'self'`; `upgrade-insecure-requests` | App middleware |
| `X-Content-Type-Options` | `nosniff` | App middleware |
| `X-Frame-Options` | `DENY` | App middleware |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | App middleware |
| `Cross-Origin-Opener-Policy` | `same-origin` | App middleware |
| `Cross-Origin-Resource-Policy` | `same-origin` | App middleware |
| `Cross-Origin-Embedder-Policy` | `credentialless` | App middleware (added 2026-06-14) |
| `Permissions-Policy` | `camera=(), geolocation=(), payment=(), usb=(), fullscreen=(self), microphone=(self)` | App middleware (added 2026-06-14) |
| `Server` | `cloudflare` | Cloudflare edge |
| `X-Served-By` | `openscribe.co.uk` | Observed on some responses |

## Cache-Control by Route Class

| Route class | Cache-Control | Set-Cookie | Notes |
| --- | --- | --- | --- |
| `/` (splash) | `no-store` | yes | Creates CSRF cookies |
| `/login` | `no-store` | yes | Auth form, creates CSRF cookies |
| `/forgot-password` | `no-store` | yes | Recovery form |
| `/request-access` | `no-store` | yes | Account request form |
| `/api` / `/api/` / `/api/v1/*` | `no-store` | authenticated | API responses |
| `/robots.txt` | `public, max-age=3600` (Cloudflare overrides to `max-age=14400`) | no | Public metadata, cookie-free |
| `/.well-known/security.txt` | `public, max-age=3600` | no | Public metadata, cookie-free |
| `/sitemap.xml` | `public, max-age=3600` | no | Intentional 404, cookie-free |
| `/static/vendor/*` | `public, max-age=3600` (Cloudflare overrides to `max-age=14400`) | no | Static assets, cookie-free |

## Cookie Contract (Production)

| Cookie | HttpOnly | Secure | SameSite | Path | Purpose |
| --- | --- | --- | --- | --- | --- |
| `openscribe_csrf` | No (by design) | Yes | Lax | `/` | Signed CSRF nonce; JS-readable for `X-CSRF-Token` header in same-origin API calls. Not an auth bearer. |
| `openscribe_csrf_anon` | Yes | Yes | Lax | `/` | Anonymous nonce for public forms without session; issued before login. |
| `openscribe_session` | Yes | Yes | Lax | `/` | Auth bearer session cookie. Never JS-readable. |
| `openscribe_trusted_device` | Yes | Yes | Lax | `/` | Trusted-device token. Never JS-readable. |

## Cookie Regression Evidence

- `tests/test_cookie_csrf_security.py` (26 passed): verifies `openscribe_csrf` is signed and readable, anonymous nonce is `HttpOnly`, session and trusted-device cookies are `HttpOnly`, CSRF cookie alone does not authenticate `/api/v1/auth/me`.
- `tests/test_api.py` CSRF tests (7 passed): verifies missing/mismatched/cross-origin CSRF rejection for cookie-authenticated unsafe API requests.

## Production ZAP Confirmation

All production hardening headers confirmed present via ZAP baseline retests:
- `PASS: Permissions Policy Header Not Set [10063]`
- `PASS: Insufficient Site Isolation Against Spectre Vulnerability [90004]` (COEP)
- `PASS: Strict-Transport-Security Header [10035]` (single HSTS header, Cloudflare-owned)
- `PASS: Absence of Anti-CSRF Tokens [10202]` (server-rendered `_csrf_token` on public forms)

ZAP output: `07-tool-outputs/zap/zap-baseline-retest-metadata-after-purge-2026-06-14.*`

## Redaction Note

No cookies values, tokens, account data, transcript/note content, prompts, provider responses, or audio committed. CSP nonces, Cloudflare report URLs, and body samples omitted. Cookie names and flags only.
