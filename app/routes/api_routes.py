"""JSON/API routes extracted from app.main."""

from .. import main as main_module
from ..main import *  # noqa: F401,F403
from ..main import (
    _clear_session_cookie,
    _enforce_localhost_only_dev_account,
    _open_realtime_workspace_db_session,
    _require_full_context_from_token,
    _serialize_sse_event,
    _set_session_cookie,
    _set_trusted_device_cookie,
)


@api.post("/auth/login", response_model=LoginResponse, responses=error_responses)
@LOGIN_RATE_LIMIT
def api_login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = authenticate_user(db, payload.email, payload.password)
    _enforce_localhost_only_dev_account(request, user)
    trusted_device = resolve_trusted_device(db, user, request.cookies.get(TRUSTED_DEVICE_COOKIE_NAME))
    auth_level = login_auth_level(user, trusted_device)
    if trusted_device and auth_level is SessionAuthLevel.full:
        touch_trusted_device_seen(db, trusted_device)
    token = create_session(db, user, auth_level=auth_level)
    body = LoginResponse(
        authenticated=True,
        auth_level=auth_level,
        redirect_to="/onboarding" if auth_level.value == "onboarding" else ("/mfa/challenge" if auth_level.value == "pending_mfa" else ("/admin" if user.is_system_admin else "/home")),
    )
    response = JSONResponse(body.model_dump(mode="json"))
    _set_session_cookie(request, response, token)
    return response


@api.post("/auth/logout", response_model=LoginResponse)
def api_logout(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        revoke_session_by_token(db, token, reason="logout")
    response = JSONResponse(LoginResponse(authenticated=False).model_dump(mode="json"))
    _clear_session_cookie(response)
    return response


@api.post("/auth/mfa/totp", response_model=LoginResponse, responses=error_responses)
@MFA_RATE_LIMIT
def api_login_mfa_totp(
    payload: MfaChallengeRequest,
    request: Request,
    context: AuthenticatedContext = Depends(require_authenticated_context),
    db: Session = Depends(get_db),
):
    if context.session.auth_level is not SessionAuthLevel.pending_mfa:
        raise AppError(409, "conflict", "MFA challenge is not pending for this session")
    user, trusted_device_token = verify_login_totp(
        db,
        context.user,
        code=payload.code,
        remember_device=payload.remember_device,
        device_label=request.headers.get("user-agent"),
    )
    token = rotate_session(db, context.token, user, auth_level=determine_auth_level(user))
    response = JSONResponse(
        LoginResponse(
            authenticated=True,
            auth_level=determine_auth_level(user),
            redirect_to="/admin" if user.is_system_admin else "/home",
        ).model_dump(mode="json")
    )
    _set_session_cookie(request, response, token)
    if trusted_device_token:
        _set_trusted_device_cookie(request, response, trusted_device_token)
    return response


@api.get("/auth/me", response_model=CurrentUserResponse, responses=error_responses)
def api_me(context: AuthenticatedContext = Depends(require_authenticated_context)):
    return CurrentUserResponse(
        id=str(context.user.id),
        full_name=context.user.full_name,
        email=context.user.email,
        is_system_admin=context.user.is_system_admin,
        team_id=str(context.user.team_id) if context.user.team_id else None,
        team_role=context.user.team_role.value if context.user.team_role else None,
        auth_level=context.session.auth_level,
        onboarding_state=context.user.onboarding_state,
    )


@api.get("/auth/trusted-device", response_model=TrustedDeviceStatusResponse, responses=error_responses)
def api_trusted_device_status(request: Request, context: AuthenticatedContext = Depends(require_authenticated_context), db: Session = Depends(get_db)):
    cookie = request.cookies.get(TRUSTED_DEVICE_COOKIE_NAME)
    device = resolve_trusted_device(db, context.user, cookie)
    if device and trusted_device_satisfies_mfa(device):
        return TrustedDeviceStatusResponse(trusted=True, requires_mfa=False, freshness_expires_at=trusted_device_fresh_until(device))
    return TrustedDeviceStatusResponse(trusted=device is not None, requires_mfa=True, freshness_expires_at=trusted_device_fresh_until(device) if device else None)


@api.post("/account-requests", response_model=AccountRequestDetail, status_code=status.HTTP_201_CREATED, responses=error_responses)
@ACCOUNT_REQUEST_RATE_LIMIT
def create_account_request(request: Request, payload: AccountRequestCreate, db: Session = Depends(get_db)):
    return create_account_request_service(db, payload)


@api.get("/account-requests", response_model=list[AccountRequestListItem], responses=error_responses)
def list_account_requests(context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    return list_manageable_account_requests_service(db, context.user)


@api.post("/account-requests/{request_id}/approve", response_model=UserDetail, responses=error_responses)
def approve_account_request(request_id: UUID, payload: AccountRequestApprove, context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    _, user = approve_account_request_service(db, context.user, request_id, payload)
    return user


@api.post("/account-requests/{request_id}/reject", response_model=AccountRequestDetail, responses=error_responses)
def reject_account_request(request_id: UUID, payload: AccountRequestReject, context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    return reject_account_request_service(db, context.user, request_id, payload)


@api.post("/onboarding/password", response_model=CurrentUserResponse, responses=error_responses)
def api_onboarding_password(payload: PasswordChangeRequest, context: AuthenticatedContext = Depends(require_authenticated_context), db: Session = Depends(get_db)):
    user = update_password_for_onboarding(db, context.user, new_password_hash=hash_password(payload.new_password))
    return CurrentUserResponse(
        id=str(user.id),
        full_name=user.full_name,
        email=user.email,
        is_system_admin=user.is_system_admin,
        team_id=str(user.team_id) if user.team_id else None,
        team_role=user.team_role.value if user.team_role else None,
        auth_level=context.session.auth_level,
        onboarding_state=user.onboarding_state,
    )


@api.post("/onboarding/totp/start", response_model=TotpEnrollmentStartResponse, responses=error_responses)
def api_onboarding_totp_start(context: AuthenticatedContext = Depends(require_authenticated_context), db: Session = Depends(get_db)):
    method = start_totp_enrollment(db, context.user)
    uri = provisioning_uri(context.user, method)
    return TotpEnrollmentStartResponse(
        secret=method.secret,
        provisioning_uri=uri,
        qr_code_svg_data_uri=provisioning_qr_svg_data_uri(uri),
    )


@api.post("/onboarding/totp/verify", response_model=CurrentUserResponse, responses=error_responses)
def api_onboarding_totp_verify(payload: TotpVerifyRequest, context: AuthenticatedContext = Depends(require_authenticated_context), db: Session = Depends(get_db)):
    user = verify_totp_enrollment(db, context.user, code=payload.code)
    return CurrentUserResponse(
        id=str(user.id),
        full_name=user.full_name,
        email=user.email,
        is_system_admin=user.is_system_admin,
        team_id=str(user.team_id) if user.team_id else None,
        team_role=user.team_role.value if user.team_role else None,
        auth_level=context.session.auth_level,
        onboarding_state=user.onboarding_state,
    )


@api.post("/onboarding/recovery-codes", response_model=RecoveryCodesResponse, responses=error_responses)
def api_onboarding_recovery_codes(request: Request, context: AuthenticatedContext = Depends(require_authenticated_context), db: Session = Depends(get_db)):
    codes = generate_recovery_codes(db, context.user)
    refreshed_user = db.get(User, context.user.id)
    token = rotate_session(db, context.token, refreshed_user, auth_level=determine_auth_level(refreshed_user))
    response = JSONResponse(RecoveryCodesResponse(codes=codes).model_dump(mode="json"))
    _set_session_cookie(request, response, token)
    return response


@api.post("/onboarding/skip-recovery-codes", response_model=LoginResponse, responses=error_responses)
def api_skip_recovery_codes(request: Request, context: AuthenticatedContext = Depends(require_authenticated_context), db: Session = Depends(get_db)):
    user = skip_recovery_codes(db, context.user)
    token = rotate_session(db, context.token, user, auth_level=determine_auth_level(user))
    response = JSONResponse(
        LoginResponse(authenticated=True, auth_level=determine_auth_level(user), redirect_to="/admin" if user.is_system_admin else "/home").model_dump(mode="json")
    )
    _set_session_cookie(request, response, token)
    return response


@api.post("/teams", response_model=TeamDetail, status_code=status.HTTP_201_CREATED, responses=error_responses)
def create_team(payload: TeamCreate, context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    return create_team_service(db, payload, actor=context.user)


@api.get("/teams", response_model=list[TeamListItem], responses=error_responses)
def list_teams(_: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    return list_teams_service(db)


@api.post("/users", response_model=UserDetail, status_code=status.HTTP_201_CREATED, responses=error_responses)
def create_user(payload: UserCreate, context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    return create_user_service(db, payload, actor=context.user)


@api.get("/users", response_model=list[UserListItem], responses=error_responses)
def list_users(context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    return list_manageable_users_service(db, context.user)


@api.get("/stt-configs", response_model=list[SttConfigDetail], responses=error_responses)
def list_stt_configs(team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    return [stt_config_response(config) for config in list_stt_configs_service(db, context.user, team_id=team_id)]


@api.get("/stt-configs/{config_id}", response_model=SttConfigDetail, responses=error_responses)
def get_stt_config(config_id: UUID, team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    return stt_config_response(get_stt_config_service(db, context.user, config_id=config_id, team_id=team_id))


@api.post("/stt-configs/inspect", response_model=SttInspectResult, responses=error_responses)
def inspect_stt_config(payload: SttInspectRequest, context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    return inspect_stt_contract_service(db, context.user, payload)


@api.post("/stt-configs", response_model=SttConfigDetail, responses=error_responses)
def upsert_stt_config(payload: SttConfigUpsert, context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    return stt_config_response(upsert_stt_config_service(db, context.user, payload))


@api.delete("/stt-configs/{config_id}", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def delete_stt_config(config_id: UUID, team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    delete_stt_config_service(db, context.user, config_id=config_id, team_id=team_id)


@api.get("/stt-selection", response_model=SttSelectionDetail | None, responses=error_responses)
def get_stt_selection(
    team_id: UUID | None = None,
    purpose: main_module.SttSelectionPurpose = main_module.SttSelectionPurpose.conversation,
    context: AuthenticatedContext = Depends(require_stt_selector),
    db: Session = Depends(get_db),
):
    selection = get_team_stt_selection_service(db, context.user, team_id=team_id, purpose=purpose)
    return stt_selection_response(selection) if selection else None


@api.get("/stt-selection/options", response_model=list[SttConfigDetail], responses=error_responses)
def list_stt_selection_options(team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_stt_selector), db: Session = Depends(get_db)):
    return [stt_config_response(config) for config in list_selectable_stt_configs_service(db, context.user, team_id=team_id)]


@api.post("/stt-selection", response_model=SttSelectionDetail, responses=error_responses)
def set_stt_selection(payload: SttSelectionUpsert, context: AuthenticatedContext = Depends(require_stt_selector), db: Session = Depends(get_db)):
    return stt_selection_response(set_team_stt_selection_service(db, context.user, payload))


@api.delete("/stt-selection", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def clear_stt_selection(
    team_id: UUID | None = None,
    purpose: main_module.SttSelectionPurpose = main_module.SttSelectionPurpose.conversation,
    context: AuthenticatedContext = Depends(require_stt_selector),
    db: Session = Depends(get_db),
):
    clear_team_stt_selection_service(db, context.user, team_id=team_id, purpose=purpose)


@api.get("/llm-configs", response_model=list[LlmConfigDetail], responses=error_responses)
def list_llm_configs(team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    return [llm_config_response(config) for config in list_llm_configs_service(db, context.user, team_id=team_id)]


@api.post("/llm-configs/inspect", response_model=LlmConfigInspectResult, responses=error_responses)
def inspect_llm_config(payload: LlmInspectRequest, context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    return inspect_llm_contract_service(db, context.user, payload)


@api.post("/llm-configs", response_model=LlmConfigDetail, responses=error_responses)
def upsert_llm_config(payload: LlmConfigUpsert, context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    return llm_config_response(upsert_llm_config_service(db, context.user, payload))


@api.delete("/llm-configs/{config_id}", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def delete_llm_config(config_id: UUID, team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    delete_llm_config_service(db, context.user, config_id=config_id, team_id=team_id)


@api.get("/llm-selection", response_model=LlmSelectionDetail | None, responses=error_responses)
def get_llm_selection(team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_llm_selector), db: Session = Depends(get_db)):
    selection = get_team_llm_selection_service(db, context.user, team_id=team_id)
    return llm_selection_response(selection) if selection else None


@api.get("/llm-selection/options", response_model=list[LlmConfigDetail], responses=error_responses)
def list_llm_selection_options(team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_llm_selector), db: Session = Depends(get_db)):
    return [llm_config_response(config) for config in list_selectable_llm_configs_service(db, context.user, team_id=team_id)]


@api.post("/llm-selection", response_model=LlmSelectionDetail, responses=error_responses)
def set_llm_selection(payload: LlmSelectionUpsert, context: AuthenticatedContext = Depends(require_llm_selector), db: Session = Depends(get_db)):
    return llm_selection_response(set_team_llm_selection_service(db, context.user, payload))


@api.delete("/llm-selection", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def clear_llm_selection(team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_llm_selector), db: Session = Depends(get_db)):
    clear_team_llm_selection_service(db, context.user, team_id=team_id)


@api.get("/deidentification-providers", response_model=list[DeidentificationProviderDetail], responses=error_responses)
def list_deidentification_providers(context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    return [deidentification_provider_response(provider) for provider in list_deidentification_providers_service(db, context.user)]


@api.post("/deidentification-providers", response_model=DeidentificationProviderDetail, responses=error_responses)
def upsert_deidentification_provider(payload: DeidentificationProviderUpsert, context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    return deidentification_provider_response(upsert_deidentification_provider_service(db, context.user, payload))


@api.post("/deidentification-providers/inspect", response_model=DeidentificationInspectResult, responses=error_responses)
def inspect_deidentification_provider(payload: DeidentificationProviderInspectRequest, context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    return inspect_deidentification_provider_service(db, context.user, payload)


@api.delete("/deidentification-providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def delete_deidentification_provider(provider_id: UUID, context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    delete_deidentification_provider_service(db, context.user, provider_id=provider_id)


@api.get("/deidentification-provider-assignments", response_model=list[DeidentificationProviderAssignmentDetail], responses=error_responses)
def list_deidentification_provider_assignments(team_id: UUID, context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    return [
        deidentification_provider_assignment_response(item)
        for item in list_team_deidentification_provider_assignments_service(db, context.user, team_id=team_id)
    ]


@api.post("/deidentification-provider-assignments", response_model=DeidentificationProviderAssignmentDetail, responses=error_responses)
def assign_deidentification_provider(payload: DeidentificationProviderAssignmentUpsert, context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    return deidentification_provider_assignment_response(assign_deidentification_provider_to_team_service(db, context.user, payload))


@api.delete("/deidentification-provider-assignments", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def remove_deidentification_provider_assignment(team_id: UUID, provider_id: UUID, context: AuthenticatedContext = Depends(require_system_admin), db: Session = Depends(get_db)):
    remove_deidentification_provider_assignment_service(db, context.user, team_id=team_id, provider_id=provider_id)


@api.get("/deidentification-selection", response_model=DeidentificationSelectionDetail | None, responses=error_responses)
def get_deidentification_selection(team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_deidentification_selector), db: Session = Depends(get_db)):
    selection = get_team_deidentification_selection_service(db, context.user, team_id=team_id)
    return deidentification_selection_response(selection) if selection else None


@api.get("/deidentification-selection/options", response_model=list[DeidentificationProviderDetail], responses=error_responses)
def list_deidentification_selection_options(team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_deidentification_selector), db: Session = Depends(get_db)):
    return [
        deidentification_provider_response(provider)
        for provider in list_selectable_deidentification_providers_service(db, context.user, team_id=team_id)
    ]


@api.post("/deidentification-selection", response_model=DeidentificationSelectionDetail, responses=error_responses)
def set_deidentification_selection(payload: DeidentificationSelectionUpsert, context: AuthenticatedContext = Depends(require_deidentification_selector), db: Session = Depends(get_db)):
    return deidentification_selection_response(set_team_deidentification_selection_service(db, context.user, payload))


@api.delete("/deidentification-selection", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def clear_deidentification_selection(team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_deidentification_selector), db: Session = Depends(get_db)):
    clear_team_deidentification_selection_service(db, context.user, team_id=team_id)


@api.get("/clinical-nlp-selection", response_model=ClinicalNlpSelectionDetail | None, responses=error_responses)
def get_clinical_nlp_selection(team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_deidentification_selector), db: Session = Depends(get_db)):
    selection = get_team_clinical_nlp_selection_service(db, context.user, team_id=team_id)
    return clinical_nlp_selection_response(selection) if selection else None


@api.get("/clinical-nlp-selection/options", response_model=list[DeidentificationProviderDetail], responses=error_responses)
def list_clinical_nlp_selection_options(team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_deidentification_selector), db: Session = Depends(get_db)):
    return [
        deidentification_provider_response(provider)
        for provider in list_selectable_clinical_nlp_providers_service(db, context.user, team_id=team_id)
    ]


@api.post("/clinical-nlp-selection", response_model=ClinicalNlpSelectionDetail, responses=error_responses)
def set_clinical_nlp_selection(payload: ClinicalNlpSelectionUpsert, context: AuthenticatedContext = Depends(require_deidentification_selector), db: Session = Depends(get_db)):
    return clinical_nlp_selection_response(set_team_clinical_nlp_selection_service(db, context.user, payload))


@api.delete("/clinical-nlp-selection", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def clear_clinical_nlp_selection(team_id: UUID | None = None, context: AuthenticatedContext = Depends(require_deidentification_selector), db: Session = Depends(get_db)):
    clear_team_clinical_nlp_selection_service(db, context.user, team_id=team_id)


@api.get("/llm-preference", response_model=UserLlmPreferenceDetail | None, responses=error_responses)
def get_llm_preference(context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    preference = get_user_llm_preference_service(db, context.user)
    if preference is None:
        return None
    selection, config, resolved_model_name, _ = resolve_user_llm_service(db, context.user)
    allowed_models = list(selection.allowed_models_json or config.available_models_json or [])
    return user_llm_preference_response(preference, resolved_model_name=resolved_model_name, allowed_models=allowed_models)


@api.post("/llm-preference", response_model=UserLlmPreferenceDetail, responses=error_responses)
def set_llm_preference(payload: UserLlmPreferenceUpsert, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    preference = set_user_llm_preference_service(db, context.user, payload)
    selection, config, resolved_model_name, _ = resolve_user_llm_service(db, context.user)
    allowed_models = list(selection.allowed_models_json or config.available_models_json or [])
    return user_llm_preference_response(preference, resolved_model_name=resolved_model_name, allowed_models=allowed_models)


@api.delete("/llm-preference", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def clear_llm_preference(context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    clear_user_llm_preference_service(db, context.user)


@api.get("/app-preferences", response_model=UserAppPreferencesDetail | None, responses=error_responses)
def get_app_preferences(context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    preference = get_user_app_preferences_service(db, context.user)
    if preference is None:
        return None
    return user_app_preferences_response(preference)


@api.post("/app-preferences", response_model=UserAppPreferencesDetail, responses=error_responses)
def set_app_preferences(payload: UserAppPreferencesUpsert, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    preference = set_user_app_preferences_service(db, context.user, payload)
    return user_app_preferences_response(preference)


@api.delete("/app-preferences", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def clear_app_preferences(context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    clear_user_app_preferences_service(db, context.user)


@api.get("/templates/available", response_model=list[PromptTemplateDetail], responses=error_responses)
def list_available_templates(context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    return [template_response(template) for template in list_available_templates_for_user_service(db, context.user)]


@api.get("/templates/team", response_model=list[PromptTemplateDetail], responses=error_responses)
def list_team_templates(context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    return [template_response(template) for template in list_team_templates_service(db, context.user)]


@api.post("/templates/team", response_model=PromptTemplateDetail, responses=error_responses)
def upsert_team_template(payload: PromptTemplateUpsert, context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    return template_response(upsert_team_template_service(db, context.user, payload))


@api.delete("/templates/team/{template_id}", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def delete_team_template(template_id: UUID, context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    delete_team_template_service(db, context.user, template_id=template_id)


@api.get("/templates/personal", response_model=list[PromptTemplateDetail], responses=error_responses)
def list_personal_templates(context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    return [template_response(template) for template in list_personal_templates_service(db, context.user)]


@api.post("/templates/personal", response_model=PromptTemplateDetail, responses=error_responses)
def upsert_personal_template(payload: PromptTemplateUpsert, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    return template_response(upsert_personal_template_service(db, context.user, payload))


@api.delete("/templates/personal/{template_id}", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def delete_personal_template(template_id: UUID, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    delete_personal_template_service(db, context.user, template_id=template_id)


@api.get("/quick-actions/available", response_model=list[QuickActionDetail], responses=error_responses)
def list_available_quick_actions(context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    return [quick_action_response(quick_action) for quick_action in list_available_quick_actions_for_user_service(db, context.user)]


@api.get("/quick-actions/team", response_model=list[QuickActionDetail], responses=error_responses)
def list_team_quick_actions(context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    return [quick_action_response(quick_action) for quick_action in list_team_quick_actions_service(db, context.user)]


@api.post("/quick-actions/team", response_model=QuickActionDetail, responses=error_responses)
def upsert_team_quick_action(payload: QuickActionUpsert, context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    return quick_action_response(upsert_team_quick_action_service(db, context.user, payload))


@api.delete("/quick-actions/team/{quick_action_id}", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def delete_team_quick_action(quick_action_id: UUID, context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    delete_team_quick_action_service(db, context.user, quick_action_id=quick_action_id)


@api.get("/quick-actions/personal", response_model=list[QuickActionDetail], responses=error_responses)
def list_personal_quick_actions(context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    return [quick_action_response(quick_action) for quick_action in list_personal_quick_actions_service(db, context.user)]


@api.post("/quick-actions/personal", response_model=QuickActionDetail, responses=error_responses)
def upsert_personal_quick_action(payload: QuickActionUpsert, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    return quick_action_response(upsert_personal_quick_action_service(db, context.user, payload))


@api.delete("/quick-actions/personal/{quick_action_id}", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def delete_personal_quick_action(quick_action_id: UUID, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    delete_personal_quick_action_service(db, context.user, quick_action_id=quick_action_id)


@api.post("/users/{user_id}/suspend", response_model=UserDetail, responses=error_responses)
def suspend_user(user_id: UUID, context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    return suspend_user_service(db, context.user, user_id)


@api.post("/users/{user_id}/reactivate", response_model=UserDetail, responses=error_responses)
def reactivate_user(user_id: UUID, context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    return reactivate_user_service(db, context.user, user_id)


@api.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def delete_user(user_id: UUID, context: AuthenticatedContext = Depends(require_user_manager), db: Session = Depends(get_db)):
    delete_user_service(db, context.user, user_id)


@api.post("/transcripts", response_model=TranscriptDetail, status_code=status.HTTP_201_CREATED, responses=error_responses)
def create_transcript(payload: TranscriptCreate, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    transcript = create_transcript_from_payload(db, context.user, payload)
    return transcript_detail_response(db, transcript)


@api.post("/transcripts/start", response_model=TranscriptDetail, status_code=status.HTTP_201_CREATED, responses=error_responses)
def start_transcript(payload: TranscriptStart, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    transcript = start_transcript_service(db, context.user, payload)
    return transcript_detail_response(db, transcript)


@api.post("/transcripts/{transcript_id}/commit", response_model=TranscriptDetail, responses=error_responses)
def commit_transcript(transcript_id: UUID, payload: TranscriptCommit, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    transcript = commit_transcript_text_service(
        db,
        context.user,
        transcript_id=transcript_id,
        plaintext=payload.text_encrypted,
    )
    return transcript_detail_response(db, transcript)


@api.patch("/transcripts/{transcript_id}", response_model=TranscriptDetail, responses=error_responses)
def update_transcript(
    transcript_id: UUID,
    payload: TranscriptUpdate,
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    transcript = update_transcript_service(
        db,
        context.user,
        transcript_id=transcript_id,
        title=payload.title,
        ingestion_mode=payload.ingestion_mode,
        structured_context_json=payload.structured_context_json,
    )
    return transcript_detail_response(db, transcript)


@api.delete("/transcripts/{transcript_id}", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def delete_transcript(
    transcript_id: UUID,
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    delete_transcripts_service(db, context.user, transcript_ids=[transcript_id])


@api.post("/transcripts/{transcript_id}/finalize-live-capture", response_model=TranscriptDetail, responses=error_responses)
def finalize_transcript_live_capture(
    transcript_id: UUID,
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    transcript = finalize_live_capture_service(db, context.user, transcript_id=transcript_id)
    return transcript_detail_response(db, transcript)


@api.post("/transcripts/{transcript_id}/manual-pii", response_model=TranscriptPiiEntityDetail, status_code=status.HTTP_201_CREATED, responses=error_responses)
def create_transcript_manual_pii(
    transcript_id: UUID,
    payload: TranscriptManualPiiEntityCreate,
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    entity = create_manual_pii_entity_service(
        db,
        context.user,
        transcript_id=transcript_id,
        entity_type=payload.entity_type,
        value=payload.value,
        occurrence_count=payload.occurrence_count,
    )
    return transcript_manual_pii_entity_response(db, entity)


@api.delete("/transcripts/{transcript_id}/manual-pii/{entity_id}", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def delete_transcript_manual_pii(
    transcript_id: UUID,
    entity_id: UUID,
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    delete_manual_pii_entity_service(db, context.user, transcript_id=transcript_id, entity_id=entity_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api.post("/transcripts/{transcript_id}/audio-chunks", response_model=TranscriptIngestionAccepted, status_code=status.HTTP_202_ACCEPTED, responses=error_responses)
@LIVE_CHUNK_UPLOAD_RATE_LIMIT
def upload_transcript_audio_chunk(
    request: Request,
    transcript_id: UUID,
    audio: UploadFile = File(...),
    chunk_sequence_no: int = Form(..., ge=1),
    declared_duration_seconds: float | None = Form(default=None, gt=0),
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    audio_bytes = audio.file.read()
    transcript, job = queue_audio_chunk_ingestion(
        db,
        context.user,
        transcript_id=transcript_id,
        filename=audio.filename or "chunk.bin",
        source_audio_bytes=audio_bytes,
        chunk_sequence_no=chunk_sequence_no,
        declared_duration_seconds=declared_duration_seconds,
    )
    try:
        task_result = main_module.enqueue_transcript_ingestion_job(job_id=job.id, audio_bytes=audio_bytes)
    except Exception as exc:
        mark_ingestion_job_enqueue_failed(db, job_id=job.id, message="Could not enqueue live chunk ingestion")
        raise AppError(502, "ingestion_enqueue_failed", "Could not enqueue live chunk ingestion") from exc
    job = attach_task_id_to_ingestion_job(db, job_id=job.id, task_id=getattr(task_result, "id", None))
    refreshed_transcript = db.get(Transcript, transcript.id) or transcript
    return TranscriptIngestionAccepted(
        transcript=transcript_detail_response(db, refreshed_transcript),
        job=TranscriptIngestionJobDetail.model_validate(job, from_attributes=True),
    )


@api.post("/transcripts/{transcript_id}/audio-file", response_model=TranscriptIngestionAccepted, status_code=status.HTTP_202_ACCEPTED, responses=error_responses)
@WHOLE_FILE_UPLOAD_DAILY_RATE_LIMIT
@WHOLE_FILE_UPLOAD_BURST_RATE_LIMIT
def upload_transcript_audio_file(
    request: Request,
    transcript_id: UUID,
    audio: UploadFile = File(...),
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    audio_bytes = audio.file.read()
    enforce_whole_file_upload_size(audio_bytes=audio_bytes)
    transcript, job = queue_audio_file_ingestion(
        db,
        context.user,
        transcript_id=transcript_id,
        filename=audio.filename or "audio.bin",
        source_audio_blob=audio_bytes,
    )
    try:
        task_result = main_module.enqueue_transcript_ingestion_job(job_id=job.id, audio_bytes=audio_bytes)
    except Exception as exc:
        mark_ingestion_job_enqueue_failed(db, job_id=job.id, message="Could not enqueue file ingestion")
        raise AppError(502, "ingestion_enqueue_failed", "Could not enqueue file ingestion") from exc
    job = attach_task_id_to_ingestion_job(db, job_id=job.id, task_id=getattr(task_result, "id", None))
    refreshed_transcript = db.get(Transcript, transcript.id) or transcript
    return TranscriptIngestionAccepted(
        transcript=transcript_detail_response(db, refreshed_transcript),
        job=TranscriptIngestionJobDetail.model_validate(job, from_attributes=True),
    )


@api.post("/transcripts/{transcript_id}/retry-audio-file", response_model=TranscriptIngestionAccepted, status_code=status.HTTP_202_ACCEPTED, responses=error_responses)
@WHOLE_FILE_UPLOAD_DAILY_RATE_LIMIT
@WHOLE_FILE_UPLOAD_BURST_RATE_LIMIT
def retry_transcript_audio_file(
    request: Request,
    transcript_id: UUID,
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    transcript, job, source_audio_blob, previous_job = retry_audio_file_ingestion(
        db,
        context.user,
        transcript_id=transcript_id,
    )
    try:
        task_result = main_module.enqueue_transcript_ingestion_job(job_id=job.id, audio_bytes=source_audio_blob)
    except Exception as exc:
        mark_ingestion_job_enqueue_failed(db, job_id=job.id, message="Could not enqueue file ingestion retry")
        raise AppError(502, "ingestion_enqueue_failed", "Could not enqueue file ingestion retry") from exc
    clear_ingestion_retry_source(
        db,
        job_id=previous_job.id,
        clear_storage=True,
        clear_accounting=False,
        delete_backing_secret=False,
    )
    job = attach_task_id_to_ingestion_job(db, job_id=job.id, task_id=getattr(task_result, "id", None))
    refreshed_transcript = db.get(Transcript, transcript.id) or transcript
    return TranscriptIngestionAccepted(
        transcript=transcript_detail_response(db, refreshed_transcript),
        job=TranscriptIngestionJobDetail.model_validate(job, from_attributes=True),
    )


@api.get("/transcripts/{transcript_id}", response_model=TranscriptDetail, responses=error_responses)
def get_transcript_detail(transcript_id: UUID, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    transcript = db.get(Transcript, transcript_id)
    if not transcript:
        raise AppError(404, "not_found", "Transcript not found", {"resource": "transcript", "transcript_id": str(transcript_id)})
    if transcript.owner_user_id != context.user.id:
        raise AppError(403, "forbidden", "Transcript access is restricted to the owning user")
    return transcript_detail_response(db, transcript)


@api.get("/transcripts/{transcript_id}/post-consultation-dictation", response_model=PostConsultationDictationDetail | None, responses=error_responses)
def get_transcript_dictation(transcript_id: UUID, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    dictation = get_post_consultation_dictation(db, context.user, transcript_id=transcript_id)
    return dictation_detail_response(db, dictation=dictation) if dictation is not None else None


@api.patch("/transcripts/{transcript_id}/post-consultation-dictation", response_model=PostConsultationDictationDetail, responses=error_responses)
def update_transcript_dictation(
    transcript_id: UUID,
    payload: PostConsultationDictationUpdate,
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    dictation = update_post_consultation_dictation(db, context.user, transcript_id=transcript_id, combined_text=payload.combined_text)
    return dictation_detail_response(db, dictation=dictation)


@api.post(
    "/transcripts/{transcript_id}/post-consultation-dictation/audio-file",
    response_model=PostConsultationDictationDetail,
    responses=error_responses,
)
@WHOLE_FILE_UPLOAD_DAILY_RATE_LIMIT
@WHOLE_FILE_UPLOAD_BURST_RATE_LIMIT
def upload_transcript_dictation_audio_file(
    request: Request,
    transcript_id: UUID,
    audio: UploadFile = File(...),
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    audio_bytes = audio.file.read()
    dictation = append_post_consultation_dictation_audio(
        db,
        context.user,
        transcript_id=transcript_id,
        audio_bytes=audio_bytes,
        filename=audio.filename or "audio.bin",
    )
    return dictation_detail_response(db, dictation=dictation)


@api.get("/transcribe/workspace", response_model=TranscribeWorkspaceDetail, responses=error_responses)
def get_transcribe_workspace(
    transcript_id: UUID | None = None,
    queued_transcript_id: UUID | None = None,
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    return resolve_transcribe_workspace_detail(
        db,
        current_user=context.user,
        transcript_id=str(transcript_id) if transcript_id is not None else None,
        queued_transcript_id=str(queued_transcript_id) if queued_transcript_id is not None else None,
    )


@api.get("/transcribe/workspace/stream", responses=error_responses)
async def stream_transcribe_workspace(
    request: Request,
    transcript_id: UUID | None = None,
    queued_transcript_id: UUID | None = None,
    once: bool = False,
):
    raw_session_token = request.cookies.get(SESSION_COOKIE_NAME)
    context = _require_full_context_from_token(request, raw_session_token)

    if once:
        with _open_realtime_workspace_db_session(request) as db:
            payload = resolve_transcribe_workspace_detail(
                db,
                current_user=context.user,
                transcript_id=str(transcript_id) if transcript_id is not None else None,
                queued_transcript_id=str(queued_transcript_id) if queued_transcript_id is not None else None,
                request=request,
            ).model_dump(mode="json")
        return Response(
            content=_serialize_sse_event(event="workspace", payload=payload),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return StreamingResponse(
        stream_transcribe_workspace_events(
            request=request,
            raw_session_token=raw_session_token,
            transcript_id=str(transcript_id) if transcript_id is not None else None,
            queued_transcript_id=str(queued_transcript_id) if queued_transcript_id is not None else None,
            once=once,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@api.get("/transcripts/{transcript_id}/generated-documents", response_model=list[GeneratedDocumentDetail], responses=error_responses)
def list_generated_documents_for_transcript(transcript_id: UUID, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    return [generated_document_response(db, document) for document in list_generated_documents_for_transcript_service(db, context.user, transcript_id=transcript_id)]


@api.get("/generated-documents/{generated_document_id}/redaction-debug", response_model=GeneratedDocumentRedactionDebugDetail, responses=error_responses)
def get_generated_document_redaction_debug(
    generated_document_id: UUID,
    context: AuthenticatedContext = Depends(require_local_dev_debug_context),
    db: Session = Depends(get_db),
):
    document = db.get(GeneratedDocument, generated_document_id)
    if document is None:
        raise AppError(404, "not_found", "Generated document not found", {"resource": "generated_document", "generated_document_id": str(generated_document_id)})
    if document.owner_user_id != context.user.id:
        raise AppError(403, "forbidden", "Generated document access is restricted to the owning user")
    return generated_document_redaction_debug_response(db, document)


@api.patch("/generated-documents/{generated_document_id}", response_model=GeneratedDocumentDetail, responses=error_responses)
def update_generated_document(
    generated_document_id: UUID,
    payload: GeneratedDocumentUpdateRequest,
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    document = update_generated_document_content_service(db, context.user, generated_document_id=generated_document_id, payload=payload)
    return generated_document_response(db, document)


@api.delete("/generated-documents/{generated_document_id}", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses)
def delete_generated_document(
    generated_document_id: UUID,
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    delete_generated_document_service(db, context.user, generated_document_id=generated_document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api.post("/transcripts/{transcript_id}/generate-output", response_model=GeneratedDocumentDetail, status_code=status.HTTP_202_ACCEPTED, responses=error_responses)
@LLM_GENERATION_DAILY_RATE_LIMIT
@LLM_GENERATION_BURST_RATE_LIMIT
def generate_transcript_output(
    request: Request,
    transcript_id: UUID,
    payload: GenerateTemplateOutputRequest,
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    document = None
    try:
        document = queue_document_generation_from_template_service(
            db,
            context.user,
            transcript_id=transcript_id,
            template_id=payload.template_id,
            structured_context=payload.structured_context,
        )
        task_result = main_module.enqueue_generated_document_job(document_id=document.id)
        attach_generated_document_task_id_service(db, document_id=document.id, task_id=getattr(task_result, "id", None))
    except AppError:
        raise
    except Exception as exc:
        if document is not None:
            mark_generated_document_enqueue_failed_service(db, document_id=document.id, message="Could not enqueue note generation")
        raise AppError(502, "generation_enqueue_failed", "Could not enqueue note generation") from exc
    return generated_document_response(db, document)


@api.post("/transcripts/{transcript_id}/generate-followup", response_model=GeneratedDocumentDetail, status_code=status.HTTP_202_ACCEPTED, responses=error_responses)
@LLM_GENERATION_DAILY_RATE_LIMIT
@LLM_GENERATION_BURST_RATE_LIMIT
def generate_transcript_followup(
    request: Request,
    transcript_id: UUID,
    payload: GenerateFollowupRequest,
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    document = None
    try:
        document = queue_followup_generation_service(db, context.user, transcript_id=transcript_id, prompt_text=payload.prompt_text)
        task_result = main_module.enqueue_generated_document_job(document_id=document.id)
        attach_generated_document_task_id_service(db, document_id=document.id, task_id=getattr(task_result, "id", None))
    except AppError:
        raise
    except Exception as exc:
        if document is not None:
            mark_generated_document_enqueue_failed_service(db, document_id=document.id, message="Could not enqueue follow-up generation")
        raise AppError(502, "generation_enqueue_failed", "Could not enqueue follow-up generation") from exc
    return generated_document_response(db, document)


@api.post("/transcripts/{transcript_id}/run-quick-action", response_model=GeneratedDocumentDetail, status_code=status.HTTP_202_ACCEPTED, responses=error_responses)
@LLM_GENERATION_DAILY_RATE_LIMIT
@LLM_GENERATION_BURST_RATE_LIMIT
def run_transcript_quick_action(
    request: Request,
    transcript_id: UUID,
    payload: GenerateQuickActionRequest,
    context: AuthenticatedContext = Depends(require_full_context),
    db: Session = Depends(get_db),
):
    document = None
    try:
        document = queue_quick_action_generation_service(
            db,
            context.user,
            transcript_id=transcript_id,
            quick_action_id=payload.quick_action_id,
            context_text=payload.context_text,
        )
        task_result = main_module.enqueue_generated_document_job(document_id=document.id)
        attach_generated_document_task_id_service(db, document_id=document.id, task_id=getattr(task_result, "id", None))
    except AppError:
        raise
    except Exception as exc:
        if document is not None:
            mark_generated_document_enqueue_failed_service(db, document_id=document.id, message="Could not enqueue quick action generation")
        raise AppError(502, "generation_enqueue_failed", "Could not enqueue quick action generation") from exc
    return generated_document_response(db, document)


@api.get("/users/{user_id}/transcripts", response_model=list[TranscriptListItem], responses=error_responses)
def list_user_transcripts(user_id: UUID, context: AuthenticatedContext = Depends(require_full_context), db: Session = Depends(get_db)):
    if user_id != context.user.id:
        raise AppError(403, "forbidden", "Transcript access is restricted to the owning user")
    rows = db.scalars(select(Transcript).where(Transcript.owner_user_id == user_id).order_by(Transcript.created_at.desc()))
    return list(rows)
