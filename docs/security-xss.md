# XSS Testing

This document describes the repeatable, non-destructive reflected/stored HTML-injection probe in `scripts/security/xss_probe.py`. It is a focused regression aid, not a complete browser security assessment or evidence that a deployment is free from XSS.

## Scope

The probe submits inert HTML-like markers and checks whether they are absent or escaped rather than rendered as live markup.

Public coverage:

- `/login` failed-login reflection;
- `/request-access` submitted name/team/details reflection.

Authenticated localhost-development coverage:

- personal Template name/description rendered in `/workspace/library/templates`;
- personal Quick Action name/description rendered in `/workspace/library/quick-actions`;
- transcript title rendered in canonical `/workspace` Scribe state.

The authenticated suite creates temporary rows and attempts to delete them after each probe. Run it only with a disposable localhost seeded development account and database. The current script requires a full login session and does not automate TOTP; seeded development accounts have MFA disabled specifically for controlled local tooling.

## Run public probes

Against a local instance:

```bash
./.venv/bin/python scripts/security/xss_probe.py \
  --base-url http://127.0.0.1:8080 \
  --suite public
```

The base URL can also be provided through `OPENSCRIBE_BASE_URL`.

Do not aim the account-request probe at a production/shared instance without an approved test plan because it creates pending account-request records.

## Run authenticated probes

With the seeded localhost-only development user:

```bash
OPENSCRIBE_EMAIL='dev.user@example.com' \
OPENSCRIBE_PASSWORD='test1234' \
./.venv/bin/python scripts/security/xss_probe.py \
  --base-url http://127.0.0.1:8080 \
  --suite authenticated
```

The probe:

1. signs in through `POST /api/v1/auth/login`;
2. requires `auth_level=full`;
3. reads the session-bound CSRF token from the HTTP client's cookie jar;
4. sends `Origin` and `X-CSRF-Token` on unsafe cookie-authorized API requests;
5. uses canonical workspace routes;
6. cleans up created Template, Quick Action, and transcript rows when identifiers are returned.

A pending-MFA response causes the authenticated suite to stop with exit code `2` rather than bypassing MFA or accepting a partial session.

Run both suites:

```bash
OPENSCRIBE_EMAIL='dev.user@example.com' \
OPENSCRIBE_PASSWORD='test1234' \
./.venv/bin/python scripts/security/xss_probe.py \
  --base-url http://127.0.0.1:8080 \
  --suite all
```

JSON output:

```bash
./.venv/bin/python scripts/security/xss_probe.py \
  --base-url http://127.0.0.1:8080 \
  --suite public \
  --json
```

## Interpret results

Expected safe outcomes:

- marker not reflected;
- marker appears only in HTML-escaped form;
- no live probe tag/attribute appears in the response.

A failed result means the returned HTML contains the submitted marker or a matching live-tag pattern. Treat it as a review trigger; confirm with the template/DOM context before classifying severity.

Exit codes:

- `0`: all executed probes passed;
- `1`: at least one probe found potentially unsafe reflection;
- `2`: setup/auth/request execution failed or required credentials were missing.

## Additional automated checks

The primary template/CSP regression tests remain:

```bash
pytest -q tests/test_xss_coverage.py tests/test_cookie_csrf_security.py
rg '\bstyle\s*=' app/templates
rg 'cdn\.jsdelivr\.net|cdn\.tailwindcss\.com|unpkg\.com|fonts\.googleapis\.com|fonts\.gstatic\.com' app/templates app/static/js
```

Expected searches: no production runtime dependency or inline-style-attribute matches unless a focused reviewed exception has been introduced.

## Limitations

The probe does not:

- execute JavaScript payloads or drive a real browser DOM;
- test every user-controlled field or every encoding context;
- automate TOTP, leader, or system-administrator workflows;
- prove CSP effectiveness against browser-specific behavior;
- replace dependency review, template escaping tests, Playwright coverage, CSP/header validation, SAST, DAST, or manual security review;
- preserve dated evidence for a deployment.

Recommended expansion areas include team assets, provider labels, account-request review, generated-document titles, imported bundle fields, and other server-rendered metadata. Add each case with automatic cleanup, current CSRF/origin handling, and safe synthetic values.

Historical manual results belong in dated compliance/security-evidence folders. Do not keep an undated production-host result in this operational guide or treat a front-door/WAF block as proof that application rendering is safe.
