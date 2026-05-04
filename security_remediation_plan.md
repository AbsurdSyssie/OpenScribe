## Next slice: PII response minimisation

### Goal

Reduce the default amount of decrypted transcript/PII content returned to the browser. This does **not** replace owner checks. It reduces blast radius if a legitimate authenticated session is abused through XSS, compromised device, or account takeover.

Current issue:

* `TranscriptDetail` uses response fields like `current_draft_text_encrypted`, but the response is populated with decrypted/plaintext transcript draft text. 
* Workspace response includes active transcript, generated documents, active PII entities, redaction status, clinical NLP status, and other sensitive data in one large payload. 
* PII entity response objects currently include original `value`. 

---

# Agent brief

## 1. Rename plaintext response fields

### File

```text
app/schemas/transcripts.py
```

Current field:

```python
class TranscriptDetail(TranscriptListItem):
    current_draft_text_encrypted: str | None = None
```

Replace with:

```python
class TranscriptDetail(TranscriptListItem):
    current_draft_text: str | None = None
```

Do not rename encrypted DB/model fields in this slice. Only fix API response naming.

### File

```text
app/web/transcribe_workspace.py
```

Change response construction:

```python
payload["current_draft_text_encrypted"] = transcript_draft_text_service(db, transcript=transcript)
```

to:

```python
payload["current_draft_text"] = transcript_draft_text_service(db, transcript=transcript)
```

### Transitional option

If frontend breakage risk is high, support both names for one release:

```python
payload["current_draft_text"] = draft_text
payload["current_draft_text_encrypted"] = draft_text  # TODO: remove after frontend migration
```

Preferred security-clean version: remove misleading `_encrypted` response fields now and update the frontend at the same time.

---

## 2. Split PII entity schemas into summary vs detail

### File

```text
app/schemas/transcripts.py
```

Replace current default schema:

```python
class TranscriptPiiEntityDetail(BaseModel):
    id: UUID | None = None
    entity_type: str
    value: str
    placeholder: str
    occurrence_count: int
    source: str = "detected"
```

with two schemas:

```python
class TranscriptPiiEntitySummary(BaseModel):
    id: UUID | None = None
    entity_type: str
    placeholder: str
    occurrence_count: int
    source: str = "detected"
    has_value: bool = True


class TranscriptPiiEntityDetail(TranscriptPiiEntitySummary):
    value: str
```

Then update workspace schemas so default workspace payload uses summaries.

Search for:

```text
active_transcript_pii_entities
TranscriptPiiEntityDetail
```

The workspace response model should become:

```python
active_transcript_pii_entities: list[TranscriptPiiEntitySummary]
```

---

## 3. Default workspace should not include original PII values

### File

```text
app/web/transcribe_workspace.py
```

Current function:

```python
def transcript_pii_entities_response(db: Session, transcript: Transcript | None) -> list[TranscriptPiiEntityDetail]:
```

Change to:

```python
def transcript_pii_entities_response(
    db: Session,
    transcript: Transcript | None,
    *,
    include_values: bool = False,
) -> list[TranscriptPiiEntitySummary | TranscriptPiiEntityDetail]:
```

For each detected/manual/clinical entity:

```python
if include_values:
    return TranscriptPiiEntityDetail(
        id=...,
        entity_type=...,
        value=...,
        placeholder=...,
        occurrence_count=...,
        source=...,
        has_value=True,
    )

return TranscriptPiiEntitySummary(
    id=...,
    entity_type=...,
    placeholder=...,
    occurrence_count=...,
    source=...,
    has_value=True,
)
```

Then ensure `resolve_transcribe_workspace(...)` calls:

```python
"active_transcript_pii_entities": transcript_pii_entities_response(
    db,
    active_transcript,
    include_values=False,
),
```

---

## 4. Add explicit PII reveal endpoint

Use a separate endpoint for original values.

Prefer **POST** rather than GET so the request gets CSRF/origin protection and is less likely to be prefetched/cached accidentally.

### File

```text
app/routes/api_routes.py
```

Add:

```python
@api.post(
    "/transcripts/{transcript_id}/pii-entities/reveal",
    response_model=list[TranscriptPiiEntityDetail],
    responses=error_responses,
)
def reveal_transcript_pii_entities(
    transcript_id: UUID,
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    transcript = db.get(Transcript, transcript_id)
    if transcript is None or transcript.owner_user_id != context.user.id:
        raise AppError(
            404,
            "not_found",
            "Transcript not found",
            {"resource": "transcript", "transcript_id": str(transcript_id)},
        )

    response = transcript_pii_entities_response(
        db,
        transcript,
        include_values=True,
    )
    return response
```

If the route returns a raw `Response` / `JSONResponse`, add `Cache-Control: no-store`. If it returns via FastAPI model, add no-store middleware as described below.

Optional stronger control:

```python
if context.session.last_mfa_verified_at is older than 10 minutes:
    raise AppError(403, "fresh_mfa_required", "Re-authenticate with MFA to reveal original PII values")
```

Only add this if the session model now tracks recent MFA. If not, leave fresh-MFA for a later slice.

---

## 5. Add no-store headers for sensitive API responses

### File

```text
app/main.py
```

Add helper:

```python
SENSITIVE_NO_STORE_PATH_PREFIXES = (
    "/api/v1/transcribe",
    "/api/v1/transcripts",
    "/api/v1/generated-documents",
    "/api/v1/post-consultation-dictation",
)
```

Add to existing security headers middleware, or create a new middleware:

```python
@app.middleware("http")
async def add_no_store_for_sensitive_api(request: Request, call_next):
    response = await call_next(request)

    if request.url.path.startswith(SENSITIVE_NO_STORE_PATH_PREFIXES):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"

    return response
```

If middleware order matters with existing middleware, combine it with the security headers middleware added in the cookie/CSRF slice.

---

## 6. Frontend changes

Search frontend code for:

```text
current_draft_text_encrypted
edited_output_text_encrypted
active_transcript_pii_entities
value
```

### Expected changes

Default UI should show PII entity rows without original values:

```text
Type | Placeholder | Count | Source | Reveal
```

Example row:

```text
NHS_NUMBER | [NHS_NUMBER_1] | 1 | detected | Reveal
```

When user clicks **Reveal**, call:

```javascript
await csrfFetch(`/api/v1/transcripts/${transcriptId}/pii-entities/reveal`, {
  method: "POST",
  credentials: "include",
  headers: { "Content-Type": "application/json" },
});
```

Then render original values into the PII panel only.

Do not store revealed values in `localStorage`, `sessionStorage`, data attributes, or long-lived global state. Keep them in the current in-memory panel state.

---

## 7. Generated document plaintext naming

If generated-document responses also use misleading `_encrypted` names, apply the same approach.

Search:

```text
edited_output_text_encrypted
original_output_text_encrypted
```

If these are API response fields but contain plaintext, rename them to:

```text
edited_output_text
original_output_text
```

Use a short compatibility window only if needed.

---

# Tests

Add or update:

```text
tests/test_pii_response_minimisation.py
```

## 1. Workspace excludes original PII values

```python
def test_workspace_pii_entities_do_not_include_values_by_default(client, user, transcript_with_pii):
    response = client.get(
        "/api/v1/transcribe/workspace",
        cookies=auth_cookies_for(user),
    )

    assert response.status_code == 200
    body = response.json()

    entities = body["active_transcript_pii_entities"]
    assert entities
    assert "value" not in entities[0]
    assert entities[0]["placeholder"]
    assert entities[0]["entity_type"]
```

## 2. Reveal endpoint returns values for owner

```python
def test_reveal_pii_entities_returns_values_for_owner(client, user, transcript_with_pii):
    response = client.post(
        f"/api/v1/transcripts/{transcript_with_pii.id}/pii-entities/reveal",
        cookies=auth_cookies_for(user),
        headers=csrf_headers_for(user),
    )

    assert response.status_code == 200
    body = response.json()

    assert body
    assert body[0]["value"]
    assert body[0]["placeholder"]
```

## 3. Reveal endpoint rejects non-owner

```python
def test_reveal_pii_entities_rejects_non_owner(client, other_user, transcript_with_pii):
    response = client.post(
        f"/api/v1/transcripts/{transcript_with_pii.id}/pii-entities/reveal",
        cookies=auth_cookies_for(other_user),
        headers=csrf_headers_for(other_user),
    )

    assert response.status_code == 404
```

Use `404` rather than `403` to avoid confirming transcript existence.

## 4. Reveal endpoint requires CSRF

```python
def test_reveal_pii_entities_requires_csrf(authenticated_client, transcript_with_pii):
    response = authenticated_client.post(
        f"/api/v1/transcripts/{transcript_with_pii.id}/pii-entities/reveal",
        headers={"Origin": "http://testserver"},
    )

    assert response.status_code == 403
```

## 5. Sensitive API responses are no-store

```python
def test_transcript_api_responses_are_no_store(client, user, transcript):
    response = client.get(
        f"/api/v1/transcripts/{transcript.id}",
        cookies=auth_cookies_for(user),
    )

    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"
```

## 6. Response field names are no longer misleading

```python
def test_transcript_detail_uses_plaintext_response_name(client, user, transcript):
    response = client.get(
        f"/api/v1/transcripts/{transcript.id}",
        cookies=auth_cookies_for(user),
    )

    body = response.json()
    assert "current_draft_text" in body
    assert "current_draft_text_encrypted" not in body
```

If a compatibility period is chosen, invert this test later and add a TODO.

---

# Acceptance criteria

This slice is done when:

* Default workspace response does **not** include original PII entity values.
* Original PII values are available only via explicit owner-only reveal endpoint.
* Reveal endpoint uses unsafe method + CSRF protection.
* Sensitive transcript/generated-document/workspace responses include `Cache-Control: no-store`.
* Plaintext response fields are not named `_encrypted`.
* Frontend still displays placeholders/counts by default.
* Frontend reveals original values only after explicit user action.
* Tests cover owner access, non-owner rejection, CSRF, no-store headers, and response shape.

---

## Next slice after this

After PII response minimisation, do **CSP + frontend XSS hardening**:

```text
strict CSP → nonce or remove inline scripts → self-host third-party runtime assets → reduce innerHTML
```
