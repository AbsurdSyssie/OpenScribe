# Hallucination Check

## Status

Implemented for structured Template note generation. This document records the current design contract rather than a future schema proposal.

The checker reviews a redacted first-pass structured note against the redacted sources used for generation, applies only validated exact-string edits, and stores the final checked or unchecked result under the normal owner-content encryption boundary.

Current route/provider/generation behavior is also documented in [api.md](api.md), [llm-providers.md](llm-providers.md), [security.md](security.md), and [transcript-capture.md](transcript-capture.md).

## Scope

Applicable:

- `generator_type=template`;
- structured EMIS output;
- redacted transcript source where used;
- redacted Working note where used;
- redacted post-consultation dictation where used;
- system-admin-selected team checker LLM configuration;
- owner-visible checked/unchecked status;
- admin-visible bounded non-content status/reason metadata;
- separate provider attempt/usage accounting for checker calls;
- owner-only localhost development debug data when explicitly enabled.

Not applicable:

- freeform Template output;
- follow-up documents;
- Quick Actions;
- production exposure of first-pass note/checker edits;
- plaintext sources or original PII;
- storing raw checker responses;
- adding sections or making clinical inferences not supported by source evidence.

## Evidence standard

The checker treats supplied redacted sources as the only evidence.

- It can preserve cleaned wording only when the fact and certainty remain equivalent.
- It cannot add diagnoses, treatment, medication, investigation, advice, safety-netting, follow-up, symptoms, duration, negatives, or examination findings based on clinical plausibility.
- It can remove unsupported text or soften it only when the softer wording is itself supported.
- Transcription messiness does not justify inventing missing detail.

This is a bounded post-generation consistency check, not an independent clinical reasoning or safety guarantee. The final note remains a draft requiring clinician review.

## Privacy boundary

Checker input:

- redacted transcript text;
- redacted Working-note content;
- redacted dictation content;
- redacted first-pass structured note;
- only the sources that participated in first-pass generation.

The checker does not receive plaintext owner content or original PII values. Reidentification, where applicable, occurs only after the checker stage.

Allowed metadata:

- team/config/attempt/document IDs;
- adapter/provider/model labels;
- status/reason code;
- duration/token counts;
- applied edit count;
- timestamps.

Forbidden in logs, audit, usage, attempt/error metadata:

- transcript/Working-note/dictation/note text;
- prompt/checker response body;
- original PII values;
- credentials, tokens, or Vault references.

## Configuration

System administrators can configure/clear a team hallucination-check selection using an existing ready LLM config and optional allowed model override.

- Team leaders and normal users cannot manage the checker selection.
- The selected config must remain valid for the team/policy.
- No raw credential is stored in the selection.
- The checker resolves the selected config's Vault/deployment identity through the ordinary LLM runtime.
- Missing/invalid/unavailable checker configuration produces an unchecked note rather than silently falling back to a different provider.

## Generated-document metadata

Generated documents store bounded checker metadata such as:

- internal checker status;
- selected config/model/provider snapshot metadata;
- completion time;
- applied edit count;
- optional encrypted development debug payload under the explicit local-debug gate.

Internal outcomes distinguish not-applicable, not-configured/invalid/skipped/failure, checked-unchanged, and checked-corrected states. User-facing output reduces these to:

- `checked`;
- `unchecked`;
- `not_applicable`.

Admins can see safe reason metadata but not first-pass/final content.

## Response and patch contract

The checker returns JSON only:

```json
{"status":"unchanged"}
```

or:

```json
{
  "status": "corrected",
  "edits": [
    {
      "section_key": "problem",
      "original": "exact existing substring",
      "replacement": "supported replacement or empty string"
    }
  ]
}
```

Validation rules:

- status is exactly `unchanged` or `corrected`;
- corrected requires at least one edit;
- section key must be an existing output section or the supported title target;
- no new sections;
- original is non-empty and occurs exactly once in the target;
- matching is exact, not regex/fuzzy/global replace;
- replacement can be empty for section content but cannot make the title empty;
- repeated text requires a longer unique substring;
- edits apply deterministically in note order;
- empty sections are omitted after patching;
- section order remains unchanged;
- output is revalidated through the structured-note contract.

Invalid response/schema/patch application gets one bounded retry. Failure after retry preserves the valid first-pass note as unchecked rather than blocking note creation.

## Provider and quota lifecycle

Checker work uses the existing LLM provider runtime and accounting boundary:

1. first-pass generation produces a valid redacted structured note;
2. checker selection/config/model metadata is resolved/snapshotted;
3. quota/provider attempt is claimed/submitted through normal lifecycle controls;
4. checker receives only redacted evidence and first-pass note;
5. response is validated and edits are applied exactly;
6. final result is reidentified as applicable, validated, encrypted, and persisted;
7. checker status/usage safe metadata is settled;
8. raw response and first-pass content are discarded unless explicit local debug capture is enabled.

Provider failure, malformed output, or patch failure yields an unchecked note with safe status metadata.

## Development debug visibility

`HALLUCINATION_CHECK_DEBUG_UI=1` enables an explicit development-only owner debug path.

Guardrails:

- disabled by default;
- localhost seeded-development owner authorization;
- not available to team leaders/system administrators merely by role;
- encrypted at rest under the generated-document owner;
- deleted with the generated document/transcript root;
- excluded from logs, audit, usage, provider metadata, and normal production responses;
- contains no credentials or original PII values.

The owner-only debug route is a testing aid, not a support/production observability mechanism.

## Testing requirements

Cover:

- team checker selection authorization/validity;
- applicable versus not-applicable generator/mode cases;
- missing/invalid checker produces unchecked output;
- redacted-source-only prompt construction;
- exact patch validation and one retry;
- unsupported/new sections and repeated/nonexistent substrings rejected;
- fail-open-to-valid-unchecked note behavior;
- usage/quota/provider-attempt metadata;
- no raw checker/source content in persistence/log/audit/error fields;
- owner-only debug gate and production disablement;
- final structured JSON validation and encrypted persistence.

## Remaining roadmap

Do not expand the checker to freeform notes, follow-ups, Quick Actions, fuzzy rewriting, plaintext source access, or production first-pass visibility without a focused privacy/clinical-safety design and corresponding migrations/services/tests/documentation.
