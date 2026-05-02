# Celery Audio Payload Hardening

## Purpose

This note describes the implementation plan for removing raw consultation audio from Celery/Redis task payloads.

The worker still needs access to audio so it can submit it to the configured STT provider. The security change is to stop sending the audio through the Celery message itself. Celery should carry only a job identifier; the worker should load the queued job, read the audio from controlled storage, process it, and clear the stored source audio when appropriate.

## Target behaviour

Before:

```text
FastAPI route -> Celery/Redis task contains base64 audio -> worker
```

After:

```text
FastAPI route -> write audio to Vault-backed storage -> DB stores source_audio_vault_ref -> Celery/Redis task contains only job_id -> worker reads audio by source_audio_vault_ref
```

## Scope

Change only the audio-ingestion worker path.

Do not include unrelated hardening work in this patch, such as CSRF, CSP, cookie, provider allowlist, or UI changes.

## Files to change

```text
app/tasks.py
app/services/transcripts.py
app/routes/web_transcribe.py
app/routes/api_routes.py
tests/...
docs/progress/2026-05-02-celery-audio-payload-hardening.md
```

Optional later cleanup:

```text
alembic/versions/<new>_remove_source_audio_blob_from_ingestion_jobs.py
```

For the first pass, stop writing and reading `source_audio_blob`, but do not necessarily drop the column immediately. Dropping the column can be a follow-up migration after confirming no deployed workers or failed legacy jobs still rely on it.

## Required changes

### 1. Update `app/tasks.py`

Remove audio from the task signature and from the enqueue function.

```diff
diff --git a/app/tasks.py b/app/tasks.py
--- a/app/tasks.py
+++ b/app/tasks.py
@@
-import base64
 from uuid import UUID
@@
 @celery_app.task(name="openscribe.process_transcript_ingestion_job")
-def process_transcript_ingestion_job_task(*, job_id: str, audio_b64: str) -> None:
-    audio_bytes = base64.b64decode(audio_b64.encode("ascii"))
+def process_transcript_ingestion_job_task(*, job_id: str) -> None:
     with SessionLocal() as db:
-        process_transcript_ingestion_job(db, job_id=UUID(job_id), audio_bytes=audio_bytes)
+        process_transcript_ingestion_job(db, job_id=UUID(job_id))
 
 
-def enqueue_transcript_ingestion_job(*, job_id: UUID, audio_bytes: bytes):
-    payload = base64.b64encode(audio_bytes).decode("ascii")
-    return process_transcript_ingestion_job_task.delay(job_id=str(job_id), audio_b64=payload)
+def enqueue_transcript_ingestion_job(*, job_id: UUID):
+    return process_transcript_ingestion_job_task.delay(job_id=str(job_id))
```

Expected result: Celery task messages contain only `job_id`.

### 2. Update `app/services/transcripts.py`

Use the existing Vault-backed source audio helpers:

```python
write_transcript_ingestion_source_audio
read_transcript_ingestion_source_audio
delete_transcript_ingestion_source_audio
```

The repo already has `source_audio_vault_ref` on `transcript_ingestion_jobs`. Use that field for queued audio.

#### 2.1 Add a queued-source reader

Add near `_read_retry_source_audio`:

```python
def _read_queued_source_audio(job: TranscriptIngestionJob) -> bytes:
    if not job.source_audio_vault_ref:
        raise AppError(
            409,
            "ingestion_source_unavailable",
            "Queued audio is no longer available. Upload the audio file again.",
            {"job_id": str(job.id), "transcript_id": str(job.transcript_id)},
        )
    try:
        return read_transcript_ingestion_source_audio(secret_ref=job.source_audio_vault_ref)
    except AppError as exc:
        if exc.code == "vault_read_failed":
            raise AppError(
                409,
                "ingestion_source_unavailable",
                "Queued audio is no longer available. Upload the audio file again.",
                {"job_id": str(job.id), "transcript_id": str(job.transcript_id)},
            ) from exc
        raise
```

#### 2.2 Stop treating `source_audio_blob` as a new retry source

Change:

```python
def _retry_source_available(job: TranscriptIngestionJob) -> bool:
    return bool(job.source_audio_blob or job.source_audio_vault_ref)
```

to:

```python
def _retry_source_available(job: TranscriptIngestionJob) -> bool:
    return bool(job.source_audio_vault_ref)
```

Change `_read_retry_source_audio` so it only reads from `source_audio_vault_ref`. Keep `clear_ingestion_retry_source` clearing `job.source_audio_blob = None` for backwards cleanup compatibility, but do not write new blobs.

#### 2.3 Queue whole-file audio via Vault

In `queue_audio_file_ingestion`, generate the job ID before constructing the model, write the audio to Vault, then store the ref:

```python
source_audio_vault_ref = None
try:
    job_id = uuid4()
    source_audio_vault_ref = write_transcript_ingestion_source_audio(
        job_id=job_id,
        audio_bytes=normalized_audio_bytes,
    )
    job = TranscriptIngestionJob(
        id=job_id,
        transcript_id=transcript.id,
        job_kind=TranscriptIngestionJobKind.audio_file,
        status=TranscriptIngestionJobStatus.queued,
        filename=filename,
        source_audio_blob=None,
        source_audio_vault_ref=source_audio_vault_ref,
        source_audio_size_bytes=len(source_audio_bytes),
        source_audio_duration_seconds=source_audio_duration_seconds,
        # existing STT snapshot fields remain unchanged
    )
    db.add(job)
    db.commit()
    db.refresh(job)
except Exception:
    if source_audio_vault_ref:
        delete_transcript_ingestion_source_audio(secret_ref=source_audio_vault_ref)
    raise
```

Use the same bytes that the worker should send to STT. If the current service intentionally normalizes to WAV before sending, store `normalized_audio_bytes`. If the current service intentionally sends the original upload, store `source_audio_bytes`. The important security property is that those bytes are not passed through Celery/Redis and are not written to `source_audio_blob`.

#### 2.4 Queue live chunks via Vault

Apply the same pattern in `queue_audio_chunk_ingestion`:

```python
source_audio_vault_ref = None
try:
    job_id = uuid4()
    source_audio_vault_ref = write_transcript_ingestion_source_audio(
        job_id=job_id,
        audio_bytes=source_audio_bytes,
    )
    job = TranscriptIngestionJob(
        id=job_id,
        transcript_id=transcript.id,
        job_kind=TranscriptIngestionJobKind.live_chunk,
        status=TranscriptIngestionJobStatus.queued,
        chunk_sequence_no=chunk_sequence_no,
        filename=filename,
        source_audio_blob=None,
        source_audio_vault_ref=source_audio_vault_ref,
        source_audio_size_bytes=len(source_audio_bytes),
        declared_duration_seconds=declared_duration_seconds,
        # existing STT snapshot fields remain unchanged
    )
    db.add(job)
    db.commit()
    db.refresh(job)
except Exception:
    if source_audio_vault_ref:
        delete_transcript_ingestion_source_audio(secret_ref=source_audio_vault_ref)
    raise
```

#### 2.5 Retry should create a new Vault-backed queued job

In `retry_audio_file_ingestion`, keep reading the previous failed job's retry source via `_read_retry_source_audio(previous_job)`, but when creating the replacement job, write the bytes to a new Vault ref and set `source_audio_blob=None`.

The replacement job should not require the route to pass bytes into Celery.

If the function currently returns `(transcript, job, source_audio_blob, previous_job)`, either:

- keep returning the bytes temporarily but ignore them in routes, or
- update the return shape to `(transcript, job, previous_job)` and adjust callers.

The smaller patch is to keep the return shape and ignore the bytes.

#### 2.6 Worker should load audio by `job_id`

Change:

```python
def process_transcript_ingestion_job(db: Session, *, job_id: UUID, audio_bytes: bytes) -> TranscriptIngestionJob:
```

to:

```python
def process_transcript_ingestion_job(db: Session, *, job_id: UUID) -> TranscriptIngestionJob:
```

After loading and validating the job/transcript, read source audio:

```python
audio_bytes = _read_queued_source_audio(job)
```

Then continue with the existing STT call using `audio_bytes`.

On success, clear the source audio secret:

```python
clear_ingestion_retry_source(
    db,
    job_id=job.id,
    clear_storage=True,
    clear_accounting=False,
    delete_backing_secret=True,
)
```

On failure, keep the source audio secret so retry still works.

Expected cleanup semantics:

```text
success -> delete Vault source audio
failure -> keep Vault source audio for retry
retry success -> delete new job's Vault source audio and clear previous retry source as existing route does
transcript deletion -> delete all source audio refs for that transcript
```

### 3. Update `app/routes/web_transcribe.py`

Every route should enqueue by `job_id` only.

Replace:

```python
task_result = main_module.enqueue_transcript_ingestion_job(job_id=job.id, audio_bytes=audio_bytes)
```

with:

```python
task_result = main_module.enqueue_transcript_ingestion_job(job_id=job.id)
```

In retry handling, replace:

```python
task_result = main_module.enqueue_transcript_ingestion_job(job_id=job.id, audio_bytes=source_audio_blob)
```

with:

```python
task_result = main_module.enqueue_transcript_ingestion_job(job_id=job.id)
```

If the retry service still returns source bytes, rename the local variable to indicate it is intentionally unused:

```python
transcript, job, _source_audio_blob, previous_job = retry_audio_file_ingestion(...)
```

### 4. Update `app/routes/api_routes.py`

Search for:

```python
enqueue_transcript_ingestion_job(job_id=job.id, audio_bytes=
```

Replace each call with:

```python
enqueue_transcript_ingestion_job(job_id=job.id)
```

Expected affected API endpoints:

```text
POST /api/v1/transcripts/{transcript_id}/audio-chunks
POST /api/v1/transcripts/{transcript_id}/audio-file
POST /api/v1/transcripts/{transcript_id}/retry-audio-file
```

### 5. Optional migration

Do not drop `source_audio_blob` immediately unless the model and tests are updated in the same deployment. A safer first migration only clears legacy blobs:

```python
"""clear legacy source audio blobs

Revision ID: <new_revision>
Revises: z6b7c8d9e0f1
Create Date: 2026-05-02
"""

from alembic import op


revision = "<new_revision>"
down_revision = "z6b7c8d9e0f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE transcript_ingestion_jobs SET source_audio_blob = NULL WHERE source_audio_blob IS NOT NULL")


def downgrade() -> None:
    pass
```

Create a later migration to drop the column after confirming no deployed code references it.

## Tests to add

### 1. Enqueue payload contains only `job_id`

```python
def test_enqueue_transcript_ingestion_job_does_not_send_audio(monkeypatch):
    from uuid import uuid4
    from app import tasks

    captured = {}

    class FakeTask:
        @staticmethod
        def delay(**kwargs):
            captured.update(kwargs)

            class Result:
                id = "task-1"

            return Result()

    monkeypatch.setattr(tasks, "process_transcript_ingestion_job_task", FakeTask)

    job_id = uuid4()
    tasks.enqueue_transcript_ingestion_job(job_id=job_id)

    assert captured == {"job_id": str(job_id)}
    assert "audio_b64" not in captured
    assert "audio_bytes" not in captured
```

### 2. Queueing audio stores a Vault ref and not a DB blob

```python
def test_queue_audio_file_ingestion_stores_vault_ref_not_raw_blob(db, active_user, transcript, stt_selection, monkeypatch):
    from app.services import transcripts as transcript_service

    written = {}

    def fake_write_source_audio(*, job_id, audio_bytes):
        written["job_id"] = job_id
        written["audio_bytes"] = audio_bytes
        return f"secret:openscribe/transcript-ingestion/{job_id}/source-audio"

    monkeypatch.setattr(transcript_service, "write_transcript_ingestion_source_audio", fake_write_source_audio)
    monkeypatch.setattr(transcript_service, "normalize_audio_to_wav_16k_mono", lambda audio: audio)
    monkeypatch.setattr(transcript_service, "inspect_audio_duration_seconds", lambda audio: 1.0)
    monkeypatch.setattr(transcript_service, "enforce_whole_file_duration_limit", lambda *, duration_seconds: None)

    _, job = transcript_service.queue_audio_file_ingestion(
        db,
        active_user,
        transcript_id=transcript.id,
        filename="sample.wav",
        source_audio_blob=b"synthetic audio",
    )

    assert job.source_audio_vault_ref
    assert job.source_audio_blob is None
    assert written["job_id"] == job.id
    assert written["audio_bytes"] == b"synthetic audio"
```

Adjust fixture names to match the existing test suite.

### 3. Worker reads source audio from Vault

```python
def test_process_transcript_ingestion_job_reads_audio_from_vault(db, queued_ingestion_job, monkeypatch):
    from app.services import transcripts as transcript_service

    read_refs = []

    def fake_read_source_audio(*, secret_ref):
        read_refs.append(secret_ref)
        return b"synthetic audio"

    def fake_transcribe(**kwargs):
        assert kwargs["audio_bytes"] == b"synthetic audio"
        return "synthetic transcript text"

    monkeypatch.setattr(transcript_service, "read_transcript_ingestion_source_audio", fake_read_source_audio)
    monkeypatch.setattr(transcript_service, "transcribe_with_stt_snapshot", fake_transcribe)

    job = transcript_service.process_transcript_ingestion_job(db, job_id=queued_ingestion_job.id)

    assert read_refs == [queued_ingestion_job.source_audio_vault_ref]
    assert job.status in {
        transcript_service.TranscriptIngestionJobStatus.completed,
        transcript_service.TranscriptIngestionJobStatus.applied,
    }
```

### 4. Successful ingestion deletes backing source audio

```python
def test_successful_ingestion_deletes_source_audio_secret(db, queued_ingestion_job, monkeypatch):
    from app.services import transcripts as transcript_service

    deleted = []

    monkeypatch.setattr(
        transcript_service,
        "read_transcript_ingestion_source_audio",
        lambda *, secret_ref: b"synthetic audio",
    )
    monkeypatch.setattr(
        transcript_service,
        "transcribe_with_stt_snapshot",
        lambda **kwargs: "synthetic transcript text",
    )
    monkeypatch.setattr(
        transcript_service,
        "delete_transcript_ingestion_source_audio",
        lambda *, secret_ref: deleted.append(secret_ref),
    )

    transcript_service.process_transcript_ingestion_job(db, job_id=queued_ingestion_job.id)

    assert queued_ingestion_job.source_audio_vault_ref in deleted
```

## Implementation checklist

```text
- Celery task payloads contain only job_id.
- Worker reads queued audio from source_audio_vault_ref.
- New ingestion jobs never write source_audio_blob.
- Successful jobs delete source audio secret.
- Failed jobs keep source audio secret for retry.
- Transcript deletion still clears source audio secrets.
- Existing STT provider snapshot logic is unchanged.
- Existing owner-only transcript access model is unchanged.
- No audio or transcript content is logged.
```

## Documentation entry

Create:

```text
docs/progress/2026-05-02-celery-audio-payload-hardening.md
```

Suggested content:

```md
# Celery Audio Payload Hardening

## 1. Scope
- Removed raw audio from Celery task payloads.
- Changed transcription workers to load audio by `source_audio_vault_ref`.
- Stopped writing new raw audio to `transcript_ingestion_jobs.source_audio_blob`.
- Preserved retry behaviour through Vault-backed source audio.

## 2. Checklist
- [x] Code complete
- [x] Tests added/updated
- [x] Docs added/updated
- [ ] Later cleanup: drop legacy `source_audio_blob` column after deployment confirmation

## 3. Files changed
- `app/tasks.py`: Celery payload now contains only `job_id`.
- `app/services/transcripts.py`: queued source audio is stored/read via Vault.
- `app/routes/web_transcribe.py`: enqueue calls no longer pass audio bytes.
- `app/routes/api_routes.py`: enqueue calls no longer pass audio bytes.
- `tests/...`: regression tests for task payload and Vault-backed audio flow.

## 4. Tests
- Verified Celery enqueue sends only `job_id`.
- Verified queueing audio stores a Vault ref and no raw DB blob.
- Verified worker reads source audio from Vault.
- Verified successful ingestion deletes backing source audio.

## 5. Documentation
- Added this progress note.

## 6. Risks / assumptions
- Existing failed jobs with only `source_audio_blob` may become non-retryable after cleanup.
- A later migration should drop `source_audio_blob` once no deployed code references it.

## 7. Checkpoint summary
- Privacy boundary preserved: Redis/Celery no longer carries audio content.
- Ownership model preserved: transcript services still resolve jobs through transcript ownership/state.
- Deletion semantics preserved: successful jobs and transcript deletion clear source audio.
- Provider rules preserved: STT provider resolution remains unchanged.
- Structured-note contract unaffected.
```

## Commit message

```text
Harden transcription worker audio payload handling
```

Longer commit message:

```text
Harden transcription worker audio payload handling

Stop passing uploaded audio through Celery/Redis task payloads. Store queued
audio via the existing Vault-backed source audio path and pass only job_id to
workers. Update web/API enqueue calls and worker processing to fetch audio by
source_audio_vault_ref, with cleanup after successful ingestion.
```
