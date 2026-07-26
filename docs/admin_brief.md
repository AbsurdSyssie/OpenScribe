# System Administrator Brief

## Status

This is the concise role/product brief for the canonical `/admin` workspace. Detailed route/tab/control behavior is maintained in [admin_workspace_function_map.md](admin_workspace_function_map.md). Provider contracts are maintained in [stt-config.md](stt-config.md), [llm-providers.md](llm-providers.md), and [api.md](api.md).

Canonical route:

- `/admin`

Compatibility/development surfaces:

- `/legacy-admin`: deprecated earlier template;
- `/admin2`: alternate development/reference UI.

Neither compatibility route overrides the canonical `/admin` behavior.

## Role boundary

System administrators manage platform and team metadata, accounts, providers, defaults, quotas, aggregate usage, and security audit data.

They do not gain access to another user's transcript, working note, dictation, generated document, prompt, redaction/PII, or uploaded audio. System-admin accounts cannot own transcripts.

The admin UI must never expose raw provider credentials, unrestricted Vault references, password/reset/setup material, TOTP/recovery values, cookies, plaintext session tokens, or raw patient-content provider responses.

## Primary functions

### Teams

- create and open teams;
- view/edit supported team policy metadata such as future-root retention default;
- inspect blockers and hard-delete an eligible team through the Danger zone;
- understand deletion as immediate/irreversible and content/key/provider-cleanup aware.

### Users and requests

- review all account requests;
- create normal users/leaders in an explicit team;
- create/manage additional system-administrator accounts through protected flows;
- send activation, password-reset, and account-recovery links;
- perform policy-approved break-glass recovery;
- reset MFA;
- suspend/reactivate/delete eligible accounts;
- protect self/last-active-admin and team/role boundaries;
- manage selected-member quotas.

Account management remains metadata-only and does not grant owner-content review.

### Provider provisioning

System administrators provision and inspect credential-bearing configurations for:

- speech-to-text;
- LLM/writing assistants, including Gemini Enterprise;
- de-identification;
- clinical NLP;
- hallucination checking through current LLM policy.

Important rules:

- provider policy selection is separate from credential provisioning;
- secrets are Vault-backed or supplied through deployment identity;
- responses show bounded status/`has_secret`, never the secret/reference;
- required-auth drafts/revisions copy inherited credentials into draft-owned unique versioned Vault paths—they do not alias the active root's secret reference;
- replacement/removal/deletion/cancel/promotion use durable cleanup intent, retries, and live-reference guards;
- synthetic inspection content only is permitted in admin diagnostics;
- successful connectivity does not establish regulatory/clinical suitability.

### Team provider policy

Within a selected team, admins can manage active selections/defaults for supported purposes. Consultation and post-consultation dictation STT can differ. LLM policy controls provider, allowed models, and default model. De-identification and clinical NLP remain separate selections.

Queued work retains its snapshotted execution metadata; later policy edits do not silently retarget an existing job.

### Defaults and reusable assets

- manage global default Templates and Quick Actions;
- view team asset summaries/navigate to leader-owned management;
- preserve owner/team/version authority on import/export;
- never place patient/transcript content in reusable prompts/instructions.

### Usage and audit

Usage surfaces expose metadata-only aggregates for global or validated team scope. See [usage_tab.md](usage_tab.md).

Audit surfaces expose bounded action/outcome/actor/target/team/route/IP/user-agent metadata. They exclude request bodies, credentials, tokens, raw emails where subject hashing is required, and all transcript-derived content.

## Navigation summary

Global areas:

- Admin home;
- Teams / Manage teams;
- Account requests;
- System administrators;
- Global defaults;
- De-identification providers;
- Usage;
- Audit.

Selected-team tabs:

- Overview;
- Members;
- Provider policy;
- STT;
- LLM;
- De-identification;
- Defaults;
- Usage;
- Security;
- Danger zone.

Team and tab are validated URL state. The workspace does not guess a team when none is selected.

## Security and browser behavior

- All mutations are explicit CSRF-protected POST operations or authenticated JSON APIs.
- Server authorization is authoritative; hidden controls are not a security boundary.
- Secret inputs are write-only and never repopulated after validation errors.
- Safe non-secret form values can be preserved.
- Redirect-after-POST preserves only closed/validated return state.
- Runtime assets are same-origin and CSP-compatible.
- Provider/usage/audit errors remain bounded and content-safe.

## Operational responsibilities

Before clinical production, administrators/operators must establish:

- HTTPS/reverse-proxy/HSTS/cookie/CSRF/forwarded-header policy;
- least-privilege database/Redis/Vault/provider identities;
- coordinated PostgreSQL + Vault backup and restore drills;
- monitoring for web, worker, Beat, database, Redis, Vault, outbox, quota, retention, source-audio cleanup, and provider-secret cleanup;
- provider/subprocessor governance;
- account recovery and destructive deletion procedures;
- audit review and incident escalation;
- user training and clinical review expectations.

The persistent Docker profile is a single-host baseline, not a production security architecture. See [docker.md](docker.md), [environment.md](environment.md), and [security.md](security.md).
