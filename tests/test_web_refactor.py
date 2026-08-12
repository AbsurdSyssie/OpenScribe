import re
from inspect import signature
from pathlib import Path
from types import SimpleNamespace

from app import main
from app.web.transcribe_workspace import _order_assets_by_preferences, render_transcribe


def test_main_compatibility_aliases_point_to_extracted_helpers():
    assert main._open_realtime_workspace_db_session is main.open_realtime_workspace_db_session
    assert main._serialize_sse_event is main.serialize_sse_event
    assert main._home_redirect_url is main.home_redirect_url
    assert main._admin_redirect_url is main.admin_redirect_url
    assert main._transcribe_redirect is main.transcribe_redirect


def test_render_transcribe_keeps_legacy_route_call_shape():
    params = signature(render_transcribe).parameters
    assert params["local_dev_emails"].default is None
    assert params["request_is_localhost_only"].default is None


def test_dev_server_reload_scope_excludes_non_app_workspace_edits():
    script = Path("start-dev.sh").read_text()

    assert '--reload-dir "${ROOT_DIR}/app"' in script


def test_followup_redesign_orders_default_then_favorites_then_name():
    assets = [
        SimpleNamespace(id="c", name="Zebra"),
        SimpleNamespace(id="a", name="Alpha"),
        SimpleNamespace(id="b", name="Beta"),
        SimpleNamespace(id="d", name="Delta"),
    ]

    ordered = _order_assets_by_preferences(assets, favorite_ids=["d", "b"], default_id="c")

    assert [asset.id for asset in ordered] == ["c", "d", "b", "a"]


def test_daily_driver_is_default_until_user_chooses_another_template():
    assets = [
        SimpleNamespace(id="custom", name="A personal note"),
        SimpleNamespace(id="daily", name="Daily Driver"),
        SimpleNamespace(id="gp", name="GP Note"),
    ]

    automatic = _order_assets_by_preferences(
        assets, favorite_ids=["custom"], default_id=None
    )
    explicit = _order_assets_by_preferences(
        assets, favorite_ids=["daily"], default_id="gp"
    )

    assert [asset.id for asset in automatic] == ["daily", "custom", "gp"]
    assert [asset.id for asset in explicit] == ["gp", "daily", "custom"]


def test_followup_redesign_preserves_required_hooks():
    workspace_template = Path("app/templates/transcribe/_workspace.html").read_text()
    transcribe_css = Path("app/static/css/transcribe.css").read_text()
    actions_js = Path("app/static/js/transcribe/actions.js").read_text()
    documents_js = Path("app/static/js/transcribe/documents.js").read_text()

    for hook in [
        "data-quick-action-select",
        "data-quick-action-context-input",
        "data-run-quick-action-trigger",
        "data-followup-prompt-input",
        "data-generate-followup-trigger",
        "data-latest-followup-output",
        "data-followup-history",
        "data-quick-action-combobox",
        "data-followup-history-rail",
        "data-followup-regenerate-latest",
    ]:
        assert hook in workspace_template

    assert workspace_template.index("followup-composer-v3") < workspace_template.index("followup-output-v3")
    assert workspace_template.index("followup-output-v3") < workspace_template.index("data-followup-history-rail")
    assert "Quick action <span>(optional)</span>" in workspace_template
    assert "Search quick actions…" in workspace_template
    assert "Context and instructions" in workspace_template
    assert "Choose a quick action or add context, then select Generate." in workspace_template
    assert "data-followup-output-title" in workspace_template
    assert "data-followup-title-input" in workspace_template
    assert "data-followup-body-input" in workspace_template
    assert "data-latest-followup-updated-at" in workspace_template
    assert "data-followup-output-subtitle" in workspace_template
    assert "data-followup-llm-request-toggle" not in workspace_template
    assert "Show request" not in workspace_template
    assert "maxlength=\"4000\"" in workspace_template
    assert re.search(
        r"<textarea\b(?=[^>]*data-quick-action-context-input)(?=[^>]*data-followup-prompt-input)[^>]*>",
        workspace_template,
    )
    assert "data-quick-action-card-run" not in workspace_template
    assert "Quick picks" not in workspace_template
    assert "followup-action-button-v2--primary" in workspace_template
    assert "event.ctrlKey || event.metaKey" in actions_js
    assert "clearSteeringAfterQueue" in actions_js
    assert "`/api/v1/generated-documents/${generatedDocumentId}/regenerate`" in actions_js
    assert "body: JSON.stringify({ steering_text: steeringText || null })" in actions_js
    assert "await saveWorkingNoteBeforeGeneration?.();" in actions_js
    assert "openscribe:transcribe:followup-history-open" in actions_js
    assert "dirtyFollowupDocumentId" in Path("app/static/js/transcribe/app.js").read_text()
    assert "hasPendingGeneratedFollowupEdits" in documents_js
    assert "const savedDocument = await persistFollowupEditsSilently?.();" in documents_js
    assert "renderSelectedFollowup({ preserveEditor: preserveDirtyFollowupEditor });" in Path("app/static/js/transcribe/app.js").read_text()
    assert "runQuickActionForm.submit();" not in Path("app/templates/transcribe/_shell_extras.html").read_text()
    assert ".followup-composer-v3" in transcribe_css
    assert ".followup-combobox-v3__panel" in transcribe_css
    assert ".followup-history-rail-v3" in transcribe_css
    assert ".followup-output-header-v2" in transcribe_css
    assert ".followup-output-title-v2" in transcribe_css
    assert ".followup-output-title-input-v2" in transcribe_css
    assert ".followup-output-body-input-v2" in transcribe_css
    assert "followupOutputTitle.textContent" in documents_js
    assert "followupOutputTitle.value = title" in documents_js
    assert "document?.follow_up_prompt_text" not in documents_js


def test_followup_quick_action_search_uses_an_accessible_combobox_contract():
    workspace_template = Path("app/templates/transcribe/_workspace.html").read_text()
    actions_js = Path("app/static/js/transcribe/actions.js").read_text()

    toggle_markup = re.search(
        r"<button\b(?=[^>]*data-quick-action-combobox-toggle)[^>]*>", workspace_template
    ).group(0)
    search_markup = re.search(
        r"<input\b(?=[^>]*data-quick-action-search)[^>]*>", workspace_template
    ).group(0)

    # The closed control is a disclosure. The field that accepts a search is the combobox.
    assert 'role="combobox"' not in toggle_markup
    assert 'aria-haspopup="listbox"' in toggle_markup
    assert 'role="combobox"' in search_markup
    assert 'aria-autocomplete="list"' in search_markup
    assert 'aria-controls="quick-action-listbox"' in search_markup
    assert "dom.quickActionSearchInput?.setAttribute('aria-activedescendant', nextOption.id)" in actions_js
    assert "dom.quickActionComboboxToggle?.setAttribute('aria-activedescendant', nextOption.id)" not in actions_js


def test_followup_mobile_focus_trap_ignores_controls_inside_closed_details():
    actions_js = Path("app/static/js/transcribe/actions.js").read_text()

    assert "const closedDetails = element.closest('details:not([open])');" in actions_js
    assert "return !closedDetails || element.tagName === 'SUMMARY';" in actions_js


def test_followup_context_mic_refreshes_its_accessible_name_with_provider_state():
    app_js = Path("app/static/js/transcribe/app.js").read_text()

    workspace_update = app_js[app_js.index("if (quickActionContextRecordButton) {"):]
    assert "quickActionContextRecordButton.setAttribute('aria-label', voiceUnavailable ? 'Voice input unavailable' : 'Record context');" in workspace_update


def test_followup_controls_require_a_generation_source():
    app_js = Path("app/static/js/transcribe/app.js").read_text()

    assert "const canUseFollowupRequest = Boolean(transcriptId && hasLlmSelection && hasGenerationSource);" in app_js
    assert "const canChooseQuickAction = Boolean(canUseFollowupRequest && hasSelectableOptions(runQuickActionSelect));" in app_js


def test_followup_llm_request_wraps_without_horizontal_scroll():
    transcribe_css = Path("app/static/css/transcribe.css").read_text()

    assert ".followup-llm-request-pre-v2" in transcribe_css
    assert "overflow-x: hidden;" in transcribe_css
    assert "overflow-y: auto;" in transcribe_css
    assert "white-space: pre-wrap;" in transcribe_css
    assert "overflow-wrap: anywhere;" in transcribe_css


def test_followup_detailing_keeps_controls_aligned_and_output_scrollable():
    """Lock the Follow Ups layout fixes behind the detailing cache revision."""
    head_assets = Path("app/templates/transcribe/_head_assets.html").read_text()
    transcribe_css = Path("app/static/css/transcribe.css").read_text()
    documents_js = Path("app/static/js/transcribe/documents.js").read_text()

    assert 'transcribe.css?v=20260810-followups-menu-layer' in head_assets

    # Desktop composer fields share label, helper, control and meta rows.
    assert ".followup-composer-v3 {\ngrid-template-rows: auto auto auto auto;" in transcribe_css
    assert ".followup-composer-field-v3 {\ndisplay: grid;\ngrid-row: 1 / span 4;\ngrid-template-rows: subgrid;" in transcribe_css
    assert ".followup-composer-v3__generate {\ngrid-row: 3;\nalign-self: start;" in transcribe_css
    assert ".followup-composer-field-v3__helper-spacer {\ndisplay: none;" in transcribe_css

    # Clear is a distinct flex item, so it cannot overlap the selection toggle.
    assert ".followup-combobox-v3__control {\ndisplay: flex;\nalign-items: stretch;" in transcribe_css
    assert ".followup-combobox-v3__toggle {\ndisplay: grid;\ngrid-template-columns: auto minmax(0, 1fr) auto;\nflex: 1 1 auto;" in transcribe_css
    assert ".followup-combobox-v3__clear {\ndisplay: inline-flex;\nflex: 0 0 auto;" in transcribe_css
    assert ".followup-combobox-v3__clear {\nposition:" not in transcribe_css

    # Selection states remain legible: pale accent and dark foreground text.
    assert ".followup-combobox-v3__option.is-selected {" in transcribe_css
    assert "background: var(--accent-pale);\ncolor: var(--fg);" in transcribe_css
    assert ".followup-history-item-v3.is-selected {" in transcribe_css
    assert ".followup-history-item-v3.is-selected .followup-history-item-v3__select small {\ncolor: var(--muted);" in transcribe_css

    # The output grows into the available height; the textarea owns overflow.
    assert ".followup-workspace-v3 {\nposition: relative;\ndisplay: grid;\nflex: 1;\ngrid-template-columns:" in transcribe_css
    assert ".followup-output-v3 {\ndisplay: flex;\nflex-direction: column;\nmin-width: 0;\nmin-height: 0;" in transcribe_css
    assert ".followup-output-card-v2 {\ndisplay: flex;\nflex-direction: column;\nflex: 1 1 auto;\nmin-height: 0;" in transcribe_css
    assert ".followup-output-body-input-v2 {\nwidth: 100%;\nheight: 100%;\nmin-height: 0;\noverflow: auto;\nresize: none;" in transcribe_css

    # History and subtitle timestamps are readable dates, not raw API timestamps.
    assert "export const formatWorkspaceCreatedAt = (value) => {" in documents_js
    assert "if (!Number.isFinite(timestamp)) return value;" in documents_js
    assert "new Intl.DateTimeFormat('en-GB', {" in documents_js
    assert "hourCycle: 'h23'," in documents_js
    assert "Created ${formatWorkspaceCreatedAt(selectedFollowup.created_at)}" in documents_js


def test_collapsed_followup_history_opener_keeps_clear_of_output_actions():
    """The reopen control shares the action row, so it cannot cover Delete."""
    workspace_template = Path("app/templates/transcribe/_workspace.html").read_text()
    transcribe_css = Path("app/static/css/transcribe.css").read_text()
    actions_js = Path("app/static/js/transcribe/actions.js").read_text()

    # The opener is a sibling of Delete in the flex-wrapping output action row.
    # It is not absolutely positioned over that row when the rail is collapsed.
    actions_markup = re.search(
        r'<div class="followup-output-actions-v2">(?P<actions>.*?)</div>',
        workspace_template,
        re.DOTALL,
    ).group("actions")
    assert actions_markup.index("data-followup-delete-latest") < actions_markup.index("data-followup-history-open")
    assert ".followup-history-open-v3 {\ndisplay: inline-flex;\nalign-items: center;\njustify-content: center;\nflex: 0 0 2.75rem;" in transcribe_css
    assert ".followup-history-open-v3 {\nposition:" not in transcribe_css

    # Do not regress the keyboard and screen-reader route back into the rail.
    opener_markup = re.search(
        r"<button\b(?=[^>]*data-followup-history-open)[^>]*>", workspace_template
    ).group(0)
    assert 'aria-label="Open Follow Ups history"' in opener_markup
    assert 'aria-controls="followup-history-rail"' in opener_markup
    assert 'aria-expanded="false"' in opener_markup
    assert 'id="followup-history-rail"' in workspace_template
    assert "dom.followupHistoryOpenButton.setAttribute('aria-expanded', next ? 'true' : 'false');" in actions_js
    assert "dom.followupHistoryOpenButton?.addEventListener('click', openHistoryRail);" in actions_js
    assert "(historyRailReturnFocus || dom.followupHistoryOpenButton)?.focus?.();" in actions_js


def test_clinical_note_empty_state_uses_flat_output_only():
    workspace_template = Path("app/templates/transcribe/_workspace.html").read_text()
    transcribe_css = Path("app/static/css/transcribe.css").read_text()

    assert "assistant-flat-output--empty" in workspace_template
    assert "empty-state empty-state--clinical-note" not in workspace_template
    assert ".empty-state--clinical-note" not in transcribe_css
    assert ".assistant-flat-output--empty" in transcribe_css


def test_note_editor_empty_state_guidance_removed():
    workspace_template = Path("app/templates/transcribe/_workspace.html").read_text()
    transcribe_css = Path("app/static/css/transcribe.css").read_text()

    assert "No note lines yet" not in workspace_template
    assert "Select a template and start recording. Add note lines here as the consultation unfolds." not in workspace_template
    assert "data-structured-note-empty-state" not in workspace_template
    assert "data-freeform-note-empty-state" not in workspace_template
    assert ".note-editor-empty-state" not in transcribe_css


def test_generation_loading_replaces_plain_text_placeholders():
    workspace_template = Path("app/templates/transcribe/_workspace.html").read_text()
    transcribe_css = Path("app/static/css/transcribe.css").read_text()
    structured_js = Path("app/static/js/transcribe/structured.js").read_text()
    app_js = Path("app/static/js/transcribe/app.js").read_text()
    documents_js = Path("app/static/js/transcribe/documents.js").read_text()
    shell_extras = Path("app/templates/transcribe/_shell_extras.html").read_text()

    assert "note-generation-loading" in workspace_template
    assert "Creating{% else %}Generating{% endif %} your {{ label }}" in workspace_template
    assert "generation_loading('note'" in workspace_template
    assert "generation_loading('follow-up'" in workspace_template
    assert "We're preparing your clinical note..." in workspace_template
    assert "This may take a few seconds." in workspace_template
    assert "Your note is waiting to be written." not in workspace_template
    assert "Your note is being written." not in workspace_template
    assert "Your follow-up is being written." not in workspace_template
    assert "generationLoadingHtml({ label: 'note'" in structured_js
    assert "label: 'follow-up'" in app_js
    assert "Your follow-up is waiting to be written." not in app_js
    assert "Your follow-up is being written." not in app_js
    assert "generationLoadingHtml" in documents_js
    assert "structured.js?v=20260812-long-note-editor" in app_js
    assert "documents.js?v=20260812-long-note-editor" in app_js
    assert "/static/js/transcribe/app.js?v=20260812-long-note-editor" in shell_extras
    assert ".note-generation-loading" in transcribe_css
    assert "@keyframes note-generation-orbit" in transcribe_css
    assert 'data-transcription-loading' in workspace_template
    assert "Transcribing your conversation" in workspace_template
    assert "--note-generation-orbit-duration: 1.45s" in transcribe_css
    assert 'data-transcript-empty' in workspace_template
    assert "Start a recording to see your transcript" in workspace_template
    assert ".note-generation-loading--idle .note-generation-loading__dot-wrap" in transcribe_css
    assert ".note-generation-loading--idle .note-generation-loading__dot {\ndisplay: none;" in transcribe_css
    assert "transcriptEmpty.hidden = isTranscribing || hasDraft" in app_js
    assert "transcriptEmpty.classList.toggle('note-generation-loading--idle', !animateEmpty)" in app_js
    assert "syncTranscriptSurface(nextLabel);" in app_js
    assert not Path("loading_animation.html").exists()


def test_retired_prototype_routes_are_not_defined_or_linked_from_canonical_ui():
    retired_routes = (
        "/legacy-admin",
        "/admin2",
        "/transcribe-glm-2",
        "/transcribe-claude",
        "/transcriber_col_changes",
    )
    route_sources = (
        Path("app/routes/web_admin.py").read_text(),
        Path("app/routes/web_transcribe.py").read_text(),
    )
    canonical_ui_sources = (
        Path("app/templates/admin_mockup.html").read_text(),
        Path("app/templates/workspace.html").read_text(),
        Path("app/templates/workspace/_sidebar.html").read_text(),
        Path("app/templates/transcribe/_workspace.html").read_text(),
        Path("app/templates/transcribe/_sidebar.html").read_text(),
        Path("app/static/js/workspace/app.js").read_text(),
        Path("app/static/js/transcribe/app.js").read_text(),
    )
    transcribe_app = Path("app/static/js/transcribe/app.js").read_text()

    for retired_route in retired_routes:
        assert all(retired_route not in source for source in route_sources)
        assert all(retired_route not in source for source in canonical_ui_sources)
    assert "'/transcribe'" in transcribe_app


def test_splash_and_transcribe_styles_are_cacheable_static_assets():
    splash_template = Path("app/templates/splashpage.html").read_text()
    splash_css = Path("app/static/css/splash.css").read_text()
    head_assets = Path("app/templates/transcribe/_head_assets.html").read_text()
    transcribe_css = Path("app/static/css/transcribe.css").read_text()

    assert '<link rel="stylesheet" href="/static/css/tokens.css?v=20260701-token-harmonise">' in splash_template
    assert '<link rel="stylesheet" href="/static/css/components.css?v=20260718-brand-lockup">' in splash_template
    assert '<link rel="stylesheet" href="/static/css/splash.css?v=20260701-splash-token-harmonise">' in splash_template
    assert "<style" not in splash_template
    assert "--font-body" in Path("app/static/css/tokens.css").read_text()
    assert "font-family: var(--font-body);" in splash_css
    assert ".workflow-wrap" in splash_css
    assert ".cta-panel" in splash_css
    assert '<link rel="stylesheet" href="/static/css/tokens.css?v=20260701-token-harmonise">' in head_assets
    assert '<link rel="stylesheet" href="/static/css/components.css?v=20260718-brand-lockup">' in head_assets
    assert '<link rel="stylesheet" href="/static/css/transcribe.css?v=20260810-followups-menu-layer">' in head_assets
    assert "<style" not in head_assets
    assert "font-family: var(--font-body);" in transcribe_css
    assert ".structured-statement-list" in transcribe_css
    assert ".dictation-modal" in transcribe_css


def test_transcribe_sidebar_reuses_brand_lockup():
    sidebar_template = Path("app/templates/transcribe/_sidebar.html").read_text()
    transcribe_css = Path("app/static/css/transcribe.css").read_text()

    assert 'class="brand transcribe-sidebar__brand"' in sidebar_template
    assert '<span class="brand-mark" aria-hidden="true"><i data-lucide="feather"></i></span>' in sidebar_template
    assert '<span class="brand-name" data-sidebar-full>OpenScribe</span>' in sidebar_template
    assert 'href="/workspace/preferences" class="transcribe-sidebar-settings-link"' in sidebar_template
    assert 'data-sidebar-settings-link aria-label="Open preferences" title="Open preferences"' in sidebar_template
    assert ".transcribe-sidebar__brand .brand-name {\nfont-size: 2rem;\n}" in transcribe_css
    assert ".transcribe-sidebar.is-collapsed .transcribe-sidebar-settings-link { justify-content: center;" in transcribe_css


def test_transcribe_note_pills_use_compact_24_hour_timestamps():
    workspace_template = Path("app/templates/transcribe/_workspace.html").read_text()
    documents_js = Path("app/static/js/transcribe/documents.js").read_text()
    actions_js = Path("app/static/js/transcribe/actions.js").read_text()
    transcribe_css = Path("app/static/css/transcribe.css").read_text()

    assert 'datetime="{{ document.created_at.isoformat() }}"' in workspace_template
    assert 'data-note-created-at="{{ document.created_at.isoformat() }}"' in workspace_template
    assert "export const formatWorkspaceCreatedAt = (value) => {" in documents_js
    assert "new Intl.DateTimeFormat('en-GB', {" in documents_js
    assert "hourCycle: 'h23'," in documents_js
    assert 'data-note-hover-delete' in workspace_template
    assert "deleteButton.dataset.noteHoverDelete = 'true';" in documents_js
    assert "selectDocumentFromUi('note', documentId)" in actions_js
    assert ".document-switcher-item__delete" in transcribe_css


def test_home_and_template_editor_reuse_shared_visual_tokens():
    home_template = Path("app/templates/home.html").read_text()
    template_editor = Path("app/templates/template_editor.html").read_text()
    template_editor_workspace = Path("app/templates/_template_editor_workspace.html").read_text()
    home_css = Path("app/static/css/home.css").read_text()
    home2_css = Path("app/static/css/home2.css").read_text()
    template_editor_css = Path("app/static/css/template-editor.css").read_text()
    components_css = Path("app/static/css/components.css").read_text()
    tokens_css = Path("app/static/css/tokens.css").read_text()

    token_link = '<link rel="stylesheet" href="/static/css/tokens.css?v=20260701-token-harmonise">'
    home_components_link = '<link rel="stylesheet" href="/static/css/components.css?v=20260718-brand-lockup">'
    template_components_link = '<link rel="stylesheet" href="/static/css/components.css?v=20260701-home-components">'
    assert token_link in home_template
    assert token_link in template_editor
    assert home_components_link in home_template
    assert template_components_link in template_editor
    assert '<link rel="stylesheet" href="/static/css/home.css?v=20260712-password-controls">' in home_template
    assert '<link rel="stylesheet" href="/static/css/home2.css?v=20260701-home-extract">' in home_template
    assert '<link rel="stylesheet" href="/static/css/template-editor.css?v=20260702-template-editor-extract">' in template_editor
    assert "<style" not in home_template
    assert "<style" not in template_editor
    assert ":root" not in home_template
    assert ":root" not in template_editor
    assert 'font-family: "DM Sans"' not in home_template
    assert 'font-family: "DM Sans"' not in template_editor
    assert 'font-family: "Fraunces"' not in home_template
    assert 'font-family: "Fraunces"' not in template_editor
    assert "font-family: var(--font-body);" in home_css
    assert "font-family: var(--font-display);" in home_css
    assert "font-family: var(--font-body);" in template_editor_css
    assert "font-family: var(--font-display);" in template_editor_css
    assert ".action-bar" in template_editor_css
    assert ".template-list-item" in template_editor_css
    assert "button, .button-link" not in template_editor_css
    assert "class=\"btn-secondary\"" in template_editor_workspace
    assert "class=\"btn-primary\"" in template_editor_workspace
    assert "--accent-warm" in tokens_css
    assert "--shadow-inset" in tokens_css
    assert "--radius-xl" in tokens_css
    assert ".btn-primary" in components_css
    assert ".toast-container" in components_css
    assert "body.home2" in home2_css


def test_workspace_template_freeform_editor_uses_compact_structured_prompt_metrics():
    workspace = Path("app/templates/_template_editor_workspace.html").read_text()
    settings_css = Path("app/static/css/settings.css").read_text()

    assert 'class="template-prompt-field"' in workspace
    assert ".template-library-detail .template-prompt-field" in settings_css
    assert ".template-library-detail textarea[name=\"prompt_text\"] { min-height: 120px; }" in settings_css
    assert ".template-library-detail .workspace-shell { display: grid; align-items: start; }" in settings_css


def test_template_editor_extracts_reusable_body_without_nested_page_shell():
    template_editor = Path("app/templates/template_editor.html").read_text()
    workspace = Path("app/templates/_template_editor_workspace.html").read_text()

    assert '{% include "_template_editor_workspace.html" %}' in template_editor
    assert 'class="app-shell"' not in workspace
    assert 'class="sidebar"' not in workspace
    assert '_csrf_script.html' not in workspace
    assert '<script' not in workspace
    assert '{% include "_template_editor_script.html" %}' in template_editor
    assert workspace.count('name="is_active"') == 1
    assert "embedded_template_editor|default(false)" in workspace
    assert "template_editor_read_only|default(false)" in workspace


def test_reusable_template_editor_has_editable_and_read_only_contracts():
    workspace = Path("app/templates/_template_editor_workspace.html").read_text()

    assert 'data-template-editor-read-only' in workspace
    assert '<article class="template-form template-preview"' in workspace
    assert '<form method="post" action="{{ editor_action }}" class="template-form">' in workspace
    assert "template_editor_read_only_action_url" in workspace
    assert "Copy to My Templates" in workspace
    assert 'name="{{ field_name }}" value="{{ field_value }}"' in workspace


def test_primary_hover_matches_transcribe_create_button_colors():
    components_css = Path("app/static/css/components.css").read_text()
    tokens_css = Path("app/static/css/tokens.css").read_text()
    tailwind_config = Path("tailwind.transcribe.config.js").read_text()
    primary_hover_rule = components_css.split(".btn-primary-sm:focus-visible {", 1)[1].split("}", 1)[0]

    assert "--accent: #1D4F5E;" in tokens_css
    assert "--accent-soft: #3D7A8C;" in tokens_css
    assert "deep: '#1D4F5E'" in tailwind_config
    assert "muted: '#3D7A8C'" in tailwind_config
    assert "background: var(--accent-soft);" in primary_hover_rule
    assert "color: white;" in primary_hover_rule


def test_workspace_refresh_burst_uses_polling_fallback_only():
    app_js = Path("app/static/js/transcribe/app.js").read_text()

    assert "const isWorkspaceRealtimeConnected = () =>" in app_js
    assert "workspaceEventSource.readyState === window.EventSource.OPEN" in app_js
    assert "const shouldUseWorkspacePollingFallback = () => {\n        return !isWorkspaceRealtimeConnected();\n      };" in app_js
    assert "const scheduleWorkspaceRefreshBurst = ({ attempts = 25, intervalMs = 1500 } = {}) => {\n        clearWorkspaceRefreshBurst();\n        if (!shouldUseWorkspacePollingFallback()) return;" in app_js
    assert "workspaceRefreshBurstTimeoutIds = workspaceRefreshBurstTimeoutIds.filter((value) => value !== timeoutId);\n            if (!shouldUseWorkspacePollingFallback()) return;\n            void fetchWorkspace();" in app_js
    assert "workspaceEventSource.addEventListener('open', () => {\n          workspaceStreamFallbackPolling = false;\n          clearWorkspaceRefreshBurst();" in app_js
    assert "if (transcriptId && shouldUseWorkspacePollingFallback())" in app_js


def test_initial_workspace_refresh_does_not_start_duplicate_requests():
    app_js = Path("app/static/js/transcribe/app.js").read_text()

    assert "const workspaceFetchesByEndpoint = new Map();" in app_js
    assert "if (workspaceFetchesByEndpoint.has(endpoint)) {\n          return workspaceFetchesByEndpoint.get(endpoint);\n        }" in app_js
    assert "workspaceFetchesByEndpoint.delete(endpoint);" in app_js
    assert "if (!hasAppliedInitialWorkspacePayload) {\n          void fetchWorkspace();\n        }" in app_js
    assert "window.setTimeout(fetchWorkspace, 250);" not in app_js


def test_workspace_marks_initial_payload_applied_only_after_processing_completes():
    app_js = Path("app/static/js/transcribe/app.js").read_text()
    apply_workspace = app_js.split("      const applyWorkspacePayload = (workspace) => {", 1)[1].split("\n\n      async function fetchWorkspace", 1)[0]

    assert apply_workspace.rfind("hasAppliedInitialWorkspacePayload = true;") > apply_workspace.rfind("syncWorkspaceRealtimeConnection();")


def test_transcribe_loading_animation_respects_reduced_motion():
    css = Path("app/static/css/transcribe.css").read_text()

    assert "@media (prefers-reduced-motion: reduce)" in css
    assert ".note-generation-loading__dot-wrap" in css
    assert ".note-generation-loading__waveform span" in css


def test_transcribe_transcript_render_guard_owns_transcript_dom_updates():
    app_js = Path("app/static/js/transcribe/app.js").read_text()

    assert "let lastDraftRenderSignature = null;" in app_js
    assert "let deferredDraftRenderText = null;" in app_js
    assert "const draftRenderSignature = (text, entities = [], options = {}) =>" in app_js
    assert "const selectionTouchesActiveDraft = () =>" in app_js
    assert "document.addEventListener('selectionchange', flushDeferredDraftRender);" in app_js
    assert "document.addEventListener('keyup', flushDeferredDraftRender);" in app_js
    assert "lastDraftRenderSignature = null;" in app_js
    assert "deferredDraftRenderText = null;" in app_js
    assert "renderDraft(draftText, { force: activeTranscriptChanged });" in app_js
    assert "renderPiiEntities(workspaceTranscriptPiiEntities, { updateTranscriptHighlights: false });" in app_js
    assert "renderDraft(currentDraftText, { force: true });" in app_js
    assert "renderHighlightedTranscript(currentDraftText" not in app_js
    assert app_js.count("renderHighlightedTranscript(nextText, workspaceTranscriptPiiEntities, { maskPii: piiMasked });") == 1
