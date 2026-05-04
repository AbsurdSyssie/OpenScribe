## Next slice: server-controlled retention policy

This is the cleanest next security slice because it is small, testable, and closes a real PII lifecycle issue.

### Goal

Users must not be able to extend transcript retention from public transcript-create/start APIs.

Current issue:

* `TranscriptCreate` and `TranscriptStart` expose `retention_days_applied`. 
* Transcript creation uses `payload.retention_days_applied or owner.team.default_retention_days`, so client input can override team policy. 

The agent should make retention **server-owned**.

---

# Implementation brief for agent

## 1. Remove retention from public transcript schemas

In:

```text
app/schemas/transcripts.py
```

Remove this field from both:

```python
class TranscriptCreate(BaseModel)
class TranscriptStart(BaseModel)
```

Remove:

```python
retention_days_applied: int | None = Field(default=None, ge=1)
```

Keep `retention_days_applied` in response models, because it is useful as a server-applied snapshot.

---

## 2. Update transcript creation service

In:

```text
app/services/transcripts.py
```

Change `_create_transcript_row`.

Current pattern:

```python
retention_days = retention_days_applied or owner.team.default_retention_days
```

Replace with:

```python
retention_days = owner.team.default_retention_days
```

Then remove the `retention_days_applied` parameter from:

```python
_create_transcript_row(...)
start_transcript(...)
create_transcript_from_payload(...)
```

Expected shape:

```python
def _create_transcript_row(
    db: Session,
    *,
    owner: User,
    title: str | None,
    current_draft_text_encrypted: str | None,
    structured_context_json: dict | None,
    ingestion_mode: TranscriptIngestionMode,
) -> Transcript:
    if owner.is_system_admin or owner.team_id is None:
        raise AppError(403, "forbidden", "System-admin accounts cannot own transcript content")
    if owner.team is None:
        raise AppError(404, "not_found", "Team not found", {"resource": "team", "team_id": str(owner.team_id)})

    retention_days = owner.team.default_retention_days

    transcript = Transcript(
        ...
        retention_days_applied=retention_days,
        retention_expires_at=transcript_expiry(retention_days),
    )
```

Then update callers:

```python
return _create_transcript_row(
    db,
    owner=owner,
    title=payload.title,
    current_draft_text_encrypted=payload.current_draft_text_encrypted,
    structured_context_json=payload.structured_context_json,
    ingestion_mode=payload.ingestion_mode,
)
```

---

## 3. Add retention bounds for team policy

Define central constants, probably in `app/services/admin.py` or a new config module:

```python
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = int(os.getenv("MAX_RETENTION_DAYS", "90"))
```

Apply them anywhere team retention is created or updated.

Expected validation:

```python
def validate_retention_days(value: int) -> int:
    if value < MIN_RETENTION_DAYS or value > MAX_RETENTION_DAYS:
        raise AppError(
            422,
            "business_rule_violation",
            f"Retention must be between {MIN_RETENTION_DAYS} and {MAX_RETENTION_DAYS} days",
            {
                "field": "default_retention_days",
                "min": MIN_RETENTION_DAYS,
                "max": MAX_RETENTION_DAYS,
            },
        )
    return value
```

Use it in:

```text
create_team_service
update_team_service, if present
```

Do not apply this to already-created transcript snapshots unless there is an explicit retention migration requirement.

---

## 4. Optional DB check constraint

Add a migration if you want database enforcement too.

Example:

```python
op.create_check_constraint(
    "ck_teams_default_retention_days_bounds",
    "teams",
    "default_retention_days >= 1 AND default_retention_days <= 90",
)
```

Only do this if `MAX_RETENTION_DAYS` is fixed in deployment. If it needs to be environment-configurable, keep the DB constraint simple:

```sql
default_retention_days >= 1
```

and enforce the max in application code.

---

# Tests to add

## Public payload cannot override retention

```python
def test_start_transcript_ignores_client_retention_override(client, db, user, team):
    team.default_retention_days = 30
    db.add(team)
    db.commit()

    response = client.post(
        "/api/v1/transcripts/start",
        json={
            "title": "Retention attempt",
            "ingestion_mode": "whole_file",
            "retention_days_applied": 999,
        },
        cookies=auth_cookies_for(user),
        headers=csrf_headers_for(user),
    )

    assert response.status_code in {200, 201}
    body = response.json()
    assert body["retention_days_applied"] == 30
```

Depending on Pydantic config, extra fields may be ignored. That is acceptable if the server ignores them. If the project uses `extra="forbid"`, assert `422` instead. Pick one behaviour and make it consistent.

## Create transcript uses team default

```python
def test_create_transcript_applies_team_retention_default(db, user, team):
    team.default_retention_days = 14
    db.add(team)
    db.commit()

    transcript = start_transcript(
        db,
        user,
        TranscriptStart(title="Test", ingestion_mode=TranscriptIngestionMode.whole_file),
    )

    assert transcript.retention_days_applied == 14
```

## User cannot extend retention via update

```python
def test_update_transcript_cannot_extend_retention(client, transcript, user):
    original_expires_at = transcript.retention_expires_at

    response = client.patch(
        f"/api/v1/transcripts/{transcript.id}",
        json={
            "title": "Updated title",
            "retention_days_applied": 999,
        },
        cookies=auth_cookies_for(user),
        headers=csrf_headers_for(user),
    )

    assert response.status_code == 200
    db.refresh(transcript)
    assert transcript.retention_expires_at == original_expires_at
```

## Team retention bounds

```python
def test_team_retention_cannot_exceed_max(client, system_admin):
    response = client.post(
        "/api/v1/teams",
        json={
            "name": "Unsafe retention",
            "status": "active",
            "default_retention_days": 9999,
        },
        cookies=auth_cookies_for(system_admin),
        headers=csrf_headers_for(system_admin),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "business_rule_violation"
```

---

# Acceptance criteria

The slice is complete when:

* `retention_days_applied` is no longer accepted as a meaningful client-controlled create/start input.
* New transcripts always use `owner.team.default_retention_days`.
* Transcript response still shows the applied retention snapshot.
* Users cannot extend retention by create, start, patch, upload, retry, or workspace flows.
* Team default retention has explicit min/max validation.
* Tests cover direct service calls and API calls.

---

## Why this slice next

This is a narrow PII-control fix with low regression risk. It also prepares for later retention-enforcement work, such as scheduled deletion, admin retention reporting, and “delete expired transcript content” jobs.
