# 08 - AI Safety Plan

Date: 2026-06-14
OWASP mapping: A04 (AI Safety), A03 (Injection), A05 (Security Misconfiguration)
Status: documented threat model and acceptance criteria.

## Scope

OpenScribe uses AI/ML models for:
1. **Speech-to-text** (STT) — transcribes audio to text
2. **LLM generation** — generates clinical notes, follow-ups, and quick actions from transcript text
3. **De-identification / PII detection** — detects and redacts PII in transcript text
4. **Clinical NLP** — detects clinical entities in transcript text
5. **Hallucination checking** — verifies LLM-generated structured notes against source transcript

## Threat Cases

### T-001: Prompt Injection

**Risk**: Attacker injects instructions into transcript text that override LLM system prompts, causing unexpected output, data exfiltration, or role confusion.

**Surface**:
- Transcript text flows into LLM prompts unmodified (after optional de-identification)
- Template prompt text (`prompt_text`), quick action prompt text, and follow-up prompt text are used as system/user messages
- Provider config labels and model names are rendered in admin HTML but not in prompts

**Current mitigations**:
- Transcript text is redacted before being sent to LLM if de-identification is active
- LLM system prompts are hardcoded in template/quick-action definitions, not user-supplied
- LLM calls are stateless — no conversation history or tool-use loop
- Output is rendered through Jinja2 auto-escaping (no XSS from LLM output)
- CSP blocks inline scripts even if LLM output contains HTML

**Gaps**:
- No explicit prompt-injection detection or sanitization
- Transcript text could contain "ignore previous instructions" patterns that affect LLM behavior
- No content filter on LLM output (e.g., checking for PII leakage, prompt leakage, or malicious content)

**Acceptance**: Low risk for MVP. Transcript is user-owned content; LLM operates on user's own data. No multi-user chat or untrusted-party prompt injection surface. System prompts are team-admin-defined, not user-supplied. Add content-filter milestone for production hardening.

### T-002: Hallucination / Fabrication

**Risk**: LLM generates plausible but clinically incorrect information, fabricates findings not in the transcript, or misrepresents source data.

**Surface**:
- All LLM generation endpoints (structured notes, freeform notes, follow-ups, quick actions)
- Transcript text is the source of truth; LLM output should be derivable from it

**Current mitigations**:
- Hallucination checker service (`hallucination_check`) compares structured note sections against source transcript
- Hallucination checker uses separate LLM config (not the generation LLM)
- Checker prompts receive redacted sources only, with explicit instructions to verify factual accuracy
- Generated documents retain `source_transcript_version_id` for traceability

**Gaps**:
- Hallucination checker is optional and must be configured by team admin
- Freeform notes and follow-ups are not checked for hallucination
- No confidence scores or uncertainty indicators in output
- No human review workflow enforced before clinical use

**Acceptance**: Medium risk. Hallucination checker exists but is optional. Freeform/follow-up outputs are unchecked. Add mandatory checker option and freeform checker support in future milestone. Document that clinicians must review all AI-generated content before clinical use.

### T-003: Data Leakage via LLM Provider

**Risk**: Transcript-derived content (potentially containing PII/PHI) is sent to external LLM providers.

**Surface**:
- All LLM generation calls send transcript text to configured provider
- Provider base_url can point to any HTTPS endpoint (admin-configured)
- No content inspection before sending to provider

**Current mitigations**:
- De-identification can be enabled per team; redacted text is sent to LLM instead of raw text
- De-identification provider is team-admin-selected
- Clinical NLP routes allow unredacted text only to local providers (private IP/localhost)
- Provider credentials stored in Vault, not in DB
- `base_url` validation requires HTTPS for remote endpoints
- Only system admins can configure providers

**Gaps**:
- De-identification is optional — teams can send raw transcript text to LLM
- No data loss prevention (DLP) or content inspection before provider egress
- No provider audit log of what content was sent

**Acceptance**: Accepted design. Team admins choose de-identification policy. System admins choose approved providers. Document that teams handling PHI must enable de-identification. Future: add provider egress content audit log (see OWASP-005).

### T-004: Redaction Failure / Bypass

**Risk**: De-identification fails silently, sending identifiable data to external providers.

**Surface**:
- Redaction pipeline (redaction.py) uses de-identification provider to detect PII entities
- If provider fails or returns empty results, raw text may be sent to LLM

**Current mitigations**:
- Redaction has a "fail closed" regression test (`test_redaction_fail_closed`)
- Redaction service checks provider response validity
- Clinical NLP routes validate provider is local before sending unredacted text
- Presidio native adapter is built-in fallback if no remote provider is configured

**Gaps**:
- Remote de-identification provider downtime = redaction unavailable
- No synthetic test probe before each redaction call
- No redaction effectiveness scoring or sampling

**Acceptance**: Medium risk. Fail-closed behavior is tested. Add provider health check before redaction calls and redaction effectiveness sampling in future milestone.

### T-005: Malicious Model / Provider Compromise

**Risk**: A compromised or malicious provider returns crafted responses that exploit downstream consumers (XSS in LLM output, DoS via oversized responses, metadata exfiltration).

**Surface**:
- LLM output text is rendered in browser
- STT transcription results are displayed in browser
- De-identification entity results are rendered in PII panel

**Current mitigations**:
- Jinja2 auto-escaping on all server-rendered template output
- Client-side `escapeHtml()` on all dynamic `innerHTML` assignments (verified: 28 XSS tests pass)
- CSP `script-src-attr 'none'` blocks inline event handlers even if HTML injection succeeds
- Max response sizes/timeouts on all provider HTTP calls

**Gaps**:
- No content-type validation on provider responses
- No response size limit on LLM output (handled by timeout only)
- No schema validation on STT transcription results

**Acceptance**: Low risk. Defense-in-depth via CSP + escaping covers XSS. Add response size limits and content-type checks in future hardening.

### T-006: Model Denial of Service

**Risk**: Attacker crafts inputs that cause excessive token usage, timeouts, or cost spikes.

**Surface**:
- Long transcripts produce large prompts
- Repeated generation requests consume LLM quota

**Current mitigations**:
- Rate limiting via slowapi on generation endpoints
- LLM generation timeout configuration per provider
- One active generation at a time (generation locks)

**Gaps**:
- No token budget enforcement
- No generation quota per user/team
- No cost tracking or alerting

**Acceptance**: Low risk for MVP. Rate limiting and timeouts provide basic protection. Add token budget and cost tracking in future milestone.

## Structured Note JSON Contract

LLM is expected to return valid JSON for structured notes:

```json
{
  "problem": "text...",
  "history": "text...",
  "examination": "text...",
  "comment": "text...",
  "tasks": "text...",
  "investigations": "text..."
}
```

**Mitigations**:
- JSON output is parsed and validated server-side
- Invalid JSON returns error, not raw LLM output
- Empty or malformed sections are omitted
- Only allowed EMIS section keys are accepted
- `jsonschema` validation library is used for output validation per AGENTS.md contract

## AI Safety Test Coverage

| Test category | Coverage | Status |
| --- | --- | --- |
| XSS from LLM output | 28 tests (test_xss_coverage.py) | Passed |
| Redaction fail-closed | test_redaction_fail_closed | Passed |
| CSP blocking inline scripts | script-src-attr 'none' verified | Passed |
| escapeHtml on all innerHTML | Verified, 1 bug fixed (structured.js) | Passed |
| Jinja2 auto-escaping | Verified, zero |safe| usages | Passed |
| Prompt injection | Not tested | Accepted risk |
| Hallucination detection | Hallucination checker service tested separately | Not in OWASP scope |
| Model DoS | Rate limiting tested separately | Not in OWASP scope |

## Acceptance Criteria

- [x] Threat model documented for all AI/ML surfaces
- [x] Current mitigations mapped to each threat
- [x] Gaps identified with severity and remediation milestones
- [x] Structured note JSON contract validated
- [x] No transcript/note content committed to OWASP evidence

## Redaction Note

No cookies, tokens, account data, transcript/note content, prompts, provider responses, or audio committed.
