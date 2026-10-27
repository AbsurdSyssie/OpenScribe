# System Administrator Brief

## Status

This is the concise role/product brief for the canonical `/admin` workspace. Detailed route/tab/control behavior is maintained in [admin_workspace_function_map.md](admin_workspace_function_map.md). Provider contracts are maintained in [stt-config.md](stt-config.md), [llm-providers.md](llm-providers.md), and [api.md](api.md).

Canonical route:

- `/admin`

The former `/legacy-admin` and `/admin2` compatibility/development surfaces have been removed. `/admin` is the only system-administrator browser workspace.

## Role boundary

System administrators manage platform and team metadata, accounts, providers, defaults, quotas, aggregate usage, and security audit data. Selected-member quota cards live in the standalone Usage Quotas view; per-team quota defaults live in Defaults and apply to new members only.

They do not gain access to another user's transcript, working note, dictation, generated document, prompt, redaction/PII, or uploaded audio. System-admin accounts cannot own transcripts.

The admin UI must never expose raw provider credentials, unrestricted Vault references, password/reset/setup material, TOTP/recovery values, cookies, plaintext session tokens, or raw patient-content provider responses.

## Primary functions

### Teams

- create and open teams;
- view/edit supported team policy metadata such as future-root retention default in the selected team's Defaults tab;
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
- provider secrets are Vault-backed; Gemini can use the deployment's runtime identity;
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

The Admin landing destination is All teams Usage (`/admin/usage?view=team&range=30d`). The compact Team picker opens a selected team workspace or starts team creation in a dialog. Global administration remains a separate group for Account requests, System admins, Global defaults, De-identification providers, Audit, and Legal content. Global pages remain unscoped; the picker displays the remembered team name and the Usage link restores that team's last section without filtering global data.

Selected-team primary tabs are Overview, Members and Quotas; Usage opens its
dedicated standalone route with local Team/Users/Quotas destinations. Provider
policy, STT, LLM and De-identification are grouped under Providers; Defaults,
Security and Danger zone are grouped under Settings. The active nested page
keeps its existing `team_tab` URL value and is marked as current. Overview
summarizes the retention default and links to its editor in Defaults.

Team and tab are validated URL state. A per-tab session-storage hint remembers only the validated team ID, allowlisted section/Usage view, and Usage range; it does not store form drafts, member IDs, or secrets. URLs, browser history, refresh, and explicit All teams navigation remain authoritative. Unsaved form edits prompt before navigation. The account disclosure retains POST logout, and explicit All teams selection and logout clear the admin navigation hint.

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
