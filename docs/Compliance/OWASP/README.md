# OWASP compliance plan

This folder tracks OpenScribe's alignment work against the current OWASP Top 10 web application security risks and the OWASP Web Security Testing Guide (WSTG).

The current OWASP Top 10 web application list remains **OWASP Top 10:2021**:

| ID | Category |
| --- | --- |
| A01:2021 | Broken Access Control |
| A02:2021 | Cryptographic Failures |
| A03:2021 | Injection |
| A04:2021 | Insecure Design |
| A05:2021 | Security Misconfiguration |
| A06:2021 | Vulnerable and Outdated Components |
| A07:2021 | Identification and Authentication Failures |
| A08:2021 | Software and Data Integrity Failures |
| A09:2021 | Security Logging and Monitoring Failures |
| A10:2021 | Server-Side Request Forgery |

For OpenScribe, the evidence pack should also track **AI and clinical-scribe safety testing** because the product handles clinical transcript-derived content, redaction, generated documents, and configurable STT, LLM, and NLP providers. This is not an OWASP Top 10 category, but it is relevant to DTAC, medical-device governance, and safe clinical deployment.

## Documents in this folder

| Document | Purpose |
| --- | --- |
| [`00-scope-and-evidence-pack.md`](00-scope-and-evidence-pack.md) | Defines the authorised testing scope, evidence structure, current repo-backed evidence, and known gaps. |
| [`01-information-gathering.md`](01-information-gathering.md) | First operational workstream: OWASP WSTG information gathering, tool usage, and evidence outputs. |
| [`OWASP_Context.md`](OWASP_Context.md) | Carry-forward context for future agents: folder structure, evidence rules, naming conventions, and current phase status. |
| [`security-evidence/owasp/2026-06-14/`](security-evidence/owasp/2026-06-14/) | Initial dated OWASP evidence pack seeded from repo evidence. |

## Evidence status labels

| Status | Meaning |
| --- | --- |
| `Repo-evidenced` | Supported by checked-in code or documentation, but not independently tested in the current OWASP cycle. |
| `Test-evidenced` | Demonstrated by an executed test, scan, script, proxy trace, screenshot, or report in the OWASP evidence pack. |
| `Partially evidenced` | Some supporting evidence exists, but coverage is incomplete. |
| `Gap` | Required evidence or control is not yet present. |
| `Not in scope` | Explicitly excluded from the authorised test scope. |

## Evidence handling rules

- Do not commit credentials, secrets, authentication material, patient-identifiable content, live transcript text, audio, or generated clinical notes.
- Redact sensitive values before committing screenshots, proxy logs, scanner exports, command output, or server logs.
- Use synthetic accounts and synthetic consultation content for all evidence wherever possible.
- Third-party provider infrastructure is out of scope unless explicitly authorised by the provider.

## First milestone

The first milestone is **information gathering and evidence-pack setup**:

1. Confirm authorised scope.
2. Build endpoint and route inventory.
3. Build role/access matrix.
4. Map architecture and trust boundaries.
5. Capture passive recon and server fingerprinting outputs.
6. Seed the OWASP Top 10 test matrix from repo evidence.
7. Create remediation tickets for missing evidence.

The current repo already contains substantial security design and test documentation. This folder turns that into a repeatable OWASP evidence process rather than treating it as ad hoc implementation notes.
