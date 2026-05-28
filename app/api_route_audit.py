from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from fastapi.routing import APIRoute

from app.main import app
from app.services.csrf import CSRF_COOKIE_NAME, session_csrf_token
from app.services.auth import SESSION_COOKIE_NAME


PLACEHOLDER_UUID = "11111111-1111-1111-1111-111111111111"
PLACEHOLDER_UUID_2 = "22222222-2222-2222-2222-222222222222"


class AccessTier(str, Enum):
    public = "public"
    authenticated = "authenticated"
    full = "full"
    manager = "manager"
    system_admin = "system_admin"
    local_debug = "local_debug"


@dataclass(frozen=True)
class AuditCase:
    method: str
    path: str
    access_tier: AccessTier
    json_body: dict[str, Any] | None = None
    query_params: dict[str, Any] | None = None
    form_data: dict[str, Any] | None = None
    files: dict[str, tuple[str, bytes, str]] | None = None


@dataclass(frozen=True)
class AuditScenario:
    name: str
    session_cookie: str | None = None


@dataclass(frozen=True)
class AuditExpectation:
    status_code: int
    error_code: str


@dataclass(frozen=True)
class AuditObservation:
    scenario: str
    status_code: int
    error_code: str | None = None
    message: str | None = None
    ok: bool = True


@dataclass(frozen=True)
class AuditResult:
    case: AuditCase
    observations: list[AuditObservation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(observation.ok for observation in self.observations)


def _json(**values: Any) -> dict[str, Any]:
    return values


def _file(name: str = "sample.wav") -> dict[str, tuple[str, bytes, str]]:
    return {"audio": (name, b"RIFF\x00\x00\x00\x00WAVEfmt ", "audio/wav")}


ALL_AUDIT_CASES: tuple[AuditCase, ...] = (
    AuditCase("POST", "/api/v1/auth/login", AccessTier.public, json_body=_json(email="nobody@example.com", password="password-1")),
    AuditCase("POST", "/api/v1/auth/logout", AccessTier.public),
    AuditCase("POST", "/api/v1/auth/password-reset/request", AccessTier.public, json_body=_json(email="nobody@example.com")),
    AuditCase("POST", "/api/v1/auth/password-reset/confirm", AccessTier.public, json_body=_json(token="invalid-token-value", new_password="password-2")),
    AuditCase("POST", "/api/v1/auth/account-activation/confirm", AccessTier.public, json_body=_json(token="invalid-token-value", new_password="password-2")),
    AuditCase("POST", "/api/v1/auth/mfa/totp", AccessTier.authenticated, json_body=_json(code="123456", remember_device=False)),
    AuditCase("GET", "/api/v1/auth/me", AccessTier.authenticated),
    AuditCase("GET", "/api/v1/auth/trusted-device", AccessTier.authenticated),
    AuditCase(
        "POST",
        "/api/v1/account-requests",
        AccessTier.public,
        json_body=_json(
            requested_name="Security Audit Request",
            requested_email="audit.request@example.com",
            requested_team_name="Security Team",
            request_details="Route audit probe",
        ),
    ),
    AuditCase("GET", "/api/v1/account-requests", AccessTier.manager),
    AuditCase(
        "POST",
        f"/api/v1/account-requests/{PLACEHOLDER_UUID}/approve",
        AccessTier.manager,
        json_body=_json(team_role="user", temporary_password="password-1", review_notes="approved"),
    ),
    AuditCase(
        "POST",
        f"/api/v1/account-requests/{PLACEHOLDER_UUID}/reject",
        AccessTier.manager,
        json_body=_json(review_notes="rejected"),
    ),
    AuditCase("POST", "/api/v1/onboarding/password", AccessTier.authenticated, json_body=_json(new_password="password-2")),
    AuditCase("POST", "/api/v1/onboarding/totp/start", AccessTier.authenticated),
    AuditCase("POST", "/api/v1/onboarding/totp/verify", AccessTier.authenticated, json_body=_json(code="123456")),
    AuditCase("POST", "/api/v1/onboarding/recovery-codes", AccessTier.authenticated),
    AuditCase("POST", "/api/v1/onboarding/skip-recovery-codes", AccessTier.authenticated),
    AuditCase("POST", "/api/v1/teams", AccessTier.system_admin, json_body=_json(name="Security Team", status="active", default_retention_days=30)),
    AuditCase("GET", "/api/v1/teams", AccessTier.system_admin),
    AuditCase(
        "POST",
        "/api/v1/users",
        AccessTier.manager,
        json_body=_json(
            email="new.user@example.com",
            temporary_password="password-1",
            team_id=PLACEHOLDER_UUID,
            team_role="user",
            is_system_admin=False,
            status="active",
            mfa_required=True,
        ),
    ),
    AuditCase("POST", f"/api/v1/users/{PLACEHOLDER_UUID}/send-activation", AccessTier.manager),
    AuditCase("POST", f"/api/v1/users/{PLACEHOLDER_UUID}/recover-password", AccessTier.manager),
    AuditCase("POST", f"/api/v1/users/{PLACEHOLDER_UUID}/send-password-reset", AccessTier.manager, json_body=_json(reason="route audit")),
    AuditCase("POST", f"/api/v1/users/{PLACEHOLDER_UUID}/reset-mfa", AccessTier.manager),
    AuditCase("POST", f"/api/v1/users/{PLACEHOLDER_UUID}/recover-account", AccessTier.manager),
    AuditCase("POST", f"/api/v1/users/{PLACEHOLDER_UUID}/send-account-recovery", AccessTier.manager, json_body=_json(reason="route audit")),
    AuditCase(
        "POST",
        f"/api/v1/users/{PLACEHOLDER_UUID}/break-glass-password-reset",
        AccessTier.manager,
        json_body=_json(mfa_code="123456", reason="route audit", confirm_email_unavailable=True),
    ),
    AuditCase(
        "POST",
        f"/api/v1/users/{PLACEHOLDER_UUID}/break-glass-account-recovery",
        AccessTier.manager,
        json_body=_json(mfa_code="123456", reason="route audit", confirm_email_unavailable=True),
    ),
    AuditCase("GET", "/api/v1/users", AccessTier.manager),
    AuditCase("GET", "/api/v1/stt-configs", AccessTier.system_admin),
    AuditCase("GET", f"/api/v1/stt-configs/{PLACEHOLDER_UUID}", AccessTier.system_admin),
    AuditCase(
        "POST",
        "/api/v1/stt-configs/inspect",
        AccessTier.system_admin,
        json_body=_json(team_id=PLACEHOLDER_UUID, adapter_kind="openai_compatible_rest", base_url="http://127.0.0.1:9000", bearer_token="secret"),
    ),
    AuditCase("POST", f"/api/v1/stt-configs/{PLACEHOLDER_UUID}/inspect", AccessTier.system_admin),
    AuditCase(
        "POST",
        "/api/v1/stt-configs/drafts",
        AccessTier.system_admin,
        json_body=_json(
            team_id=PLACEHOLDER_UUID,
            provider_preset="openai",
            label="Audit STT Draft",
            base_url="https://api.openai.com/v1",
            bearer_token="secret",
        ),
    ),
    AuditCase(
        "POST",
        f"/api/v1/stt-configs/{PLACEHOLDER_UUID}/finalize",
        AccessTier.system_admin,
        json_body=_json(team_id=PLACEHOLDER_UUID, label="Audit STT", model_name="whisper-1", is_active=True),
    ),
    AuditCase(
        "POST",
        f"/api/v1/stt-configs/{PLACEHOLDER_UUID}/replace-credential",
        AccessTier.system_admin,
        json_body=_json(team_id=PLACEHOLDER_UUID, bearer_token="secret"),
    ),
    AuditCase(
        "POST",
        "/api/v1/stt-configs",
        AccessTier.system_admin,
        json_body=_json(
            team_id=PLACEHOLDER_UUID,
            label="Audit STT",
            adapter_kind="openai_compatible_rest",
            base_url="http://127.0.0.1:9000",
            bearer_token="secret",
            model_name="whisper-1",
            is_active=True,
        ),
    ),
    AuditCase("DELETE", f"/api/v1/stt-configs/{PLACEHOLDER_UUID}", AccessTier.system_admin),
    AuditCase("GET", "/api/v1/stt-selection", AccessTier.manager),
    AuditCase("GET", "/api/v1/stt-selection/options", AccessTier.manager),
    AuditCase("POST", "/api/v1/stt-selection", AccessTier.manager, json_body=_json(stt_config_id=PLACEHOLDER_UUID)),
    AuditCase("DELETE", "/api/v1/stt-selection", AccessTier.manager),
    AuditCase("GET", "/api/v1/llm-configs", AccessTier.system_admin),
    AuditCase(
        "POST",
        "/api/v1/llm-configs/inspect",
        AccessTier.system_admin,
        json_body=_json(team_id=PLACEHOLDER_UUID, adapter_kind="ollama_chat", base_url="http://127.0.0.1:11434"),
    ),
    AuditCase("POST", f"/api/v1/llm-configs/{PLACEHOLDER_UUID}/inspect", AccessTier.system_admin),
    AuditCase(
        "POST",
        "/api/v1/llm-configs/drafts",
        AccessTier.system_admin,
        json_body=_json(
            team_id=PLACEHOLDER_UUID,
            provider_preset="openai",
            label="Audit LLM Draft",
            base_url="https://api.openai.com/v1",
            bearer_token="secret",
        ),
    ),
    AuditCase(
        "POST",
        f"/api/v1/llm-configs/{PLACEHOLDER_UUID}/finalize",
        AccessTier.system_admin,
        json_body=_json(team_id=PLACEHOLDER_UUID, config_id=PLACEHOLDER_UUID, label="Audit LLM", model_name="gpt-4o-mini", is_active=True),
    ),
    AuditCase(
        "POST",
        f"/api/v1/llm-configs/{PLACEHOLDER_UUID}/replace-credential",
        AccessTier.system_admin,
        json_body=_json(team_id=PLACEHOLDER_UUID, config_id=PLACEHOLDER_UUID, bearer_token="secret"),
    ),
    AuditCase(
        "POST",
        "/api/v1/llm-configs",
        AccessTier.system_admin,
        json_body=_json(
            team_id=PLACEHOLDER_UUID,
            label="Audit LLM",
            adapter_kind="ollama_chat",
            base_url="http://127.0.0.1:11434",
            model_name="llama3.2",
            is_active=True,
        ),
    ),
    AuditCase("DELETE", f"/api/v1/llm-configs/{PLACEHOLDER_UUID}", AccessTier.system_admin),
    AuditCase("GET", "/api/v1/llm-selection", AccessTier.manager),
    AuditCase("GET", "/api/v1/llm-selection/options", AccessTier.manager),
    AuditCase(
        "POST",
        "/api/v1/llm-selection",
        AccessTier.manager,
        json_body=_json(llm_config_id=PLACEHOLDER_UUID, allowed_models_json=["llama3.2"], model_name_override="llama3.2"),
    ),
    AuditCase("DELETE", "/api/v1/llm-selection", AccessTier.manager),
    AuditCase("GET", "/api/v1/llm-preference", AccessTier.full),
    AuditCase("POST", "/api/v1/llm-preference", AccessTier.full, json_body=_json(preferred_model_name="gpt-4o-mini")),
    AuditCase("DELETE", "/api/v1/llm-preference", AccessTier.full),
    AuditCase("GET", "/api/v1/deidentification-providers", AccessTier.system_admin),
    AuditCase(
        "POST",
        "/api/v1/deidentification-providers",
        AccessTier.system_admin,
        json_body=_json(
            label="Audit De-id",
            adapter_kind="generic_rest",
            base_url="http://127.0.0.1:9300",
            detect_path="/detect",
            auth_mode="none",
        ),
    ),
    AuditCase(
        "POST",
        "/api/v1/deidentification-providers/inspect",
        AccessTier.system_admin,
        json_body=_json(
            label="Audit De-id Inspect",
            adapter_kind="generic_rest",
            base_url="http://127.0.0.1:9300",
            detect_path="/detect",
            auth_mode="none",
        ),
    ),
    AuditCase("DELETE", f"/api/v1/deidentification-providers/{PLACEHOLDER_UUID}", AccessTier.system_admin),
    AuditCase("GET", "/api/v1/deidentification-provider-assignments", AccessTier.system_admin, query_params=_json(team_id=PLACEHOLDER_UUID)),
    AuditCase(
        "POST",
        "/api/v1/deidentification-provider-assignments",
        AccessTier.system_admin,
        json_body=_json(team_id=PLACEHOLDER_UUID, provider_id=PLACEHOLDER_UUID_2),
    ),
    AuditCase(
        "DELETE",
        "/api/v1/deidentification-provider-assignments",
        AccessTier.system_admin,
        query_params=_json(team_id=PLACEHOLDER_UUID, provider_id=PLACEHOLDER_UUID_2),
    ),
    AuditCase("GET", "/api/v1/deidentification-selection", AccessTier.manager),
    AuditCase("GET", "/api/v1/deidentification-selection/options", AccessTier.manager),
    AuditCase("POST", "/api/v1/deidentification-selection", AccessTier.manager, json_body=_json(provider_id=PLACEHOLDER_UUID)),
    AuditCase("DELETE", "/api/v1/deidentification-selection", AccessTier.manager),
    AuditCase("GET", "/api/v1/clinical-nlp-selection", AccessTier.manager),
    AuditCase("GET", "/api/v1/clinical-nlp-selection/options", AccessTier.manager),
    AuditCase("POST", "/api/v1/clinical-nlp-selection", AccessTier.manager, json_body=_json(provider_id=PLACEHOLDER_UUID)),
    AuditCase("DELETE", "/api/v1/clinical-nlp-selection", AccessTier.manager),
    AuditCase("GET", "/api/v1/app-preferences", AccessTier.full),
    AuditCase("POST", "/api/v1/app-preferences", AccessTier.full, json_body=_json(preferred_transcribe_tab="output")),
    AuditCase("DELETE", "/api/v1/app-preferences", AccessTier.full),
    AuditCase("GET", "/api/v1/templates/available", AccessTier.full),
    AuditCase("GET", "/api/v1/templates/team", AccessTier.manager),
    AuditCase(
        "POST",
        "/api/v1/templates/team",
        AccessTier.manager,
        json_body=_json(scope="team", name="Team Template", prompt_text="Write a team note", mode="freeform", is_active=True),
    ),
    AuditCase("DELETE", f"/api/v1/templates/team/{PLACEHOLDER_UUID}", AccessTier.manager),
    AuditCase("GET", "/api/v1/templates/personal", AccessTier.full),
    AuditCase(
        "POST",
        "/api/v1/templates/personal",
        AccessTier.full,
        json_body=_json(scope="user", name="Personal Template", prompt_text="Write a personal note", mode="freeform", is_active=True),
    ),
    AuditCase("DELETE", f"/api/v1/templates/personal/{PLACEHOLDER_UUID}", AccessTier.full),
    AuditCase("GET", "/api/v1/quick-actions/available", AccessTier.full),
    AuditCase("GET", "/api/v1/quick-actions/team", AccessTier.manager),
    AuditCase(
        "POST",
        "/api/v1/quick-actions/team",
        AccessTier.manager,
        json_body=_json(scope="team", name="Team Quick Action", prompt_text="Create a team follow-up", is_active=True),
    ),
    AuditCase("DELETE", f"/api/v1/quick-actions/team/{PLACEHOLDER_UUID}", AccessTier.manager),
    AuditCase("GET", "/api/v1/quick-actions/personal", AccessTier.full),
    AuditCase(
        "POST",
        "/api/v1/quick-actions/personal",
        AccessTier.full,
        json_body=_json(scope="user", name="Personal Quick Action", prompt_text="Create a personal follow-up", is_active=True),
    ),
    AuditCase("DELETE", f"/api/v1/quick-actions/personal/{PLACEHOLDER_UUID}", AccessTier.full),
    AuditCase("GET", "/api/v1/smart-phrases/available", AccessTier.full),
    AuditCase("GET", "/api/v1/smart-phrases/personal", AccessTier.full),
    AuditCase(
        "POST",
        "/api/v1/smart-phrases/personal",
        AccessTier.full,
        json_body=_json(trigger="AUDIT", expansion_text="Audit expansion"),
    ),
    AuditCase(
        "PATCH",
        f"/api/v1/smart-phrases/personal/{PLACEHOLDER_UUID}",
        AccessTier.full,
        json_body=_json(description="Audit update"),
    ),
    AuditCase("DELETE", f"/api/v1/smart-phrases/personal/{PLACEHOLDER_UUID}", AccessTier.full),
    AuditCase("POST", f"/api/v1/smart-phrases/personal/{PLACEHOLDER_UUID}/used", AccessTier.full),
    AuditCase("POST", f"/api/v1/users/{PLACEHOLDER_UUID}/suspend", AccessTier.manager),
    AuditCase("POST", f"/api/v1/users/{PLACEHOLDER_UUID}/reactivate", AccessTier.manager),
    AuditCase("DELETE", f"/api/v1/users/{PLACEHOLDER_UUID}", AccessTier.manager),
    AuditCase(
        "POST",
        "/api/v1/transcripts",
        AccessTier.full,
        json_body=_json(owner_user_id=PLACEHOLDER_UUID, team_id=PLACEHOLDER_UUID_2, title="Audit Transcript"),
    ),
    AuditCase("POST", "/api/v1/transcripts/start", AccessTier.full, json_body=_json(title="Audit Transcript")),
    AuditCase("POST", f"/api/v1/transcripts/{PLACEHOLDER_UUID}/commit", AccessTier.full, json_body=_json(text_encrypted="ciphertext")),
    AuditCase("POST", f"/api/v1/transcripts/{PLACEHOLDER_UUID}/finalize-live-capture", AccessTier.full),
    AuditCase("PATCH", f"/api/v1/transcripts/{PLACEHOLDER_UUID}", AccessTier.full, json_body=_json(title="Updated Transcript")),
    AuditCase("DELETE", f"/api/v1/transcripts/{PLACEHOLDER_UUID}", AccessTier.full),
    AuditCase(
        "POST",
        f"/api/v1/transcripts/{PLACEHOLDER_UUID}/manual-pii",
        AccessTier.full,
        json_body=_json(entity_type="PERSON", value="Audit Patient", occurrence_count=1),
    ),
    AuditCase("DELETE", f"/api/v1/transcripts/{PLACEHOLDER_UUID}/manual-pii/{PLACEHOLDER_UUID_2}", AccessTier.full),
    AuditCase("POST", f"/api/v1/transcripts/{PLACEHOLDER_UUID}/pii-entities/reveal", AccessTier.full),
    AuditCase(
        "POST",
        f"/api/v1/transcripts/{PLACEHOLDER_UUID}/audio-chunks",
        AccessTier.full,
        form_data={"chunk_sequence_no": "1", "declared_duration_seconds": "1.0"},
        files=_file("chunk.wav"),
    ),
    AuditCase(
        "POST",
        f"/api/v1/transcripts/{PLACEHOLDER_UUID}/audio-file",
        AccessTier.full,
        files=_file("audio.wav"),
    ),
    AuditCase("POST", f"/api/v1/transcripts/{PLACEHOLDER_UUID}/retry-audio-file", AccessTier.full),
    AuditCase("GET", f"/api/v1/transcripts/{PLACEHOLDER_UUID}", AccessTier.full),
    AuditCase("GET", f"/api/v1/transcripts/{PLACEHOLDER_UUID}/post-consultation-dictation", AccessTier.full),
    AuditCase(
        "PATCH",
        f"/api/v1/transcripts/{PLACEHOLDER_UUID}/post-consultation-dictation",
        AccessTier.full,
        json_body=_json(combined_text="Edited dictation"),
    ),
    AuditCase(
        "POST",
        f"/api/v1/transcripts/{PLACEHOLDER_UUID}/post-consultation-dictation/audio-file",
        AccessTier.full,
        files=_file("dictation.wav"),
    ),
    AuditCase(
        "POST",
        f"/api/v1/transcripts/{PLACEHOLDER_UUID}/post-consultation-dictation/preview-audio-file",
        AccessTier.full,
        files=_file("dictation.wav"),
    ),
    AuditCase(
        "POST",
        f"/api/v1/transcripts/{PLACEHOLDER_UUID}/quick-action-context/preview-audio-file",
        AccessTier.full,
        files=_file("context.wav"),
    ),
    AuditCase("GET", "/api/v1/transcribe/workspace", AccessTier.full),
    AuditCase("POST", "/api/v1/transcribe/stt-health/recheck", AccessTier.full),
    AuditCase("GET", "/api/v1/transcribe/workspace/stream", AccessTier.full),
    AuditCase("GET", f"/api/v1/transcripts/{PLACEHOLDER_UUID}/working-note", AccessTier.full),
    AuditCase(
        "PATCH",
        f"/api/v1/transcripts/{PLACEHOLDER_UUID}/working-note",
        AccessTier.full,
        json_body=_json(mode="freeform", freeform_text="Working note"),
    ),
    AuditCase("DELETE", f"/api/v1/transcripts/{PLACEHOLDER_UUID}/working-note", AccessTier.full),
    AuditCase("GET", f"/api/v1/transcripts/{PLACEHOLDER_UUID}/generated-documents", AccessTier.full),
    AuditCase("GET", f"/api/v1/generated-documents/{PLACEHOLDER_UUID}/redaction-debug", AccessTier.local_debug),
    AuditCase(
        "PATCH",
        f"/api/v1/generated-documents/{PLACEHOLDER_UUID}",
        AccessTier.full,
        json_body=_json(expected_updated_at="2026-01-01T00:00:00Z", edited_output_text="Updated"),
    ),
    AuditCase("DELETE", f"/api/v1/generated-documents/{PLACEHOLDER_UUID}", AccessTier.full),
    AuditCase(
        "POST",
        f"/api/v1/transcripts/{PLACEHOLDER_UUID}/generate-output",
        AccessTier.full,
        json_body=_json(template_id=PLACEHOLDER_UUID_2),
    ),
    AuditCase(
        "POST",
        f"/api/v1/transcripts/{PLACEHOLDER_UUID}/generate-followup",
        AccessTier.full,
        json_body=_json(prompt_text="Generate a follow-up"),
    ),
    AuditCase(
        "POST",
        f"/api/v1/transcripts/{PLACEHOLDER_UUID}/run-quick-action",
        AccessTier.full,
        json_body=_json(quick_action_id=PLACEHOLDER_UUID_2),
    ),
    AuditCase("GET", "/api/v1/transcripts", AccessTier.full),
)


NEGATIVE_EXPECTATIONS: dict[AccessTier, dict[str, AuditExpectation]] = {
    AccessTier.public: {},
    AccessTier.authenticated: {
        "anonymous": AuditExpectation(401, "unauthorized"),
        "invalid_cookie": AuditExpectation(401, "unauthorized"),
    },
    AccessTier.full: {
        "anonymous": AuditExpectation(401, "unauthorized"),
        "invalid_cookie": AuditExpectation(401, "unauthorized"),
        "onboarding": AuditExpectation(403, "onboarding_incomplete"),
        "pending_mfa": AuditExpectation(403, "mfa_required"),
    },
    AccessTier.manager: {
        "anonymous": AuditExpectation(401, "unauthorized"),
        "invalid_cookie": AuditExpectation(401, "unauthorized"),
        "onboarding": AuditExpectation(403, "onboarding_incomplete"),
        "pending_mfa": AuditExpectation(403, "mfa_required"),
        "full_user": AuditExpectation(403, "forbidden"),
    },
    AccessTier.system_admin: {
        "anonymous": AuditExpectation(401, "unauthorized"),
        "invalid_cookie": AuditExpectation(401, "unauthorized"),
        "onboarding": AuditExpectation(403, "onboarding_incomplete"),
        "pending_mfa": AuditExpectation(403, "mfa_required"),
        "full_user": AuditExpectation(403, "forbidden"),
        "leader": AuditExpectation(403, "forbidden"),
    },
    AccessTier.local_debug: {
        "anonymous": AuditExpectation(401, "unauthorized"),
        "invalid_cookie": AuditExpectation(401, "unauthorized"),
        "onboarding": AuditExpectation(403, "onboarding_incomplete"),
        "pending_mfa": AuditExpectation(403, "mfa_required"),
        "full_user": AuditExpectation(403, "forbidden"),
        "leader": AuditExpectation(403, "forbidden"),
    },
}


def route_inventory() -> set[tuple[str, str]]:
    inventory: set[tuple[str, str]] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/v1"):
            continue
        for method in route.methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            inventory.add((method, route.path))
    return inventory


def manifest_inventory() -> set[tuple[str, str]]:
    normalized_paths: set[tuple[str, str]] = set()
    for case in ALL_AUDIT_CASES:
        path = case.path.replace(PLACEHOLDER_UUID, "{id}").replace(PLACEHOLDER_UUID_2, "{id}")
        normalized_paths.add((case.method, path))
    return normalized_paths


def missing_route_specs() -> set[tuple[str, str]]:
    normalized_actual = {
        (
            method,
            path.replace("{request_id}", "{id}")
            .replace("{config_id}", "{id}")
            .replace("{provider_id}", "{id}")
            .replace("{template_id}", "{id}")
            .replace("{quick_action_id}", "{id}")
            .replace("{smart_phrase_id}", "{id}")
            .replace("{user_id}", "{id}")
            .replace("{transcript_id}", "{id}")
            .replace("{entity_id}", "{id}")
            .replace("{generated_document_id}", "{id}"),
        )
        for method, path in route_inventory()
    }
    return normalized_actual - manifest_inventory()


def audit_cases() -> tuple[AuditCase, ...]:
    return ALL_AUDIT_CASES


def send_case(client: Any, case: AuditCase, scenario: AuditScenario):
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    if scenario.session_cookie is not None:
        cookies[SESSION_COOKIE_NAME] = scenario.session_cookie
        if case.method in {"POST", "PUT", "PATCH", "DELETE"}:
            csrf_token = session_csrf_token(scenario.session_cookie)
            cookies[CSRF_COOKIE_NAME] = csrf_token
            headers["X-CSRF-Token"] = csrf_token
            headers["Origin"] = "http://testserver"
    if cookies:
        headers["Cookie"] = "; ".join(f"{name}={value}" for name, value in cookies.items())

    kwargs: dict[str, Any] = {"params": case.query_params, "headers": headers}
    if case.json_body is not None:
        kwargs["json"] = case.json_body
    if case.form_data is not None:
        kwargs["data"] = case.form_data
    if case.files is not None:
        kwargs["files"] = case.files
    return client.request(case.method, case.path, **kwargs)


def extract_error(response: Any) -> tuple[str | None, str | None]:
    try:
        payload = response.json()
    except Exception:
        return None, None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None, None
    return error.get("code"), error.get("message")


def run_negative_audit(client: Any, scenarios: dict[str, AuditScenario]) -> list[AuditResult]:
    results: list[AuditResult] = []
    for case in audit_cases():
        observations: list[AuditObservation] = []
        for scenario_name, expectation in NEGATIVE_EXPECTATIONS[case.access_tier].items():
            response = send_case(client, case, scenarios[scenario_name])
            error_code, message = extract_error(response)
            ok = response.status_code == expectation.status_code and error_code == expectation.error_code
            observations.append(
                AuditObservation(
                    scenario=scenario_name,
                    status_code=response.status_code,
                    error_code=error_code,
                    message=message,
                    ok=ok,
                )
            )
        results.append(AuditResult(case=case, observations=observations))
    return results
