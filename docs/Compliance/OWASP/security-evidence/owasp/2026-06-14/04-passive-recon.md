# 04 - Passive Recon

Status: public passive recon complete for `openscribe.co.uk` on 2026-06-14. Search-engine, archive, and Shodan/Censys checks evidenced; Shodan/Censys require auth so marked `Not in scope`.

## Scope Reminder

- Local development is in scope.
- Staging target and production target are not confirmed for active testing.
- Production checks are passive/read-only unless written approval exists.
- Do not test third-party provider infrastructure without provider authorisation.

## Repo-Backed Passive Findings

| Item | Observation | Status | Source |
| --- | --- | --- | --- |
| Primary local app URL | `http://127.0.0.1:8080` with API docs at `/docs` | Repo-evidenced | `README.md` |
| Public routes | `/`, `/login`, `/request-access`, password reset, activation | Repo-evidenced | `README.md`, `docs/auth.md` |
| Authenticated routes | `/home`, `/transcribe`, `/admin` | Repo-evidenced | `README.md`, `docs/auth.md` |
| API versioning | Canonical JSON API under `/api/v1` | Repo-evidenced | `docs/api.md` |
| Local services | Postgres, Redis, Vault bound to localhost | Repo-evidenced | `docker-compose.yml` |
| Public CDN usage policy | Production runtime JS/CSS/fonts must not load from public CDNs | Repo-evidenced | `docs/security.md` |
| Sensitive evidence rule | Do not commit secrets/tokens/patient content/transcript/note/audio | Repo-evidenced | OWASP pack README |

## Public Passive Findings - `openscribe.co.uk`

| Item | Observation | Status | Evidence |
| --- | --- | --- | --- |
| DNS/edge | Domain resolves to Cloudflare IPv4/IPv6 edge addresses. | Test-evidenced | `07-tool-outputs/passive-http-tls-summary.md` |
| HTTPS redirect | `http://openscribe.co.uk/` redirects to `https://openscribe.co.uk/`. | Test-evidenced | `07-tool-outputs/passive-http-tls-summary.md` |
| Public splash/login/request access | `/`, `/login`, and `/request-access` return public HTML over HTTPS. | Test-evidenced | `07-tool-outputs/passive-http-tls-summary.md`, ZAP baseline |
| API docs exposure | `/docs` and `/openapi.json` return `200 OK` unauthenticated on the public URL. | Test-evidenced; finding opened | `07-tool-outputs/passive-http-tls-summary.md`, `09-findings-and-remediation.md` |
| Metadata paths | `/robots.txt`, `/sitemap.xml`, and `/.well-known/security.txt` redirect to `/login` rather than serving metadata files. | Test-evidenced; follow-up opened | `07-tool-outputs/passive-http-tls-summary.md`, `09-findings-and-remediation.md` |
| Static asset clue | ZAP observed local `/static/vendor/lucide/1.8.0/lucide.min.js`; no cross-domain JS inclusion alert fired. | Test-evidenced | `07-tool-outputs/zap/zap-baseline.md` |
| HTML comments/debug output | ZAP passive rules passed debug error, suspicious comments, source-code disclosure, and X-Powered-By checks. | Test-evidenced | `07-tool-outputs/zap/zap-baseline.md` |
| Public CDN usage | ZAP `Cross-Domain JavaScript Source File Inclusion` passed for crawled pages. | Test-evidenced | `07-tool-outputs/zap/zap-baseline.md` |

## Passive Recon Checklist

| Check | Target | Tool | Evidence output | Status |
| --- | --- | --- | --- | --- |
| Search indexed pages | `openscribe.co.uk` | DuckDuckGo/Bing `site:` search, `webfetch` | Zero indexed pages on DDG and Bing; Google blocked by bot detection (manual browser check recommended) | Test-evidenced |
| Historical archive | `openscribe.co.uk` | Wayback CDX API, `/web/` redirect | Zero Wayback captures; CDX empty; `/web/2026/` redirects to save page (never archived) | Test-evidenced |
| Internet exposure search | `openscribe.co.uk` / Cloudflare edge | Shodan/Censys | Both require authenticated session/API key; no public results obtainable without auth | Not in scope |
| Metadata paths | `openscribe.co.uk` | Python HTTP capture | `robots.txt`, `sitemap.xml`, `.well-known/security.txt`, docs exposure | Test-evidenced |
| Static asset review | `openscribe.co.uk` public pages | ZAP passive | Script/style/font origins and CSP fit | Test-evidenced |
| HTML comments/debug output | `openscribe.co.uk` public pages | ZAP passive | Redacted page-source notes | Test-evidenced |
| API docs exposure | `openscribe.co.uk` | Python HTTP capture | `/docs` and OpenAPI visibility by environment | Test-evidenced |
| External passive DNS | `openscribe.co.uk` | HackerTarget `dnsquery`, `httpheaders` | Cloudflare-only A/AAAA/NS; no origin IP exposed; all hardening headers present | Test-evidenced |

## External Recon Commands To Run Later

Use only against approved targets.

```bash
curl -I https://approved-openscribe.example/
curl -s https://approved-openscribe.example/robots.txt
curl -s https://approved-openscribe.example/sitemap.xml
curl -s https://approved-openscribe.example/.well-known/security.txt
```

Record commands with secrets/cookies omitted. Store only redacted summaries in git.

## Search Engine Results

- DuckDuckGo `site:openscribe.co.uk`: zero results.
- Bing `site:openscribe.co.uk`: zero `b_algo` result blocks.
- Google: bot-detected on `webfetch`; manual browser check recommended if full evidence needed.
- Conclusion: No search engine indexed pages found for `openscribe.co.uk`. Site is new/not yet indexed. `/robots.txt` intentionally allows `/` so indexing is not blocked; it has simply not occurred yet.

## Wayback Machine Results

- CDX API (`web.archive.org/cdx/search/cdx?url=openscribe.co.uk/*`): empty response, no captures.
- `/web/2026/https://openscribe.co.uk/`: HTTP 302 to save page; confirms domain has never been archived.
- Conclusion: Zero historical captures accessible through Wayback Machine.

## Shodan/Censys Results

- Shodan web search: 503 timeout or requires login; API requires key; no public results.
- Censys web search: `403 Forbidden`; requires authenticated session.
- Conclusion: Neither service allows unauthenticated passive queries. Authenticated account/API key needed for future internet-exposure enumeration. Marked `Not in scope` for this public passive cycle.

## External Passive DNS (HackerTarget)

- A records: `188.114.96.1`, `188.114.97.1` (Cloudflare anycast IPv4).
- AAAA records: `2a06:98c1:3121::1`, `2a06:98c1:3120::1` (Cloudflare anycast IPv6).
- NS records: `candy.ns.cloudflare.com.`, `clayton.ns.cloudflare.com.`
- SOA: `candy.ns.cloudflare.com. dns.cloudflare.com. 2402982055 10000 2400 604800 1800`.
- HTTP headers (HackerTarget): confirms all production security hardening headers present; `no-store` on HTML; CSRF cookies set; `x-served-by: op`; Cloudflare edge.
- Conclusion: Domain only resolves to Cloudflare edge. No origin IP, no non-Cloudflare services, no MX visible. Origin IP protection effective.

Full passive recon evidence: `07-tool-outputs/passive-recon-search-archive-exposure-2026-06-14.md`.

## Open Questions

- Should `/docs` and `/openapi.json` remain public on `openscribe.co.uk`, or be disabled/restricted in production?
- Should `/.well-known/security.txt` be published with a supplier-assurance/security contact?
- Should public `robots.txt` and `sitemap.xml` exist, or should the current login redirect be retained intentionally?
- What staging URL is authorised for crawler/scanner evidence?
- Which additional public subdomains belong to OpenScribe and are authorised for passive recon?

## ZAP Baseline Summary

OWASP ZAP baseline was run with unauthenticated target `https://openscribe.co.uk`, 1-minute spider, report-only/passive baseline mode. Outputs are stored under `07-tool-outputs/zap/`.

| Result | Count |
| --- | --- |
| URLs observed | 12 |
| FAIL alerts | 0 |
| WARN alert types | 9 |
| PASS passive rules | 58 |

| Alert | Risk | Initial triage |
| --- | --- | --- |
| Absence of Anti-CSRF Tokens | Medium | Resolved by server-rendered `_csrf_token` fields and production ZAP retest. |
| Cookie No HttpOnly Flag | Low | Accepted design warning for readable signed `openscribe_csrf`; regression tests verify session/trusted-device cookies are HttpOnly and CSRF cookie alone is not auth. |
| Permissions Policy Header Not Set | Low | Resolved by shared header middleware and production ZAP retest. |
| Cross-Origin-Embedder-Policy Header Missing or Invalid | Low | Resolved by `Cross-Origin-Embedder-Policy: credentialless` and production ZAP retest. |
| Strict-Transport-Security Multiple Header Entries | Low | Resolved by Cloudflare HSTS ownership and production ZAP retest. |
| Re-examine Cache-control Directives / Non-Storable Content / Retrieved from Cache / Storable and Cacheable Content | Informational | Accepted as `OWASP-2026-06-14-015`: `/`, public auth/account pages, and `/api`/`/api/` are `no-store`; metadata and static assets are public, cookie-free, and cacheable. Production ZAP retest left only accepted design/heuristic alerts. |
| Authentication Request Identified / Session Management Response Identified | Informational | Accepted as `OWASP-2026-06-14-016`: expected `/login` auth form and anonymous CSRF cookies; regression tests prove CSRF cookies alone are not auth and auth-bearing cookies remain `HttpOnly`. |

Remediation plan: `11-remediation-plan.md`.

## Public Metadata Decision

Current code publishes explicit metadata responses instead of redirecting these paths to login:

- `/robots.txt`: public `text/plain` crawl guidance, disallowing app/auth/API areas.
- `/.well-known/security.txt`: public `text/plain` contact metadata using `mailto:oscar@meddleapp.com`.
- `/sitemap.xml`: intentional `404` because no public sitemap is published for the app.

These routes are public, do not expose transcript-derived content, and do not issue CSRF cookies.
