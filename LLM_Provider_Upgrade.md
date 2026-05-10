Current repo state

The branch already added:

LlmProviderPreset with the agreed provider set.
provider_preset and inspection_metadata_json on TeamLlmConfig.
Branded provider defaults and Bedrock HTTP region helpers.
Model discovery and manual fallback.
System-admin-only provisioning.
Vault-backed credential storage.

The current LlmConfigUpsert still expects a label/model-oriented save payload, and LlmConfigDetail has no setup status yet.

The current service upsert still requires a model_name before a config can be persisted, which conflicts with the desired wizard state where the API key is saved before model selection.

The current selectable config query only filters by is_active=True, so it must be tightened to exclude setup drafts.

Goal

Change admin provider setup from a single dense form into a stateful wizard:

Choose provider
→ Enter key / endpoint details
→ Check API key and find models
→ Create draft config and save key to Vault
→ Select default model
→ Edit provider label
→ Toggle “Available for team selection”
→ Save provider

The API key must not be returned to the browser or carried in hidden inputs after inspection.

Locked UX behavior
First step

Show only the relevant fields:

Team
Provider
API key
[Check API key and find models]

Provider-specific additions:

Ollama:
Team
Provider
Base URL
[Check API key and find models]

Bedrock HTTP gateway:
Team
Provider
Region
API key
[Check API key and find models]

Custom OpenAI-compatible:
Team
Provider
Base URL
API key
[Check API key and find models]
After successful discovery

Hide the API key field. Show:

Provider: OpenRouter
Credential: saved
Provider name: [OpenRouter · Clinic North]
Default model: [model dropdown]
Available for team selection: [x]
[Save provider]
[Replace API key]
[Delete incomplete setup]
If discovery fails or returns zero models

Show:

Could not find models.
[Try again]
[Enter model manually]

If admin chooses manual entry:

Model name
Provider name
Available for team selection
[Save provider]
Provider list states

Show provider configs in these states:

Setup incomplete
Ready · available
Ready · unavailable

Pending providers must be visible to system admins but unavailable to team leaders/users.

Data model changes
Add setup status enum

Add to app/models.py:

class LlmConfigSetupStatus(str, enum.Enum):
    pending_model_selection = "pending_model_selection"
    ready = "ready"

Add to TeamLlmConfig:

setup_status: Mapped[LlmConfigSetupStatus] = mapped_column(
    Enum(LlmConfigSetupStatus),
    default=LlmConfigSetupStatus.ready,
    server_default=LlmConfigSetupStatus.ready.value,
    nullable=False,
)
Migration

Create a new Alembic migration after the provider preset migration.

Migration behavior:

ALTER TABLE team_llm_configs
ADD COLUMN setup_status VARCHAR/ENUM NOT NULL DEFAULT 'ready';

Backfill rule:

model_name IS NULL -> pending_model_selection
model_name exists  -> ready

If using SQLAlchemy enum, align with existing enum migration style. If avoiding a DB enum, use a string column and Python enum validation.

Recommended if the project tolerates string status fields:

op.add_column(
    "team_llm_configs",
    sa.Column(
        "setup_status",
        sa.String(length=64),
        nullable=False,
        server_default="ready",
    ),
)
op.execute(
    """
    UPDATE team_llm_configs
    SET setup_status = CASE
        WHEN model_name IS NULL THEN 'pending_model_selection'
        ELSE 'ready'
    END
    """
)
op.create_index(
    "ix_team_llm_configs_setup_status",
    "team_llm_configs",
    ["setup_status"],
)
op.alter_column("team_llm_configs", "setup_status", server_default=None)
Schema changes
Extend LlmConfigDetail

Add:

setup_status: LlmConfigSetupStatus

Also consider adding display-only fields:

provider_display_name: str
setup_status_label: str | None = None

The display fields are optional, but useful for admin templates.

Add draft request/response schemas

Add:

class LlmConfigDraftCreate(BaseModel):
    model_config = {"protected_namespaces": ()}

    team_id: UUID
    provider_preset: LlmProviderPreset = LlmProviderPreset.openai
    base_url: str = Field(default="", max_length=2048)
    bearer_token: str | None = Field(default=None, min_length=1)
    bedrock_region: str | None = Field(default=None, max_length=64)

Response can reuse LlmConfigDetail plus inspection result, or define a specific result:

class LlmConfigDraftCreateResult(BaseModel):
    model_config = {"protected_namespaces": ()}

    config: LlmConfigDetail
    provider_display_name: str
    available_models: list[str]
    available_model_options: list[LlmModelOption]
    discovery_status: Literal["fetched", "manual_required", "failed"]
    default_model_source: Literal["provider", "manual", "none"]
    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
Add finalize request schema
class LlmConfigFinalize(BaseModel):
    model_config = {"protected_namespaces": ()}

    team_id: UUID
    config_id: UUID
    label: str = Field(min_length=1, max_length=255)
    model_name: str = Field(min_length=1, max_length=255)
    is_active: bool = True
Add key replacement request schema
class LlmConfigDraftReplaceCredential(BaseModel):
    model_config = {"protected_namespaces": ()}

    team_id: UUID
    config_id: UUID
    bearer_token: str = Field(min_length=1)

This should re-run discovery and keep the config in pending_model_selection.

Service changes
Add helper: auto-label provider

Add in llm_presets.py or llm.py:

def default_llm_config_label(*, provider_display_name: str, team_name: str) -> str:
    return f"{provider_display_name} · {team_name}"
Add helper: build draft config

Create a service function:

def create_llm_config_draft(
    db: Session,
    actor: User,
    payload: LlmConfigDraftCreate,
) -> tuple[TeamLlmConfig, LlmConfigInspectResult]:
    ...

Behavior:

Require system admin.
Resolve team.
Apply provider defaults.
Reclassify branded base URL overrides to custom_openai_compatible.
Resolve preset/adapter/base URL.
Validate credential requirements.
Run model discovery.
Create TeamLlmConfig with:
auto label
setup_status=pending_model_selection
is_active=False
model_name=None
available_models_json=discovered models
inspection_metadata_json=inspection metadata
vault_secret_ref=pending initially if needed
Flush to get config ID.
Write credential to Vault if supplied.
Commit.
Return config + inspection result.
Never return raw secret.

Pseudo-code:

def create_llm_config_draft(db, actor, payload):
    team = _resolve_admin_scoped_team(db, actor, team_id=payload.team_id)

    provider_preset, adapter_kind, base_url, region = apply_provider_defaults(
        provider_preset=payload.provider_preset,
        base_url=payload.base_url,
        bedrock_region=payload.bedrock_region,
    )
    provider_preset = reclassify_preset_for_base_url(provider_preset, base_url)
    preset = get_llm_provider_preset(provider_preset)
    adapter_kind = preset.adapter_kind

    if preset.requires_bearer_token and not payload.bearer_token:
        raise AppError(
            422,
            "business_rule_violation",
            "This LLM provider requires an API key",
            {"field": "bearer_token"},
        )

    inspection = inspect_llm_contract(db, actor, LlmInspectRequest(
        team_id=team.id,
        provider_preset=provider_preset,
        adapter_kind=adapter_kind,
        base_url=base_url,
        bearer_token=payload.bearer_token,
        bedrock_region=region,
    ))

    config = TeamLlmConfig(
        id=uuid4(),
        team_id=team.id,
        label=default_llm_config_label(
            provider_display_name=preset.display_name,
            team_name=team.name,
        ),
        provider_preset=provider_preset,
        adapter_kind=adapter_kind,
        base_url=base_url,
        auth_mode=LlmAuthMode.bearer if preset.requires_bearer_token else LlmAuthMode.none,
        model_name=None,
        available_models_json=list(inspection.available_models),
        inspection_metadata_json=_inspection_metadata(inspection),
        vault_secret_ref="pending" if payload.bearer_token else "",
        setup_status=LlmConfigSetupStatus.pending_model_selection,
        is_active=False,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    db.add(config)
    db.flush()

    if payload.bearer_token:
        config.vault_secret_ref = write_team_llm_bearer_token(
            team_id=team.id,
            config_id=config.id,
            bearer_token=payload.bearer_token,
        )

    db.commit()
    db.refresh(config)
    return config, inspection
Add helper: finalize draft
def finalize_llm_config_draft(
    db: Session,
    actor: User,
    payload: LlmConfigFinalize,
) -> TeamLlmConfig:
    ...

Behavior:

Require system admin.
Load config by team/config.
Allow only if setup_status=pending_model_selection, or optionally allow re-finalizing ready configs.
Require model name.
If available_models_json is non-empty, selected model must be in it.
If available_models_json is empty and model supplied manually, store [model_name].
Set:
label
model_name
setup_status=ready
is_active=payload.is_active
inspection_metadata_json.manual_model_name if manual
Commit.

Pseudo-code:

def finalize_llm_config_draft(db, actor, payload):
    team = _resolve_admin_scoped_team(db, actor, team_id=payload.team_id)
    config = _get_admin_llm_config_for_team(db, team.id, payload.config_id)

    if _llm_config_has_in_flight_jobs(db, config_id=config.id):
        raise AppError(409, "conflict", "Cannot edit this LLM config while generated documents are queued or processing")

    model_name = payload.model_name.strip()
    available = list(config.available_models_json or [])

    if available and model_name not in available:
        raise AppError(
            422,
            "business_rule_violation",
            "Selected model is not available for this provider",
            {"field": "model_name"},
        )

    if not available:
        available = [model_name]
        metadata = dict(config.inspection_metadata_json or {})
        metadata["manual_model_name"] = model_name
        metadata["discovery_status"] = "manual_required"
        metadata["default_model_source"] = "manual"
        config.inspection_metadata_json = metadata

    config.label = payload.label.strip()
    config.model_name = model_name
    config.available_models_json = available
    config.setup_status = LlmConfigSetupStatus.ready
    config.is_active = payload.is_active
    config.updated_by_user_id = actor.id

    db.add(config)
    db.commit()
    db.refresh(config)
    return config
Add helper: replace draft credential
def replace_llm_config_draft_credential(
    db: Session,
    actor: User,
    payload: LlmConfigDraftReplaceCredential,
) -> tuple[TeamLlmConfig, LlmConfigInspectResult]:
    ...

Behavior:

Require system admin.
Load config.
Re-run inspection using saved provider/base URL and new key.
Write new key to Vault.
Store new discovered models.
Clear model_name if it is no longer in returned models.
Set setup_status=pending_model_selection.
Set is_active=False.
Commit.
Return config + inspection.
Modify existing upsert_llm_config

Keep it for existing edit/full-form behavior, but make it setup-aware:

If creating through old upsert with model_name, create as ready.
If editing ready configs, preserve current behavior.
Do not use old upsert for the new first wizard step.
Reject setting setup_status=ready without model.
Do not allow is_active=True unless setup_status=ready.
Modify selectable config query

Current selectable query only filters active configs. Tighten it:

stmt = (
    select(TeamLlmConfig)
    .where(
        TeamLlmConfig.team_id == team.id,
        TeamLlmConfig.is_active.is_(True),
        TeamLlmConfig.setup_status == LlmConfigSetupStatus.ready,
        TeamLlmConfig.model_name.is_not(None),
    )
    .order_by(TeamLlmConfig.created_at.desc(), TeamLlmConfig.id.desc())
)

Also apply the same guard in set_team_llm_selection, so a caller cannot select a pending config by ID.

Modify delete behavior

Existing delete already deletes the config and then cleans up the Vault secret. Reuse it for:

Delete incomplete setup
Cancel setup

Keep the in-flight job guard for ready configs. Pending drafts should not have in-flight jobs, but the existing guard is safe.

API route changes

Add these JSON API routes:

POST /api/v1/llm-configs/drafts
POST /api/v1/llm-configs/{config_id}/finalize
POST /api/v1/llm-configs/{config_id}/replace-credential
DELETE /api/v1/llm-configs/{config_id}

The delete route already exists; reuse it for deleting incomplete setup.

Draft create route
@api.post("/llm-configs/drafts", response_model=LlmConfigDraftCreateResult)
def create_llm_config_draft_route(...):
    config, inspection = create_llm_config_draft_service(db, context.user, payload)
    return LlmConfigDraftCreateResult(
        config=llm_config_response(config),
        provider_display_name=inspection.provider_display_name,
        available_models=inspection.available_models,
        available_model_options=inspection.available_model_options,
        discovery_status=inspection.discovery_status,
        default_model_source=inspection.default_model_source,
        warnings=inspection.warnings,
        notes=inspection.notes,
    )
Finalize route
@api.post("/llm-configs/{config_id}/finalize", response_model=LlmConfigDetail)
def finalize_llm_config_draft_route(config_id: UUID, payload: LlmConfigFinalize, ...):
    payload = payload.model_copy(update={"config_id": config_id})
    config = finalize_llm_config_draft_service(db, context.user, payload)
    return llm_config_response(config)
Replace key route
@api.post("/llm-configs/{config_id}/replace-credential", response_model=LlmConfigDraftCreateResult)
def replace_llm_config_draft_credential_route(...):
    ...
Browser/admin route changes

Add equivalent form routes if admin UI is server-rendered:

POST /admin/llm-configs/drafts
POST /admin/llm-configs/{config_id}/finalize
POST /admin/llm-configs/{config_id}/replace-credential
POST /admin/llm-configs/{config_id}/delete
Draft route behavior

On submit:

Create draft.
Redirect to admin LLM tab with llm_config_id=<draft_id>.
Render model selection state.
Do not repopulate API key.
Finalize route behavior

On submit:

Finalize draft.
Redirect to provider list.
Show “Provider saved.”
Replace credential behavior

On click:

Show key-entry step for the existing draft/config.
After submit, rediscover models and return to model-selection step.
Presentation changes
Extend llm_config_response

Add:

setup_status=config.setup_status

Optionally:

provider_display_name=get_llm_provider_preset(config.provider_preset).display_name
Extend llm_form_defaults

Current llm_form_defaults() already passes provider presets and Bedrock regions into templates. Extend it with:

"setup_status": config.setup_status.value if config else "",
"has_secret": bool(config.vault_secret_ref) if config else False,
"is_setup_incomplete": config.setup_status is LlmConfigSetupStatus.pending_model_selection if config else False,
"show_credential_step": config is None or replace_credential_mode,
"show_model_step": config is not None and config.setup_status is LlmConfigSetupStatus.pending_model_selection,
"can_finalize": config is not None and config.setup_status is LlmConfigSetupStatus.pending_model_selection,
Provider list rendering

For each LLM config:

if setup_status == pending_model_selection:
    badge = "Setup incomplete"
    actions = Continue setup, Delete incomplete setup
elif is_active:
    badge = "Ready · available"
else:
    badge = "Ready · unavailable"
Admin template changes

Apply to both admin.html and admin2.html.

New state structure

The LLM setup panel should render one of three states.

State A: no selected config / new provider

Fields:

Team
Provider
Provider-specific endpoint fields
API key if needed
[Check API key and find models]

Hide:

Label
Model selection
Available for team selection
Save provider
State B: pending draft / model selection

Fields:

Provider summary
Credential: saved
Provider name
Model dropdown if available models exist
Manual model button/field if manual_required
Available for team selection
[Save provider]
[Replace API key]
[Delete incomplete setup]

Hide:

API key field
Check API key and find models button
State C: ready config edit

Fields:

Provider summary
Provider name
Default model
Available for team selection
[Save changes]
[Replace API key]
[Delete]

Do not require re-entering API key.

Button copy

Use exactly:

Check API key and find models
Save provider
Continue setup
Delete incomplete setup
Replace API key
Available for team selection
Setup incomplete
Ready · available
Ready · unavailable
Manual model UI

If discovery_status=manual_required and no available_models:

Initial display:

No compatible models were returned.
[Try again]
[Enter model manually]

After “Enter model manually”:

Model name
Provider name
Available for team selection
[Save provider]

No built-in model suggestions.

Validation rules
Draft creation
System admin only.
Team required.
Credential required unless provider does not require one.
Base URL required after preset defaults.
Remote base URLs must use HTTPS, except local/private/localish endpoints as currently allowed by schema validation.
For Bedrock HTTP, region must normalize through existing helper.
Store key in Vault.
Never return key.
Finalization
System admin only.
Config must belong to team.
Model required.
If available_models_json exists, model must be in that list.
If no models exist because discovery failed/zeroed, manual model is accepted and stored as [model_name].
Finalized config becomes setup_status=ready.
is_active comes from “Available for team selection.”
Finalization does not set team active LLM selection.
Selection
Team leaders/users cannot select pending providers.
Both list and set endpoints must enforce:
setup_status == ready
is_active == true
model_name is not null
Tests to add
Migration tests
- setup_status column exists on team_llm_configs
- existing configs with model_name are backfilled as ready
- existing configs without model_name are backfilled as pending_model_selection
- setup_status index exists if added
Service/API tests
- system admin can create LLM draft after successful discovery
- draft writes credential to Vault
- draft response has has_secret=true but does not expose key
- draft has setup_status=pending_model_selection
- draft has model_name=None
- draft is_active=false
- draft stores all discovered models
- draft is not returned from selectable LLM configs
- team leader cannot select draft config by ID
- finalize draft with discovered model sets setup_status=ready
- finalized active config appears in selectable list
- finalized inactive config does not appear in selectable list
- zero-model discovery creates draft with manual_required metadata
- failed discovery allows manual model finalize
- manual model finalize stores available_models_json=[manual_model]
- deleting incomplete setup deletes Vault secret
- replace credential re-runs discovery and keeps setup_status=pending_model_selection
- continue setup can render/use saved credential without re-entering key
Admin UI tests
- new provider setup shows provider/API key step only
- model selection hidden before draft exists
- Check API key and find models creates draft and redirects to model step
- API key field hidden after draft exists
- pending config card shows Setup incomplete
- pending config card has Continue setup and Delete incomplete setup
- Continue setup skips API key step
- Replace API key action is visible
- final save shows Save provider and creates Ready state
- ready active provider shows Ready · available
- ready inactive provider shows Ready · unavailable
- /admin and /admin2 have parity for the LLM flow
Suggested implementation sequence
Step 1 — schema and migration
Add LlmConfigSetupStatus.
Add setup_status to TeamLlmConfig.
Add migration.
Update migration tests.
Step 2 — response/request schemas
Add setup status to LlmConfigDetail.
Add draft create/finalize/replace credential schemas.
Update presentation response helpers.
Step 3 — service layer
Add create_llm_config_draft.
Add finalize_llm_config_draft.
Add replace_llm_config_draft_credential.
Tighten selectable query and selection setter.
Keep existing upsert_llm_config for full edit/backward-compatible paths.
Step 4 — API routes
Add draft/finalize/replace credential routes.
Reuse delete route for incomplete setup deletion.
Add API tests.
Step 5 — admin form routes
Add browser POST handlers for draft/finalize/replace.
Ensure redirects preserve team/tab/config selection.
Add form error handling.
Step 6 — templates
Update admin.html.
Update admin2.html.
Render LLM setup as a stateful wizard.
Remove hidden API-key carryover.
Show incomplete setup cards.
Step 7 — test and harden

Run:

pytest tests/test_api.py -q
pytest tests/test_admin_ui.py -q
pytest tests/test_migrations.py -q

Then:

pytest
Non-goals

Do not add in this PR:

Anthropic/Gemini/Azure/native Bedrock.
OpenAI Responses API migration.
Scheduled cleanup of incomplete drafts.
Personal user API keys.
Team-leader credential provisioning.
Curated built-in model fallback lists.
Final agent instruction

Implement a draft/finalize wizard for admin LLM provider setup. The first step should store credentials safely in Vault by creating a pending_model_selection config after “Check API key and find models.” The second step should let admins choose/edit the label, pick a default model, toggle availability, and save the provider as ready. Pending configs must be visible to system admins as “Setup incomplete,” but excluded from all team/user selectable provider flows. Never return or persist API keys in browser state after inspection.