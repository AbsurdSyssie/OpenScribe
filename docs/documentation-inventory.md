# Documentation inventory

Inventory date: 18 October 2026. This list groups documentation by function. It records files present in this checkout, including local files that the normal file listing omits. Review results and outstanding claims are in the [review record](reviews/documentation-review-2026-10-18.md).

## Scope and status

The inventory contains **277 documentation files**, plus the nine in-app documentation source files listed below. It includes first-party Markdown/text references, plans, historical progress notes, compliance records, the migration scaffold note, and the project licence. It excludes dependency documentation, third-party licence copies, generated test reports, caches, editor state, and local agent/skill configuration. [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) indexes bundled third-party licences.

A file listed here has not necessarily passed factual or editorial review. The review record states coverage and limits. Maintained references describe current behavior or label their history and remaining work. Plans describe intended work. Dated evidence records what a reviewer observed at that time. Preserve its results; add a new record for later assessments.

Local/ignored paths appear as plain text because another checkout may not contain them. Their presence here does not make them supported product documentation. Progress-note grouping uses filenames and does not validate the contents.

## Writing and review rules

[AGENTS.md](../AGENTS.md) requires concise, concrete, plain English; active voice where useful; and removal of unnecessary words without sacrificing technical, clinical, security, or legal precision. It also requires maintained documentation to match implementation, tests, and configuration.

Apply these steps in order:

1. Identify the audience, purpose, status, and scope. Separate current behavior, compatibility, history, and proposed work.
2. Check each behavior claim against the closest executable source: migrations/constraints for persistence; services, routes, schemas and configuration for runtime behavior; focused tests for corroboration. Record the source and any uncertainty.
3. Correct errors and missing steps before rewriting prose. Use authoritative external evidence for regulatory, clinical-governance, NHS-assurance, and deployment claims that code cannot prove.
4. Lead with the action or fact. Name the actor, use concrete terms, and keep product labels consistent with the interface. Use numbered steps for procedures and tables for comparisons.
5. Remove filler, vague emphasis, repeated explanations, rhetorical questions, slogans, and formulaic contrasts. Preserve conditions, limits, error handling, exact identifiers, and clinical-review requirements.
6. Check links and cited commands. Report checks actually run and keep unresolved claims visible.

Do not shorten a statement by weakening owner-only access, retention/deletion rules, encryption, redaction, quota/idempotency, structured-output validation, or clinician review. Do not restyle licence terms or rewrite dated assessment results.

## Areas

| Area | Files |
| --- | ---: |
| Project guidance and documentation | 10 |
| Setup, deployment and database | 10 |
| Identity, account recovery and access | 6 |
| Security, privacy and encryption | 86 |
| Audio capture, STT and recovery | 20 |
| Generation, Working note, dictation and clinical review | 27 |
| Templates, Quick Actions and Smart Phrases | 7 |
| Workspace and frontend | 22 |
| Providers, administration, usage and API | 14 |
| Tutorials and role guidance | 6 |
| Testing and accessibility | 15 |
| Clinical safety, compliance and legal notices | 10 |
| Engineering review records | 3 |
| Cross-cutting engineering history | 41 |

## Project guidance and documentation

| Document | Status / review handling |
| --- | --- |
| [AGENTS.md](../AGENTS.md) | Maintained reference/guidance |
| [CONTEXT.md](../CONTEXT.md) | Maintained reference/guidance |
| [DISCOVERED_ISSUES.md](../DISCOVERED_ISSUES.md) | Issue work log; not a behavior contract |
| [README.md](../README.md) | Maintained reference/guidance |
| [brief.md](../brief.md) | Maintained reference/guidance |
| [docs/README.md](README.md) | Maintained reference/guidance |
| [docs/documentation-inventory.md](documentation-inventory.md) | Maintained reference/guidance |
| [docs/feature_todo.md](feature_todo.md) | Maintained backlog; intended work |
| [docs/goals.md](goals.md) | Historical design/work log |
| [docs/progress.md](progress.md) | Historical design/work log |

## Setup, deployment and database

| Document | Status / review handling |
| --- | --- |
| [alembic/README](../alembic/README) | Migration scaffold note |
| [docs/DatabasePlan.md](DatabasePlan.md) | Maintained reference/guidance |
| [docs/dbroadmap.md](dbroadmap.md) | Historical design/work log |
| [docs/dek-kek-production-plan.md](dek-kek-production-plan.md) | Maintained reference/guidance |
| [docs/docker.md](docker.md) | Maintained reference/guidance |
| [docs/environment.md](environment.md) | Maintained reference/guidance |
| [docs/local-demo.md](local-demo.md) | Maintained reference/guidance |
| `docs/progress/Daily Note 01-4-26 Start Dev Bind Lockdown.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 30-3-26 Infra Hardening.md` (local/ignored) | Local historical work log |
| [docs/setup.md](setup.md) | Maintained reference/guidance |

## Identity, account recovery and access

| Document | Status / review handling |
| --- | --- |
| `CIS2.md` (local/ignored) | Local reference; not indexed as current |
| [docs/account_recovery_brief.md](account_recovery_brief.md) | Maintained reference/guidance |
| [docs/auth.md](auth.md) | Maintained reference/guidance |
| [docs/cis2.md](cis2.md) | Maintained reference/guidance |
| [docs/mfa-secret-encryption.md](mfa-secret-encryption.md) | Maintained reference/guidance |
| `docs/progress/Daily Note 14-6-26 Auth Session Scanner Triage.md` (local/ignored) | Local historical work log |

## Security, privacy and encryption

| Document | Status / review handling |
| --- | --- |
| [docs/Compliance/OWASP/00-scope-and-evidence-pack.md](Compliance/OWASP/00-scope-and-evidence-pack.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/01-information-gathering.md](Compliance/OWASP/01-information-gathering.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/OWASP_Context.md](Compliance/OWASP/OWASP_Context.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/README.md](Compliance/OWASP/README.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/00-scope.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/00-scope.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/03-architecture-map.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/03-architecture-map.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/04-passive-recon.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/04-passive-recon.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/05-server-fingerprinting.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/05-server-fingerprinting.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/06-proxy-crawl-summary.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/06-proxy-crawl-summary.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/README.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/README.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/audit/audit-logging-test-2026-06-15-local.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/audit/audit-logging-test-2026-06-15-local.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/cache-header-samples-2026-06-14.txt](Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/cache-header-samples-2026-06-14.txt) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/passive-http-tls-summary.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/passive-http-tls-summary.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/passive-recon-search-archive-exposure-2026-06-14.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/passive-recon-search-archive-exposure-2026-06-14.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/sbom-pip-audit-2026-06-14.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/sbom-pip-audit-2026-06-14.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/session-scanner-triage-2026-06-14.txt](Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/session-scanner-triage-2026-06-14.txt) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/tls-header-cookie-evidence-2026-06-14.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/tls-header-cookie-evidence-2026-06-14.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-auth-crawl-external-admin-2026-06-24-summary.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-auth-crawl-external-admin-2026-06-24-summary.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-auth-crawl-external-admin-private-ip-retest-2026-06-24-summary.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-auth-crawl-external-admin-private-ip-retest-2026-06-24-summary.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-auth-crawl-external-admin-private-ip-retest-pass-2026-06-24-summary.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-auth-crawl-external-admin-private-ip-retest-pass-2026-06-24-summary.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-auth-crawl-local-2026-06-15-summary.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-auth-crawl-local-2026-06-15-summary.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-auth-crawl-local-admin-2026-06-23-summary.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-auth-crawl-local-admin-2026-06-23-summary.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-auth-crawl-local-onboarding-mfa-2026-06-23-summary.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-auth-crawl-local-onboarding-mfa-2026-06-23-summary.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-baseline-retest-2026-06-14.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-baseline-retest-2026-06-14.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-baseline-retest-cache-2026-06-14.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-baseline-retest-cache-2026-06-14.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-baseline-retest-headers-2026-06-14.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-baseline-retest-headers-2026-06-14.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-baseline-retest-hsts-cloudflare-2026-06-14.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-baseline-retest-hsts-cloudflare-2026-06-14.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-baseline-retest-metadata-2026-06-14.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-baseline-retest-metadata-2026-06-14.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-baseline-retest-metadata-after-purge-2026-06-14.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-baseline-retest-metadata-after-purge-2026-06-14.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-baseline-retest-metadata-final-2026-06-14.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-baseline-retest-metadata-final-2026-06-14.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-baseline.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-baseline.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-js-browser-crawl-local-2026-06-23-summary.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/07-tool-outputs/zap/zap-js-browser-crawl-local-2026-06-23-summary.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/08-ai-safety-plan.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/08-ai-safety-plan.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/09-findings-and-remediation.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/09-findings-and-remediation.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/10-retest-log.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/10-retest-log.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/11-remediation-plan.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/11-remediation-plan.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/12-audit-logging-plan.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/12-audit-logging-plan.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/13-audit-logging-test-protocol.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/13-audit-logging-test-protocol.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/14-audit-detection-response-playbook.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/14-audit-detection-response-playbook.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/15-live-lifecycle-deletion-probe.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/15-live-lifecycle-deletion-probe.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-06-14/owasp-top-10-matrix.md](Compliance/OWASP/security-evidence/owasp/2026-06-14/owasp-top-10-matrix.md) | Assessment/reference record; preserve evidence |
| [docs/Compliance/OWASP/security-evidence/owasp/2026-08-03/01-security-remediation-status.md](Compliance/OWASP/security-evidence/owasp/2026-08-03/01-security-remediation-status.md) | Assessment/reference record; preserve evidence |
| [docs/at-rest-encryption-plan.md](at-rest-encryption-plan.md) | Historical design/work log |
| [docs/cookies-browser-storage.md](cookies-browser-storage.md) | Maintained reference/guidance |
| `docs/progress/2026-06-15-audit-logging-retest.md` (local/ignored) | Local historical work log |
| `docs/progress/2026-07-14-provider-secret-rollback-guard.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 01-4-26 STT Secret Handling Fix.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 03-5-26 Security Recovery.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 06-4-26 Ingestion Security Followups.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 07-4-26 Encryption Regression Fixes.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 07-4-26 Owner Content Encryption Expansion.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 07-4-26 Transcript Encryption Slice.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 07-4-26 Vault Least Privilege and Document Switching.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 07-4-26 Vault Transit Bootstrap Recovery.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 10-5-26 LLM Stale Vault Ref Removal.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 14-6-26 CSRF Cookie HttpOnly Scanner Decision.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 14-6-26 OWASP Passive Recon Gap Closure.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 14-6-26 OWASP Public Form CSRF Retest.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 14-6-26 OWASP Public Passive Recon.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 14-6-26 OWASP Remediation Planning.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 14-6-26 OWASP SBOM Scan and Evidence Formalization.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 14-6-26 OWASP Scope Evidence Pack.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 14-6-26 OWASP Starlette CVE Resolution.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 14-6-26 OWASP XSS SSRF AI Safety Coverage.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 14-6-26 Public Form CSRF Rendering Fix.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 14-6-26 Security Header Hardening Fix.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 15-6-26 OWASP Audit Detection.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 15-6-26 OWASP Audit IP and Abuse Signals.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 15-6-26 OWASP Audit Logging Implementation.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 15-6-26 OWASP Audit Logging Plan.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 15-6-26 OWASP Audit Remaining Events.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 15-6-26 OWASP Audit Test Protocol.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 15-6-26 OWASP Local Auth Crawl.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 2-7-26 Anti-Crawling Policy Hardening.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 23-6-26 OWASP Admin Crawl Seed.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 23-6-26 OWASP Live Lifecycle Probe.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 23-6-26 OWASP Onboarding MFA JS Crawl.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 23-6-26 OWASP System Admin Crawl.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 24-6-26 OWASP API Docs Auth Gate.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 24-6-26 OWASP External Admin Crawl.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 24-6-26 OWASP Private IP Disclosure Remediation.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 30-3-26 At Rest Encryption Plan.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 30-3-26 Cookie Security.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 30-3-26 XSS Probe.md` (local/ignored) | Local historical work log |
| [docs/security-xss.md](security-xss.md) | Maintained reference/guidance |
| [docs/security.md](security.md) | Maintained reference/guidance |

## Audio capture, STT and recovery

| Document | Status / review handling |
| --- | --- |
| [docs/live_stt.md](live_stt.md) | Maintained reference/guidance |
| `docs/progress/2026-05-13-vad-inactivity-prompt.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 01-4-26 Live Chunk Failure Reconcile.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 01-4-26 Live STT VAD Slice.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 01-4-26 Live VAD Stop Fix.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 01-4-26 Ricky Browser VAD Swap.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 01-4-26 STT Retry Blob Retention.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 02-4-26 STT Live Plan.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 05-4-26 Live Chunk Rate Limit.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 05-4-26 Live Upload Duration Budget.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 10-5-26 STT Replacement Token Review Fix.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 12-5-26 STT Path Config ID API Fix.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 13-4-26 Live Mic Activity Indicator.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 16-4-26 Transcription Ingestion Stale Jobs.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 30-3-26 Home STT Visibility Fix.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 30-3-30 STT Review Fixes.md` (local/ignored) | Local historical work log |
| [docs/stt-config.md](stt-config.md) | Maintained reference/guidance |
| [docs/transcript-capture.md](transcript-capture.md) | Maintained reference/guidance |
| [plans/DEEPGRAM_LIVE_STREAMING.md](../plans/DEEPGRAM_LIVE_STREAMING.md) | Plan; use its own status, not runtime authority |
| [plans/audio-ingestion-recovery.md](../plans/audio-ingestion-recovery.md) | Plan; use its own status, not runtime authority |

## Generation, Working note, dictation and clinical review

| Document | Status / review handling |
| --- | --- |
| [docs/consultation-splitting-preference.md](consultation-splitting-preference.md) | Maintained reference/guidance |
| [docs/consultation-splitting-release-evidence-2026-09-10.md](consultation-splitting-release-evidence-2026-09-10.md) | Dated release evidence; preserve results |
| [docs/emis-roadmap.md](emis-roadmap.md) | Maintained reference/guidance |
| [docs/hallucination-check-design.md](hallucination-check-design.md) | Maintained reference/guidance |
| `docs/progress/2026-05-14-llm-generation-rate-limits.md` (local/ignored) | Local historical work log |
| `docs/progress/2026-05-28-generation-waits-for-transcript.md` (local/ignored) | Local historical work log |
| `docs/progress/2026-05-31-llm-key-replacement-unblocks-failed-generations.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 03-4-26 Clinical Note Statement Editor.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 03-6-26 Structured Section Copy Body Only.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 13-4-26 Post Consultation Dictation Phase 0 Lock.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 13-4-26 Post Consultation Dictation Phase 1 Schema.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 13-4-26 Post Consultation Dictation Phase 2 Provider Resolution.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 14-4-26 Dictation API UI Generation Slice.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 14-4-26 Dictation Mic Recorder.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 14-4-26 Dictation STT Team Selection UI.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 17-5-26 Stale Note Review Fixes.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 19-5-26 Working Note Debt.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 8-5-26 Dictation Modal.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 9-5-26 Dictation Stop Auto Transcribe.md` (local/ignored) | Local historical work log |
| `docs/progress/dictation-modal-checklist.md` (local/ignored) | Local historical work log |
| [docs/working_note_implementation.md](working_note_implementation.md) | Maintained reference/guidance |
| [plans/NOTE_GENERATION_UNSAFE_FAILURES.md](../plans/NOTE_GENERATION_UNSAFE_FAILURES.md) | Plan; use its own status, not runtime authority |
| [plans/llm-resilience-and-operational-monitoring.md](../plans/llm-resilience-and-operational-monitoring.md) | Plan; use its own status, not runtime authority |
| [plans/multiple-problems-toolbar.md](../plans/multiple-problems-toolbar.md) | Plan; use its own status, not runtime authority |
| [plans/review-problems-suggestions.md](../plans/review-problems-suggestions.md) | Plan; use its own status, not runtime authority |
| [plans/split-generation-diagnostics.md](../plans/split-generation-diagnostics.md) | Plan; use its own status, not runtime authority |
| [split_note.md](../split_note.md) | Historical design/work log |

## Templates, Quick Actions and Smart Phrases

| Document | Status / review handling |
| --- | --- |
| [docs/editor-smart-phrases.md](editor-smart-phrases.md) | Maintained reference/guidance |
| `docs/progress/Daily Note 01-4-26 Vestigial Template Cleanup.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 12-4-26 Review Fixes Template Guard and Followup SSR.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 13-4-26 Home Asset Duplication.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 13-4-26 Home Asset Row UI Polish.md` (local/ignored) | Local historical work log |
| [docs/template-suggestion-preference.md](template-suggestion-preference.md) | Maintained reference/guidance |
| [docs/template_io_plan.md](template_io_plan.md) | Maintained reference/guidance |

## Workspace and frontend

| Document | Status / review handling |
| --- | --- |
| `docs/frontend-nextjs.md` (local/ignored) | Local frontend experiment; not maintained runtime |
| [docs/frontend-roadmap.md](frontend-roadmap.md) | Roadmap with current-runtime boundary |
| [docs/home_brief.md](home_brief.md) | Maintained reference/guidance |
| `docs/progress/2026-05-06-transcribe-record-button-state.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 01-4-26 Preview Alias Fixes and Remote Bind Restore.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 01-4-26 Remote Frontend Default.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 01-4-26 Transcribe Editor Return Fix.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 02-4-26 Transcribe SSE.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 08-4-26 Transcribe Frontend Module Refactor.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 08-4-26 Transcribe UI Flow Polish.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 13-4-26 Transcribe Stability Fixes.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 14-4-26 Transcribe Workspace Preferences Defaults.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 18-4-26 Home Flat Workspace Layout.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 30-3-26 Browser UI Verification.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 30-3-26 Remaining UI Cutover.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 30-3-26 Transcribe GLM Cutover.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 30-3-26 UI Switchover.md` (local/ignored) | Local historical work log |
| [docs/styling_condensation_plan.md](styling_condensation_plan.md) | Maintained reference/guidance |
| [docs/transcribe_brief.md](transcribe_brief.md) | Maintained reference/guidance |
| [docs/workspace.md](workspace.md) | Maintained reference/guidance |
| `frontend/README.md` (local/ignored) | Local frontend experiment; not maintained runtime |
| [plans/SCRIBE_ONSCREEN_GUIDE_IMPLEMENTATION_PLAN.md](../plans/SCRIBE_ONSCREEN_GUIDE_IMPLEMENTATION_PLAN.md) | Plan; use its own status, not runtime authority |

## Providers, administration, usage and API

| Document | Status / review handling |
| --- | --- |
| [docs/admin_brief.md](admin_brief.md) | Maintained reference/guidance |
| [docs/admin_workspace_function_map.md](admin_workspace_function_map.md) | Maintained reference/guidance |
| [docs/api.md](api.md) | Maintained reference/guidance |
| [docs/gemini-enterprise-setup.md](gemini-enterprise-setup.md) | Maintained reference/guidance |
| [docs/llm-providers.md](llm-providers.md) | Maintained reference/guidance |
| `docs/progress/Daily Note 03-4-26 Bedrock LLM Provider.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 03-4-26 Home LLM Preference Cleanup.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 07-4-26 MVP Completion Plan and Usage Dashboard.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 10-5-26 LLM Provider Draft Wizard.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 13-7-26 Admin Usage Table.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 17-4-26 Usage Tab Refresh.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 9-5-26 API Inspection Upgrade Completion.md` (local/ignored) | Local historical work log |
| [docs/provider-credential-combined-flow-plan.md](provider-credential-combined-flow-plan.md) | Maintained reference/guidance |
| [docs/usage_tab.md](usage_tab.md) | Maintained reference/guidance |

## Tutorials and role guidance

| Document | Status / review handling |
| --- | --- |
| [docs/tutorials/README.md](tutorials/README.md) | Maintained reference/guidance |
| [docs/tutorials/admin.md](tutorials/admin.md) | Maintained reference/guidance |
| [docs/tutorials/onboarding.md](tutorials/onboarding.md) | Maintained reference/guidance |
| [docs/tutorials/system-admin-setup.md](tutorials/system-admin-setup.md) | Maintained reference/guidance |
| [docs/tutorials/team-leader.md](tutorials/team-leader.md) | Maintained reference/guidance |
| [docs/tutorials/user.md](tutorials/user.md) | Maintained reference/guidance |

## Testing and accessibility

| Document | Status / review handling |
| --- | --- |
| [docs/accessibility/evidence-manifest-2026-10-15.md](accessibility/evidence-manifest-2026-10-15.md) | Dated accessibility record; preserve evidence |
| [docs/accessibility/evidence-summary-2026-10-16.md](accessibility/evidence-summary-2026-10-16.md) | Dated accessibility record; preserve evidence |
| [docs/accessibility/help-verification-2026-10-18.md](accessibility/help-verification-2026-10-18.md) | Dated accessibility record; preserve evidence |
| [docs/accessibility/human-testing-guide.md](accessibility/human-testing-guide.md) | Maintained reference/guidance |
| [docs/accessibility/interactive-browser-assessment-2026-10-15.md](accessibility/interactive-browser-assessment-2026-10-15.md) | Dated accessibility record; preserve evidence |
| [docs/accessibility/scribe-guide-verification-2026-10-16.md](accessibility/scribe-guide-verification-2026-10-16.md) | Dated accessibility record; preserve evidence |
| [docs/accessibility/wcag-2.2-manual-checklist-2026-10-15.md](accessibility/wcag-2.2-manual-checklist-2026-10-15.md) | Dated accessibility record; preserve evidence |
| [docs/dbtesting.md](dbtesting.md) | Maintained reference/guidance |
| `docs/progress/Daily Note 05-4-26 Test Regression Cleanup.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 14-6-26 Redaction Fail Closed Regression.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 30-6-26 Review Regression Worklist.md` (local/ignored) | Local historical work log |
| [docs/testing.md](testing.md) | Maintained reference/guidance |
| [docs/transcribe-playwright-checklist.md](transcribe-playwright-checklist.md) | Maintained reference/guidance |
| [plans/WCAG_ACCESSIBILITY_IMPLEMENTATION_PLAN.md](../plans/WCAG_ACCESSIBILITY_IMPLEMENTATION_PLAN.md) | Plan; use its own status, not runtime authority |
| [tests/accessibility/AGENTS.md](../tests/accessibility/AGENTS.md) | Scoped contributor instructions |

## Clinical safety, compliance and legal notices

| Document | Status / review handling |
| --- | --- |
| `DSPT Remediation Conversation.md` (local/ignored) | Local reference; not indexed as current |
| `DSPT_Questions.md` (local/ignored) | Local reference; not indexed as current |
| `DSPT_Test_Walkthrough.md` (local/ignored) | Local reference; not indexed as current |
| [LICENSE](../LICENSE) | Licence text; preserve terms |
| [SAFETY.md](../SAFETY.md) | Maintained reference/guidance |
| [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) | Current licence/attribution reference |
| `docs/Compliance/Hazard Log/example_hazard_log.md` (local/ignored) | Assessment/reference record; preserve evidence |
| `docs/Compliance/Hazard Log/hazard_discussion.txt` (local/ignored) | Assessment/reference record; preserve evidence |
| `docs/Compliance/Hazard Log/hazard_log_reference.md` (local/ignored) | Assessment/reference record; preserve evidence |
| `docs/Compliance/Hazard Log/openscribe_hazard_log.md` (local/ignored) | Assessment/reference record; preserve evidence |

## Engineering review records

| Document | Status / review handling |
| --- | --- |
| [docs/reviews/documentation-review-2026-10-18.md](reviews/documentation-review-2026-10-18.md) | Dated review record |
| [docs/reviews/generation-stage-cost-logging-review-2026-10-16.md](reviews/generation-stage-cost-logging-review-2026-10-16.md) | Dated review record |
| [docs/reviews/note-generation-pipeline-review-2026-10-16.md](reviews/note-generation-pipeline-review-2026-10-16.md) | Dated review record |

## Cross-cutting engineering history

| Document | Status / review handling |
| --- | --- |
| `docs/progress/2026-05-01-logged-in-ui-harmonisation.md` (local/ignored) | Local historical work log |
| `docs/progress/2026-05-02-celery-audio-payload-hardening.md` (local/ignored) | Local historical work log |
| `docs/progress/2026-05-28-consult-boundary-recording-guard.md` (local/ignored) | Local historical work log |
| `docs/progress/2026-05-28-consult-history-infinite-scroll.md` (local/ignored) | Local historical work log |
| `docs/progress/2026-07-12-managed-password-generator.md` (local/ignored) | Local historical work log |
| `docs/progress/2026/07/16 Daily Progress Note.md` (local/ignored) | Local historical work log |
| `docs/progress/2026/07/18 Daily Progress Note.md` (local/ignored) | Local historical work log |
| `docs/progress/2026/07/19 Daily Progress Note.md` (local/ignored) | Local historical work log |
| `docs/progress/2026/07/20 Daily Progress Note.md` (local/ignored) | Local historical work log |
| `docs/progress/2026/07/21 Daily Progress Note.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 01-4-26 Live 30s Flush Continuation Fix.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 01-4-26 Review Followups.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 05-4-26 Whole File Upload Budget.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 07-4-26 Guided UX and Session Mode Polish.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 07-4-26 Production DEK KEK Plan.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 08-4-26 Big File Refactor Pass.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 08-4-26 Main Refactor Completion and Plain Language Pass.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 08-4-26 Main Refactor Slice.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 11-4-26.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 12-3-26.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 12-4-26 Review Fixes Note Switching and Validation.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 12-4-26 Review Fixes Snapshot Backfill and Client Guard.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 12-4-26 Review Fixes Snapshot Conflict and Web Validation.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 12-5-26 Status Pill Review Fixes.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 13-7-26 Review Fixes.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 14-3-26.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 14-6-26 Cache-Control Triage Fix.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 14-6-26 Public Metadata Routes Fix.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 17-3-26.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 19-3-26.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 22-3-26.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 24-4-26 Agent Caveman Policy.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 25-5-26 Review Followups.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 28-4-26 Clinical NLP.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 29-3-26.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 30-3-26 Exposure Guard.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 30-3-26.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 30-3-30 Review Fixes.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 30-4-26.md` (local/ignored) | Local historical work log |
| `docs/progress/Daily Note 31-3-26.md` (local/ignored) | Local historical work log |
| `docs/progress/refactor-handoff.md` (local/ignored) | Local historical work log |

## In-app documentation sources

These files contain Help, guided-tour, Library instructions, or legal-page presentation. Review the rendered copy against the same workflow and privacy contracts as the written tutorials. Operator-published legal text lives in the legal-content service/database and is outside this file inventory. This review did not read deployment legal content.

| Area | Source |
| --- | --- |
| Workspace Help | [app/templates/workspace/_help.html](../app/templates/workspace/_help.html) |
| Scribe Guide step copy | [app/static/js/transcribe/app.js](../app/static/js/transcribe/app.js) |
| Template Help dialog | [app/templates/settings/_template_library.html](../app/templates/settings/_template_library.html) |
| Quick Action Help dialog | [app/templates/settings/_quick_action_library.html](../app/templates/settings/_quick_action_library.html) |
| Smart Phrase Help dialog | [app/templates/settings/_smart_phrase_library.html](../app/templates/settings/_smart_phrase_library.html) |
| Template bundle instructions for an AI assistant | [app/static/js/settings/template-io.js](../app/static/js/settings/template-io.js) |
| Quick Action bundle instructions for an AI assistant | [app/static/js/settings/quick-action-io.js](../app/static/js/settings/quick-action-io.js) |
| Smart Phrase bundle instructions for an AI assistant | [app/static/js/settings/smart-phrase-io.js](../app/static/js/settings/smart-phrase-io.js) |
| Published legal-page presentation | [app/templates/legal_document.html](../app/templates/legal_document.html) |

## Keeping this list current

When documentation is added, removed, or reclassified, update this inventory and the [documentation index](README.md). Keep current references in `.github/scripts/check-operational-docs.py`. Use a new dated review record for later findings; do not replace historical evidence with current claims.
