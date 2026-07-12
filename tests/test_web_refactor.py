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


def test_followup_redesign_orders_default_then_favorites_then_name():
    assets = [
        SimpleNamespace(id="c", name="Zebra"),
        SimpleNamespace(id="a", name="Alpha"),
        SimpleNamespace(id="b", name="Beta"),
        SimpleNamespace(id="d", name="Delta"),
    ]

    ordered = _order_assets_by_preferences(assets, favorite_ids=["d", "b"], default_id="c")

    assert [asset.id for asset in ordered] == ["c", "d", "b", "a"]


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
        "data-quick-action-card",
    ]:
        assert hook in workspace_template

    assert workspace_template.index("followup-output-v2") < workspace_template.index("followup-request-v2")
    assert workspace_template.index("followup-output-footer-v2") < workspace_template.index("data-followup-history")
    assert workspace_template.index("followup-output-v2") < workspace_template.index("data-followup-history")
    assert workspace_template.index("followup-step-v2--actions") < workspace_template.index("data-quick-action-card-list")
    assert workspace_template.index("data-quick-action-card-list") < workspace_template.index("data-followup-llm-request-slot")
    assert "data-followup-selected-action-panel" not in workspace_template
    assert "data-followup-output-title" in workspace_template
    assert "data-followup-title-input" in workspace_template
    assert "data-followup-body-input" in workspace_template
    assert "data-latest-followup-updated-at" in workspace_template
    assert "data-followup-llm-request-toggle-label" in workspace_template
    assert workspace_template.index("data-followup-llm-request-toggle") < workspace_template.index("data-followup-llm-request-slot")
    assert "data-followup-output-subtitle" in workspace_template
    assert "data-followup-prompt-preview" not in workspace_template
    assert "Prompt preview" not in workspace_template
    assert workspace_template.index("data-followup-llm-request-slot") < workspace_template.index("followup-primary-actions-v2")
    assert "maxlength=\"2000\"" in workspace_template
    assert re.search(
        r"<textarea\b(?=[^>]*data-quick-action-context-input)(?=[^>]*data-followup-prompt-input)[^>]*>",
        workspace_template,
    )
    assert "data-followup-selected-action-name" not in workspace_template
    assert "data-selected-quick-action-run" not in workspace_template
    assert "data-quick-action-card-run" in workspace_template
    assert "aria-label=\"Generate {{ quick_action.name }} without context\"" in workspace_template
    assert "data-lucide=\"arrow-left\"" not in workspace_template
    assert "followup-action-button-v2--primary" in workspace_template
    assert "dom.followupSelectedActionPanel" not in actions_js
    assert "dom.selectedQuickActionRunButton" not in actions_js
    assert "dom.quickActionCardRunButtons?.forEach" in actions_js
    assert "dirtyFollowupDocumentId" in Path("app/static/js/transcribe/app.js").read_text()
    assert "hasPendingGeneratedFollowupEdits" in documents_js
    assert "const savedDocument = await persistFollowupEditsSilently?.();" in documents_js
    assert "renderSelectedFollowup({ preserveEditor: preserveDirtyFollowupEditor });" in Path("app/static/js/transcribe/app.js").read_text()
    assert "quickActionContextOverride = '';" in actions_js
    assert "runQuickActionForm.submit();" not in Path("app/templates/transcribe/_shell_extras.html").read_text()
    assert ".followup-selected-action-v2" not in transcribe_css
    assert ".followup-action-card-shell-v2" in transcribe_css
    assert ".followup-action-card-run-v2" in transcribe_css
    assert ".followup-output-header-v2" in transcribe_css
    assert ".followup-output-title-v2" in transcribe_css
    assert ".followup-output-title-input-v2" in transcribe_css
    assert ".followup-output-body-input-v2" in transcribe_css
    assert "font-size: 1.05rem;" in transcribe_css
    assert ".followup-selected-action-v2__generate" not in transcribe_css
    assert ".followup-action-button-v2--primary" in transcribe_css
    assert "followup-llm-request-pre-v2" in documents_js
    assert "followupOutputTitle.textContent" in documents_js
    assert "followupOutputTitle.value = title" in documents_js
    assert "wrapper.hidden = true" in documents_js
    assert "shouldRestoreOpen" not in documents_js
    assert "[data-followup-llm-request-slot]" in transcribe_css
    assert "followup-output-card-v2 followup-llm-request-card-v2" in documents_js
    assert "panel.hidden = !panel.hidden" in actions_js
    assert "Hide request" in actions_js
    assert "Show request" in actions_js
    assert "flex: 0 0 auto;" in transcribe_css
    assert ".followup-llm-request-card-v2[hidden]" in transcribe_css
    assert "display: none;" in transcribe_css
    assert ".followup-llm-request-card-v2" in transcribe_css


def test_followup_llm_request_wraps_without_horizontal_scroll():
    transcribe_css = Path("app/static/css/transcribe.css").read_text()

    assert ".followup-llm-request-pre-v2" in transcribe_css
    assert "overflow-x: hidden;" in transcribe_css
    assert "overflow-y: auto;" in transcribe_css
    assert "white-space: pre-wrap;" in transcribe_css
    assert "overflow-wrap: anywhere;" in transcribe_css


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
    assert "Generating your {{ label }}" in workspace_template
    assert "generation_loading('note'" in workspace_template
    assert "generation_loading('follow-up'" in workspace_template
    assert "We're preparing your clinical note..." in workspace_template
    assert "We're preparing your follow-up..." in workspace_template
    assert "Your note is waiting to be written." not in workspace_template
    assert "Your note is being written." not in workspace_template
    assert "Your follow-up is being written." not in workspace_template
    assert "Your note is waiting to be written." not in structured_js
    assert "Your note is being written." not in structured_js
    assert "Your follow-up is waiting to be written." not in app_js
    assert "Your follow-up is being written." not in app_js
    assert "generationLoadingHtml" in documents_js
    assert "generationLoadingHtml({ label: 'note'" in structured_js
    assert "generationLoadingHtml({ label: 'follow-up'" in app_js
    assert "structured.js?v=20260701-generation-loading-shared" in app_js
    assert "documents.js?v=20260701-generation-loading-shared" in app_js
    assert "/static/js/transcribe/app.js?v=20260702-transcript-empty-state" in shell_extras
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


def test_legacy_glm_transcribe_route_is_removed():
    transcribe_routes = Path("app/routes/web_transcribe.py").read_text()
    transcribe_app = Path("app/static/js/transcribe/app.js").read_text()

    assert '"/transcribe-glm-2"' not in transcribe_routes
    assert "'/transcribe-glm-2'" not in transcribe_app
    assert "'/transcribe'" in transcribe_app


def test_splash_and_transcribe_styles_are_cacheable_static_assets():
    splash_template = Path("app/templates/splashpage.html").read_text()
    splash_css = Path("app/static/css/splash.css").read_text()
    head_assets = Path("app/templates/transcribe/_head_assets.html").read_text()
    transcribe_css = Path("app/static/css/transcribe.css").read_text()

    assert '<link rel="stylesheet" href="/static/css/tokens.css?v=20260701-token-harmonise">' in splash_template
    assert '<link rel="stylesheet" href="/static/css/components.css?v=20260701-home-components">' in splash_template
    assert '<link rel="stylesheet" href="/static/css/splash.css?v=20260701-splash-token-harmonise">' in splash_template
    assert "<style" not in splash_template
    assert "--font-body" in Path("app/static/css/tokens.css").read_text()
    assert "font-family: var(--font-body);" in splash_css
    assert ".workflow-wrap" in splash_css
    assert ".cta-panel" in splash_css
    assert '<link rel="stylesheet" href="/static/css/tokens.css?v=20260701-token-harmonise">' in head_assets
    assert '<link rel="stylesheet" href="/static/css/components.css?v=20260701-home-components">' in head_assets
    assert '<link rel="stylesheet" href="/static/css/transcribe.css?v=20260702-transcript-empty-no-dot">' in head_assets
    assert "<style" not in head_assets
    assert "font-family: var(--font-body);" in transcribe_css
    assert ".structured-statement-list" in transcribe_css
    assert ".dictation-modal" in transcribe_css


def test_home_and_template_editor_reuse_shared_visual_tokens():
    home_template = Path("app/templates/home.html").read_text()
    template_editor = Path("app/templates/template_editor.html").read_text()
    admin_template = Path("app/templates/admin.html").read_text()
    home_css = Path("app/static/css/home.css").read_text()
    home2_css = Path("app/static/css/home2.css").read_text()
    template_editor_css = Path("app/static/css/template-editor.css").read_text()
    admin_css = Path("app/static/css/admin.css").read_text()
    components_css = Path("app/static/css/components.css").read_text()
    tokens_css = Path("app/static/css/tokens.css").read_text()

    token_link = '<link rel="stylesheet" href="/static/css/tokens.css?v=20260701-token-harmonise">'
    components_link = '<link rel="stylesheet" href="/static/css/components.css?v=20260701-home-components">'
    assert token_link in home_template
    assert token_link in template_editor
    assert token_link in admin_template
    assert components_link in home_template
    assert components_link in template_editor
    assert components_link in admin_template
    assert '<link rel="stylesheet" href="/static/css/home.css?v=20260701-home-extract">' in home_template
    assert '<link rel="stylesheet" href="/static/css/home2.css?v=20260701-home-extract">' in home_template
    assert '<link rel="stylesheet" href="/static/css/template-editor.css?v=20260702-template-editor-extract">' in template_editor
    assert '<link rel="stylesheet" href="/static/css/admin.css?v=20260702-admin-extract">' in admin_template
    assert "<style" not in home_template
    assert "<style" not in template_editor
    assert "<style" not in admin_template
    assert ":root" not in home_template
    assert ":root" not in template_editor
    assert ":root" not in admin_css
    assert 'font-family: "DM Sans"' not in home_template
    assert 'font-family: "DM Sans"' not in template_editor
    assert 'font-family: "DM Sans"' not in admin_template
    assert 'font-family: "DM Sans"' not in admin_css
    assert 'font-family: "Fraunces"' not in home_template
    assert 'font-family: "Fraunces"' not in template_editor
    assert 'font-family: "Fraunces"' not in admin_template
    assert 'font-family: "Fraunces"' not in admin_css
    assert "font-family: var(--font-body);" in home_css
    assert "font-family: var(--font-display);" in home_css
    assert "font-family: var(--font-body);" in template_editor_css
    assert "font-family: var(--font-display);" in template_editor_css
    assert "font-family: var(--font-body);" in admin_css
    assert "font-family: var(--font-display);" in admin_css
    assert ".action-bar" in template_editor_css
    assert ".template-list-item" in template_editor_css
    assert "button, .button-link" not in template_editor_css
    assert "class=\"btn-secondary\"" in template_editor
    assert "class=\"btn-primary\"" in template_editor
    assert ".admin-shell" in admin_css
    assert "border-radius: var(--radius-lg);" in admin_css
    assert ".toast-container" not in admin_css
    assert "--accent-warm" in tokens_css
    assert "--shadow-inset" in tokens_css
    assert "--radius-xl" in tokens_css
    assert ".btn-primary" in components_css
    assert ".toast-container" in components_css
    assert "body.home2" in home2_css


def test_workspace_refresh_burst_uses_polling_fallback_only():
    app_js = Path("app/static/js/transcribe/app.js").read_text()

    assert "const isWorkspaceRealtimeConnected = () =>" in app_js
    assert "return Boolean(window.EventSource && workspaceEventSource && !workspaceStreamFallbackPolling);" in app_js
    assert "const shouldUseWorkspacePollingFallback = () => {\n        return !isWorkspaceRealtimeConnected();\n      };" in app_js
    assert "const scheduleWorkspaceRefreshBurst = ({ attempts = 25, intervalMs = 1500 } = {}) => {\n        clearWorkspaceRefreshBurst();\n        if (!shouldUseWorkspacePollingFallback()) return;" in app_js
    assert "workspaceRefreshBurstTimeoutIds = workspaceRefreshBurstTimeoutIds.filter((value) => value !== timeoutId);\n            if (!shouldUseWorkspacePollingFallback()) return;\n            void fetchWorkspace();" in app_js
    assert "workspaceEventSource.addEventListener('open', () => {\n          workspaceStreamFallbackPolling = false;\n          clearWorkspaceRefreshBurst();" in app_js
    assert "if (transcriptId && shouldUseWorkspacePollingFallback())" in app_js


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
