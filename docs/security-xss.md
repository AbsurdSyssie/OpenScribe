# XSS Testing

This document records the current XSS test plan and the repeatable probe script for OpenScribe.

## Scope

The goal is to detect straightforward reflected or stored XSS in:

- public browser pages
- authenticated browser pages
- user-controlled labels and titles rendered back into the UI

The current probe set is intentionally non-destructive. It checks whether inert HTML-like payloads are reflected as live markup or only as escaped text.

## Public pages checked

Target public pages:

- `/login`
- `/request-access`

Checks:

- query and form input are not reflected as live HTML
- failed-login responses do not echo attacker-controlled values into the page
- request-access success pages do not render submitted values unsafely

Manual public-site result noted on `https://medscribe.duckdns.org`:

- no obvious reflected XSS observed on `/login`
- no obvious reflected or post-submit HTML injection observed on `/request-access`
- a direct `<script>` query probe triggered a front-door `403`, likely upstream filtering, so the manual verification relied on inert HTML markers instead

## Authenticated/stored candidates

The first authenticated/stored checks target user-editable content that is later rendered in browser pages:

- personal template names and descriptions
- personal quick-action names and descriptions
- transcript titles

Recommended follow-up coverage after credentials are available:

- team template names and descriptions
- team quick-action names and descriptions
- account-request review screens
- provider labels
- generated-document titles

## Script

Run the probe script:

```bash
./.venv/bin/python scripts/security/xss_probe.py --base-url https://medscribe.duckdns.org --suite public
```

Authenticated suite:

```bash
OPENSCRIBE_EMAIL='user@example.com' \
OPENSCRIBE_PASSWORD='password-1' \
./.venv/bin/python scripts/security/xss_probe.py \
  --base-url https://medscribe.duckdns.org \
  --suite authenticated
```

All suites:

```bash
OPENSCRIBE_EMAIL='user@example.com' \
OPENSCRIBE_PASSWORD='password-1' \
./.venv/bin/python scripts/security/xss_probe.py \
  --base-url https://medscribe.duckdns.org \
  --suite all
```

JSON output:

```bash
./.venv/bin/python scripts/security/xss_probe.py --base-url https://medscribe.duckdns.org --suite public --json
```

## Interpretation

Expected safe outcomes:

- payload not reflected
- payload present only in escaped form

Potentially unsafe outcomes:

- payload reflected as live HTML or raw markup
- new DOM elements with probe attributes appear after submission/render
- probe strings execute as script or event handlers

## Limitations

- the script does not execute JavaScript payloads or attempt exploitation
- authenticated coverage depends on valid credentials and does not yet automate TOTP
- upstream WAF/proxy behavior can block some payloads before they reach the app
