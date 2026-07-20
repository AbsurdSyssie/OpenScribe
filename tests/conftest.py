import os
import fcntl
import secrets
from collections.abc import Generator
from typing import Callable
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.models import (
    AccountRequest,
    AccountRequestStatus,
    DefaultPromptTemplate,
    DefaultPromptTemplateVersion,
    DefaultQuickAction,
    DefaultQuickActionVersion,
    DeidentificationAdapterKind,
    DeidentificationAuthMode,
    DeidentificationProvider,
    GeneratedDocument,
    GeneratedDocumentGeneratorType,
    GeneratedDocumentStatus,
    LlmAdapterKind,
    LlmAuthMode,
    PromptTemplate,
    PromptTemplateVersion,
    QuickAction,
    QuickActionVersion,
    RedactionEntity,
    RedactionRun,
    RedactionRunStatus,
    SttAdapterKind,
    SttAuthMode,
    SttSelectionPurpose,
    Team,
    TeamClinicalNlpSelection,
    TeamDeidentificationProviderAssignment,
    TeamDeidentificationSelection,
    TeamLlmConfig,
    TeamLlmSelection,
    TeamRole,
    TeamStatus,
    TeamSttConfig,
    TeamSttSelection,
    Transcript,
    TranscriptVersion,
    User,
    UserAppPreference,
    UserLlmPreference,
    UserOnboardingState,
    UserStatus,
    TemplateMode,
    TemplateScope,
)
from app.normalization import normalize_email, normalize_team_name_key
from app.services.admin import hash_password
from app.services.content_crypto import decrypt_text_for_owner, encrypt_text_for_owner
from tests.db_utils import (
    ensure_database_exists,
    ensure_safe_test_database_url,
    ensure_safe_test_rate_limit_storage_url,
)

from app.db import Base, get_db
from app.services.auth import SESSION_COOKIE_NAME, TRUSTED_DEVICE_COOKIE_NAME
from app.services.llm_presets import infer_llm_provider_preset


TEST_RATE_LIMIT_STORAGE_URL = ensure_safe_test_rate_limit_storage_url()
os.environ.setdefault("APP_ENV", "test")
os.environ["RATE_LIMIT_STORAGE_URL"] = TEST_RATE_LIMIT_STORAGE_URL

from app.main import CSRF_COOKIE_NAME, app


TEST_DATABASE_URL = ensure_safe_test_database_url()
ensure_database_exists(TEST_DATABASE_URL)
test_engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, future=True)
rate_limit_redis = Redis.from_url(TEST_RATE_LIMIT_STORAGE_URL)
TEST_RUN_LOCK_PATH = "/tmp/openscribe_pytest.lock"


def reset_public_schema() -> None:
    test_engine.dispose()
    with test_engine.connect() as connection:
        connection = connection.execution_options(isolation_level="AUTOCOMMIT")
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public AUTHORIZATION CURRENT_USER"))
        connection.execute(text("GRANT ALL ON SCHEMA public TO CURRENT_USER"))


@pytest.fixture(scope="session", autouse=True)
def test_run_lock() -> Generator[None, None, None]:
    lock_handle = open(TEST_RUN_LOCK_PATH, "w")
    try:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            pytest.exit(
                "Another pytest run is already using the shared OpenScribe test database. "
                "Wait for it to finish before starting another test run.",
                returncode=2,
            )
        yield
    finally:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            lock_handle.close()


@pytest.fixture(autouse=True)
def reset_database(request: pytest.FixtureRequest) -> Generator[None, None, None]:
    if request.node.get_closest_marker("migration"):
        yield
        return

    rate_limit_redis.flushdb()
    reset_public_schema()
    Base.metadata.create_all(bind=test_engine)
    yield
    rate_limit_redis.flushdb()


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    original_session_factory = getattr(app.state, "db_session_factory", None)
    app.state.db_session_factory = TestingSessionLocal
    with TestClient(app) as test_client:
        original_request = test_client.request

        def request_with_csrf(method, url, *args, **kwargs):
            method_upper = str(method).upper()
            path = str(url)
            unsafe_method = method_upper in {"POST", "PUT", "PATCH", "DELETE"}
            has_cookie_backed_authority = bool(
                test_client.cookies.get(SESSION_COOKIE_NAME)
                or test_client.cookies.get(TRUSTED_DEVICE_COOKIE_NAME)
            )
            should_inject_api_csrf = path.startswith("/api/") and has_cookie_backed_authority
            if unsafe_method and (not path.startswith("/api/") or should_inject_api_csrf):
                if CSRF_COOKIE_NAME not in test_client.cookies:
                    original_request("GET", "/login")
                csrf_token = test_client.cookies.get(CSRF_COOKIE_NAME, "")
                headers = dict(kwargs.pop("headers", {}) or {})
                headers.setdefault("X-CSRF-Token", csrf_token)
                headers.setdefault("Origin", "http://testserver")
                kwargs["headers"] = headers
                data = kwargs.get("data")
                if isinstance(data, dict):
                    merged_data = dict(data)
                    merged_data.setdefault("_csrf_token", csrf_token)
                    kwargs["data"] = merged_data
            return original_request(method, url, *args, **kwargs)

        test_client.request = request_with_csrf  # type: ignore[method-assign]
        yield test_client
    app.state.db_session_factory = original_session_factory
    app.dependency_overrides.clear()


@pytest.fixture
def raw_client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    original_session_factory = getattr(app.state, "db_session_factory", None)
    app.state.db_session_factory = TestingSessionLocal
    with TestClient(app) as test_client:
        yield test_client
    app.state.db_session_factory = original_session_factory
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def fake_user_content_transit(monkeypatch: pytest.MonkeyPatch) -> Generator[dict[str, bytes], None, None]:
    wrapped_keys: dict[str, bytes] = {}

    def fake_generate_user_content_data_key() -> tuple[bytes, str, int]:
        plaintext = secrets.token_bytes(32)
        wrapped = f"vault:v1:wrapped-{len(wrapped_keys) + 1}"
        wrapped_keys[wrapped] = plaintext
        return plaintext, wrapped, 1

    def fake_unwrap_user_content_data_key(*, wrapped_dek: str, mount_point: str | None = None, key_name: str | None = None) -> bytes:
        return wrapped_keys[wrapped_dek]

    monkeypatch.setattr("app.services.content_crypto.generate_user_content_data_key", fake_generate_user_content_data_key)
    monkeypatch.setattr("app.services.content_crypto.unwrap_user_content_data_key", fake_unwrap_user_content_data_key)
    yield wrapped_keys


@pytest.fixture
def make_team(db_session: Session) -> Callable[..., Team]:
    def factory(
        *,
        name: str = "Primary Team",
        status: TeamStatus = TeamStatus.active,
        default_retention_days: int = 30,
    ) -> Team:
        team = Team(
            name=name,
            name_key=normalize_team_name_key(name),
            status=status,
            default_retention_days=default_retention_days,
        )
        db_session.add(team)
        db_session.commit()
        db_session.refresh(team)
        return team

    return factory


@pytest.fixture
def make_user(db_session: Session, make_team: Callable[..., Team]) -> Callable[..., User]:
    def factory(
        *,
        email: str = "user@example.com",
        full_name: str | None = None,
        password: str = "password-1",
        team: Team | None = None,
        team_role: TeamRole | None = TeamRole.user,
        is_system_admin: bool = False,
        status: UserStatus = UserStatus.active,
        mfa_required: bool = True,
        onboarding_state: UserOnboardingState = UserOnboardingState.complete,
        must_change_password: bool = False,
        mfa_enabled: bool = True,
    ) -> User:
        resolved_team = None if is_system_admin else (team or make_team())
        user = User(
            full_name=full_name,
            email=normalize_email(email),
            password_hash=hash_password(password),
            team_id=resolved_team.id if resolved_team else None,
            team_role=None if is_system_admin else team_role,
            is_system_admin=is_system_admin,
            status=status,
            must_change_password=must_change_password,
            onboarding_state=onboarding_state,
            mfa_required=mfa_required,
            mfa_enabled=mfa_enabled,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        return user

    return factory


@pytest.fixture
def make_account_request(db_session: Session) -> Callable[..., AccountRequest]:
    def factory(
        *,
        requested_name: str = "Requester Name",
        requested_email: str = "request@example.com",
        requested_team_name: str = "Primary Team",
        request_details: str | None = "Please create my account",
        status: AccountRequestStatus = AccountRequestStatus.pending,
    ) -> AccountRequest:
        account_request = AccountRequest(
            requested_name=requested_name,
            requested_email=normalize_email(requested_email),
            requested_team_name=requested_team_name,
            requested_team_name_key=normalize_team_name_key(requested_team_name),
            request_details=request_details,
            status=status,
        )
        db_session.add(account_request)
        db_session.commit()
        db_session.refresh(account_request)
        return account_request

    return factory


@pytest.fixture
def make_deidentification_provider(db_session: Session, make_user: Callable[..., User]) -> Callable[..., DeidentificationProvider]:
    def factory(
        *,
        actor: User | None = None,
        label: str = "Built-in Native Presidio",
        adapter_kind: DeidentificationAdapterKind = DeidentificationAdapterKind.native_presidio,
        base_url: str = "",
        detect_path: str = "",
        auth_mode: DeidentificationAuthMode = DeidentificationAuthMode.none,
        request_text_field: str = "text",
        request_language_field: str | None = None,
        extra_headers_json: dict[str, str] | None = None,
        extra_body_json: dict[str, str] | None = None,
        response_entities_path: str = "entities",
        response_start_field: str = "start",
        response_end_field: str = "end",
        response_type_field: str = "entity_type",
        response_score_field: str | None = None,
        response_model_version_path: str | None = None,
        entity_type_map_json: dict[str, str] | None = None,
        clinical_detection_enabled: bool = False,
        clinical_detection_allow_unredacted: bool = False,
        is_active: bool = True,
        is_builtin: bool = False,
        has_secret: bool = False,
    ) -> DeidentificationProvider:
        resolved_actor = actor or make_user(email=f"deid-admin-{uuid4()}@example.com", password="password-1", is_system_admin=True)
        provider = DeidentificationProvider(
            label=label,
            adapter_kind=adapter_kind,
            base_url=base_url,
            detect_path=detect_path,
            auth_mode=auth_mode,
            request_text_field=request_text_field,
            request_language_field=request_language_field,
            extra_headers_json=extra_headers_json or {},
            extra_body_json=extra_body_json or {},
            response_entities_path=response_entities_path,
            response_start_field=response_start_field,
            response_end_field=response_end_field,
            response_type_field=response_type_field,
            response_score_field=response_score_field,
            response_model_version_path=response_model_version_path,
            entity_type_map_json=entity_type_map_json or {},
            clinical_detection_enabled=clinical_detection_enabled,
            clinical_detection_allow_unredacted=clinical_detection_allow_unredacted,
            vault_secret_ref=f"secret:openscribe/deidentification/provider/{uuid4()}" if has_secret else "",
            is_active=is_active,
            is_builtin=is_builtin,
            created_by_user_id=resolved_actor.id if not is_builtin else None,
            updated_by_user_id=resolved_actor.id if not is_builtin else None,
        )
        db_session.add(provider)
        db_session.commit()
        db_session.refresh(provider)
        return provider

    return factory


@pytest.fixture
def make_deidentification_provider_assignment(db_session: Session, make_user: Callable[..., User]) -> Callable[..., TeamDeidentificationProviderAssignment]:
    def factory(
        *,
        team: Team,
        provider: DeidentificationProvider,
        actor: User | None = None,
    ) -> TeamDeidentificationProviderAssignment:
        resolved_actor = actor or make_user(email=f"deid-assigner-{uuid4()}@example.com", password="password-1", is_system_admin=True)
        assignment = TeamDeidentificationProviderAssignment(
            team_id=team.id,
            provider_id=provider.id,
            assigned_by_user_id=resolved_actor.id,
        )
        db_session.add(assignment)
        db_session.commit()
        db_session.refresh(assignment)
        return assignment

    return factory


@pytest.fixture
def make_deidentification_selection(db_session: Session, make_user: Callable[..., User]) -> Callable[..., TeamDeidentificationSelection]:
    def factory(
        *,
        team: Team,
        provider: DeidentificationProvider,
        actor: User | None = None,
    ) -> TeamDeidentificationSelection:
        resolved_actor = actor or make_user(
            email=f"leader-deid-{team.id}@example.com",
            password="password-1",
            team=team,
            team_role=TeamRole.leader,
            is_system_admin=False,
        )
        selection = TeamDeidentificationSelection(
            team_id=team.id,
            provider_id=provider.id,
            selected_by_user_id=resolved_actor.id,
        )
        db_session.add(selection)
        db_session.commit()
        db_session.refresh(selection)
        return selection

    return factory


@pytest.fixture
def make_clinical_nlp_selection(db_session: Session, make_user: Callable[..., User]) -> Callable[..., TeamClinicalNlpSelection]:
    def factory(
        *,
        team: Team,
        provider: DeidentificationProvider,
        actor: User | None = None,
    ) -> TeamClinicalNlpSelection:
        resolved_actor = actor or make_user(
            email=f"leader-clinical-nlp-{team.id}@example.com",
            password="password-1",
            team=team,
            team_role=TeamRole.leader,
            is_system_admin=False,
        )
        selection = TeamClinicalNlpSelection(
            team_id=team.id,
            provider_id=provider.id,
            selected_by_user_id=resolved_actor.id,
        )
        db_session.add(selection)
        db_session.commit()
        db_session.refresh(selection)
        return selection

    return factory


@pytest.fixture
def make_stt_config(db_session: Session, make_team: Callable[..., Team], make_user: Callable[..., User]) -> Callable[..., TeamSttConfig]:
    def factory(
        *,
        team: Team | None = None,
        actor: User | None = None,
        label: str = "Provisioned STT",
        adapter_kind: SttAdapterKind = SttAdapterKind.openai_compatible_rest,
        base_url: str = "http://127.0.0.1:9000",
        transcribe_path: str = "/v1/audio/transcriptions",
        model_name: str | None = "whisper-1",
        available_models_json: list[str] | None = None,
        file_field_name: str = "file",
        language: str | None = "en",
        response_text_path: str = "text",
        extra_form_fields_json: dict[str, str] | None = None,
        is_active: bool = True,
        has_secret: bool = True,
    ) -> TeamSttConfig:
        resolved_team = team or make_team()
        resolved_actor = actor or make_user(email=f"stt-admin-{resolved_team.id}@example.com", password="password-1", is_system_admin=True)
        config = TeamSttConfig(
            team_id=resolved_team.id,
            label=label,
            adapter_kind=adapter_kind,
            base_url=base_url,
            transcribe_path=transcribe_path,
            auth_mode=SttAuthMode.bearer,
            model_name=model_name,
            available_models_json=available_models_json or [],
            file_field_name=file_field_name,
            language=language,
            response_text_path=response_text_path,
            extra_form_fields_json=extra_form_fields_json or {},
            vault_secret_ref=f"secret:openscribe/stt/team/{resolved_team.id}/config/{uuid4()}" if has_secret else "",
            is_active=is_active,
            created_by_user_id=resolved_actor.id,
            updated_by_user_id=resolved_actor.id,
        )
        db_session.add(config)
        db_session.commit()
        db_session.refresh(config)
        return config

    return factory


@pytest.fixture
def make_stt_selection(db_session: Session, make_user: Callable[..., User]) -> Callable[..., TeamSttSelection]:
    def factory(
        *,
        config: TeamSttConfig,
        actor: User | None = None,
        purpose: SttSelectionPurpose = SttSelectionPurpose.conversation,
        model_name_override: str | None = None,
        language_override: str | None = None,
    ) -> TeamSttSelection:
        resolved_actor = actor or make_user(
            email=f"leader-{config.team_id}@example.com",
            password="password-1",
            team=db_session.get(Team, config.team_id),
            team_role=TeamRole.leader,
            is_system_admin=False,
        )
        selection = TeamSttSelection(
            team_id=config.team_id,
            purpose=purpose,
            stt_config_id=config.id,
            model_name_override=model_name_override,
            language_override=language_override,
            selected_by_user_id=resolved_actor.id,
        )
        db_session.add(selection)
        db_session.commit()
        db_session.refresh(selection)
        return selection

    return factory


@pytest.fixture
def make_llm_config(db_session: Session, make_team: Callable[..., Team], make_user: Callable[..., User]) -> Callable[..., TeamLlmConfig]:
    def factory(
        *,
        team: Team | None = None,
        actor: User | None = None,
        label: str = "Provisioned LLM",
        provider_preset: str | None = None,
        adapter_kind: LlmAdapterKind = LlmAdapterKind.openai_chat,
        base_url: str = "https://api.openai.com/v1",
        model_name: str | None = "gpt-4o-mini",
        available_models_json: list[str] | None = None,
        is_active: bool = True,
        has_secret: bool = True,
    ) -> TeamLlmConfig:
        resolved_team = team or make_team()
        resolved_actor = actor or make_user(email=f"llm-admin-{resolved_team.id}@example.com", password="password-1", is_system_admin=True)
        resolved_provider_preset = provider_preset or infer_llm_provider_preset(adapter_kind, base_url)
        config = TeamLlmConfig(
            team_id=resolved_team.id,
            label=label,
            provider_preset=resolved_provider_preset,
            adapter_kind=adapter_kind,
            base_url=base_url,
            auth_mode=LlmAuthMode.bearer if has_secret else LlmAuthMode.none,
            model_name=model_name,
            available_models_json=available_models_json or [],
            inspection_metadata_json={},
            vault_secret_ref=f"secret:openscribe/llm/team/{resolved_team.id}/config/{uuid4()}" if has_secret else "",
            is_active=is_active,
            created_by_user_id=resolved_actor.id,
            updated_by_user_id=resolved_actor.id,
        )
        db_session.add(config)
        db_session.commit()
        db_session.refresh(config)
        return config

    return factory


@pytest.fixture
def make_llm_selection(db_session: Session, make_user: Callable[..., User]) -> Callable[..., TeamLlmSelection]:
    def factory(
        *,
        config: TeamLlmConfig,
        actor: User | None = None,
        allowed_models_json: list[str] | None = None,
        model_name_override: str | None = None,
    ) -> TeamLlmSelection:
        resolved_actor = actor or make_user(
            email=f"leader-llm-{config.team_id}@example.com",
            password="password-1",
            team=db_session.get(Team, config.team_id),
            team_role=TeamRole.leader,
            is_system_admin=False,
        )
        selection = TeamLlmSelection(
            team_id=config.team_id,
            llm_config_id=config.id,
            allowed_models_json=allowed_models_json or list(config.available_models_json or []),
            model_name_override=model_name_override,
            selected_by_user_id=resolved_actor.id,
        )
        db_session.add(selection)
        db_session.commit()
        db_session.refresh(selection)
        return selection

    return factory


@pytest.fixture
def make_template(db_session: Session, make_team: Callable[..., Team], make_user: Callable[..., User]) -> Callable[..., PromptTemplate]:
    def factory(
        *,
        scope: TemplateScope = TemplateScope.user,
        team: Team | None = None,
        owner: User | None = None,
        actor: User | None = None,
        name: str = "Template",
        description: str | None = "Template description",
        prompt_text: str = "Summarise the transcript as a note.",
        mode: TemplateMode = TemplateMode.freeform,
        config_json: dict | None = None,
        is_active: bool = True,
    ) -> PromptTemplate:
        resolved_team = team or (owner.team if owner is not None else make_team())
        resolved_owner = owner if scope is TemplateScope.user else None
        if scope is TemplateScope.user and resolved_owner is None:
            resolved_owner = make_user(email=f"template-owner-{uuid4()}@example.com", password="password-1", team=resolved_team, team_role=TeamRole.user)
        resolved_actor = actor or resolved_owner or make_user(email=f"template-leader-{uuid4()}@example.com", password="password-1", team=resolved_team, team_role=TeamRole.leader)
        template = PromptTemplate(
            scope=scope,
            owner_user_id=resolved_owner.id if resolved_owner is not None else None,
            team_id=resolved_team.id if scope is TemplateScope.team else None,
            name=name,
            description=description,
            is_active=is_active,
            created_by_user_id=resolved_actor.id,
        )
        db_session.add(template)
        db_session.flush()
        version = PromptTemplateVersion(
            template_id=template.id,
            version_no=1,
            mode=mode,
            prompt_text=prompt_text,
            config_json=config_json,
            created_by_user_id=resolved_actor.id,
        )
        db_session.add(version)
        db_session.commit()
        db_session.refresh(template)
        return template

    return factory


@pytest.fixture
def make_default_template(db_session: Session, make_user: Callable[..., User]) -> Callable[..., DefaultPromptTemplate]:
    def factory(
        *,
        actor: User | None = None,
        name: str = "Default template",
        description: str | None = "Default template description",
        prompt_text: str = "Summarise the transcript as a note.",
        mode: TemplateMode = TemplateMode.freeform,
        config_json: dict | None = None,
        is_active: bool = True,
    ) -> DefaultPromptTemplate:
        resolved_actor = actor or make_user(email=f"default-template-admin-{uuid4()}@example.com", password="password-1", is_system_admin=True)
        template = DefaultPromptTemplate(
            name=name,
            description=description,
            is_active=is_active,
            created_by_user_id=resolved_actor.id,
        )
        db_session.add(template)
        db_session.flush()
        version = DefaultPromptTemplateVersion(
            default_template_id=template.id,
            version_no=1,
            mode=mode,
            prompt_text=prompt_text,
            config_json=config_json,
            created_by_user_id=resolved_actor.id,
        )
        db_session.add(version)
        db_session.commit()
        db_session.refresh(template)
        return template

    return factory


@pytest.fixture
def make_quick_action(db_session: Session, make_team: Callable[..., Team], make_user: Callable[..., User]) -> Callable[..., QuickAction]:
    def factory(
        *,
        scope: TemplateScope = TemplateScope.user,
        team: Team | None = None,
        owner: User | None = None,
        actor: User | None = None,
        name: str = "Quick action",
        description: str | None = "Quick action description",
        prompt_text: str = "Write a short follow-up from the doctor's perspective.",
        is_active: bool = True,
    ) -> QuickAction:
        resolved_team = team or (owner.team if owner is not None else make_team())
        resolved_owner = owner if scope is TemplateScope.user else None
        if scope is TemplateScope.user and resolved_owner is None:
            resolved_owner = make_user(email=f"quick-action-owner-{uuid4()}@example.com", password="password-1", team=resolved_team, team_role=TeamRole.user)
        resolved_actor = actor or resolved_owner or make_user(email=f"quick-action-leader-{uuid4()}@example.com", password="password-1", team=resolved_team, team_role=TeamRole.leader)
        quick_action = QuickAction(
            scope=scope,
            owner_user_id=resolved_owner.id if resolved_owner is not None else None,
            team_id=resolved_team.id if scope is TemplateScope.team else None,
            name=name,
            description=description,
            is_active=is_active,
            created_by_user_id=resolved_actor.id,
        )
        db_session.add(quick_action)
        db_session.flush()
        version = QuickActionVersion(
            quick_action_id=quick_action.id,
            version_no=1,
            mode=TemplateMode.freeform,
            prompt_text=prompt_text,
            created_by_user_id=resolved_actor.id,
        )
        db_session.add(version)
        db_session.commit()
        db_session.refresh(quick_action)
        return quick_action

    return factory


@pytest.fixture
def make_default_quick_action(db_session: Session, make_user: Callable[..., User]) -> Callable[..., DefaultQuickAction]:
    def factory(
        *,
        actor: User | None = None,
        name: str = "Default quick action",
        description: str | None = "Default quick action description",
        prompt_text: str = "Write a short follow-up from the doctor's perspective.",
        is_active: bool = True,
    ) -> DefaultQuickAction:
        resolved_actor = actor or make_user(email=f"default-quick-action-admin-{uuid4()}@example.com", password="password-1", is_system_admin=True)
        quick_action = DefaultQuickAction(
            name=name,
            description=description,
            is_active=is_active,
            created_by_user_id=resolved_actor.id,
        )
        db_session.add(quick_action)
        db_session.flush()
        version = DefaultQuickActionVersion(
            default_quick_action_id=quick_action.id,
            version_no=1,
            mode=TemplateMode.freeform,
            prompt_text=prompt_text,
            created_by_user_id=resolved_actor.id,
        )
        db_session.add(version)
        db_session.commit()
        db_session.refresh(quick_action)
        return quick_action

    return factory


@pytest.fixture
def make_generated_document(db_session: Session) -> Callable[..., GeneratedDocument]:
    def factory(
        *,
        owner: User,
        transcript: Transcript,
        transcript_version: TranscriptVersion,
        template_version: PromptTemplateVersion | None = None,
        title: str = "Generated output",
        output_text: str = "Generated note text",
        model_used: str = "gpt-4o-mini",
    ) -> GeneratedDocument:
        document = GeneratedDocument(
            owner_user_id=owner.id,
            team_id=transcript.team_id,
            transcript_id=transcript.id,
            transcript_version_id=transcript_version.id,
            generator_type=GeneratedDocumentGeneratorType.template,
            template_version_id=template_version.id if template_version is not None else None,
            source_template_name="Template" if template_version is None else template_version.template.name,
            status=GeneratedDocumentStatus.ready,
            title=title,
            document_mode=TemplateMode.freeform,
            original_output_text_encrypted=output_text,
            edited_output_text_encrypted=output_text,
            is_edited=False,
            retention_expires_at=transcript.retention_expires_at,
            model_used=model_used,
        )
        db_session.add(document)
        db_session.commit()
        db_session.refresh(document)
        return document

    return factory


@pytest.fixture
def make_redaction_run(db_session: Session) -> Callable[..., RedactionRun]:
    def factory(
        *,
        transcript: Transcript,
        transcript_version: TranscriptVersion,
        owner: User,
        redacted_text: str = "[PHI-1] attended the clinic.",
        entities: list[tuple[int, str, str]] | None = None,
        status: RedactionRunStatus = RedactionRunStatus.succeeded,
    ) -> RedactionRun:
        run = RedactionRun(
            transcript_id=transcript.id,
            transcript_version_id=transcript_version.id,
            owner_user_id=owner.id,
            team_id=transcript.team_id,
            status=status,
            redacted_text_encrypted=redacted_text,
            mapping_hash="hash",
            entity_count=len(entities or [(1, "PERSON", "John Smith")]),
            api_provider="native_presidio",
            api_model_or_version="en_core_web_sm",
        )
        db_session.add(run)
        db_session.flush()
        for order, entity_type, original_value in entities or [(1, "PERSON", "John Smith")]:
            db_session.add(
                RedactionEntity(
                    redaction_run_id=run.id,
                    entity_order=order,
                    entity_type=entity_type,
                    placeholder=f"[PHI-{order}]",
                    original_value_encrypted=original_value,
                    normalized_value_hash="hash",
                    occurrence_count=1,
                )
            )
        db_session.commit()
        db_session.refresh(run)
        return run

    return factory


@pytest.fixture
def make_user_llm_preference(db_session: Session) -> Callable[..., UserLlmPreference]:
    def factory(*, user: User, preferred_model_name: str | None = None) -> UserLlmPreference:
        preference = UserLlmPreference(user_id=user.id, preferred_model_name=preferred_model_name)
        db_session.add(preference)
        db_session.commit()
        db_session.refresh(preference)
        return preference

    return factory


@pytest.fixture
def make_user_app_preference(db_session: Session) -> Callable[..., UserAppPreference]:
    def factory(*, user: User, preferences_json: dict | None = None) -> UserAppPreference:
        preference = UserAppPreference(user_id=user.id, preferences_json=preferences_json or {})
        db_session.add(preference)
        db_session.commit()
        db_session.refresh(preference)
        return preference

    return factory


@pytest.fixture(autouse=True)
def stub_vault_secret_write(monkeypatch: pytest.MonkeyPatch):
    def fake_write_team_stt_bearer_token(*, team_id, config_id, bearer_token, secret_id=None):
        suffix = f"/{secret_id}" if secret_id else ""
        return f"secret:openscribe/stt/team/{team_id}/config/{config_id}{suffix}"

    def fake_delete_team_stt_bearer_token(*, team_id, config_id, secret_ref=None):
        return None

    def fake_read_team_stt_bearer_token(*, team_id, config_id, secret_ref=None):
        return "test-stt-token"

    def fake_write_team_llm_bearer_token(*, team_id, config_id, bearer_token, secret_id=None, secret_ref=None):
        if secret_ref is not None:
            return secret_ref
        suffix = f"/{secret_id}" if secret_id else ""
        return f"secret:openscribe/llm/team/{team_id}/config/{config_id}{suffix}"

    def fake_delete_team_llm_bearer_token(*, team_id, config_id, secret_ref=None):
        return None

    def fake_read_team_llm_bearer_token(*, team_id, config_id, secret_ref=None):
        return "test-llm-token"

    def fake_write_team_llm_secret(*, team_id, config_id, secret_payload, secret_id=None, secret_ref=None):
        if secret_ref is not None:
            return secret_ref
        suffix = f"/{secret_id}" if secret_id else ""
        return f"secret:openscribe/llm/team/{team_id}/config/{config_id}{suffix}"

    def fake_read_team_llm_secret(*, team_id, config_id, secret_ref=None):
        return {"secret_type": "bearer_token", "bearer_token": "test-llm-token"}

    def fake_write_deidentification_bearer_token(*, provider_id, bearer_token, secret_id=None):
        suffix = f"/{secret_id}" if secret_id else ""
        return f"secret:openscribe/deidentification/provider/{provider_id}{suffix}"

    def fake_delete_deidentification_bearer_token(*, provider_id, secret_ref=None):
        return None

    def fake_read_deidentification_bearer_token(*, provider_id, secret_ref=None):
        return "test-deidentification-token"

    monkeypatch.setattr("app.services.stt.write_team_stt_bearer_token", fake_write_team_stt_bearer_token)
    monkeypatch.setattr("app.services.stt.delete_team_stt_bearer_token", fake_delete_team_stt_bearer_token)
    monkeypatch.setattr("app.services.stt.read_team_stt_bearer_token", fake_read_team_stt_bearer_token)
    monkeypatch.setattr("app.services.llm.write_team_llm_bearer_token", fake_write_team_llm_bearer_token)
    monkeypatch.setattr("app.services.llm.delete_team_llm_bearer_token", fake_delete_team_llm_bearer_token)
    monkeypatch.setattr("app.services.llm.read_team_llm_bearer_token", fake_read_team_llm_bearer_token)
    monkeypatch.setattr("app.services.llm.write_team_llm_secret", fake_write_team_llm_secret)
    monkeypatch.setattr("app.services.llm.read_team_llm_secret", fake_read_team_llm_secret)
    monkeypatch.setattr("app.services.templates.read_team_llm_bearer_token", fake_read_team_llm_bearer_token)
    monkeypatch.setattr("app.services.llm_credentials.read_team_llm_secret", fake_read_team_llm_secret)
    monkeypatch.setattr("app.services.deidentification.write_deidentification_bearer_token", fake_write_deidentification_bearer_token)
    monkeypatch.setattr("app.services.deidentification.delete_deidentification_bearer_token", fake_delete_deidentification_bearer_token)
    monkeypatch.setattr("app.services.deidentification.read_deidentification_bearer_token", fake_read_deidentification_bearer_token)
    monkeypatch.setattr("app.services.redaction.read_deidentification_provider_bearer_token", lambda db, provider_id: fake_read_deidentification_bearer_token(provider_id=provider_id))


@pytest.fixture(autouse=True)
def stub_transcript_ingestion_enqueue(monkeypatch: pytest.MonkeyPatch):
    class FakeTaskResult:
        id = "test-task-id"

    def fake_enqueue_transcript_ingestion_job(*, job_id):
        return FakeTaskResult()

    monkeypatch.setattr("app.main.enqueue_transcript_ingestion_job", fake_enqueue_transcript_ingestion_job)


@pytest.fixture(autouse=True)
def stub_transcript_ingestion_source_audio_vault(monkeypatch: pytest.MonkeyPatch):
    stored_audio: dict[str, bytes] = {}

    def fake_write_transcript_ingestion_source_audio(*, job_id, audio_bytes):
        secret_ref = f"secret:openscribe/transcript-ingestion/{job_id}/source-audio"
        stored_audio[secret_ref] = audio_bytes
        return secret_ref

    def fake_read_transcript_ingestion_source_audio(*, secret_ref):
        return stored_audio[secret_ref]

    def fake_delete_transcript_ingestion_source_audio(*, secret_ref):
        stored_audio.pop(secret_ref, None)

    monkeypatch.setattr("app.services.transcripts.write_transcript_ingestion_source_audio", fake_write_transcript_ingestion_source_audio)
    monkeypatch.setattr("app.services.transcripts.read_transcript_ingestion_source_audio", fake_read_transcript_ingestion_source_audio)
    monkeypatch.setattr("app.services.transcripts.delete_transcript_ingestion_source_audio", fake_delete_transcript_ingestion_source_audio)


@pytest.fixture(autouse=True)
def stub_redaction_pipeline(monkeypatch: pytest.MonkeyPatch, db_session: Session):
    def fake_ensure_redaction_run_for_transcript_version(*, transcript_version: TranscriptVersion):
        existing = db_session.query(RedactionRun).filter(RedactionRun.transcript_version_id == transcript_version.id).first()
        if existing is not None:
            return existing
        run_id = uuid4()
        plaintext = transcript_version.text_encrypted
        run = RedactionRun(
            id=run_id,
            transcript_id=transcript_version.transcript_id,
            transcript_version_id=transcript_version.id,
            owner_user_id=transcript_version.transcript.owner_user_id,
            team_id=transcript_version.transcript.team_id,
            status=RedactionRunStatus.succeeded,
            redacted_text_encrypted=encrypt_text_for_owner(
                db_session,
                owner_user_id=transcript_version.transcript.owner_user_id,
                table="redaction_runs",
                field="redacted_text_encrypted",
                record_id=run_id,
                plaintext=(
                    decrypt_text_for_owner(
                        db_session,
                        owner_user_id=transcript_version.transcript.owner_user_id,
                        table="transcript_versions",
                        field="text_encrypted",
                        record_id=transcript_version.id,
                        stored_value=transcript_version.text_encrypted,
                    )
                    or ""
                ),
            ),
            mapping_hash="stub-redaction",
            entity_count=0,
            api_provider="stub_redaction",
            api_model_or_version="stub",
        )
        db_session.add(run)
        db_session.commit()
        db_session.refresh(run)
        return run

    def fake_redact_transient_text(db, text: str, *, team_id, start_index: int):
        return {
            "redacted_text": text.strip() if text.strip() else text,
            "phi_mapping": {},
            "phi_index": [],
            "phi_count": 0,
            "api_provider": "stub_redaction",
            "api_model_or_version": "stub",
        }

    monkeypatch.setattr(
        "app.services.templates.ensure_redaction_run_for_transcript_version",
        lambda db, *, transcript_version: fake_ensure_redaction_run_for_transcript_version(transcript_version=transcript_version),
    )
    monkeypatch.setattr("app.services.templates.redact_transient_text", fake_redact_transient_text)
    monkeypatch.setattr(
        "app.services.templates.reidentify_text",
        lambda redacted_text, *, phi_index: redacted_text,
    )
