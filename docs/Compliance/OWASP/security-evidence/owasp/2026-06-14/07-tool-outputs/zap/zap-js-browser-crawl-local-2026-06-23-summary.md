# ZAP JS Browser Crawl Summary - 2026-06-23

## Scope

- Tool: OWASP ZAP `2.17.0` AJAX Spider using the bundled headless browser.
- Environment: local dev app, `http://127.0.0.1:8080`.
- Account: seeded dev normal user via in-memory session-cookie header injection in ZAP Replacer. No cookies, passwords, session tokens, CSRF tokens, transcript text, note text, prompt text, provider responses, provider secrets, or audio are stored here.
- Method: conservative AJAX Spider against `/home` with JavaScript execution enabled.

## Safety Options

- `maxCrawlDepth = 1`
- `maxCrawlStates = 20`
- `numberOfBrowsers = 1`
- `clickElemsOnce = true`
- `clickDefaultElems = false`
- `randomInputs = false`
- `eventWait = 1000`
- `reloadWait = 1000`

No destructive POST/DELETE routes, upload routes, provider inspect/test calls, content-generating actions, or raw ZAP history exports were run intentionally. This crawl was limited to browser execution and shallow discovery from `/home`.

## Crawl Totals

| Metric | Value |
| --- | --- |
| AJAX spider browser states | 23 |
| ZAP messages | 31 |
| ZAP unique URLs | 11 |
| ZAP alert risks | `Medium`: 2, `Low`: 1, `Informational`: 2 |

## Unique Paths Observed

- `/`
- `/home`
- `/static`
- `/static/js`
- `/static/js/csrf.js`
- `/static/js/home`
- `/static/js/home/smart-phrases.js`
- `/static/vendor`
- `/static/vendor/lucide`
- `/static/vendor/lucide/1.8.0`
- `/static/vendor/lucide/1.8.0/lucide.min.js`

## Alert Triage

| Alert | Risk/count | Paths | Triage |
| --- | --- | --- | --- |
| `Absence of Anti-CSRF Tokens` | Medium / 2 | `/home` | Scanner heuristic on authenticated HTML page. Unsafe routes require same-origin `Origin`/`Referer` plus signed CSRF token; public server-rendered forms already have visible `_csrf_token` and passed production ZAP retest. |
| `Cookie No HttpOnly Flag` | Low / 1 | `/home` | Existing accepted design for `openscribe_csrf`, a signed non-authoritative CSRF token readable by same-origin JS. Auth-bearing `openscribe_session` remains `HttpOnly`; values were not committed. See `OWASP-2026-06-14-014`. |
| `Session Management Response Identified` | Informational / 1 | `/home` | Expected scanner session/cookie auto-detection. See `OWASP-2026-06-14-016`. |
| `Information Disclosure - Suspicious Comments` | Informational / 1 | `/home` | ZAP evidence matched a harmless section-divider CSS/comment fragment: `r state (for system admin forms) ... */`. No secret, token, route, transcript/note content, prompt, provider response, or deployment detail was exposed. No new finding created. |

## Residual Gaps

- This was a shallow local JavaScript crawl, not a full authenticated browser workflow test.
- This was local dev evidence, not staging/production authenticated evidence.
- No active scan, fuzzing, content mutation, upload, destructive route testing, or provider inspect/test calls were performed.
