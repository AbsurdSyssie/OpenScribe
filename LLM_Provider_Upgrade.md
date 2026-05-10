Backend fix

Add a clearer inspection error classification.

Right now the low-level OpenAI-compatible model discovery wraps every exception as:

raise AppError(502, "llm_inspection_failed", "Could not load available models")

Change it to preserve auth failures:

from openai import APIStatusError, AuthenticationError, PermissionDeniedError

def _list_openai_compatible_models(*, api_key: str, base_url: str) -> list[str]:
    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        models_page = client.models.list()
    except (AuthenticationError, PermissionDeniedError) as exc:
        raise AppError(
            401,
            "llm_invalid_credential",
            "The API key was rejected by the provider.",
            {"provider_status": getattr(exc, "status_code", None)},
        ) from exc
    except APIStatusError as exc:
        if exc.status_code in {401, 403}:
            raise AppError(
                401,
                "llm_invalid_credential",
                "The API key was rejected by the provider.",
                {"provider_status": exc.status_code},
            ) from exc
        raise AppError(
            502,
            "llm_inspection_failed",
            "Could not load available models from the provider.",
            {"provider_status": exc.status_code},
        ) from exc
    except Exception as exc:
        raise AppError(
            502,
            "llm_inspection_failed",
            "Could not load available models from the provider.",
        ) from exc

    ...

Then in inspect_llm_contract() / create_llm_config_draft():

try:
    inspection = inspect_llm_contract(...)
except AppError as exc:
    if exc.code == "llm_invalid_credential":
        raise
    ...

Do not convert llm_invalid_credential into manual_required.

UI fix

If key is invalid, show:

The API key was rejected by the provider. Check the key and try again.

Keep the admin on the first step:

Provider
API key
[Check API key and find models]

Do not show model selection. Do not create a draft.

Issue 2: failed discovery saved as “Ready · unavailable”

This should not happen automatically after a bad key.

Correct states
State	Meaning
pending_model_selection	Credential/setup incomplete; not selectable
ready + is_active=true	Ready · available
ready + is_active=false	Ready · unavailable

If the key is bad, the config should not reach ready.

Fix

For auth failure:

No config created
No Vault secret written
No Ready state

For non-auth discovery failure where manual entry is allowed:

Draft created only after explicit manual path
setup_status = pending_model_selection
is_active = false

Only final save should set:

setup_status = LlmConfigSetupStatus.ready

If manual model save is allowed after a non-auth discovery failure, the UI must show a warning above the final save:

Models could not be discovered. You can save this model manually, but generation may fail if the model name or endpoint is wrong.
Issue 3: user-entered label overwritten by default label
Current problem

create_llm_config_draft() auto-generates the label:

label=default_llm_config_label(...)

That is fine only if the user has not supplied a label. But your intended UX now includes an admin-entered label during adding time, so the backend must preserve it.

Schema fix

Add optional label to draft creation:

class LlmConfigDraftCreate(BaseModel):
    team_id: UUID
    provider_preset: LlmProviderPreset = LlmProviderPreset.openai
    label: str | None = Field(default=None, min_length=1, max_length=255)
    base_url: str = Field(default="", max_length=2048)
    bearer_token: str | None = Field(default=None, min_length=1)
    bedrock_region: str | None = Field(default=None, max_length=64)
Service fix

Use admin label if supplied:

raw_label = (payload.label or "").strip()
label = raw_label or default_llm_config_label(
    provider_display_name=inspection.provider_display_name,
    team_name=team.name,
)

Then save:

label=label

On finalize, keep allowing label edit:

config.label = payload.label.strip()

The draft and finalize routes should both validate uniqueness.

Issue 4: label names are not unique

You do not want duplicate LLM config labels per team. Add a DB-level uniqueness rule and service-level friendly error.

Data rule

Recommended uniqueness:

A team cannot have two LLM configs with the same label, case-insensitively, ignoring surrounding whitespace.

Examples that should conflict:

OpenRouter
openrouter
 OpenRouter 
Migration

Add a normalized unique index:

op.create_index(
    "uq_team_llm_configs_team_label_lower",
    "team_llm_configs",
    ["team_id"],
    unique=True,
    postgresql_where=None,
    postgresql_ops={},
)

For expression index, use:

op.create_index(
    "uq_team_llm_configs_team_label_lower",
    "team_llm_configs",
    ["team_id", sa.text("lower(trim(label))")],
    unique=True,
)

Depending on the project’s Alembic conventions, this may need raw SQL:

op.execute(
    """
    CREATE UNIQUE INDEX uq_team_llm_configs_team_label_lower
    ON team_llm_configs (team_id, lower(trim(label)))
    """
)

Downgrade:

op.execute("DROP INDEX IF EXISTS uq_team_llm_configs_team_label_lower")
Existing duplicate backfill

Before creating the index, dedupe existing rows.

Use a deterministic rename like:

OpenRouter
OpenRouter copy 2
OpenRouter copy 3

Pseudo-SQL approach:

WITH ranked AS (
    SELECT
        id,
        label,
        ROW_NUMBER() OVER (
            PARTITION BY team_id, lower(trim(label))
            ORDER BY created_at, id
        ) AS rn
    FROM team_llm_configs
)
UPDATE team_llm_configs c
SET label = c.label || ' copy ' || ranked.rn
FROM ranked
WHERE c.id = ranked.id
AND ranked.rn > 1;

Better Python migration may be safer if you want to avoid second-order collisions like an existing OpenRouter copy 2.

Service validation

Add helper:

def _ensure_unique_llm_config_label(
    db: Session,
    *,
    team_id: UUID,
    label: str,
    current_config_id: UUID | None = None,
) -> None:
    normalized = label.strip().lower()
    stmt = select(TeamLlmConfig.id).where(
        TeamLlmConfig.team_id == team_id,
        func.lower(func.trim(TeamLlmConfig.label)) == normalized,
    )
    if current_config_id is not None:
        stmt = stmt.where(TeamLlmConfig.id != current_config_id)

    if db.scalar(stmt.limit(1)) is not None:
        raise AppError(
            409,
            "conflict",
            "An LLM provider with this name already exists for this team.",
            {"field": "label"},
        )

Call it from:

create_llm_config_draft
finalize_llm_config_draft
upsert_llm_config

Also catch DB IntegrityError as a final guard.


1. Invalid API key handling
Distinguish invalid credentials from generic discovery failure.
For OpenAI-compatible providers, map provider 401/403/auth exceptions to:
code = llm_invalid_credential
status = 401
message = The API key was rejected by the provider.
Do not convert llm_invalid_credential to manual_required.
Do not create a draft config for invalid credentials.
Do not write invalid credentials to Vault.
Admin UI should show the key error and stay on the credential step.
2. Manual model warning

For non-auth discovery failures or zero-model discovery:

Show clear warning text before manual model entry.
Make manual entry an explicit user action.
Keep draft as pending_model_selection until final save.
Only final save may set setup_status=ready.

Suggested copy:

OpenScribe could not discover models from this provider. You can enter a model name manually, but generation may fail if the endpoint, key, or model name is wrong.
3. Preserve admin-entered label
Add optional label to LlmConfigDraftCreate.
Include label in the admin draft form.
In create_llm_config_draft, use the submitted label if present.
Fall back to the default generated label only when no label is supplied.
Continue allowing label edit on final save.
4. Enforce unique labels per team
Add case-insensitive, trim-normalized uniqueness for LLM config labels per team.
Dedupe existing rows in the migration before adding the index.
Add service-level validation in draft create, finalize, and legacy upsert.
Return friendly error:
An LLM provider with this name already exists for this team.
5. Tests to add
API/service tests
- invalid provider API key returns llm_invalid_credential
- invalid key does not create TeamLlmConfig
- invalid key does not write Vault secret
- failed non-auth discovery still allows explicit manual model flow
- draft create preserves supplied label
- draft create falls back to generated label when label omitted
- duplicate label in same team is rejected on draft create
- duplicate label in same team is rejected on finalize rename
- same label in different teams is allowed
- label uniqueness is case-insensitive and trim-normalized
Migration tests
- duplicate existing LLM config labels are deduped before unique index creation
- unique normalized team/label index exists
Admin UI tests
- bad API key shows API-key error, not manual model step
- bad API key does not show Ready · unavailable
- manual model step shows warning when discovery fails for non-auth reason
- label entered during add flow remains visible after draft creation
- duplicate label shows friendly validation error
Expected final behavior

Bad API key:

Provider + API key step
→ Check API key and find models
→ “The API key was rejected by the provider.”
→ no draft
→ no Ready state

Valid key:

Provider + API key + optional label
→ Check API key and find models
→ draft created
→ key saved to Vault
→ label preserved
→ model selection
→ Save provider
→ Ready · available/unavailable

Duplicate label:

Save/draft attempt
→ “An LLM provider with this name already exists for this team.”
→ no duplicate provider saved