# System Administrator Tutorial

## Audience and boundary

This guide is for system administrators using the browser admin workspace after bootstrap. First-time infrastructure/bootstrap is in [system-admin-setup.md](system-admin-setup.md).

System administrators manage platform/team metadata, provider provisioning, defaults, quotas, account requests, and security/usage metadata. They are admin-only accounts and cannot own or read transcript-derived content through normal product routes.

## Admin workspace

Use `/admin` as the canonical and only admin workspace. The former `/legacy-admin` and `/admin2` development/compatibility routes have been removed.

The current workspace includes global and team-scoped areas such as:

- provider directory/policy;
- teams and members;
- account requests and system administrators;
- STT, LLM, de-identification, clinical NLP, and hallucination-check configuration;
- default Templates and Quick Actions;
- user/team quotas;
- aggregate usage/failure metadata;
- security audit metadata;
- team danger/deletion controls.

Admin pages must not expose transcript, working-note, dictation, generated-document, prompt, raw provider response, credential, reset/setup token, TOTP/recovery code, or plaintext session content.

## Teams

System administrators can create/manage/delete teams subject to current blockers and cleanup.

Before team deletion:

- confirm the exact team and authorization;
- resolve linked system-administrator accounts, because deletion blocks while a system-admin remains attached;
- understand that normal users and their owner content can be hard-deleted through the team lifecycle;
- verify team Templates/Quick Actions, provider selections/configs, usage/quota rows, account-request links, and Vault cleanup intents are included by current services;
- treat the operation as irreversible.

Do not use team deletion to pause access. Suspend users or change team status/policy where the product supports a reversible action.

## Users and account requests

System administrators can:

- review all account requests;
- create normal users/leaders across teams;
- create additional system administrators through protected admin flows;
- send activation/setup, password-reset, and account-recovery links;
- use approved break-glass recovery when email is unavailable and policy permits;
- suspend/reactivate eligible accounts;
- reset MFA;
- hard-delete eligible users subject to protected-account/self/last-admin rules.

System-admin lifecycle actions do not grant owner-content visibility.

Hard user deletion removes implemented owner transcript-derived content, personal assets, sessions/trusted devices, key metadata, and related rows/cleanup according to current cascades. It has no undo path.

## Provider provisioning

Raw provider credentials belong in Vault/deployment identity, not PostgreSQL or docs/logs.

Provisioning areas include:

- speech-to-text;
- LLM/writing assistants;
- Gemini Enterprise identity/configuration;
- de-identification;
- clinical NLP;
- hallucination-check provider selection.

For credential-bearing STT/LLM providers:

1. select the team and provider preset;
2. enter endpoint/identity metadata;
3. enter the credential once;
4. inspect/discover supported contracts/models;
5. finalize a ready config;
6. run the saved diagnostic where available;
7. activate it and verify leader selection options;
8. confirm retired/orphan secret cleanup jobs remain healthy after edits/deletes.

Draft/revision credential inheritance copies to a draft-owned Vault reference. Never implement or document an alias to an active secret as a substitute for rollback-safe versioning.

Detailed references:

- [../stt-config.md](../stt-config.md)
- [../llm-providers.md](../llm-providers.md)
- [../gemini-enterprise-setup.md](../gemini-enterprise-setup.md)

## Provider diagnostics and safe errors

Provider tools may show bounded operational data:

- provider/config/model labels;
- setup/credential status;
- sanitized error code/status;
- latency/duration/size/counts;
- synthetic bundled test output where a diagnostic intentionally transcribes a repository fixture.

They must not show/log raw secrets, arbitrary provider response bodies, patient content, prompt bodies, generated clinical text, or unrestricted Vault references.

A successful technical diagnostic does not establish data-protection or clinical suitability. Deployment owners must separately assess contracts, subprocessors, residency, retention, model behavior, consent, and local approval.

## Team provider policy

System administrators provision providers; leaders select among eligible options for their own team. Admins can also manage policy across teams.

Selections are metadata references and can be purpose-specific. Consultation STT and post-consultation dictation STT should be checked separately. Later selection changes do not rewrite already queued ingestion snapshots.

## Quotas and usage

The admin workspace can manage per-user/team/provider accounting controls and display aggregate usage/failure metadata.

Allowed examples:

- request/attempt counts and status;
- audio seconds/bytes;
- token counts;
- latency/duration;
- provider/model/config labels;
- estimated cost;
- quota limits/grants/expiry/reset metadata.

Quota/usage data is not content access. Do not add prompts, transcripts, notes, dictation, PII values, raw audio, or raw provider responses to usage/audit tables.

When resetting/revoking quotas, understand the active reservation/attempt lifecycle rather than editing aggregate counters directly.

## Defaults and shared assets

System administrators manage global default Templates and Quick Actions. Team leaders manage own-team assets.

- Keep reusable instructions generic and patient-free.
- Use synthetic examples.
- Validate structured EMIS section keys and required instructions.
- Review active/version behavior after edits.
- Imported bundle metadata must not become ownership/team/version/creator authority.
- Mark output as draft requiring clinician review where appropriate.

## Security audit

Audit views contain bounded metadata only. Useful investigation dimensions include action, outcome, reason code, actor/target/team IDs, route/method, sanitized IP/user agent, and timestamp.

Subject identifiers such as login/reset email are HMAC digests where recorded, not plaintext. Never add request bodies or sensitive values to audit detail payloads.

Proxy/audit IP trust must be configured deliberately; forwarded headers are not trustworthy merely because they exist.

## Production operations

The single-host persistent Docker profile is a local/small-host runtime baseline. Before clinical production, confirm:

- external TLS/proxy and HSTS ownership;
- production cookie/CSRF/proxy trust settings;
- least-privilege database/Redis/Vault identities;
- consistent PostgreSQL + Vault backups and restore drills;
- persistent worker/Beat monitoring;
- retention/audio/provider-secret cleanup health;
- provider/subprocessor governance;
- logging/audit monitoring without content leakage;
- tested account recovery and destructive deletion procedures.

See [../docker.md](../docker.md), [../environment.md](../environment.md), and [../security.md](../security.md).

## Escalate architecture changes

Do not silently change:

- owner-only content policy;
- admin-only account ownership;
- hard-delete/retention roots;
- DEK/KEK and Vault boundaries;
- provider secret versioning/cleanup;
- durable outbox/quota settlement semantics;
- structured-note JSON contracts;
- sharing/export of transcript-derived content.

Use a focused plan, migrations/services/tests, and update the operational docs/README when such a change is approved.
