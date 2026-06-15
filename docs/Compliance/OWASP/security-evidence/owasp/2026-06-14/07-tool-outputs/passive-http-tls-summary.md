# Passive HTTP/TLS Summary

Date: 2026-06-14  
Target: `openscribe.co.uk`  
Mode: unauthenticated public passive/read-only capture using Python stdlib HTTP/TLS calls.  
Redaction: cookie values, CSP nonces, Cloudflare report URLs, and body samples omitted from this committed summary.

## DNS

| Record type | Observed values |
| --- | --- |
| A | `104.21.38.82`, `172.67.220.196` |
| AAAA | `2606:4700:3031::ac43:dcc4`, `2606:4700:3037::6815:2652` |
| Edge/provider clue | Cloudflare IP space and `server: cloudflare` header observed. |

## TLS

| Field | Observed value |
| --- | --- |
| TLS version | `TLSv1.3` |
| Cipher | `TLS_AES_256_GCM_SHA384` |
| Certificate subject | `CN=openscribe.co.uk` |
| Certificate SAN | `DNS:*.openscribe.co.uk`, `DNS:openscribe.co.uk` |
| Issuer | Let's Encrypt `E7` |
| Validity | `Apr 28 21:55:54 2026 GMT` to `Jul 27 21:55:53 2026 GMT` |
| Certificate SHA-256 | `62196bc1e69618ab7be768e29ced247fa0919b8e2e5d9254db24bad0bc42efec` |

## HTTP Redirect

| URL | Status | Location | Notes |
| --- | --- | --- | --- |
| `http://openscribe.co.uk/` | `301 Moved Permanently` | `https://openscribe.co.uk/` | HTTP redirects to HTTPS. |

## Public Path Samples

| Path | HEAD status | GET status | Content type | Notes |
| --- | --- | --- | --- | --- |
| `/` | `405 Method Not Allowed` | `200 OK` | `text/html; charset=utf-8` | Public splash page. HEAD is not allowed and returns JSON. |
| `/login` | `405 Method Not Allowed` | `200 OK` | `text/html; charset=utf-8` | Public login page. |
| `/request-access` | `405 Method Not Allowed` | `200 OK` | `text/html; charset=utf-8` | Public account request page. |
| `/robots.txt` | `303 See Other` | `303 See Other` | Empty redirect | Redirects to `/login`; no public robots file observed. |
| `/sitemap.xml` | `303 See Other` | `303 See Other` | Empty redirect | Redirects to `/login`; no public sitemap observed. |
| `/.well-known/security.txt` | `303 See Other` | `303 See Other` | Empty redirect | Redirects to `/login`; no public `security.txt` observed. |
| `/docs` | `200 OK` | Not fetched in full | `text/html; charset=utf-8` | FastAPI/Swagger documentation appears publicly reachable. |
| `/openapi.json` | `200 OK` | `200 OK` | `application/json` | OpenAPI schema is publicly reachable; sampled body shows `OpenScribe MVP` and route definitions. |

## Security Header Samples

Observed on public HTML/API responses unless noted:

| Header | Observed value / status |
| --- | --- |
| `Strict-Transport-Security` | `max-age=63072000; preload` observed on Python samples. ZAP separately reported multiple HSTS entries on some POST/redirect/error responses; needs manual triage. |
| `Content-Security-Policy` | Present, nonce-based, includes `default-src 'self'`, `frame-ancestors 'none'`, `object-src 'none'`, `script-src 'self' 'nonce-...' 'wasm-unsafe-eval'`, `connect-src 'self'`, `upgrade-insecure-requests`. |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Cross-Origin-Opener-Policy` | `same-origin` |
| `Cross-Origin-Resource-Policy` | `same-origin` |
| `Cross-Origin-Embedder-Policy` | Not observed. |
| `Permissions-Policy` | Not observed. |
| `Cache-Control` | Not observed on public pages sampled; ZAP raised cache-control review alerts. |
| `Server` | `cloudflare` |
| `X-Served-By` | `openscribe.co.uk` observed on several responses. |

## Cookie Flag Samples

| Cookie name | Observed flags | Notes |
| --- | --- | --- |
| `openscribe_csrf` | `Path=/; SameSite=lax; Secure` | No `HttpOnly`, consistent with documented design that JS reads the CSRF cookie to submit `X-CSRF-Token`. Verify HTML forms also carry hidden `_csrf_token` or origin/header protection covers them. |

## Passive Observations

- Public `/docs` and `/openapi.json` expose the application route schema on `openscribe.co.uk`.
- `security.txt` is not publicly served; it redirects to `/login`.
- `robots.txt` and `sitemap.xml` are not publicly served; both redirect to `/login`.
- Security headers are broadly present, with missing `Permissions-Policy` and `Cross-Origin-Embedder-Policy` noted by ZAP.
- Public pages did not show obvious stack traces in sampled first 4 KiB body snippets.
