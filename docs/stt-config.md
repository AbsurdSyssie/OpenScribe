# Team STT Configuration

This document describes the implemented speech-to-text provider configuration, team policy, secret, and runtime boundaries.

## Authority model

System administrators:

- provision STT endpoint metadata and credentials for an explicitly selected team;
- create, inspect, edit, finalize, activate/deactivate, diagnose, and delete provider configs;
- can manage platform-wide metadata without gaining transcript-content access.

Team leaders:

- list only ready, active, provisioned options for their own team;
- choose or clear the active provider/model policy for each supported STT selection purpose;
- cannot create, edit, reveal, rotate, or delete raw credentials;
- do not gain transcript-content access.

Normal users:

- cannot use STT management routes;
- consume the resolved team policy indirectly during file, microphone-batch, live-chunked, dictation-preview, or other implemented STT workflows;
- never receive provider credentials or unrestricted Vault references.

## Setup lifecycle

STT setup is stateful:

- `pending_model_selection`: provider/credential inspection has created an incomplete draft; the config is not selectable by leaders/users;
- `ready`: required metadata and a valid model/contract have been finalized; the config may be made active and selected.

Selectable configs must be ready, active, and complete for their adapter/preset. Drafts and revisions are visible only to system administrators.

The system-administrator wizard can:

1. select a provider preset;
2. submit endpoint/credential fields required by that preset;
3. for Deepgram, choose the EU endpoint or explicitly acknowledge the compliance warning before using the Global endpoint;
4. validate credentials and discover models/contracts;
5. persist an incomplete draft without returning the submitted secret to the browser;
6. select/confirm a model and finalize the config;
7. run saved-provider reinspection or a bundled-audio diagnostic later.

## Provider presets and adapters

The UI exposes provider-oriented presets while runtime stores an explicit adapter contract.

Current preset families include:

- OpenAI;
- Deepgram;
- ElevenLabs;
- Custom OpenAI-compatible;
- Custom REST/OpenAPI.

Current adapter kinds include:

- `openai_cloud`;
- `openai_compatible_rest`;
- `elevenlabs_speech_to_text`;
- `generic_rest`.

Preset and adapter are related but not interchangeable: preset is the management-facing provider choice; adapter is the runtime request/response protocol.

### OpenAI cloud

- fixed hosted base path `https://api.openai.com/v1`;
- known `POST /v1/audio/transcriptions` multipart contract;
- required bearer credential, file, and model;
- live discovery through the official SDK;
- bounded built-in transcription model fallback when discovery is unavailable;
- supported IDs include the current GPT transcription families and `whisper-1`.

### OpenAI-compatible REST

- known `/v1/audio/transcriptions` multipart contract;
- intended for private/vendor endpoints implementing the OpenAI transcription shape;
- model is required;
- bearer auth can be optional for self-hosted providers;
- no OpenAPI discovery is required to define the runtime contract.

### Deepgram

- the branded preset defaults to the EU base URL `https://api.eu.deepgram.com`;
- the wizard also permits the Global base URL `https://api.deepgram.com`, but requires the administrator to confirm that its use is permitted by applicable data-protection law, controller instructions, contracts, and international-transfer requirements;
- both supported hosts are recognised as Deepgram and retain the Deepgram-specific request and security rules;
- credential/catalog inspection uses Deepgram's model endpoint;
- runtime sends raw audio bytes to `/v1/listen`;
- model/language/options are query parameters;
- response extraction uses the saved Deepgram channel/alternative path;
- `mip_opt_out=true` is mandatory and enforced on save/runtime;
- raw keys remain Vault-backed.

Selecting the EU endpoint controls routing but is not, by itself, evidence that a deployment satisfies its controller contract, residency terms, retention obligations, or local law. Deployment owners must verify those matters against the purchased Deepgram service and current DPA/order form.

### ElevenLabs

- credential/catalog inspection probes the provider but selectable synchronous STT models are bounded to supported Scribe IDs;
- realtime-only/non-STT models are rejected;
- runtime sends multipart audio to `/v1/speech-to-text` with `xi-api-key`, `file`, `model_id`, and optional language;
- word timestamps/speaker metadata use the provider's `words` response structure;
- raw keys remain Vault-backed.

### Generic REST/OpenAPI

- inspection fetches `base_url + openapi_path`;
- OpenAPI documents are schema-validated and local references are dereferenced before inference;
- the administrator confirms saved request/response mapping fields;
- runtime never re-infers provider shape from the live document; it uses the persisted contract;
- manual save-and-inspect validates the runtime contract with a bundled synthetic sample before finalization.

## Stored metadata

Provider rows can contain metadata such as:

- label and provider preset;
- adapter kind and authentication mode;
- base URL, transcribe path, and optional OpenAPI path;
- default/available models;
- file, model, and language field names;
- response text path;
- segment path and text/start/end/speaker field mapping;
- bounded extra request fields;
- setup/credential inspection status;
- active/ready state;
- credential fingerprint and Vault reference metadata.

The raw credential is never stored in the provider row or returned by API/browser responses.

Labels are unique per team after trimming and case-insensitive comparison.

## Credential lifecycle

Credentials are stored in Vault KV and referenced from PostgreSQL.

- New/replacement credentials are written to unique versioned paths rather than overwriting an active secret before database commit.
- Drafts/revisions that inherit a required credential copy it into a draft-owned Vault path; they do not persist an alias to the active config's reference.
- Finalization copies/promotes metadata and credential references transactionally.
- Replacement, removal, draft cancellation, revision promotion, provider deletion, and team deletion create durable FK-free cleanup intents for retired references.
- The cleanup worker retries and verifies a reference is no longer live before deletion.
- When a Vault write succeeds but the database transaction rolls back, compensation durably queues the orphan reference for cleanup.
- `has_secret` and bounded credential status may be returned; raw values and unrestricted `vault_secret_ref` are not.

`PROVIDER_CREDENTIAL_FINGERPRINT_SECRET` HMACs STT credentials for duplicate detection. Set a stable dedicated value in production. Fingerprints are non-reversible and are not authentication material.

## URL and transport rules

- Non-local provider endpoints require HTTPS.
- HTTP is accepted only for localhost or explicitly allowed private-network development targets.
- Unsupported schemes and unsafe endpoint shapes are rejected.
- Redirects, OpenAPI fetches, model discovery, and diagnostics use bounded provider-inspection rules to reduce SSRF and oversized-response risk.

A local/private HTTP allowance is a development exception, not a production recommendation.

## Team selections

STT selections are team policy rows separate from credential-bearing provider configs.

- A leader/system administrator chooses a provisioned ready config and, where supported, a model/language override.
- Selection purposes remain distinct; consultation STT and post-consultation dictation/preview policy are not silently conflated.
- Clearing a selection removes only the team policy row, not the provider config or secret.
- Runtime snapshots the resolved config/model metadata onto queued ingestion work so later selection edits do not mutate an existing job.
- Runtime validates the snapshot and resolves the saved credential before marking the provider attempt submitted.

## Runtime transcription

For queued ingestion:

1. create the transcript/job and task-dispatch outbox row transactionally;
2. read source audio from its bounded Vault reference;
3. normalize/probe audio and enforce limits;
4. claim the job and provider quota reservation;
5. validate the snapshotted STT config;
6. resolve the Vault-backed credential;
7. mark the provider attempt submitted only at the dispatch boundary;
8. call the adapter with `STT_TRANSCRIPTION_TIMEOUT_SECONDS` (default four hours);
9. encrypt and append result text for the owner;
10. settle usage and clear/durably clean source audio.

A definite credential failure before dispatch cancels the reservation without audio usage. Duplicate workers use database claims so a losing worker cannot fail or settle the winner's attempt.

Current capture behavior is described in [transcript-capture.md](transcript-capture.md).

## Diagnostics

System administrators can run a saved-config diagnostic from the admin workspace for a selected team. It:

- reads saved metadata and the Vault-backed credential;
- performs an adapter-appropriate health check when applicable;
- submits the local `tests/example_audio.wav` sample;
- reports bounded status, endpoint/model metadata, duration/size, and either sample transcript text or a sanitized provider error;
- does not create a user transcript or ingestion job;
- does not reveal the credential.

Before running the diagnostic, add a synthetic, non-patient WAV file at `tests/example_audio.wav`; Git ignores this local file. If it is missing, the diagnostic fails before it reads credentials or calls the provider. Returned sample transcript text is test-fixture output, not user content, but it should still be handled as diagnostic data and not copied into public logs/issues unnecessarily.

## API boundary

Provider provisioning routes are system-admin-only. Selection routes require a full manager session and enforce own-team leader scope. Onboarding and pending-MFA sessions cannot use them.

The complete route and schema inventory is maintained in [api.md](api.md). The route-auth audit manifest must be updated with every new `/api/v1` STT endpoint.

## Operational checklist

Before enabling a provider for users:

1. verify Vault KV-v2 and runtime token permissions;
2. create/inspect the provider without copying credentials into logs;
3. verify the selected provider region against the controller contract and transfer documentation;
4. finalize a supported model/contract;
5. run the saved diagnostic;
6. make the config active;
7. select it for the intended team/purpose;
8. run an owner file/live capture smoke test;
9. confirm worker, Beat, quota, and cleanup processes are running;
10. review provider residency/retention/processing terms separately from technical connectivity.

Provider success does not establish regulatory suitability. Deployment owners must separately assess data processing, retention, regional routing, contracts, and consent requirements.
