# 03 - Architecture Map

Status: `Repo-evidenced` seed. Live deployment diagram and proxy-verified flow evidence still required.

## Components

| Component | Role | Trust boundary | Sensitive data handling |
| --- | --- | --- | --- |
| Browser/Jinja UI | User interaction for public, auth, transcribe, home, admin routes | Untrusted client | Receives owner-visible plaintext only after auth; submits CSRF token on unsafe browser/API requests. |
| FastAPI app | Auth, authorization, API routing, HTML rendering, provider orchestration | Main application boundary | Enforces owner/team/admin scope; must not log transcript text, notes, prompts, tokens, or provider secrets. |
| Postgres | Relational persistence | Private service boundary | Stores encrypted transcript-derived content; stores metadata, hashed auth/session/token material, provider metadata, Vault refs. |
| Redis | Rate-limit store and Celery broker/result backend | Private service boundary | Must stay localhost/private; exposure indirectly exposes queue/rate-limit state. |
| Vault | KEK/master-key layer, KV/transit, provider credentials, retry audio refs | Secret-management boundary | Stores/unwraps DEKs and provider secrets; DB stores refs, not raw secrets. |
| Celery worker | Async STT/generation processing | Internal worker boundary | Uses queued snapshots; fetches provider secrets from Vault; writes encrypted results. |
| Mail provider | Account setup/reset/recovery mail | External provider boundary | Receives email/reset links only; token plaintext should never be logged or stored. |
| STT provider | Audio transcription | External provider boundary | Receives normalized owner audio only through selected team config; credentials read from Vault. |
| LLM provider | Note/follow-up/quick-action generation | External provider boundary | Receives redacted transcript/Working-note/dictation context only; no raw secrets returned to UI. |
| De-identification provider | PII redaction/provider inspection | External or local provider boundary | Runtime redaction sends source text according to selected provider policy; external endpoint config requires HTTPS unless local/private/link-local. |
| Clinical NLP provider | Disease/symptom extraction | External or local provider boundary | Receives redacted transcript text unless admin enabled unredacted text only for local/private/link-local endpoints. |

## Data Classes

| Data class | Owner/visibility | Storage/lifecycle |
| --- | --- | --- |
| Transcript draft/version text | Owning user only | Encrypted with user DEK; transcript root is retention/deletion root. |
| Working note | Owning user only | Encrypted; follows transcript-root retention and deletion. |
| Generated document body/sections | Owning user only | Encrypted; deleted with generated document or transcript cascade. |
| Redaction original values/manual PII | Owning user explicit reveal only | Encrypted; follows transcript/redaction lifecycle. |
| Provider credentials | System-admin managed; never raw to browser/API | Vault secret; DB stores refs/metadata. |
| Team/user/provider metadata | Admin/leader scoped | Plain metadata; not transcript content. |
| Usage telemetry | Admin/system metadata | Must contain IDs/status/counts/provider names/errors/durations only, no content. |

## Main Trust Boundaries

1. Browser to FastAPI: session cookies, CSRF token, Origin/Referer checks, CSP, security headers.
2. FastAPI to Postgres: parameterized ORM/query layer; encrypted-at-rest content fields for transcript-derived data.
3. FastAPI/Celery to Vault: DEK unwrap/wrap, provider credentials, retry-source refs; Vault failures must fail closed for secret reads where needed.
4. FastAPI/Celery to Redis: rate-limits and job queue; Redis must not be exposed publicly.
5. FastAPI/Celery to third-party providers: STT/LLM/de-ID/clinical NLP calls; only selected, authorised, configured providers.
6. Admin/leader metadata boundary: admin/leader may manage accounts/config but do not gain transcript-content readability.
7. Transcript-root lifecycle boundary: transcript deletion cascades transcript-derived children; user deletion removes user-owned transcript-derived content.

## High-Risk Flows For OWASP Testing

| Flow | Risk | Evidence needed |
| --- | --- | --- |
| Login/MFA/trusted-device | Session fixation, MFA bypass, brute force, cookie flags | Role tests, headers/cookie capture, rate-limit evidence. |
| Account request/recovery | Enumeration, token leakage, break-glass misuse | Generic responses, token redaction, audit metadata, manager scope tests. |
| Transcript create/read/update/delete | Broken access control, plaintext leakage, deletion cascade failure | Owner/non-owner API tests, no-store headers, cascade checks. |
| Audio upload/live chunks | Upload abuse, content leakage, provider forwarding, SSRF-like provider path | Size/rate checks, owner checks, provider snapshot evidence. |
| Note generation | Prompt/content leakage, redaction failure, provider secret handling | Redaction fail-closed tests, provider payload redaction evidence. |
| Provider inspection | SSRF, raw secret return, raw provider response exposure | URL validation tests, canary endpoint tests, response redaction review. |
| Admin usage telemetry | Content leakage through logs/metrics | Sample telemetry/log review with synthetic data. |

## Repo-Backed Controls

- `docker-compose.yml` binds Postgres, Redis, and Vault to `127.0.0.1`.
- `docs/security.md` documents production secure-cookie requirement, HSTS, CSP, no-store sensitive responses, and local service exposure guardrails.
- `docs/api.md` documents owner-only transcript/generated-document/Working-note routes and provider secret metadata-only responses.
- `docs/auth.md` documents opaque DB-backed sessions, onboarding-only sessions, pending-MFA sessions, trusted-device freshness, and token hashing.
- `CONTEXT.md` documents Working-note ownership, retention, deletion, and redaction boundaries.

## Open Evidence Gaps

- Need a rendered architecture diagram or exported threat-boundary image for assurance packs.
- Need live header/cookie samples for each browser auth state.
- Need proxy crawl evidence for each role.
- Need SSRF canary evidence for provider inspection endpoints.
- Need persistent audit-event coverage review for actions still logger-only.
