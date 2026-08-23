# Cookies and browser storage

This document records the browser-side storage used by the current OpenScribe application. It supports the operator cookie/browser-storage notice and DSPT evidence. It does not describe every cookie or log that a reverse proxy, CDN, identity provider, or modified deployment may add.

Last reviewed: 21 August 2026.

## Scope and method

The review covered cookie issuance in `app/main.py`, cookie names and lifetimes in the authentication and CSRF services, and every `localStorage` and `sessionStorage` reference under `app/`. Focused cookie, legal-content, workspace, admin-UI, and JavaScript contract tests provide regression evidence. A deployment check remains necessary before publication because Cloudflare or another proxy can add storage independently of OpenScribe.

No application use of IndexedDB, Cache Storage, service workers, advertising storage, or third-party analytics SDK storage was found in this review.

## Application cookies

| Name | Purpose and data | Lifetime | Classification | Protection |
| --- | --- | --- | --- | --- |
| `openscribe_session` | Opaque sign-in session token. The database stores its hash and server-side session state. | 12 hours | Essential sign-in and security | `HttpOnly`, `SameSite=Lax`, path `/`; `Secure` is mandatory in production |
| `openscribe_trusted_device` | Opaque token for the optional remembered-device MFA flow. It cannot authenticate by itself. | Up to 30 days | Optional sign-in convenience and security | `HttpOnly`, `SameSite=Lax`, path `/`; `Secure` is mandatory in production |
| `openscribe_csrf` | Signed CSRF value bound to the anonymous nonce or authenticated session. It is not authentication authority. | Browser session | Essential request-integrity protection | `HttpOnly`, `SameSite=Lax`, path `/`; `Secure` is mandatory in production |
| `openscribe_csrf_anon` | Random nonce used to bind an anonymous CSRF value before sign-in. | Browser session | Essential request-integrity protection | `HttpOnly`, `SameSite=Lax`, path `/`; deleted when an authenticated session is issued |
| `openscribe_oidc_state` | One-time random value binding an OIDC callback to the browser, provider, and server-side authorization request. | Up to 10 minutes | Essential only while OIDC sign-in/linking is in progress | `HttpOnly`, provider-specific callback path; `SameSite=None; Secure` for production `form_post`, or `SameSite=Lax` for local query mode; deleted on callback |
| `openscribe_oidc_verifier` | One-time PKCE verifier. The database stores only its SHA-256 digest. | Up to 10 minutes | Essential only while OIDC sign-in/linking is in progress | Same provider-specific callback protection and deletion rule as `openscribe_oidc_state` |

Published `/privacy`, `/cookies`, and `/terms` pages, public metadata routes, and static assets do not issue OpenScribe CSRF cookies. Public account-request and sign-in pages do issue anonymous CSRF cookies because they contain state-changing forms.

## Application local storage

`localStorage` has no built-in expiry. These entries remain for the site origin until the application overwrites them or the browser/user clears site data.

| Key | Value and purpose | Classification |
| --- | --- | --- |
| `openscribe_browser_storage_notice_v1` | A value such as `dismissed:3:2`, where the suffix is the server-published cookie-notice version and operator-profile revision; remembers dismissal only for that notice metadata. | Interface preference |
| `openscribe:workspace:sidebar-width` | A bounded numeric sidebar width. | Interface preference |
| `openscribe:transcribe:sidebar-width` | A bounded numeric Scribe sidebar width. | Interface preference |
| `openscribe:transcribe:consultations-open` | Boolean text recording whether the consultations panel is open. | Interface preference |
| `openscribe-glm2-pane-state` | One of the bounded Scribe pane states. | Interface preference |
| `openscribe-glm2-split-ratio` | A bounded numeric Scribe pane split ratio. | Interface preference |
| `openscribe:transcribe:followup-history-open` | Boolean text recording whether follow-up history is expanded. | Interface preference |
| `openscribe:tour:home:<role>` | `dismissed`; remembers completion/dismissal of the legacy home tour for a user role. | Interface preference |
| `openscribe:tour:transcribe:<role>` | `done`; remembers completion/dismissal of the Scribe tour for a user role. | Interface preference |
| `openscribe:dictation-nudge:<transcript UUID>` | `shown`; prevents the same post-consultation dictation nudge being repeated for that transcript. | Transcript-linked metadata |
| `openscribe-glm2-recording-durations` | JSON map from transcript UUID to elapsed recording milliseconds, used to preserve the displayed timer across page reloads. It does not contain audio or transcript text. | Transcript-linked metadata |

The two transcript-linked entries are not authentication authority and cannot bypass server-side ownership or retention checks. They can, however, remain in a shared browser profile after sign-out because no automatic local expiry or removal was found. This is a data-minimisation finding. Before relying on the final public notice, decide whether to move them to `sessionStorage`, remove entries when their transcript is deleted or expires, or document and justify a bounded persistence rule.

## Application session storage

| Key | Value and purpose | Lifetime | Classification |
| --- | --- | --- | --- |
| `openscribe.workspace.lastTranscriptId` | Active transcript UUID used only as an untrusted navigation hint. Every use repeats server-side owner and retention checks. | Current browser tab/session | Transcript-linked navigation metadata |

No session, trusted-device, CSRF, provider credential, audio, transcript text, Working note, generated note, prompt, or PII value is intentionally stored in Web Storage.

## Cloudflare boundary

A read-only Cloudflare API and public-response review was reported on 10 August 2026 for the active `openscribe.co.uk` zone. The repository does not retain the API response or a dashboard export, so these account and zone observations are deployment evidence to recheck, not independently verified application behavior:

- the zone uses Cloudflare's Free Website plan;
- Bot Fight Mode, JavaScript bot detection, crawler protection, Browser Integrity Check and Privacy Pass are enabled;
- the security level is `medium` and challenge state lasts 1,800 seconds;
- managed/custom firewall and rate-limit ruleset phases exist;
- Always Online is disabled, Always Use HTTPS is enabled and edge-to-origin SSL mode is `strict`; and
- one ordinary request to the public home page set no Cloudflare cookie and its returned HTML contained no Cloudflare Web Analytics, RUM or Zaraz beacon marker.

The one-request sample does not prove that Cloudflare never sets cookies. Cloudflare's current documentation says its security and traffic products may set strictly necessary cookies conditionally. Bot Fight Mode can use `__cf_bm`; a challenge can use `cf_clearance`; and unique-visitor rate limiting can use `_cfuvid`. These cookies may appear only when the relevant detection, challenge or rule is exercised.

Cloudflare's Free-plan Security Events dashboard uses sampled records and can show source IP address, user agent, path, country and related security actions for up to 24 hours. Cloudflare documents up to seven days of historical Security Analytics data for the Free plan. Zone HTTP Traffic Analytics also processes request, bandwidth, country and unique-visitor metrics at the edge without requiring a browser analytics script. Logpush is an Enterprise facility; the API used for this review did not authorize Logpush or Web Analytics account endpoints, so the review does not claim an account-level setting or absence from those API responses.

Before Memre publishes the final deployment-specific notice, retain evidence of:

- which Cloudflare cookies are observed over a representative public, sign-in, authenticated and challenged route sample;
- whether a Web Analytics site, Zaraz configuration or unique-visitor rate-limit rule exists, using dashboard access or a token authorised for those account endpoints;
- who can access the Cloudflare account and whether MFA and recovery controls are enabled; and
- whether Cloudflare data-localisation controls are used.

Subject to retained deployment evidence, the public notice may say that Cloudflare processes IP address, request, device/browser and security information to deliver and protect the service, uses edge traffic/security analytics with the plan-bounded history above, and may set essential bot/challenge cookies. It should not claim that Cloudflare stores only IP addresses or never sets cookies.

## Evidence and sources

- `app/main.py`
- `app/services/auth.py`
- `app/services/csrf.py`
- `app/services/oidc.py`
- `app/static/js/legal-content-banner.js`
- `app/static/js/workspace/app.js`
- `app/static/js/transcribe/`
- `app/templates/transcribe/_shell_extras.html`
- `app/templates/home.html`
- `tests/test_cookie_csrf_security.py`
- `tests/test_legal_content.py`
- `tests/test_workspace_frontend_contract.py`
- `tests/test_admin_ui.py`
- `tests/test_web_refactor.py`
- [Cloudflare Cookies](https://developers.cloudflare.com/fundamentals/reference/policies-compliances/cloudflare-cookies/), reviewed 10 August 2026
- [Cloudflare HTTP request log fields](https://developers.cloudflare.com/logs/reference/log-fields/zone/http_requests/), reviewed 10 August 2026
- [Cloudflare Security Events](https://developers.cloudflare.com/waf/analytics/security-events/), reviewed 10 August 2026
- [Cloudflare Zone Analytics](https://developers.cloudflare.com/analytics/account-and-zone-analytics/zone-analytics/), reviewed 10 August 2026
