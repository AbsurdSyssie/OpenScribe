# 07 - Passive Recon Tool Outputs: Search, Archive, Internet Exposure

Date: 2026-06-14
Target: `openscribe.co.uk`
Scope: public passive recon only

## Search Engine Index Check

| Engine | Method | Result | Notes |
| --- | --- | --- | --- |
| Google `site:openscribe.co.uk` | Browser/Javascript-blocked; `webfetch` returned no useful results | No indexed pages confirmed; bot detection blocked automated verification | Manual browser check recommended if full evidence needed |
| DuckDuckGo `site:openscribe.co.uk` | `webfetch` HTML result | No results returned | DDG reports "No results found for site:openscribe.co.uk" |
| Bing `site:openscribe.co.uk` | `curl` with browser UA; result count `0` from `class="b_algo"` pattern | Zero indexed results | Confirmed zero `b_algo` result blocks |

Conclusion: No search engine indexed pages found for `openscribe.co.uk`. Site is new/not yet indexed. `/robots.txt` intentionally allows `/` (splash) so indexing is not blocked; indexing has simply not occurred yet.

## Wayback Machine Archive Check

| Source | Method | Result | Notes |
| --- | --- | --- | --- |
| Wayback CDX API | `curl` to `web.archive.org/cdx/search/cdx?url=openscribe.co.uk/*` | Empty response (no captures) | No historical snapshots exist |
| Wayback availability API | `curl` to `web.archive.org/web/2026/https://openscribe.co.uk/` | HTTP 302 to save page | Confirms domain has never been archived |

Conclusion: `openscribe.co.uk` has zero Wayback Machine captures. No historic pages, versions, or deleted content is accessible through the archive.

## Internet Exposure Check (Shodan/Censys)

| Source | Method | Result | Notes |
| --- | --- | --- | --- |
| Shodan web search | `curl`/`webfetch` to `shodan.io` | 503 timeout or requires login; no results without authentication | Shodan requires authenticated API or web login for search |
| Shodan API | `curl` to `api.shodan.io/shodan/host/search` without key | `401 Unauthorized` | API key required for any Shodan query |
| Censys web search | `webfetch` to `search.censys.io` | `403 Forbidden` | Censys requires authenticated session for search |

Conclusion: Neither Shodan nor Censys allows unauthenticated passive queries. Authorised account/API key needed for future internet-exposure enumeration.

## External Passive DNS (HackerTarget)

| Record | Value | Notes |
| --- | --- | --- |
| A | `188.114.96.1`, `188.114.97.1` | Cloudflare anycast edge IPv4 |
| AAAA | `2a06:98c1:3121::1`, `2a06:98c1:3120::1` | Cloudflare anycast edge IPv6 |
| NS | `candy.ns.cloudflare.com.`, `clayton.ns.cloudflare.com.` | Cloudflare nameservers |
| SOA | `candy.ns.cloudflare.com. dns.cloudflare.com. 2402982055 10000 2400 604800 1800` | Standard Cloudflare SOA |

Conclusion: Domain only resolves to Cloudflare edge. No origin IP, no non-Cloudflare services, no MX records visible in passive DNS. Origin IP protection is effective.

## Production HTTP Header Sample (Passive HTTPS)

| Path | Status | Cache-Control | CSRF cookie | Security headers |
| --- | --- | --- | --- | --- |
| `https://openscribe.co.uk/` | 301→200 (via HTTPS) | `no-store` | `openscribe_csrf_anon` (HttpOnly), `openscribe_csrf` (Secure, SameSite=lax) | HSTS, CSP, COEP, COOP, CORP, Permissions-Policy, X-Frame-Options, X-Content-Type-Options, Referrer-Policy |
| `http://openscribe.co.uk/` | 301 | N/A | None | Cloudflare HTTPS redirect |

Observations:
- All security hardening headers confirmed present on production.
- `no-store` confirmed on root and auth pages.
- Cloudflare handles HTTPS redirect and HSTS; app `HSTS_SOURCE=proxy`.
- No secrets, tokens, cookie values, or content recorded.

## Redaction Note

No cookies, tokens, account data, transcript/note content, prompts, provider responses, or audio were committed. Only redacted summaries and public HTTP headers are stored.
