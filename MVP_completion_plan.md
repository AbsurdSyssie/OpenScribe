# MVP Completion Plan

## Goal

Finish OpenScribe as a production-ready MVP without weakening the current ownership, privacy, deletion, provider, or structured-note rules.

The product already has the core user-facing flows:

- managed auth and onboarding
- team/provider administration
- whole-file transcription
- semi-live transcription
- note, follow-up, and quick-action generation
- redaction
- owner-scoped at-rest encryption

The remaining work is mostly operational hardening, deletion/retention completion, and supportability.

## Milestones

### 1. Admin observability and usage controls

Status: in progress

- [x] persist ingestion byte and duration telemetry
- [x] persist provider usage metadata for document generation
- [x] expose a system-admin usage view for team and per-user telemetry
- [ ] add explicit admin API/report exports for usage review
- [ ] add quota-management UI for team/user operational limits

### 2. Retention and deletion completion

Status: pending

- [ ] add deterministic retention-expiry worker coverage
- [ ] add best-effort external cleanup reconciliation for Vault-backed retry audio
- [ ] add admin verification tooling for transcript/user hard-delete completion
- [ ] add operational reporting for orphaned external artifacts

### 3. Production encryption rollout

Status: pending

- [x] encrypt new transcript-derived content at rest with owner DEKs
- [x] preserve per-row KEK metadata for unwraps
- [ ] backfill pre-existing plaintext transcript-derived rows in production environments
- [ ] add DEK rotation/rewrap tooling
- [ ] document Vault recovery, KEK rotation, and account-recovery implications

### 4. Provider transport hardening

Status: pending

- [ ] split STT transport capability into `http_chunked` vs `websocket_streaming`
- [ ] keep current batch-oriented live flow for non-streaming STT providers
- [ ] add websocket-native live transport where the provider supports it
- [ ] preserve owner-only transcript visibility and server-side provider-secret handling

### 5. Deployment hardening

Status: pending

- [ ] production Vault bootstrap and least-privilege policy runbooks
- [ ] background-job readiness and failure-recovery runbooks
- [ ] backup/restore validation for Postgres and Vault
- [ ] production health/readiness checks aligned to real dependencies

## Delivery order

1. Admin observability and usage controls
2. Retention and deletion completion
3. Production encryption rollout
4. Deployment hardening
5. Provider transport hardening

## Notes

- Transcript roots remain the deletion and retention root.
- All transcript-derived content remains owner-only.
- Team leaders and system admins continue to manage metadata and configuration, not transcript content.
- Provider secrets remain server-side only.
- Structured-note payload shape remains unchanged.
