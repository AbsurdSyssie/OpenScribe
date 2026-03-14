Phase 1: foundation

repo bootstrap

Docker Compose

Postgres

Redis

Vault

FastAPI skeleton

Alembic migrations

pytest setup

baseline docs

Phase 2: identity and onboarding

teams

users

account_requests

sessions

password reset

MFA tables and flow scaffolding

lock/deactivate behavior

Phase 3: transcript core

user_encryption_keys

transcripts

transcript_versions

transcript creation on recording start

draft update flow

commit version flow

transcript root deletion cascade

Phase 4: pseudonymisation persistence

redaction_runs

redaction_entities

lazy redaction creation

reuse logic for same transcript version

Phase 5: templates and quick actions

templates

template_versions

template_watchers

quick_actions

quick_action_versions

quick_action_watchers

watch/fork behavior

same-team discoverability

Phase 6: generated documents

generated_documents

generated_document_sections

freeform generation

structured EMIS note generation

JSON validation

immediate delete for docs and note parts

Phase 7: provider layer

providers

team_provider_credentials

team_provider_policies

user_provider_preferences

provider_usage_events

deterministic provider resolution/fallback

Phase 8: audit, retention, jobs

audit_events

job_runs

retention cleanup worker

user deletion workflow

team deletion preflight checks
