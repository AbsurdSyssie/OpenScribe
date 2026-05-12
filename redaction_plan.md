````markdown
# Agent plan: fix LLM request redaction boundary + displayed request payload

## Objective

Fix the LLM generation/request logging path so that:

1. Static template instructions are **not PHI-redacted**.
2. Dynamic clinician/user/patient-originated prompt inputs **are PHI-redacted before provider send**.
3. The UI/debug field labelled **“LLM request”** shows only the actual API request body sent to the LLM provider, not the internal wrapper/envelope.

Primary file to inspect: `app/services/templates.py`, which is the generation seam containing the prompt input fields, `redact_transient_text(...)`, and provider request construction. :contentReference[oaicite:0]{index=0}

---

# Phase 1 — Build feedback loop

Create deterministic tests at the request-construction seam. Do not call a real LLM or real de-identification provider.

Mock/stub:

- the LLM client call, capturing the exact provider request body
- the transient redactor, returning predictable `[PHI-n]` placeholders
- the transcript redaction run, where needed

The feedback loop must assert both:

- what is sent to the provider
- what is displayed/logged as “LLM request”

The displayed/logged “LLM request” must equal the captured provider request body.

---

# Phase 2 — Reproduce

Use a fixture like this:

```text
template_prompt_text:
"You are a GP who works in the NHS."

transcript_text:
"John Smith attended with low mood."

dictation_text:
"John Smith dictated additional context."

follow_up_prompt_text:
"Please include John Smith's follow-up concern."

quick_action_context_text:
"John Smith asked for a brief summary."

structured_context:
{
  "history": ["John Smith has poor sleep."],
  "social_history": ["John Smith lives alone."]
}
````

Stub redaction so dynamic strings become placeholders, for example:

```text
John Smith attended with low mood.
=> [PHI-1] attended with low mood.

John Smith dictated additional context.
=> [PHI-2] dictated additional context.

Please include John Smith's follow-up concern.
=> Please include [PHI-3]'s follow-up concern.

John Smith asked for a brief summary.
=> [PHI-4] asked for a brief summary.

John Smith has poor sleep.
=> [PHI-5] has poor sleep.

John Smith lives alone.
=> [PHI-6] lives alone.
```

Expected failing symptoms before the fix:

```text
template_prompt_text is redacted into:
"You are a [PHI-n] who works in the [PHI-n]."
```

and/or the displayed “LLM request” contains wrapper fields:

```json
{
  "generation": {...},
  "input": {...},
  "provider": {...},
  "request": {...}
}
```

---

# Phase 3 — Ranked hypotheses

Test these in order.

## Hypothesis 1 — template prompt is being passed through transient redaction

Prediction: if `template_prompt_text` is excluded from `redact_transient_text(...)`, `[PHI-n]` placeholders disappear from the template while transcript/dictation/follow-up/quick-action/context placeholders remain.

## Hypothesis 2 — a combined prompt-input structure is redacted wholesale

Prediction: if redaction happens per dynamic field before prompt collation, static template text remains raw and all dynamic fields are still redacted.

## Hypothesis 3 — displayed “LLM request” is an internal debug envelope

Prediction: if the displayed value is changed to the final provider request dict, wrapper keys disappear and the displayed object equals the captured provider request body.

## Hypothesis 4 — provider call and displayed request are built from different objects

Prediction: capturing LLM client kwargs will show a smaller body than the displayed “LLM request”. The fix is to build one `provider_request` object and use it both for send and display.

---

# Phase 4 — Instrument only if needed

If the tests do not reveal the flow, add temporary tagged logs:

```python
logger.info(
    "[DEBUG-llm-redaction-boundary] template_before=%r template_after=%r",
    raw_template_prompt_text,
    template_prompt_text,
)

logger.info(
    "[DEBUG-llm-redaction-boundary] provider_request_keys=%s displayed_request_keys=%s",
    sorted(provider_request.keys()),
    sorted(displayed_llm_request.keys()),
)
```

Before finalizing:

```bash
grep -R "\[DEBUG-llm-redaction-boundary\]" .
```

There must be no matches.

---

# Phase 5 — Fix redaction boundary

## Rule A — never redact static prompt assets

Do **not** redact:

```text
template_prompt_text
template name
section labels
EMIS section definitions
system prompt
built-in instruction text
```

The template prompt must be used as configured:

```python
raw_template_prompt_text = template_version.prompt_text
template_prompt_text = raw_template_prompt_text
```

Do **not** do this:

```python
template_prompt_text = redact_transient_text(
    db,
    template_version.prompt_text,
    team_id=actor.team_id,
    start_index=...
)["redacted_text"]
```

## Rule B — always redact dynamic prompt inputs before provider send

These must go through PHI redaction before they are included in `messages`:

```text
dictation_text
follow_up_prompt_text
quick_action_context_text
structured_context
```

Transcript text must continue to use the persisted redaction run / redacted transcript path.

## Suggested helper

Add a small helper to make the redaction boundary explicit:

```python
def _redact_dynamic_prompt_text(
    db: Session,
    text: str | None,
    *,
    team_id: UUID,
    start_index: int,
) -> tuple[str | None, list[dict]]:
    if text is None:
        return None, []

    if not text.strip():
        return text, []

    result = redact_transient_text(
        db,
        text,
        team_id=team_id,
        start_index=start_index,
    )
    return result["redacted_text"], list(result["phi_index"])
```

Then use it only for dynamic text inputs.

---

# Phase 6 — Fix structured_context redaction

`structured_context` may be a dict/list tree. Redact every string value recursively while preserving the original structure.

Use a helper like:

```python
def _redact_dynamic_prompt_value(
    db: Session,
    value,
    *,
    team_id: UUID,
    start_index: int,
) -> tuple[object, list[dict]]:
    if isinstance(value, str):
        return _redact_dynamic_prompt_text(
            db,
            value,
            team_id=team_id,
            start_index=start_index,
        )

    if isinstance(value, list):
        redacted_items = []
        phi_index: list[dict] = []
        next_index = start_index

        for item in value:
            redacted_item, item_phi_index = _redact_dynamic_prompt_value(
                db,
                item,
                team_id=team_id,
                start_index=next_index,
            )
            redacted_items.append(redacted_item)
            phi_index.extend(item_phi_index)
            next_index += len(item_phi_index)

        return redacted_items, phi_index

    if isinstance(value, dict):
        redacted_dict = {}
        phi_index: list[dict] = []
        next_index = start_index

        for key, item in value.items():
            redacted_item, item_phi_index = _redact_dynamic_prompt_value(
                db,
                item,
                team_id=team_id,
                start_index=next_index,
            )
            redacted_dict[key] = redacted_item
            phi_index.extend(item_phi_index)
            next_index += len(item_phi_index)

        return redacted_dict, phi_index

    return value, []
```

Important: preserve keys and non-string values exactly.

---

# Phase 7 — Manage placeholder numbering

Build a single dynamic PHI index in the same order the dynamic fields are processed.

Suggested order:

```text
1. transcript redaction run
2. dictation_text
3. follow_up_prompt_text
4. quick_action_context_text
5. structured_context string values
```

Use the next available placeholder index after the transcript redaction run:

```python
next_index = next_placeholder_index(redaction_run)
extra_phi_index: list[dict] = []
```

After each dynamic redaction:

```python
redacted_value, phi = _redact_dynamic_prompt_text(...)
extra_phi_index.extend(phi)
next_index += len(phi)
```

For final mapping:

```python
phi_index = combined_phi_index(
    db,
    redaction_run,
    extra_phi_index=extra_phi_index,
)
```

Do not add any template-derived entries to `extra_phi_index`.

---

# Phase 8 — Fix “LLM request” display/logging

Build one actual provider request dict:

```python
provider_request = {
    "model": resolved_model_name,
    "messages": messages,
    "temperature": 0.2,
    "max_completion_tokens": max_completion_tokens,
    "user": str(actor.id),
}
```

Use this exact object for:

1. the provider API call
2. the UI/debug field labelled `LLM request`

Do not display this as “LLM request”:

```python
debug_context = {
    "generation": {...},
    "input": {...},
    "provider": {...},
    "request": provider_request,
}
```

If the wider envelope is still needed internally, rename it clearly:

```text
LLM debug context
```

or

```text
Generation debug context
```

The displayed **LLM request** must not contain these top-level keys:

```text
generation
input
provider
request
```

It should contain only the provider API request shape, for example:

```json
{
  "model": "deepseek.v3.2",
  "messages": [
    {
      "role": "system",
      "content": "..."
    },
    {
      "role": "user",
      "content": "..."
    }
  ],
  "temperature": 0.2,
  "max_completion_tokens": 1600,
  "user": "..."
}
```

---

# Phase 9 — Regression tests

## Test 1 — template prompt is not redacted

```python
provider_request_json = json.dumps(captured_provider_request)

assert "You are a GP who works in the NHS." in provider_request_json
assert "You are a [PHI-" not in provider_request_json
```

## Test 2 — transcript remains redacted

```python
assert "[PHI-1] attended with low mood." in provider_request_json
assert "John Smith attended with low mood." not in provider_request_json
```

## Test 3 — dictation_text is redacted

```python
assert "John Smith dictated additional context." not in provider_request_json
assert "[PHI-2] dictated additional context." in provider_request_json
```

## Test 4 — follow_up_prompt_text is redacted

```python
assert "John Smith's follow-up concern" not in provider_request_json
assert "[PHI-3]" in provider_request_json
```

## Test 5 — quick_action_context_text is redacted

```python
assert "John Smith asked for a brief summary." not in provider_request_json
assert "[PHI-4] asked for a brief summary." in provider_request_json
```

## Test 6 — structured_context string values are redacted

```python
assert "John Smith has poor sleep." not in provider_request_json
assert "John Smith lives alone." not in provider_request_json

assert "[PHI-5] has poor sleep." in provider_request_json
assert "[PHI-6] lives alone." in provider_request_json
```

Also assert structure is preserved before collation if the code keeps a structured object before rendering:

```python
assert redacted_structured_context.keys() == structured_context.keys()
assert isinstance(redacted_structured_context["history"], list)
assert isinstance(redacted_structured_context["social_history"], list)
```

## Test 7 — PHI index excludes static template text

```python
values = {item["value"] for item in phi_index}

assert "John Smith" in values
assert "GP" not in values
assert "NHS" not in values
```

## Test 8 — displayed LLM request is provider request only

```python
assert displayed_llm_request == captured_provider_request

assert "model" in displayed_llm_request
assert "messages" in displayed_llm_request
assert "temperature" in displayed_llm_request
assert "max_completion_tokens" in displayed_llm_request

assert "generation" not in displayed_llm_request
assert "input" not in displayed_llm_request
assert "provider" not in displayed_llm_request
assert "request" not in displayed_llm_request
```

---

# Phase 10 — Re-run original scenario

Use the originally observed example and verify:

```text
template_prompt_text:
"You are a GP who works in the NHS."
```

or equivalent real template wording remains readable and unredacted.

The transcript still contains placeholders:

```text
[PHI-1], [PHI-2], ...
```

Dynamic non-template inputs still contain placeholders if present:

```text
dictation_text
follow_up_prompt_text
quick_action_context_text
structured_context
```

---

# Phase 11 — Cleanup

Before declaring done:

```text
[ ] Original repro no longer reproduces.
[ ] New regression tests fail before fix and pass after fix.
[ ] No [DEBUG-...] logs remain.
[ ] No throwaway harness files remain.
[ ] “LLM request” equals actual provider request body.
[ ] No static template text enters the PHI index.
[ ] All dynamic user/clinician/patient fields are redacted before provider send.
```

Run focused tests first:

```bash
pytest -q tests/test_api.py -k "llm or generated or template"
pytest -q tests/test_pii_response_minimisation.py
```

Then run the broader relevant suite:

```bash
pytest -q tests/test_api.py tests/test_admin_ui.py tests/test_pii_response_minimisation.py
```

Prefer full suite before merge:

```bash
pytest -q
```

---

# Final acceptance criteria

The patch is acceptable only if:

```text
[ ] template_prompt_text is sent exactly as configured.
[ ] template_prompt_text does not generate PHI placeholders.
[ ] transcript_text is redacted before provider send.
[ ] dictation_text is redacted before provider send.
[ ] follow_up_prompt_text is redacted before provider send.
[ ] quick_action_context_text is redacted before provider send.
[ ] structured_context string values are redacted before provider send.
[ ] structured_context shape is preserved.
[ ] PHI mappings include all dynamic placeholders needed for reidentification.
[ ] PHI mappings exclude static template/system/section text.
[ ] displayed “LLM request” contains only the actual provider request body.
[ ] displayed “LLM request” equals the captured provider API request.
```

```
```
